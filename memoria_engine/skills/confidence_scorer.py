#!/usr/bin/env python3
"""
Confidence Scorer for Hermes WorkBuddy
========================================
Phase 3E: Multi-factor scoring for skill auto-creation decisions.

Scores detected workflow patterns on 5 factors to determine if they
qualify for automatic skill installation.

Factors:
    frequency:      How many times the pattern repeats
    stability:      Cross-session consistency
    diversity:      Variety of tool types involved
    cross_workspace: Appears across multiple workspaces
    complexity:     Number of tool calls in the sequence

Auto-install when score >= 0.7 (configurable).

Usage:
    # Score a pattern from JSON
    python3 confidence_scorer.py --score '<pattern_json>'

    # Score from sequence_analyzer output
    python3 confidence_scorer.py --score-file <path_to_json>

    # Check if a pattern meets auto-install threshold
    python3 confidence_scorer.py --check --score '<pattern_json>'
"""

import argparse
import json
import sys


# ── Constants ────────────────────────────────────────────────

AUTO_INSTALL_THRESHOLD = 0.7
DRY_RUN_THRESHOLD = 0.5  # Suggest but don't auto-install

# Tool diversity categories
TOOL_CATEGORIES = {
    "文件操作": {"Read", "Write", "Edit", "Glob", "Grep", "cat", "sed"},
    "命令执行": {"Bash", "python3", "node", "git", "curl", "npm", "pip", "npx"},
    "网络查询": {"WebFetch", "WebSearch", "curl", "wget"},
    "AI操作": {"Skill", "Agent", "Task"},
    "数据处理": {"python3", "node", "jq", "awk"},
    "文档生成": {"Write", "Edit", "pdfkit", "markdown"},
}


# ── Scoring Functions ─────────────────────────────────────────

def score_frequency(member_count: int) -> float:
    """Score based on repetition frequency.

    ≥3 → 0.5, ≥5 → 0.7, ≥8 → 0.9, ≥10 → 1.0
    """
    if member_count >= 10:
        return 1.0
    elif member_count >= 8:
        return 0.9
    elif member_count >= 5:
        return 0.7
    elif member_count >= 3:
        return 0.5
    elif member_count >= 2:
        return 0.3
    else:
        return 0.1


def score_stability(cross_session_count: int, member_count: int) -> float:
    """Score cross-session consistency.

    Ratio of unique sessions to total occurrences.
    A pattern seen 5 times across 5 different days is highly stable.
    """
    if member_count == 0:
        return 0.0
    ratio = cross_session_count / member_count
    if ratio >= 0.8:
        return 1.0
    elif ratio >= 0.6:
        return 0.8
    elif ratio >= 0.4:
        return 0.5
    elif ratio >= 0.2:
        return 0.3
    else:
        return 0.1


def score_diversity(tools: list) -> float:
    """Score tool type diversity.

    More tool categories → higher score.
    1 category → 0.2, 2 → 0.5, 3 → 0.7, 4+ → 1.0
    """
    if not tools:
        return 0.0

    categories_used = set()
    for tool in tools:
        for cat, members in TOOL_CATEGORIES.items():
            if tool in members:
                categories_used.add(cat)
                break

    count = len(categories_used)
    if count >= 4:
        return 1.0
    elif count == 3:
        return 0.7
    elif count == 2:
        return 0.5
    else:
        return 0.2


def score_cross_workspace(workspace_count: int) -> float:
    """Score cross-workspace appearance.

    1 ws → 0.0, 2 ws → 0.5, 3+ → 1.0
    But this is a bonus, not core.
    """
    if workspace_count >= 3:
        return 1.0
    elif workspace_count == 2:
        return 0.5
    else:
        return 0.0


def score_complexity(tool_count: int) -> float:
    """Score workflow complexity by tool call count.

    ≥5 → 0.5, ≥8 → 0.7, ≥12 → 1.0
    """
    if tool_count >= 12:
        return 1.0
    elif tool_count >= 8:
        return 0.7
    elif tool_count >= 5:
        return 0.5
    elif tool_count >= 3:
        return 0.3
    else:
        return 0.1


# ── Composite Score ───────────────────────────────────────────

