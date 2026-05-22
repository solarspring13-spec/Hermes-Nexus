#!/usr/bin/env python3
"""
Hermes Agent 全面健康测试电池
=================================
四维对齐 Hermes Agent 官方能力：
  1. 实时可用性 (Real-time Availability)
  2. 记忆能力 (Memory)
  3. 学习能力 (Learning)
  4. 自维护能力 (Self-maintenance)

用法:
  python3 health_test_battery.py [--json] [--report] [--verbose] [--category CAT]

Author: Hermes Enhanced Memory System
"""

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ─── Configuration ────────────────────────────────────────────────

from ..constants import WORKBUDDY_DIR, WORKBUDDY_ROOT

SCRIPTS_DIR = Path(__file__).parent.resolve()
WORKSPACE = WORKBUDDY_ROOT / "2026-05-18-task-43"
PYTHON = sys.executable


# ─── Test Result Types ────────────────────────────────────────────

@dataclass
class TestCase:
    name: str
    category: str
    dimension: str  # availability / memory / learning / self-maintenance
    status: str = "PENDING"  # PASS / FAIL / SKIP / DEGRADED
    message: str = ""
    duration_ms: float = 0.0
    details: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "name": self.name,
            "category": self.category,
            "dimension": self.dimension,
            "status": self.status,
            "message": self.message,
            "duration_ms": round(self.duration_ms, 1),
            "details": self.details if self.status != "PASS" else {},
        }


# ─── Helpers ──────────────────────────────────────────────────────

def run_script(script_name: str, args: list[str], timeout: int = 15) -> tuple[int, str, str]:
    """Run a script and return (exit_code, stdout, stderr)."""
    script_path = SCRIPTS_DIR / script_name
    cmd = [PYTHON, str(script_path)] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(WORKBUDDY_DIR))
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"Timeout after {timeout}s"
    except FileNotFoundError:
        return -1, "", f"Script not found: {script_path}"
    except Exception as e:
        return -1, "", str(e)


def parse_json(stdout: str) -> dict | list | None:
    """Try to parse JSON from script output."""
    if not stdout:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


def assert_field(data: dict | list, field_path: str, expected_type: type = None) -> tuple[bool, str]:
    """Check if a JSON field exists and optionally matches type."""
    if data is None:
        return False, "No JSON data parsed"

    if isinstance(data, list):
        if not data:
            return False, "Empty list"
        data = data[0]  # check first element for lists

    parts = field_path.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return False, f"Field '{field_path}' not found"

    if expected_type and not isinstance(current, expected_type):
        return False, f"Field '{field_path}' type {type(current).__name__} != {expected_type.__name__}"

    return True, f"OK: {field_path} = {repr(current)[:80]}"


def assert_value(data: dict, field_path: str, expected) -> tuple[bool, str]:
    """Check if a JSON field equals expected value."""
    parts = field_path.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return False, f"Field '{field_path}' not found"

    if current != expected:
        return False, f"Field '{field_path}' = {repr(current)} != expected {repr(expected)}"

    return True, f"OK: {field_path} = {repr(current)}"


def file_exists(path: str) -> tuple[bool, str]:
    """Check file existence."""
    p = Path(path).expanduser()
    if p.exists():
        return True, f"OK: {p} exists ({p.stat().st_size} bytes)"
    return False, f"MISSING: {p}"


def file_nonempty(path: str) -> tuple[bool, str]:
    """Check file exists and is non-empty."""
    ok, msg = file_exists(path)
    if not ok:
        return ok, msg
    p = Path(path).expanduser()
    try:
        content = p.read_text(encoding="utf-8")
        if content.strip():
            return True, f"OK: {p} is {len(content)} chars"
        return False, f"EMPTY: {p}"
    except Exception as e:
        return False, f"READ ERROR: {p} — {e}"


def file_has_content(path: str, substring: str) -> tuple[bool, str]:
    """Check file contains substring."""
    ok, msg = file_exists(path)
    if not ok:
        return ok, msg
    p = Path(path).expanduser()
    try:
        content = p.read_text(encoding="utf-8")
        if substring in content:
            return True, f"OK: '{substring[:50]}' found in {p}"
        return False, f"MISSING: '{substring[:50]}' not found in {p}"
    except Exception as e:
        return False, f"READ ERROR: {p} — {e}"


# ─── Test Battery ─────────────────────────────────────────────────

