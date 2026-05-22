#!/usr/bin/env python3
"""
kanban_db.py — Kanban SQLite 持久化层

Phase 4 组件 — 管理 tasks、task_links、task_history、worker_heartbeat 四个表。
提供完整的 CRUD 接口和查询方法。

存储: {MEMORIA_HOME}

用法:
    python3 kanban_db.py --init                           # 初始化数据库
    python3 kanban_db.py --create-task --title "..." ...  # 创建任务
    python3 kanban_db.py --list-tasks --status pending    # 列出任务
    python3 kanban_db.py --assign --task-id "..." --worker "agent-1"  # 分配
    python3 kanban_db.py --stats                          # 统计
"""

import argparse
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DATA_DIR = os.path.expanduser("{MEMORIA_HOME}")
DB_PATH = os.path.join(DATA_DIR, "kanban.db")
TZ_OFFSET = timedelta(hours=8)

# 任务状态枚举
STATUSES = ["pending", "claimed", "in_progress", "completed", "failed", "cancelled"]
VALID_TRANSITIONS = {
    "pending": ["claimed", "cancelled"],
    "claimed": ["in_progress", "failed", "cancelled", "pending"],  # pending = zombie release
    "in_progress": ["completed", "failed", "cancelled"],
    "failed": ["pending", "cancelled"],  # pending = retry
    "completed": [],
    "cancelled": [],
}

# 优先级权重
PRIORITY_WEIGHT = {"critical": 4, "high": 3, "medium": 2, "low": 1}


# ---------------------------------------------------------------------------
# 数据库初始化
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone(TZ_OFFSET)).isoformat()


def _now_ts() -> int:
    return int(datetime.now(timezone(TZ_OFFSET)).timestamp() * 1000)


def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def get_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    """获取数据库连接（自动初始化）"""
    _ensure_dir()
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    # 幂等初始化
    _init_tables(db)
    return db


