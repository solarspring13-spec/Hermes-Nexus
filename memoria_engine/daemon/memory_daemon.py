#!/usr/bin/env python3
"""
Memory Daemon for WorkBuddy
=============================
Hermes-inspired background maintenance daemon.

Runs as a macOS LaunchAgent every 30 minutes to:
1. Rebuild FTS5 search index
2. Check and execute memory compression
3. Auto-commit memory changes to Git

When a WorkBuddy session is active, the daemon skips (Layer 1 handles it).

Usage:
    # Install as LaunchAgent
    python3 memory_daemon.py --install

    # Uninstall LaunchAgent
    python3 memory_daemon.py --uninstall

    # Run a single background cycle (called by launchd)
    python3 memory_daemon.py --background

    # Show daemon status
    python3 memory_daemon.py --status

    # Run a manual cycle (foreground, for testing)
    python3 memory_daemon.py --cycle
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


# ── Constants ────────────────────────────────────────────────

from ..constants import WORKBUDDY_ROOT, SCRIPTS_DIR

from ..constants import (
    LOG_DIR, LOG_PATH_MEMORY as LOG_PATH, ERR_PATH_MEMORY as ERR_PATH,
    HEARTBEAT_DIR, HEARTBEAT_FILE_MEMORY as HEARTBEAT_PATH,
    SCRIPTS_DIR, WORKBUDDY_DIR, GRACE_MINUTES,
)

PLIST_LABEL = "com.workbuddy.memory-nudge"
PLIST_DIR = Path.home() / "Library" / "LaunchAgents"
PLIST_PATH = PLIST_DIR / f"{PLIST_LABEL}.plist"


# ── Plist Template ───────────────────────────────────────────

PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/env</string>
        <string>python3</string>
        <string>{script}</string>
        <string>--background</string>
    </array>
    <key>StartInterval</key>
    <integer>{interval}</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{stdout}</string>
    <key>StandardErrorPath</key>
    <string>{stderr}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
"""


# ── Helpers ───────────────────────────────────────────────────

