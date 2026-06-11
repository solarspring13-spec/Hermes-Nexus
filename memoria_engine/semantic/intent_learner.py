#!/usr/bin/env python3
# -*- RUNTIME: {MEMORIA_HOME} -*-
# numpy C-ext 在 managed 3.13.12 存在 macOS Team ID 签名冲突，专用 venv (3.11) 解决
# 调用方式: {MEMORIA_HOME} intent_learner.py [args]
"""
Intent Learner — Hermes Intent Awareness Engine
=================================================
Core intent extraction, matching, and feedback learning engine.

Extracts intent fingerprints from user queries, matches them against
known patterns in user_model.db, and provides preload bundles for
context-aware Agent responses. Includes a reinforcement feedback loop
that learns from hit/miss outcomes.

Usage:
    # Match intent from query
    python3 intent_learner.py --query "查一下宁德时代行情" --json

    # Preload mode: returns context_bundle for Agent
    python3 intent_learner.py --query "... " --preload --json

    # Feedback: record hit/miss to refine patterns
    python3 intent_learner.py --feedback --intent intent_id --status hit --query "..."

    # Analyze recent query signatures for pattern discovery
    python3 intent_learner.py --analyze --days 7 --json
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

# ── Lazy imports for semantic mode ──
_semantic_available = None


def _check_semantic_available() -> bool:
    """Check if semantic embedding dependencies are installed."""
    global _semantic_available
    if _semantic_available is not None:
        return _semantic_available
    try:
        from ..semantic.embeddings import get_embedder
        _semantic_available = True
    except ImportError:
        _semantic_available = False
    return _semantic_available


# ── Constants ────────────────────────────────────────────────

from ..constants import WORKBUDDY_DIR, DB_PATH
from ..models.user_model import _get_db  # uses same DB; ensures schema exists

CN_STOP_WORDS = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "他", "她", "它", "们", "那", "些",
    "什么", "怎么", "如何", "可以", "这个", "那个", "还是", "或者", "以及",
    "使用", "进行", "通过", "对于", "关于", "已经", "需要", "可能", "应该",
    "呃", "嗯", "啊", "吧", "吗", "呢", "哦", "哈", "嘛", "呀",
}

EN_STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "and", "or", "but", "not", "no", "yes", "if", "then", "this", "that",
}

# Intent matching thresholds (v3.1 Trinity-Refactor fix)
# HIGH: 0.35 (3-4 keyword hits + medium base_conf → qualifies; real matches should reach this)
# MEDIUM: 0.08 (at least 2 keyword hits + decent base_conf; lower bound floor)
HIGH_CONFIDENCE = 0.35
MEDIUM_CONFIDENCE = 0.08
LOW_CONFIDENCE = 0.0

# Feedback adjustment
HIT_BONUS = 0.05
MISS_PENALTY = 0.08


# ── Chinese Tokenization ─────────────────────────────────────

def _tokenize_cn(text: str) -> list:
    """Simple Chinese tokenization: extract 2-4 char bigrams/trigrams.
    
    Falls back to character-level for single-character keywords.
    """
    # Remove punctuation
    text = re.sub(r'[，。！？、；：""''（）【】《》\s,.!?;:()\[\]{}""'']+', ' ', text)
    
    tokens = []
    # Extract 2-gram and 3-gram
    clean = re.sub(r'\s+', '', text)
    for i in range(len(clean) - 1):
        bigram = clean[i:i+2]
        if bigram not in CN_STOP_WORDS:
            tokens.append(bigram)
    for i in range(len(clean) - 2):
        trigram = clean[i:i+3]
        tokens.append(trigram)
    
    # Also extract English words
    en_words = re.findall(r'[a-zA-Z0-9._-]+', text)
    tokens.extend(w.lower() for w in en_words)
    
    # Also check for known multi-char keywords in the original text
    # by sliding window
    for window in [2, 3, 4]:
        for i in range(len(clean) - window + 1):
            tokens.append(clean[i:i+window])
    
    return list(set(tokens))  # deduplicate


def _tokenize_pattern(keywords_str: str) -> set:
    """Tokenize a comma-separated pattern keywords string."""
    return set(k.strip() for k in keywords_str.split(",") if k.strip())


# ── Matching ─────────────────────────────────────────────────

def _keyword_hit_ratio(query: str, pattern_keywords_str: str) -> float:
    """Calculate keyword hit ratio: what fraction of pattern keywords 
    appear as substrings in the query.
    
    This is more effective for Chinese than Jaccard because pattern keywords
    are word-level (e.g., "查一下", "行情") while character bigram tokenization
    fails to match them.
    
    Each keyword hit is weighted by keyword length (longer keywords = stronger signal).
    Total weight is capped at MAX_TOTAL_WEIGHT to prevent dilution when patterns
    have many keywords (denominator inflation).
    """
    keywords = [k.strip() for k in pattern_keywords_str.split(",") if k.strip()]
    if not keywords:
        return 0.0
    
    query_lower = query.lower()
    MAX_TOTAL_WEIGHT = 16  # cap denominator to prevent dilution
    
    total_weight = 0
    hit_weight = 0
    hit_count = 0
    
    for kw in keywords:
        weight = min(len(kw), 4)  # cap at 4 to avoid over-weighting long phrases
        total_weight += weight
        if kw.lower() in query_lower:
            hit_weight += weight
            hit_count += 1
    
    if total_weight == 0:
        return 0.0
    
    # Require at least 2 keyword matches to reduce false positives
    if hit_count < 2:
        return 0.0
    
    # Cap denominator to prevent dilution from large keyword sets
    effective_total = min(total_weight, MAX_TOTAL_WEIGHT)
    
    return min(hit_weight / effective_total, 1.0)


# ── B+5a Post-Match Lexical Override Rules ──────────────────
# Added per CTO B+5-production authorization (2026-06-04).
# These rules fix known FP patterns that LanceDB semantic matching cannot
# distinguish due to vector space overlap. Rules are purely lexical — no
# LanceDB access, no DB mutation.
#
# Rule precedence: Rule 1 > 2 > 3 > 4 > 5 > 6 > 7 (order-sensitive; first match wins).

def _lookup_intent_by_name(intent_name: str) -> dict:
    """Look up intent metadata by name from the DB.

    Used by _post_match_rules() to fetch context_bundle and intent_id
    when a lexical rule overrides the matched intent to one that may
    not be in the candidate list.

    Returns empty dict if intent not found or DB unavailable.
    """
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM intent_patterns WHERE intent_name = ?",
            (intent_name,)
        ).fetchone()
        conn.close()
        if row:
            return {
                "intent_name": row["intent_name"],
                "intent_id": row["id"],
                "base_confidence": row["confidence"],
                "context_bundle": json.loads(row["context_bundle"]) if row["context_bundle"] else {},
            }
    except Exception:
        pass
    return {}


def _post_match_rules(query: str, result: dict) -> dict:
    """B+5 post-match lexical override rules.

    Applies 7 ordered lexical rules to correct known FP/FN patterns where
    LanceDB semantic matching produces wrong intent due to vector space
    overlap. Each rule checks explicit lexical signals in the query and
    overrides/re-ranks the matched intent accordingly.

    Args:
        query: Original user query string.
        result: Result dict from match_intent() / _hybrid_match_intent().

    Returns:
        Modified result dict. If a rule fires, intent_name, intent_id,
        confidence, context_bundle, and match_level may be overridden.
        A ``post_rule`` key is added to indicate which rule fired.
        When no rule fires, the original result is returned unchanged.

    Guardrails:
        - Does NOT access LanceDB or modify any database.
        - Does NOT add new intent categories (general_qa / glossary_lookup).
        - Does NOT implement no-match threshold or confidence-based rejection.
        - Rule 1 anti-collision prevents skill_create from absorbing
          system_architect queries with explicit architecture signals.
    """
    query_lower = query.lower()

    # ── Rule 1: skill_create lexical override ──────────────────
    # Pattern: creation verbs + skill/agent nouns → skill_create.
    # Anti-collision: presence of system_architect-specific terms
    # (architecture, interface, module boundary, blueprint, daemon, MCP)
    # prevents this override — "创建MCP架构" stays system_architect.
    #
    # Fixes FP: "帮我创建一个新的Skill" → skill_create (was system_architect)
    # Fixes FP: "生成一个新的能力组件" → skill_create (was system_architect)
    # Fixes FP: "这个工作流帮我固化成Skill" → skill_create (was system_architect)

    sc_verb_set = {
        "创建", "生成", "固化", "新建", "写一个", "做一个", "做个",
        "搞个", "弄个"
    }
    sc_noun_set = {
        "skill", "技能", "能力组件", "agent", "prompt", "工作流",
        "固化成", "做成skill", "变成skill", "skill.md",
        "自动化", "组件", "工具"
    }
    # system_architect anti-collision: these terms signal genuine architecture
    # work, NOT skill creation — do NOT override to skill_create
    sa_anti_set = {
        "架构", "接口", "模块边界", "系统设计", "蓝图", "白皮书",
        "守护进程", "解耦", "mcp架构", "组件化", "core/", "agents/"
    }

    has_sc_verb = any(v in query_lower for v in sc_verb_set)
    has_sc_noun = any(n in query_lower for n in sc_noun_set)
    has_sa_anti = any(s in query_lower for s in sa_anti_set)

    if has_sc_verb and has_sc_noun and not has_sa_anti:
        info = _lookup_intent_by_name("skill_create")
        if info:
            result["intent_name"] = info["intent_name"]
            result["intent_id"] = info["intent_id"]
            result["confidence"] = 0.85
            result["context_bundle"] = info["context_bundle"]
            result["match_level"] = "high"
            result["post_rule"] = "rule_1_skill_create"
            return result

    # ── Rule 2: memory_review lexical override ─────────────────
    # Pattern: retrospective time words + target reference → memory_review.
    # These are retrieval/recall queries that LanceDB routes to system_architect
    # because of shared vocabulary ("架构", "Hermes", "项目").
    #
    # Fixes FP: "上次我们讨论的架构变更是什么" → memory_review (was system_architect)
    # Fixes FP: "Hermes里有没有记录那个项目" → memory_review (was system_architect)

    mr_time_set = {
        "上次", "之前", "还记得", "记录", "回忆", "有没有记录",
        "记不记得", "之前聊", "之前说", "之前讨论", "提到过", "说过"
    }
    mr_target_set = {
        "讨论", "项目", "架构变更", "hermes", "那个项目", "那个任务",
        "那个", "那件事", "任务", "工作"
    }

    has_mr_time = any(t in query_lower for t in mr_time_set)
    has_mr_target = any(t in query_lower for t in mr_target_set)

    if has_mr_time and has_mr_target:
        info = _lookup_intent_by_name("memory_review")
        if info:
            result["intent_name"] = info["intent_name"]
            result["intent_id"] = info["intent_id"]
            result["confidence"] = 0.80
            result["context_bundle"] = info["context_bundle"]
            result["match_level"] = "high"
            result["post_rule"] = "rule_2_memory_review"
            return result

    # ── Rule 3: legal_review vs investment_dd tiebreaker ──────
    # In L-role context (CVC/strategic investment), SPA protocol review
    # is part of due diligence work, not standalone legal affairs.
    #
    # Direction A (legal → investment): query has investment signals
    #   (SPA/SHA/投资协议/尽调/DD/交割/估值/融资/对赌/deal)
    #   AND lacks pure legal keywords → re-rank to investment_dd.
    #
    # Direction B (investment → legal): query has ONLY pure legal
    #   keywords (违约/赔偿/管辖/仲裁/不可抗力/GDPR)
    #   AND lacks investment signals → re-rank to legal_review.
    #
    # Fixes FP: "审查这份SPA协议的关键条款" → investment_dd (was legal_review)

    best_intent = result.get("intent_name")

    invest_signal_set = {
        "spa", "sha", "投资协议", "尽调", "dd", "交割", "估值",
        "融资", "对赌", "回购", "优先权", "反稀释", "deal",
        "尽调报告", "bp", "term sheet", "termsheet"
    }
    pure_legal_set = {
        "违约", "赔偿", "管辖", "仲裁", "不可抗力", "gdpr",
        "数据隐私", "知识产权", "专利", "商标"
    }

    has_invest = any(t in query_lower for t in invest_signal_set)
    has_pure_legal = any(t in query_lower for t in pure_legal_set)

    # Direction A: legal_review → investment_dd
    if best_intent == "legal_review" and has_invest and not has_pure_legal:
        info = _lookup_intent_by_name("investment_dd")
        if info:
            result["intent_name"] = info["intent_name"]
            result["intent_id"] = info["intent_id"]
            result["confidence"] = max(result.get("confidence", 0.0), 0.75)
            result["context_bundle"] = info["context_bundle"]
            result["match_level"] = "high"
            result["post_rule"] = "rule_3_investment_dd_tiebreaker"
            return result

    # Direction B: investment_dd → legal_review
    # Enhanced: also override when query has strong legal focus terms
    # even if SPA/SHA/document-type terms are present — when the query's
    # primary intent is legal review (审查/审阅/法律风险), not investment
    # decision-making (尽调/DD/估值/交割).
    legal_focus_set = {
        "法律风险", "合规风险", "法律合规", "法律审查",
        "法律意见", "法务"
    }
    has_legal_focus = any(t in query_lower for t in legal_focus_set)
    invest_decision_set = {
        "尽调", "dd", "估值", "交割", "融资轮次", "deal",
        "尽调报告", "投资决策", "投委会"
    }
    has_invest_decision = any(t in query_lower for t in invest_decision_set)

    if best_intent == "investment_dd" and (
        (has_pure_legal and not has_invest) or
        (has_legal_focus and not has_invest_decision)
    ):
        info = _lookup_intent_by_name("legal_review")
        if info:
            result["intent_name"] = info["intent_name"]
            result["intent_id"] = info["intent_id"]
            result["confidence"] = max(result.get("confidence", 0.0), 0.75)
            result["context_bundle"] = info["context_bundle"]
            result["match_level"] = "high"
            result["post_rule"] = "rule_3_legal_review_tiebreaker"
            return result

    # ── Rule 4: code_debug positive boundary constraint ───────
    # code_debug MUST have explicit debugging/error signal words.
    # Without these signals, even if LanceDB returns code_debug as the
    # best match, downgrade to no-match. This prevents absorption of
    # generic knowledge queries like "什么是机器学习"/"Token是什么意思".
    #
    # Also: if best match is NOT code_debug but the query contains
    # strong code-fix signals, override to code_debug.
    #
    # Fixes FP (boundary): "Token是什么意思" → no_match
    # Fixes FP (boundary): "什么是机器学习" → no_match

    debug_signal_set = {
        "报错", "错误", "bug", "调试", "修复", "配置出错", "运行失败",
        "栈", "traceback", "崩溃", "异常", "debug", "修一下",
        "不好使", "不work", "不工作", "出错", "挂了", "失败",
        "怎么配置", "配置"
    }

    has_debug_signal = any(d in query_lower for d in debug_signal_set)

    if best_intent == "code_debug" and not has_debug_signal:
        # No debugging signal → this match is likely spurious;
        # the query is probably a generic knowledge or concept question.
        result["intent_name"] = None
        result["intent_id"] = None
        result["confidence"] = 0.0
        result["match_level"] = "low"
        result["post_rule"] = "rule_4_code_debug_boundary_downgrade"
        return result

    if best_intent and best_intent != "code_debug" and has_debug_signal:
        # Query has debugging signals but was routed elsewhere.
        # Check for strong code-fix context before overriding.
        strong_debug = {"报错", "bug", "traceback", "崩溃", "调试", "debug",
                        "修复", "修一下", "不好使", "不work", "不工作"}
        has_strong = any(d in query_lower for d in strong_debug)
        if has_strong:
            info = _lookup_intent_by_name("code_debug")
            if info:
                result["intent_name"] = info["intent_name"]
                result["intent_id"] = info["intent_id"]
                result["confidence"] = 0.82
                result["context_bundle"] = info["context_bundle"]
                result["match_level"] = "high"
                result["post_rule"] = "rule_4_code_debug_override"
                return result

    # ── Rule 5: system_architect positive boundary constraint ──
    # Mirror of Rule 4: system_architect MUST have explicit
    # architectural/design signal words. Without these signals,
    # the match is likely a LanceDB false positive on generic
    # knowledge/concept queries (e.g., "什么是机器学习", "Token是什么").
    #
    # This rule complements Rule 1's anti-collision set: Rule 1 prevents
    # skill_create queries from being absorbed by system_architect;
    # Rule 5 prevents non-architectural queries from matching
    # system_architect at all.
    #
    # Fixes FP: "什么是机器学习" → no_match (was system_architect)
    # Fixes FP: "Token是什么意思" → no_match (was system_architect)
    # Fixes FP: "怎么优化SQL查询性能" → no_match (was system_architect)
    # Fixes FP: "什么是大语言模型" → no_match (was system_architect)

    sa_positive_set = {
        "架构", "设计", "系统设计", "蓝图", "白皮书",
        "解耦", "守护进程", "模块边界", "接口",
        "agent系统", "agent架构", "mcp架构", "skill架构",
        "workbuddy架构", "hermes架构", "core/", "agents/"
    }

    has_sa_positive = any(s in query_lower for s in sa_positive_set)

    if best_intent == "system_architect" and not has_sa_positive:
        result["intent_name"] = None
        result["intent_id"] = None
        result["confidence"] = 0.0
        result["match_level"] = "low"
        result["post_rule"] = "rule_5_system_architect_boundary_downgrade"
        return result

    # ── Rule 6: memory_review positive boundary constraint ──
    # Mirror of Rule 4/5: memory_review MUST have explicit
    # recollection/history/record signal words. Without these signals,
    # the match is likely a LanceDB false positive on casual small-talk
    # (e.g., "今天天气怎么样", "帮我写一首诗").
    #
    # Fixes FP: "今天天气怎么样" → no_match (was memory_review)
    # Fixes FP: "帮我写一首诗" → no_match (was memory_review)

    mr_positive_set = {
        "上次", "之前", "还记得", "回忆", "记录", "讨论过",
        "我们聊过", "hermes里有没有记录", "以前", "历史",
    }

    has_mr_positive = any(s in query_lower for s in mr_positive_set)

    if best_intent == "memory_review" and not has_mr_positive:
        result["intent_name"] = None
        result["intent_id"] = None
        result["confidence"] = 0.0
        result["match_level"] = "low"
        result["post_rule"] = "rule_6_memory_review_boundary_downgrade"
        return result

    # ── Rule 7: travel_plan city route anchor ──
    # City-to-city route queries ("A到B怎么走") should match travel_plan,
    # even if the core engine returns None. This is a safety-net override
    # for the pre-existing core engine limitation that misses these patterns.
    #
    # Fixes FN: "北京到上海怎么走" → travel_plan (was no_match)

    import re as _re_route
    route_pattern = _re_route.compile(r'.+到.+怎么走|.+到.+路线|.+去.+怎么走')

    has_route_pattern = bool(route_pattern.search(query_lower))
    # Anti-collision: don't override if query has strong code/debug/system signals
    code_debug_system_keywords = {
        "代码", "bug", "报错", "异常", "debug", "调试",
        "架构", "系统设计", "模块", "接口设计"
    }
    has_cds_keywords = any(k in query_lower for k in code_debug_system_keywords)

    if best_intent is None and has_route_pattern and not has_cds_keywords:
        info = _lookup_intent_by_name("travel_plan")
        if info:
            result["intent_name"] = "travel_plan"
            result["intent_id"] = info["intent_id"]
            result["confidence"] = 0.85
            result["match_level"] = "high"
            result["post_rule"] = "rule_7_travel_route_anchor"
            return result

    # No rule fired — return original result unchanged
    return result


def _semantic_similarity(query: str, pattern_keywords_str: str) -> float:
    """Calculate semantic similarity between query and intent pattern using BGE-M3.

    Encodes both the user query and the pattern's keyword description
    (comma-separated keywords joined into a pseudo-sentence), then computes
    cosine similarity between the two vectors.

    This captures semantic relatedness that keyword substring matching misses.
    For example: "帮我分析特斯拉财报" semantically matches "股票深度研究"
    even though no keywords overlap.

    Args:
        query: User's natural language query.
        pattern_keywords_str: Comma-separated intent pattern keywords.

    Returns:
        Cosine similarity in [0, 1] (negative values clipped to 0).
        Returns 0.0 if semantic dependencies are unavailable.
    """
    if not _check_semantic_available():
        return 0.0

    keywords = [k.strip() for k in pattern_keywords_str.split(",") if k.strip()]
    if not keywords:
        return 0.0

    # Build pattern description from keywords
    pattern_text = " ".join(keywords)

    try:
        from ..semantic.embeddings import get_embedder
        embedder = get_embedder()

        query_vec = embedder.encode(query)
        pattern_vec = embedder.encode(pattern_text)

        sim = embedder.cosine_similarity(query_vec, pattern_vec)
        # Cosine similarity can be slightly negative with BGE-M3; clip to [0,1]
        return max(0.0, min(sim, 1.0))
    except Exception:
        return 0.0


def _lancedb_semantic_match(query: str, top_k: int = 3) -> list:
    """Fast LanceDB semantic search using pre-computed BGE-M3 intent embeddings.

    Only encodes the query vector (BGE-M3 loaded once, SHA256-cached).
    Intent vectors are pre-computed in LanceDB — no re-encoding needed.
    This is the Phase 2 hybrid fallback: keyword match confidence < 0.15 → LanceDB.

    Returns:
        list of dicts with intent_name, intent_id, similarity, base_confidence,
        context_bundle, keywords_text. Empty list if LanceDB not built.
    """
    import lancedb

    lance_table_path = os.path.join(WORKBUDDY_DIR, "lancedb", "intent_embeddings.lance")
    if not os.path.exists(lance_table_path):
        return []

    try:
        db = lancedb.connect(os.path.dirname(lance_table_path))
        table = db.open_table("intent_embeddings")
    except Exception:
        return []

    if not _check_semantic_available():
        return []

    try:
        from ..semantic.embeddings import get_embedder
        embedder = get_embedder()
        query_vec = embedder.encode(query)
    except Exception:
        return []

    if np.linalg.norm(query_vec) == 0:
        return []

    try:
        results = table.search(query_vec.tolist()).metric("cosine").limit(top_k).to_list()
    except Exception:
        return []

    matches = []
    for r in results:
        distance = r.get("_distance", 0.0)
        similarity = max(0.0, 1.0 - distance / 2.0)
        ctx = {}
        try:
            ctx = json.loads(r.get("context_bundle", "{}"))
        except (json.JSONDecodeError, TypeError):
            pass

        matches.append({
            "intent_name": r.get("intent_name"),
            "intent_id": r.get("intent_id"),
            "similarity": round(similarity, 4),
            "base_confidence": r.get("base_confidence", 0.5),
            "context_bundle": ctx,
            "keywords_text": r.get("keywords_text", ""),
        })

    return matches


def _hybrid_match_intent(query: str, min_confidence: float = MEDIUM_CONFIDENCE) -> dict:
    """Hybrid intent matching: keyword-first, LanceDB fallback.

    Phase 2 implementation:
    1. Run keyword matching (<30ms)
    2. If best confidence >= LOW_CONFIDENCE (0.0), return keyword result
    3. If keyword fails (no candidates or best conf < LOW_CONFIDENCE),
       fall back to LanceDB semantic search (first call ~10s model load,
       subsequent calls ~50ms via SHA256 cache)
    4. LanceDB result must exceed MEDIUM_CONFIDENCE to qualify

    This ensures:
    - 80%+ cases: keyword match (<30ms) — fast path
    - <20% cases: LanceDB semantic fallback — better matching for edge cases
    """
    # Stage 1: Keyword matching
    kw_result = match_intent(query, min_confidence=LOW_CONFIDENCE, mode="keyword")

    # If keyword gave a real match (any confidence > 0), use it
    if kw_result["match_level"] != "low":
        kw_result["mode"] = "hybrid"
        kw_result["fallback_used"] = False
        hybrid_result = kw_result
    else:
        # Stage 2: LanceDB semantic fallback
        lance_matches = _lancedb_semantic_match(query, top_k=3)
        if not lance_matches:
            # No LanceDB index available, return keyword result as-is
            kw_result["mode"] = "hybrid"
            kw_result["fallback_used"] = False
            kw_result["fallback_reason"] = "lancedb_unavailable"
            hybrid_result = kw_result
        else:
            # Convert LanceDB matches to candidate format
            candidates = []
            for m in lance_matches:
                sim = m["similarity"]
                base_conf = m.get("base_confidence", 0.5)
                raw_score = sim * base_conf
                candidates.append({
                    "intent_name": m["intent_name"],
                    "id": m["intent_id"],
                    "hit_ratio": round(sim, 4),
                    "raw_score": round(raw_score, 4),
                    "base_confidence": base_conf,
                    "hit_count": 0,
                    "context_bundle": m["context_bundle"],
                })

            candidates.sort(key=lambda x: x["raw_score"], reverse=True)

            if not candidates or candidates[0]["raw_score"] < min_confidence:
                hybrid_result = {
                    "intent_name": None,
                    "confidence": 0.0,
                    "context_bundle": {},
                    "match_level": "low",
                    "candidates": candidates[:3] + kw_result.get("candidates", [])[:3],
                    "mode": "hybrid",
                    "fallback_used": True,
                    "fallback_reason": "confidence_too_low" if candidates else "no_matches",
                }
            else:
                best = candidates[0]
                if best["raw_score"] >= HIGH_CONFIDENCE:
                    level = "high"
                else:
                    level = "medium"

                hybrid_result = {
                    "intent_name": best["intent_name"],
                    "intent_id": best["id"],
                    "confidence": best["raw_score"],
                    "context_bundle": best["context_bundle"],
                    "match_level": level,
                    "candidates": candidates[:3],
                    "mode": "hybrid",
                    "fallback_used": True,
                }

    # B+5: Apply post-match lexical override rules
    hybrid_result = _post_match_rules(query, hybrid_result)
    return hybrid_result


def _recency_factor(last_matched: str) -> float:
    """Calculate recency factor for intent confidence adjustment."""
    if not last_matched:
        return 1.0
    try:
        dt = datetime.fromisoformat(last_matched)
        days_ago = (datetime.now() - dt).days
    except (ValueError, TypeError):
        return 0.5
    if days_ago <= 7:
        return 1.0
    elif days_ago <= 30:
        return 0.7
    else:
        return 0.4


def match_intent(query: str, min_confidence: float = MEDIUM_CONFIDENCE,
                mode: str = "keyword") -> dict:
    """Match a user query against intent_patterns.

    Three matching modes:
    - "keyword" (default): _keyword_hit_ratio() — substring matching (<30ms)
    - "semantic": _semantic_similarity() — BGE-M3 cosine similarity (has model load cost)
    - "hybrid": keyword-first, LanceDB semantic fallback when keyword conf < 0.15
      (Phase 2: keyword handles 80%+ cases instantly, LanceDB covers edge cases)

    When mode="semantic", falls back to "keyword" if semantic dependencies
    are unavailable.

    Algorithm (keyword/semantic):
    1. For each intent, compute match score using selected mode
    2. Score = hit_ratio * base_confidence * (1 + history_bonus)
    3. Adjust by recency factor

    Returns:
        dict with intent_name, confidence, context_bundle, match_level
        match_level: "high" (>0.65), "medium" (0.3-0.65), "low" (<0.3)
    """
    # Phase 2: hybrid mode delegates to _hybrid_match_intent
    # (which already applies _post_match_rules internally)
    if mode == "hybrid":
        return _hybrid_match_intent(query, min_confidence=min_confidence)

    conn = _get_db()

    rows = conn.execute(
        "SELECT * FROM intent_patterns ORDER BY confidence DESC"
    ).fetchall()
    conn.close()

    if not rows:
        match_result = {
            "intent_name": None,
            "confidence": 0.0,
            "context_bundle": {},
            "match_level": "low",
            "candidates": [],
            "mode": mode,
        }
        match_result = _post_match_rules(query, match_result)
        return match_result

    # Determine matcher function
    use_semantic = (mode == "semantic" and _check_semantic_available())
    matcher = _semantic_similarity if use_semantic else _keyword_hit_ratio

    candidates = []
    for row in rows:
        hit_ratio = matcher(query, row["pattern_keywords"])

        if hit_ratio == 0:
            continue

        # History bonus: each hit adds 0.03 to the multiplier (cap at 1.3x)
        history_bonus = min(row["hit_count"] * 0.03, 0.30)
        recency = _recency_factor(row["last_matched"])

        # Final score: hit_ratio weighted by base_confidence and history
        raw_score = hit_ratio * row["confidence"] * (1.0 + history_bonus) * recency

        candidates.append({
            "intent_name": row["intent_name"],
            "id": row["id"],
            "hit_ratio": round(hit_ratio, 4),
            "raw_score": round(raw_score, 4),
            "base_confidence": row["confidence"],
            "hit_count": row["hit_count"],
            "context_bundle": json.loads(row["context_bundle"]) if row["context_bundle"] else {},
        })

    candidates.sort(key=lambda x: x["raw_score"], reverse=True)

    if not candidates or candidates[0]["raw_score"] < min_confidence:
        match_result = {
            "intent_name": None,
            "confidence": 0.0,
            "context_bundle": {},
            "match_level": "low",
            "candidates": candidates[:3],
            "mode": mode,
        }
    else:
        best = candidates[0]
        if best["raw_score"] >= HIGH_CONFIDENCE:
            level = "high"
        else:
            level = "medium"

        match_result = {
            "intent_name": best["intent_name"],
            "intent_id": best["id"],
            "confidence": best["raw_score"],
            "context_bundle": best["context_bundle"],
            "match_level": level,
            "candidates": candidates[:3],
            "mode": mode,
        }

    # B+5: Apply post-match lexical override rules
    match_result = _post_match_rules(query, match_result)
    return match_result


# ── Preload ───────────────────────────────────────────────────

def get_preload_bundle(query: str, mode: str = "hybrid") -> dict:
    """Get a preload bundle for Agent context injection.
    
    Returns skills to load, memory sections to reference, and role to set.
    """
    result = match_intent(query, mode=mode)
    
    if result["match_level"] == "low":
        return {
            "preload": False,
            "reason": "confidence_too_low",
            "intent": None,
            "intent_id": None,
            "skills": [],
            "memory_sections": [],
            "role": None,
            "auto_actions": [],
        }
    
    bundle = result["context_bundle"]
    return {
        "preload": True,
        "intent": result["intent_name"],
        "intent_id": result.get("intent_id"),
        "confidence": result["confidence"],
        "match_level": result["match_level"],
        "mode": result.get("mode", mode),
        "fallback_used": result.get("fallback_used", False),
        "skills": bundle.get("skills", []),
        "memory_sections": bundle.get("memory_sections", []),
        "role": bundle.get("role"),
        "auto_actions": bundle.get("auto_actions", []),
    }


# ── Feedback ─────────────────────────────────────────────────

def record_feedback(intent_id: str, status: str, query: str = "",
                    session_id: str = "") -> dict:
    """Record hit/miss feedback and update intent confidence.
    
    Args:
        intent_id: The matched intent ID (or "none" if no match)
        status: "hit" or "miss"
        query: Original user query for signature recording
        session_id: Session identifier
    
    Returns:
        dict with updated intent state
    """
    conn = _get_db()
    now = datetime.now().isoformat()
    
    # Record query signature
    sig_id = str(uuid.uuid4())[:8]
    keywords = ",".join(_tokenize_cn(query)) if query else ""
    conn.execute(
        """INSERT INTO query_signatures
           (id, raw_query, extracted_keywords, matched_intent, confidence,
            was_hit, session_id, timestamp)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (sig_id, query[:500], keywords[:500], intent_id,
         0.0, 1 if status == "hit" else 0, session_id, now)
    )
    
    # Update intent pattern
    if intent_id and intent_id != "none":
        # Auto-prefix: accept both "stock_quick_check" and "intent_stock_quick_check"
        lookup_ids = [intent_id]
        if not intent_id.startswith("intent_"):
            lookup_ids.append(f"intent_{intent_id}")
        
        existing = None
        matched_id = intent_id
        for lid in lookup_ids:
            existing = conn.execute(
                "SELECT * FROM intent_patterns WHERE id = ?", (lid,)
            ).fetchone()
            if existing:
                matched_id = lid
                break
        
        if existing:
            new_hits = existing["hit_count"] + (1 if status == "hit" else 0)
            new_misses = existing["miss_count"] + (1 if status == "miss" else 0)
            total = new_hits + new_misses
            
            # Update confidence: weighted average with recency
            if status == "hit":
                new_conf = min(existing["confidence"] + HIT_BONUS, 1.0)
            else:
                new_conf = max(existing["confidence"] - MISS_PENALTY, 0.1)
            
            conn.execute(
                """UPDATE intent_patterns
                   SET hit_count = ?, miss_count = ?, confidence = ?,
                       last_matched = ?, updated_at = ?
                   WHERE id = ?""",
                (new_hits, new_misses, round(new_conf, 4), now, now, matched_id)
            )
            
            result = {
                "intent_id": matched_id,
                "intent_name": existing["intent_name"],
                "old_confidence": existing["confidence"],
                "new_confidence": round(new_conf, 4),
                "hits": new_hits,
                "misses": new_misses,
                "total": total,
            }
        else:
            result = {"error": "intent_not_found", "intent_id": intent_id}
    else:
        result = {"intent_id": None, "note": "no intent to update"}
    
    conn.commit()
    conn.close()
    return result


