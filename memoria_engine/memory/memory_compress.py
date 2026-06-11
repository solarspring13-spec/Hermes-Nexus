#!/usr/bin/env python3
"""
Memory Compressor for WorkBuddy
================================
Inspired by Hermes Agent's memory capacity management.

Analyzes MEMORY.md and USER.md for:
- Character count vs capacity limits
- Duplicate or semantically redundant entries
- Compression candidates (mergeable entries)

Can also EXECUTE compression:
- --compress: Heuristic auto-compression (remove duplicates, merge entries)
- --auto-compress: Daemon-safe mode (no user interaction, heuristic only)

Usage:
    # Check memory status
    python3 memory_compress.py --check --workspace /path/to/workspace

    # Get compression suggestions (does NOT modify files)
    python3 memory_compress.py --suggest --workspace /path/to/workspace

    # Execute compression (modifies files, with backup)
    python3 memory_compress.py --compress --workspace /path/to/workspace

    # Auto-compress for daemon (heuristic only, no backup prompt)
    python3 memory_compress.py --auto-compress --workspace /path/to/workspace

    # Show memory stats
    python3 memory_compress.py --stats --workspace /path/to/workspace

    # Scan for secrets
    python3 memory_compress.py --scan --workspace /path/to/workspace
"""

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..constants import (
    MEMORY_SOFT_LIMIT, MEMORY_HARD_LIMIT,
    USER_SOFT_LIMIT, USER_HARD_LIMIT,
    MEMORY_DIR, WORKBUDDY_DIR, SCRIPTS_DIR,
)


# ── File Operations ──────────────────────────────────────────

def get_memory_files(workspace: str) -> dict:
    """Locate memory files.

    Canonical L2 (2026-05-29): {MEMORIA_HOME} is the single source of truth.
    Workspace-level memory is in <ws>/.workbuddy/memory/YYYY-MM-DD.md (daily logs),
    not in a separate MEMORY.md. No workspace preference for global L2.
    """
    result = {
        "memory_path": WORKBUDDY_DIR / "MEMORY.md",
        "user_path": WORKBUDDY_DIR / "USER.md",
    }

    # Workspace daily log (not L2 — informational only)
    ws_memory = Path(workspace) / MEMORY_DIR / "MEMORY.md"
    if ws_memory.exists():
        # Workspace MEMORY.md is retired per L2 consolidation.
        # Only read if root MEMORY.md is missing (degraded fallback).
        if not result["memory_path"].exists():
            result["memory_path"] = ws_memory

    return result


def read_file_safe(path: Path) -> str:
    """Read a file, return empty string if missing."""
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def count_sections(content: str) -> int:
    """Count distinct sections in memory content (separated by headings or blank lines)."""
    # Count markdown headings as section boundaries
    headings = re.findall(r"^#{1,3}\s+.+$", content, re.MULTILINE)
    # Also consider blank-line separated blocks
    blocks = [b.strip() for b in content.split("\n\n") if b.strip()]
    return max(len(headings), len(blocks))


def detect_duplicates(content: str) -> list:
    """Detect potential duplicate entries by finding repeated key phrases."""
    # Split by markdown headings and blank lines to get semantic blocks
    blocks = re.split(r"\n#{1,3}\s+|\n\n+", content)
    seen = {}
    duplicates = []

    for block in blocks:
        block = block.strip()
        if len(block) < 30:
            continue
        # Use first 50 alphanumeric chars as key (including Chinese)
        key = "".join(c for c in block[:100] if c.isalnum())
        if len(key) < 20:
            continue
        key = key[:60]
        if key in seen:
            # Only flag if the blocks are in different sections (heuristic: different line counts)
            if abs(len(block) - len(seen[key])) > 20:
                duplicates.append({
                    "original": seen[key][:80],
                    "duplicate": block[:80],
                })
        else:
            seen[key] = block

    return duplicates


# ── Analysis ─────────────────────────────────────────────────

