#!/usr/bin/env python3
"""
Sequence Analyzer for Hermes WorkBuddy
========================================
Phase 3E: Tool call sequence extraction and clustering.

Extracts tool call sequences from FTS5-indexed session data,
clusters similar workflows using Levenshtein distance,
and identifies cross-session repeated patterns.

Usage:
    # Extract tool sequences from workspace
    python3 sequence_analyzer.py --extract --workspace <cwd>

    # Cluster sequences and find patterns
    python3 sequence_analyzer.py --cluster --workspace <cwd>

    # Generate pattern report
    python3 sequence_analyzer.py --pattern-report --workspace <cwd>

    # Analyze all workspaces
    python3 sequence_analyzer.py --analyze-all
"""

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path


# ── Constants ────────────────────────────────────────────────

from ..constants import (
    WORKBUDDY_ROOT, SCRIPTS_DIR, DB_PATH,
    MIN_SEQUENCE_LENGTH, MIN_PATTERN_FREQ,
)

# Tool names to detect in session logs (as they appear in daily logs)
TOOL_PATTERNS = [
    r'\b(Bash|Read|Write|Edit|WebFetch|WebSearch|Skill|Agent)\b',
    r'\b(python3|node|git|curl|npm|pip)\b',
    r'\b(用|使用|运行|执行|调用|创建|生成|分析|查询|搜索|修改|更新|添加|安装|配置|初始化|重建|推送|同步)\s+(\S+)',
]


# ── Levenshtein Distance ──────────────────────────────────────

def levenshtein_distance(a: list, b: list) -> int:
    """Levenshtein edit distance between two sequences of tool names."""
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]

    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            cost = 0 if a[i-1] == b[j-1] else 1
            dp[i][j] = min(
                dp[i-1][j] + 1,      # deletion
                dp[i][j-1] + 1,      # insertion
                dp[i-1][j-1] + cost  # substitution
            )

    return dp[m][n]


