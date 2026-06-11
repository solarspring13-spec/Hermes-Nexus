#!/usr/bin/env python3
"""
Agent Memory Router for Hermes WorkBuddy
==========================================
Phase 3D: Topic-based routing engine for the shared memory pool.

Extracts keywords from memory entries and routes them to relevant workspaces
using Jaccard similarity matching.

Usage:
    # Extract topics from content
    python3 agent_router.py --topics "some memory content here"

    # Find relevant shared memories for a workspace
    python3 agent_router.py --relevant --workspace <cwd>

    # Route a specific pool entry to matching workspaces
    python3 agent_router.py --route --entry-id <id> --workspace <cwd>
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path

from ..memory.memory_pool import _load_pool, SHARED_DIR


# ── Constants ────────────────────────────────────────────────

from ..constants import WORKBUDDY_ROOT, DEFAULT_ROUTE_THRESHOLD

logger = logging.getLogger(__name__)

# Chinese stop words (common words that don't carry topic signal)
CN_STOP_WORDS = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "他", "她", "它", "们", "那", "些",
    "什么", "怎么", "如何", "可以", "这个", "那个", "还是", "或者", "以及",
    "使用", "进行", "通过", "对于", "关于", "已经", "需要", "可能", "应该",
}

# English stop words
EN_STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "must", "can", "could", "i", "you", "he",
    "she", "it", "we", "they", "me", "him", "her", "us", "them", "my",
    "your", "his", "its", "our", "their", "this", "that", "these", "those",
    "and", "or", "but", "not", "no", "yes", "if", "then", "else", "when",
    "where", "why", "how", "all", "each", "every", "both", "few", "more",
    "most", "other", "some", "such", "only", "own", "same", "so", "than",
    "too", "very", "just", "about", "also", "into", "over", "after",
}


# ── Topic Extraction ──────────────────────────────────────────

def extract_cn_keywords(text: str, max_keywords: int = 5) -> list:
    """Extract Chinese keywords using simple character n-gram TF.

    Splits Chinese text into bigrams, filters stop words, ranks by frequency.
    """
    # Extract Chinese characters only
    cn_chars = re.findall(r'[\u4e00-\u9fff]+', text.lower())
    cn_text = ''.join(cn_chars)

    if len(cn_text) < 2:
        return []

    # Bigram extraction
    bigrams = {}
    for i in range(len(cn_text) - 1):
        bigram = cn_text[i:i+2]
        if bigram not in CN_STOP_WORDS:
            bigrams[bigram] = bigrams.get(bigram, 0) + 1

    # Also extract longer sequences (3-4 chars) for multi-char words
    for window in (3, 4):
        for i in range(len(cn_text) - window + 1):
            seq = cn_text[i:i+window]
            if seq not in CN_STOP_WORDS and len(seq) >= 3:
                bigrams[seq] = bigrams.get(seq, 0) + 1

    # Sort by frequency
    sorted_keywords = sorted(bigrams.items(), key=lambda x: x[1], reverse=True)
    return [kw for kw, _ in sorted_keywords[:max_keywords]]


def extract_en_keywords(text: str, max_keywords: int = 5) -> list:
    """Extract English keywords using word frequency."""
    words = re.findall(r'[a-zA-Z]{3,}', text.lower())
    word_freq = {}
    for w in words:
        if w not in EN_STOP_WORDS:
            word_freq[w] = word_freq.get(w, 0) + 1

    sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
    return [w for w, _ in sorted_words[:max_keywords]]


def suggest_topics(content: str, max_keywords: int = 8) -> list:
    """Extract topics from content (Chinese + English mixed)."""
    cn_keywords = extract_cn_keywords(content, max_keywords // 2)
    en_keywords = extract_en_keywords(content, max_keywords // 2)
    return cn_keywords + en_keywords


# ── Workspace Profiling ───────────────────────────────────────

def _get_workspace_topics(workspace: str) -> list:
    """Extract topics from a workspace's MEMORY.md."""
    memory_path = Path(workspace) / ".workbuddy" / "memory" / "MEMORY.md"
    if not memory_path.exists():
        return []

    try:
        content = memory_path.read_text(encoding="utf-8")
        return suggest_topics(content)
    except (OSError, UnicodeDecodeError) as e:
        logger.error("Failed to read workspace topics from %s: %s", memory_path, e)
        return []


def _find_all_workspaces() -> list:
    """Find all workspaces with .workbuddy/memory/."""
    if not WORKBUDDY_ROOT.exists():
        return []

    workspaces = []
    for project_dir in WORKBUDDY_ROOT.iterdir():
        memory_dir = project_dir / ".workbuddy" / "memory"
        if memory_dir.exists() and any(memory_dir.glob("*.md")):
            workspaces.append(str(project_dir))
    return sorted(workspaces)


# ── Jaccard Similarity ────────────────────────────────────────

