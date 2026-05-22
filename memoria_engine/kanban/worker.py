#!/usr/bin/env python3
"""
kanban_worker.py — Kanban Worker 生命周期管理

Phase 4 组件 — 管理 Worker 的任务声明、心跳、完成、释放。

依赖: kanban_db.py

用法:
    python3 kanban_worker.py --claim --worker "agent-1" --task-id "task-xxx"
    python3 kanban_worker.py --complete --worker "agent-1" --task-id "task-xxx"
    python3 kanban_worker.py --heartbeat --worker "agent-1"
    python3 kanban_worker.py --release --worker "agent-1"
    python3 kanban_worker.py --status --worker "agent-1"
"""

import argparse
import json
import os
import sys
import uuid
from typing import Dict, Any, List, Optional

# 添加 scripts 目录到 path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from kanban_db import (
    get_db, DB_PATH,
    get_task, list_tasks, update_task_status,
    worker_heartbeat, get_active_workers, get_idle_workers,
    _now_ts,
)


# ---------------------------------------------------------------------------
# Worker 操作
# ---------------------------------------------------------------------------

def claim_task(
    worker_id: str,
    task_id: str = "",
    db_path: str = DB_PATH,
    auto: bool = False,
) -> Dict[str, Any]:
    """
    Worker 声明任务。

    - 如果指定 task_id: 直接声明该任务
    - 如果 auto=True: 自动分配最高优先级的 pending 任务
    """
    db = get_db(db_path)

    if auto and not task_id:
        # 自动选择: 最高优先级的 pending 任务（无依赖阻塞）
        pending = db.execute(
            """SELECT t.* FROM tasks t
               WHERE t.status = 'pending'
               AND t.id NOT IN (
                   SELECT tl.task_id FROM task_links tl
                   JOIN tasks dep ON tl.depends_on = dep.id
                   WHERE dep.status != 'completed'
               )
               ORDER BY CASE t.priority
                   WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                   WHEN 'medium' THEN 2 ELSE 3 END,
                   t.created_at ASC
               LIMIT 1"""
        ).fetchone()

        if not pending:
            return {"status": "no_task", "message": "No pending tasks available"}

        task_id = pending["id"]

    if not task_id:
        return {"error": "task_id required (or use --auto)"}

    task = get_task(task_id, db_path)
    if not task:
        return {"error": f"Task '{task_id}' not found"}

    if task["status"] != "pending":
        return {"error": f"Task '{task_id}' is not pending (current: {task['status']})"}

    # 声明任务
    result = update_task_status(task_id, "claimed", assignee=worker_id, db_path=db_path)
    if "error" in result:
        return result

    # 更新心跳 → working
    worker_heartbeat(worker_id, task_id, "working", db_path=db_path)

    return {
        "status": "claimed",
        "worker_id": worker_id,
        "task_id": task_id,
        "task_title": task["title"],
    }


def complete_task(
    worker_id: str,
    task_id: str,
    comment: str = "",
    db_path: str = DB_PATH,
) -> Dict[str, Any]:
    """Worker 完成任务"""
    task = get_task(task_id, db_path)
    if not task:
        return {"error": f"Task '{task_id}' not found"}

    if task["assignee"] != worker_id:
        return {"error": f"Task '{task_id}' is assigned to '{task['assignee']}', not '{worker_id}'"}

    if task["status"] not in ("claimed", "in_progress"):
        return {"error": f"Task '{task_id}' is not in active state (current: {task['status']})"}

    # 先更新为 in_progress (如果尚未)
    if task["status"] == "claimed":
        update_task_status(task_id, "in_progress", assignee=worker_id, db_path=db_path)

    # 完成
    result = update_task_status(task_id, "completed", assignee=worker_id, comment=comment, db_path=db_path)
    if "error" in result:
        return result

    # 心跳 → idle
    worker_heartbeat(worker_id, None, "idle", db_path=db_path)

    return {
        "status": "completed",
        "worker_id": worker_id,
        "task_id": task_id,
        "task_title": task["title"],
    }


def fail_task(
    worker_id: str,
    task_id: str,
    error_msg: str = "",
    db_path: str = DB_PATH,
) -> Dict[str, Any]:
    """Worker 任务失败"""
    task = get_task(task_id, db_path)
    if not task:
        return {"error": f"Task '{task_id}' not found"}

    result = update_task_status(
        task_id, "failed",
        assignee=worker_id,
        comment=f"Failed: {error_msg}" if error_msg else "Failed (no error message)",
        db_path=db_path
    )
    if "error" in result:
        return result

    # 心跳 → idle
    worker_heartbeat(worker_id, None, "idle", db_path=db_path)

    return {
        "status": "failed",
        "worker_id": worker_id,
        "task_id": task_id,
        "error": error_msg,
    }


def release_task(
    worker_id: str,
    task_id: str,
    db_path: str = DB_PATH,
) -> Dict[str, Any]:
    """Worker 释放任务（回退到 pending）"""
    task = get_task(task_id, db_path)
    if not task:
        return {"error": f"Task '{task_id}' not found"}

    if task["assignee"] != worker_id:
        return {"error": f"Task '{task_id}' is assigned to '{task['assignee']}', not '{worker_id}'"}

    result = update_task_status(
        task_id, "pending",
        assignee="",
        comment=f"Released by worker '{worker_id}'",
        db_path=db_path
    )
    if "error" in result:
        return result

    worker_heartbeat(worker_id, None, "idle", db_path=db_path)

    return {
        "status": "released",
        "worker_id": worker_id,
        "task_id": task_id,
    }


def get_worker_status(worker_id: str, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    """获取 Worker 状态"""
    db = get_db(db_path)
    row = db.execute(
        "SELECT * FROM worker_heartbeat WHERE worker_id = ?",
        (worker_id,)
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="kanban_worker.py — Worker 生命周期管理")
    parser.add_argument("--claim", action="store_true", help="声明任务")
    parser.add_argument("--complete", action="store_true", help="完成任务")
    parser.add_argument("--fail", action="store_true", help="任务失败")
    parser.add_argument("--release", action="store_true", help="释放任务")
    parser.add_argument("--heartbeat", action="store_true", help="心跳")
    parser.add_argument("--status", action="store_true", help="Worker 状态")
    parser.add_argument("--worker", type=str, required=True, help="Worker ID")
    parser.add_argument("--task-id", type=str, default="", help="任务 ID")
    parser.add_argument("--auto", action="store_true", help="自动分配 (配合 --claim)")
    parser.add_argument("--comment", type=str, default="", help="评论/错误消息")
    parser.add_argument("--json", action="store_true", help="JSON 输出")

    args = parser.parse_args()

    if args.claim:
        result = claim_task(args.worker, args.task_id, auto=args.auto)
    elif args.complete and args.task_id:
        result = complete_task(args.worker, args.task_id, args.comment)
    elif args.fail and args.task_id:
        result = fail_task(args.worker, args.task_id, args.comment)
    elif args.release and args.task_id:
        result = release_task(args.worker, args.task_id)
    elif args.heartbeat:
        result = worker_heartbeat(args.worker, args.task_id, "idle" if not args.task_id else "working")
    elif args.status:
        result = get_worker_status(args.worker)
        if not result:
            result = {"worker_id": args.worker, "status": "unknown", "message": "No heartbeat found"}
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
