#!/usr/bin/env python3
"""
Correction Tracker — 纠错强化学习
==================================
Part of Hermes 3-Tier Memory Architecture — C2.

Scans MEMORY.md across all workspaces for correction entries,
tracks frequency, identifies recurrent blind spots (count > 1).

Correction entry format in MEMORY.md:
```
## 用户纠正 (YYYY-MM-DD)
- 纠正前: [Agent's wrong understanding/behavior]
- 纠正后: [User's correct instruction]
- 生效范围: [本次会话 / 永久]
- 纠正次数: N
- 上次纠正: YYYY-MM-DD
```

Usage:
    # Scan all workspaces for corrections
    python3 correction_tracker.py --scan --json

    # Stats for a specific workspace
    python3 correction_tracker.py --workspace /path --stats

    # Check if similar topic was corrected before
    python3 correction_tracker.py --workspace /path --check "topic text"

    # Increment counter for a matching correction
    python3 correction_tracker.py --workspace /path --increment "topic text"

    # Resolve a blind spot (mark as fixed)
    python3 correction_tracker.py --workspace /path --resolve "topic text"

    # Identify recurrent blind spots across all workspaces
    python3 correction_tracker.py --blind-spots --json

    # Audit all scripts for constants.py reference consistency
    python3 correction_tracker.py --audit-references --json
"""

import argparse
import ast
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path


# ── Constants ────────────────────────────────────────────────

from ..constants import WORKBUDDY_ROOT, MEMORY_DIR, MEMORY_FILE

# Keep local short alias for backward compat
MEMORY_DIR_PATTERN = MEMORY_DIR

# Pattern to match correction entries
CORRECTION_HEADER = re.compile(
    r'^##\s+用户纠正\s*\((\d{4}-\d{2}-\d{2})\)',
    re.MULTILINE
)
CORRECTION_FIELDS = {
    "纠正前": re.compile(r'-\s*纠正前\s*:\s*(.+?)$', re.MULTILINE),
    "纠正后": re.compile(r'-\s*纠正后\s*:\s*(.+?)$', re.MULTILINE),
    "生效范围": re.compile(r'-\s*生效范围\s*:\s*(.+?)$', re.MULTILINE),
    "纠正次数": re.compile(r'-\s*纠正次数\s*:\s*(\d+)', re.MULTILINE),
    "上次纠正": re.compile(r'-\s*上次纠正\s*:\s*(\S+)', re.MULTILINE),
}


# ── Scanner ──────────────────────────────────────────────────

def find_workspace_memory_files() -> list[Path]:
    """Find all workspace MEMORY.md files under {WORKSPACES_ROOT}"""
    results = []
    if not WORKBUDDY_ROOT.exists():
        return results
    for ws_dir in WORKBUDDY_ROOT.iterdir():
        if not ws_dir.is_dir():
            continue
        mem_path = ws_dir / MEMORY_DIR_PATTERN / MEMORY_FILE
        if mem_path.exists():
            results.append(mem_path)
    return results


def parse_corrections(mem_path: Path) -> list[dict]:
    """Parse correction entries from a MEMORY.md file.

    Returns list of dicts with keys:
        date, topic, correction_before, correction_after,
        scope, count, last_corrected, source_file
    """
    try:
        text = mem_path.read_text(encoding="utf-8")
    except (IOError, UnicodeDecodeError):
        return []

    corrections = []

    # Find all correction section headers
    for match in CORRECTION_HEADER.finditer(text):
        date = match.group(1)
        # Extract content until next ## or end
        start = match.start()
        next_section = text.find("\n##", start + 1)
        if next_section == -1:
            next_section = len(text)
        section = text[start:next_section]

        entry = {
            "date": date,
            "source_file": str(mem_path),
        }

        # Extract fields
        for field_name, pattern in CORRECTION_FIELDS.items():
            fm = pattern.search(section)
            if fm:
                val = fm.group(1).strip()
                if field_name == "纠正次数":
                    entry["count"] = int(val)
                elif field_name == "纠正前":
                    entry["before"] = val
                elif field_name == "纠正后":
                    entry["after"] = val
                elif field_name == "生效范围":
                    entry["scope"] = val
                elif field_name == "上次纠正":
                    entry["last_corrected"] = val

        # Default count to 1 if not present
        entry.setdefault("count", 1)
        entry.setdefault("scope", "本次会话")
        entry.setdefault("last_corrected", date)

        corrections.append(entry)

    return corrections