def analyze_memory(workspace: str) -> dict:
    """Analyze memory files and report status."""
    files = get_memory_files(workspace)

    memory_content = read_file_safe(files["memory_path"])
    user_content = read_file_safe(files["user_path"])

    memory_len = len(memory_content)
    user_len = len(user_content)

    # Status levels
    def status_level(current: int, soft: int, hard: int) -> str:
        if current >= hard:
            return "critical"
        elif current >= soft:
            return "warning"
        elif current >= soft * 0.7:
            return "approaching"
        else:
            return "healthy"

    return {
        "memory": {
            "path": str(files["memory_path"]),
            "exists": files["memory_path"].exists(),
            "char_count": memory_len,
            "soft_limit": MEMORY_SOFT_LIMIT,
            "hard_limit": MEMORY_HARD_LIMIT,
            "usage_pct": round(memory_len / MEMORY_SOFT_LIMIT * 100, 1) if MEMORY_SOFT_LIMIT else 0,
            "status": status_level(memory_len, MEMORY_SOFT_LIMIT, MEMORY_HARD_LIMIT),
            "sections": count_sections(memory_content),
            "duplicates": len(detect_duplicates(memory_content)),
        },
        "user": {
            "path": str(files["user_path"]),
            "exists": files["user_path"].exists(),
            "char_count": user_len,
            "soft_limit": USER_SOFT_LIMIT,
            "hard_limit": USER_HARD_LIMIT,
            "usage_pct": round(user_len / USER_SOFT_LIMIT * 100, 1) if USER_SOFT_LIMIT else 0,
            "status": status_level(user_len, USER_SOFT_LIMIT, USER_HARD_LIMIT),
            "sections": count_sections(user_content),
        },
        "workspace": workspace,
    }


def suggest_compression(workspace: str) -> dict:
    """Generate compression suggestions."""
    analysis = analyze_memory(workspace)
    suggestions = []

    # MEMORY.md suggestions
    mem = analysis["memory"]
    if mem["status"] in ("critical", "warning"):
        to_remove = mem["char_count"] - MEMORY_SOFT_LIMIT
        suggestions.append({
            "file": "MEMORY.md",
            "priority": "high" if mem["status"] == "critical" else "medium",
            "action": "compress",
            "current_chars": mem["char_count"],
            "target_chars": MEMORY_SOFT_LIMIT,
            "excess_chars": max(0, to_remove),
            "hint": "Ask LLM to merge similar entries and remove obsolete information. "
                    f"Remove {to_remove}+ characters."
        })

    if mem["duplicates"] > 0:
        suggestions.append({
            "file": "MEMORY.md",
            "priority": "medium",
            "action": "deduplicate",
            "duplicate_count": mem["duplicates"],
            "hint": f"Found {mem['duplicates']} potential duplicate entries. Review and merge."
        })

    # USER.md suggestions
    usr = analysis["user"]
    if usr["status"] in ("critical", "warning"):
        to_remove = usr["char_count"] - USER_SOFT_LIMIT
        suggestions.append({
            "file": "USER.md",
            "priority": "high" if usr["status"] == "critical" else "medium",
            "action": "compress",
            "current_chars": usr["char_count"],
            "target_chars": USER_SOFT_LIMIT,
            "excess_chars": max(0, to_remove),
            "hint": "Consolidate user preferences, remove outdated info."
        })

    analysis["suggestions"] = suggestions
    analysis["needs_action"] = len(suggestions) > 0

    return analysis


# ── Safety Scanner ───────────────────────────────────────────

