#!/usr/bin/env python3
"""
Intent Embedding Cache — 意图嵌入预计算与毫秒级语义搜索
==========================================================
Phase 2: Intent Preload 语义增强引擎。

预计算所有 intent_patterns 的 BGE-M3 嵌入向量，存入 LanceDB。
运行时无需加载 BGE-M3 模型（2s→<5ms），实现毫秒级语义搜索兜底。

Architecture:
    Build:  Load BGE-M3 once → encode 12 intents → store in LanceDB
    Search: Load LanceDB → encode query (single call) → cosine top-k
    Status: Check LanceDB table existence + row count

LanceDB table schema:
    intent_name: str       — e.g. "stock_deep_research"
    intent_id: str         — e.g. "intent_stock_deep_research"
    keywords_text: str     — space-joined keyword string for reference
    vector: list[float]    — 1024-dim BGE-M3 dense vector
    context_bundle: str    — JSON-serialized context bundle
    base_confidence: float — original confidence score

Usage:
    # Build index (one-time or after keyword changes)
    python3 intent_embedder.py --build --json

    # Search (fast, no model load needed)
    python3 intent_embedder.py --search "查询文本" --top 3 --json

    # Status check
    python3 intent_embedder.py --status --json
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import lancedb
import numpy as np
import pyarrow as pa

from ..constants import WORKBUDDY_DIR
from ..models.user_model import _get_db

# ── Config ────────────────────────────────────────────────────

LANCE_DB_DIR = os.path.join(WORKBUDDY_DIR, "lancedb")
TABLE_NAME = "intent_embeddings"
EMBEDDING_DIM = 1024  # BGE-M3 dense output dimension


# ── Build ─────────────────────────────────────────────────────

def build_index(db_path: str = None, force: bool = False) -> dict:
    """Build or rebuild the LanceDB intent embedding index.

    Loads BGE-M3 once, encodes all intent patterns, stores in LanceDB.

    Args:
        db_path: Path to user_model.db (default from constants)
        force: If True, drop existing table and rebuild.

    Returns:
        dict with status, count, timing info.
    """
    db_path = db_path or os.path.join(WORKBUDDY_DIR, "user_model.db")

    # Load intents from SQLite
    conn = _get_db()
    rows = conn.execute(
        "SELECT intent_name, id, pattern_keywords, confidence, context_bundle "
        "FROM intent_patterns ORDER BY id"
    ).fetchall()
    conn.close()

    if not rows:
        return {"status": "error", "reason": "no_intents_found", "count": 0}

    intent_count = len(rows)

    # Build pattern texts
    pattern_texts = []
    intent_data = []
    for row in rows:
        keywords = [k.strip() for k in row["pattern_keywords"].split(",") if k.strip()]
        pattern_text = " ".join(keywords)
        pattern_texts.append(pattern_text)
        intent_data.append({
            "intent_name": row["intent_name"],
            "intent_id": row["id"],
            "keywords_text": pattern_text,
            "context_bundle": row["context_bundle"] or "{}",
            "base_confidence": row["confidence"] or 0.5,
        })

    # Load BGE-M3 and encode
    start = time.time()
    try:
        from ..semantic.embeddings import get_embedder
        embedder = get_embedder()
        vectors = embedder.encode_batch(pattern_texts)
        encode_time = time.time() - start
    except Exception as e:
        return {
            "status": "error",
            "reason": f"encode_failed: {e}",
            "count": intent_count,
        }

    # Validate dimensions
    dim = vectors.shape[1]
    if dim == 0:
        return {"status": "error", "reason": "zero_dim_vectors", "count": 0}

    # Open LanceDB and create/overwrite table
    os.makedirs(LANCE_DB_DIR, exist_ok=True)
    db = lancedb.connect(LANCE_DB_DIR)

    # Drop existing if force
    if force and TABLE_NAME in db.table_names():
        db.drop_table(TABLE_NAME)

    if TABLE_NAME in db.table_names():
        db.drop_table(TABLE_NAME)

    # Build PyArrow table
    records = []
    for i, data in enumerate(intent_data):
        vec = vectors[i].tolist()
        records.append({
            "intent_name": data["intent_name"],
            "intent_id": data["intent_id"],
            "keywords_text": data["keywords_text"],
            "vector": vec,
            "context_bundle": data["context_bundle"],
            "base_confidence": float(data["base_confidence"]),
        })

    # Create table
    db.create_table(TABLE_NAME, records)

    total_time = time.time() - start
    return {
        "status": "ok",
        "intents": intent_count,
        "dim": dim,
        "encode_time_s": round(encode_time, 2),
        "total_time_s": round(total_time, 2),
        "path": os.path.join(LANCE_DB_DIR, f"{TABLE_NAME}.lance"),
    }


# ── Search ────────────────────────────────────────────────────

def search_intents(query: str, top_k: int = 3) -> dict:
    """Search intent embeddings by query text using cosine similarity.

    Encodes the query with BGE-M3, then searches LanceDB for top-k matches.
    This is MUCH faster than the per-query semantic mode in intent_learner.py
    because the model is loaded once and reused, not reloaded per query.

    However, even better: for real-time preloading, we use this only as a
    fallback when keyword matching confidence is low. The typical flow:
        1. keyword mode (<30ms) → if conf >= 0.15, done
        2. LanceDB semantic (<50ms) → encode + search
    Total: typically <30ms for high-confidence, <80ms for fallback.

    Args:
        query: User's natural language query.
        top_k: Number of top matches to return (default 3).

    Returns:
        dict with matches (list of {intent_name, similarity, ...})
    """
    db_path = os.path.join(LANCE_DB_DIR, f"{TABLE_NAME}.lance")
    if not os.path.exists(db_path):
        return {"status": "error", "reason": "index_not_built", "matches": []}

    db = lancedb.connect(LANCE_DB_DIR)
    table = db.open_table(TABLE_NAME)

    # Encode query
    try:
        from ..semantic.embeddings import get_embedder
        embedder = get_embedder()
        query_vec = embedder.encode(query)
    except Exception as e:
        return {"status": "error", "reason": f"encode_failed: {e}", "matches": []}

    if np.linalg.norm(query_vec) == 0:
        return {"status": "error", "reason": "zero_query_vector", "matches": []}

    # Search LanceDB using cosine distance
    try:
        results = (
            table.search(query_vec.tolist())
            .metric("cosine")
            .limit(top_k)
            .to_list()
        )
    except Exception as e:
        return {"status": "error", "reason": f"search_failed: {e}", "matches": []}

    # Convert distance to similarity (cosine distance ∈ [0, 2])
    matches = []
    for r in results:
        distance = r.get("_distance", 0.0)
        similarity = max(0.0, 1.0 - distance / 2.0)  # normalize to [0, 1]
        context_bundle = {}
        try:
            context_bundle = json.loads(r.get("context_bundle", "{}"))
        except (json.JSONDecodeError, TypeError):
            pass

        matches.append({
            "intent_name": r.get("intent_name"),
            "intent_id": r.get("intent_id"),
            "similarity": round(similarity, 4),
            "distance": round(distance, 4),
            "base_confidence": r.get("base_confidence", 0.5),
            "context_bundle": context_bundle,
            "keywords_text": r.get("keywords_text", ""),
        })

    return {
        "status": "ok",
        "query": query,
        "matches": matches,
    }


# ── Status ────────────────────────────────────────────────────

def check_status() -> dict:
    """Check if the intent embedding index exists and is healthy."""
    db_path = os.path.join(LANCE_DB_DIR, f"{TABLE_NAME}.lance")
    if not os.path.exists(db_path):
        return {
            "status": "not_built",
            "table_exists": False,
            "row_count": 0,
            "path": db_path,
        }

    try:
        db = lancedb.connect(LANCE_DB_DIR)
        table = db.open_table(TABLE_NAME)
        rows = table.to_lance().count_rows()
        return {
            "status": "ok",
            "table_exists": True,
            "row_count": rows,
            "path": db_path,
        }
    except Exception as e:
        return {
            "status": "error",
            "table_exists": True,
            "row_count": 0,
            "error": str(e),
            "path": db_path,
        }


# ── CLI ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Intent Embedding Cache — Pre-computed BGE-M3 embeddings for intent patterns"
    )
    parser.add_argument("--build", action="store_true",
                        help="Build/rebuild the LanceDB index")
    parser.add_argument("--force", action="store_true",
                        help="Force rebuild (drop existing table)")
    parser.add_argument("--search", type=str,
                        help="Search intents by query text")
    parser.add_argument("--top", type=int, default=3,
                        help="Number of top results for search (default: 3)")
    parser.add_argument("--status", action="store_true",
                        help="Check index status")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    parser.add_argument("--db", type=str, default=None,
                        help="Path to user_model.db (default from constants)")

    args = parser.parse_args()

    if args.build:
        result = build_index(db_path=args.db, force=args.force)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            if result["status"] == "ok":
                print(f"✅ Built intent embedding index: {result['intents']} intents, "
                      f"{result['dim']}d, encode={result['encode_time_s']}s, "
                      f"total={result['total_time_s']}s")
            else:
                print(f"❌ Build failed: {result.get('reason', 'unknown')}")
        return

    if args.search:
        result = search_intents(args.search, top_k=args.top)
        if args.json:
            # Clean up for JSON output
            clean = {"status": result["status"], "query": result["query"], "matches": result["matches"]}
            if "reason" in result:
                clean["reason"] = result["reason"]
            print(json.dumps(clean, ensure_ascii=False, indent=2))
        else:
            if result["status"] != "ok":
                print(f"❌ Search failed: {result.get('reason', 'unknown')}")
            elif not result["matches"]:
                print("⚪ No matches found")
            else:
                for i, m in enumerate(result["matches"], 1):
                    print(f"  [{i}] {m['intent_name']}: "
                          f"sim={m['similarity']:.4f}, dist={m['distance']:.4f}")
        return

    if args.status:
        result = check_status()
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            icon = "✅" if result["status"] == "ok" else "❌" if result["status"] == "error" else "⚪"
            print(f"{icon} Intent embedding index: {result['status']}")
            if result.get("row_count"):
                print(f"   Rows: {result['row_count']}")
            if result.get("path"):
                print(f"   Path: {result['path']}")
            if result.get("error"):
                print(f"   Error: {result['error']}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