# ── Analysis ──────────────────────────────────────────────────

def analyze_signatures(days: int = 7) -> dict:
    """Analyze recent query signatures for pattern discovery.
    
    Returns clusters of unmatched queries that may indicate new intents.
    """
    conn = _get_db()
    
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    
    rows = conn.execute(
        """SELECT * FROM query_signatures
           WHERE timestamp >= ?
           ORDER BY timestamp DESC""",
        (cutoff,)
    ).fetchall()
    conn.close()
    
    hits = sum(1 for r in rows if r["was_hit"])
    misses = sum(1 for r in rows if not r["was_hit"])
    total = len(rows)
    
    # Group unmatched queries by extracted keywords for clustering
    unmatched = [r for r in rows if not r["was_hit"] and r["extracted_keywords"]]
    
    keyword_freq = {}
    for r in unmatched:
        for kw in r["extracted_keywords"].split(","):
            kw = kw.strip()
            if kw and len(kw) >= 2:
                keyword_freq[kw] = keyword_freq.get(kw, 0) + 1
    
    # Find clusters of frequently co-occurring keywords
    clusters = []
    freq_keywords = {k: v for k, v in keyword_freq.items() if v >= 2}
    
    return {
        "period_days": days,
        "total_signatures": total,
        "hit_rate": round(hits / total, 3) if total else 0,
        "miss_rate": round(misses / total, 3) if total else 0,
        "unmatched_queries": len(unmatched),
        "frequent_unmatched_keywords": dict(
            sorted(freq_keywords.items(), key=lambda x: x[1], reverse=True)[:10]
        ),
        "total_intents": 0,  # populated by caller if needed
    }


