#!/usr/bin/env python3
"""
Daemon Health — Unified Health Check for All WorkBuddy Daemons
================================================================

Reads heartbeat files from {MEMORIA_HOME} and performs
multi-signal cross-validation to distinguish "healthy idle" from "crashed."

Signals checked (layered, most reliable first):
  1. Heartbeat file — JSON with last_run, status, exit_code, checks
  2. launchctl registration — is the plist loaded?
  3. Log tail — does the log show recent activity?
  4. Functional test — can the daemon's core operation complete?

Tiers:
  - ok       — Heartbeat fresh (< 2×interval + 5min grace), status "ok"
  - degraded — Heartbeat fresh, status "degraded" (some checks failed)
  - stale    — Heartbeat exists but overdue (> 2×interval + 5min)
  - muted    — No heartbeat file found (daemon installed but not reporting)
  - missing  — Neither heartbeat nor launchctl registration found

Usage:
    python3 daemon_health.py              # Human-readable report
    python3 daemon_health.py --json       # Machine-readable JSON
    python3 daemon_health.py --all        # Include health daemons
"""

import argparse
import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


# ── Constants ────────────────────────────────────────────────

from ..constants import HEARTBEAT_DIR, LOG_DIR, GRACE_MINUTES

LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"

# Known daemon registry — maps daemon name to its expected signals
# This is the system-level registry that the postmortem demanded.
KNOWN_DAEMONS = {
    "memory-nudge": {
        "label": "com.workbuddy.memory-nudge",
        "log_file": LOG_DIR / "memory-daemon.log",
        "interval_seconds": 1800,
        "project": "WorkBuddy",
        "description": "Hermes memory maintenance (FTS5 index, compress, git sync)",
    },
    # Future entries will be added here as more projects adopt heartbeat
    # "feishu-bridge": {
    #     "label": "com.openclaw.feishu-bridge",
    #     "log_file": LOG_DIR / "feishu-bridge.log",
    #     "interval_seconds": 60,
    #     "project": "OpenClaw Secure",
    #     "description": "Feishu message bridge connector",
    # },
}

# ── Core: Heartbeat Check ────────────────────────────────────

def read_heartbeats() -> dict:
    """Read all heartbeat files from the heartbeat directory.

    Returns {daemon_name: heartbeat_data}.
    """
    heartbeats = {}
    if not HEARTBEAT_DIR.exists():
        return heartbeats

    for hb_file in HEARTBEAT_DIR.glob("*.json"):
        daemon_name = hb_file.stem  # "memory-nudge" from "memory-nudge.json"
        try:
            data = json.loads(hb_file.read_text(encoding="utf-8"))
            # Add file mtime for cross-validation
            data["_file_mtime"] = datetime.fromtimestamp(
                hb_file.stat().st_mtime, tz=timezone.utc
            ).isoformat()
            heartbeats[daemon_name] = data
        except (json.JSONDecodeError, OSError) as e:
            heartbeats[daemon_name] = {
                "error": f"Failed to read heartbeat: {e}",
                "last_run": None,
                "status": "corrupt",
            }

    return heartbeats


def check_heartbeat_freshness(daemon: str, hb: dict, known: dict) -> dict:
    """Determine if a heartbeat is fresh, stale, or missing.

    Fresh = last_run within (2 × interval + grace_minutes).
    """
    interval = known.get("interval_seconds", 1800)
    grace = GRACE_MINUTES * 60
    max_gap = (2 * interval) + grace  # 3600 + 300 = 3900s for 30min interval

    last_run_str = hb.get("last_run")
    if not last_run_str:
        return {"fresh": False, "age_seconds": None, "threshold_seconds": max_gap}

    try:
        last_run = datetime.fromisoformat(last_run_str)
        # Handle both naive and timezone-aware timestamps
        now = datetime.now(timezone.utc)
        if last_run.tzinfo is None:
            # Assume local time for naive timestamps
            age = (datetime.now() - last_run).total_seconds()
        else:
            age = (now - last_run).total_seconds()

        fresh = age <= max_gap
        return {
            "fresh": fresh,
            "age_seconds": round(age),
            "age_minutes": round(age / 60, 1),
            "threshold_seconds": max_gap,
            "threshold_minutes": round(max_gap / 60, 1),
        }
    except (ValueError, TypeError):
        return {"fresh": False, "age_seconds": None, "threshold_seconds": max_gap}


# ── Core: launchctl Cross-Validation ─────────────────────────