def scan_all_workspaces() -> list[dict]:
    """Scan all workspaces, return all corrections."""
    all_corrections = []
    for mem_path in find_workspace_memory_files():
        all_corrections.extend(parse_corrections(mem_path))
    return all_corrections


def scan_workspace(workspace: str) -> list[dict]:
    """Scan a specific workspace."""
    mem_path = Path(workspace) / MEMORY_DIR_PATTERN / MEMORY_FILE
    if not mem_path.exists():
        return []
    return parse_corrections(mem_path)


# ── Analysis ─────────────────────────────────────────────────

def group_by_similarity(corrections: list[dict], threshold: float = 0.4) -> list[dict]:
    """Group corrections by topic similarity (simple substring overlap).

    Returns list of groups: [{topic, count, dates, entries}]
    """
    if not corrections:
        return []

    groups = []
    used = set()

    for i, c in enumerate(corrections):
        if i in used:
            continue

        group = {
            "topic": c.get("after", c.get("before", ""))[:80],
            "total_count": c.get("count", 1),
            "dates": [c.get("date", "")],
            "entries": [c],
            "scope": c.get("scope", ""),
        }
        used.add(i)

        # Find similar entries
        for j in range(i + 1, len(corrections)):
            if j in used:
                continue
            other = corrections[j]

            # Simple overlap: if the core "纠正后" text shares significant substrings
            after_self = c.get("after", "")
            after_other = other.get("after", "")
            if not after_self or not after_other:
                continue

            # Check word overlap
            words_self = set(after_self.lower().split())
            words_other = set(after_other.lower().split())
            if not words_self:
                continue

            overlap = len(words_self & words_other) / min(len(words_self), len(words_other))
            if overlap >= threshold:
                group["total_count"] += other.get("count", 1)
                group["dates"].append(other.get("date", ""))
                group["entries"].append(other)
                used.add(j)

        groups.append(group)

    # Sort by total_count descending (most recurrent first)
    groups.sort(key=lambda g: g["total_count"], reverse=True)
    return groups


def find_blind_spots(corrections: list[dict]) -> list[dict]:
    """Find recurrent blind spots (corrections with count > 1 or group total > 1)."""
    groups = group_by_similarity(corrections)
    return [g for g in groups if g["total_count"] > 1]


def match_existing(corrections: list[dict], topic: str) -> list[dict]:
    """Find existing corrections that match a given topic (substring-based).

    Returns list of matching correction entries, sorted by recency.
    """
    topic_lower = topic.lower()
    matches = []

    for c in corrections:
        # Check both before and after fields
        before = c.get("before", "").lower()
        after = c.get("after", "").lower()
        scope = c.get("scope", "").lower()

        # Substring check
        if (topic_lower in before or topic_lower in after or
            any(w in before for w in topic_lower.split() if len(w) >= 3) or
            any(w in after for w in topic_lower.split() if len(w) >= 3)):
            matches.append(c)

    # Sort by date descending
    matches.sort(key=lambda c: c.get("date", ""), reverse=True)
    return matches


# ── Increment ────────────────────────────────────────────────

def increment_correction(workspace: str, topic: str) -> dict | None:
    """Find matching correction in workspace MEMORY.md and increment count.

    Returns updated entry or None if no match found.
    """
    mem_path = Path(workspace) / MEMORY_DIR_PATTERN / MEMORY_FILE
    if not mem_path.exists():
        return None

    text = mem_path.read_text(encoding="utf-8")
    corrections = parse_corrections(mem_path)
    matches = match_existing(corrections, topic)

    if not matches:
        return None

    # Update the most recent match
    target = matches[0]
    target_date = target["date"]

    # Find the section in text and update count
    pattern = re.compile(
        rf'(##\s+用户纠正\s*\({re.escape(target_date)}\).*?'
        rf'-\s*纠正次数\s*:\s*)(\d+)',
        re.DOTALL
    )
    m = pattern.search(text)
    if m:
        current_count = int(m.group(2))
        new_count = current_count + 1
        new_text = m.group(1) + str(new_count)

        updated_text = text[:m.start()] + new_text + text[m.end():]

        # Also update last_corrected
        today = datetime.now().strftime("%Y-%m-%d")
        updated_text = re.sub(
            rf'(##\s+用户纠正\s*\({re.escape(target_date)}\).*?'
            rf'-\s*上次纠正\s*:\s*)\S+',
            rf'\g<1>{today}',
            updated_text,
            flags=re.DOTALL
        )

        mem_path.write_text(updated_text, encoding="utf-8")

        return {
            "status": "incremented",
            "topic": topic,
            "count": new_count,
            "previous_count": current_count,
            "date": target_date,
            "is_recurrent": new_count >= 2,
        }

    return {"status": "not_found_in_text"}