def scan_for_secrets(content: str) -> list:
    """Scan content for potential secrets/credentials (Hermes-style safety check)."""
    warnings = []

    patterns = [
        (r'sk-[a-zA-Z0-9]{20,}', "OpenAI API Key"),
        (r'ghp_[a-zA-Z0-9]{36}', "GitHub Personal Access Token"),
        (r'gho_[a-zA-Z0-9]{36}', "GitHub OAuth Token"),
        (r'AKIA[0-9A-Z]{16}', "AWS Access Key"),
        (r'AIza[0-9A-Za-z\-_]{35}', "Google API Key"),
        (r'xox[baprs]-[0-9a-zA-Z\-]+', "Slack Token"),
        (r'eyJ[a-zA-Z0-9\-_]+\.eyJ[a-zA-Z0-9\-_]+\.[a-zA-Z0-9\-_]+', "JWT Token"),
        (r'Bearer\s+[a-zA-Z0-9\-_\.]{20,}', "Bearer Token"),
        (r'password\s*[:=]\s*\S+', "Hardcoded Password (suspicious)"),
        (r'secret\s*[:=]\s*\S+', "Hardcoded Secret (suspicious)"),
    ]

    for pattern, label in patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        if matches:
            warnings.append({
                "type": label,
                "count": len(matches),
                "pattern": pattern,
            })

    return warnings


# ── Compression Execution ────────────────────────────────────

def atomic_write(path: Path, content: str) -> bool:
    """Write content to file atomically using temp file + os.replace()."""
    try:
        parent = path.parent
        parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(parent), suffix=".md.tmp")
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
        os.replace(tmp_path, str(path))
        return True
    except Exception as e:
        # Clean up temp file if it exists
        if 'tmp_path' in locals() and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        print(f"ERROR: Atomic write failed for {path}: {e}", file=sys.stderr)
        return False


def backup_file(path: Path) -> Path:
    """Create a backup of the file before compression."""
    backup_path = path.parent / f"{path.stem}.backup.{datetime.now().strftime('%Y%m%d%H%M%S')}.md"
    if path.exists():
        import shutil
        shutil.copy2(str(path), str(backup_path))
    return backup_path


# ── Smart Compression: Priority Scoring ──────────────────────

P0_KEYWORDS = [
    "永远禁止", "绝对不能", "身份", "安全", "密码", "credentials",
    "核心偏好", "关键决策", "架构", "P0", "CRITICAL", "MUST",
    "git config", "submodule", "API Key", "token", "secret",
    "验收", "强制输出", "硬约束", "输出格式", "规范",
]
P1_KEYWORDS = [
    "偏好", "风格", "常用", "近期", "当前关注", "进行中",
    "investigation", "排查", "推进", "跟踪", "监控",
    "deploy", "配置", "选定", "流程", "每周",
]
P2_PATTERNS = [
    r"^\s*[-*]\s+[A-Za-z]+:\s*.{0,30}$",  # single short bullet
    r"^\s*[-*]\s+\d{4}-\d{2}-\d{2}\s*$",  # date-only bullet
    r"(?:临时|tmp|temp|cache|\.bak)",         # temp/transient
]

def score_memory_priority(entry: str) -> tuple:
    """Score a memory entry as P0 (critical), P1 (valuable), or P2 (expendable).
    
    Returns (priority: str, score: float, reasons: list)
    """
    score = 50.0  # start neutral
    reasons = []
    
    # ── P0 signals ──
    p0_matches = [kw for kw in P0_KEYWORDS if kw.lower() in entry.lower()]
    if p0_matches:
        score += 30
        reasons.append(f"P0 keywords: {p0_matches[:3]}")
    
    # ── P1 signals ──
    p1_matches = [kw for kw in P1_KEYWORDS if kw.lower() in entry.lower()]
    if p1_matches:
        score += 15
        reasons.append(f"P1 keywords: {p1_matches[:3]}")
    
    # ── P2 signals ──
    import re
    p2_matches = [p for p in P2_PATTERNS if re.search(p, entry)]
    if p2_matches:
        score -= 20
        reasons.append("P2 pattern match")
    
    # ── Length heuristics ──
    if len(entry) < 50:
        score -= 10
        reasons.append("very short entry")
    elif len(entry) > 300:
        score += 10
        reasons.append("detailed entry")
    
    # ── Date recency ──
    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', entry)
    if date_match:
        try:
            entry_date = datetime.strptime(date_match.group(1), "%Y-%m-%d")
            days_ago = (datetime.now() - entry_date).days
            if days_ago <= 7:
                score += 15
                reasons.append(f"recent ({days_ago}d ago)")
            elif days_ago <= 30:
                score += 5
            elif days_ago > 90:
                score -= 15
                reasons.append(f"stale ({days_ago}d ago)")
        except ValueError:
            pass
    
    # ── Structural signals ──
    if entry.strip().startswith("## "):
        score += 5
        reasons.append("section heading")
    if "**" in entry:  # bold text
        score += 3
    
    # ── Classify ──
    if score >= 70:
        priority = "P0"
    elif score >= 40:
        priority = "P1"
    else:
        priority = "P2"
    
    return priority, round(score, 1), reasons