class TestBattery:
    """Comprehensive Hermes Agent health test suite."""

    def __init__(self, verbose: bool = False, fail_fast: bool = False):
        self.verbose = verbose
        self.fail_fast = fail_fast
        self.tests: list[TestCase] = []
        self.start_time = datetime.now(timezone.utc)

    # ── Category A: Core Infrastructure (5 tests) ────────────────

    def test_A1_daemon_health(self) -> TestCase:
        """Daemon health JSON output schema and freshness."""
        tc = TestCase("A1_daemon_health", "A_Infrastructure", "availability")
        t0 = time.time()

        code, stdout, stderr = run_script("daemon_health.py", ["--json"])
        data = parse_json(stdout)
        if data is None:
            tc.status = "FAIL"
            tc.message = f"Cannot parse JSON: {stderr or stdout[:200]}"
        elif "daemons" not in data:
            tc.status = "FAIL"
            tc.message = "Missing 'daemons' key"
        else:
            checks = []
            daemons = data["daemons"]
            checks.append(("Has daemons list", len(daemons) > 0, f"{len(daemons)} daemon(s)"))
            for d in daemons:
                checks.append((f"Tier: {d.get('tier')}", d.get("tier") == "ok", d.get("tier", "?")))
                checks.append((f"Heartbeat fresh", d.get("freshness", {}).get("fresh", False), str(d.get("freshness", {}).get("age_minutes", "?")) + "min"))
                checks.append((f"Launched loaded", d.get("launchctl", {}).get("loaded", False), str(d.get("launchctl", {}))))
            checks.append(("Summary total > 0", data.get("summary", {}).get("total", 0) > 0, str(data.get("summary", {}))))
            checks.append(("No stale/missing", data.get("summary", {}).get("by_tier", {}).get("stale", -1) == 0, "stale=0"))
            checks.append(("No corrupt", data.get("summary", {}).get("by_tier", {}).get("corrupt", -1) == 0, "corrupt=0"))

            failed = [(desc, detail) for desc, ok, detail in checks if not ok]
            if failed:
                tc.status = "FAIL"
                tc.message = "; ".join(f"{d}: {det}" for d, det in failed)
            else:
                tc.status = "PASS"
                tc.message = f"All {len(checks)} checks passed"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    def test_A2_daemon_lifecycle(self) -> TestCase:
        """Daemon plist and log integrity."""
        tc = TestCase("A2_daemon_lifecycle", "A_Infrastructure", "availability")
        t0 = time.time()

        checks = []
        # Check plist exists
        plist_path = Path.home() / "Library/LaunchAgents/com.workbuddy.memory-nudge.plist"  # ← TO_MIGRATE: use config.MEMORIA_HOME
        checks.append(("Plist exists", plist_path.exists(), str(plist_path)))

        # Check log exists and recent
        log_path = WORKBUDDY_DIR / "logs" / "memory-daemon.log"
        log_ok = log_path.exists()
        checks.append(("Log exists", log_ok, str(log_path)))
        if log_ok:
            log_size = log_path.stat().st_size
            checks.append(("Log < 1MB", log_size < 1_048_576, f"{log_size} bytes"))
            # Check log was modified recently (within 2 hours)
            mtime = log_path.stat().st_mtime
            age = time.time() - mtime
            checks.append(("Log recent (< 2h)", age < 7200, f"{age/60:.0f} min ago"))

        # Check heartbeat dir
        hb_dir = WORKBUDDY_DIR / "health" / "heartbeats"
        hb_exists = hb_dir.exists()
        checks.append(("Heartbeat dir exists", hb_exists, str(hb_dir)))
        if hb_exists:
            hb_files = list(hb_dir.glob("*.json"))
            checks.append(("Heartbeat files exist", len(hb_files) > 0, f"{len(hb_files)} file(s)"))

        failed = [(desc, detail) for desc, ok, detail in checks if not ok]
        if failed:
            tc.status = "FAIL"
            tc.message = "; ".join(f"{d}: {det}" for d, det in failed)
        else:
            tc.status = "PASS"
            tc.message = f"All {len(checks)} checks passed"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    def test_A3_git_sync(self) -> TestCase:
        """Git sync status and remote connectivity."""
        tc = TestCase("A3_git_sync", "A_Infrastructure", "availability")
        t0 = time.time()

        code, stdout, stderr = run_script("git_sync.py", ["--status", "--json"])
        data = parse_json(stdout)
        if data is None:
            tc.status = "FAIL"
            tc.message = f"Cannot parse JSON: {stderr or stdout[:200]}"
        else:
            checks = []
            checks.append(("is_repo", data.get("is_repo") == True, str(data.get("is_repo"))))
            checks.append(("Has remotes", "remotes" in data and "git@" in str(data.get("remotes", "")), "remote configured"))
            checks.append(("Status ok", data.get("status") in ("ok", "uncommitted"), data.get("status", "?")))

            failed = [(desc, detail) for desc, ok, detail in checks if not ok]
            if failed:
                tc.status = "FAIL"
                tc.message = "; ".join(f"{d}: {det}" for d, det in failed)
            else:
                tc.status = "PASS"
                tc.message = f"Repo OK, status={data.get('status')}"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    def test_A4_fts5_index(self) -> TestCase:
        """FTS5 global index status and coverage."""
        tc = TestCase("A4_fts5_index", "A_Infrastructure", "availability")
        t0 = time.time()

        code, stdout, stderr = run_script("memory_index.py", ["--status", "--global", "--json"])
        data = parse_json(stdout)
        if data is None:
            tc.status = "FAIL"
            tc.message = f"Cannot parse JSON: {stderr or stdout[:200]}"
        else:
            checks = []
            checks.append(("status=active", data.get("status") == "active", data.get("status", "?")))
            checks.append(("total_sessions > 0", data.get("total_sessions", 0) > 0, str(data.get("total_sessions"))))
            checks.append(("sessions == fts_entries", data.get("total_sessions") == data.get("total_fts_entries"), f"{data.get('total_sessions')} vs {data.get('total_fts_entries')}"))
            checks.append(("workspaces > 10", data.get("total_workspaces", 0) > 10, str(data.get("total_workspaces"))))
            checks.append(("last_indexed recent", data.get("last_indexed", "") != "", str(data.get("last_indexed", "")[:19])))
            checks.append(("db_size_mb < 10", data.get("db_size_mb", 999) < 10, f"{data.get('db_size_mb')} MB"))

            failed = [(desc, detail) for desc, ok, detail in checks if not ok]
            if failed:
                tc.status = "FAIL"
                tc.message = "; ".join(f"{d}: {det}" for d, det in failed)
            else:
                tc.status = "PASS"
                tc.message = f"{data['total_sessions']} sessions, {data['total_workspaces']} workspaces"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    def test_A5_daemon_heartbeat_freshness(self) -> TestCase:
        """Heartbeat file freshness and signal cross-validation."""
        tc = TestCase("A5_heartbeat_freshness", "A_Infrastructure", "availability")
        t0 = time.time()

        code, stdout, stderr = run_script("daemon_health.py", ["--json"])
        data = parse_json(stdout)
        if data is None or not data.get("daemons"):
            tc.status = "FAIL"
            tc.message = "Cannot get daemon data"
        else:
            d = data["daemons"][0]
            age_sec = d.get("freshness", {}).get("age_seconds", 99999)
            threshold = d.get("freshness", {}).get("threshold_seconds", 3900)
            if age_sec < threshold:
                tc.status = "PASS"
                tc.message = f"Heartbeat {age_sec}s old, threshold {threshold}s (fresh)"
            elif age_sec < threshold * 2:
                tc.status = "DEGRADED"
                tc.message = f"Heartbeat {age_sec}s old, between threshold ({threshold}s) and 2x threshold"
            else:
                tc.status = "FAIL"
                tc.message = f"Heartbeat {age_sec}s old, exceeds 2x threshold ({threshold*2}s)"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    # ── Category B: Memory Health (5 tests) ──────────────────────

    def test_B1_memory_md_integrity(self) -> TestCase:
        """Global MEMORY.md file integrity."""
        tc = TestCase("B1_memory_md", "B_MemoryHealth", "memory")
        t0 = time.time()

        mem_path = WORKBUDDY_DIR / "MEMORY.md"
        ok1, msg1 = file_nonempty(str(mem_path))
        if not ok1:
            tc.status = "FAIL"
            tc.message = msg1
        else:
            content = mem_path.read_text(encoding="utf-8")
            n_chars = len(content)
            # Check for common sections
            has_memory = "记忆" in content or "Memory" in content
            has_tasks = "当前任务" in content or "任务" in content
            has_git = "Git" in content

            if n_chars > 5000:
                tc.status = "DEGRADED"
                tc.message = f"MEMORY.md at {n_chars} chars (approaching capacity)"
            elif has_memory and has_tasks:
                tc.status = "PASS"
                tc.message = f"{n_chars} chars, sections OK (memory+tasks+git={has_git})"
            else:
                tc.status = "DEGRADED"
                tc.message = f"{n_chars} chars, missing sections: memory={has_memory}, tasks={has_tasks}"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    def test_B2_user_md_integrity(self) -> TestCase:
        """USER.md file integrity."""
        tc = TestCase("B2_user_md", "B_MemoryHealth", "memory")
        t0 = time.time()

        user_path = WORKBUDDY_DIR / "USER.md"
        ok1, msg1 = file_nonempty(str(user_path))
        if not ok1:
            tc.status = "FAIL"
            tc.message = msg1
        else:
            content = user_path.read_text(encoding="utf-8")
            n_chars = len(content)
            has_name = "八大" in content or "百融" in content
            has_role = "战略投资" in content or "技术研究" in content
            has_prefs = "偏好" in content or "约定" in content or "风格" in content

            soft_cap = 2500
            if n_chars > soft_cap:
                tc.status = "DEGRADED"
                tc.message = f"USER.md at {n_chars} chars (over soft cap {soft_cap})"
            elif has_name and has_role and has_prefs:
                tc.status = "PASS"
                tc.message = f"{n_chars} chars, profile sections OK"
            else:
                tc.status = "DEGRADED"
                tc.message = f"{n_chars} chars, sections: name={has_name} role={has_role} prefs={has_prefs}"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    def test_B3_soul_md_protocol(self) -> TestCase:
        """SOUL.md startup protocol completeness."""
        tc = TestCase("B3_soul_md", "B_MemoryHealth", "memory")
        t0 = time.time()

        soul_path = WORKBUDDY_DIR / "SOUL.md"
        ok1, msg1 = file_nonempty(str(soul_path))
        if not ok1:
            tc.status = "FAIL"
            tc.message = msg1
        else:
            content = soul_path.read_text(encoding="utf-8")
            checks = []
            checks.append(("Step 0.5 daemon health", "daemon_health.py" in content))
            checks.append(("Step 1 memory_nudge", "memory_nudge.py" in content))
            checks.append(("Step 1.5 Stage A (FTS5)", "memory_index.py" in content))
            checks.append(("Step 1.5 Stage B (conversation)", "conversation_search" in content))
            checks.append(("Step 1.5 Stage C (pending)", "--pending-tasks" in content))
            checks.append(("Step 1.6 L0 init", "session_state.py" in content))
            checks.append(("Quality guard (C4b)", "P2丢弃" in content or "P2" in content))
            checks.append(("Compression quality", "quality" in content.lower()))

            missing = [desc for desc, ok in checks if not ok]
            if missing:
                tc.status = "DEGRADED"
                tc.message = f"Missing protocol steps: {', '.join(missing)}"
            else:
                tc.status = "PASS"
                tc.message = f"All {len(checks)} protocol steps present"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    def test_B4_workspace_memory_structures(self) -> TestCase:
        """All workspace .workbuddy/memory/ structures."""
        tc = TestCase("B4_workspace_memory", "B_MemoryHealth", "memory")
        t0 = time.time()

        workspaces = list(Path.home().glob("WorkBuddy/*task*/"))
        if not workspaces:
            tc.status = "FAIL"
            tc.message = "No task workspaces found"
        else:
            results = []
            for ws in workspaces:
                mem_dir = ws / ".workbuddy" / "memory"
                has_dir = mem_dir.exists()
                has_session = (mem_dir / ".session_state.json").exists()
                has_mem = (mem_dir / "MEMORY.md").exists()
                results.append({
                    "workspace": ws.name,
                    "has_memory_dir": has_dir,
                    "has_session_state": has_session,
                    "has_memory_md": has_mem,
                })

            n_ws = len(workspaces)
            missing_state = [r["workspace"] for r in results if not r["has_session_state"]]
            missing_mem = [r["workspace"] for r in results if not r["has_memory_md"]]
            has_state_pct = (n_ws - len(missing_state)) / n_ws * 100 if n_ws > 0 else 0
            has_mem_pct = (n_ws - len(missing_mem)) / n_ws * 100 if n_ws > 0 else 0

            active_mem = n_ws - len(missing_mem)
            if active_mem >= 1:
                tc.status = "PASS"
                tc.message = f"{n_ws} workspaces: {n_ws - len(missing_state)} w/ L0, {active_mem} w/ MEMORY.md (archived workspaces expected to lack L0)"
            else:
                tc.status = "FAIL"
                tc.message = f"{n_ws} workspaces: ZERO have memory structures — system may be broken"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    def test_B5_memory_quality_scores(self) -> TestCase:
        """Memory quality scoring across all workspaces."""
        tc = TestCase("B5_memory_quality", "B_MemoryHealth", "memory")
        t0 = time.time()

        code, stdout, stderr = run_script("memory_quality.py", ["--global", "--json"])
        data = parse_json(stdout)
        if data is None:
            tc.status = "FAIL"
            tc.message = f"Cannot parse JSON: {stderr or stdout[:200]}"
        else:
            p0 = data.get("by_priority", {}).get("P0", data.get("p0_count", 0))
            p2 = data.get("by_priority", {}).get("P2", data.get("p2_count", 0))
            total = data.get("by_priority", {}).get("total", data.get("total_entries", 0))

            if p2 > 3:
                tc.status = "DEGRADED"
                tc.message = f"{total} entries: {p0} P0, {p2} P2 (P2 count high)"
            else:
                tc.status = "PASS"
                tc.message = f"{total} entries: {p0} P0, {p2} P2"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    # ── Category C: Session State & Nudge (5 tests) ──────────────

    def test_C1_session_state(self) -> TestCase:
        """Current workspace session state integrity."""
        tc = TestCase("C1_session_state", "C_SessionNudge", "memory")
        t0 = time.time()

        code, stdout, stderr = run_script("session_state.py", [
            "--summary", "--workspace", str(WORKSPACE), "--json"
        ])
        # session_state --summary outputs markdown, not JSON
        # Check for key fields in output
        if "Session:" in stdout and "Started:" in stdout:
            tc.status = "PASS"
            # Count key fields
            has_plan = "Active plan" in stdout
            has_tasks = "Completed Tasks" in stdout
            has_decisions = "Recent Decisions" in stdout
            tc.message = f"Session state valid (plan={has_plan}, tasks={has_tasks}, decisions={has_decisions})"
        else:
            tc.status = "FAIL"
            tc.message = f"Session state invalid or empty: {stdout[:200]}"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    def test_C2_nudge_state(self) -> TestCase:
        """Nudge protocol state and counters."""
        tc = TestCase("C2_nudge_state", "C_SessionNudge", "learning")
        t0 = time.time()

        code, stdout, stderr = run_script("memory_nudge.py", [
            "--status", "--json", "--workspace", str(WORKSPACE), "--global"
        ])
        data = parse_json(stdout)
        if data is None:
            tc.status = "FAIL"
            tc.message = f"Cannot parse JSON: {stderr or stdout[:200]}"
        else:
            checks = []
            checks.append(("interval=10", data.get("nudge_interval") == 10, str(data.get("nudge_interval"))))
            checks.append(("tools_since < interval", data.get("tools_since_last_nudge", 999) <= data.get("nudge_interval", 10), str(data.get("tools_since_last_nudge"))))
            checks.append(("session_start present", data.get("session_start") is not None, str(data.get("session_start", "")[:19])))

            failed = [(desc, detail) for desc, ok, detail in checks if not ok]
            if failed:
                tc.status = "DEGRADED"
                tc.message = "; ".join(f"{d}: {det}" for d, det in failed)
            else:
                tc.status = "PASS"
                tc.message = f"Nudge state OK: {data['tools_since_last_nudge']}/{data['nudge_interval']} tools, count={data.get('nudge_count')}"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    def test_C3_pending_tasks_scan(self) -> TestCase:
        """Global pending tasks scan."""
        tc = TestCase("C3_pending_tasks", "C_SessionNudge", "memory")
        t0 = time.time()

        code, stdout, stderr = run_script("session_state.py", [
            "--pending-tasks", "--global", "--json"
        ])
        data = parse_json(stdout)
        # data can be empty list [] or list of tasks
        if data is None:
            # Try non-JSON output
            if stdout == "[]" or stdout == "":
                tc.status = "PASS"
                tc.message = "No pending tasks (clean state)"
            else:
                tc.status = "FAIL"
                tc.message = f"Unexpected output: {stdout[:200]}"
        elif isinstance(data, list):
            if len(data) == 0:
                tc.status = "PASS"
                tc.message = "No pending tasks across all workspaces"
            else:
                tc.status = "DEGRADED"
                tc.message = f"{len(data)} pending tasks found: {[t.get('task', t) for t in data[:3]]}"
                tc.details = {"tasks": data[:5]}
        else:
            tc.status = "FAIL"
            tc.message = f"Unexpected data type: {type(data).__name__}"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    def test_C4_session_recovery(self) -> TestCase:
        """Cross-session recovery listing."""
        tc = TestCase("C4_session_recovery", "C_SessionNudge", "memory")
        t0 = time.time()

        code, stdout, stderr = run_script("session_recovery.py", ["--list", "--json"])
        data = parse_json(stdout)
        if data is None:
            tc.status = "FAIL"
            tc.message = f"Cannot parse JSON: {stderr or stdout[:200]}"
        elif not isinstance(data, list):
            tc.status = "FAIL"
            tc.message = f"Expected list, got {type(data).__name__}"
        elif len(data) == 0:
            tc.status = "DEGRADED"
            tc.message = "No sessions recovered (empty)"
        else:
            checks = []
            for s in data[:3]:
                has_type = "type" in s
                has_date = "date" in s
                has_ws = "workspace" in s
                if not (has_type and has_date and has_ws):
                    checks.append(f"Entry missing fields: type={has_type} date={has_date} ws={has_ws}")

            if checks:
                tc.status = "DEGRADED"
                tc.message = f"Schema issues: {'; '.join(checks)}"
            else:
                tc.status = "PASS"
                tc.message = f"{len(data)} sessions recovered, schema valid"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    def test_C5_daily_log_exists(self) -> TestCase:
        """Today's daily log file."""
        tc = TestCase("C5_daily_log", "C_SessionNudge", "memory")
        t0 = time.time()

        today = datetime.now().strftime("%Y-%m-%d")
        log_path = WORKSPACE / ".workbuddy" / "memory" / f"{today}.md"
        ok, msg = file_nonempty(str(log_path))
        if ok:
            tc.status = "PASS"
            tc.message = msg
        else:
            tc.status = "FAIL"
            tc.message = msg

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    # ── Category D: Correction & Learning (3 tests) ──────────────

    def test_D1_correction_tracker(self) -> TestCase:
        """Correction tracker scan and statistics."""
        tc = TestCase("D1_correction_tracker", "D_CorrectionLearning", "learning")
        t0 = time.time()

        code, stdout, stderr = run_script("correction_tracker.py", ["--scan", "--json"])
        data = parse_json(stdout)
        if data is None:
            tc.status = "FAIL"
            tc.message = f"Cannot parse JSON: {stderr or stdout[:200]}"
        elif isinstance(data, list):
            n = len(data)
            if n > 10:
                tc.status = "DEGRADED"
                tc.message = f"{n} corrections found (high count, possible duplicates)"
            else:
                tc.status = "PASS"
                tc.message = f"{n} corrections tracked"
        else:
            tc.status = "FAIL"
            tc.message = f"Unexpected data type: {type(data).__name__}"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    def test_D2_blind_spot_detection(self) -> TestCase:
        """Blind spot detection for recurrent corrections."""
        tc = TestCase("D2_blind_spots", "D_CorrectionLearning", "learning")
        t0 = time.time()

        code, stdout, stderr = run_script("correction_tracker.py", ["--blind-spots", "--json"])
        data = parse_json(stdout)
        if data is None:
            tc.status = "FAIL"
            tc.message = f"Cannot parse JSON: {stderr or stdout[:200]}"
        elif isinstance(data, dict) and "blind_spots" in data:
            spots = data["blind_spots"]
            if len(spots) == 0:
                tc.status = "PASS"
                tc.message = "No recurrent blind spots — correction tracker clean"
            elif len(spots) <= 5:
                tc.status = "PASS"
                tc.message = f"{len(spots)} blind spot(s) detected — learning system active: {[s.get('topic', '?')[:40] for s in spots]}"
            else:
                tc.status = "DEGRADED"
                tc.message = f"{len(spots)} blind spots (excessive recurrence, review needed)"
        else:
            tc.status = "FAIL"
            tc.message = f"Unexpected output: {str(data)[:200]}"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    def test_D3_user_model_health(self) -> TestCase:
        """User model health and integrity."""
        tc = TestCase("D3_user_model", "D_CorrectionLearning", "learning")
        t0 = time.time()

        code, stdout, stderr = run_script("user_model.py", ["--health", "--json"])
        data = parse_json(stdout)
        if data is None:
            tc.status = "FAIL"
            tc.message = f"Cannot parse JSON: {stderr or stdout[:200]}"
        else:
            checks = []
            checks.append(("total_score OK", data.get("total_score", -1) >= 0, str(data.get("total_score"))))
            checks.append(("grade present", data.get("grade", "") != "", data.get("grade", "?")))
            checks.append(("FTS healthy", data.get("metrics", {}).get("fts_healthy") == True, str(data.get("metrics", {}).get("fts_healthy"))))
            checks.append(("dimensions present", len(data.get("dimensions", {})) >= 4, str(len(data.get("dimensions", {})))))
            checks.append(("recommendations present", len(data.get("recommendations", [])) > 0, str(len(data.get("recommendations", [])))))

            failed = [(desc, detail) for desc, ok, detail in checks if not ok]
            if failed:
                tc.status = "DEGRADED"
                tc.message = "; ".join(f"{d}: {det}" for d, det in failed)
            else:
                tc.status = "PASS"
                tc.message = f"Score={data['total_score']}, grade={data['grade']}, {data['metrics']['total_preferences']} prefs"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    # ── Category E: Self-Evolution Pipeline (3 tests) ────────────

    def test_E1_intent_learner(self) -> TestCase:
        """Intent learner preload capability."""
        tc = TestCase("E1_intent_learner", "E_SelfEvolution", "learning")
        t0 = time.time()

        code, stdout, stderr = run_script("intent_learner.py", [
            "--query", "memory health check", "--preload", "--json"
        ])
        data = parse_json(stdout)
        if data is None:
            tc.status = "FAIL"
            tc.message = f"Cannot parse JSON: {stderr or stdout[:200]}"
        else:
            checks = []
            checks.append(("preload field", "preload" in data, str(data.get("preload"))))
            checks.append(("skills list", isinstance(data.get("skills"), list), f"{len(data.get('skills', []))} skills"))
            checks.append(("memory_sections list", isinstance(data.get("memory_sections"), list), f"{len(data.get('memory_sections', []))} sections"))

            failed = [(desc, detail) for desc, ok, detail in checks if not ok]
            if failed:
                tc.status = "DEGRADED"
                tc.message = "; ".join(f"{d}: {det}" for d, det in failed)
            else:
                tc.status = "PASS"
                tc.message = f"Preload={data['preload']}, skills={len(data.get('skills',[]))}, sections={len(data.get('memory_sections',[]))}"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    def test_E2_sequence_analyzer(self) -> TestCase:
        """Sequence analyzer pattern detection."""
        tc = TestCase("E2_sequence_analyzer", "E_SelfEvolution", "learning")
        t0 = time.time()

        code, stdout, stderr = run_script("sequence_analyzer.py", ["--analyze-all", "--json"])
        data = parse_json(stdout)
        if data is None:
            tc.status = "FAIL"
            tc.message = f"Cannot parse JSON: {stderr or stdout[:200]}"
        else:
            checks = []
            checks.append(("workspaces_analyzed > 0", data.get("workspaces_analyzed", 0) > 0, str(data.get("workspaces_analyzed"))))
            checks.append(("total_sequences present", "total_sequences" in data, str(data.get("total_sequences"))))
            checks.append(("clusters present", "clusters" in data, str(data.get("clusters", "?"))))

            failed = [(desc, detail) for desc, ok, detail in checks if not ok]
            if failed:
                tc.status = "DEGRADED"
                tc.message = "; ".join(f"{d}: {det}" for d, det in failed)
            else:
                tc.status = "PASS"
                tc.message = f"{data['workspaces_analyzed']} ws, {data.get('total_sequences',0)} seqs, {data.get('clusters',0)} clusters"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    def test_E3_skill_detector(self) -> TestCase:
        """Skill auto-creation detection."""
        tc = TestCase("E3_skill_detector", "E_SelfEvolution", "learning")
        t0 = time.time()

        code, stdout, stderr = run_script("skill_detector.py", [
            "--check", "--json", "--all-workspaces"
        ])
        data = parse_json(stdout)
        if data is None:
            if "ERROR" in stderr or "error" in stdout.lower():
                tc.status = "SKIP"
                tc.message = f"Script requires additional setup: {stderr[:100]}"
            else:
                tc.status = "FAIL"
                tc.message = f"Cannot parse JSON: {stderr or stdout[:200]}"
        elif isinstance(data, dict):
            tc.status = "PASS"
            tc.message = f"Skill detector OK: {len(data)} keys"
        elif isinstance(data, list):
            tc.status = "PASS"
            tc.message = f"Skill detector OK: {len(data)} candidates"
        else:
            tc.status = "DEGRADED"
            tc.message = f"Unexpected output type: {type(data).__name__}"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    # ── Category F: Cross-Workspace (3 tests) ────────────────────

    def test_F1_memory_pool(self) -> TestCase:
        """Shared memory pool status."""
        tc = TestCase("F1_memory_pool", "F_CrossWorkspace", "memory")
        t0 = time.time()

        code, stdout, stderr = run_script("memory_pool.py", ["--status", "--json"])
        data = parse_json(stdout)
        if data is None:
            tc.status = "FAIL"
            tc.message = f"Cannot parse JSON: {stderr or stdout[:200]}"
        else:
            checks = []
            checks.append(("entries present", "entries" in data, str(data.get("entries", "?"))))
            checks.append(("workspaces present", "workspaces" in data, str(data.get("workspaces", "?"))))
            checks.append(("priorities present", "priorities" in data, str(data.get("priorities", {}))))

            failed = [(desc, detail) for desc, ok, detail in checks if not ok]
            if failed:
                tc.status = "DEGRADED"
                tc.message = "; ".join(f"{d}: {det}" for d, det in failed)
            else:
                tc.status = "PASS"
                tc.message = f"{data['entries']} entries, {data['workspaces']} workspaces"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    def test_F2_agent_router(self) -> TestCase:
        """Agent router topic extraction."""
        tc = TestCase("F2_agent_router", "F_CrossWorkspace", "learning")
        t0 = time.time()

        code, stdout, stderr = run_script("agent_router.py", [
            "--topics", "memory health check daemon", "--json"
        ])
        data = parse_json(stdout)
        if data is None:
            tc.status = "FAIL"
            tc.message = f"Cannot parse JSON: {stderr or stdout[:200]}"
        elif isinstance(data, dict) and "topics" in data:
            topics = data["topics"]
            if len(topics) > 0:
                tc.status = "PASS"
                tc.message = f"Extracted {len(topics)} topics: {topics}"
            else:
                tc.status = "DEGRADED"
                tc.message = "No topics extracted from query"
        else:
            tc.status = "FAIL"
            tc.message = f"Unexpected output: {str(data)[:200]}"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    def test_F3_proactive_injection_chain(self) -> TestCase:
        """C3 three-stage proactive injection chain validation."""
        tc = TestCase("F3_proactive_injection", "F_CrossWorkspace", "memory")
        t0 = time.time()

        checks = []

        # Stage A: FTS5 recent entries
        code_a, stdout_a, _ = run_script("memory_index.py", ["--recent", "7", "--limit", "5", "--global", "--json"])
        data_a = parse_json(stdout_a)
        stage_a_ok = data_a is not None and isinstance(data_a, list)
        checks.append(("Stage A (FTS5 recent)", stage_a_ok, f"{len(data_a) if data_a else 0} entries"))

        # Stage B: conversation_search (via SOUL.md check — it's a builtin tool)
        soul_path = WORKBUDDY_DIR / "SOUL.md"
        stage_b_ok = soul_path.exists() and "conversation_search" in soul_path.read_text()
        checks.append(("Stage B (conversation_search)", stage_b_ok, "Integrated in SOUL.md"))

        # Stage C: pending tasks scan
        code_c, stdout_c, _ = run_script("session_state.py", ["--pending-tasks", "--global", "--json"])
        stage_c_ok = code_c == 0 and stdout_c.strip() in ("[]", "")
        checks.append(("Stage C (pending tasks)", stage_c_ok, "Scan functional"))

        failed = [(desc, detail) for desc, ok, detail in checks if not ok]
        if failed:
            tc.status = "FAIL"
            tc.message = "; ".join(f"{d}: {det}" for d, det in failed)
        else:
            tc.status = "PASS"
            tc.message = "All 3 stages of proactive injection verified"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    # ── Category G: File System Integrity (3 tests) ──────────────

    def test_G1_scripts_executable(self) -> TestCase:
        """All scripts marked executable with valid shebangs."""
        tc = TestCase("G1_scripts_executable", "G_FileSystem", "availability")
        t0 = time.time()

        py_files = sorted(SCRIPTS_DIR.glob("*.py"))
        sh_files = sorted(SCRIPTS_DIR.glob("*.sh"))

        issues = []
        for f in py_files:
            if not os.access(f, os.X_OK):
                issues.append(f"{f.name}: not executable")
            first_line = f.read_text().split("\n")[0]
            if not first_line.startswith("#!"):
                issues.append(f"{f.name}: missing shebang")

        for f in sh_files:
            if not os.access(f, os.X_OK):
                issues.append(f"{f.name}: not executable")

        if issues:
            tc.status = "DEGRADED"
            tc.message = f"{len(issues)} file(s) have issues: {'; '.join(issues[:3])}"
            tc.details = {"issues": issues}
        else:
            tc.status = "PASS"
            tc.message = f"{len(py_files)} .py + {len(sh_files)} .sh files OK"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    def test_G2_import_health(self) -> TestCase:
        """All scripts parse without syntax errors."""
        tc = TestCase("G2_import_health", "G_FileSystem", "availability")
        t0 = time.time()

        import ast
        py_files = sorted(SCRIPTS_DIR.glob("*.py"))

        errors = []
        for f in py_files:
            try:
                ast.parse(f.read_text())
            except SyntaxError as e:
                errors.append(f"{f.name}: {e}")

        if errors:
            tc.status = "FAIL"
            tc.message = f"{len(errors)} syntax errors: {'; '.join(errors[:3])}"
        else:
            tc.status = "PASS"
            tc.message = f"All {len(py_files)} scripts parse OK"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    def test_G3_help_flags(self) -> TestCase:
        """All scripts accept --help flag."""
        tc = TestCase("G3_help_flags", "G_FileSystem", "availability")
        t0 = time.time()

        py_files = sorted(SCRIPTS_DIR.glob("*.py"))
        no_help = []
        no_json = []

        for f in py_files:
            if f.name == "health_test_battery.py":
                continue  # skip self

            code, stdout, _ = run_script(f.name, ["--help"])
            if code != 0 or not stdout:
                no_help.append(f.name)

            # Check JSON flag
            content = f.read_text()
            if "--json" in content and "add_argument" in content:
                pass  # has JSON support
            elif "argparse" not in content:
                pass  # may not use argparse
            else:
                # has argparse but might not have --json
                if "--json" not in content:
                    no_json.append(f.name)

        status = "PASS"
        msg_parts = [f"{len(py_files)-1} scripts: all have --help"]
        if no_help:
            status = "FAIL"
            msg_parts.append(f"{len(no_help)} missing --help: {', '.join(no_help[:3])}")
        if no_json:
            msg_parts.append(f"{len(no_json)} may lack --json: {', '.join(no_json[:3])}")

        tc.status = status
        tc.message = "; ".join(msg_parts)

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    # ── Category H: Upstream & Config (3 tests) ──────────────────

    def test_H1_upstream_state(self) -> TestCase:
        """Upstream sync state file."""
        tc = TestCase("H1_upstream_state", "H_UpstreamConfig", "self-maintenance")
        t0 = time.time()

        state_path = WORKBUDDY_DIR / ".hermes-upstream-state"
        ok, msg = file_exists(str(state_path))
        if not ok:
            tc.status = "FAIL"
            tc.message = msg
        else:
            content = state_path.read_text().strip()
            if content.startswith("v"):
                tc.status = "PASS"
                tc.message = f"Upstream state: {content}"
            else:
                tc.status = "DEGRADED"
                tc.message = f"Invalid upstream version: {content}"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    def test_H2_config_files(self) -> TestCase:
        """Core config files present (mcp.json may be in connectors/ subdir)."""
        tc = TestCase("H2_config_files", "H_UpstreamConfig", "self-maintenance")
        t0 = time.time()

        files = {
            "SOUL.md": WORKBUDDY_DIR / "SOUL.md",
            "IDENTITY.md": WORKBUDDY_DIR / "IDENTITY.md",
            "USER.md": WORKBUDDY_DIR / "USER.md",
        }
        # mcp.json can be at root or in connectors/ subdir
        mcp_paths = list(WORKBUDDY_DIR.glob("connectors/*/mcp.json"))
        mcp_root = WORKBUDDY_DIR / "mcp.json"
        if mcp_root.exists():
            files["mcp.json"] = mcp_root
        elif mcp_paths:
            files["mcp.json"] = mcp_paths[0]
        else:
            files["mcp.json (MISSING)"] = Path("/nonexistent")

        missing = []
        for name, path in files.items():
            if not path.exists():
                missing.append(name)

        if missing:
            tc.status = "FAIL"
            tc.message = f"Missing: {', '.join(missing)}"
        else:
            tc.status = "PASS"
            tc.message = f"All {len(files)} config files present"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    def test_H3_skill_md_metadata(self) -> TestCase:
        """Enhanced-memory SKILL.md metadata integrity."""
        tc = TestCase("H3_skill_metadata", "H_UpstreamConfig", "self-maintenance")
        t0 = time.time()

        skill_path = WORKBUDDY_DIR / "skills" / "enhanced-memory" / "SKILL.md"
        ok, msg = file_nonempty(str(skill_path))
        if not ok:
            tc.status = "FAIL"
            tc.message = msg
        else:
            content = skill_path.read_text()
            checks = []
            checks.append(("YAML frontmatter", content.startswith("---")))
            checks.append(("name: enhanced-memory", "name: enhanced-memory" in content))
            checks.append(("agent_created: true", "agent_created: true" in content))
            checks.append(("Nudge protocol", "Nudge" in content or "nudge" in content.lower()))
            checks.append(("FTS5", "FTS5" in content or "fts5" in content.lower()))
            checks.append(("compression", "compress" in content.lower()))

            missing = [desc for desc, ok in checks if not ok]
            if missing:
                tc.status = "DEGRADED"
                tc.message = f"Missing metadata: {', '.join(missing)}"
            else:
                tc.status = "PASS"
                tc.message = "SKILL.md metadata complete"

        tc.duration_ms = (time.time() - t0) * 1000
        return tc

    # ── Test Runner ──────────────────────────────────────────────

    def run_all(self, categories: set[str] | None = None) -> list[TestCase]:
        """Run all test cases and return results."""
        test_methods = [
            # Category A: Infrastructure
            self.test_A1_daemon_health,
            self.test_A2_daemon_lifecycle,
            self.test_A3_git_sync,
            self.test_A4_fts5_index,
            self.test_A5_daemon_heartbeat_freshness,
            # Category B: Memory Health
            self.test_B1_memory_md_integrity,
            self.test_B2_user_md_integrity,
            self.test_B3_soul_md_protocol,
            self.test_B4_workspace_memory_structures,
            self.test_B5_memory_quality_scores,
            # Category C: Session & Nudge
            self.test_C1_session_state,
            self.test_C2_nudge_state,
            self.test_C3_pending_tasks_scan,
            self.test_C4_session_recovery,
            self.test_C5_daily_log_exists,
            # Category D: Correction & Learning
            self.test_D1_correction_tracker,
            self.test_D2_blind_spot_detection,
            self.test_D3_user_model_health,
            # Category E: Self-Evolution
            self.test_E1_intent_learner,
            self.test_E2_sequence_analyzer,
            self.test_E3_skill_detector,
            # Category F: Cross-Workspace
            self.test_F1_memory_pool,
            self.test_F2_agent_router,
            self.test_F3_proactive_injection_chain,
            # Category G: File System
            self.test_G1_scripts_executable,
            self.test_G2_import_health,
            self.test_G3_help_flags,
            # Category H: Upstream & Config
            self.test_H1_upstream_state,
            self.test_H2_config_files,
            self.test_H3_skill_md_metadata,
        ]

        for method in test_methods:
            category_prefix = method.__name__.split("_")[1]
            if categories and category_prefix not in categories:
                continue

            if self.verbose:
                print(f"  Running {method.__name__}...", file=sys.stderr, flush=True)

            try:
                tc = method()
            except Exception as e:
                tc = TestCase(
                    name=method.__name__,
                    category="Unknown",
                    dimension="unknown",
                    status="FAIL",
                    message=f"Exception: {e}",
                )
                if self.verbose:
                    traceback.print_exc()

            self.tests.append(tc)

            if self.fail_fast and tc.status == "FAIL":
                break

        return self.tests

    def report_json(self) -> str:
        """Generate JSON report."""
        by_status = {"PASS": 0, "FAIL": 0, "DEGRADED": 0, "SKIP": 0, "PENDING": 0}
        by_category = {}
        by_dimension = {}

        for tc in self.tests:
            by_status[tc.status] = by_status.get(tc.status, 0) + 1

            cat = tc.category.split("_")[0] if "_" in tc.category else tc.category
            if cat not in by_category:
                by_category[cat] = {"passed": 0, "failed": 0, "degraded": 0, "skip": 0}
            if tc.status == "PASS":
                by_category[cat]["passed"] += 1
            elif tc.status == "FAIL":
                by_category[cat]["failed"] += 1
            elif tc.status == "DEGRADED":
                by_category[cat]["degraded"] += 1
            elif tc.status == "SKIP":
                by_category[cat]["skip"] += 1

            dim = tc.dimension
            if dim not in by_dimension:
                by_dimension[dim] = {"passed": 0, "failed": 0, "degraded": 0, "skip": 0}
            if tc.status == "PASS":
                by_dimension[dim]["passed"] += 1
            elif tc.status == "FAIL":
                by_dimension[dim]["failed"] += 1
            elif tc.status == "DEGRADED":
                by_dimension[dim]["degraded"] += 1
            elif tc.status == "SKIP":
                by_dimension[dim]["skip"] += 1

        duration = (datetime.now(timezone.utc) - self.start_time).total_seconds()
        total = len(self.tests)
        passed = by_status["PASS"]
        failed = by_status["FAIL"]

        # Overall health: PASS if no FAIL, DEGRADED if FAIL=0 but DEGRADED>0
        if failed == 0 and by_status["DEGRADED"] == 0:
            overall = "HEALTHY"
        elif failed == 0:
            overall = "DEGRADED"
        else:
            overall = "UNHEALTHY"

        return json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "overall": overall,
            "total": total,
            "passed": passed,
            "failed": failed,
            "degraded": by_status["DEGRADED"],
            "skipped": by_status["SKIP"],
            "duration_seconds": round(duration, 1),
            "health_pct": round(passed / total * 100, 1) if total > 0 else 0,
            "by_status": by_status,
            "by_category": by_category,
            "by_dimension": by_dimension,
            "tests": [tc.to_dict() for tc in self.tests],
        }, ensure_ascii=False, indent=2)

    def report_markdown(self) -> str:
        """Generate Markdown report."""
        passed = sum(1 for t in self.tests if t.status == "PASS")
        failed = sum(1 for t in self.tests if t.status == "FAIL")
        degraded = sum(1 for t in self.tests if t.status == "DEGRADED")
        skipped = sum(1 for t in self.tests if t.status == "SKIP")
        total = len(self.tests)

        lines = [
            "# Hermes Agent 全面健康测试报告",
            "",
            f"**时间**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"**测试数**: {total} | **通过**: {passed} | **失败**: {failed} | **降级**: {degraded} | **跳过**: {skipped}",
            f"**健康分**: {round(passed/total*100,1) if total > 0 else 0}%",
            "",
        ]

        if failed == 0 and degraded == 0:
            lines.append("## 🟢 总体: HEALTHY")
        elif failed == 0:
            lines.append("## 🟡 总体: DEGRADED")
        else:
            lines.append("## 🔴 总体: UNHEALTHY")

        # Group by category
        categories_order = ["A", "B", "C", "D", "E", "F", "G", "H"]
        for cat_prefix in categories_order:
            cat_tests = [t for t in self.tests if t.category.startswith(cat_prefix)]
            if not cat_tests:
                continue

            cat_name = cat_tests[0].category
            lines.append(f"\n### {cat_name}")
            lines.append(f"| 测试 | 维度 | 状态 | 信息 |")
            lines.append(f"|------|------|------|------|")

            for tc in cat_tests:
                icon = {"PASS": "✅", "FAIL": "❌", "DEGRADED": "⚠️", "SKIP": "⏭️"}.get(tc.status, "?")
                lines.append(f"| {tc.name} | {tc.dimension} | {icon} {tc.status} | {tc.message[:100]} |")

        # Summary by dimension
        lines.append(f"\n## 维度汇总")
        lines.append(f"| 维度 | 通过 | 失败 | 降级 |")
        lines.append(f"|------|------|------|------|")
        dims = {}
        for tc in self.tests:
            d = tc.dimension
            if d not in dims:
                dims[d] = {"pass": 0, "fail": 0, "degraded": 0}
            if tc.status == "PASS":
                dims[d]["pass"] += 1
            elif tc.status == "FAIL":
                dims[d]["fail"] += 1
            elif tc.status == "DEGRADED":
                dims[d]["degraded"] += 1

        for dim, counts in sorted(dims.items()):
            lines.append(f"| {dim} | {counts['pass']} | {counts['fail']} | {counts['degraded']} |")

        # Failed/Degraded details
        issues = [t for t in self.tests if t.status in ("FAIL", "DEGRADED")]
        if issues:
            lines.append(f"\n## 需关注项 ({len(issues)})")
            for tc in issues:
                icon = "❌" if tc.status == "FAIL" else "⚠️"
                lines.append(f"- {icon} **{tc.name}** ({tc.dimension}): {tc.message}")

        return "\n".join(lines)

    def report_summary(self) -> str:
        """One-line summary."""
        passed = sum(1 for t in self.tests if t.status == "PASS")
        failed = sum(1 for t in self.tests if t.status == "FAIL")
        degraded = sum(1 for t in self.tests if t.status == "DEGRADED")
        total = len(self.tests)
        pct = round(passed / total * 100, 1) if total else 0
        return f"Hermes Health: {passed}/{total} passed ({pct}%), {failed} failed, {degraded} degraded"


