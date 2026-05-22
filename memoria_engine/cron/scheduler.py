#!/usr/bin/env python3
"""
cron_scheduler.py — Hermes Cron 调度引擎

Phase 2 核心组件 — 每分钟 tick 轮询，at-most-once 执行语义。

上游参考: nousresearch/hermes-agent cron/scheduler.py
  - tick() 文件锁 (fcntl.flock LOCK_EX|LOCK_NB)
  - advance next_run_at BEFORE execution (at-most-once guarantee)
  - sequential/parallel job partitioning
  - no_agent 脚本模式
  - context_from job chaining (Phase 2.1)

存储: {MEMORIA_HOME} (复用现有 automations 表)
锁文件: {MEMORIA_HOME}
上下文: {MEMORIA_HOME}
守护: ~/Library/LaunchAgents/com.workbuddy.hermes-cron.plist (1 分钟轮询)
"""

import argparse
import fcntl
import json
import os
import sqlite3
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

try:
    from dateutil.rrule import rrulestr
    HAS_DATEUTIL = True
except ImportError:
    HAS_DATEUTIL = False

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

TZ_OFFSET = timedelta(hours=8)  # CST
DB_PATH = os.path.expanduser("{MEMORIA_HOME}")
LOCK_DIR = os.path.expanduser("{MEMORIA_HOME}")
LOCK_FILE = os.path.join(LOCK_DIR, ".tick.lock")
OUTPUT_DIR = os.path.expanduser("{MEMORIA_HOME}")
CONTEXT_DIR = os.path.expanduser("{MEMORIA_HOME}")
LOG_DIR = os.path.expanduser("{MEMORIA_HOME}")
CRON_LOG = os.path.join(LOG_DIR, "hermes-cron.log")

# ---------------------------------------------------------------------------
# 时间戳归一化 (workbuddy.db 使用毫秒)
# ---------------------------------------------------------------------------

def _to_seconds(ts) -> int:
    """将可能是毫秒的时间戳归一化为秒"""
    if ts is None:
        return 0
    if isinstance(ts, float):
        ts = int(ts)
    if ts > 1_000_000_000_000:  # 毫秒
        return ts // 1000
    return ts


def _to_millis(ts: int) -> int:
    """将秒时间戳转为毫秒 (写入 DB 时使用)"""
    return ts * 1000


def _now_ts() -> int:
    """当前 Unix 时间戳 (秒)"""
    return int(datetime.now(timezone(TZ_OFFSET)).timestamp())


def _now_millis() -> int:
    """当前 Unix 时间戳 (毫秒)"""
    return _now_ts() * 1000

DEFAULT_JOB_TIMEOUT = int(os.environ.get("HERMES_CRON_TIMEOUT", "600"))  # 10 min
MAX_PARALLEL = int(os.environ.get("HERMES_CRON_MAX_PARALLEL", "4"))


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