def log(msg: str):
    """Append a timestamped message to the daemon log."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(line)


def write_heartbeat(status: str, details: dict = None):
    """Write heartbeat file for daemon health monitoring.

    Called at start and end of every daemon cycle. The heartbeat file provides
    the single source of truth for daemon health — this is what daemon_health.py
    reads to distinguish "healthy idle between intervals" from "crashed."

    Heartbeat file: {MEMORIA_HOME}
    """
    import socket
    HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)

    heartbeat = {
        "daemon": "memory-nudge",
        "label": PLIST_LABEL,
        "manager": "launchd",
        "hostname": socket.gethostname(),
        "status": status,
        "last_run": datetime.now().isoformat(),
        "pid": os.getpid(),
        "python": sys.executable,
        "interval_seconds": 1800,
    }

    if details:
        heartbeat.update(details)

    try:
        HEARTBEAT_PATH.write_text(
            json.dumps(heartbeat, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        # Don't crash the daemon if heartbeat write fails
        log(f"⚠️  Heartbeat write failed: {e}")


def is_session_active() -> bool:
    """Check if a WorkBuddy session is currently active.

    Heuristic: look for recent activity in session directories.
    If there's been activity in the last 5 minutes, consider it active.
    """
    sessions_dir = WORKBUDDY_DIR / "sessions"
    if not sessions_dir.exists():
        return False

    now = datetime.now().timestamp()
    for item in sessions_dir.iterdir():
        if item.is_file():
            try:
                mtime = item.stat().st_mtime
                if now - mtime < 300:  # 5 minutes
                    return True
            except OSError:
                continue

    return False


def find_active_workspaces() -> list:
    """Find all workspace directories that have .workbuddy/memory/."""
    workbuddy_root = WORKBUDDY_ROOT
    workspaces = []

    if not workbuddy_root.exists():
        return workspaces

    for project_dir in workbuddy_root.iterdir():
        memory_dir = project_dir / ".workbuddy" / "memory"
        if memory_dir.exists() and any(memory_dir.glob("*.md")):
            workspaces.append(str(project_dir))

    return sorted(workspaces)


def run_script(script_name: str, args: list) -> tuple:
    """Run a Python script from the scripts directory.

    Returns (return_code, stdout, stderr).
    """
    script_path = SCRIPTS_DIR / script_name
    if not script_path.exists():
        return (1, "", f"Script not found: {script_path}")

    cmd = [sys.executable, str(script_path)] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(WORKBUDDY_DIR),
        )
        return (result.returncode, result.stdout, result.stderr)
    except subprocess.TimeoutExpired:
        return (1, "", "Script timed out after 120s")
    except Exception as e:
        return (1, "", str(e))


# ── Core Cycle ────────────────────────────────────────────────

def off_session_review(workspaces: list) -> dict:
    """Run a lightweight off-session memory review.

    When no WorkBuddy session is active, the daemon performs heuristic checks:
    1. Compare daily log topics vs MEMORY.md sections — find gaps
    2. Check USER.md for [可能过时] markers that need attention
    3. Report findings in the daemon log (does NOT auto-write memory)
    """
    findings = []
    
    for ws in workspaces:
        ws_path = Path(ws)
        memory_dir = ws_path / ".workbuddy" / "memory"
        if not memory_dir.exists():
            continue
        
        memory_md = memory_dir / "MEMORY.md"
        user_md = memory_dir / "USER.md"
        
        ws_finding = {"workspace": ws, "gaps": [], "stale": []}
        
        # Check daily log for topics not in MEMORY.md
        if memory_md.exists():
            memory_content = memory_md.read_text(encoding="utf-8")
            memory_topics = set()
            for line in memory_content.split("\n"):
                line = line.strip()
                if line.startswith("## "):
                    memory_topics.add(line[3:].strip().lower())
            
            # Check latest daily log
            daily_files = sorted(memory_dir.glob("202[0-9]-[0-9][0-9]-[0-9][0-9].md"))
            if daily_files:
                latest = daily_files[-1]
                daily_content = latest.read_text(encoding="utf-8")
                daily_topics = set()
                for line in daily_content.split("\n"):
                    line = line.strip()
                    if line.startswith("## "):
                        daily_topics.add(line[3:].strip().lower())
                
                new_topics = daily_topics - memory_topics
                if new_topics:
                    ws_finding["gaps"] = list(new_topics)
        
        # Check USER.md for stale markers
        if user_md.exists():
            user_content = user_md.read_text(encoding="utf-8")
            for line in user_content.split("\n"):
                if "[可能过时]" in line:
                    ws_finding["stale"].append(line.strip())
        
        if ws_finding["gaps"] or ws_finding["stale"]:
            findings.append(ws_finding)
    
    # Log findings
    if findings:
        log("Off-session review: found memory gaps")
        for f in findings:
            ws_name = Path(f["workspace"]).name
            if f["gaps"]:
                log(f"  [{ws_name}] New topics in daily log not in MEMORY.md: {', '.join(f['gaps'][:5])}")
            if f["stale"]:
                log(f"  [{ws_name}] Stale USER.md markers: {len(f['stale'])} entry(s)")
        return {"reviewed": True, "findings": len(findings), "gaps_found": True}
    else:
        log("Off-session review: no gaps found")
        return {"reviewed": True, "findings": 0, "gaps_found": False}


def _clean_stale_pyc() -> int:
    """Clean stale .pyc bytecode files from __pycache__ directories.

    Removes .pyc files that are:
    - Orphaned (no corresponding .py source file)
    - Stale (.py source is newer than .pyc)

    Scans all __pycache__ dirs under enhanced-memory/scripts/.
    Returns number of files cleaned.
    """
    scripts_dir = SCRIPTS_DIR
    if not scripts_dir.exists():
        return 0

    cleaned = 0
    for pycache_dir in scripts_dir.rglob("__pycache__"):
        if not pycache_dir.is_dir():
            continue
        for pyc in pycache_dir.glob("*.pyc"):
            try:
                # Derive source .py name from .pyc filename
                # e.g., "memory_compress.cpython-311.pyc" → "memory_compress.py"
                stem = pyc.stem  # "memory_compress.cpython-311"
                base_name = stem.split(".")[0]  # "memory_compress"
                py_path = pycache_dir.parent / f"{base_name}.py"

                if not py_path.exists():
                    # Orphaned .pyc — source removed
                    pyc.unlink()
                    cleaned += 1
                    log(f"🧹 Removed orphaned .pyc: {pyc.name}")
                elif py_path.stat().st_mtime > pyc.stat().st_mtime:
                    # Stale .pyc — source newer than bytecode
                    pyc.unlink()
                    cleaned += 1
                    log(f"🧹 Removed stale .pyc: {pyc.name} (source newer)")
            except OSError as e:
                log(f"⚠️  Failed to clean {pyc}: {e}")

    # Clean empty __pycache__ dirs
    for pycache_dir in scripts_dir.rglob("__pycache__"):
        if pycache_dir.is_dir():
            try:
                if not any(pycache_dir.iterdir()):
                    pycache_dir.rmdir()
            except OSError:
                pass

    return cleaned


def health_check(workspaces: list) -> dict:
    """Run daemon health self-checks.

    Checks:
    1. Nudge state staleness (last_nudge > 3 hours)
    2. FTS5 index freshness (last_indexed > 2 hours)
    3. Log file size (warn if > 1MB)
    4. User model contradictions (Phase 3F)
    5. Stale .pyc bytecode cache cleanup
    """
    issues = []

    # ── Clean stale .pyc files ──
    pyc_cleaned = _clean_stale_pyc()
    if pyc_cleaned > 0:
        log(f"🧹 Bytecode cleanup: removed {pyc_cleaned} stale .pyc file(s)")

    # Check log size with auto-rotation
    if LOG_PATH.exists():
        log_size = LOG_PATH.stat().st_size
        if log_size > 1_000_000:
            try:
                # Rotate: keep last 5000 lines, backup old log
                with open(LOG_PATH, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                if len(lines) > 5000:
                    backup_path = LOG_PATH.with_suffix(f".bak.{datetime.now().strftime('%Y%m%d-%H%M%S')}")
                    LOG_PATH.rename(backup_path)
                    with open(LOG_PATH, 'w', encoding='utf-8') as f:
                        f.writelines(lines[-5000:])
                    new_size = LOG_PATH.stat().st_size
                    log(f"🔄 Log rotated: {log_size:,} → {new_size:,} bytes (backup: {backup_path.name})")
                    # Re-check if still too large (extreme case)
                    if new_size > 1_000_000:
                        issues.append(f"Log file still too large after rotation: {new_size:,} bytes (> 1MB)")
                else:
                    # < 5000 lines but > 1MB — unusual, flag it
                    issues.append(f"Log file too large: {log_size:,} bytes ({len(lines)} lines > 1MB)")
                    log(f"⚠️ Health: log file {log_size:,} bytes, {len(lines)} lines")
            except Exception as e:
                issues.append(f"Log rotation failed: {e}")
                log(f"⚠️ Health: log rotation error: {e}")

    # Check nudge state for each workspace
    nudge_script = SCRIPTS_DIR / "memory_nudge.py"
    if nudge_script.exists():
        for ws in workspaces:
            try:
                result = subprocess.run(
                    [sys.executable, str(nudge_script), "--workspace", ws, "--status", "--json"],
                    capture_output=True, text=True, timeout=10,
                    cwd=str(WORKBUDDY_DIR),
                )
                if result.returncode == 0:
                    state = json.loads(result.stdout)
                    last_nudge = state.get("last_nudge_at", "")
                    if last_nudge:
                        last_dt = datetime.fromisoformat(last_nudge)
                        hours_ago = (datetime.now() - last_dt).total_seconds() / 3600
                        if hours_ago > 3:
                            issues.append(f"[{Path(ws).name}] Nudge stale: {hours_ago:.1f}h since last review")
            except Exception:
                pass

    # Check FTS5 index freshness
    index_script = SCRIPTS_DIR / "memory_index.py"
    if index_script.exists():
        for ws in workspaces:
            try:
                result = subprocess.run(
                    [sys.executable, str(index_script), "--workspace", ws, "--status", "--json"],
                    capture_output=True, text=True, timeout=10,
                    cwd=str(WORKBUDDY_DIR),
                )
                if result.returncode == 0:
                    state = json.loads(result.stdout)
                    last_idx = state.get("last_indexed", "")
                    if last_idx:
                        last_dt = datetime.fromisoformat(last_idx)
                        hours_ago = (datetime.now() - last_dt).total_seconds() / 3600
                        if hours_ago > 2:
                            issues.append(f"[{Path(ws).name}] FTS5 index stale: {hours_ago:.1f}h since last update")
            except Exception:
                pass

    # Phase 3F: Check user model contradictions
    user_model_script = SCRIPTS_DIR / "user_model.py"
    if user_model_script.exists():
        try:
            result = subprocess.run(
                [sys.executable, str(user_model_script), "--check", "--json"],
                capture_output=True, text=True, timeout=10,
                cwd=str(WORKBUDDY_DIR),
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                findings = data.get("findings", [])
                if findings:
                    issues.append(f"User model: {len(findings)} unresolved contradiction(s)")
                    log(f"⚠️ User model: {len(findings)} contradictions detected")
        except Exception:
            pass

    if issues:
        for issue in issues:
            log(f"⚠️ {issue}")
        return {"healthy": False, "issues": issues}
    else:
        return {"healthy": True, "issues": []}


def run_cycle(is_background: bool = False) -> dict:
    """Run a single maintenance cycle.

    Steps:
    1. Check if session is active → log it, but still do maintenance
    2. Find all workspaces with memory
    3. For each workspace:
       a. Rebuild FTS5 index
       b. Check compression
       c. Auto-compress if needed
    4. Git auto-commit

    When a session is active, maintenance still runs — only the review
    (nudge) is deferred to the L1 session layer.

    Returns a summary dict.
    """
    # Write "running" heartbeat at cycle start — proves daemon is alive
    # even if it crashes mid-cycle (end heartbeat will be missing)
    write_heartbeat("running")

    result = {
        "timestamp": datetime.now().isoformat(),
        "skipped": False,
        "session_active": False,
        "workspaces": [],
        "git_sync": None,
    }

    # Check if session is active
    session_active = is_session_active()
    if session_active:
        result["session_active"] = True
        log("Maintenance-only mode (active session detected — review deferred to L1)")
    else:
        log("Full maintenance cycle (no active session)")

    log("Starting maintenance cycle")

    # Find workspaces
    workspaces = find_active_workspaces()
    if not workspaces:
        log("No workspaces with memory found")
        result["reason"] = "No workspaces found"
        return result

    # Process each workspace
    for ws in workspaces:
        ws_result = {"workspace": ws, "index": None, "compress": None}

        # Rebuild FTS5 index
        rc, stdout, stderr = run_script("memory_index.py", [
            "--workspace", ws, "--rebuild"
        ])
        ws_result["index"] = {
            "returncode": rc,
            "output": stdout.strip() if rc == 0 else stderr.strip(),
        }
        log(f"Index [{ws}]: {stdout.strip() if rc == 0 else 'ERROR'}")

        # Check compression
        rc, stdout, stderr = run_script("memory_compress.py", [
            "--auto-compress", "--workspace", ws
        ])
        ws_result["compress"] = {
            "returncode": rc,
            "output": stdout.strip() if rc == 0 else stderr.strip(),
        }
        log(f"Compress [{ws}]: {stdout.strip() if rc == 0 else 'ERROR'}")

        result["workspaces"].append(ws_result)

    # Rebuild global cross-workspace FTS5 index (B1)
    rc, stdout, stderr = run_script("memory_index.py", [
        "--global", "--rebuild", "--json"
    ])
    result["global_index"] = {
        "returncode": rc,
        "output": stdout.strip()[:80] if rc == 0 else stderr.strip()[:80],
    }
    log(f"Global Index: {stdout.strip()[:80] if rc == 0 else 'ERROR'}")

    # Git auto-commit
    git_script = SCRIPTS_DIR / "git_sync.py"
    if git_script.exists():
        rc, stdout, stderr = run_script("git_sync.py", ["--auto"])
        result["git_sync"] = {
            "returncode": rc,
            "output": stdout.strip() if rc == 0 else stderr.strip(),
        }
        log(f"Git: {stdout.strip() if rc == 0 else 'ERROR'}")
    else:
        result["git_sync"] = {"returncode": -1, "output": "git_sync.py not found yet"}
        log("Git: skipped (git_sync.py not found)")

    # Phase 3D: Shared memory pool maintenance
    pool_script = SCRIPTS_DIR / "memory_pool.py"
    if pool_script.exists():
        # Rebuild shared index
        rc, stdout, stderr = run_script("memory_index.py", [
            "--shared", "--rebuild", "--json"
        ])
        result["shared_index"] = {
            "returncode": rc,
            "output": stdout.strip() if rc == 0 else stderr.strip(),
        }
        log(f"Shared index: indexed")

        # Run routing (for each workspace with memory)
        router_script = SCRIPTS_DIR / "agent_router.py"
        if router_script.exists():
            for ws in workspaces:
                rc, stdout, stderr = run_script("agent_router.py", [
                    "--relevant", "--workspace", ws, "--json"
                ])
                if rc == 0:
                    try:
                        route_data = json.loads(stdout)
                        relevant = route_data.get("relevant", [])
                        if relevant:
                            log(f"Shared route [{Path(ws).name}]: {len(relevant)} relevant entries")
                    except json.JSONDecodeError:
                        pass

        # Compact pool
        rc, stdout, stderr = run_script("memory_pool.py", ["--compact", "--json"])
        result["pool_compact"] = {
            "returncode": rc,
            "output": stdout.strip() if rc == 0 else stderr.strip(),
        }
        log(f"Pool: compacted")

    # Phase 3F: User model health check
    user_model_script = SCRIPTS_DIR / "user_model.py"
    if user_model_script.exists():
        rc, stdout, stderr = run_script("user_model.py", ["--check", "--json"])
        result["user_model_check"] = {
            "returncode": rc,
            "output": stdout.strip() if rc == 0 else stderr.strip(),
        }

    # Off-session review: when no active session, check for memory gaps
    if not session_active:
        review_result = off_session_review(workspaces)
        result["review"] = review_result

    # Health check: verify system integrity every cycle
    health_result = health_check(workspaces)
    result["health"] = health_result

    log("Maintenance cycle complete")

    # Write final heartbeat with full check details
    # Status "ok" = all steps passed, "degraded" = some checks failed, "error" = cycle aborted
    health_ok = result.get("health", {}).get("healthy", True)
    index_ok = all(ws.get("index", {}).get("returncode") == 0 for ws in result.get("workspaces", []))
    compress_ok = all(ws.get("compress", {}).get("returncode") == 0 for ws in result.get("workspaces", []))
    git_ok = result.get("git_sync", {}).get("returncode", -1) == 0
    global_index_ok = result.get("global_index", {}).get("returncode", -1) == 0

    all_ok = health_ok and index_ok and compress_ok and git_ok and global_index_ok

    heartbeat_status = "ok" if all_ok else "degraded"
    heartbeat_details = {
        "exit_code": 0,
        "checks": {
            "index": {"ok": index_ok, "workspaces": len(result.get("workspaces", []))},
            "compress": {"ok": compress_ok},
            "git_sync": {"ok": git_ok},
            "health": {"ok": health_ok},
            "global_index": {"ok": global_index_ok},
        },
    }

    if result.get("reason"):
        heartbeat_details["reason"] = result["reason"]

    write_heartbeat(heartbeat_status, heartbeat_details)

    return result


# ── Install / Uninstall ───────────────────────────────────────

def install_daemon(interval: int = 1800) -> dict:
    """Install the LaunchAgent plist."""
    PLIST_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    plist_content = PLIST_TEMPLATE.format(
        label=PLIST_LABEL,
        script=str(SCRIPTS_DIR / "memory_daemon.py"),
        interval=interval,
        stdout=str(LOG_PATH),
        stderr=str(ERR_PATH),
    )

    PLIST_PATH.write_text(plist_content, encoding="utf-8")

    # Load the LaunchAgent
    try:
        subprocess.run(["launchctl", "unload", str(PLIST_PATH)],
                       capture_output=True, timeout=10)
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["launchctl", "load", str(PLIST_PATH)],
            capture_output=True, text=True, timeout=10
        )
        loaded = result.returncode == 0
    except Exception as e:
        loaded = False
        error = str(e)

    return {
        "installed": True,
        "plist_path": str(PLIST_PATH),
        "interval_seconds": interval,
        "loaded": loaded,
        "error": None if loaded else error if 'error' in dir() else "launchctl load failed",
    }


def uninstall_daemon() -> dict:
    """Unload and remove the LaunchAgent."""
    # Unload
    try:
        subprocess.run(
            ["launchctl", "unload", str(PLIST_PATH)],
            capture_output=True, timeout=10
        )
    except Exception:
        pass

    # Remove plist
    removed = False
    if PLIST_PATH.exists():
        PLIST_PATH.unlink()
        removed = True

    return {
        "uninstalled": True,
        "plist_removed": removed,
    }


def get_daemon_status() -> dict:
    """Check the current daemon status."""
    # Check if plist exists
    plist_exists = PLIST_PATH.exists()

    # Check if loaded
    loaded = False
    try:
        result = subprocess.run(
            ["launchctl", "list", PLIST_LABEL],
            capture_output=True, text=True, timeout=10
        )
        loaded = result.returncode == 0
    except Exception:
        pass

    # Check log
    log_exists = LOG_PATH.exists()
    last_log_line = ""
    if log_exists:
        try:
            lines = LOG_PATH.read_text(encoding="utf-8").strip().split('\n')
            last_log_line = lines[-1] if lines else ""
        except Exception:
            pass

    return {
        "installed": plist_exists,
        "loaded": loaded,
        "plist_path": str(PLIST_PATH),
        "log_path": str(LOG_PATH),
        "log_exists": log_exists,
        "last_log": last_log_line,
        "scripts_dir": str(SCRIPTS_DIR),
    }


# ── CLI ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Memory Daemon for WorkBuddy (Hermes-inspired background maintenance)"
    )
    parser.add_argument("--install", action="store_true",
                        help="Install as macOS LaunchAgent")
    parser.add_argument("--uninstall", action="store_true",
                        help="Unload and remove LaunchAgent")
    parser.add_argument("--background", action="store_true",
                        help="Run a single background cycle (called by launchd)")
    parser.add_argument("--cycle", action="store_true",
                        help="Run a manual cycle (foreground, for testing)")
    parser.add_argument("--status", action="store_true",
                        help="Show daemon status")
    parser.add_argument("--interval", type=int, default=1800,
                        help="Daemon interval in seconds (default: 1800 = 30 min)")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")

    args = parser.parse_args()

    if args.install:
        result = install_daemon(interval=args.interval)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            if result["loaded"]:
                print(f"✅ Daemon installed and loaded")
                print(f"   Interval: {result['interval_seconds']}s ({result['interval_seconds']//60} min)")
                print(f"   Plist: {result['plist_path']}")
                print(f"   Log:   {LOG_PATH}")
            else:
                print(f"⚠️  Daemon plist created but failed to load: {result.get('error')}")
                print(f"   Try manually: launchctl load {result['plist_path']}")
        return

    if args.uninstall:
        result = uninstall_daemon()
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("✅ Daemon unloaded and plist removed")
        return

    if args.status:
        status = get_daemon_status()
        if args.json:
            print(json.dumps(status, ensure_ascii=False, indent=2))
        else:
            print("📊 Memory Daemon Status")
            print(f"   Installed: {'✅' if status['installed'] else '❌'}")
            print(f"   Loaded:    {'✅' if status['loaded'] else '❌'}")
            print(f"   Plist:     {status['plist_path']}")
            print(f"   Log:       {status['log_path']}")
            if status['last_log']:
                print(f"   Last log:  {status['last_log'][:80]}")
        return

    if args.background or args.cycle:
        result = run_cycle(is_background=args.background)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif not result["skipped"]:
            mode = "🔄 Maintenance-only" if result.get("session_active") else "🔄 Full cycle"
            print(f"{mode} complete at {result['timestamp']}")
            for ws in result["workspaces"]:
                print(f"   📁 {ws['workspace']}")
                if ws["index"]:
                    print(f"      Index:   {ws['index']['output'][:60]}")
                if ws["compress"]:
                    print(f"      Compress: {ws['compress']['output'][:60]}")
            if result["git_sync"]:
                print(f"   Git: {result['git_sync']['output'][:60]}")
        else:
            if not args.json:
                print(f"⏭️  Skipped: {result['reason']}")
        return

    # Default: show status
    status = get_daemon_status()
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        print("📊 Use --status, --install, --uninstall, --cycle, or --background")


if __name__ == "__main__":
    main()