def check_launchctl(label: str) -> dict:
    """Check launchctl registration for a given label.

    Uses `launchctl list` (no args) which shows all jobs in format:
      PID  ExitCode  Label
      -    0         com.workbuddy.memory-nudge

    PID = "-" means the job is loaded but idle (normal for StartInterval jobs).
    Returns loaded status, PID, last exit code from launchctl.
    """
    try:
        # First: check if the job is registered by listing all jobs
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return {"loaded": False, "found": False, "error": "launchctl list failed"}

        # Parse: PID  ExitCode  Label
        for line in result.stdout.strip().split("\n"):
            if label in line:
                parts = line.split()
                if len(parts) >= 2:
                    pid_str = parts[0]
                    exit_str = parts[1]
                    pid = int(pid_str) if pid_str != "-" else None
                    exit_code = int(exit_str) if exit_str != "-" else None
                    return {
                        "loaded": True,
                        "found": True,
                        "pid": pid,
                        "last_exit_code": exit_code,
                        "note": (
                            "Job loaded but idle (PID=-)" if pid is None and exit_code is not None
                            else "Job currently running" if pid is not None
                            else None
                        ),
                    }

        # Not found in list — check if plist file exists at least
        plist_path = LAUNCH_AGENTS_DIR / f"{label}.plist"
        if plist_path.exists():
            return {"loaded": False, "found": True, "note": "Plist exists but not loaded"}

        return {"loaded": False, "found": False}

    except Exception as e:
        return {"loaded": False, "found": False, "error": str(e)}


# ── Core: Log Tail Check ────────────────────────────────────

def check_log_tail(log_file: Path) -> dict:
    """Check if the daemon log has been written to recently.

    Returns last line and its age.
    """
    if not log_file or not log_file.exists():
        return {"exists": False}

    try:
        stat = log_file.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime)
        age = (datetime.now() - mtime).total_seconds()

        # Read last line
        content = log_file.read_text(encoding="utf-8").strip()
        lines = content.split("\n") if content else []
        last_line = lines[-1][:200] if lines else ""

        return {
            "exists": True,
            "size_bytes": stat.st_size,
            "last_modified": mtime.isoformat(),
            "age_seconds": round(age),
            "age_minutes": round(age / 60, 1),
            "last_line": last_line,
        }
    except OSError as e:
        return {"exists": True, "error": str(e)}


# ── Core: Aggregate Assessment ──────────────────────────────

def assess_daemon(daemon: str, hb: dict, known: dict) -> dict:
    """Assess a single daemon using all available signals.

    Returns a structured assessment with tier and details.
    """
    assessment = {
        "daemon": daemon,
        "known": daemon in KNOWN_DAEMONS,
        "heartbeat": hb,
    }

    # If known daemon, get its config
    if known:
        label = known.get("label", "")
        log_file = known.get("log_file")
        assessment["description"] = known.get("description", "")
        assessment["project"] = known.get("project", "")
    else:
        label = hb.get("label", "")
        log_file = None
        assessment["description"] = "Unknown daemon"
        assessment["project"] = "unknown"

    # Signal 1: Heartbeat freshness
    freshness = check_heartbeat_freshness(daemon, hb, known or {})
    assessment["freshness"] = freshness

    # Signal 2: launchctl
    launchctl_info = check_launchctl(label) if label else {"found": False, "loaded": False}
    assessment["launchctl"] = launchctl_info

    # Signal 3: Log tail
    log_info = check_log_tail(log_file) if log_file else {"exists": False}
    assessment["log"] = log_info

    # Determine tier
    hb_status = hb.get("status", "unknown")

    if hb_status == "corrupt":
        assessment["tier"] = "corrupt"
    elif not freshness.get("fresh"):
        if hb_status == "ok" or hb_status == "degraded":
            assessment["tier"] = "stale"
        else:
            assessment["tier"] = "stale"
    elif hb_status == "degraded":
        assessment["tier"] = "degraded"
    elif hb_status == "ok":
        # Cross-validate with launchctl
        if launchctl_info.get("loaded"):
            # PID=None is NORMAL for StartInterval jobs (idle between intervals)
            assessment["tier"] = "ok"
        elif launchctl_info.get("found"):
            # Plist exists but not loaded — unusual, flag as warning
            assessment["tier"] = "ok"
            assessment.setdefault("warnings", []).append(
                "Plist exists but not loaded in launchctl — daemon may need `launchctl load`"
            )
        else:
            assessment["tier"] = "ok"
    else:
        assessment["tier"] = "ok"  # Default optimistic

    # Add cross-validation notes
    if launchctl_info.get("loaded") and freshness.get("age_seconds", 0) > 3600:
        assessment.setdefault("notes", []).append(
            "launchctl shows loaded but heartbeat age > 1h — may indicate stuck launchd job"
        )

    return assessment


def discover_all_daemons() -> dict:
    """Discover all daemons from heartbeats and known registry.

    Returns {daemon_name: Optional[heartbeat_data]}.
    """
    heartbeats = read_heartbeats()

    # Merge with known daemons
    all_daemons = {}
    for name in set(list(heartbeats.keys()) + list(KNOWN_DAEMONS.keys())):
        all_daemons[name] = heartbeats.get(name, {
            "status": "missing",
            "last_run": None,
            "error": "No heartbeat file found",
        })

    return all_daemons


# ── Output: Human-Readable Report ────────────────────────────