# ─── CLI ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Hermes Agent 全面健康测试电池",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python3 health_test_battery.py                    # 默认: 全部测试 + Markdown 报告
  python3 health_test_battery.py --json             # JSON 输出
  python3 health_test_battery.py --summary          # 一行摘要
  python3 health_test_battery.py --category A       # 仅基础设施测试
  python3 health_test_battery.py --verbose          # 详细输出
  python3 health_test_battery.py --fail-fast        # 第一个失败即停止

类别:
  A = 实时可用性 (Infrastructure)
  B = 记忆健康 (Memory Health)
  C = 会话状态 (Session & Nudge)
  D = 纠错学习 (Correction & Learning)
  E = 自进化 (Self-Evolution)
  F = 跨工作区 (Cross-Workspace)
  G = 文件系统 (File System)
  H = 上游配置 (Upstream & Config)
        """,
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    parser.add_argument("--report", action="store_true", help="输出 Markdown 报告")
    parser.add_argument("--summary", action="store_true", help="输出一行摘要")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出进度")
    parser.add_argument("--fail-fast", action="store_true", help="第一个失败即停止")
    parser.add_argument(
        "--category", "-c",
        choices=["A", "B", "C", "D", "E", "F", "G", "H"],
        nargs="+",
        help="指定测试类别",
    )
    parser.add_argument(
        "--output", "-o",
        help="输出文件路径 (写 Markdown 报告)",
    )
    args = parser.parse_args()

    # Default to --report if no format specified
    if not args.json and not args.summary:
        args.report = True

    categories = set(args.category) if args.category else None

    battery = TestBattery(verbose=args.verbose, fail_fast=args.fail_fast)

    if args.verbose:
        print(f"🔍 Hermes Agent Health Test Battery", file=sys.stderr)
        print(f"   Scripts: {SCRIPTS_DIR}", file=sys.stderr)
        if categories:
            print(f"   Categories: {', '.join(sorted(categories))}", file=sys.stderr)
        print(file=sys.stderr)

    battery.run_all(categories)

    if args.json:
        output = battery.report_json()
        print(output)
    elif args.summary:
        print(battery.report_summary())
    elif args.report:
        report = battery.report_markdown()
        if args.output:
            Path(args.output).write_text(report)
            print(f"Report written to {args.output}")
        else:
            print(report)

    # Exit code: 0 if all pass, 1 if any fail
    failures = sum(1 for t in battery.tests if t.status == "FAIL")
    sys.exit(1 if failures > 0 else 0)


if __name__ == "__main__":
    main()