# ── Resolve ──────────────────────────────────────────────────

def resolve_correction(workspace: str, topic: str) -> dict:
    """Mark a correction as resolved in MEMORY.md.

    Finds the matching correction entry and adds a status line:
        - 状态: 已解决 (YYYY-MM-DD)

    If already resolved, returns the existing status.
    """
    mem_path = Path(workspace) / MEMORY_DIR_PATTERN / MEMORY_FILE
    if not mem_path.exists():
        return {"status": "error", "message": "MEMORY.md not found"}

    text = mem_path.read_text(encoding="utf-8")
    corrections = parse_corrections(mem_path)
    matches = match_existing(corrections, topic)

    if not matches:
        return {"status": "no_match", "message": f"No correction matched '{topic}'"}

    target = matches[0]
    target_date = target["date"]

    # Check if already resolved
    section_pattern = re.compile(
        rf'(##\s+用户纠正\s*\({re.escape(target_date)}\).*?)(?=\n##\s|\Z)',
        re.DOTALL
    )
    sm = section_pattern.search(text)
    if sm:
        section_text = sm.group(1)
        if "状态: 已解决" in section_text:
            return {
                "status": "already_resolved",
                "topic": topic,
                "date": target_date,
            }

    # Add resolved marker after the last correction field line
    today = datetime.now().strftime("%Y-%m-%d")

    # Find the correction section end (before next ## or EOF)
    section_re = re.compile(
        rf'(##\s+用户纠正\s*\({re.escape(target_date)}\)[\s\S]*?)(?=\n##\s|\n*$)',
        re.DOTALL
    )
    sm = section_re.search(text)
    if not sm:
        return {"status": "error", "message": "Could not locate correction section"}

    section = sm.group(1)
    # Insert resolved marker before the last line
    resolved_line = f"\n- 状态: 已解决 ({today})"

    # Find insertion point — after the last non-empty line before next ## or EOF
    if section.rstrip().endswith("- 状态: 已解决"):
        return {"status": "already_resolved", "topic": topic}

    new_section = section.rstrip() + resolved_line
    updated_text = text[:sm.start()] + new_section + text[sm.end():]

    try:
        mem_path.write_text(updated_text, encoding="utf-8")
    except IOError as e:
        return {"status": "error", "message": f"Write failed: {e}"}

    return {
        "status": "resolved",
        "topic": topic,
        "date": target_date,
        "resolved_at": today,
    }


# ── Report Generation ────────────────────────────────────────

def generate_report(corrections: list[dict]) -> str:
    """Generate a human-readable report of correction statistics."""
    if not corrections:
        return "No corrections found."

    total = len(corrections)
    total_count = sum(c.get("count", 1) for c in corrections)
    groups = group_by_similarity(corrections)
    blind_spots = [g for g in groups if g["total_count"] > 1]

    lines = [
        "# 纠错统计报告",
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        f"## 总览",
        f"- 纠正条目数: {total}",
        f"- 总纠正次数: {total_count}",
        f"- 反复纠正主题: {len(blind_spots)}",
        "",
    ]

    if blind_spots:
        lines.append("## ⚠️ 反复纠正盲点（需优先学习）")
        for g in blind_spots:
            lines.append(f"### {g['topic']}")
            lines.append(f"- 纠正次数: {g['total_count']}")
            lines.append(f"- 出现日期: {', '.join(g['dates'])}")
            lines.append(f"- 生效范围: {g.get('scope', '未知')}")
            lines.append("")

    if groups:
        lines.append("## 所有纠正主题")
        for g in groups:
            flag = "🔴" if g["total_count"] > 1 else "🟢"
            lines.append(f"- {flag} {g['topic']} ({g['total_count']}次)")
        lines.append("")

    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────