def score_pattern(pattern: dict) -> dict:
    """Calculate composite confidence score for a pattern.

    Expected input keys:
        member_count, cross_session_count, cross_workspace_count,
        tool_count, tools (list of tool names)

    Weights:
        frequency:        0.30
        stability:        0.25
        diversity:        0.20
        cross_workspace:  0.10 (bonus)
        complexity:       0.15
    """
    member_count = pattern.get("member_count", 1)
    cross_session = pattern.get("cross_session_count", 1)
    cross_ws = pattern.get("cross_workspace_count", 1)
    tool_count = pattern.get("tool_count", 0)

    # Get tools from representative or members
    tools = pattern.get("tools", [])
    if not tools:
        members = pattern.get("members", [])
        if members:
            tools = members[0].get("tools", [])

    f_score = score_frequency(member_count)
    s_score = score_stability(cross_session, member_count)
    d_score = score_diversity(tools)
    cw_score = score_cross_workspace(cross_ws)
    cx_score = score_complexity(tool_count)

    composite = round(
        f_score * 0.30 +
        s_score * 0.25 +
        d_score * 0.20 +
        cw_score * 0.10 +
        cx_score * 0.15,
        3
    )

    return {
        "composite_score": composite,
        "breakdown": {
            "frequency":       {"score": f_score,      "weight": 0.30, "weighted": round(f_score * 0.30, 3)},
            "stability":       {"score": s_score,      "weight": 0.25, "weighted": round(s_score * 0.25, 3)},
            "diversity":       {"score": d_score,      "weight": 0.20, "weighted": round(d_score * 0.20, 3)},
            "cross_workspace": {"score": cw_score,     "weight": 0.10, "weighted": round(cw_score * 0.10, 3)},
            "complexity":      {"score": cx_score,     "weight": 0.15, "weighted": round(cx_score * 0.15, 3)},
        },
        "meets_auto_install": composite >= AUTO_INSTALL_THRESHOLD,
        "meets_dry_run": composite >= DRY_RUN_THRESHOLD,
        "thresholds": {
            "auto_install": AUTO_INSTALL_THRESHOLD,
            "dry_run": DRY_RUN_THRESHOLD,
        },
        "suggestion": _get_suggestion(composite),
    }


def _get_suggestion(score: float) -> str:
    """Get human-readable suggestion based on score."""
    if score >= AUTO_INSTALL_THRESHOLD:
        return "高置信度 — 建议自动安装为 Skill"
    elif score >= DRY_RUN_THRESHOLD:
        return "中等置信度 — 建议 dry-run 生成并人工审查"
    elif score >= 0.3:
        return "低置信度 — 保持观察，待更多数据积累"
    else:
        return "置信度不足 — 暂不建议创建 Skill"


def score_batch(patterns: list) -> list:
    """Score multiple patterns and return sorted by confidence."""
    scored = []
    for p in patterns:
        result = score_pattern(p)
        result["pattern"] = p
        scored.append(result)
    scored.sort(key=lambda x: x["composite_score"], reverse=True)
    return scored


# ── CLI ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Confidence Scorer for Skill Auto-Creation (Phase 3E)"
    )
    parser.add_argument("--score", type=str,
                        help="Score a pattern (JSON string)")
    parser.add_argument("--score-file", type=str,
                        help="Score patterns from a JSON file")
    parser.add_argument("--check", action="store_true",
                        help="Check if pattern meets auto-install threshold")
    parser.add_argument("--threshold", type=float, default=AUTO_INSTALL_THRESHOLD,
                        help=f"Auto-install threshold (default: {AUTO_INSTALL_THRESHOLD})")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")

    args = parser.parse_args()

    if args.score:
        try:
            pattern = json.loads(args.score)
        except json.JSONDecodeError:
            print("ERROR: Invalid JSON for --score", file=sys.stderr)
            sys.exit(1)

        result = score_pattern(pattern)

        if args.check:
            if args.json:
                print(json.dumps({
                    "meets_threshold": result["composite_score"] >= args.threshold,
                    "score": result["composite_score"],
                    "threshold": args.threshold,
                }, indent=2))
            else:
                if result["composite_score"] >= args.threshold:
                    print(f"✅ Meets threshold: score {result['composite_score']} >= {args.threshold}")
                else:
                    print(f"❌ Below threshold: score {result['composite_score']} < {args.threshold}")
            return

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"📊 Confidence Score: {result['composite_score']:.3f}")
            print(f"   Auto-install: {'✅ YES' if result['meets_auto_install'] else '❌ NO'}")
            print(f"   Dry-run:      {'✅ YES' if result['meets_dry_run'] else '❌ NO'}")
            print(f"\n   Breakdown:")
            for factor, detail in result["breakdown"].items():
                bar = "█" * int(detail["score"] * 10)
                print(f"   {factor:20s}: {detail['score']:.2f} × {detail['weight']:.2f} = {detail['weighted']:.3f} {bar}")
            print(f"\n   {result['suggestion']}")
        return

    if args.score_file:
        try:
            with open(args.score_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)

        # Handle both single pattern and list
        if isinstance(data, list):
            scored = score_batch(data)
        elif isinstance(data, dict):
            # Try to extract patterns from sequence_analyzer output format
            patterns = data.get("patterns", [data])
            scored = score_batch(patterns)
        else:
            print("ERROR: Expected JSON array or object", file=sys.stderr)
            sys.exit(1)

        if args.json:
            print(json.dumps(scored, ensure_ascii=False, indent=2))
        else:
            print(f"📊 Scored {len(scored)} pattern(s):\n")
            for i, s in enumerate(scored, 1):
                icon = "🔴" if s["meets_auto_install"] else "🟡" if s["meets_dry_run"] else "⚪"
                rep = s["pattern"].get("representative_sequence", "?")[:80]
                print(f"  {icon} [{i}] Score: {s['composite_score']:.3f} — {rep}")
                print(f"      {s['suggestion']}")
                if s["meets_auto_install"]:
                    print(f"      ⚡ AUTO-INSTALL candidate!")
                print()
        return

    # Default
    print("📊 Confidence Scorer — Use --score '<json>' or --score-file <path>")


if __name__ == "__main__":
    main()