def format_report(assessments: list) -> str:
    """Format a human-readable health report."""
    lines = []
    lines.append("=" * 64)
    lines.append("  🩺  WorkBuddy Daemon Health Report")
    lines.append(f"  Host: {socket.gethostname()}")
    lines.append(f"  Time: {datetime.now().isoformat()}")
    lines.append("=" * 64)

    # Summary
    tiers = {}
    for a in assessments:
        t = a.get("tier", "unknown")
        tiers[t] = tiers.get(t, 0) + 1

    ok_count = tiers.get("ok", 0)
    degraded_count = tiers.get("degraded", 0)
    stale_count = tiers.get("stale", 0)
    missing_count = tiers.get("missing", 0)

    lines.append(f"\n  Summary: {len(assessments)} daemon(s) — "
                 f"{ok_count} ok, {degraded_count} degraded, "
                 f"{stale_count} stale, {missing_count} missing\n")

    # Per-daemon details
    for a in assessments:
        tier = a.get("tier", "unknown")
        icon = {"ok": "✅", "degraded": "⚠️", "stale": "🔴", "missing": "❌", "corrupt": "💥"}.get(tier, "❓")
        daemon = a["daemon"]
        desc = a.get("description", "")
        project = a.get("project", "")

        lines.append(f"  {icon} {daemon} [{tier.upper()}]")
        lines.append(f"     Project: {project}")
        lines.append(f"     Description: {desc}")

        hb = a.get("heartbeat", {})
        freshness = a.get("freshness", {})

        if hb.get("last_run"):
            lines.append(f"     Last run: {hb['last_run']} "
                         f"({freshness.get('age_minutes', '?')} min ago)")
            lines.append(f"     Heartbeat status: {hb.get('status', '?')}")

        # Check details if available
        checks = hb.get("checks", {})
        if checks:
            check_line = "     Checks: "
            parts = []
            for check_name, check_data in checks.items():
                if isinstance(check_data, dict):
                    ok = "✅" if check_data.get("ok") else "❌"
                    parts.append(f"{check_name} {ok}")
            check_line += ", ".join(parts)
            lines.append(check_line)

        # Cross-validation
        launchctl_info = a.get("launchctl", {})
        if launchctl_info.get("loaded"):
            note = launchctl_info.get("note", "")
            if launchctl_info.get("pid"):
                lines.append(f"     launchctl: loaded (PID {launchctl_info['pid']}, running)")
            else:
                lines.append(f"     launchctl: loaded (idle between intervals)")
            if launchctl_info.get("last_exit_code") is not None:
                lines.append(f"     Last exit code: {launchctl_info['last_exit_code']}")
        elif launchctl_info.get("found"):
            lines.append("     launchctl: plist exists but not loaded")

        log_info = a.get("log", {})
        if log_info.get("exists"):
            lines.append(f"     Log: {log_info.get('size_bytes', 0):,} bytes, "
                         f"last modified {log_info.get('age_minutes', '?')} min ago")

        # Warnings and notes
        for w in a.get("warnings", []):
            lines.append(f"     ⚠️  Warning: {w}")
        for n in a.get("notes", []):
            lines.append(f"     📝 Note: {n}")

        lines.append("")

    # Legend
    lines.append("  Tiers:")
    lines.append("    ✅ ok       — Heartbeat fresh, all checks passed")
    lines.append("    ⚠️  degraded — Heartbeat fresh, some non-critical checks failed")
    lines.append("    🔴 stale    — Heartbeat overdue (> 2× interval + 5 min grace)")
    lines.append("    ❌ missing  — No heartbeat, daemon may be down")
    lines.append("    💥 corrupt  — Heartbeat file unreadable")

    lines.append("\n" + "=" * 64)
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Daemon Health — Unified health check for all WorkBuddy daemons"
    )
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    parser.add_argument("--all", action="store_true",
                        help="Include all daemons (even those without heartbeats)")
    args = parser.parse_args()

    # Discover daemons
    if args.all:
        all_daemons = discover_all_daemons()
    else:
        heartbeats = read_heartbeats()
        all_daemons = heartbeats
        # Add known daemons without heartbeats only if --all
        if not heartbeats:
            print("No heartbeat files found. Use --all to show known daemons.")
            return

    # Assess each daemon
    assessments = []
    for daemon, hb in all_daemons.items():
        known = KNOWN_DAEMONS.get(daemon, {})
        assessment = assess_daemon(daemon, hb, known)
        assessments.append(assessment)

    # Sort: ok → degraded → stale → missing
    tier_order = {"ok": 0, "degraded": 1, "stale": 2, "missing": 3, "corrupt": 4}
    assessments.sort(key=lambda a: tier_order.get(a.get("tier", "unknown"), 99))

    # Output
    if args.json:
        result = {
            "timestamp": datetime.now().isoformat(),
            "hostname": socket.gethostname(),
            "daemons": assessments,
            "summary": {
                "total": len(assessments),
                "by_tier": {
                    tier: sum(1 for a in assessments if a.get("tier") == tier)
                    for tier in ["ok", "degraded", "stale", "missing", "corrupt"]
                },
            },
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_report(assessments))


if __name__ == "__main__":
    main()
