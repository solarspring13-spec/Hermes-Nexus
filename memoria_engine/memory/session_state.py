#!/usr/bin/env python3
"""
Session State Manager — L0 Instant Memory Layer
================================================
Part of Hermes 3-Tier Memory Architecture:

    L0 (Instant)  → .session_state.json   per-session, machine-readable
    L1 (Short)    → YYYY-MM-DD.md         daily log, human-readable
    L2 (Long)     → MEMORY.md             curated, compressed

L0 captures session-level context: active tasks, recent decisions,
open questions, discovered facts. At session end, distill to L1 (daily log).

Usage:
    # Initialize session
    python3 session_state.py --workspace /path --init

    # Update state (JSON on stdin or --set)
    python3 session_state.py --workspace /path --set '{"active_plan": "C1"}'

    # Add a decision
    python3 session_state.py --workspace /path --add-decision \\
        --decision "Use JSON for L0" --rationale "Machine-readable, easy to parse"

    # Add a discovered fact
    python3 session_state.py --workspace /path --add-fact \\
        --fact "memory_daemon uses python3.14, fixes need backport"

    # Add an open question
    python3 session_state.py --workspace /path --add-question \\
        --question "Should L0 distillation run at session end or periodically?"

    # Add a pending task
    python3 session_state.py --workspace /path --add-task "C2: 纠错强化学习"

    # Mark task complete
    python3 session_state.py --workspace /path --complete-task "C1: L0即时记忆层"

    # Get session summary (for LLM context injection)
    python3 session_state.py --workspace /path --summary

    # Distill L0 → L1 (generate daily log entry, mark distilled)
    python3 session_state.py --workspace /path --distill

    # Distill with auto mode (no confirmation)
    python3 session_state.py --workspace /path --distill --auto

    # Close session (mark as closed, archive if needed)
    python3 session_state.py --workspace /path --close

    # Increment tool call counter
    python3 session_state.py --workspace /path --inc-tool-count

    # Get JSON status
    python3 session_state.py --workspace /path --json
"""

import argparse
import fcntl
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


# ── Constants ────────────────────────────────────────────────

from ..constants import (
    SESSION_FILE, SESSION_TIMEOUT_HOURS,
    MEMORY_DIR, WORKBUDDY_ROOT,
)

MAX_DECISIONS = 20      # Rolling window — oldest auto-purged
MAX_FACTS = 30
MAX_QUESTIONS = 15
MAX_TASKS = 20


# ── Path helpers ─────────────────────────────────────────────

def _memory_dir(workspace: str) -> Path:
    """Ensure and return .workbuddy/memory/ for workspace."""
    d = Path(workspace) / MEMORY_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _session_path(workspace: str) -> Path:
    return _memory_dir(workspace) / SESSION_FILE


def _daily_log_path(workspace: str) -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    return _memory_dir(workspace) / f"{today}.md"


# ── State I/O ────────────────────────────────────────────────