def smart_compress_content(content: str, target_chars: int, protect_first: int = 0) -> tuple:
    """Intelligent compression using priority scoring.
    
    1. Split into entries (by headings / blank lines)
    2. Score each entry P0/P1/P2
    3. Protect first N entries (by original order) from discard
    4. Keep all P0, merge P1, discard P2
    5. If still over target, truncate lowest-scoring P1 entries
    
    Args:
        content: Raw memory file content
        target_chars: Target character count after compression
        protect_first: Number of leading entries to protect from compression
    
    Returns (compressed_content, stats_dict).
    """
    import re
    
    original_len = len(content)
    
    # Split into entries (by ## headings first, then by blank lines within sections)
    entries = []
    current_section = ""
    
    lines = content.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        # New section heading
        if re.match(r'^#{1,3}\s+', line):
            if current_section.strip():
                entries.append(current_section.strip())
            current_section = line + '\n'
            i += 1
            # Collect lines until next heading
            while i < len(lines) and not re.match(r'^#{1,3}\s+', lines[i]):
                current_section += lines[i] + '\n'
                i += 1
        else:
            current_section += line + '\n'
            i += 1
    
    if current_section.strip():
        entries.append(current_section.strip())
    
    # Score each entry
    scored = []
    for entry in entries:
        priority, score, reasons = score_memory_priority(entry)
        scored.append({
            "entry": entry,
            "priority": priority,
            "score": score,
            "reasons": reasons,
            "length": len(entry),
        })
    
    # Permanent mark detection: entries with <!-- permanent --> are unconditionally P0
    for s in scored:
        if "<!-- permanent -->" in s["entry"]:
            s["score"] += 100
            s["priority"] = "P0"
            s["reasons"].append("permanent_marker")
    
    # Sort: P0 first, then P1 by score desc, then P2
    priority_order = {"P0": 0, "P1": 1, "P2": 2}
    scored.sort(key=lambda x: (priority_order[x["priority"]], -x["score"]))
    
    # Mark protected entries (first N by original position)
    protected_count = 0
    if protect_first > 0:
        for i in range(min(protect_first, len(scored))):
            scored[i]["protected"] = True
            protected_count += 1
    
    # Collect: protect first N, then keep all P0, then P1 by score until target
    output_entries = []
    current_len = 0
    p0_count = p1_count = p2_discarded = 0
    p1_discarded = []
    
    for s in scored:
        if s.get("protected"):
            output_entries.append(s["entry"])
            current_len += s["length"] + 2
            # Protected entries bypass priority — count as preserved
            if s["priority"] == "P0":
                p0_count += 1
            elif s["priority"] == "P1":
                p1_count += 1
        elif s["priority"] == "P0":
            output_entries.append(s["entry"])
            current_len += s["length"] + 2
            p0_count += 1
        elif s["priority"] == "P1":
            if current_len + s["length"] <= target_chars:
                output_entries.append(s["entry"])
                current_len += s["length"] + 2
                p1_count += 1
            else:
                p1_discarded.append(s)
                p2_discarded += 1
        else:  # P2
            p2_discarded += 1
    
    # If we're still over target after keeping only P0+P1, merge P1 entries
    if current_len > target_chars:
        # Strategy: keep section headings, merge bullet points into summary
        merged = []
        for entry in output_entries:
            if entry.startswith("## ") or entry.startswith("### "):
                merged.append(entry)
            else:
                # Condense: keep first line, add count
                entry_lines = entry.split('\n')
                if len(entry_lines) > 3:
                    merged.append(entry_lines[0] + f"\n  _(...{len(entry_lines)-1} more lines condensed)_")
                else:
                    merged.append(entry)
        output_entries = merged
    
    compressed = '\n\n'.join(output_entries)
    
    if len(compressed) > target_chars:
        compressed = compressed[:target_chars].rstrip()
        compressed += f"\n\n[auto-truncated at {datetime.now().strftime('%Y-%m-%d %H:%M')}]"
    
    stats = {
        "original_chars": original_len,
        "compressed_chars": len(compressed),
        "reduction": original_len - len(compressed),
        "reduction_pct": round((1 - len(compressed) / original_len) * 100, 1) if original_len else 0,
        "method": "smart",
        "p0_kept": p0_count,
        "p1_kept": p1_count,
        "p2_discarded": p2_discarded,
        "protected": protected_count,
        "truncated": len(compressed) > target_chars,
    }
    
    return compressed, stats