# ── Main CLI ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Intent Learner — Hermes Intent Awareness Engine"
    )
    parser.add_argument("--query", "-q", type=str,
                        help="User query to match against intent patterns")
    parser.add_argument("--preload", action="store_true",
                        help="Return preload bundle (skills + memory + role)")
    parser.add_argument("--feedback", action="store_true",
                        help="Record hit/miss feedback")
    parser.add_argument("--intent", type=str,
                        help="Intent ID for feedback (use 'none' for no match)")
    parser.add_argument("--status", type=str, choices=["hit", "miss"],
                        help="Hit or miss status")
    parser.add_argument("--analyze", action="store_true",
                        help="Analyze recent query signatures")
    parser.add_argument("--days", type=int, default=7,
                        help="Days to analyze (default: 7)")
    parser.add_argument("--session-id", type=str, default="",
                        help="Session identifier")
    parser.add_argument("--mode", type=str, default="keyword",
                        choices=["keyword", "semantic", "hybrid"],
                        help="Matching mode: keyword (substring), semantic (BGE-M3 cosine), "
                             "hybrid (keyword + LanceDB fallback)")
    parser.add_argument("--test", type=str,
                        help="Quick test: run match_intent with this query and print results")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    
    args = parser.parse_args()
    
    # Feedback mode
    if args.feedback:
        if not args.intent:
            print("ERROR: --intent required for feedback", file=sys.stderr)
            sys.exit(1)
        if not args.status:
            print("ERROR: --status (hit|miss) required for feedback", file=sys.stderr)
            sys.exit(1)
        
        result = record_feedback(
            intent_id=args.intent,
            status=args.status,
            query=args.query or "",
            session_id=args.session_id,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            if "error" in result:
                print(f"❌ {result['error']}")
            elif result.get("intent_id") is None:
                print("📝 Query signature recorded (no intent update)")
            else:
                direction = "↑" if result["new_confidence"] > result["old_confidence"] else "↓"
                print(f"📊 {result['intent_name']}: "
                      f"conf {result['old_confidence']:.3f} → {result['new_confidence']:.3f} {direction} "
                      f"({result['hits']}h/{result['misses']}m)")
        return
    
    # Analysis mode
    if args.analyze:
        result = analyze_signatures(days=args.days)
        # Add total intent count
        conn = _get_db()  # Fix: use _get_db() to ensure schema exists
        result["total_intents"] = conn.execute(
            "SELECT COUNT(*) FROM intent_patterns"
        ).fetchone()[0]
        conn.close()
        
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"📈 Intent Analysis (last {args.days} days)")
            print(f"   Signatures: {result['total_signatures']} "
                  f"(hit={result['hit_rate']:.1%}, miss={result['miss_rate']:.1%})")
            print(f"   Unmatched:  {result['unmatched_queries']}")
            print(f"   Total intents: {result['total_intents']}")
            if result["frequent_unmatched_keywords"]:
                print(f"   Frequent unmatched keywords:")
                for kw, freq in result["frequent_unmatched_keywords"].items():
                    print(f"   - {kw}: {freq}x")
        return
    
    # Test mode: quick semantic/keyword matching test
    if args.test:
        result = match_intent(args.test, mode=args.mode)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"🧪 Test query: '{args.test}' (mode={args.mode})")
            if result["candidates"]:
                print(f"   Matcher: {result.get('mode', 'keyword')}")
                for i, c in enumerate(result["candidates"], 1):
                    print(f"   [{i}] {c['intent_name']}: "
                          f"hit_ratio={c['hit_ratio']:.4f} raw_score={c['raw_score']:.4f}")
            else:
                print(f"   No candidates matched")
            if result["match_level"] != "low":
                print(f"   Best: {result['intent_name']} "
                      f"(conf={result['confidence']:.4f}, level={result['match_level']})")
            else:
                print(f"   Result: NO MATCH (confidence < {MEDIUM_CONFIDENCE})")
        return

    # Query matching mode
    if not args.query:
        parser.print_help()
        sys.exit(1)
    
    if args.preload:
        result = get_preload_bundle(args.query, mode=args.mode)
    else:
        result = match_intent(args.query, mode=args.mode)
    
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if args.preload:
            if not result["preload"]:
                print(f"⚪ No preload: {result['reason']}")
            else:
                level_icon = "🟢" if result["match_level"] == "high" else "🟡"
                print(f"{level_icon} Intent: {result['intent']} "
                      f"(conf={result['confidence']:.3f}, {result['match_level']})")
                if result["skills"]:
                    print(f"   Skills: {', '.join(result['skills'])}")
                if result["memory_sections"]:
                    print(f"   Memory: {', '.join(result['memory_sections'])}")
                if result["role"]:
                    print(f"   Role:   {result['role']}")
                if result["auto_actions"]:
                    print(f"   Actions: {', '.join(result['auto_actions'])}")
        else:
            if result["match_level"] == "low":
                print(f"⚪ No strong match (confidence < {MEDIUM_CONFIDENCE})")
                if result["candidates"]:
                    print(f"   Top candidates:")
                    for c in result["candidates"]:
                        print(f"   - {c['intent_name']} (score={c['raw_score']:.3f})")
            else:
                level_icon = "🟢" if result["match_level"] == "high" else "🟡"
                print(f"{level_icon} Best match: {result['intent_name']} "
                      f"(conf={result['confidence']:.3f}, {result['match_level']})")
                if len(result["candidates"]) > 1:
                    print(f"   Alternates:")
                    for c in result["candidates"][1:3]:
                        print(f"   - {c['intent_name']} (score={c['raw_score']:.3f})")


if __name__ == "__main__":
    main()