def _init_tables(db: sqlite3.Connection):
    """幂等初始化表结构"""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            priority TEXT NOT NULL DEFAULT 'medium',
            assignee TEXT DEFAULT '',
            deadline TEXT DEFAULT '',
            tags TEXT DEFAULT '[]',
            metadata TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS task_links (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            depends_on TEXT NOT NULL,
            link_type TEXT NOT NULL DEFAULT 'depends_on',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS task_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            from_status TEXT,
            to_status TEXT NOT NULL,
            assignee TEXT DEFAULT '',
            comment TEXT DEFAULT '',
            changed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS worker_heartbeat (
            worker_id TEXT PRIMARY KEY,
            current_task_id TEXT DEFAULT NULL,
            last_seen_ts INTEGER NOT NULL,
            last_seen_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'idle',
            metadata TEXT DEFAULT '{}'
        );

        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee);
        CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);
        CREATE INDEX IF NOT EXISTS idx_task_links_task ON task_links(task_id);
        CREATE INDEX IF NOT EXISTS idx_task_links_depends ON task_links(depends_on);
        CREATE INDEX IF NOT EXISTS idx_task_history_task ON task_history(task_id);
        CREATE INDEX IF NOT EXISTS idx_worker_last_seen ON worker_heartbeat(last_seen_ts);
    """)
    db.commit()


# ---------------------------------------------------------------------------
# 任务 CRUD
# ---------------------------------------------------------------------------

def create_task(
    title: str,
    description: str = "",
    priority: str = "medium",
    deadline: str = "",
    tags: List[str] = None,
    metadata: Dict = None,
    db_path: str = DB_PATH,
) -> Dict[str, Any]:
    """创建新任务"""
    db = get_db(db_path)
    task_id = f"task-{uuid.uuid4().hex[:12]}"
    now = _now_iso()

    db.execute(
        """INSERT INTO tasks (id, title, description, status, priority, deadline, tags, metadata, created_at, updated_at)
           VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)""",
        (task_id, title, description, priority, deadline,
         json.dumps(tags or [], ensure_ascii=False),
         json.dumps(metadata or {}, ensure_ascii=False),
         now, now)
    )

    # 记录历史
    db.execute(
        "INSERT INTO task_history (task_id, from_status, to_status, changed_at) VALUES (?, NULL, 'pending', ?)",
        (task_id, now)
    )

    db.commit()

    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(row) if row else {}


def get_task(task_id: str, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    """获取单个任务"""
    db = get_db(db_path)
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(row) if row else None


def list_tasks(
    status: str = "",
    assignee: str = "",
    priority: str = "",
    limit: int = 50,
    db_path: str = DB_PATH,
) -> List[Dict[str, Any]]:
    """列出任务（支持过滤）"""
    db = get_db(db_path)
    clauses = []
    params = []

    if status:
        clauses.append("status = ?")
        params.append(status)
    if assignee:
        clauses.append("assignee = ?")
        params.append(assignee)
    if priority:
        clauses.append("priority = ?")
        params.append(priority)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"SELECT * FROM tasks {where} ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, created_at ASC LIMIT ?"
    params.append(limit)

    rows = db.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def update_task_status(
    task_id: str,
    new_status: str,
    assignee: str = "",
    comment: str = "",
    db_path: str = DB_PATH,
) -> Dict[str, Any]:
    """更新任务状态（带状态机校验）"""
    db = get_db(db_path)

    task = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task:
        return {"error": f"Task '{task_id}' not found"}

    old_status = task["status"]
    valid_next = VALID_TRANSITIONS.get(old_status, [])
    if new_status not in valid_next:
        return {"error": f"Invalid transition: {old_status} → {new_status}. Valid: {valid_next}"}

    now = _now_iso()

    # 更新任务
    updates = ["status = ?", "updated_at = ?"]
    params = [new_status, now, task_id]

    if assignee:
        updates.insert(0, "assignee = ?")
        params.insert(0, assignee)

    db.execute(
        f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?",
        params
    )

    # 记录历史
    db.execute(
        "INSERT INTO task_history (task_id, from_status, to_status, assignee, comment, changed_at) VALUES (?, ?, ?, ?, ?, ?)",
        (task_id, old_status, new_status, assignee, comment, now)
    )

    db.commit()

    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(row)


def assign_task(
    task_id: str,
    worker_id: str,
    db_path: str = DB_PATH,
) -> Dict[str, Any]:
    """分配任务给 Worker"""
    return update_task_status(task_id, "claimed", assignee=worker_id, db_path=db_path)


def get_task_history(
    task_id: str,
    limit: int = 20,
    db_path: str = DB_PATH,
) -> List[Dict[str, Any]]:
    """获取任务状态变更历史"""
    db = get_db(db_path)
    rows = db.execute(
        "SELECT * FROM task_history WHERE task_id = ? ORDER BY changed_at DESC LIMIT ?",
        (task_id, limit)
    ).fetchall()
    return [dict(r) for r in rows]


def get_task_dependencies(
    task_id: str,
    db_path: str = DB_PATH,
) -> Dict[str, List[Dict]]:
    """获取任务的依赖关系"""
    db = get_db(db_path)
    depends_on = db.execute(
        "SELECT tl.*, t.title as dep_title, t.status as dep_status FROM task_links tl JOIN tasks t ON tl.depends_on = t.id WHERE tl.task_id = ?",
        (task_id,)
    ).fetchall()
    blocked_by = db.execute(
        "SELECT tl.*, t.title as blocker_title, t.status as blocker_status FROM task_links tl JOIN tasks t ON tl.task_id = t.id WHERE tl.depends_on = ?",
        (task_id,)
    ).fetchall()
    return {
        "depends_on": [dict(r) for r in depends_on],
        "blocks": [dict(r) for r in blocked_by],
    }


def add_dependency(
    task_id: str,
    depends_on: str,
    link_type: str = "depends_on",
    db_path: str = DB_PATH,
) -> Dict[str, Any]:
    """添加任务依赖"""
    db = get_db(db_path)
    link_id = f"link-{uuid.uuid4().hex[:8]}"
    now = _now_iso()

    db.execute(
        "INSERT INTO task_links (id, task_id, depends_on, link_type, created_at) VALUES (?, ?, ?, ?, ?)",
        (link_id, task_id, depends_on, link_type, now)
    )
    db.commit()
    return {"id": link_id, "task_id": task_id, "depends_on": depends_on, "link_type": link_type}


# ---------------------------------------------------------------------------
# Worker 心跳
# ---------------------------------------------------------------------------

def worker_heartbeat(
    worker_id: str,
    current_task_id: str = None,
    status: str = "idle",
    metadata: Dict = None,
    db_path: str = DB_PATH,
) -> Dict[str, Any]:
    """更新 Worker 心跳"""
    db = get_db(db_path)
    now_ts = _now_ts()
    now_iso = _now_iso()

    # 空字符串 → None (避免 FK 约束问题)
    if current_task_id == "":
        current_task_id = None

    db.execute(
        """INSERT INTO worker_heartbeat (worker_id, current_task_id, last_seen_ts, last_seen_at, status, metadata)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(worker_id) DO UPDATE SET
             current_task_id = excluded.current_task_id,
             last_seen_ts = excluded.last_seen_ts,
             last_seen_at = excluded.last_seen_at,
             status = excluded.status,
             metadata = excluded.metadata""",
        (worker_id, current_task_id, now_ts, now_iso, status,
         json.dumps(metadata or {}, ensure_ascii=False))
    )
    db.commit()
    return {"worker_id": worker_id, "status": status, "last_seen": now_iso}


def get_active_workers(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """获取活跃 Worker 列表"""
    db = get_db(db_path)
    cutoff = _now_ts() - 90_000  # 90s 超时
    rows = db.execute(
        "SELECT * FROM worker_heartbeat WHERE last_seen_ts > ? ORDER BY status",
        (cutoff,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_idle_workers(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """获取空闲 Worker (status=idle + 心跳有效)"""
    db = get_db(db_path)
    cutoff = _now_ts() - 90_000
    rows = db.execute(
        "SELECT * FROM worker_heartbeat WHERE status = 'idle' AND last_seen_ts > ?",
        (cutoff,)
    ).fetchall()
    return [dict(r) for r in rows]


def detect_zombies(
    timeout_ms: int = 90_000,
    db_path: str = DB_PATH,
    auto_release: bool = False,
) -> List[Dict[str, Any]]:
    """
    检测僵尸 Worker (心跳超时) 并可选自动释放任务。

    返回: 僵尸 Worker 列表
    """
    db = get_db(db_path)
    cutoff = _now_ts() - timeout_ms

    zombies = db.execute(
        "SELECT * FROM worker_heartbeat WHERE last_seen_ts <= ?",
        (cutoff,)
    ).fetchall()

    results = []
    for z in zombies:
        zid = z["worker_id"]
        task_id = z["current_task_id"]
        zombie_info = {"worker_id": zid, "current_task_id": task_id, "last_seen_ts": z["last_seen_ts"]}

        if auto_release and task_id:
            # 释放僵尸 Worker 的任务
            result = update_task_status(
                task_id, "pending",
                assignee="",
                comment=f"Zombie release: worker '{zid}' heartbeat timeout ({timeout_ms}ms)",
                db_path=db_path,
            )
            zombie_info["released"] = "error" not in result

        results.append(zombie_info)

    return results


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------

def get_stats(db_path: str = DB_PATH) -> Dict[str, Any]:
    """获取 Kanban 统计"""
    db = get_db(db_path)

    status_counts = {}
    for s in STATUSES:
        cnt = db.execute("SELECT COUNT(*) FROM tasks WHERE status = ?", (s,)).fetchone()[0]
        status_counts[s] = cnt

    total = sum(status_counts.values())
    active_workers = len(get_active_workers(db_path))
    idle_workers = len(get_idle_workers(db_path))
    zombies = len(detect_zombies(db_path=db_path))

    return {
        "total_tasks": total,
        "by_status": status_counts,
        "active_workers": active_workers,
        "idle_workers": idle_workers,
        "zombies": zombies,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="kanban_db.py — Kanban SQLite 持久化层")
    parser.add_argument("--init", action="store_true", help="初始化数据库")
    parser.add_argument("--create-task", action="store_true", help="创建任务")
    parser.add_argument("--title", type=str, help="任务标题")
    parser.add_argument("--description", type=str, default="", help="任务描述")
    parser.add_argument("--priority", type=str, default="", help="优先级过滤 (low/medium/high/critical，留空=全部)")
    parser.add_argument("--deadline", type=str, default="", help="截止时间")
    parser.add_argument("--tags", type=str, default="[]", help="标签 JSON")
    parser.add_argument("--get-task", action="store_true", help="获取单个任务")
    parser.add_argument("--list-tasks", action="store_true", help="列出任务")
    parser.add_argument("--status", type=str, default="", help="状态过滤")
    parser.add_argument("--assignee", type=str, default="", help="分配人过滤")
    parser.add_argument("--assign", action="store_true", help="分配任务")
    parser.add_argument("--task-id", type=str, default="", help="任务 ID")
    parser.add_argument("--worker", type=str, default="", help="Worker ID")
    parser.add_argument("--update-status", type=str, default="", help="更新任务状态")
    parser.add_argument("--history", action="store_true", help="查看任务历史")
    parser.add_argument("--add-dep", action="store_true", help="添加依赖")
    parser.add_argument("--depends-on", type=str, default="", help="依赖任务 ID")
    parser.add_argument("--deps", action="store_true", help="查看任务依赖")
    parser.add_argument("--heartbeat", action="store_true", help="Worker 心跳")
    parser.add_argument("--active-workers", action="store_true", help="活跃 Worker 列表")
    parser.add_argument("--idle-workers", action="store_true", help="空闲 Worker 列表")
    parser.add_argument("--detect-zombies", action="store_true", help="检测僵尸")
    parser.add_argument("--auto-release", action="store_true", help="自动释放僵尸任务")
    parser.add_argument("--stats", action="store_true", help="统计")
    parser.add_argument("--limit", type=int, default=50, help="列表限制")
    parser.add_argument("--json", action="store_true", help="JSON 输出")

    args = parser.parse_args()

    if args.init:
        get_db()  # 自动初始化
        print(json.dumps({"status": "initialized", "path": DB_PATH}, ensure_ascii=False))
        return

    if args.create_task and args.title:
        priority = args.priority if args.priority else "medium"
        result = create_task(
            args.title, args.description, priority, args.deadline,
            json.loads(args.tags)
        )
        _print(result, args.json)
        return

    if args.list_tasks:
        results = list_tasks(args.status, args.assignee, args.priority, args.limit)
        _print(results, args.json)
        return

    if args.assign and args.task_id and args.worker:
        result = assign_task(args.task_id, args.worker)
        _print(result, args.json)
        return

    if args.update_status and args.task_id:
        result = update_task_status(args.task_id, args.update_status, args.worker)
        _print(result, args.json)
        return

    if args.history and args.task_id:
        results = get_task_history(args.task_id, args.limit)
        _print(results, args.json)
        return

    if args.add_dep and args.task_id and args.depends_on:
        result = add_dependency(args.task_id, args.depends_on)
        _print(result, args.json)
        return

    if args.deps and args.task_id:
        result = get_task_dependencies(args.task_id)
        _print(result, args.json)
        return

    if args.get_task and args.task_id:
        result = get_task(args.task_id)
        _print(result, args.json)
        return

    if args.heartbeat and args.worker:
        result = worker_heartbeat(args.worker, args.task_id or None, "idle" if not args.task_id else "working")
        _print(result, args.json)
        return

    if args.active_workers:
        results = get_active_workers()
        _print(results, args.json)
        return

    if args.idle_workers:
        results = get_idle_workers()
        _print(results, args.json)
        return

    if args.detect_zombies:
        results = detect_zombies(auto_release=args.auto_release)
        _print(results, args.json)
        return

    if args.stats:
        result = get_stats()
        _print(result, args.json)
        return

    parser.print_help()


def _print(data, as_json: bool):
    if as_json or isinstance(data, (dict, list)):
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(data)


if __name__ == "__main__":
    main()