# ── Heuristic Compression ────────────────────────────────────
    """Heuristic compression: remove duplicates, merge short entries, trim whitespace.

    Returns (compressed_content, stats_dict).
    """
    original_len = len(content)
    lines = content.split('\n')

    # Step 1: Remove duplicate lines (case-insensitive for bullet points)
    seen_lines = {}
    deduped_lines = []
    dup_count = 0
    for line in lines:
        # Normalize for comparison: strip whitespace and lowercase
        key = line.strip().lower()
        if not key:
            deduped_lines.append(line)
            continue
        if key in seen_lines:
            dup_count += 1
            # Keep the longer version
            if len(line.strip()) > len(seen_lines[key].strip()):
                # Replace with longer version
                idx = deduped_lines.index(seen_lines[key]) if seen_lines[key] in deduped_lines else -1
                if idx >= 0:
                    deduped_lines[idx] = line
            continue
        seen_lines[key] = line
        deduped_lines.append(line)

    content = '\n'.join(deduped_lines)

    # Step 2: Collapse multiple blank lines into one
    content = re.sub(r'\n{3,}', '\n\n', content)

    # Step 3: Merge single-item bullet points under same heading
    # (e.g., "- A\n- B" under "## Data" stays, but duplicate sections merge)

    # Step 4: If still over target, trim trailing whitespace per line
    content = '\n'.join(line.rstrip() for line in content.split('\n'))

    # Step 5: If still over target, remove lines that look like P2 (low value)
    # P2 indicators: temp paths, single-char entries, obvious common knowledge
    p2_patterns = [
        r'^- /tmp/',           # temp file paths
        r'^- \d{4}-\d{2}-\d{2}$',  # date-only entries
        r'^- \.$',             # single dot entries
        r'^\s*$',              # blank lines (already handled but just in case)
    ]

    if len(content) > target_chars:
        lines = content.split('\n')
        filtered = []
        removed_p2 = 0
        for line in lines:
            is_p2 = any(re.match(p, line) for p in p2_patterns)
            if not is_p2:
                filtered.append(line)
            else:
                removed_p2 += 1
        content = '\n'.join(filtered)

    # Step 6: Final check - if still over hard limit, truncate from bottom
    # preserving the header and first sections
    truncated = False
    if len(content) > target_chars:
        # Find the last ## heading that fits
        header_end = 0
        sections = list(re.finditer(r'^#{1,3}\s+', content, re.MULTILINE))
        cut_point = len(content)
        for sec in reversed(sections):
            if sec.start() <= target_chars * 0.9:
                # Find the next section start after this
                idx = sections.index(sec)
                if idx + 1 < len(sections):
                    cut_point = sections[idx + 1].start()
                break

        if cut_point < len(content):
            content = content[:cut_point].rstrip()
            content += f"\n\n[auto-truncated at {datetime.now().strftime('%Y-%m-%d %H:%M')}]"
            truncated = True

    stats = {
        "original_chars": original_len,
        "compressed_chars": len(content),
        "reduction": original_len - len(content),
        "reduction_pct": round((1 - len(content) / original_len) * 100, 1) if original_len else 0,
        "duplicates_removed": dup_count,
        "truncated": truncated,
    }

    return content, stats


