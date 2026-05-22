#!/usr/bin/env python3
"""
Memory Quality Scoring System
==============================
Parses MEMORY.md files across workspaces and scores each memory entry
for quality, priority, and retention value.

Uses a weighted scoring formula to classify entries as:
    P0 (≥10): Core knowledge — never compress
    P1 (5-9): Valuable — retain but can merge
    P2 (<5) : Low quality — candidate for removal/compression

Scoring factors:
    +10  is_correction        (section title contains "用户纠正")
    +5   recurrent_blind_spot (纠正次数 ≥ 2)
    +3   recent_7d            (last updated ≤ 7 days ago)
    +1   recent_30d           (7-30 days)
    -2   stale_30d            (> 30 days)
    -3   temporary_scope      (生效范围: 本次会话)
    -2   too_short            (content < 30 characters)

Usage:
    # Score a single workspace
    python3 memory_quality.py --workspace /path/to/workspace --json

    # Score all workspaces (global)
    python3 memory_quality.py --global --json

    # Only show low-quality (P2) entries
    python3 memory_quality.py --global --low-quality --json

    # Auto-remove P2 entries (daemon-safe, no confirmation)
    python3 memory_quality.py --global --auto-remove-p2 --json

    # Generate human-readable report
    python3 memory_quality.py --global --report
"""

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..constants import (
    WORKBUDDY_ROOT, MEMORY_DIR, MEMORY_FILE,
    QUALITY_P0_THRESHOLD, QUALITY_P1_THRESHOLD,
)


# Regex patterns for parsing
SECTION_HEADER = re.compile(r"^##\s+(.+)$")
LIST_ITEM = re.compile(r"^-\s+(.*)")
CORRECTION_KEY = re.compile(r"^纠正[前后次数]|^生效范围|^上次纠正")
DATE_IN_TITLE = re.compile(r"\((\d{4}-\d{2}-\d{2})\)")
DATE_IN_FIELD = re.compile(r"(\d{4}-\d{2}-\d{2})")
CORRECTION_COUNT = re.compile(r"纠正次数:\s*(\d+)")


# ── Parsing ──────────────────────────────────────────────────

def parse_memory_md(filepath: Path) -> list:
    """Parse a MEMORY.md file into a list of sections.

    Each ## section becomes one scored entry with its full content concatenated.
    The # title line is skipped.

    Returns list of dicts:
        {section_title, content (all list items joined), entries_raw}
    """
    if not filepath.exists():
        return []

    try:
        raw = filepath.read_text(encoding="utf-8")
    except (IOError, UnicodeDecodeError):
        return []

    sections = []
    current_section = None
    current_lines = []

    for line in raw.split("\n"):
        m = SECTION_HEADER.match(line)
        if m:
            # Flush previous section
            if current_section is not None and current_lines:
                content = "\n".join(current_lines)
                sections.append({
                    "section_title": current_section,
                    "content": content,
                })
            current_section = m.group(1).strip()
            current_lines = []
        elif current_section is not None:
            # Only collect lines after first ## section (skip # title)
            item_match = LIST_ITEM.match(line)
            if item_match:
                current_lines.append(item_match.group(1))
            elif line.strip() and current_lines:
                # continuation line (multi-line entry)
                current_lines.append(line.strip())

    # Flush last section
    if current_section is not None and current_lines:
        content = "\n".join(current_lines)
        sections.append({
            "section_title": current_section,
            "content": content,
        })

    return sections


