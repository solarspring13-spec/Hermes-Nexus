#!/usr/bin/env python3
"""
kanban_scheduler.py — Kanban 调度引擎

Phase 4 组件 — 30s 心跳调度，自动分配任务、僵尸检测、任务状态流转。

调度逻辑:
  1. 扫描 pending 任务 → 按优先级分配给空闲 Worker
  2. 检测僵尸 Worker (心跳 > 90s) → 自动释放任务
  3. 检查阻塞任务 → 依赖满足后自动标记为可分配

守护: ~/Library/LaunchAgents/com.workbuddy.hermes-kanban.plist

用法:
    python3 kanban_scheduler.py --tick           # 单次调度
    python3 kanban_scheduler.py --daemon         # 持续运行 (30s 循环)
    python3 kanban_scheduler.py --detect-zombies  # 仅僵尸检测
    python3 kanban_scheduler.py --auto-assign     # 仅自动分配
"""

import argparse
import fcntl
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from kanban_db import (
    get_db, DB_PATH,
    list_tasks, get_task, update_task_status,
    get_active_workers, get_idle_workers,
    detect_zombies, worker_heartbeat,
    get_task_dependencies,
    _now_ts, _now_iso,
)

TZ_OFFSET = timedelta(hours=8)
LOCK_DIR = os.path.expanduser("{MEMORIA_HOME}")
LOCK_FILE = os.path.join(LOCK_DIR, ".kanban-tick.lock")
TICK_INTERVAL = 30  # 秒
ZOMBIE_TIMEOUT_MS = 90_000  # 90s
MAX_AUTO_ASSIGN_PER_TICK = 5


def _log(msg: str):
    """结构化日志"""
    ts = datetime.now(timezone(TZ_OFFSET)).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _acquire_lock() -> bool:
    """获取文件锁 (非阻塞)"""
    os.makedirs(LOCK_DIR, exist_ok=True)
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (BlockingIOError, OSError):
        return False


def _release_lock():
    """释放文件锁"""
    try:
        fd = os.open(LOCK_FILE, os.O_RDONLY)
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    except OSError:
        pass


def auto_assign(
    db_path: str = DB_PATH,
    max_assign: int = MAX_AUTO_ASSIGN_PER_TICK,
) -> Dict[str, Any]:
    """
    自动分配 pending 任务给空闲 Worker。

    规则:
      1. 选择空闲 Worker
      2. 选择最高优先级 pending 任务（无未完成依赖）
      3. 分配
    """
    db = get_db(db_path)
    idle_workers = get_idle_workers(db_path)

    if not idle_workers:
        return {"assigned": 0, "message": "No idle workers"}

    # 获取未阻塞的 pending 任务
    pending = db.execute(
        """SELECT t.* FROM tasks t
           WHERE t.status = 'pending'
           AND t.id NOT IN (
               SELECT tl.task_id FROM task_links tl
               JOIN tasks dep ON tl.depends_on = dep.id
               WHERE dep.status NOT IN ('completed', 'cancelled')
           )
           ORDER BY CASE t.priority
               WHEN 'critical' THEN 0 WHEN 'high' THEN 1
               WHEN 'medium' THEN 2 ELSE 3 END,
               t.created_at ASC
           LIMIT ?""",
        (max_assign * 2,)
    ).fetchall()

    if not pending:
        return {"assigned": 0, "message": "No assignable tasks (all blocked or none pending)"}

    assigned = []
    worker_idx = 0

    for task in pending:
        if worker_idx >= len(idle_workers) or len(assigned) >= max_assign:
            break

        worker = idle_workers[worker_idx]
        result = update_task_status(
            task["id"], "claimed",
            assignee=worker["worker_id"],
            comment="Auto-assigned by scheduler",
            db_path=db_path
        )

        if "error" not in result:
            worker_heartbeat(
                worker["worker_id"], task["id"], "working",
                db_path=db_path
            )
            assigned.append({
                "task_id": task["id"],
                "worker_id": worker["worker_id"],
                "title": task["title"],
            })
            worker_idx += 1

    return {"assigned": len(assigned), "assignments": assigned}