def _log(msg: str) -> None:
    """追加日志到 cron log 文件"""
    os.makedirs(LOG_DIR, exist_ok=True)
    ts = datetime.now(timezone(TZ_OFFSET)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(CRON_LOG, "a") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 文件锁 (at-most-once 保证)
# ---------------------------------------------------------------------------

def acquire_lock() -> bool:
    """
    获取 cron tick 文件锁。
    非阻塞 — 如果锁已被持有则立即返回 False。
    """
    os.makedirs(LOCK_DIR, exist_ok=True)
    try:
        lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (IOError, OSError):
        return False


def release_lock() -> None:
    """释放锁（尽力而为）"""
    try:
        lock_fd = os.open(LOCK_FILE, os.O_RDONLY)
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 数据库操作
# ---------------------------------------------------------------------------

def _get_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    return db


def get_due_jobs(db: sqlite3.Connection, now_ts: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    查询所有到期的活跃自动化任务。
    条件: status='ACTIVE' AND next_run_at IS NOT NULL AND next_run_at <= now
    next_run_at 在 DB 中为毫秒。
    """
    if now_ts is None:
        now_ts = _now_millis()

    # 所有 active 任务，筛选到期
    rows = db.execute(
        """SELECT * FROM automations
           WHERE status = 'ACTIVE'
             AND next_run_at IS NOT NULL
             AND next_run_at <= ?
           ORDER BY next_run_at ASC""",
        (now_ts,)
    ).fetchall()

    return [dict(r) for r in rows]


def advance_next_run(db: sqlite3.Connection, job_id: str) -> bool:
    """
    计算并更新 job 的下一次运行时间。
    对 recurring 任务解析 RRULE → 下一个触发时间。
    对 once 任务将状态设为 PAUSED（已完成）。

    返回: True 表示成功更新
    """
    job = db.execute("SELECT * FROM automations WHERE id = ?", (job_id,)).fetchone()
    if not job:
        return False

    job = dict(job)
    now = datetime.now(timezone(TZ_OFFSET))

    if job.get("schedule_type") == "once":
        # 一次性任务执行后暂停
        db.execute(
            "UPDATE automations SET status = 'PAUSED', updated_at = ? WHERE id = ?",
            (_now_millis(), job_id)
        )
        db.commit()
        return True

    # recurring 任务: 从 RRULE 计算下一个触发时间
    rrule_str = job.get("rrule", "")
    if not rrule_str:
        # 无 RRULE → 无法计算，保持现状
        return False

    try:
        if not HAS_DATEUTIL:
            _log(f"WARN: dateutil not available, skipping RRULE advance for {job_id}")
            return False

        # 使用 naive datetime 避免时区比较问题
        now_naive = now.replace(tzinfo=None)

        # 使用 dateutil 解析 RRULE
        rule = rrulestr(f"DTSTART:{now_naive.strftime('%Y%m%dT%H%M%S')}\nRRULE:{rrule_str}")
        next_dt = rule.after(now_naive, inc=False)
        if next_dt is None:
            _log(f"WARN: No next occurrence for job {job_id} with RRULE {rrule_str}")
            return False

        # 将 naive datetime 转回 aware 后再取 timestamp
        next_aware = next_dt.replace(tzinfo=timezone(TZ_OFFSET))
        next_ts = _to_millis(int(next_aware.timestamp()))
        db.execute(
            "UPDATE automations SET next_run_at = ?, updated_at = ? WHERE id = ?",
            (next_ts, _now_millis(), job_id)
        )
        db.commit()
        return True
    except Exception as e:
        _log(f"ERROR advancing next_run for {job_id}: {e}")
        return False


def record_run(
    db: sqlite3.Connection,
    job_id: str,
    success: bool,
    output: str = "",
    error: str = "",
) -> None:
    """记录执行结果到 automation_runs 表"""
    now_ms = _now_millis()
    try:
        db.execute(
            """INSERT INTO automation_runs
               (thread_id, automation_id, status, result_success, runs_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                f"cron-{job_id}-{now_ms}",
                job_id,
                "completed" if success else "failed",
                1 if success else 0,
                json.dumps({"output": output[:5000], "error": error[:2000]}, ensure_ascii=False),
                now_ms,
                now_ms,
            )
        )
        # 同时更新 last_run_at
        db.execute(
            "UPDATE automations SET last_run_at = ?, updated_at = ? WHERE id = ?",
            (now_ms, now_ms, job_id)
        )
        db.commit()
    except Exception as e:
        _log(f"ERROR recording run for {job_id}: {e}")


def save_job_output(job_id: str, output: str) -> str:
    """持久化 job 输出到磁盘"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ts = datetime.now(timezone(TZ_OFFSET)).strftime("%Y%m%d-%H%M%S")
    path = os.path.join(OUTPUT_DIR, f"{job_id}_{ts}.md")
    with open(path, "w") as f:
        f.write(f"# Job Output: {job_id}\n\n")
        f.write(f"**Executed:** {datetime.now(timezone(TZ_OFFSET)).isoformat()}\n\n")
        f.write("---\n\n")
        f.write(output)
    return path


def save_job_context(job_id: str, success: bool, output: str, error: str) -> str:
    """
    Phase 2.1 — 保存 job 执行上下文供下游 job 消费。
    
    写入 JSON 文件到 CONTEXT_DIR，包含执行结果、输出摘要和时间戳。
    下游 job 通过 context_from 字段引用此上下文。
    """
    os.makedirs(CONTEXT_DIR, exist_ok=True)
    ctx = {
        "job_id": job_id,
        "success": success,
        "output_truncated": output[:2000] if output else "",
        "error": error[:500] if error else "",
        "executed_at": datetime.now(timezone(TZ_OFFSET)).isoformat(),
        "executed_at_ts": _now_ts(),
    }
    path = os.path.join(CONTEXT_DIR, f"{job_id}.json")
    with open(path, "w") as f:
        json.dump(ctx, f, ensure_ascii=False, indent=2)
    return path


def load_upstream_context(context_from: str) -> Dict[str, Any]:
    """
    Phase 2.1 — 加载上游 job 的执行上下文。
    
    从 CONTEXT_DIR 读取指定 job_id 的上下文文件。
    返回空 dict 如果上下文不存在。
    """
    if not context_from:
        return {}
    path = os.path.join(CONTEXT_DIR, f"{context_from}.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


# ---------------------------------------------------------------------------
# Job 执行
# ---------------------------------------------------------------------------

def execute_job(
    db: sqlite3.Connection,
    job: Dict[str, Any],
    upstream_context: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str, str]:
    """
    执行单个 cron job。

    Phase 2.1: 支持 upstream_context — 当 job 有 context_from 依赖时，
    上游 job 的执行结果会作为上下文注入。

    返回: (success, output, error_message)
    """
    job_id = job["id"]
    job_name = job.get("name", job_id)
    prompt = job.get("prompt", "")
    no_agent = job.get("no_agent", False)
    script_path = job.get("script_path", "")

    ctx_note = ""
    if upstream_context:
        ctx_note = f" (ctx: {upstream_context.get('job_id', '?')})"
    _log(f"EXEC: {job_name} ({job_id}){ctx_note}")

    try:
        if no_agent and script_path:
            # no_agent 模式: 直接执行脚本 (可通过环境变量传递上下文)
            if upstream_context:
                os.environ["HERMES_CRON_UPSTREAM_CTX"] = json.dumps(upstream_context, ensure_ascii=False)
            return _execute_script(job_id, script_path)

        # 标准模式: 通过 automation 系统执行 (由 WorkBuddy 平台处理)
        output = _generate_execution_summary(job, upstream_context)
        save_job_output(job_id, output)

        return (True, output, "")

    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        _log(f"ERROR {job_name} ({job_id}): {error_msg}")
        traceback.print_exc()
        return (False, "", error_msg)


def _execute_script(job_id: str, script_path: str) -> Tuple[bool, str, str]:
    """执行 no_agent 模式脚本"""
    expanded = os.path.expanduser(script_path)
    if not os.path.isfile(expanded):
        return (False, "", f"Script not found: {expanded}")

    try:
        result = subprocess.run(
            ["python3", expanded],
            capture_output=True,
            text=True,
            timeout=DEFAULT_JOB_TIMEOUT,
            cwd=os.path.dirname(expanded) or os.getcwd(),
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            return (False, output, result.stderr.strip())
        return (True, output, "")
    except subprocess.TimeoutExpired:
        return (False, "", f"Script timed out after {DEFAULT_JOB_TIMEOUT}s")
    except Exception as e:
        return (False, "", str(e))


def _generate_execution_summary(
    job: Dict[str, Any],
    upstream_context: Optional[Dict[str, Any]] = None,
) -> str:
    """生成 job 执行摘要（标准模式 — 由平台调度实际 agent）"""
    job_name = job.get("name", job["id"])
    prompt = job.get("prompt", "")
    schedule_type = job.get("schedule_type", "unknown")
    rrule = job.get("rrule", "")
    context_from = job.get("context_from", "")

    # Phase 2.1: 追加上游上下文
    upstream_section = ""
    if upstream_context:
        uc = upstream_context
        upstream_section = f"""
## 上游 Job 上下文 (context_from={uc.get('job_id', '?')})

- **上游状态:** {'✅ 成功' if uc.get('success') else '❌ 失败'}
- **上游输出:** 
```
{uc.get('output_truncated', '')[:800]}
```
- **上游执行时间:** {uc.get('executed_at', 'N/A')}

---
"""
    elif context_from:
        upstream_section = f"""
## 上游 Job 上下文 (context_from={context_from})

> ⚠️ 上游上下文不可用 — 文件可能已过期或上游 Job 尚未执行

---
"""

    return f"""## Cron Job 触发: {job_name}

- **Job ID:** {job["id"]}
- **调度类型:** {schedule_type}
- **RRULE:** {rrule or "N/A"}
- **上游依赖:** {context_from or "无"}
- **触发时间:** {datetime.now(timezone(TZ_OFFSET)).isoformat()}
- **提示词:** {prompt[:500]}{"..." if len(prompt) > 500 else ""}
{upstream_section}
---

> 此记录由 hermes-cron scheduler 生成。
> 实际 Agent 执行由 WorkBuddy 平台调度。
"""


# ---------------------------------------------------------------------------
# Tick — 主调度循环
# ---------------------------------------------------------------------------

def get_downstream_jobs(
    db: sqlite3.Connection,
    upstream_job_id: str,
    due_jobs: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Phase 2.1 — 查找依赖 upstream_job_id 且同时到期的下游 job。
    仅在当前 tick 的 due_jobs 列表中查找（非全局扫描）。
    """
    return [j for j in due_jobs if j.get("context_from") == upstream_job_id]


def resolve_chain_order(
    due_jobs: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Phase 2.1 — 按依赖拓扑排序 due_jobs。
    
    无 context_from 的 job 排在最前，有依赖的 job 排在依赖项之后。
    循环依赖检测：如果检测到环，按原始顺序返回。
    """
    if not due_jobs:
        return []

    # 构建依赖图
    job_ids = {j["id"] for j in due_jobs}
    dependencies: Dict[str, str] = {}  # job_id -> depends_on
    for j in due_jobs:
        cf = j.get("context_from", "")
        if cf and cf in job_ids:
            dependencies[j["id"]] = cf

    if not dependencies:
        return due_jobs  # 无链，原序返回

    # 简单拓扑排序（不考虑多级链，只确保上游先于下游）
    ordered = []
    visited = set()
    processing = set()

    def visit(job: Dict[str, Any]):
        jid = job["id"]
        if jid in visited:
            return
        if jid in processing:
            _log(f"WARN: circular dependency detected involving {jid}")
            return
        processing.add(jid)
        # 先处理依赖
        dep_id = dependencies.get(jid)
        if dep_id:
            dep_job = next((j for j in due_jobs if j["id"] == dep_id), None)
            if dep_job:
                visit(dep_job)
        visited.add(jid)
        processing.discard(jid)
        ordered.append(job)

    for j in due_jobs:
        visit(j)

    return ordered


def tick(db_path: str = DB_PATH, verbose: bool = True) -> int:
    """
    主 tick 入口 — 由 launchd 每分钟调用。

    流程:
      1. 获取文件锁 (非阻塞, at-most-once)
      2. 查询到期 job
      3. 依赖拓扑排序 (Phase 2.1: context_from chaining)
      4. 提前 advance 所有到期 job 的 next_run_at (at-most-once)
      5. 释放锁
      6. 按依赖顺序执行 jobs，传递上游上下文
      7. 记录结果 + 保存上下文

    返回: 执行的 job 数量 (0 = 无到期 job 或锁被占用)
    """
    if not acquire_lock():
        if verbose:
            _log("SKIP: lock held by another tick")
        return 0

    try:
        db = _get_db(db_path)
        due_jobs = get_due_jobs(db)

        if not due_jobs:
            return 0

        # Phase 2.1: 依赖拓扑排序 — 有 context_from 的 job 排在依赖项之后
        ordered_jobs = resolve_chain_order(due_jobs)

        # ---- at-most-once: 先 advance, 后执行 ----
        job_ids = [j["id"] for j in ordered_jobs]
        advanced_ok = 0
        advanced_fail = 0
        for jid in job_ids:
            ok = advance_next_run(db, jid)
            if ok:
                advanced_ok += 1
            else:
                advanced_fail += 1
                # 兜底: 无法计算下次运行时间时，强制推进 1 小时防止重复执行
                fallback_ts = _now_millis() + 3_600_000
                try:
                    db.execute(
                        "UPDATE automations SET next_run_at = ?, updated_at = ? WHERE id = ?",
                        (fallback_ts, _now_millis(), jid)
                    )
                    db.commit()
                    _log(f"FALLBACK: {jid} next_run_at force-advanced +1h (RRULE advance failed)")
                except Exception as fe:
                    _log(f"ERROR fallback advance for {jid}: {fe}")

        if verbose or advanced_fail > 0:
            _log(f"TICK: {len(ordered_jobs)} job(s) due — {advanced_ok} advanced, {advanced_fail} fallback, releasing lock")

    finally:
        release_lock()

    # ---- 锁外执行 (Phase 2.1: 链式上下文传递) ----
    db = _get_db(db_path)
    executed = 0
    # job_id → 执行结果上下文 (供下游消费)
    job_contexts: Dict[str, Dict[str, Any]] = {}

    # 分离: 有 context_from 依赖的 job 必须串行，无依赖的可并行
    chained = [j for j in ordered_jobs if j.get("context_from", "") in job_ids]
    unchained = [j for j in ordered_jobs if j.get("context_from", "") not in job_ids]

    # 分区: 串行优先 (有 workdir/profile/context_from 的), 并行 safe (无依赖 + 无 workdir)
    sequential = [j for j in ordered_jobs if j.get("workdir") or j.get("profile") or j.get("context_from")]
    parallel_safe = [j for j in ordered_jobs if not j.get("workdir") and not j.get("profile") and not j.get("context_from")]

    # 串行执行 (按拓扑顺序，含上下文传递)
    for job in sequential:
        job_id = job["id"]
        upstream_ctx = None

        # Phase 2.1: 加载上游上下文
        context_from = job.get("context_from", "")
        if context_from:
            # 优先使用本次 tick 内刚生成的上游上下文
            if context_from in job_contexts:
                upstream_ctx = job_contexts[context_from]
            else:
                # 回退: 从磁盘加载（上游可能在之前 tick 执行过）
                upstream_ctx = load_upstream_context(context_from)

        success, output, error = execute_job(db, job, upstream_ctx)
        record_run(db, job["id"], success, output, error)

        # Phase 2.1: 保存上下文供下游消费
        ctx_path = save_job_context(job_id, success, output, error)
        job_contexts[job_id] = {
            "job_id": job_id,
            "success": success,
            "output_truncated": output[:2000] if output else "",
            "error": error[:500] if error else "",
            "executed_at": datetime.now(timezone(TZ_OFFSET)).isoformat(),
        }

        executed += 1
        db.commit()

    # 并行执行 (仅无依赖 + 无 workdir/profile 的 job)
    if parallel_safe:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(parallel_safe))) as executor:
            futures = {
                executor.submit(execute_job, _get_db(db_path), job, None): job
                for job in parallel_safe
            }
            for future in as_completed(futures):
                job = futures[future]
                job_id = job["id"]
                try:
                    success, output, error = future.result(timeout=DEFAULT_JOB_TIMEOUT)
                except Exception as e:
                    success, output, error = False, "", str(e)
                record_run(db, job["id"], success, output, error)
                save_job_context(job_id, success, output, error)
                db.commit()
                executed += 1

    if verbose:
        _log(f"DONE: {executed} job(s) executed")

    db.close()
    return executed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Hermes Cron Scheduler — tick 轮询调度引擎"
    )
    parser.add_argument(
        "--tick", action="store_true",
        help="执行一次 tick 轮询（由 launchd 每分钟调用）"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="JSON 输出"
    )
    parser.add_argument(
        "--list-jobs", action="store_true",
        help="列出所有活跃 cron jobs"
    )
    parser.add_argument(
        "--verbose", action="store_true", default=False,
        help="详细输出"
    )
    parser.add_argument(
        "--db", type=str, default=DB_PATH,
        help=f"SQLite 数据库路径 (默认: {DB_PATH})"
    )
    parser.add_argument(
        "--init-next-runs", action="store_true",
        help="初始化所有活跃 automation 的 next_run_at（首次部署用）"
    )
    args = parser.parse_args()

    if args.list_jobs:
        db = _get_db(args.db)
        jobs = db.execute(
            """SELECT id, name, schedule_type, rrule, next_run_at, last_run_at
               FROM automations WHERE status = 'ACTIVE'
               ORDER BY next_run_at ASC"""
        ).fetchall()
        if args.json:
            result = []
            for j in jobs:
                jd = dict(j)
                if jd.get("next_run_at"):
                    jd["next_run_iso"] = datetime.fromtimestamp(_to_seconds(jd["next_run_at"]), tz=timezone(TZ_OFFSET)).isoformat()
                if jd.get("last_run_at"):
                    jd["last_run_iso"] = datetime.fromtimestamp(_to_seconds(jd["last_run_at"]), tz=timezone(TZ_OFFSET)).isoformat()
                result.append(jd)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"{'ID':<30} {'Name':<30} {'Schedule':<20} {'Next Run':<25} {'Last Run'}")
            print("-" * 130)
            for j in jobs:
                jd = dict(j)
                next_run = datetime.fromtimestamp(_to_seconds(jd["next_run_at"]), tz=timezone(TZ_OFFSET)).strftime("%Y-%m-%d %H:%M:%S") if jd.get("next_run_at") else "N/A"
                last_run = datetime.fromtimestamp(_to_seconds(jd["last_run_at"]), tz=timezone(TZ_OFFSET)).strftime("%Y-%m-%d %H:%M:%S") if jd.get("last_run_at") else "N/A"
                print(f"{jd['id']:<30} {jd.get('name', '')[:28]:<30} {jd.get('schedule_type', '')[:18]:<20} {next_run:<25} {last_run}")
        db.close()
        return

    if args.init_next_runs:
        db = _get_db(args.db)
        now = datetime.now(timezone(TZ_OFFSET))
        jobs = db.execute(
            "SELECT id, schedule_type, rrule, scheduled_at FROM automations WHERE status = 'ACTIVE' AND next_run_at IS NULL"
        ).fetchall()

        updated = 0
        for j in jobs:
            jd = dict(j)
            try:
                if jd["schedule_type"] == "once" and jd.get("scheduled_at"):
                    dt = datetime.fromisoformat(jd["scheduled_at"])
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone(TZ_OFFSET))
                    next_ts = _to_millis(int(dt.timestamp()))
                elif jd.get("rrule") and HAS_DATEUTIL:
                    rule = rrulestr(f"DTSTART:{now.strftime('%Y%m%dT%H%M%S')}\nRRULE:{jd['rrule']}")
                    next_dt = rule.after(now, inc=True)
                    next_ts = _to_millis(int(next_dt.timestamp())) if next_dt else _now_millis() + 60000
                else:
                    continue

                db.execute(
                    "UPDATE automations SET next_run_at = ?, updated_at = ? WHERE id = ?",
                    (next_ts, _now_millis(), jd["id"])
                )
                updated += 1
            except Exception as e:
                print(f"  ⚠️  Failed for {jd['id']}: {e}", file=sys.stderr)

        db.commit()
        print(f"Initialized next_run_at for {updated}/{len(jobs)} jobs")
        db.close()
        return

    if args.tick:
        count = tick(args.db, verbose=args.verbose)
        if args.json:
            print(json.dumps({"jobs_executed": count}, ensure_ascii=False))
        elif count > 0:
            print(f"Cron tick: {count} job(s) executed")
        # 静默: count=0 时不输出（除非 --verbose）
        return

    parser.print_help()


if __name__ == "__main__":
    main()