def extract_correction_meta(section_title: str, entry_text: str) -> dict:
    """Extract metadata from a correction entry.

    Returns dict with: is_correction, correction_count, last_updated, scope
    """
    is_correction = "用户纠正" in section_title

    # Count corrections
    count_match = CORRECTION_COUNT.search(entry_text)
    correction_count = int(count_match.group(1)) if count_match else 0

    # Extract last updated date — prefer "上次纠正" field, then "纠正后" field
    last_updated = None
    for line in entry_text.split("\n"):
        line_s = line.strip()
        if "上次纠正" in line_s or "纠正后" in line_s:
            date_m = DATE_IN_FIELD.search(line_s)
            if date_m:
                last_updated = date_m.group(1)
                break

    # Also check section title for date
    if not last_updated:
        title_m = DATE_IN_TITLE.search(section_title)
        if title_m:
            last_updated = title_m.group(1)

    # Calculate days since update
    days_since = 999
    if last_updated:
        try:
            updated_dt = datetime.strptime(last_updated, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            days_since = (datetime.now(timezone.utc) - updated_dt).days
        except ValueError:
            pass

    # Scope detection
    scope = None
    for line in entry_text.split("\n"):
        line_s = line.strip()
        if "生效范围" in line_s:
            scope = line_s.split(":", 1)[-1].strip()
            break

    return {
        "is_correction": is_correction,
        "correction_count": correction_count,
        "last_updated": last_updated,
        "days_since_update": days_since,
        "scope": scope,
    }


# ── Scoring ──────────────────────────────────────────────────

def score_entry(section_title: str, entry_text: str) -> dict:
    """Score a single memory entry.

    Returns dict with: score, priority, breakdown, action, content_preview
    """
    meta = extract_correction_meta(section_title, entry_text)
    s = 0
    breakdown = {}

    # Correction bonus
    if meta["is_correction"]:
        s += 10
        breakdown["is_correction"] = 10

        if meta["correction_count"] >= 2:
            s += 5
            breakdown["recurrent_blind_spot"] = 5

    # Recency scoring
    days = meta["days_since_update"]
    if days <= 7:
        s += 3
        breakdown["recent_7d"] = 3
    elif days <= 30:
        s += 1
        breakdown["recent_30d"] = 1
    else:
        s -= 2
        breakdown["stale_30d"] = -2

    # Temporary scope penalty
    if meta["scope"] == "本次会话":
        s -= 3
        breakdown["temporary_scope"] = -3

    # Too short penalty
    content_clean = entry_text.strip()
    if len(content_clean) < 30:
        s -= 2
        breakdown["too_short"] = -2

    # Priority
    if s >= QUALITY_P0_THRESHOLD:
        priority = "P0"
        action = "keep"
    elif s >= QUALITY_P1_THRESHOLD:
        priority = "P1"
        action = "keep_or_merge"
    else:
        priority = "P2"
        action = "review_for_removal"

    # Preview (truncated)
    preview = content_clean[:120] + ("..." if len(content_clean) > 120 else "")

    return {
        "section_title": section_title,
        "priority": priority,
        "score": s,
        "breakdown": breakdown,
        "action": action,
        "content_preview": preview,
        "meta": meta,
    }


def score_workspace(workspace_path: str) -> dict:
    """Score all MEMORY.md entries for a workspace."""
    ws = Path(workspace_path)
    memory_file = ws / MEMORY_DIR / MEMORY_FILE

    if not memory_file.exists():
        return {
            "workspace": str(ws),
            "workspace_name": ws.name,
            "entries": [],
            "summary": {"total": 0, "p0": 0, "p1": 0, "p2": 0},
        }

    sections = parse_memory_md(memory_file)
    entries = []

    for section in sections:
        scored = score_entry(section["section_title"], section["content"])
        entries.append(scored)

    summary = {
        "total": len(entries),
        "p0": sum(1 for e in entries if e["priority"] == "P0"),
        "p1": sum(1 for e in entries if e["priority"] == "P1"),
        "p2": sum(1 for e in entries if e["priority"] == "P2"),
    }

    return {
        "workspace": str(ws),
        "workspace_name": ws.name,
        "entries": entries,
        "summary": summary,
    }


def discover_workspaces() -> list:
    """Discover all workspaces with MEMORY.md files."""
    workspaces = []
    if not WORKBUDDY_ROOT.exists():
        return workspaces
    for item in sorted(WORKBUDDY_ROOT.iterdir()):
        if item.is_dir():
            memory_file = item / MEMORY_DIR / MEMORY_FILE
            if memory_file.exists():
                workspaces.append(str(item))
    return workspaces


def score_all_workspaces(low_quality_only: bool = False) -> dict:
    """Score all workspaces and return aggregated results."""
    workspaces = discover_workspaces()
    results = []
    global_p0 = global_p1 = global_p2 = 0

    for ws_path in workspaces:
        result = score_workspace(ws_path)
        entries = result["entries"]

        if low_quality_only:
            entries = [e for e in entries if e["priority"] == "P2"]

        global_p0 += result["summary"]["p0"]
        global_p1 += result["summary"]["p1"]
        global_p2 += result["summary"]["p2"]

        if entries or not low_quality_only:
            results.append({
                "workspace": result["workspace"],
                "workspace_name": result["workspace_name"],
                "entries": entries,
                "summary": {
                    "total": len(entries),
                    "p0": sum(1 for e in entries if e["priority"] == "P0"),
                    "p1": sum(1 for e in entries if e["priority"] == "P1"),
                    "p2": sum(1 for e in entries if e["priority"] == "P2"),
                },
            })

    return {
        "total_workspaces": len(workspaces),
        "workspaces_with_memory": len(results),
        "global_summary": {"p0": global_p0, "p1": global_p1, "p2": global_p2},
        "workspaces": results,
    }


def auto_remove_p2(workspace_path: str = None, global_mode: bool = False,
                   dry_run: bool = False) -> dict:
    """Automatically remove P2 (low quality, score < 5) entries from MEMORY.md.

    Operates on a single workspace or globally across all workspaces.
    Uses atomic write (temp file + os.replace) for safety.
    Creates backup before modification.

    Returns dict with removal stats per workspace.
    """
    results = {"removed": [], "skipped": [], "dry_run": dry_run}

    if global_mode:
        workspaces = discover_workspaces()
    elif workspace_path:
        workspaces = [workspace_path]
    else:
        return {"error": "No workspace specified"}

    for ws_path in workspaces:
        ws = Path(ws_path)
        memory_file = ws / MEMORY_DIR / MEMORY_FILE
        if not memory_file.exists():
            continue

        # Score entries
        scored = score_workspace(ws_path)
        p2_entries = [e for e in scored["entries"] if e["priority"] == "P2"]

        if not p2_entries:
            results["skipped"].append({
                "workspace": str(ws),
                "workspace_name": ws.name,
                "reason": "No P2 entries",
            })
            continue

        # Read original text
        try:
            original_text = memory_file.read_text(encoding="utf-8")
        except (IOError, UnicodeDecodeError):
            results["skipped"].append({
                "workspace": str(ws),
                "workspace_name": ws.name,
                "reason": "Read failed",
            })
            continue

        # Remove P2 sections from text
        new_text = original_text
        removed_titles = []
        for entry in p2_entries:
            title = entry["section_title"]
            # Find the ## Section block and remove it
            # Pattern: ## Title\n...content... (until next ## or EOF)
            pattern = re.compile(
                rf'^##\s+{re.escape(title)}\s*\n(?:.*\n)*?(?=^##\s|\Z)',
                re.MULTILINE
            )
            new_text = pattern.sub("", new_text)
            removed_titles.append(title)

        # Clean up extra blank lines (3+ consecutive → 2)
        new_text = re.sub(r'\n{3,}', '\n\n', new_text)

        if dry_run:
            results["removed"].append({
                "workspace": str(ws),
                "workspace_name": ws.name,
                "entries_removed": len(removed_titles),
                "entries": removed_titles,
                "would_remove_chars": len(original_text) - len(new_text),
            })
            continue

        # Backup
        backup_path = memory_file.parent / f"MEMORY.backup.{datetime.now().strftime('%Y%m%d%H%M%S')}.md"
        try:
            import shutil
            shutil.copy2(str(memory_file), str(backup_path))
        except OSError:
            pass

        # Atomic write
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(memory_file.parent), suffix=".md.tmp"
            )
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(new_text)
            os.replace(tmp_path, str(memory_file))
            write_ok = True
        except OSError:
            write_ok = False
            if 'tmp_path' in dir() and os.path.exists(tmp_path):
                os.unlink(tmp_path)

        results["removed"].append({
            "workspace": str(ws),
            "workspace_name": ws.name,
            "entries_removed": len(removed_titles),
            "entries": removed_titles,
            "chars_removed": len(original_text) - len(new_text),
            "backup": str(backup_path),
            "written": write_ok,
        })

    return results