def jaccard_similarity(set_a: set, set_b: set) -> float:
    """Calculate Jaccard similarity between two sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


# ── Routing ───────────────────────────────────────────────────

def route_to_workspace(pool_entry: dict, workspace_list: list,
                       threshold: float = DEFAULT_ROUTE_THRESHOLD) -> list:
    """Route a shared pool entry to matching workspaces.

    Returns list of (workspace, similarity_score) sorted by relevance.
    """
    entry_topics = set(pool_entry.get("topics", []))
    if not entry_topics:
        # Extract topics from content if not provided
        entry_topics = set(suggest_topics(pool_entry.get("content", "")))

    matches = []
    for ws in workspace_list:
        ws_topics = set(_get_workspace_topics(ws))
        if not ws_topics:
            continue
        score = jaccard_similarity(entry_topics, ws_topics)
        if score >= threshold:
            matches.append((ws, round(score, 3)))

    matches.sort(key=lambda x: x[1], reverse=True)
    return matches


def find_relevant(workspace: str, pool: list = None,
                  threshold: float = DEFAULT_ROUTE_THRESHOLD,
                  limit: int = 10) -> list:
    """Find shared pool entries relevant to a workspace.

    Uses workspace's MEMORY.md topics as the profile.
    """
    if pool is None:
        pool = _load_pool()

    ws_topics = set(_get_workspace_topics(workspace))
    if not ws_topics or not pool:
        return []

    scored = []
    for entry in pool:
        entry_topics = set(entry.get("topics", []))
        if not entry_topics:
            entry_topics = set(suggest_topics(entry.get("content", "")))

        score = jaccard_similarity(ws_topics, entry_topics)
        if score >= threshold:
            scored.append((score, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in scored[:limit]]


# ── CLI ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Agent Memory Router for WorkBuddy (Phase 3D)"
    )
    parser.add_argument("--topics", type=str,
                        help="Extract topics from given content")
    parser.add_argument("--route", action="store_true",
                        help="Route a pool entry to matching workspaces")
    parser.add_argument("--entry-id", type=str,
                        help="Pool entry ID (for --route)")
    parser.add_argument("--relevant", action="store_true",
                        help="Find relevant shared memories for a workspace")
    parser.add_argument("--workspace", "-w", type=str, default="",
                        help="Workspace path for routing context")
    parser.add_argument("--threshold", type=float, default=DEFAULT_ROUTE_THRESHOLD,
                        help=f"Similarity threshold (default: {DEFAULT_ROUTE_THRESHOLD})")
    parser.add_argument("--limit", type=int, default=10,
                        help="Max results")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")

    args = parser.parse_args()

    if args.topics:
        keywords = suggest_topics(args.topics)
        if args.json:
            print(json.dumps({"content": args.topics, "topics": keywords},
                           ensure_ascii=False, indent=2))
        else:
            print(f"🔑 Extracted topics: {', '.join(keywords)}")
        return

    if args.route:
        if not args.entry_id:
            print("ERROR: --entry-id is required with --route", file=sys.stderr)
            sys.exit(1)

        pool = _load_pool()
        entry = next((e for e in pool if e.get("id") == args.entry_id), None)
        if not entry:
            print(f"ERROR: Entry '{args.entry_id}' not found in pool", file=sys.stderr)
            sys.exit(1)

        workspaces = _find_all_workspaces()
        if args.workspace and args.workspace not in workspaces:
            workspaces.append(args.workspace)

        matches = route_to_workspace(entry, workspaces, threshold=args.threshold)

        if args.json:
            print(json.dumps({
                "entry_id": args.entry_id,
                "entry_content": entry.get("content", "")[:100],
                "matches": [{"workspace": Path(w).name, "score": s} for w, s in matches],
            }, ensure_ascii=False, indent=2))
        else:
            if not matches:
                print(f"📭 No matching workspaces for entry {args.entry_id}")
            else:
                print(f"🔗 Routed to {len(matches)} workspaces:")
                for ws, score in matches:
                    icon = "🟢" if score >= 0.5 else "🟡"
                    print(f"   {icon} {Path(ws).name} (similarity: {score})")
        return

    if args.relevant:
        if not args.workspace:
            print("ERROR: --workspace is required with --relevant", file=sys.stderr)
            sys.exit(1)

        pool = _load_pool()
        results = find_relevant(args.workspace, pool,
                                threshold=args.threshold, limit=args.limit)

        if args.json:
            print(json.dumps({
                "workspace": Path(args.workspace).name,
                "workspace_topics": _get_workspace_topics(args.workspace),
                "relevant": [{
                    "id": e.get("id"),
                    "content": e.get("content", "")[:100],
                    "topics": e.get("topics", []),
                    "priority": e.get("priority", "P1"),
                } for e in results],
            }, ensure_ascii=False, indent=2))
        else:
            ws_topics = _get_workspace_topics(args.workspace)
            print(f"📊 Workspace: {Path(args.workspace).name}")
            print(f"   Topics: {', '.join(ws_topics[:8])}")
            if not results:
                print(f"   📭 No relevant shared memories found.")
            else:
                print(f"   🔗 {len(results)} relevant shared memories:")
                for i, entry in enumerate(results, 1):
                    content_preview = entry.get("content", "")[:80]
                    topics = ', '.join(entry.get("topics", [])[:5])
                    print(f"      [{i}] [{entry.get('priority', 'P1')}] {content_preview}")
                    print(f"          Topics: {topics}")
        return

    # Default: show usage
    print("🧭 Agent Router — Use --topics, --route, or --relevant")
    workspaces = _find_all_workspaces()
    print(f"   Found {len(workspaces)} workspace(s) with memory")


if __name__ == "__main__":
    main()