def audit_references() -> dict:
    """Audit all scripts in scripts/ directory for constants.py reference consistency.

    Checks:
    1. Scripts that import from constants but also redefine the same constant locally
    2. Scripts that DON'T import from constants but have hardcoded values matching
       constants.py definitions
    3. Scripts that use Path.home() / ".workbuddy" instead of WORKBUDDY_DIR

    Returns dict with findings organized by severity.
    """
    scripts_dir = Path(__file__).parent
    constants_file = scripts_dir / "constants.py"

    if not constants_file.exists():
        return {"status": "error", "message": "constants.py not found"}

    # Load constants.py values
    const_module = {}
    try:
        import constants as _c
        for name in dir(_c):
            if name.isupper() and not name.startswith("_"):
                val = getattr(_c, name)
                # Store string representations for comparison
                if isinstance(val, Path):
                    const_module[name] = str(val)
                elif isinstance(val, (int, float, str)):
                    const_module[name] = val
    except ImportError:
        return {"status": "error", "message": "Failed to import constants"}

    findings = {
        "total_scripts": 0,
        "scripts_with_imports": 0,
        "scripts_without_imports": 0,
        "issues": [],
        "summary": {"duplicate_definitions": 0, "missing_imports": 0, "hardcoded_paths": 0},
    }

    for py_file in sorted(scripts_dir.glob("*.py")):
        if py_file.name in ("constants.py", Path(__file__).name):
            continue

        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError, IOError):
            continue

        findings["total_scripts"] += 1
        imported_names = set()

        # Check for constants imports
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "constants":
                    for alias in node.names:
                        imported_names.add(alias.asname or alias.name)

        if imported_names:
            findings["scripts_with_imports"] += 1
        else:
            findings["scripts_without_imports"] += 1

        # Check for local redefinitions of imported constants
        local_assignments = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        name = target.id
                        if isinstance(node.value, ast.Constant):
                            local_assignments[name] = node.value.value
                        elif isinstance(node.value, ast.Call):
                            # Detect Path.home() / ... patterns
                            pass

        # Find duplicates: names imported from constants but also locally assigned
        duplicates = imported_names & set(local_assignments.keys())
        for dup in sorted(duplicates):
            findings["issues"].append({
                "file": str(py_file),
                "severity": "P2",
                "type": "duplicate_definition",
                "constant": dup,
                "detail": f"Imports '{dup}' from constants but also defines it locally",
            })
            findings["summary"]["duplicate_definitions"] += 1

        # Check for hardcoded paths matching constants.py values
        # Pattern: Path.home() / ".workbuddy" / ...
        if "WORKBUDDY_DIR" not in imported_names:
            hardcoded_wbd = re.search(
                r'Path\.home\(\)\s*/\s*"\.workbuddy"',
                source
            )
            if hardcoded_wbd:
                findings["issues"].append({
                    "file": str(py_file),
                    "severity": "P1",
                    "type": "hardcoded_path",
                    "constant": "WORKBUDDY_DIR",
                    "detail": "Uses Path.home() / '.workbuddy' — should import WORKBUDDY_DIR from constants",
                })
                findings["summary"]["hardcoded_paths"] += 1

        # Check for hardcoded "WorkBuddy" root path
        if "WORKBUDDY_ROOT" not in imported_names:
            hardcoded_root = re.search(
                r'Path\.home\(\)\s*/\s*"WorkBuddy"',
                source
            )
            if hardcoded_root:
                findings["issues"].append({
                    "file": str(py_file),
                    "severity": "P1",
                    "type": "hardcoded_path",
                    "constant": "WORKBUDDY_ROOT",
                    "detail": "Uses Path.home() / 'WorkBuddy' — should import WORKBUDDY_ROOT from constants",
                })
                findings["summary"]["hardcoded_paths"] += 1

        # Check for hardcoded ".workbuddy/memory" pattern
        if "MEMORY_DIR" not in imported_names:
            hardcoded_mem = re.search(
                r'"\.workbuddy/memory"',
                source
            )
            if hardcoded_mem:
                findings["issues"].append({
                    "file": str(py_file),
                    "severity": "P1",
                    "type": "hardcoded_path",
                    "constant": "MEMORY_DIR",
                    "detail": "Uses '.workbuddy/memory' string — should import MEMORY_DIR from constants",
                })
                findings["summary"]["hardcoded_paths"] += 1

        # Check for hardcoded capacity limits
        capacity_map = {
            "MEMORY_SOFT_LIMIT": 3500,
            "MEMORY_HARD_LIMIT": 3500,
            "USER_SOFT_LIMIT": 2500,
            "USER_HARD_LIMIT": 3000,
        }
        for cap_name, cap_val in capacity_map.items():
            if cap_name not in imported_names:
                # Look for bare numeric literals assigned to similar variable names
                for local_name, local_val in local_assignments.items():
                    if local_val == cap_val and local_name.upper().replace("_", "") == cap_name.replace("_", ""):
                        findings["issues"].append({
                            "file": str(py_file),
                            "severity": "P2",
                            "type": "hardcoded_capacity",
                            "constant": cap_name,
                            "detail": f"Hardcodes {cap_name}={cap_val} — should import from constants",
                        })
                        findings["summary"]["missing_imports"] += 1

    # Count scripts without imports
    findings["summary"]["missing_imports"] += findings["scripts_without_imports"]

    findings["status"] = "clean" if not findings["issues"] else "issues_found"
    return findings