def check_unblocked_tasks(db_path: str = DB_PATH) -> Dict[str, Any]:
    """
    检查阻塞任务是否依赖已满足。

    遍历所有 pending 任务，如果它们的依赖都已 completed → 标记为 unblocked。
    实际上不需要额外状态 — auto_assign 已经跳过阻塞任务。
    此函数用于监控/报告。
    """
    db = get_db(db_path)

    blocked = db.execute(
        """SELECT DISTINCT tl.task_id, t.title
           FROM task_links tl
           JOIN tasks t ON tl.task_id = t.id
           JOIN tasks dep ON tl.depends_on = dep.id
           WHERE t.status = 'pending' AND dep.status NOT IN ('completed', 'cancelled')"""
    ).fetchall()

    unblocked = db.execute(
        """SELECT DISTINCT tl.task_id, t.title
           FROM task_links tl
           JOIN tasks t ON tl.task_id = t.id
           WHERE t.status = 'pending'
           AND tl.task_id NOT IN (
               SELECT tl2.task_id FROM task_links tl2
               JOIN tasks dep ON tl2.depends_on = dep.id
               WHERE dep.status NOT IN ('completed', 'cancelled')
           )"""
    ).fetchall()

    return {
        "blocked_count": len(blocked),
        "blocked": [{"task_id": r[0], "title": r[1]} for r in blocked],
        "unblocked_count": len(unblocked),
        "unblocked": [{"task_id": r[0], "title": r[1]} for r in unblocked],
    }


def tick(db_path: str = DB_PATH, verbose: bool = True) -> Dict[str, Any]:
    """
    单次调度 tick。

    执行:
      1. 检测僵尸 → 释放任务
      2. 自动分配 pending 任务
      3. 检查阻塞任务
    """
    if not _acquire_lock():
        return {"status": "locked", "message": "Another tick in progress"}

    result = {
        "tick_at": _now_iso(),
        "zombies_found": 0,
        "zombies_released": 0,
        "auto_assigned": 0,
        "blocked_tasks": 0,
        "unblocked_tasks": 0,
    }

    try:
        # 1. 僵尸检测
        zombies = detect_zombies(ZOMBIE_TIMEOUT_MS, db_path, auto_release=True)
        result["zombies_found"] = len(zombies)
        result["zombies_released"] = sum(1 for z in zombies if z.get("released"))

        if verbose and zombies:
            _log(f"ZOMBIE: {len(zombies)} found, {result['zombies_released']} released")

        # 2. 自动分配
        assign_result = auto_assign(db_path)
        result["auto_assigned"] = assign_result["assigned"]

        if verbose and result["auto_assigned"] > 0:
            _log(f"ASSIGN: {result['auto_assigned']} task(s) auto-assigned")

        # 3. 阻塞检查
        block_result = check_unblocked_tasks(db_path)
        result["blocked_tasks"] = block_result["blocked_count"]
        result["unblocked_tasks"] = block_result["unblocked_count"]

    except Exception as e:
        result["error"] = str(e)
        traceback.print_exc()
    finally:
        _release_lock()

    return result


def daemon(db_path: str = DB_PATH, interval: int = TICK_INTERVAL):
    """持续运行调度守护进程"""
    _log(f"Kanban daemon started (interval={interval}s)")

    tick_count = 0
    while True:
        try:
            result = tick(db_path, verbose=(tick_count % 20 == 0))  # 每 20 tick 详细日志
            tick_count += 1

            # 只在有活动时输出
            if any([
                result.get("zombies_found", 0) > 0,
                result.get("auto_assigned", 0) > 0,
                result.get("unblocked_tasks", 0) > 0,
            ]):
                _log(f"TICK #{tick_count}: {json.dumps(result, ensure_ascii=False)}")

        except KeyboardInterrupt:
            _log("Daemon stopped by signal")
            break
        except Exception as e:
            _log(f"ERROR: {e}")
            traceback.print_exc()

        time.sleep(interval)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="kanban_scheduler.py — Kanban 调度引擎")
    parser.add_argument("--tick", action="store_true", help="单次调度")
    parser.add_argument("--daemon", action="store_true", help="持续运行守护进程")
    parser.add_argument("--detect-zombies", action="store_true", help="仅僵尸检测")
    parser.add_argument("--auto-assign", action="store_true", help="仅自动分配")
    parser.add_argument("--check-blocked", action="store_true", help="检查阻塞任务")
    parser.add_argument("--interval", type=int, default=TICK_INTERVAL, help="守护间隔 (秒)")
    parser.add_argument("--json", action="store_true", help="JSON 输出")

    args = parser.parse_args()

    if args.daemon:
        daemon(interval=args.interval)
        return

    if args.tick:
        result = tick()
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif result.get("status") == "locked":
            print("⚠️ Tick locked (another in progress)")
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.detect_zombies:
        zombies = detect_zombies(ZOMBIE_TIMEOUT_MS, auto_release=True)
        output = {"zombies": zombies, "count": len(zombies)}
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    if args.auto_assign:
        result = auto_assign()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.check_blocked:
        result = check_unblocked_tasks()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    parser.print_help()


if __name__ == "__main__":
    main()