def compress_memory(workspace: str, dry_run: bool = False, auto: bool = False,
                   smart: bool = False, protect_first: int = 0) -> dict:
    """Execute memory compression.

    Args:
        workspace: Workspace path
        dry_run: If True, show what would happen without modifying files
        auto: If True, daemon mode (no backup prompt, heuristic only)
        smart: If True, use priority-based intelligent compression
        protect_first: Number of leading entries to protect from compression

    Returns:
        Dict with compression results
    """
    files = get_memory_files(workspace)
    results = {"files": [], "overall": {"compressed": False}}

    for name, path in [("MEMORY.md", files["memory_path"]),
                        ("USER.md", files["user_path"])]:
        if not path.exists():
            continue

        content = read_file_safe(path)
        if not content:
            continue

        # Safety scan first
        secrets = scan_for_secrets(content)
        if secrets:
            results["files"].append({
                "file": name,
                "path": str(path),
                "action": "skipped",
                "reason": f"Found {len(secrets)} potential secret(s). Manual review required.",
                "secrets": secrets,
            })
            continue

        # Check if compression needed
        if name == "MEMORY.md":
            soft_limit = MEMORY_SOFT_LIMIT
            hard_limit = MEMORY_HARD_LIMIT
        else:
            soft_limit = USER_SOFT_LIMIT
            hard_limit = USER_HARD_LIMIT

        if len(content) < soft_limit:
            results["files"].append({
                "file": name,
                "path": str(path),
                "action": "none",
                "reason": f"Under soft limit ({len(content)}/{soft_limit} chars)",
            })
            continue

        # Determine target
        target = soft_limit if len(content) < hard_limit else hard_limit

        # Compress (always use smart compression — only implementation)
        compressed_content, stats = smart_compress_content(content, target, protect_first)

        if stats["original_chars"] == stats["compressed_chars"]:
            results["files"].append({
                "file": name,
                "path": str(path),
                "action": "none",
                "reason": "Heuristic compression found no improvements",
            })
            continue

        if dry_run:
            results["files"].append({
                "file": name,
                "path": str(path),
                "action": "would_compress",
                "stats": stats,
            })
            continue

        # Backup before writing
        backup_path = backup_file(path)

        # Atomic write
        success = atomic_write(path, compressed_content)

        results["files"].append({
            "file": name,
            "path": str(path),
            "action": "compressed" if success else "failed",
            "stats": stats,
            "backup": str(backup_path),
        })

        if success:
            results["overall"]["compressed"] = True

    return results


