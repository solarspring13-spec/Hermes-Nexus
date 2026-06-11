#!/usr/bin/env python3
"""
Honcho-Style Dialectical User Model for Hermes WorkBuddy
=========================================================
Phase 3F: Structured user model with behavior tracking,
belief confidence scoring, contradiction detection, and evidence chains.

Stores in SQLite at {MEMORIA_HOME} with 4 tables:
- preferences: core belief/preference entries with confidence scores
- choices: historical choice records with context
- contradictions: detected belief contradictions
- evidence: evidence chain entries supporting each preference

Confidence Formula:
  confidence = base_score * source_multiplier * recency_factor - contradiction_penalty

Usage:
    # Record a preference
    python3 user_model.py --record-preference --key "output_format" --value "markdown" --source stated

    # Record a choice
    python3 user_model.py --record-choice --key "output_format" --choice "markdown" --context "报告生成"

    # Check for contradictions
    python3 user_model.py --check

    # Show user model status
    python3 user_model.py --status

    # Health report (0-100 score)
    python3 user_model.py --health
    python3 user_model.py --health --json

    # Ebbinghaus decay (preview or apply)
    python3 user_model.py --decay --decay-dry-run   # preview
    python3 user_model.py --decay                    # apply

    # Get evidence for a preference
    python3 user_model.py --evidence "output_format"

    # Search preferences
    python3 user_model.py --search "格式"

    # Intent Awareness Layer
    python3 user_model.py --seed-intents          # seed initial intent patterns
    python3 user_model.py --list-intents          # list all intent patterns
    python3 user_model.py --list-intents --json   # JSON output
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path


# ── Constants ────────────────────────────────────────────────

from ..constants import WORKBUDDY_DIR, DB_PATH

# Confidence formula parameters
BASE_SCORES = {1: 0.3, 2: 0.5, 3: 0.7, 5: 0.9}  # observation_count → score
SOURCE_MULTIPLIER = {"stated": 1.2, "inferred": 0.8, "system": 1.0}
CONTRADICTION_PENALTY = 0.2  # per unresolved contradiction
STALE_THRESHOLD = 0.3  # confidence below this → mark as stale
HIGH_CONFIDENCE = 0.8  # confidence above this → solid belief

SCHEMA = """
CREATE TABLE IF NOT EXISTS preferences (
    id TEXT PRIMARY KEY,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    confidence REAL DEFAULT 0.0,
    source TEXT DEFAULT 'inferred',
    evidence_count INTEGER DEFAULT 0,
    observation_count INTEGER DEFAULT 1,
    first_seen TEXT,
    last_updated TEXT,
    workspace TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    UNIQUE(key, value)
);

CREATE TABLE IF NOT EXISTS choices (
    id TEXT PRIMARY KEY,
    preference_id TEXT,
    choice_made TEXT NOT NULL,
    alternatives TEXT DEFAULT '',
    context TEXT DEFAULT '',
    session_id TEXT DEFAULT '',
    timestamp TEXT NOT NULL,
    FOREIGN KEY (preference_id) REFERENCES preferences(id)
);

CREATE TABLE IF NOT EXISTS contradictions (
    id TEXT PRIMARY KEY,
    preference_id TEXT,
    old_value TEXT NOT NULL,
    new_value TEXT NOT NULL,
    detected_session TEXT DEFAULT '',
    detected_at TEXT NOT NULL,
    resolved INTEGER DEFAULT 0,
    resolution TEXT DEFAULT '',
    FOREIGN KEY (preference_id) REFERENCES preferences(id)
);

CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    preference_id TEXT,
    evidence_type TEXT DEFAULT 'observation',
    evidence_content TEXT NOT NULL,
    session_id TEXT DEFAULT '',
    timestamp TEXT NOT NULL,
    FOREIGN KEY (preference_id) REFERENCES preferences(id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS preferences_fts USING fts5(
    key, value, tags,
    content='preferences',
    content_rowid='rowid'
);

CREATE INDEX IF NOT EXISTS idx_preferences_key ON preferences(key);
CREATE INDEX IF NOT EXISTS idx_choices_pref ON choices(preference_id);
CREATE INDEX IF NOT EXISTS idx_contradictions_pref ON contradictions(preference_id);
CREATE INDEX IF NOT EXISTS idx_evidence_pref ON evidence(preference_id);

-- Intent Awareness Layer tables
CREATE TABLE IF NOT EXISTS intent_patterns (
    id TEXT PRIMARY KEY,
    intent_name TEXT NOT NULL,
    pattern_keywords TEXT NOT NULL,
    context_bundle TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,
    hit_count INTEGER DEFAULT 0,
    miss_count INTEGER DEFAULT 0,
    last_matched TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS query_signatures (
    id TEXT PRIMARY KEY,
    raw_query TEXT,
    extracted_keywords TEXT,
    matched_intent TEXT,
    confidence REAL DEFAULT 0.0,
    was_hit INTEGER DEFAULT 0,
    context_used TEXT,
    session_id TEXT DEFAULT '',
    timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_intent_name ON intent_patterns(intent_name);
CREATE INDEX IF NOT EXISTS idx_query_intent ON query_signatures(matched_intent);
"""

# ── Intent Seed Data ──────────────────────────────────────────

INTENT_SEEDS = [
    {
        "id": "intent_stock_quick_check",
        "intent_name": "stock_quick_check",
        "pattern_keywords": "查一下,行情,股价,最新,看看,怎么样,涨跌,走势,实时,报价,快报",
        "context_bundle": json.dumps({
            "skills": ["westock-data", "neodata-financial-search"],
            "memory_sections": ["投资研究"],
            "role": "八大",
            "auto_actions": ["quote", "kline", "latest_finance"]
        }, ensure_ascii=False),
        "confidence": 0.75
    },
    {
        "id": "intent_stock_deep_research",
        "intent_name": "stock_deep_research",
        "pattern_keywords": "深度,研报,对比,估值,基本面,技术面,资金流向,财务分析,横向,财报,年报,产业链,竞争,护城河",
        "context_bundle": json.dumps({
            "skills": ["投研大脑", "deep-research", "westock-data"],
            "memory_sections": ["投资研究", "工程约定"],
            "role": "八大",
            "auto_actions": ["generate_research_prompt", "multi_period_finance"]
        }, ensure_ascii=False),
        "confidence": 0.70
    },
    {
        "id": "intent_investment_dd",
        "intent_name": "investment_dd",
        "pattern_keywords": "尽调,BP,项目,条款,估值,SPA,交割,CVC,战投,财投,融资轮次,portfolio,deal,基金,LP,GP,退出,并购,SPAC",
        "context_bundle": json.dumps({
            "skills": ["due-diligence", "investment-memo", "lexbridge-legal-counsel"],
            "memory_sections": ["投资研究", "核心投资判断"],
            "role": "L",
            "auto_actions": ["load_dd_framework"]
        }, ensure_ascii=False),
        "confidence": 0.80
    },
    {
        "id": "intent_investment_memo",
        "intent_name": "investment_memo",
        "pattern_keywords": "纪要,访谈,录音,周会,汇报,项目纪要,七大模块,口头稿,浓缩",
        "context_bundle": json.dumps({
            "skills": ["investment-memo", "meeting-transcript", "weekly-report"],
            "memory_sections": ["投资研究"],
            "role": "L",
            "auto_actions": ["load_memo_template"]
        }, ensure_ascii=False),
        "confidence": 0.78
    },
    {
        "id": "intent_travel_plan",
        "intent_name": "travel_plan",
        "pattern_keywords": "去,旅行,攻略,酒店,机票,火车票,景点,行程,周末,短途,自驾,花园,美术馆,博物馆,美食,旅拍,打卡,目的地,云水司,J型旅行,j-travel-planner,郊游,温泉,滑雪,看展,古镇,园林",
        "context_bundle": json.dumps({
            "skills": ["j-travel-planner", "xiaohongshu", "tc-deeptrip"],
            "memory_sections": ["个人上下文"],
            "role": "共享",
            "auto_actions": ["search_xiaohongshu", "plan_itinerary"]
        }, ensure_ascii=False),
        "confidence": 0.72
    },
    {
        "id": "intent_system_architect",
        "intent_name": "system_architect",
        "pattern_keywords": "MCP,Skill,架构,系统,Hermes,记忆,WorkBuddy,OpenClaw,Agent,守护,daemon,配置,模型,Ollama,解耦,审计,白皮书,蓝图",
        "context_bundle": json.dumps({
            "skills": ["enhanced-memory", "debug", "doubt-driven-development", "caveman"],
            "memory_sections": ["工程约定", "产品工作"],
            "role": "八大",
            "auto_actions": ["read_blueprint", "check_daemon_health"]
        }, ensure_ascii=False),
        "confidence": 0.85
    },
    {
        "id": "intent_skill_create",
        "intent_name": "skill_create",
        "pattern_keywords": "创建skill,自动化,Skill,生成Agent,Prompt,模板,固化,复用",
        "context_bundle": json.dumps({
            "skills": ["skill-creator", "meta-agent-generator", "skills-security-check"],
            "memory_sections": ["产品工作"],
            "role": "八大",
            "auto_actions": ["load_skill_template"]
        }, ensure_ascii=False),
        "confidence": 0.68
    },
    {
        "id": "intent_memory_review",
        "intent_name": "memory_review",
        "pattern_keywords": "回顾,上次,之前,还记得,回忆,memory,记录,查一下之前,找一下以前",
        "context_bundle": json.dumps({
            "skills": ["enhanced-memory"],
            "memory_sections": ["全部"],
            "role": "共享",
            "auto_actions": ["fts5_search", "conversation_search"]
        }, ensure_ascii=False),
        "confidence": 0.82
    },
    {
        "id": "intent_report_gen",
        "intent_name": "report_gen",
        "pattern_keywords": "报告,可视化,图表,PPT,PDF,Excel,数据报告,财报,路演,Pitch,slides,演示,图表生成",
        "context_bundle": json.dumps({
            "skills": ["html-report", "pptx", "pdf", "xlsx", "docx"],
            "memory_sections": ["工程约定"],
            "role": "共享",
            "auto_actions": ["load_report_template"]
        }, ensure_ascii=False),
        "confidence": 0.70
    },
    {
        "id": "intent_legal_review",
        "intent_name": "legal_review",
        "pattern_keywords": "合同,条款,法律,合规,法务,协议,SPA,SHA,公司章程,章程,数据隐私,GDPR,跨境,香港法,美国法,中国法,欧盟法",
        "context_bundle": json.dumps({
            "skills": ["lexbridge-legal-counsel"],
            "memory_sections": ["投资研究"],
            "role": "L",
            "auto_actions": ["load_legal_framework"]
        }, ensure_ascii=False),
        "confidence": 0.76
    },
    {
        "id": "intent_meeting_work",
        "intent_name": "meeting_work",
        "pattern_keywords": "会议,转录,周报,汇报,录音转文字,腾讯会议,飞书,纪要整理",
        "context_bundle": json.dumps({
            "skills": ["meeting-transcript", "weekly-report", "tencent-meeting-mcp"],
            "memory_sections": ["投资研究"],
            "role": "L",
            "auto_actions": ["load_transcript_template"]
        }, ensure_ascii=False),
        "confidence": 0.73
    },
    {
        "id": "intent_code_debug",
        "intent_name": "code_debug",
        "pattern_keywords": "报错,bug,不工作,修复,调试,debug,错误,失败,exception,traceback,崩溃,异常",
        "context_bundle": json.dumps({
            "skills": ["debug", "caveman"],
            "memory_sections": ["工程约定"],
            "role": "八大",
            "auto_actions": ["reproduce", "isolate", "diagnose"]
        }, ensure_ascii=False),
        "confidence": 0.78
    },
    {
        "id": "intent_system_janitor",
        "intent_name": "system_janitor",
        "pattern_keywords": "清理,扫尘,janitor,磁盘空间,缓存,dry-run,熵减,熵增,垃圾,空间不足,磁盘满了,释放空间,系统清理,清理日志,清理缓存,cleaner",
        "context_bundle": json.dumps({
            "skills": ["agent-janitor"],
            "memory_sections": ["工程约定"],
            "role": "八大",
            "auto_actions": ["scan_filesystem", "classify_risk", "generate_report"]
        }, ensure_ascii=False),
        "confidence": 0.65
    },
]


def seed_intents() -> dict:
    """Seed the intent_patterns table with initial patterns."""
    conn = _get_db()
    now = datetime.now().isoformat()
    count_new = 0
    count_skip = 0

    for seed in INTENT_SEEDS:
        existing = conn.execute(
            "SELECT id FROM intent_patterns WHERE id = ?", (seed["id"],)
        ).fetchone()
        if existing:
            count_skip += 1
            continue
        conn.execute(
            """INSERT INTO intent_patterns
               (id, intent_name, pattern_keywords, context_bundle,
                confidence, hit_count, miss_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?)""",
            (seed["id"], seed["intent_name"], seed["pattern_keywords"],
             seed["context_bundle"], seed["confidence"], now, now)
        )
        count_new += 1

    conn.commit()
    conn.close()
    return {"seeded": count_new, "skipped": count_skip}


def list_intents() -> list:
    """List all intent patterns."""
    conn = _get_db()
    rows = conn.execute(
        """SELECT id, intent_name, confidence, hit_count, miss_count,
                  last_matched, updated_at
           FROM intent_patterns ORDER BY confidence DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── DB Management ─────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    """Get SQLite connection, create schema if needed."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ── Confidence Calculation ────────────────────────────────────

def _base_score_from_count(count: int) -> float:
    """Map observation count to base score."""
    thresholds = sorted(BASE_SCORES.keys(), reverse=True)
    for t in thresholds:
        if count >= t:
            return BASE_SCORES[t]
    return 0.1


def _recency_factor(last_updated: str) -> float:
    """Calculate recency factor from last_updated timestamp."""
    try:
        dt = datetime.fromisoformat(last_updated)
        days_ago = (datetime.now() - dt).days
    except (ValueError, TypeError):
        days_ago = 365

    if days_ago <= 7:
        return 1.0
    elif days_ago <= 30:
        return 0.7
    else:
        return 0.3


def calculate_confidence(key: str, value: str, source: str,
                         observation_count: int,
                         last_updated: str,
                         unresolved_contradictions: int) -> float:
    """Calculate confidence score for a preference."""
    base = _base_score_from_count(observation_count)
    multiplier = SOURCE_MULTIPLIER.get(source, 1.0)
    recency = _recency_factor(last_updated)
    penalty = CONTRADICTION_PENALTY * unresolved_contradictions

    confidence = base * multiplier * recency - penalty
    return max(0.0, min(1.0, round(confidence, 3)))


# ── Core Operations ───────────────────────────────────────────

def record_preference(key: str, value: str, source: str = "inferred",
                      workspace: str = "", tags: str = "") -> dict:
    """Record or update a preference."""
    conn = _get_db()
    now = datetime.now().isoformat()
    pref_id = str(uuid.uuid4())[:8]

    # Check if exact key+value already exists
    existing = conn.execute(
        "SELECT id, observation_count, last_updated FROM preferences WHERE key=? AND value=?",
        (key, value)
    ).fetchone()

    if existing:
        # Update existing
        new_count = existing["observation_count"] + 1

        # Count unresolved contradictions
        unresolved = conn.execute(
            "SELECT COUNT(*) as cnt FROM contradictions WHERE preference_id=? AND resolved=0",
            (existing["id"],)
        ).fetchone()["cnt"]

        confidence = calculate_confidence(
            key, value, source, new_count, now, unresolved
        )

        conn.execute(
            """UPDATE preferences SET
               observation_count=?, confidence=?, source=?,
               last_updated=?, workspace=?, tags=?
               WHERE id=?""",
            (new_count, confidence, source, now, workspace, tags, existing["id"])
        )
        conn.commit()
        conn.close()

        return {
            "recorded": True,
            "preference_id": existing["id"],
            "action": "updated",
            "observation_count": new_count,
            "confidence": confidence,
        }
    else:
        # New preference
        conn.execute(
            """INSERT INTO preferences
               (id, key, value, confidence, source, observation_count,
                first_seen, last_updated, workspace, tags)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)""",
            (pref_id, key, value,
             _base_score_from_count(1) * SOURCE_MULTIPLIER.get(source, 1.0),
             source, now, now, workspace, tags)
        )
        conn.commit()
        conn.close()

        return {
            "recorded": True,
            "preference_id": pref_id,
            "action": "created",
            "observation_count": 1,
            "confidence": _base_score_from_count(1) * SOURCE_MULTIPLIER.get(source, 1.0),
        }


def record_choice(key: str, choice: str, context: str = "",
                  session_id: str = "", alternatives: str = "") -> dict:
    """Record a user choice, linking to the corresponding preference."""
    conn = _get_db()
    now = datetime.now().isoformat()

    # Find or create preference
    pref = conn.execute(
        "SELECT id FROM preferences WHERE key=? AND value=?",
        (key, choice)
    ).fetchone()

    if not pref:
        # Auto-create preference as inferred
        result = record_preference(key, choice, source="inferred")
        pref_id = result["preference_id"]
    else:
        pref_id = pref["id"]

    # Record the choice
    choice_id = str(uuid.uuid4())[:8]
    conn.execute(
        """INSERT INTO choices
           (id, preference_id, choice_made, alternatives, context, session_id, timestamp)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (choice_id, pref_id, choice, alternatives, context, session_id, now)
    )

    # Add evidence
    evidence_id = str(uuid.uuid4())[:8]
    conn.execute(
        """INSERT INTO evidence
           (id, preference_id, evidence_type, evidence_content, session_id, timestamp)
           VALUES (?, ?, 'choice', ?, ?, ?)""",
        (evidence_id, pref_id,
         f"Chose '{choice}' in context: {context}" if context else f"Chose '{choice}'",
         session_id, now)
    )

    # Update preference stats
    pref = conn.execute("SELECT observation_count, last_updated FROM preferences WHERE id=?",
                        (pref_id,)).fetchone()
    new_count = pref["observation_count"] + 1

    # Count unresolved contradictions
    unresolved = conn.execute(
        "SELECT COUNT(*) as cnt FROM contradictions WHERE preference_id=? AND resolved=0",
        (pref_id,)
    ).fetchone()["cnt"]

    confidence = calculate_confidence(key, choice, "inferred", new_count, now, unresolved)

    conn.execute(
        "UPDATE preferences SET observation_count=?, confidence=?, last_updated=? WHERE id=?",
        (new_count, confidence, now, pref_id)
    )
    conn.commit()
    conn.close()

    return {
        "recorded": True,
        "preference_id": pref_id,
        "choice_id": choice_id,
        "observation_count": new_count,
        "confidence": confidence,
    }


def check_contradictions() -> list:
    """Detect contradictions: same key, different values with high confidence."""
    conn = _get_db()
    findings = []

    # Find keys with multiple values
    rows = conn.execute(
        """SELECT `key`, COUNT(DISTINCT `value`) as val_count,
                  GROUP_CONCAT(`value`) as vals,
                  GROUP_CONCAT(id) as ids
           FROM preferences
           GROUP BY `key`
           HAVING val_count > 1"""
    ).fetchall()

    for row in rows:
        values = row["vals"].split(",")
        ids = row["ids"].split(",")

        # Check if any pair has both values with confidence > 0.3
        for i in range(len(values)):
            for j in range(i + 1, len(values)):
                conf_i = conn.execute(
                    "SELECT confidence FROM preferences WHERE id=?",
                    (ids[i],)
                ).fetchone()["confidence"]
                conf_j = conn.execute(
                    "SELECT confidence FROM preferences WHERE id=?",
                    (ids[j],)
                ).fetchone()["confidence"]

                if conf_i > STALE_THRESHOLD and conf_j > STALE_THRESHOLD:
                    # Found contradiction
                    existing = conn.execute(
                        """SELECT id FROM contradictions
                           WHERE preference_id IN (?, ?) AND resolved=0""",
                        (ids[i], ids[j])
                    ).fetchone()

                    if not existing:
                        now = datetime.now().isoformat()
                        cont_id = str(uuid.uuid4())[:8]
                        conn.execute(
                            """INSERT INTO contradictions
                               (id, preference_id, old_value, new_value, detected_at)
                               VALUES (?, ?, ?, ?, ?)""",
                            (cont_id, ids[i], values[i], values[j], now)
                        )
                        findings.append({
                            "id": cont_id,
                            "key": row["key"],
                            "value_a": values[i],
                            "confidence_a": conf_i,
                            "value_b": values[j],
                            "confidence_b": conf_j,
                        })

    conn.commit()
    conn.close()
    return findings


def get_evidence(key: str) -> list:
    """Get evidence chain for a preference key."""
    conn = _get_db()

    prefs = conn.execute(
        "SELECT id, value, confidence FROM preferences WHERE key=?",
        (key,)
    ).fetchall()

    evidence_list = []
    for pref in prefs:
        ev_rows = conn.execute(
            """SELECT evidence_type, evidence_content, session_id, timestamp
               FROM evidence WHERE preference_id=? ORDER BY timestamp""",
            (pref["id"],)
        ).fetchall()

        evidence_list.append({
            "preference_id": pref["id"],
            "value": pref["value"],
            "confidence": pref["confidence"],
            "evidence": [dict(r) for r in ev_rows],
        })

    conn.close()
    return evidence_list


def get_contradictions(key: str = None) -> list:
    """Get contradictions, optionally filtered by key."""
    conn = _get_db()

    if key:
        rows = conn.execute(
            """SELECT c.*, p.key as pref_key
               FROM contradictions c
               JOIN preferences p ON c.preference_id = p.id
               WHERE p.key=? AND c.resolved=0
               ORDER BY c.detected_at DESC""",
            (key,)
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT c.*, p.key as pref_key
               FROM contradictions c
               JOIN preferences p ON c.preference_id = p.id
               WHERE c.resolved=0
               ORDER BY c.detected_at DESC"""
        ).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def suggest_stale() -> list:
    """Find preferences with confidence below STALE_THRESHOLD."""
    conn = _get_db()

    rows = conn.execute(
        """SELECT key, value, confidence, observation_count, last_updated
           FROM preferences
           WHERE confidence < ?
           ORDER BY confidence ASC""",
        (STALE_THRESHOLD,)
    ).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def resolve_contradiction(contradiction_id: str, resolution: str,
                          keep_value: str = None) -> dict:
    """Resolve a contradiction."""
    conn = _get_db()

    cont = conn.execute(
        "SELECT * FROM contradictions WHERE id=?",
        (contradiction_id,)
    ).fetchone()

    if not cont:
        conn.close()
        return {"error": "Contradiction not found"}

    # Mark as resolved
    conn.execute(
        "UPDATE contradictions SET resolved=1, resolution=? WHERE id=?",
        (resolution, contradiction_id)
    )

    # If a value was chosen, reduce confidence of the other value
    if keep_value:
        pref = conn.execute(
            "SELECT id, value FROM preferences WHERE id=?",
            (cont["preference_id"],)
        ).fetchone()
        if pref and pref["value"] != keep_value:
            conn.execute(
                "UPDATE preferences SET confidence = confidence * 0.5 WHERE id=?",
                (pref["id"],)
            )

    conn.commit()
    conn.close()
    return {"resolved": True, "contradiction_id": contradiction_id}


def get_model_status() -> dict:
    """Get overall user model statistics."""
    conn = _get_db()

    total_prefs = conn.execute("SELECT COUNT(*) as cnt FROM preferences").fetchone()["cnt"]
    total_choices = conn.execute("SELECT COUNT(*) as cnt FROM choices").fetchone()["cnt"]
    total_cont = conn.execute(
        "SELECT COUNT(*) as cnt FROM contradictions WHERE resolved=0"
    ).fetchone()["cnt"]
    total_evidence = conn.execute("SELECT COUNT(*) as cnt FROM evidence").fetchone()["cnt"]

    # Confidence distribution
    high = conn.execute(
        "SELECT COUNT(*) as cnt FROM preferences WHERE confidence >= ?",
        (HIGH_CONFIDENCE,)
    ).fetchone()["cnt"]
    stale = conn.execute(
        "SELECT COUNT(*) as cnt FROM preferences WHERE confidence < ?",
        (STALE_THRESHOLD,)
    ).fetchone()["cnt"]

    # Source distribution
    sources = {}
    for row in conn.execute("SELECT source, COUNT(*) as cnt FROM preferences GROUP BY source"):
        sources[row["source"]] = row["cnt"]

    # Top preferences by confidence
    top = conn.execute(
        "SELECT key, value, confidence FROM preferences ORDER BY confidence DESC LIMIT 10"
    ).fetchall()

    conn.close()

    return {
        "total_preferences": total_prefs,
        "total_choices": total_choices,
        "unresolved_contradictions": total_cont,
        "total_evidence": total_evidence,
        "high_confidence": high,
        "stale": stale,
        "sources": sources,
        "top_preferences": [dict(r) for r in top],
        "stale_threshold": STALE_THRESHOLD,
        "high_threshold": HIGH_CONFIDENCE,
    }


# ── Health Report ─────────────────────────────────────────────

def get_health_report() -> dict:
    """Generate comprehensive health report (0-100 score).
    
    Scoring dimensions:
    - Data density (25 pts): total_preferences vs expected minimum
    - Contradiction health (25 pts): fewer unresolved = better
    - Confidence distribution (20 pts): spread across high/medium/stale
    - Data freshness (15 pts): average recency of last_updated
    - Evidence coverage (10 pts): % preferences with evidence
    - DB integrity (5 pts): FTS5 index consistency
    """
    conn = _get_db()
    
    total_prefs = conn.execute("SELECT COUNT(*) as cnt FROM preferences").fetchone()["cnt"]
    total_choices = conn.execute("SELECT COUNT(*) as cnt FROM choices").fetchone()["cnt"]
    total_evidence = conn.execute("SELECT COUNT(*) as cnt FROM evidence").fetchone()["cnt"]
    
    unresolved = conn.execute(
        "SELECT COUNT(*) as cnt FROM contradictions WHERE resolved=0"
    ).fetchone()["cnt"]
    
    # High / Medium / Stale distribution
    high = conn.execute(
        "SELECT COUNT(*) as cnt FROM preferences WHERE confidence >= ?",
        (HIGH_CONFIDENCE,)
    ).fetchone()["cnt"]
    medium = conn.execute(
        "SELECT COUNT(*) as cnt FROM preferences WHERE confidence >= ? AND confidence < ?",
        (STALE_THRESHOLD, HIGH_CONFIDENCE)
    ).fetchone()["cnt"]
    stale = conn.execute(
        "SELECT COUNT(*) as cnt FROM preferences WHERE confidence < ?",
        (STALE_THRESHOLD,)
    ).fetchone()["cnt"]
    
    # Average days since last update
    avg_days = 0
    if total_prefs > 0:
        rows = conn.execute(
            "SELECT last_updated FROM preferences WHERE last_updated IS NOT NULL"
        ).fetchall()
        total_days = 0
        for r in rows:
            try:
                dt = datetime.fromisoformat(r["last_updated"])
                total_days += (datetime.now() - dt).days
            except (ValueError, TypeError):
                total_days += 365
        avg_days = total_days / len(rows) if rows else 365
    
    # Evidence coverage
    pref_with_evidence = conn.execute(
        "SELECT COUNT(DISTINCT preference_id) as cnt FROM evidence"
    ).fetchone()["cnt"]
    evidence_coverage = pref_with_evidence / total_prefs if total_prefs > 0 else 0
    
    # FTS5 integrity
    fts_count = conn.execute("SELECT COUNT(*) as cnt FROM preferences_fts").fetchone()["cnt"]
    fts_ok = fts_count == total_prefs
    
    conn.close()
    
    # ── Scoring ──
    # 1. Data density (25 pts) — expected min: 10 preferences
    density_score = min(25, (total_prefs / 10) * 25) if total_prefs > 0 else 0
    
    # 2. Contradiction health (25 pts)
    if total_prefs == 0:
        contradiction_score = 25
    else:
        cont_ratio = unresolved / total_prefs
        contradiction_score = max(0, 25 - (cont_ratio * 100))
    
    # 3. Confidence distribution (20 pts) — prefer bimodal (high + medium > stale)
    if total_prefs == 0:
        conf_score = 20
    else:
        healthy_ratio = (high + medium) / total_prefs
        conf_score = healthy_ratio * 20
    
    # 4. Data freshness (15 pts) — avg_days ≤ 30 = full, > 180 = 0
    if total_prefs == 0:
        freshness_score = 15
    else:
        freshness_score = max(0, 15 - (avg_days / 12))
    
    # 5. Evidence coverage (10 pts)
    evidence_score = evidence_coverage * 10
    
    # 6. DB integrity (5 pts)
    db_score = 5 if fts_ok else 0
    
    total_score = round(
        density_score + contradiction_score + conf_score + 
        freshness_score + evidence_score + db_score, 1
    )
    
    # ── Recommendations ──
    recommendations = []
    if total_prefs < 10:
        recommendations.append(f"偏好数据不足({total_prefs}/10)，建议增加交互记录")
    if unresolved > 0:
        recommendations.append(f"存在{unresolved}个未解决矛盾，建议审查并解决")
    if stale > total_prefs * 0.3 and total_prefs > 0:
        recommendations.append(f"低置信度偏好占比过高({stale}/{total_prefs})，建议运行 --decay 清理")
    if avg_days > 60:
        recommendations.append(f"数据平均{avg_days:.0f}天未更新，用户模型可能过时")
    if not fts_ok:
        recommendations.append("FTS5索引不一致，建议运行 --rebuild-fts")
    if evidence_coverage < 0.3 and total_prefs > 0:
        recommendations.append(f"证据覆盖率低({evidence_coverage:.0%})，建议补充选择记录")
    
    # ── Grade ──
    if total_score >= 85:
        grade = "A"
    elif total_score >= 70:
        grade = "B"
    elif total_score >= 50:
        grade = "C"
    elif total_score >= 30:
        grade = "D"
    else:
        grade = "F"
    
    return {
        "total_score": total_score,
        "grade": grade,
        "dimensions": {
            "data_density": round(density_score, 1),
            "contradiction_health": round(contradiction_score, 1),
            "confidence_distribution": round(conf_score, 1),
            "data_freshness": round(freshness_score, 1),
            "evidence_coverage": round(evidence_score, 1),
            "db_integrity": round(db_score, 1),
        },
        "metrics": {
            "total_preferences": total_prefs,
            "total_choices": total_choices,
            "total_evidence": total_evidence,
            "unresolved_contradictions": unresolved,
            "high_confidence": high,
            "medium_confidence": medium,
            "stale": stale,
            "avg_days_since_update": round(avg_days, 1),
            "evidence_coverage_pct": round(evidence_coverage * 100, 1),
            "fts_healthy": fts_ok,
        },
        "recommendations": recommendations,
    }


# ── Ebbinghaus Decay ──────────────────────────────────────────

def apply_ebbinghaus_decay(dry_run: bool = True) -> dict:
    """Apply Ebbinghaus forgetting curve to all preferences.
    
    Formula: R = e^(-t / S)
    - t = days since last_updated
    - S = relative strength (observation_count * base_multiplier)
    
    New confidence = current_confidence * R
    Only decays preferences with confidence > 0.05 (floor).
    """
    import math
    
    conn = _get_db()
    now = datetime.now()
    
    rows = conn.execute(
        """SELECT id, key, value, confidence, observation_count, last_updated
           FROM preferences"""
    ).fetchall()
    
    updated = []
    skipped = []
    total_decay = 0.0
    
    for r in rows:
        try:
            dt = datetime.fromisoformat(r["last_updated"])
            days = (now - dt).days
        except (ValueError, TypeError):
            days = 365
        
        if days < 1:
            skipped.append({"key": r["key"], "value": r["value"], "reason": "updated today"})
            continue
        
        # Relative strength: observation_count gives stronger memory
        S = r["observation_count"] * 7  # each observation = 7 days of strength
        
        # Ebbinghaus retention rate
        R = math.exp(-days / S)
        
        new_confidence = round(r["confidence"] * R, 4)
        
        # Floor at 0.05, ceiling at current (don't increase)
        new_confidence = max(0.05, min(r["confidence"], new_confidence))
        
        if new_confidence < r["confidence"]:
            decay = round(r["confidence"] - new_confidence, 4)
            total_decay += decay
            
            if not dry_run:
                conn.execute(
                    "UPDATE preferences SET confidence = ? WHERE id = ?",
                    (new_confidence, r["id"])
                )
            
            updated.append({
                "key": r["key"],
                "value": r["value"],
                "old_confidence": round(r["confidence"], 4),
                "new_confidence": new_confidence,
                "decay": decay,
                "days": days,
                "observations": r["observation_count"],
                "retention_rate": round(R, 4),
            })
        else:
            skipped.append({
                "key": r["key"], "value": r["value"],
                "reason": f"floor reached (conf={r['confidence']:.4f})"
            })
    
    if not dry_run:
        conn.commit()
    
    conn.close()
    
    return {
        "dry_run": dry_run,
        "total_preferences": len(rows),
        "updated": len(updated),
        "skipped": len(skipped),
        "total_decay": round(total_decay, 4),
        "details": updated[:20],  # Limit details to top 20
        "skipped_summary": f"{len(skipped)} skipped" if skipped else "none",
    }


def rebuild_fts():
    """Rebuild FTS5 index for preferences."""
    conn = _get_db()
    conn.execute("DELETE FROM preferences_fts")
    conn.execute(
        """INSERT INTO preferences_fts(rowid, key, value, tags)
           SELECT rowid, key, value, tags FROM preferences"""
    )
    conn.commit()
    conn.close()


def search_preferences(query: str, limit: int = 10) -> list:
    """Search preferences using FTS5."""
    conn = _get_db()
    rebuild_fts()  # Refresh index

    try:
        rows = conn.execute(
            """SELECT p.key, p.value, p.confidence, p.source, p.last_updated
               FROM preferences_fts f
               JOIN preferences p ON f.rowid = p.rowid
               WHERE preferences_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (query, limit)
        ).fetchall()
    except sqlite3.OperationalError:
        # FTS5 may fail on special chars; fall back to LIKE
        rows = conn.execute(
            """SELECT key, value, confidence, source, last_updated
               FROM preferences
               WHERE key LIKE ? OR value LIKE ? OR tags LIKE ?
               LIMIT ?""",
            (f"%{query}%", f"%{query}%", f"%{query}%", limit)
        ).fetchall()

    conn.close()
    return [dict(r) for r in rows]


# ── Data Export ───────────────────────────────────────────────

def export_to_markdown() -> str:
    """Export user model as Markdown for USER.md integration."""
    conn = _get_db()

    lines = ["## 用户画像（自动生成）\n"]

    # High confidence preferences
    high = conn.execute(
        """SELECT key, value, confidence, source, observation_count
           FROM preferences WHERE confidence >= ?
           ORDER BY confidence DESC""",
        (HIGH_CONFIDENCE,)
    ).fetchall()

    if high:
        lines.append("### [用户自述] 高置信度偏好\n")
        for r in high:
            src_label = "用户自述" if r["source"] == "stated" else "行为推断"
            lines.append(f"- [{src_label}] {r['key']}: {r['value']} "
                        f"(置信度 {r['confidence']:.2f}, {r['observation_count']}次观察)")

    # Medium confidence
    medium = conn.execute(
        """SELECT key, value, confidence, source, observation_count
           FROM preferences WHERE confidence >= ? AND confidence < ?
           ORDER BY confidence DESC""",
        (STALE_THRESHOLD, HIGH_CONFIDENCE)
    ).fetchall()

    if medium:
        lines.append("\n### [行为推断] 中等置信度偏好\n")
        for r in medium:
            lines.append(f"- [行为推断] {r['key']}: {r['value']} "
                        f"(置信度 {r['confidence']:.2f}, {r['observation_count']}次观察)")

    # Stale / low confidence
    stale = conn.execute(
        """SELECT key, value, confidence, observation_count
           FROM preferences WHERE confidence < ?
           ORDER BY confidence ASC""",
        (STALE_THRESHOLD,)
    ).fetchall()

    if stale:
        lines.append("\n### [可能过时] 低置信度标记\n")
        for r in stale:
            lines.append(f"- [可能过时] {r['key']}: {r['value']} "
                        f"(置信度 {r['confidence']:.2f})")

    # Contradictions
    unresolved = conn.execute(
        """SELECT c.*, p.key, p.value
           FROM contradictions c
           JOIN preferences p ON c.preference_id = p.id
           WHERE c.resolved=0"""
    ).fetchall()

    if unresolved:
        lines.append("\n### ⚠️ 未解决的矛盾\n")
        for c in unresolved:
            lines.append(f"- {c['key']}: 曾偏好「{c['old_value']}」，"
                        f"现偏好「{c['new_value']}」(检测于 {c['detected_at'][:10]})")

    conn.close()
    return "\n".join(lines) + "\n"


# ── CLI ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Honcho-Style Dialectical User Model (Phase 3F)"
    )
    parser.add_argument("--record-preference", action="store_true",
                        help="Record a preference")
    parser.add_argument("--record-choice", action="store_true",
                        help="Record a user choice")
    parser.add_argument("--key", type=str,
                        help="Preference key")
    parser.add_argument("--value", type=str,
                        help="Preference value")
    parser.add_argument("--choice", type=str,
                        help="Choice made (for --record-choice)")
    parser.add_argument("--source", type=str, default="inferred",
                        choices=["stated", "inferred", "system"],
                        help="Preference source: stated (explicit), inferred, system")
    parser.add_argument("--context", type=str, default="",
                        help="Context for the choice")
    parser.add_argument("--session-id", type=str, default="",
                        help="Session identifier")
    parser.add_argument("--alternatives", type=str, default="",
                        help="Alternative options (comma-separated)")
    parser.add_argument("--workspace", "-w", type=str, default="",
                        help="Source workspace")
    parser.add_argument("--tags", type=str, default="",
                        help="Tags (comma-separated)")
    parser.add_argument("--check", action="store_true",
                        help="Check for contradictions")
    parser.add_argument("--status", action="store_true",
                        help="Show user model status")
    parser.add_argument("--evidence", type=str,
                        help="Get evidence for a preference key")
    parser.add_argument("--contradictions", type=str, nargs="?",
                        const="__ALL__",
                        help="List contradictions (optionally filtered by key)")
    parser.add_argument("--stale", action="store_true",
                        help="List stale/low-confidence preferences")
    parser.add_argument("--search", type=str,
                        help="Search preferences by keyword")
    parser.add_argument("--resolve", type=str,
                        help="Resolve a contradiction by ID")
    parser.add_argument("--resolution", type=str, default="",
                        help="Resolution description")
    parser.add_argument("--keep-value", type=str,
                        help="Value to keep when resolving")
    parser.add_argument("--export-markdown", action="store_true",
                        help="Export user model as Markdown")
    parser.add_argument("--health", action="store_true",
                        help="Generate user model health report (0-100 score)")
    parser.add_argument("--decay", action="store_true",
                        help="Apply Ebbinghaus decay to all preferences")
    parser.add_argument("--decay-dry-run", action="store_true",
                        help="Preview Ebbinghaus decay without writing")
    parser.add_argument("--seed-intents", action="store_true",
                        help="Seed initial intent patterns into intent_patterns table")
    parser.add_argument("--list-intents", action="store_true",
                        help="List all intent patterns")
    parser.add_argument("--rebuild-fts", action="store_true",
                        help="Rebuild FTS5 index")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")

    args = parser.parse_args()

    if args.record_preference:
        if not args.key or not args.value:
            print("ERROR: --key and --value required", file=sys.stderr)
            sys.exit(1)
        result = record_preference(
            key=args.key, value=args.value,
            source=args.source, workspace=args.workspace,
            tags=args.tags,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"✅ Preference '{args.key}={args.value}' {result['action']}")
            print(f"   Confidence: {result['confidence']:.2f} | Obs: {result['observation_count']}")
        return

    if args.record_choice:
        if not args.key or not args.choice:
            print("ERROR: --key and --choice required", file=sys.stderr)
            sys.exit(1)
        result = record_choice(
            key=args.key, choice=args.choice,
            context=args.context, session_id=args.session_id,
            alternatives=args.alternatives,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"✅ Choice recorded: '{args.key}' → '{args.choice}'")
            print(f"   Confidence: {result['confidence']:.2f} | Obs: {result['observation_count']}")
        return

    if args.check:
        findings = check_contradictions()
        if args.json:
            print(json.dumps({"contradictions_found": len(findings), "findings": findings},
                           ensure_ascii=False, indent=2))
        else:
            if not findings:
                print("✅ No contradictions detected.")
            else:
                print(f"⚠️  Found {len(findings)} contradiction(s):")
                for f in findings:
                    print(f"   - {f['key']}: '{f['value_a']}' (conf={f['confidence_a']:.2f})"
                          f" vs '{f['value_b']}' (conf={f['confidence_b']:.2f})")
        return

    if args.status:
        status = get_model_status()
        if args.json:
            print(json.dumps(status, ensure_ascii=False, indent=2))
        else:
            print("📊 User Model Status")
            print(f"   Preferences:      {status['total_preferences']}")
            print(f"   Choices:          {status['total_choices']}")
            print(f"   Evidence:         {status['total_evidence']}")
            print(f"   Contradictions:   {status['unresolved_contradictions']} ⚠️" if status['unresolved_contradictions'] else f"   Contradictions:   {status['unresolved_contradictions']} ✅")
            print(f"   High confidence:  {status['high_confidence']} (≥{status['high_threshold']})")
            print(f"   Stale:            {status['stale']} (<{status['stale_threshold']})")
            if status.get("sources"):
                srcs = ", ".join(f"{k}={v}" for k, v in status["sources"].items())
                print(f"   Sources:          {srcs}")
            if status.get("top_preferences"):
                print(f"\n   Top preferences:")
                for p in status["top_preferences"][:5]:
                    print(f"   - {p['key']}: {p['value']} ({p['confidence']:.2f})")
        return

    if args.evidence:
        evidence = get_evidence(args.evidence)
        if args.json:
            print(json.dumps(evidence, ensure_ascii=False, indent=2))
        else:
            if not evidence:
                print(f"🔍 No evidence found for '{args.evidence}'")
            else:
                for e in evidence:
                    print(f"📋 {args.evidence} = {e['value']} (置信度: {e['confidence']:.2f})")
                    for ev in e["evidence"]:
                        print(f"   [{ev['timestamp'][:10]}] {ev['evidence_content'][:100]}")
        return

    if args.contradictions is not None:
        key = None if args.contradictions == "__ALL__" else args.contradictions
        contradictions = get_contradictions(key)
        if args.json:
            print(json.dumps(contradictions, ensure_ascii=False, indent=2))
        else:
            if not contradictions:
                print("✅ No unresolved contradictions.")
            else:
                print(f"⚠️  {len(contradictions)} unresolved contradiction(s):")
                for c in contradictions:
                    print(f"   [{c['id']}] {c['pref_key']}: '{c['old_value']}' → '{c['new_value']}'"
                          f" ({c['detected_at'][:10]})")
        return

    if args.stale:
        stale = suggest_stale()
        if args.json:
            print(json.dumps(stale, ensure_ascii=False, indent=2))
        else:
            if not stale:
                print("✅ No stale preferences.")
            else:
                print(f"🟡 {len(stale)} stale/low-confidence preferences:")
                for s in stale:
                    print(f"   - {s['key']}: {s['value']} (conf={s['confidence']:.2f}, {s['observation_count']} obs)")
        return

    if args.search:
        results = search_preferences(args.search)
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            if not results:
                print(f"🔍 No preferences matching '{args.search}'")
            else:
                print(f"🔍 {len(results)} result(s) for '{args.search}':")
                for r in results:
                    print(f"   - {r['key']}: {r['value']} (conf={r['confidence']:.2f})")
        return

    if args.resolve:
        result = resolve_contradiction(args.resolve, args.resolution, args.keep_value)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"✅ Contradiction {args.resolve} resolved.")
        return

    if args.health:
        report = get_health_report()
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            dims = report["dimensions"]
            metrics = report["metrics"]
            print(f"🩺 User Model Health Report")
            print(f"   Overall Score:  {report['total_score']}/100 [{report['grade']}]")
            print(f"")
            print(f"   📊 Dimensions:")
            print(f"   Data Density:            {dims['data_density']:>5.1f}/25")
            print(f"   Contradiction Health:    {dims['contradiction_health']:>5.1f}/25")
            print(f"   Confidence Distribution: {dims['confidence_distribution']:>5.1f}/20")
            print(f"   Data Freshness:          {dims['data_freshness']:>5.1f}/15")
            print(f"   Evidence Coverage:       {dims['evidence_coverage']:>5.1f}/10")
            print(f"   DB Integrity:            {dims['db_integrity']:>5.1f}/5")
            print(f"")
            print(f"   📈 Metrics:")
            print(f"   Preferences:     {metrics['total_preferences']}")
            print(f"   Choices:         {metrics['total_choices']}")
            print(f"   Evidence:        {metrics['total_evidence']}")
            print(f"   Contradictions:  {metrics['unresolved_contradictions']}")
            print(f"   High/Med/Stale:  {metrics['high_confidence']}/{metrics['medium_confidence']}/{metrics['stale']}")
            print(f"   Avg Update Age:  {metrics['avg_days_since_update']} days")
            print(f"   Evidence Cov:    {metrics['evidence_coverage_pct']}%")
            print(f"   FTS Healthy:     {'✅' if metrics['fts_healthy'] else '❌'}")
            if report["recommendations"]:
                print(f"")
                print(f"   💡 Recommendations:")
                for rec in report["recommendations"]:
                    print(f"   - {rec}")
        return

    if args.decay or args.decay_dry_run:
        dry_run = args.decay_dry_run or not args.decay
        if not dry_run:
            print("⚠️  Applying Ebbinghaus decay to all preferences...")
        else:
            print("🔍 Preview: Ebbinghaus decay (dry run)...")
        
        result = apply_ebbinghaus_decay(dry_run=dry_run)
        
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"   Total preferences:  {result['total_preferences']}")
            print(f"   Updated:            {result['updated']}")
            print(f"   Skipped:            {result['skipped']}")
            print(f"   Total decay:        {result['total_decay']:.4f}")
            if result["details"]:
                print(f"\n   Top decays:")
                for d in result["details"][:10]:
                    print(f"   - {d['key']}={d['value']}: {d['old_confidence']:.4f} → {d['new_confidence']:.4f} "
                          f"({d['days']}d, {d['observations']}obs, R={d['retention_rate']:.3f})")
            if dry_run:
                print(f"\n   💡 Dry run complete. Use --decay to apply changes.")
            else:
                print(f"\n   ✅ Decay applied.")
        return

    if args.seed_intents:
        result = seed_intents()
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"🌱 Intent seeds: {result['seeded']} new, {result['skipped']} skipped")
        return

    if args.list_intents:
        intents = list_intents()
        if args.json:
            print(json.dumps(intents, ensure_ascii=False, indent=2))
        else:
            print(f"🎯 Intent Patterns ({len(intents)}):")
            for i in intents:
                status = "🟢" if i["confidence"] >= 0.7 else "🟡"
                print(f"   {status} {i['intent_name']} (conf={i['confidence']:.2f}, "
                      f"hits={i['hit_count']}, misses={i['miss_count']})")
        return

    if args.export_markdown:
        md = export_to_markdown()
        print(md)
        return

    if args.rebuild_fts:
        rebuild_fts()
        print("✅ FTS5 index rebuilt.")
        return

    # Default: show status
    status = get_model_status()
    print(f"📊 User Model: {status['total_preferences']} preferences, "
          f"{status['unresolved_contradictions']} contradictions")
    print(f"   Use --status for details, --check for contradiction scan.")


if __name__ == "__main__":
    main()