def format_report(result: dict) -> str:
    """Generate a human-readable quality report."""
    lines = ["📊 记忆质量评分报告", "=" * 50, ""]

    if "workspaces" in result:
        # Global report
        gs = result["global_summary"]
        lines.append(f"工作区总数: {result['total_workspaces']}")
        lines.append(f"有 MEMORY.md 的工作区: {result['workspaces_with_memory']}")
        lines.append(f"全局统计: P0={gs['p0']}  P1={gs['p1']}  P2={gs['p2']}")
        lines.append("")

        for ws in result["workspaces"]:
            lines.append(f"📁 {ws['workspace_name']} (P0:{ws['summary']['p0']} P1:{ws['summary']['p1']} P2:{ws['summary']['p2']})")
            for e in ws["entries"]:
                icon = {"P0": "⭐", "P1": "📌", "P2": "🗑️"}.get(e["priority"], "❓")
                score_str = f"({e['score']})"
                breakdown_str = ", ".join(f"{k}={v}" for k, v in e["breakdown"].items())
                lines.append(f"  {icon} [{e['priority']} {score_str}] {e['section_title']}")
                lines.append(f"     {e['content_preview']}")
                lines.append(f"     ↳ {breakdown_str}")
                lines.append("")
    else:
        # Single workspace
        ws = result
        lines.append(f"工作区: {ws['workspace_name']}")
        lines.append(f"总条目: {ws['summary']['total']} (P0:{ws['summary']['p0']} P1:{ws['summary']['p1']} P2:{ws['summary']['p2']})")
        lines.append("")

        for e in ws["entries"]:
            icon = {"P0": "⭐", "P1": "📌", "P2": "🗑️"}.get(e["priority"], "❓")
            score_str = f"({e['score']})"
            breakdown_str = ", ".join(f"{k}={v}" for k, v in e["breakdown"].items())
            lines.append(f"{icon} [{e['priority']} {score_str}] {e['section_title']}")
            lines.append(f"   {e['content_preview']}")
            lines.append(f"   ↳ {breakdown_str}")
            lines.append("")

    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Memory Quality Scoring System"
    )
    parser.add_argument("--workspace", "-w",
                        help="WorkBuddy workspace path (for single workspace scoring)")
    parser.add_argument("--global", dest="global_mode", action="store_true",
                        help="Score all workspaces globally")
    parser.add_argument("--low-quality", action="store_true",
                        help="Only show P2 (low quality) entries")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    parser.add_argument("--report", action="store_true",
                        help="Generate human-readable report")
    parser.add_argument("--auto-remove-p2", action="store_true",
                        help="Auto-remove all P2 (score < 5) entries from MEMORY.md (creates .backup)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview --auto-remove-p2 without modifying files")

    args = parser.parse_args()

    # ── Auto-remove P2 mode ──
    if args.auto_remove_p2:
        result = auto_remove_p2(
            workspace_path=args.workspace,
            global_mode=args.global_mode,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if not args.workspace and not args.global_mode:
        parser.error("either --workspace or --global is required")

    if args.global_mode:
        result = score_all_workspaces(low_quality_only=args.low_quality)

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(format_report(result))
    else:
        result = score_workspace(args.workspace)

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.report:
            print(format_report(result))
        else:
            # Default: compact summary
            s = result["summary"]
            print(f"📁 {result['workspace_name']}: P0={s['p0']} P1={s['p1']} P2={s['p2']}")
            for e in result["entries"]:
                icon = {"P0": "⭐", "P1": "📌", "P2": "🗑️"}.get(e["priority"], "❓")
                print(f"  {icon} [{e['priority']} {e['score']}] {e['section_title']}")


if __name__ == "__main__":
    main()