def main():
    parser = argparse.ArgumentParser(
        description="Correction Tracker — 纠错强化学习 (C2)"
    )
    parser.add_argument("--workspace",
                        help="WorkBuddy workspace path")
    parser.add_argument("--scan", action="store_true",
                        help="Scan all workspaces for corrections")
    parser.add_argument("--stats", action="store_true",
                        help="Show correction statistics")
    parser.add_argument("--check", type=str,
                        help="Check if similar topic was corrected before")
    parser.add_argument("--increment", type=str,
                        help="Increment counter for matching correction")
    parser.add_argument("--blind-spots", action="store_true",
                        help="Identify recurrent blind spots")
    parser.add_argument("--resolve", type=str,
                        help="Mark a correction as resolved (provide topic text)")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    parser.add_argument("--report", action="store_true",
                        help="Generate full human-readable report")
    parser.add_argument("--audit-references", action="store_true",
                        help="Audit all scripts for constants.py reference consistency")

    args = parser.parse_args()

    # ── Audit references mode ──
    if args.audit_references:
        result = audit_references()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    # Determine scope
    if args.workspace:
        corrections = scan_workspace(args.workspace)
    else:
        corrections = scan_all_workspaces()

    if args.blind_spots:
        spots = find_blind_spots(corrections)
        if args.json:
            print(json.dumps({"blind_spots": spots}, indent=2, ensure_ascii=False))
        else:
            for s in spots:
                print(f"🔴 {s['topic']} ({s['total_count']}次) — {', '.join(s['dates'])}")
            if not spots:
                print("No recurrent blind spots found.")

    elif args.stats:
        total = len(corrections)
        total_count = sum(c.get("count", 1) for c in corrections)
        groups = group_by_similarity(corrections)
        blind_count = sum(1 for g in groups if g["total_count"] > 1)

        if args.json:
            print(json.dumps({
                "total_entries": total,
                "total_corrections": total_count,
                "unique_topics": len(groups),
                "recurrent_topics": blind_count,
                "groups": groups
            }, indent=2, ensure_ascii=False))
        else:
            print(f"总纠正条目: {total} | 总次数: {total_count} | "
                  f"反复纠正主题: {blind_count}/{len(groups)}")

    elif args.check:
        matches = match_existing(corrections, args.check)
        if args.json:
            print(json.dumps({
                "query": args.check,
                "matches": len(matches),
                "entries": matches[:3]  # top 3
            }, indent=2, ensure_ascii=False))
        else:
            if matches:
                print(f"找到 {len(matches)} 条相关纠正:")
                for m in matches[:3]:
                    print(f"  [{m.get('date')}] {m.get('after', '')[:60]} "
                          f"(次数: {m.get('count', 1)})")
            else:
                print(f"未找到与 '{args.check}' 相关的纠正记录")

    elif args.increment:
        ws = args.workspace or str(Path.cwd())
        result = increment_correction(ws, args.increment)
        if result:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(json.dumps({
                "status": "no_match",
                "message": f"No existing correction matched '{args.increment}'"
            }, ensure_ascii=False))

    elif args.resolve:
        ws = args.workspace or str(Path.cwd())
        result = resolve_correction(ws, args.resolve)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.report:
        print(generate_report(corrections))

    elif args.scan:
        if args.json:
            print(json.dumps(corrections, indent=2, ensure_ascii=False))
        else:
            for c in corrections:
                print(f"[{c.get('date', '?')}] {c.get('after', '')[:60]} "
                      f"(次数: {c.get('count', 1)}) — {c.get('source_file', '')}")

    else:
        # Default: show stats
        total = len(corrections)
        total_count = sum(c.get("count", 1) for c in corrections)
        print(f"纠错追踪器 — {total} 条纠正, {total_count} 次总计")
        if args.workspace:
            print(f"工作区: {args.workspace}")


if __name__ == "__main__":
    main()