def levenshtein_similarity(a: list, b: list) -> float:
    """Normalized similarity score (0-1) from Levenshtein distance."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    max_len = max(len(a), len(b))
    dist = levenshtein_distance(a, b)
    return 1.0 - (dist / max_len)


# ── Tool Sequence Extraction ──────────────────────────────────

def _extract_tools_from_text(text: str) -> list:
    """Extract tool names from session text using pattern matching."""
    tools = []
    for pattern in TOOL_PATTERNS:
        matches = re.findall(pattern, text)
        for m in matches:
            if isinstance(m, tuple):
                tool = ''.join(m)
            else:
                tool = m
            tool = tool.strip()
            if tool and len(tool) > 1:
                tools.append(tool)
    return tools


def extract_sequences(workspace: str) -> list:
    """Extract tool call sequences from workspace FTS5 index.

    Each sequence is a dict with: tool_list, date, topic, length.
    """
    db_path = Path(workspace) / ".workbuddy" / "session_index.db"
    if not db_path.exists():
        return []

    sequences = []
    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT date, topic, content FROM session_fts"
        ).fetchall()

        for date, topic, content in rows:
            tools = _extract_tools_from_text(content)
            if len(tools) >= MIN_SEQUENCE_LENGTH:
                sequences.append({
                    "date": date,
                    "topic": topic,
                    "tools": tools,
                    "tool_list": " → ".join(tools),
                    "length": len(tools),
                })
        conn.close()
    except Exception:
        pass

    return sequences


def find_all_workspaces() -> list:
    """Find all workspace paths with FTS5 index."""
    if not WORKBUDDY_ROOT.exists():
        return []

    workspaces = []
    for project_dir in WORKBUDDY_ROOT.iterdir():
        db_path = project_dir / ".workbuddy" / "session_index.db"
        if db_path.exists():
            workspaces.append(str(project_dir))
    return sorted(workspaces)


# ── Sequence Clustering ───────────────────────────────────────

def cluster_sequences(sequences: list, threshold: float = 0.6) -> list:
    """Agglomerative clustering of tool sequences by Levenshtein similarity.

    Returns list of clusters, each with: members, representative, frequency.
    """
    if not sequences:
        return []

    n = len(sequences)

    # Build similarity matrix
    sim_matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        sim_matrix[i][i] = 1.0
        for j in range(i + 1, n):
            sim = levenshtein_similarity(sequences[i]["tools"], sequences[j]["tools"])
            sim_matrix[i][j] = sim_matrix[j][i] = sim

    # Union-Find for agglomerative clustering
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for i in range(n):
        for j in range(i + 1, n):
            if sim_matrix[i][j] >= threshold:
                union(i, j)

    # Group by cluster
    clusters = defaultdict(list)
    for i in range(n):
        root = find(i)
        clusters[root].append(i)

    # Build cluster summaries
    result = []
    for root, indices in clusters.items():
        members = [sequences[i] for i in indices]
        # Find representative (longest sequence, or most common)
        members.sort(key=lambda m: m["length"], reverse=True)
        representative = members[0]

        # Count unique dates (cross-session)
        dates = set(m["date"] for m in members if m.get("date"))

        # Count unique workspaces (cross-workspace)
        workspaces = set()
        for m in members:
            if "workspace" in m:
                workspaces.add(m["workspace"])

        result.append({
            "cluster_id": f"cluster_{root}",
            "member_count": len(members),
            "representative_sequence": representative["tool_list"],
            "tool_count": representative["length"],
            "cross_session_count": len(dates),
            "cross_workspace_count": len(workspaces) if workspaces else 1,
            "members": [{
                "date": m.get("date", ""),
                "topic": m.get("topic", ""),
                "tools": m["tools"],
                "tool_list": m["tool_list"],
            } for m in members],
        })

    # Sort by member count (most frequent first)
    result.sort(key=lambda c: c["member_count"], reverse=True)
    return result


def find_patterns(clusters: list, min_frequency: int = MIN_PATTERN_FREQ) -> list:
    """Filter clusters to identify cross-session patterns."""
    patterns = []
    for c in clusters:
        if c["member_count"] >= min_frequency:
            score = _score_pattern(c)
            patterns.append({
                **c,
                "pattern_score": round(score, 3),
                "is_pattern": True,
                "suggestion": _generate_pattern_suggestion(c),
            })
    patterns.sort(key=lambda p: p["pattern_score"], reverse=True)
    return patterns


def _score_pattern(cluster: dict) -> float:
    """Score a cluster for pattern quality (0-1)."""
    freq_score = min(cluster["member_count"] / 10, 1.0) * 0.4
    length_score = min(cluster["tool_count"] / 8, 1.0) * 0.2
    cross_session = min(cluster["cross_session_count"] / 3, 1.0) * 0.3
    cross_ws = min(cluster["cross_workspace_count"] / 2, 1.0) * 0.1
    return freq_score + length_score + cross_session + cross_ws


def _generate_pattern_suggestion(cluster: dict) -> str:
    """Generate a human-readable suggestion from a cluster."""
    rep = cluster["representative_sequence"]
    count = cluster["member_count"]
    sessions = cluster["cross_session_count"]
    ws_count = cluster["cross_workspace_count"]

    parts = [f"工作流 '{rep}' 在 {count} 次会话中出现"]
    if sessions > 1:
        parts.append(f"跨 {sessions} 个不同日期")
    if ws_count > 1:
        parts.append(f"跨 {ws_count} 个工作区")

    parts.append(f"— 建议固化为 Skill")
    return "，".join(parts)


def generate_workflow(pattern: dict) -> dict:
    """Generate a structured workflow description from a pattern."""
    members = pattern.get("members", [])
    if not members:
        return {"steps": [], "description": ""}

    # Use representative member
    rep = members[0]
    tools = rep.get("tools", [])

    # Determine workflow type from tools
    tool_types = set()
    for t in tools:
        if t in ("Read", "Write", "Edit"):
            tool_types.add("文件操作")
        elif t in ("WebFetch", "WebSearch"):
            tool_types.add("网络搜索")
        elif t in ("Bash", "python3", "node", "git"):
            tool_types.add("命令执行")
        elif t in ("Skill", "Agent"):
            tool_types.add("技能/代理")

    workflow_type = "混合操作" if len(tool_types) > 1 else (tool_types.pop() if tool_types else "通用")

    return {
        "tools": tools,
        "workflow_type": workflow_type,
        "step_count": len(tools),
        "topic": rep.get("topic", ""),
        "description": f"{workflow_type}工作流：{' → '.join(tools[:8])}"
                        + ("..." if len(tools) > 8 else ""),
    }


# ── Intent Discovery from Query Signatures ──────────────────

# Skill-to-intent mapping: maps skill names → likely intent name + role
SKILL_INTENT_MAP = {
    "westock-data": ("stock_quick_check", "八大"),
    "neodata-financial-search": ("stock_quick_check", "八大"),
    "投研大脑": ("stock_deep_research", "八大"),
    "deep-research": ("stock_deep_research", "八大"),
    "due-diligence": ("investment_dd", "L"),
    "investment-memo": ("investment_memo", "L"),
    "律合-lexbridge-counsel": ("legal_review", "L"),
    "j-travel-planner": ("travel_plan", "共享"),
    "xiaohongshu": ("travel_plan", "共享"),
    "enhanced-memory": ("memory_review", "八大"),
    "debug": ("code_debug", "八大"),
    "skill-creator": ("skill_create", "八大"),
    "html-report": ("report_gen", "共享"),
    "meeting-transcript": ("meeting_work", "L"),
    "weekly-report": ("meeting_work", "L"),
}

def _db_path() -> Path:
    return DB_PATH


def _extract_keywords_from_text(text: str) -> list:
    """Extract Chinese keywords from text: 2-4 char substrings, skip stop words."""
    import re as _re
    stop = {"的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
            "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
            "没有", "看", "好", "这个", "那个", "什么", "怎么", "如何", "可以",
            "使用", "进行", "通过", "对于", "关于", "已经", "需要", "可能", "应该",
            "呃", "嗯", "啊", "吧", "吗", "呢", "哦", "哈", "嘛", "呀"}
    clean = _re.sub(r'[，。！？、；：""''（）【】《》\s,.!?;:()\[\]{}""'']+', '', text)
    keywords = []
    for w in [2, 3, 4]:
        for i in range(len(clean) - w + 1):
            kw = clean[i:i+w]
            if kw not in stop:
                keywords.append(kw)
    # Also extract English words
    en = _re.findall(r'[a-zA-Z0-9._-]+', text)
    keywords.extend(w.lower() for w in en if len(w) >= 2)
    return list(set(keywords))


def _cluster_keywords(unmatched_queries: list) -> list:
    """Cluster unmatched queries by co-occurring keywords.
    
    Returns list of clusters, each with: keywords, queries, frequency.
    """
    # Build keyword co-occurrence graph
    co_occur = defaultdict(lambda: defaultdict(int))
    query_keywords = []
    
    for q in unmatched_queries:
        kws = q.get("keywords", [])
        if not kws:
            kws = _extract_keywords_from_text(q.get("query", ""))
        query_keywords.append(set(kws))
        for kw in kws:
            for kw2 in kws:
                if kw < kw2:  # count each pair once
                    co_occur[kw][kw2] += 1
    
    # Union-Find on keywords that co-occur ≥ 2 times
    all_keywords = list(co_occur.keys())
    parent = {kw: kw for kw in all_keywords}
    
    def find(kw):
        while parent.get(kw, kw) != kw:
            parent[kw] = parent.get(parent[kw], parent[kw])
            kw = parent[kw]
        return kw
    
    def union(a, b):
        pa, pb = find(a), find(b)
        if pa != pb:
            parent[pa] = pb
    
    for kw, neighbors in co_occur.items():
        for kw2, count in neighbors.items():
            if count >= 2:
                union(kw, kw2)
    
    # Group keywords by cluster
    clusters = defaultdict(set)
    for kw in all_keywords:
        clusters[find(kw)].add(kw)
    
    # For each cluster, find which queries contain its keywords
    result = []
    for root, kws in clusters.items():
        if len(kws) < 2:
            continue
        # Find queries that contain ≥ 2 keywords from this cluster
        matched_queries = []
        for i, qkws in enumerate(query_keywords):
            overlap = len(kws & qkws)
            if overlap >= 2:
                matched_queries.append(unmatched_queries[i])
        
        if matched_queries:
            result.append({
                "keywords": sorted(kws, key=len, reverse=True)[:8],
                "query_count": len(matched_queries),
                "queries": matched_queries[:10],
            })
    
    result.sort(key=lambda c: c["query_count"], reverse=True)
    return result


def _guess_intent_context(keywords: list) -> dict:
    """Guess skills, role, and auto_actions from keywords.
    
    Uses keyword matching against known skill domains.
    """
    kw_str = " ".join(keywords).lower()
    
    # Domain detection
    skills = []
    role = "共享"
    auto_actions = []
    
    domains = [
        (["股票", "行情", "股价", "A股", "港股", "美股", "涨跌", "实时", "kline", "quote"], 
         ["westock-data", "neodata-financial-search"], "八大", ["quote", "latest_finance"]),
        (["研报", "深度", "估值", "财报", "基本面", "技术面", "对比", "产业链", "竞争"],
         ["投研大脑", "deep-research", "westock-data"], "八大", ["generate_research_prompt"]),
        (["尽调", "投资", "BP", "项目", "条款", "SPA", "融资", "deal", "portfolio"],
         ["due-diligence", "investment-memo"], "L", ["load_dd_framework"]),
        (["法律", "合同", "合规", "法务", "条款", "协议", "章程"],
         ["律合-lexbridge-counsel"], "L", ["load_legal_framework"]),
        (["旅行", "攻略", "酒店", "机票", "火车票", "景点", "周末", "目的地", "打卡"],
         ["j-travel-planner", "xiaohongshu", "tc-deeptrip", "flyai"], "共享", ["search_xiaohongshu"]),
        (["WorkBuddy", "OpenClaw", "Agent", "MCP", "Skill", "配置", "守护", "daemon", "模型", "Ollama"],
         ["enhanced-memory", "debug"], "八大", ["check_daemon_health"]),
        (["创建", "自动化", "Skill", "Prompt", "模板", "固化"],
         ["skill-creator", "创世架构师-meta-agent-gen"], "八大", ["load_skill_template"]),
        (["回顾", "上次", "之前", "记忆", "memory", "历史"],
         ["enhanced-memory"], "共享", ["fts5_search"]),
        (["报告", "PPT", "PDF", "图表", "可视化", "数据报告", "路演"],
         ["html-report", "pptx", "pdf"], "共享", ["load_report_template"]),
        (["会议", "转录", "周报", "录音", "汇报", "纪要"],
         ["meeting-transcript", "weekly-report"], "L", ["load_transcript_template"]),
        (["报错", "bug", "调试", "修复", "错误", "exception", "不工作"],
         ["debug", "caveman"], "八大", ["reproduce", "isolate"]),
    ]
    
    for domain_kws, domain_skills, domain_role, domain_actions in domains:
        if any(dk in kw_str for dk in domain_kws):
            skills.extend(domain_skills)
            role = domain_role
            auto_actions.extend(domain_actions)
            break
    
    return {
        "skills": list(set(skills)),
        "role": role,
        "auto_actions": auto_actions,
    }


def _suggest_intent_name(keywords: list) -> str:
    """Auto-suggest an intent name from keyword clusters."""
    # Map known domains to prefixes
    domain_prefix = {
        "股票": "stock", "行情": "stock", "股价": "stock", "A股": "stock",
        "投资": "investment", "尽调": "investment", "BP": "investment",
        "旅行": "travel", "攻略": "travel", "酒店": "travel", "景点": "travel",
        "法律": "legal", "合同": "legal", "合规": "legal",
        "WorkBuddy": "system", "Agent": "system", "MCP": "system",
        "会议": "meeting", "转录": "meeting", "周报": "meeting",
        "报告": "report", "PPT": "report", "图表": "report",
        "Skill": "skill_create",
        "记忆": "memory", "回顾": "memory",
        "报错": "code_debug", "bug": "code_debug",
        "小红书": "social_media", "微信": "social_media",
        "搜索": "search", "查询": "search",
    }
    
    for kw in keywords:
        for domain, prefix in domain_prefix.items():
            if domain in kw:
                # Use the first 2-3 most distinctive keywords to build name
                distinctive = [k for k in keywords[:3] if len(k) >= 2]
                suffix = "_".join(distinctive[:2]).lower() if distinctive else "unknown"
                return f"{prefix}_{suffix}"
    
    # Fallback: use first 2 keywords
    fallback = "_".join(keywords[:2]).lower() if len(keywords) >= 2 else "unknown"
    return f"intent_{fallback}"


def discover_intents(days: int = 14) -> dict:
    """Discover new intent patterns from unmatched query signatures.
    
    Algorithm:
    1. Read unmatched query_signatures from user_model.db
    2. Extract and cluster keywords by co-occurrence
    3. For each cluster, suggest intent name, keywords, context_bundle
    4. Cross-reference with existing intents to avoid duplicates
    
    Returns dict with candidates ready for intent_seed insertion.
    """
    db_path = _db_path()
    if not db_path.exists():
        return {"error": "user_model.db not found", "candidates": [], "unmatched_count": 0}
    
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    
    # Get unmatched queries
    rows = conn.execute(
        """SELECT * FROM query_signatures
           WHERE was_hit = 0 AND timestamp >= ?
           ORDER BY timestamp DESC""",
        (cutoff,)
    ).fetchall()
    
    # Get existing intent names for dedup
    existing = set(r["intent_name"] for r in conn.execute(
        "SELECT intent_name FROM intent_patterns"
    ).fetchall())
    
    conn.close()
    
    if not rows:
        return {"unmatched_count": 0, "keyword_clusters": 0, "candidates": []}
    
    # Parse queries with keywords
    unmatched = []
    for r in rows:
        raw = r["raw_query"] or ""
        kws = r["extracted_keywords"] or ""
        unmatched.append({
            "query": raw,
            "keywords": [k.strip() for k in kws.split(",") if k.strip()],
            "timestamp": r["timestamp"],
        })
    
    # Cluster
    clusters = _cluster_keywords(unmatched)
    
    # Generate candidates
    candidates = []
    for cluster in clusters:
        name = _suggest_intent_name(cluster["keywords"])
        # Skip if similar to existing
        if name in existing:
            continue
        
        ctx = _guess_intent_context(cluster["keywords"])
        
        candidates.append({
            "suggested_name": name,
            "keywords": ", ".join(cluster["keywords"]),
            "query_count": cluster["query_count"],
            "samples": [{"query": q["query"], "ts": q.get("timestamp", "")} 
                       for q in cluster["queries"][:5]],
            "suggested_context": ctx,
            "confidence_seed": round(min(0.6, 0.3 + cluster["query_count"] * 0.05), 2),
        })
    
    return {
        "unmatched_count": len(unmatched),
        "keyword_clusters": len(clusters),
        "candidates": candidates,
    }


# ── CLI ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Sequence Analyzer for WorkBuddy (Phase 3E)"
    )
    parser.add_argument("--extract", action="store_true",
                        help="Extract tool sequences from workspace")
    parser.add_argument("--cluster", action="store_true",
                        help="Cluster sequences and find patterns")
    parser.add_argument("--pattern-report", action="store_true",
                        help="Generate pattern report")
    parser.add_argument("--analyze-all", action="store_true",
                        help="Analyze all workspaces")
    parser.add_argument("--workspace", "-w", type=str, default="",
                        help="Workspace path")
    parser.add_argument("--threshold", type=float, default=0.6,
                        help="Clustering similarity threshold (default: 0.6)")
    parser.add_argument("--min-freq", type=int, default=MIN_PATTERN_FREQ,
                        help=f"Minimum frequency for pattern detection (default: {MIN_PATTERN_FREQ})")
    parser.add_argument("--discover-intents", action="store_true",
                        help="Discover new intent patterns from unmatched query signatures")
    parser.add_argument("--intent-days", type=int, default=14,
                        help="Days to analyze for intent discovery (default: 14)")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")

    args = parser.parse_args()

    if args.discover_intents:
        result = discover_intents(days=args.intent_days)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"🔍 Intent Discovery (last {args.intent_days} days)")
            print(f"   Unmatched queries: {result['unmatched_count']}")
            print(f"   Keyword freq clusters: {len(result['keyword_clusters'])}")
            print(f"   Candidate intents: {len(result['candidates'])}\n")
            for i, c in enumerate(result['candidates'], 1):
                print(f"  [{i}] Suggested: {c['suggested_name']}")
                print(f"      Keywords: {c['keywords']}")
                print(f"      Sample queries: {len(c['samples'])}")
                for sq in c['samples'][:3]:
                    print(f"        - {sq['query'][:80]}")
                print(f"      Suggested context: {json.dumps(c['suggested_context'], ensure_ascii=False)}")
                print()
        return

    if args.analyze_all:
        workspaces = find_all_workspaces()
        all_sequences = []
        for ws in workspaces:
            seqs = extract_sequences(ws)
            ws_name = Path(ws).name
            for s in seqs:
                s["workspace"] = ws_name
            all_sequences.extend(seqs)

        clusters = cluster_sequences(all_sequences, threshold=args.threshold)
        patterns = find_patterns(clusters, min_frequency=args.min_freq)

        if args.json:
            print(json.dumps({
                "workspaces_analyzed": len(workspaces),
                "total_sequences": len(all_sequences),
                "clusters": len(clusters),
                "patterns": len(patterns),
                "pattern_details": patterns,
            }, ensure_ascii=False, indent=2))
        else:
            print(f"📊 Analyzed {len(workspaces)} workspace(s)")
            print(f"   Extracted {len(all_sequences)} tool sequences")
            print(f"   Formed {len(clusters)} clusters")
            print(f"   Found {len(patterns)} cross-session patterns\n")
            for i, p in enumerate(patterns, 1):
                print(f"  🔴 Pattern {i}: {p['representative_sequence'][:100]}")
                print(f"     Frequency: {p['member_count']}x | "
                      f"跨会话: {p['cross_session_count']} | Score: {p['pattern_score']:.2f}")
                print(f"     {p['suggestion'][:120]}")
                print()
        return

    if not args.workspace:
        print("ERROR: --workspace is required unless using --analyze-all", file=sys.stderr)
        sys.exit(1)

    if args.extract:
        sequences = extract_sequences(args.workspace)
        if args.json:
            print(json.dumps(sequences, ensure_ascii=False, indent=2))
        else:
            if not sequences:
                print("📭 No tool sequences found in this workspace.")
            else:
                print(f"🔍 Extracted {len(sequences)} tool sequences:")
                for i, s in enumerate(sequences, 1):
                    print(f"  [{i}] [{s['date']}] {s['tool_list'][:120]}")
        return

    if args.cluster or args.pattern_report:
        sequences = extract_sequences(args.workspace)
        if not sequences:
            print("📭 No sequences to cluster.")
            return

        clusters = cluster_sequences(sequences, threshold=args.threshold)
        patterns = find_patterns(clusters, min_frequency=args.min_freq)

        if args.json:
            print(json.dumps({
                "total_sequences": len(sequences),
                "clusters": clusters,
                "patterns": patterns,
            }, ensure_ascii=False, indent=2))
        else:
            print(f"🔬 Clustering: {len(sequences)} sequences → {len(clusters)} clusters")
            if not patterns:
                print("📭 No cross-session patterns found (increase sessions for better results).")
            else:
                print(f"🔴 Found {len(patterns)} pattern(s):\n")
                for i, p in enumerate(patterns, 1):
                    print(f"  [{i}] {p['representative_sequence'][:100]}")
                    print(f"      Frequency: {p['member_count']}x | Score: {p['pattern_score']:.2f}")
                    workflow = generate_workflow(p)
                    print(f"      Type: {workflow['workflow_type']} | Steps: {workflow['step_count']}")
                    print(f"      {p['suggestion'][:120]}")
                    print()
        return

    # Default: extract
    sequences = extract_sequences(args.workspace)
    print(f"🔍 {len(sequences)} tool sequences in {Path(args.workspace).name}")
    print(f"   Use --cluster to find patterns, --analyze-all for cross-workspace analysis.")


if __name__ == "__main__":
    main()
