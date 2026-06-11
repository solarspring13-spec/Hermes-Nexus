#!/usr/bin/env python3
"""
Git Sync for WorkBuddy
======================
Manages the {MEMORIA_HOME} Git repository for cross-device sync.

Usage:
    python3 git_sync.py --init          # Initialize git repo
    python3 git_sync.py --commit         # Auto-commit memory changes
    python3 git_sync.py --status         # Show git status
    python3 git_sync.py --push           # Push to remote
    python3 git_sync.py --pull           # Pull from remote
    python3 git_sync.py --auto           # Commit + push if changes (daemon mode)
    python3 git_sync.py --add-remote URL # Add remote origin
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


# ── Constants ────────────────────────────────────────────────

from ..constants import WORKBUDDY_DIR

# Files/dirs to track in git (must match .gitignore allowlist)
TRACK_PATTERNS = [
    "SOUL.md",
    "IDENTITY.md",
    "USER.md",
    "MEMORY.md",
    "skills/",
    # settings.json removed from tracking 2026-05-29 — contains plaintext secrets, now gitignored
    "mcp.json",
    "models.json",
    "memory/",
    # memery/ retired on 2026-05-29 — archived, not tracked
    "argv.json",
]


# ── Helpers ───────────────────────────────────────────────────

def run_git(args: list, check: bool = False) -> tuple:
    """Run a git command in the workbuddy directory."""
    cmd = ["git", "-C", str(WORKBUDDY_DIR)] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if check and result.returncode != 0:
            raise RuntimeError(f"Git error: {result.stderr.strip()}")
        return (result.returncode, result.stdout.strip(), result.stderr.strip())
    except subprocess.TimeoutExpired:
        return (1, "", "Git command timed out")
    except FileNotFoundError:
        return (1, "", "git not found — please install git")


def is_git_repo() -> bool:
    """Check if {MEMORIA_HOME} is a git repo."""
    return (WORKBUDDY_DIR / ".git").exists()


def has_changes() -> bool:
    """Check if there are uncommitted changes."""
    rc, stdout, _ = run_git(["status", "--porcelain"])
    return rc == 0 and len(stdout.strip()) > 0


# ── Commands ──────────────────────────────────────────────────

def git_init() -> dict:
    """Initialize git repo in {MEMORIA_HOME}"""
    if is_git_repo():
        return {"initialized": False, "reason": "Already a git repo"}

    # Init
    rc, stdout, stderr = run_git(["init"])
    if rc != 0:
        return {"initialized": False, "error": stderr}

    # Add .gitignore
    gitignore = WORKBUDDY_DIR / ".gitignore"
    if gitignore.exists():
        run_git(["add", ".gitignore"])

    # Add tracked files
    for pattern in TRACK_PATTERNS:
        path = WORKBUDDY_DIR / pattern
        if path.exists():
            run_git(["add", pattern])

    # Initial commit
    rc, stdout, stderr = run_git([
        "commit", "-m", "Initial WorkBuddy config sync",
        "--author=WorkBuddy Memory Daemon <<REDACTED_EMAIL>>"
    ])

    return {
        "initialized": True,
        "commit_output": stdout if rc == 0 else stderr,
        "next_step": "Add a remote: git_sync.py --add-remote <URL>",
    }


def git_commit() -> dict:
    """Auto-commit memory changes."""
    if not is_git_repo():
        return {"committed": False, "reason": "Not a git repo. Run --init first."}

    if not has_changes():
        return {"committed": False, "reason": "No changes to commit"}

    # Add all tracked patterns
    for pattern in TRACK_PATTERNS:
        path = WORKBUDDY_DIR / pattern
        if path.exists():
            run_git(["add", pattern])

    # Also add .gitignore if changed
    run_git(["add", ".gitignore"])

    # Commit with timestamp
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    rc, stdout, stderr = run_git([
        "commit", "-m", f"Memory sync: {ts}",
        "--author=WorkBuddy Memory Daemon <<REDACTED_EMAIL>>"
    ])

    files_changed = 0
    if rc == 0:
        # Count changed files from commit output
        for line in stdout.split("\n"):
            if line.strip().startswith("-") or "file changed" in line or "files changed" in line:
                files_changed += 1

    return {
        "committed": rc == 0,
        "timestamp": ts,
        "output": stdout if rc == 0 else stderr,
    }


def git_status() -> dict:
    """Show git status."""
    if not is_git_repo():
        return {"status": "not_a_repo"}

    rc, stdout, _ = run_git(["status", "--short"])
    rc2, stdout2, _ = run_git(["remote", "-v"])

    return {
        "status": "ok",
        "is_repo": True,
        "changes": stdout.strip().split("\n") if stdout.strip() else [],
        "has_changes": len(stdout.strip()) > 0,
        "remotes": stdout2.strip(),
    }


def git_push() -> dict:
    """Push to remote."""
    if not is_git_repo():
        return {"pushed": False, "reason": "Not a git repo"}

    rc, stdout, stderr = run_git(["push", "origin", "main"])
    if rc != 0:
        # Try master branch
        rc, stdout, stderr = run_git(["push", "origin", "master"])

    return {
        "pushed": rc == 0,
        "output": stdout if rc == 0 else stderr,
    }


def git_pull() -> dict:
    """Pull from remote."""
    if not is_git_repo():
        return {"pulled": False, "reason": "Not a git repo"}

    rc, stdout, stderr = run_git(["pull", "origin", "main"])
    if rc != 0:
        rc, stdout, stderr = run_git(["pull", "origin", "master"])

    return {
        "pulled": rc == 0,
        "output": stdout if rc == 0 else stderr,
    }


def git_add_remote(url: str) -> dict:
    """Add a remote origin."""
    if not is_git_repo():
        return {"added": False, "reason": "Not a git repo"}

    # Check if remote already exists
    rc, stdout, _ = run_git(["remote", "get-url", "origin"])
    if rc == 0:
        # Update existing
        run_git(["remote", "set-url", "origin", url])
        return {"added": True, "action": "updated", "url": url}

    run_git(["remote", "add", "origin", url])
    return {"added": True, "action": "created", "url": url}


def git_auto() -> dict:
    """Auto mode: commit → pull --rebase → push (daemon multi-device sync).

    Flow:
      1. Commit local changes (if any)
      2. Pull --rebase to integrate remote changes
      3. Push to remote
    If pull causes a rebase conflict, abort and skip — human resolves later.
    """
    if not is_git_repo():
        return {"auto": False, "reason": "Not a git repo"}

    pull_result = {"pulled": False, "output": "No remote"}
    commit_result = None
    push_result = None

    # Step 1: Has remote? Try pull first to get latest
    rc, _, _ = run_git(["remote", "get-url", "origin"])
    has_remote = (rc == 0)

    # Step 2: Commit local changes (must do before pull --rebase)
    if has_changes():
        commit_result = git_commit()
        if not commit_result.get("committed"):
            return {"auto": False, "commit": commit_result}

    # Step 3: Pull with rebase to integrate remote changes
    if has_remote:
        rc, stdout, stderr = run_git(["pull", "--rebase", "origin", "main"])
        if rc != 0:
            # Distinguish real rebase conflicts from unstaged changes
            stderr_lower = stderr.lower()
            if "unstaged changes" in stderr_lower or "please commit" in stderr_lower:
                # Unstaged changes after commit (e.g., submodule dirt, backup files)
                # Skip pull — just proceed to push
                pull_result = {"pulled": False, "output": "Skipped: unstaged changes (non-blocking)"}
            elif "rebase" in stderr_lower or "conflict" in stderr_lower:
                # True rebase conflict — abort, human resolves later
                run_git(["rebase", "--abort"])
                return {
                    "auto": False,
                    "action": "pull_conflict",
                    "reason": f"Rebase conflict with remote — manual resolution needed: {stderr.strip()[:120]}",
                    "commit": commit_result,
                }
            else:
                # Unknown pull failure — skip but don't abort
                pull_result = {"pulled": False, "output": "Skipped: pull failed (non-blocking)", "stderr": stderr.strip()[:120]}
        else:
            pull_result = {"pulled": True, "output": stdout.strip()}

    # Step 4: Push if we committed something or rebase rewrote history
    if has_remote and (commit_result or pull_result.get("pulled")):
        push_result = git_push()

    if not commit_result and not pull_result.get("pulled"):
        return {"auto": True, "action": "none", "reason": "No changes on either side"}

    return {
        "auto": True,
        "commit": commit_result,
        "pull": pull_result,
        "push": push_result,
    }


# ── CLI ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Git Sync for WorkBuddy (cross-device config & memory sync)"
    )
    parser.add_argument("--init", action="store_true",
                        help="Initialize git repo in {MEMORIA_HOME}")
    parser.add_argument("--commit", action="store_true",
                        help="Auto-commit memory changes")
    parser.add_argument("--status", action="store_true",
                        help="Show git status")
    parser.add_argument("--push", action="store_true",
                        help="Push to remote")
    parser.add_argument("--pull", action="store_true",
                        help="Pull from remote")
    parser.add_argument("--auto", action="store_true",
                        help="Commit + push if changes (daemon mode)")
    parser.add_argument("--add-remote", type=str,
                        help="Add remote origin URL")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")

    args = parser.parse_args()

    if args.init:
        result = git_init()
    elif args.commit:
        result = git_commit()
    elif args.status:
        result = git_status()
    elif args.push:
        result = git_push()
    elif args.pull:
        result = git_pull()
    elif args.auto:
        result = git_auto()
    elif args.add_remote:
        result = git_add_remote(args.add_remote)
    else:
        # Default: status
        result = git_status()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        # Human-readable output
        if args.init:
            if result.get("initialized"):
                print("✅ Git repo initialized in {MEMORIA_HOME}")
                print(f"   {result.get('next_step', '')}")
            else:
                print(f"ℹ️  {result.get('reason', 'Already initialized')}")
        elif args.commit:
            if result.get("committed"):
                print(f"💾 Committed: {result.get('timestamp', '')}")
            else:
                print(f"ℹ️  {result.get('reason', 'No action')}")
        elif args.push:
            if result.get("pushed"):
                print("📤 Pushed to remote")
            else:
                print(f"⚠️  Push failed: {result.get('output', 'Unknown error')}")
        elif args.pull:
            if result.get("pulled"):
                print("📥 Pulled from remote")
            else:
                print(f"⚠️  Pull failed: {result.get('output', 'Unknown error')}")
        elif args.add_remote:
            print(f"✅ Remote {result.get('action', 'set')}: {result.get('url', '')}")
        elif args.auto:
            if result.get("action") == "none":
                pass  # Silent — no changes
            elif result.get("action") == "pull_conflict":
                print(f"⚠️  Sync stalled: {result.get('reason', 'Pull conflict')}")
            elif result.get("auto"):
                parts = []
                if result.get("commit"):
                    parts.append(f"Committed: {result['commit'].get('timestamp', '')}")
                if result.get("pull", {}).get("pulled"):
                    parts.append("Pulled remote")
                if result.get("push", {}).get("pushed"):
                    parts.append("Pushed")
                elif result.get("push"):
                    parts.append("Push skipped")
                print(f"💾 Auto-synced: {' | '.join(parts)}")
            else:
                print(f"ℹ️  {result.get('reason', 'No action')}")
        else:
            # Status
            if result.get("status") == "not_a_repo":
                print("❌ Not a git repo. Run --init first.")
            else:
                has = result.get("has_changes", False)
                print(f"📊 Git Status: {'📝 Changes' if has else '✅ Clean'}")
                if result.get("remotes"):
                    print(f"   Remote: {result['remotes'][:80]}")
                else:
                    print("   Remote: (none — use --add-remote <URL>)")


if __name__ == "__main__":
    main()