def full_health_report(workspace: str) -> dict:
    """Generate comprehensive health dashboard combining all subsystems."""
    import subprocess
    
    report = {
        "timestamp": datetime.now().isoformat(),
        "workspace": workspace,
    }
    
    # 1. Memory capacity
    analysis = analyze_memory(workspace)
    report["memory"] = {
        "memory_md": analysis["memory"],
        "user_md": analysis["user"],
    }
    
    # 2. User model health (if available)
    try:
        um_script = SCRIPTS_DIR / "user_model.py"
        if um_script.exists():
            result = subprocess.run(
                [sys.executable, str(um_script), "--health", "--json"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                report["user_model"] = json.loads(result.stdout)
    except Exception:
        report["user_model"] = {"error": "unavailable"}
    
    # 3. Nudge state (if available)
    try:
        nudge_script = SCRIPTS_DIR / "memory_nudge.py"
        if nudge_script.exists():
            result = subprocess.run(
                [sys.executable, str(nudge_script), "--workspace", workspace,
                 "--global", "--status", "--json"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                report["nudge"] = json.loads(result.stdout)
    except Exception:
        report["nudge"] = {"error": "unavailable"}
    
    # 4. Overall status
    issues = []
    mem_status = analysis["memory"]["status"]
    usr_status = analysis["user"]["status"]
    if mem_status == "critical":
        issues.append("MEMORY.md critical")
    elif mem_status == "warning":
        issues.append("MEMORY.md near limit")
    if usr_status == "critical":
        issues.append("USER.md critical")
    elif usr_status == "warning":
        issues.append("USER.md near limit")
    
    um_health = report.get("user_model", {})
    if um_health.get("total_score", 100) < 50:
        issues.append(f"User model health low ({um_health.get('total_score', '?')}/100)")
    
    report["status"] = "healthy" if not issues else ("warning" if len(issues) < 2 else "critical")
    report["issues"] = issues
    
    return report


# ── CLI ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Memory Compressor for WorkBuddy (Hermes-inspired capacity management)"
    )
    parser.add_argument("--workspace", "-w", required=True,
                        help="WorkBuddy workspace path")
    parser.add_argument("--check", action="store_true",
                        help="Quick health check")
    parser.add_argument("--stats", action="store_true",
                        help="Detailed memory statistics")
    parser.add_argument("--suggest", action="store_true",
                        help="Show compression suggestions")
    parser.add_argument("--scan", action="store_true",
                        help="Scan for secrets/credentials in memory files")
    parser.add_argument("--compress", action="store_true",
                        help="Execute compression (modifies files with backup)")
    parser.add_argument("--auto-compress", action="store_true",
                        help="Daemon-safe auto compression (no backup prompt)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what compression would do without modifying files")
    parser.add_argument("--smart", action="store_true",
                        help="Use priority-based intelligent compression (P0 keep, P1 merge, P2 discard)")
    parser.add_argument("--protect-first", type=int, default=0, metavar="N",
                        help="Protect first N entries from compression (keep regardless of priority)")
    parser.add_argument("--health", action="store_true",
                        help="Comprehensive health dashboard: memory + user model + nudge status")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")

    args = parser.parse_args()

    if args.health:
        report = full_health_report(args.workspace)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            mem = report["memory"]["memory_md"]
            usr = report["memory"]["user_md"]
            um = report.get("user_model", {})
            nudge = report.get("nudge", {})
            
            print("🩺 Hermes Memory System — Comprehensive Health Dashboard")
            print(f"   Timestamp: {report['timestamp'][:19]}")
            print(f"   Overall:   {report['status'].upper()}")
            print()
            print("📊 Memory Files")
            print(f"   MEMORY.md: {mem['char_count']}/{mem['soft_limit']} chars ({mem['usage_pct']}%) — {mem['status'].upper()}")
            print(f"   USER.md:   {usr['char_count']}/{usr['soft_limit']} chars ({usr['usage_pct']}%) — {usr['status'].upper()}")
            print()
            if isinstance(um, dict) and "total_score" in um:
                print("🧠 User Model")
                print(f"   Health:    {um['total_score']}/100 [{um.get('grade', '?')}]")
                print(f"   Prefs:     {um['metrics']['total_preferences']} total, "
                      f"{um['metrics']['high_confidence']} high, {um['metrics']['stale']} stale")
                print(f"   Issues:    {um['metrics']['unresolved_contradictions']} contradictions")
            print()
            if isinstance(nudge, dict) and "nudge_count" in nudge:
                print("📬 Nudge State")
                print(f"   Progress:  {nudge.get('tools_since_last_nudge', '?')}/{nudge.get('nudge_interval', '?')}")
                print(f"   Total:     {nudge.get('nudge_count', '?')} nudges")
                print(f"   Due:       {'YES' if nudge.get('nudge_due') else 'No'}")
            print()
            if report["issues"]:
                print("⚠️  Issues:")
                for issue in report["issues"]:
                    print(f"   - {issue}")
            else:
                print("✅ All systems healthy.")
        return

    if args.scan:
        files = get_memory_files(args.workspace)
        all_warnings = {}
        for name, path in files.items():
            content = read_file_safe(path)
            warnings = scan_for_secrets(content)
            if warnings:
                all_warnings[str(path)] = warnings

        if args.json:
            print(json.dumps(all_warnings, ensure_ascii=False, indent=2))
        else:
            if not all_warnings:
                print("✅ No secrets detected in memory files.")
            else:
                print("⚠️  Potential secrets found:")
                for path, warnings in all_warnings.items():
                    print(f"\n  📄 {path}:")
                    for w in warnings:
                        print(f"     - {w['type']}: {w['count']} occurrence(s)")
        return

    # Execute compression
    if args.compress or args.auto_compress or args.dry_run:
        result = compress_memory(
            args.workspace,
            dry_run=args.dry_run,
            auto=args.auto_compress,
            smart=args.smart,
            protect_first=args.protect_first,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            if not result["overall"]["compressed"] and not args.dry_run:
                print("✅ No compression needed or no improvements found.")
            for f in result["files"]:
                action = f["action"]
                if action == "compressed":
                    stats = f["stats"]
                    print(f"💾 {f['file']}: compressed "
                          f"({stats['original_chars']} → {stats['compressed_chars']} chars, "
                          f"-{stats['reduction_pct']}%)")
                    if stats.get("duplicates_removed"):
                        print(f"   Removed {stats['duplicates_removed']} duplicate(s)")
                    if stats.get("truncated"):
                        print(f"   ⚠️  Hard limit hit — auto-truncated")
                    print(f"   Backup: {f.get('backup', 'N/A')}")
                elif action == "would_compress":
                    stats = f["stats"]
                    print(f"🔍 {f['file']}: would compress "
                          f"({stats['original_chars']} → {stats['compressed_chars']} chars)")
                elif action == "skipped":
                    print(f"⚠️  {f['file']}: SKIPPED — {f['reason']}")
                elif action == "none":
                    print(f"✅ {f['file']}: {f['reason']}")
                elif action == "failed":
                    print(f"❌ {f['file']}: Compression FAILED")
        return

    # Default: full analysis with suggestions
    analysis = suggest_compression(args.workspace)

    if args.check:
        mem = analysis["memory"]
        usr = analysis["user"]
        print(f"📊 Memory Status Check")
        print(f"   MEMORY.md: {mem['char_count']}/{mem['soft_limit']} chars "
              f"({mem['usage_pct']}%) — {mem['status'].upper()}")
        print(f"   USER.md:   {usr['char_count']}/{usr['soft_limit']} chars "
              f"({usr['usage_pct']}%) — {usr['status'].upper()}")
        if analysis["needs_action"]:
            print(f"   ⚠️  Action needed: {len(analysis['suggestions'])} suggestion(s)")
        else:
            print(f"   ✅ All within limits")
    elif args.stats:
        if args.json:
            print(json.dumps(analysis, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(analysis, ensure_ascii=False, indent=2))
    elif args.suggest:
        if args.json:
            print(json.dumps(analysis["suggestions"], ensure_ascii=False, indent=2))
        else:
            if not analysis["suggestions"]:
                print("✅ No compression needed.")
            for s in analysis["suggestions"]:
                icon = "🔴" if s["priority"] == "high" else "🟡"
                print(f"{icon} [{s['file']}] {s['action'].upper()}")
                print(f"   {s.get('hint', '')}")
    else:
        # Default: print full analysis
        print(json.dumps(analysis, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