def _load(workspace: str) -> dict:
    """Load session state, returning empty dict if not found."""
    path = _session_path(workspace)
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _save(workspace: str, state: dict) -> None:
    """Save session state, auto-updating last_updated."""
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    path = _session_path(workspace)
    with open(path, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Core API ─────────────────────────────────────────────────

def init_session(workspace: str, session_id: str = None) -> dict:
    """Initialize a new L0 session state.

    If a session already exists and is not closed, returns it unchanged.
    If an old closed session exists, archives it and creates new.
    """
    existing = _load(workspace)

    if existing and not existing.get("closed"):
        # Session still active — don't overwrite
        return existing

    # Archive old session if it exists
    if existing:
        _archive_session(workspace, existing)

    state = {
        "session_id": session_id or str(uuid.uuid4()),
        "workspace": str(workspace),
        "created_at": _now_iso(),
        "last_updated": _now_iso(),
        "active_plan": None,
        "current_phase": None,
        "pending_tasks": [],
        "completed_tasks": [],
        "recent_decisions": [],
        "discovered_facts": [],
        "open_questions": [],
        "tool_call_count": 0,
        "corrections_captured": 0,
        "distilled_to_l1": False,
        "closed": False,
        "version": 1
    }
    _save(workspace, state)
    return state


def load_session(workspace: str) -> dict | None:
    """Load current session state. Returns None if no active session."""
    state = _load(workspace)
    if not state:
        return None
    if state.get("closed"):
        return None

    # Check staleness
    try:
        created = datetime.fromisoformat(state.get("created_at", ""))
        age = (datetime.now(timezone.utc) - created).total_seconds() / 3600
        if age > SESSION_TIMEOUT_HOURS:
            # Auto-close stale session
            close_session(workspace, reason=f"stale ({age:.1f}h)")
            return None
    except (ValueError, TypeError):
        pass

    return state


def update_session(workspace: str, updates: dict) -> dict:
    """Apply updates to session state. Creates session if none exists."""
    state = load_session(workspace)
    if state is None:
        state = init_session(workspace)

    # Merge top-level keys
    for key, value in updates.items():
        if key in ("session_id", "workspace", "created_at", "version"):
            continue  # Immutable fields
        state[key] = value

    _save(workspace, state)
    return state


def add_decision(workspace: str, decision: str, rationale: str = "") -> dict:
    """Record a decision made during the session."""
    state = load_session(workspace) or init_session(workspace)
    entry = {
        "decision": decision,
        "rationale": rationale,
        "timestamp": _now_iso()
    }
    state["recent_decisions"].insert(0, entry)
    # Rolling window
    if len(state["recent_decisions"]) > MAX_DECISIONS:
        state["recent_decisions"] = state["recent_decisions"][:MAX_DECISIONS]
    _save(workspace, state)
    return state


def add_fact(workspace: str, fact: str) -> dict:
    """Record a discovered fact."""
    state = load_session(workspace) or init_session(workspace)
    entry = {"fact": fact, "timestamp": _now_iso()}
    state["discovered_facts"].insert(0, entry)
    if len(state["discovered_facts"]) > MAX_FACTS:
        state["discovered_facts"] = state["discovered_facts"][:MAX_FACTS]
    _save(workspace, state)
    return state


def add_question(workspace: str, question: str) -> dict:
    """Record an open question."""
    state = load_session(workspace) or init_session(workspace)
    entry = {"question": question, "timestamp": _now_iso(), "resolved": False}
    state["open_questions"].insert(0, entry)
    if len(state["open_questions"]) > MAX_QUESTIONS:
        state["open_questions"] = state["open_questions"][:MAX_QUESTIONS]
    _save(workspace, state)
    return state


def add_task(workspace: str, task: str) -> dict:
    """Add a pending task."""
    state = load_session(workspace) or init_session(workspace)
    entry = {"task": task, "added_at": _now_iso(), "status": "pending"}
    state["pending_tasks"].insert(0, entry)
    if len(state["pending_tasks"]) > MAX_TASKS:
        state["pending_tasks"] = state["pending_tasks"][:MAX_TASKS]
    _save(workspace, state)
    return state


def complete_task(workspace: str, task_substring: str) -> dict:
    """Mark a task as completed by substring match."""
    state = load_session(workspace) or init_session(workspace)
    for t in state["pending_tasks"]:
        if task_substring in t["task"]:
            t["status"] = "completed"
            t["completed_at"] = _now_iso()
            state["completed_tasks"].append(t)
            state["pending_tasks"].remove(t)
            break
    _save(workspace, state)
    return state


def increment_tool_count(workspace: str) -> int:
    """Increment and return the tool call counter."""
    state = load_session(workspace) or init_session(workspace)
    state["tool_call_count"] = state.get("tool_call_count", 0) + 1
    _save(workspace, state)
    return state["tool_call_count"]


def increment_corrections(workspace: str) -> int:
    """Increment corrections counter."""
    state = load_session(workspace) or init_session(workspace)
    state["corrections_captured"] = state.get("corrections_captured", 0) + 1
    _save(workspace, state)
    return state["corrections_captured"]


def get_summary(workspace: str) -> str:
    """Generate a natural language summary for LLM context injection."""
    state = load_session(workspace)
    if state is None:
        return "No active session."

    lines = ["## L0 Session State"]
    lines.append(f"- Session: `{state['session_id'][:8]}...`")
    lines.append(f"- Started: {state.get('created_at', 'unknown')[:19]}")
    lines.append(f"- Tool calls: {state.get('tool_call_count', 0)}")
    lines.append(f"- Corrections: {state.get('corrections_captured', 0)}")

    if state.get("active_plan"):
        lines.append(f"- Active plan: **{state['active_plan']}**")
    if state.get("current_phase"):
        lines.append(f"- Current phase: **{state['current_phase']}**")

    if state.get("pending_tasks"):
        lines.append("\n### Pending Tasks")
        for t in state["pending_tasks"]:
            lines.append(f"- [ ] {t['task']}")

    if state.get("completed_tasks"):
        lines.append("\n### Completed Tasks")
        for t in state["completed_tasks"]:
            lines.append(f"- [x] {t['task']}")

    if state.get("recent_decisions"):
        lines.append("\n### Recent Decisions")
        for d in state["recent_decisions"][:5]:
            r = f" ({d['rationale']})" if d.get("rationale") else ""
            lines.append(f"- {d['decision']}{r}")

    if state.get("discovered_facts"):
        lines.append("\n### Discovered Facts")
        for f in state["discovered_facts"][:5]:
            lines.append(f"- {f['fact']}")

    if state.get("open_questions"):
        lines.append("\n### Open Questions")
        for q in state["open_questions"]:
            lines.append(f"- [?] {q['question']}")

    return "\n".join(lines)


# ── Session Lock (Concurrent Fork Prevention) ──────────────────

def _lock_path(workspace: str) -> Path:
    """Return the lock file path for a given workspace."""
    return Path(workspace) / ".workbuddy" / "memory" / ".session.lock"


def acquire_session_lock(workspace: str) -> int | None:
    """Acquire an exclusive fcntl.flock on the session state.

    Returns the file descriptor on success, or None if the lock is held
    by another process.  fcntl.flock is kernel-level — the lock is
    automatically released when the process exits, even on crash.
    """
    lock_file = _lock_path(workspace)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.write(fd, str(os.getpid()).encode())
        return fd
    except BlockingIOError:
        os.close(fd)
        return None


def release_session_lock(fd: int | None, workspace: str = ""):
    """Release the fcntl.flock and close the file descriptor."""
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except Exception:
        pass
    finally:
        try:
            os.close(fd)
        except Exception:
            pass


def distill_to_l1(workspace: str, auto: bool = False, _lock: bool = True) -> str | None:
    """Distill L0 session state into L1 daily log.

    Protected by fcntl.flock to prevent concurrent session-id forks.
    Set _lock=False when the caller already holds the session lock
    (e.g. close_session wrapping distill+close atomically).
    """
    if _lock:
        lock_fd = acquire_session_lock(workspace)
        if lock_fd is None:
            print("LOCK_CONFLICT: Another process is distilling this session.", file=sys.stderr)
            return None
        try:
            return _distill_to_l1_impl(workspace, auto)
        finally:
            release_session_lock(lock_fd, workspace)
    else:
        return _distill_to_l1_impl(workspace, auto)


def _distill_to_l1_impl(workspace: str, auto: bool = False) -> str | None:
    """Inner implementation of distill — called while holding the session lock."""
    state = load_session(workspace)
    if state is None:
        print("No active session to distill.", file=sys.stderr)
        return None

    if state.get("distilled_to_l1"):
        print("Session already distilled to L1.", file=sys.stderr)
        return None

    # Build daily log entry
    now = datetime.now()
    lines = []

    # Header with session span
    try:
        started = datetime.fromisoformat(state["created_at"])
        duration = now - started
        hours = duration.total_seconds() / 3600
        dur_str = f"{hours:.1f}h" if hours < 24 else f"{hours/24:.1f}d"
    except (ValueError, TypeError, KeyError):
        dur_str = "unknown"

    if state.get("active_plan"):
        lines.append(f"## {state['active_plan']} ({dur_str})")

    # Completed tasks
    completed = state.get("completed_tasks", [])
    if completed:
        lines.append("")
        lines.append("### 完成事项")
        for t in completed:
            lines.append(f"- {t['task']}")

    # Decisions
    decisions = state.get("recent_decisions", [])
    if decisions:
        lines.append("")
        lines.append("### 关键决策")
        for d in decisions:
            r = f" — {d['rationale']}" if d.get("rationale") else ""
            lines.append(f"- {d['decision']}{r}")

    # Facts
    facts = state.get("discovered_facts", [])
    if facts:
        lines.append("")
        lines.append("### 发现")
        for f in facts:
            lines.append(f"- {f['fact']}")

    # Open questions
    questions = state.get("open_questions", [])
    if questions:
        lines.append("")
        lines.append("### 待解决问题")
        for q in questions:
            lines.append(f"- {q['question']}")

    # Session stats
    lines.append("")
    lines.append(f"*Session: {state['session_id'][:8]} | "
                 f"工具调用 {state.get('tool_call_count', 0)} | "
                 f"纠错 {state.get('corrections_captured', 0)} | "
                 f"时长 {dur_str}*")

    entry = "\n".join(lines)

    if not auto:
        print(entry)
        print("\n---")
        resp = input("Append to daily log? [Y/n]: ").strip().lower()
        if resp and resp != "y":
            print("Aborted.", file=sys.stderr)
            return None

    # Append to daily log
    log_path = _daily_log_path(workspace)
    existing = ""
    if log_path.exists():
        existing = log_path.read_text(encoding="utf-8")
        if not existing.endswith("\n"):
            existing += "\n"

    with open(log_path, "w", encoding="utf-8") as f:
        f.write(existing)
        if existing and not existing.endswith("\n\n"):
            f.write("\n")
        f.write(entry + "\n")

    # Mark as distilled
    state["distilled_to_l1"] = True
    state["distilled_at"] = _now_iso()
    _save(workspace, state)

    print(f"✅ Distilled to {log_path}", file=sys.stderr)
    return entry


def close_session(workspace: str, reason: str = "normal") -> dict:
    """Close current session. Archives state and marks closed.

    Protected by fcntl.flock — the entire close+distill operation is atomic.
    """
    lock_fd = acquire_session_lock(workspace)
    if lock_fd is None:
        return {"status": "lock_conflict", "reason": "Another process is closing this session"}

    try:
        state = load_session(workspace)
        if state is None:
            return {"status": "no_active_session"}

        # Auto-distill if not done (passes _lock=False — we already hold the lock)
        if not state.get("distilled_to_l1"):
            print("⚠️  Session not yet distilled. Running distill...", file=sys.stderr)
            distill_to_l1(workspace, auto=True, _lock=False)

        state["closed"] = True
        state["closed_at"] = _now_iso()
        state["close_reason"] = reason
        _save(workspace, state)

        # Archive
        _archive_session(workspace, state)

        # Remove active session file
        _session_path(workspace).unlink(missing_ok=True)

        return {"status": "closed", "reason": reason, "session_id": state["session_id"]}
    finally:
        release_session_lock(lock_fd, workspace)


def _archive_session(workspace: str, state: dict) -> None:
    """Save session to archive."""
    archive_dir = _memory_dir(workspace) / ".session_archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    sid = state.get("session_id", "unknown")[:8]
    archive_path = archive_dir / f"session_{ts}_{sid}.json"

    with open(archive_path, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# ── Global Pending Tasks Scan ─────────────────────────────────


def scan_pending_tasks_global() -> list:
    """Scan all WorkBuddy workspaces for pending tasks.

    Reads .workbuddy/memory/.session_state.json in each workspace under
    {WORKSPACES_ROOT} and returns all tasks with status "pending".

    Filters out:
    - Closed sessions (closed == true)
    - Stale sessions (created_at > 24h ago)

    Returns list of dicts:
        {workspace, workspace_name, task, added_at, age_hours, session_active}
    """
    results = []
    now = datetime.now(timezone.utc)

    if not WORKBUDDY_ROOT.exists():
        return results

    for item in sorted(WORKBUDDY_ROOT.iterdir()):
        if not item.is_dir():
            continue
        session_file = item / MEMORY_DIR / SESSION_FILE
        if not session_file.exists():
            continue

        try:
            with open(session_file) as f:
                state = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

        # Skip closed sessions
        if state.get("closed", False):
            continue

        # Skip stale sessions (created > 24h ago)
        try:
            created_str = state.get("created_at", "")
            if created_str:
                created = datetime.fromisoformat(created_str)
                age_hours = (now - created).total_seconds() / 3600
                if age_hours > SESSION_TIMEOUT_HOURS:
                    continue
            else:
                age_hours = None
        except (ValueError, TypeError):
            age_hours = None

        # Extract pending tasks
        pending = state.get("pending_tasks", [])
        if not pending:
            continue

        for t in pending:
            if t.get("status") == "pending":
                task_added_at = t.get("added_at", "")
                task_age = None
                if task_added_at:
                    try:
                        task_age = (now - datetime.fromisoformat(task_added_at)).total_seconds() / 3600
                    except (ValueError, TypeError):
                        pass

                results.append({
                    "workspace": str(item),
                    "workspace_name": item.name,
                    "task": t.get("task", ""),
                    "added_at": task_added_at,
                    "age_hours": round(task_age, 1) if task_age is not None else None,
                    "session_active": True,
                    "session_created": state.get("created_at", ""),
                    "session_age_hours": round(age_hours, 1) if age_hours is not None else None,
                })

    return results


def get_json(workspace: str) -> str:
    """Return full session state as JSON string."""
    state = load_session(workspace)
    if state is None:
        return json.dumps({"status": "no_active_session"})
    return json.dumps(state, indent=2, ensure_ascii=False)


def transfer_session(workspace: str, from_session_id: str, to_session_id: str) -> dict:
    """Transfer L0 state from one session to another.

    Merges decisions, facts, questions, and pending tasks from the source
    session into the target session. The source session is archived but
    not modified; the target session receives all mergable state.

    Args:
        workspace: Workspace path
        from_session_id: Source session ID (or 'latest' for most recent archived)
        to_session_id: Target session ID (or 'current' for active session)

    Returns:
        Dict with transfer results
    """
    archive_dir = Path(workspace) / MEMORY_DIR / ARCHIVE_DIR

    # ── Locate source session ──
    source_state = None
    source_path = None

    if from_session_id == "latest":
        # Find most recent archived session
        archives = sorted(archive_dir.glob("session_*.json"), reverse=True)
        if archives:
            source_path = archives[0]
    else:
        source_path = archive_dir / f"session_{from_session_id}.json"

    if source_path and source_path.exists():
        try:
            with open(source_path, 'r', encoding='utf-8') as f:
                source_state = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            pass

    if source_state is None:
        return {"transferred": False, "error": f"Source session '{from_session_id}' not found"}

    # ── Locate target session ──
    target_state = None
    if to_session_id == "current":
        target_state = load_session(workspace)
    else:
        target_path = archive_dir / f"session_{to_session_id}.json" if archive_dir.exists() else None
        if target_path and target_path.exists():
            try:
                with open(target_path, 'r', encoding='utf-8') as f:
                    target_state = json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                pass

    if target_state is None:
        return {"transferred": False, "error": f"Target session '{to_session_id}' not found"}

    # ── Merge state ──
    transfers = {}

    # Merge decisions (dedup by text content)
    target_decisions_texts = {d.get("decision", "") for d in target_state.get("recent_decisions", [])}
    new_decisions = [d for d in source_state.get("recent_decisions", [])
                     if d.get("decision", "") not in target_decisions_texts]
    if new_decisions:
        target_state.setdefault("recent_decisions", []).extend(new_decisions)
        transfers["decisions"] = len(new_decisions)

    # Merge facts (dedup by text)
    target_facts = set(target_state.get("discovered_facts", []))
    new_facts = [f for f in source_state.get("discovered_facts", []) if f not in target_facts]
    if new_facts:
        target_state.setdefault("discovered_facts", []).extend(new_facts)
        transfers["facts"] = len(new_facts)

    # Merge questions (dedup by text)
    target_questions = set(target_state.get("open_questions", []))
    new_questions = [q for q in source_state.get("open_questions", []) if q not in target_questions]
    if new_questions:
        target_state.setdefault("open_questions", []).extend(new_questions)
        transfers["questions"] = len(new_questions)

    # Merge pending tasks (dedup by text)
    target_tasks_texts = {t.get("task", "") for t in target_state.get("pending_tasks", [])}
    new_tasks = [t for t in source_state.get("pending_tasks", [])
                 if t.get("task", "") not in target_tasks_texts]
    if new_tasks:
        target_state.setdefault("pending_tasks", []).extend(new_tasks)
        transfers["tasks"] = len(new_tasks)

    # ── Save target ──
    if to_session_id == "current":
        # Write back to active session
        state_file = get_state_path(workspace)
        tmp = state_file.with_suffix(".tmp")
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(target_state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, state_file)
    else:
        # Write back to archived session
        target_path = archive_dir / f"session_{to_session_id}.json"
        tmp = target_path.with_suffix(".tmp")
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(target_state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, target_path)

    return {
        "transferred": True,
        "from_session": source_state.get("session_id", from_session_id),
        "to_session": target_state.get("session_id", to_session_id),
        "transfers": transfers,
        "total_items_transferred": sum(transfers.values()),
    }


# ── CLI ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Session State Manager — L0 Instant Memory Layer"
    )
    parser.add_argument("--workspace",
                        help="WorkBuddy workspace path (required unless --pending-tasks --global)")
    parser.add_argument("--init", action="store_true",
                        help="Initialize new session")
    parser.add_argument("--summary", action="store_true",
                        help="Print session summary")
    parser.add_argument("--json", action="store_true",
                        help="Output full state as JSON")
    parser.add_argument("--distill", action="store_true",
                        help="Distill L0 → L1 daily log")
    parser.add_argument("--auto", action="store_true",
                        help="Auto mode (no confirmation for distill)")
    parser.add_argument("--close", action="store_true",
                        help="Close session")
    parser.add_argument("--inc-tool-count", action="store_true",
                        help="Increment tool call counter")
    parser.add_argument("--inc-corrections", action="store_true",
                        help="Increment corrections captured counter")
    parser.add_argument("--set", type=str,
                        help="JSON string of fields to update")
    parser.add_argument("--add-decision", action="store_true")
    parser.add_argument("--decision", type=str, help="Decision text")
    parser.add_argument("--rationale", type=str, default="",
                        help="Decision rationale")
    parser.add_argument("--add-fact", action="store_true")
    parser.add_argument("--fact", type=str, help="Fact text")
    parser.add_argument("--add-question", action="store_true")
    parser.add_argument("--question", type=str, help="Question text")
    parser.add_argument("--add-task", action="store_true")
    parser.add_argument("--task", type=str, help="Task text")
    parser.add_argument("--complete-task", type=str,
                        help="Substring to match for completing a task")
    parser.add_argument("--reason", type=str, default="normal",
                        help="Close reason")
    parser.add_argument("--pending-tasks", action="store_true",
                        help="Scan for pending tasks (requires --global)")
    parser.add_argument("--global", dest="global_mode", action="store_true",
                        help="Operate globally across all workspaces (for --pending-tasks)")
    parser.add_argument("--transfer", nargs=2, metavar=("FROM_SESSION", "TO_SESSION"),
                        help="Transfer L0 state: --transfer <old_session_id|latest> <new_session_id|current>")

    args = parser.parse_args()

    # ── Global pending tasks scan ──
    if args.pending_tasks and args.global_mode:
        tasks = scan_pending_tasks_global()
        print(json.dumps(tasks, ensure_ascii=False, indent=2))
        return

    # All other modes require --workspace
    if not args.workspace:
        parser.error("--workspace is required (unless --pending-tasks --global)")

    if args.init:
        state = init_session(args.workspace)
        print(json.dumps({"status": "initialized",
                          "session_id": state["session_id"]}))

    elif args.summary:
        print(get_summary(args.workspace))

    elif args.json:
        print(get_json(args.workspace))

    elif args.distill:
        distill_to_l1(args.workspace, auto=args.auto)

    elif args.close:
        result = close_session(args.workspace, reason=args.reason)
        print(json.dumps(result))

    elif args.inc_tool_count:
        count = increment_tool_count(args.workspace)
        print(json.dumps({"tool_call_count": count}))

    elif args.inc_corrections:
        count = increment_corrections(args.workspace)
        print(json.dumps({"corrections_captured": count}))

    elif args.set:
        try:
            updates = json.loads(args.set)
            state = update_session(args.workspace, updates)
            print(json.dumps({"status": "updated", "session_id": state["session_id"]}))
        except json.JSONDecodeError as e:
            print(f"Invalid JSON: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.add_decision and args.decision:
        state = add_decision(args.workspace, args.decision, args.rationale)
        print(json.dumps({"status": "decision_added",
                          "total_decisions": len(state["recent_decisions"])}))

    elif args.add_fact and args.fact:
        state = add_fact(args.workspace, args.fact)
        print(json.dumps({"status": "fact_added",
                          "total_facts": len(state["discovered_facts"])}))

    elif args.add_question and args.question:
        state = add_question(args.workspace, args.question)
        print(json.dumps({"status": "question_added",
                          "total_questions": len(state["open_questions"])}))

    elif args.add_task and args.task:
        state = add_task(args.workspace, args.task)
        print(json.dumps({"status": "task_added",
                          "total_tasks": len(state["pending_tasks"])}))

    elif args.complete_task:
        state = complete_task(args.workspace, args.complete_task)
        print(json.dumps({"status": "task_completed",
                          "completed": args.complete_task}))

    elif args.transfer:
        from_id, to_id = args.transfer
        result = transfer_session(args.workspace, from_id, to_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    else:
        # Default: show summary
        print(get_summary(args.workspace))


if __name__ == "__main__":
    main()
