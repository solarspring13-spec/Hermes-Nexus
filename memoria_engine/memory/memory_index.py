#!/usr/bin/env python3
"""
FTS5 Session Search Index for WorkBuddy
========================================
Inspired by Hermes Agent's FTS5 session search with LLM summarization.

Creates and maintains a SQLite FTS5 index of daily memory logs,
enabling fast cross-session context retrieval.

Usage:
    # Index the workspace's daily logs
    python3 memory_index.py --workspace /path/to/workspace

    # Search across sessions
    python3 memory_index.py --search "宁德时代 电池技术" --workspace /path/to/workspace --limit 5

    # Show index status
    python3 memory_index.py --status --workspace /path/to/workspace

    # Rebuild index from scratch
    python3 memory_index.py --rebuild --workspace /path/to/workspace

    # Build/search global cross-workspace index (Tier 1+ unified)
    python3 memory_index.py --global --rebuild
    python3 memory_index.py --global --search "投资 尽调" --limit 10
    python3 memory_index.py --global --status
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional


# ── Constants ────────────────────────────────────────────────

DB_NAME = "session_index.db"
GLOBAL_DB_NAME = "global_index.db"
SHARED_DB_NAME = "shared_index.db"

from ..constants import (
    SHARED_DIR, WORKBUDDY_DIR, WORKBUDDY_ROOT,
    MEMORY_DIR, DAILY_LOG_PATTERN, DB_PATH,
)

# CJK Unicode ranges for character detection
CJK_RANGES = [
    (0x4E00, 0x9FFF),    # CJK Unified Ideographs
    (0x3400, 0x4DBF),    # CJK Unified Ideographs Extension A
    (0x20000, 0x2A6DF),  # CJK Unified Ideographs Extension B
    (0x2A700, 0x2B73F),  # CJK Unified Ideographs Extension C
    (0x2B740, 0x2B81F),  # CJK Unified Ideographs Extension D
    (0x2B820, 0x2CEAF),  # CJK Unified Ideographs Extension E
    (0xF900, 0xFAFF),    # CJK Compatibility Ideographs
    (0x2F800, 0x2FA1F),  # CJK Compatibility Ideographs Supplement
    (0x3000, 0x303F),    # CJK Symbols and Punctuation
    (0xFF00, 0xFFEF),    # Halfwidth and Fullwidth Forms
    (0x3040, 0x309F),    # Hiragana
    (0x30A0, 0x30FF),    # Katakana
    (0xAC00, 0xD7AF),    # Hangul Syllables
]


def is_cjk(char: str) -> bool:
    """Check if a character is in CJK Unicode ranges."""
    cp = ord(char)
    return any(lo <= cp <= hi for lo, hi in CJK_RANGES)


def segment_for_fts(text: str) -> str:
    """Insert spaces around CJK characters for FTS5 tokenization.

    FTS5's unicode61 tokenizer doesn't split CJK characters into separate
    tokens. By inserting spaces between CJK characters, we make each character
    individually searchable, and phrases work via FTS5's phrase matching.
    """
    result = []
    prev_is_cjk = False

    for char in text:
        curr_is_cjk = is_cjk(char)

        if curr_is_cjk:
            # Space before CJK character if previous was non-CJK non-space
            if not prev_is_cjk and result and result[-1] != ' ':
                result.append(' ')
            result.append(char)
            result.append(' ')  # Space after each CJK char
        else:
            result.append(char)

        prev_is_cjk = curr_is_cjk

    # Clean up: collapse multiple spaces
    segmented = ''.join(result)
    segmented = re.sub(r' {2,}', ' ', segmented)
    return segmented.strip()


def prepare_search_query(query: str) -> str:
    """Prepare a user search query for FTS5.

    If query contains CJK characters, segment them for FTS5 phrase matching.
    Also wraps multi-word queries in quotes for phrase search.
    """
    has_cjk = any(is_cjk(c) for c in query)

    if has_cjk:
        # Segment CJK characters with spaces
        segmented = segment_for_fts(query)
        # Remove extra spaces
        terms = segmented.split()
        if len(terms) > 1:
            # Wrap in quotes for phrase matching
            return '"' + ' '.join(terms) + '"'
        else:
            return terms[0] if terms else query
    else:
        # For pure ASCII queries, use as-is (FTS5 handles word boundaries)
        return query


# ── Database Setup ───────────────────────────────────────────

def get_db_path(workspace: str) -> Path:
    """Get or create the FTS5 database path inside the workspace's .workbuddy dir."""
    db_dir = Path(workspace) / ".workbuddy"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / DB_NAME


def init_db(db_path: Path) -> sqlite3.Connection:
    """Initialize the FTS5 database with schema if not exists."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    # Sessions metadata table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            workspace TEXT NOT NULL,
            file_path TEXT NOT NULL,
            char_count INTEGER DEFAULT 0,
            indexed_at TEXT NOT NULL
        )
    """)

    # FTS5 virtual table for full-text search
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS session_fts USING fts5(
            session_id UNINDEXED,
            date,
            topic,
            content,
            decisions,
            tokenize='porter unicode61'
        )
    """)

    conn.commit()
    return conn


# ── Content Extraction ───────────────────────────────────────

def parse_memory_file(filepath: Path) -> dict:
    """Parse a daily memory log into structured fields."""
    if not filepath.exists():
        return {}

    content = filepath.read_text(encoding="utf-8")
    if not content.strip():
        return {}

    # Extract date from filename
    date_match = DAILY_LOG_PATTERN.match(filepath.name)
    date_str = date_match.group(1) if date_match else "unknown"

    # Try to extract topic (first meaningful heading after the date title)
    topic = ""
    headings = re.findall(r"^#{1,3}\s+(.+)$", content, re.MULTILINE)
    for h in headings:
        h = h.strip()
        # Skip if it looks like a date heading
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", h) and len(h) > 3:
            topic = h
            break
    if not topic and headings:
        topic = headings[0].strip()

    # Extract decisions (lines with decision keywords)
    decisions = []
    decision_keywords = ["决定", "采用", "选择", "使用", "确认", "设定", "配置为",
                         "decided", "chose", "configured", "set up"]
    for line in content.split("\n"):
        line_stripped = line.strip()
        for kw in decision_keywords:
            if kw in line_stripped and len(line_stripped) > 10:
                decisions.append(line_stripped)
                break

    return {
        "date": date_str,
        "topic": topic,
        "content": content,
        "decisions": " | ".join(decisions) if decisions else "",
        "char_count": len(content),
    }


def index_file(conn: sqlite3.Connection, filepath: Path, workspace: str) -> bool:
    """Index a single memory file into FTS5."""
    parsed = parse_memory_file(filepath)
    if not parsed:
        return False

    date_str = parsed["date"]

    # Check if already indexed (and unchanged)
    cursor = conn.execute(
        "SELECT id, char_count FROM sessions WHERE date = ? AND workspace = ?",
        (date_str, workspace)
    )
    existing = cursor.fetchone()

    if existing and existing[1] == parsed["char_count"]:
        return False  # Unchanged, skip

    # Upsert session record
    if existing:
        conn.execute(
            "UPDATE sessions SET char_count = ?, indexed_at = ? WHERE id = ?",
            (parsed["char_count"], datetime.now().isoformat(), existing[0])
        )
        session_id = existing[0]
        # Remove old FTS entry
        conn.execute("DELETE FROM session_fts WHERE session_id = ?", (str(session_id),))
    else:
        cursor = conn.execute(
            "INSERT INTO sessions (date, workspace, file_path, char_count, indexed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (date_str, workspace, str(filepath), parsed["char_count"],
             datetime.now().isoformat())
        )
        session_id = cursor.lastrowid

    # Insert into FTS (use segmented content for CJK tokenization)
    segmented_content = segment_for_fts(parsed["content"])
    segmented_topic = segment_for_fts(parsed["topic"])
    segmented_decisions = segment_for_fts(parsed["decisions"])

    conn.execute(
        "INSERT INTO session_fts (session_id, date, topic, content, decisions) "
        "VALUES (?, ?, ?, ?, ?)",
        (str(session_id), date_str, segmented_topic, segmented_content, segmented_decisions)
    )

    return True


def index_all(workspace: str, rebuild: bool = False) -> dict:
    """Index all daily memory logs in the workspace."""
    memory_dir = Path(workspace) / MEMORY_DIR
    db_path = get_db_path(workspace)

    if rebuild and db_path.exists():
        db_path.unlink()

    conn = init_db(db_path)

    if not memory_dir.exists():
        conn.close()
        return {"indexed": 0, "skipped": 0, "total": 0, "message": "No memory directory found"}

    files = sorted(memory_dir.glob("????-??-??.md"))
    indexed = 0
    skipped = 0

    for f in files:
        if index_file(conn, f, workspace):
            indexed += 1
        else:
            skipped += 1

    conn.commit()
    conn.close()

    return {
        "indexed": indexed,
        "skipped": skipped,
        "total": len(files),
        "db_path": str(db_path),
        "message": f"Indexed {indexed} new/updated, skipped {skipped} unchanged out of {len(files)} files"
    }


# ── Search ────────────────────────────────────────────────────

def search_sessions(workspace: str, query: str, limit: int = 5) -> list:
    """Search across indexed sessions using FTS5 with CJK-aware querying."""
    db_path = get_db_path(workspace)

    if not db_path.exists():
        return [{"error": "No index found. Run with --workspace first to build index."}]

    conn = sqlite3.connect(str(db_path))
    prepared_query = prepare_search_query(query)
    has_cjk = any(is_cjk(c) for c in query)

    # Try FTS5 search first
    try:
        cursor = conn.execute(
            """
            SELECT
                session_id,
                date,
                topic,
                snippet(session_fts, -1, '<mark>', '</mark>', '...', 60) as preview,
                decisions,
                length(content) as content_len
            FROM session_fts
            WHERE session_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (prepared_query, limit)
        )
        results = list(cursor.fetchall())
    except sqlite3.OperationalError as e:
        results = []

    # FTS5 fallback: if no results and query contains CJK, try individual character matching
    if not results and has_cjk:
        try:
            # Match any of the CJK characters individually
            cjk_chars = [c for c in query if is_cjk(c)]
            if cjk_chars:
                # Use OR of individual characters
                or_query = " OR ".join(cjk_chars)
                cursor = conn.execute(
                    """
                    SELECT
                        session_id,
                        date,
                        topic,
                        snippet(session_fts, -1, '<mark>', '</mark>', '...', 60) as preview,
                        decisions,
                        length(content) as content_len
                    FROM session_fts
                    WHERE session_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (or_query, limit)
                )
                results = list(cursor.fetchall())
        except sqlite3.OperationalError:
            pass

    conn.close()

    if not results:
        return []

    formatted = []
    for row in results:
        formatted.append({
            "session_id": row[0],
            "date": row[1],
            "topic": row[2] or "(无标题)",
            "preview": row[3],
            "decisions": row[4] or "",
            "content_length": row[5],
        })

    return formatted


def build_shared_index() -> dict:
    """Build FTS5 index for the shared memory pool (Phase 3D).

    Creates shared_index.db in {MEMORIA_HOME} with FTS5 table
    indexing pool.json entries by id, content, and topics.
    """
    pool_path = SHARED_DIR / "pool.json"
    db_path = SHARED_DIR / SHARED_DB_NAME

    if not pool_path.exists():
        return {"indexed": 0, "message": "No pool.json found", "db_path": str(db_path)}

    try:
        with open(pool_path, 'r', encoding='utf-8') as f:
            pool = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {"indexed": 0, "message": "Failed to read pool.json", "db_path": str(db_path)}

    SHARED_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))

    # Drop and recreate FTS5 table
    conn.execute("DROP TABLE IF EXISTS shared_fts")
    conn.execute("""
        CREATE VIRTUAL TABLE shared_fts USING fts5(
            entry_id,
            content,
            topics,
            priority,
            source_workspace,
            tokenize='unicode61'
        )
    """)

    indexed = 0
    for entry in pool:
        entry_id = entry.get("id", "")
        content = entry.get("content", "")
        topics = " ".join(entry.get("topics", []))
        priority = entry.get("priority", "P1")
        source_ws = entry.get("source_workspace", "")

        # Tokenize CJK content for better search
        tokenized_content = segment_for_fts(content)

        conn.execute(
            "INSERT INTO shared_fts VALUES (?, ?, ?, ?, ?)",
            (entry_id, tokenized_content, topics, priority, source_ws)
        )
        indexed += 1

    conn.commit()
    conn.close()

    return {
        "indexed": indexed,
        "total_pool_entries": len(pool),
        "db_path": str(db_path),
        "message": f"Indexed {indexed}/{len(pool)} shared pool entries",
    }


# ── Global Cross-Workspace Index ────────────────────────────────

def discover_workspaces() -> list:
    """Discover all workspace directories under {WORKSPACES_ROOT} that have memory logs."""
    workspaces = []
    if not WORKBUDDY_ROOT.exists():
        return workspaces

    for item in sorted(WORKBUDDY_ROOT.iterdir()):
        if item.is_dir():
            memory_path = item / MEMORY_DIR
            if memory_path.exists() and any(memory_path.glob("*.md")):
                workspaces.append(str(item))
    return workspaces


def get_global_db_path() -> Path:
    """Get path to the global unified index database."""
    WORKBUDDY_DIR.mkdir(parents=True, exist_ok=True)
    return WORKBUDDY_DIR / GLOBAL_DB_NAME


def init_global_db(db_path: Path) -> sqlite3.Connection:
    """Initialize the global FTS5 database with workspace-aware schema."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS global_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            workspace TEXT NOT NULL,
            workspace_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            char_count INTEGER DEFAULT 0,
            indexed_at TEXT NOT NULL,
            UNIQUE(date, workspace)
        )
    """)

    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS global_fts USING fts5(
            session_id UNINDEXED,
            workspace_name,
            date,
            topic,
            content,
            decisions,
            tokenize='porter unicode61'
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS context_cache (
            cache_key TEXT PRIMARY KEY,
            query TEXT NOT NULL,
            context_hash TEXT NOT NULL,
            result_json TEXT,
            created_at TEXT NOT NULL,
            ttl_seconds INTEGER DEFAULT 3600
        )
    """)

    conn.commit()
    return conn


def index_all_workspaces(rebuild: bool = False) -> dict:
    """Index all workspace memory logs into a unified global index."""
    db_path = get_global_db_path()

    if rebuild and db_path.exists():
        db_path.unlink()

    conn = init_global_db(db_path)
    workspaces = discover_workspaces()

    total_indexed = 0
    total_skipped = 0
    workspace_stats = []

    for ws_path in workspaces:
        ws_name = Path(ws_path).name
        memory_dir = Path(ws_path) / MEMORY_DIR
        files = sorted(memory_dir.glob("????-??-??.md"))

        ws_indexed = 0
        ws_skipped = 0

        for f in files:
            parsed = parse_memory_file(f)
            if not parsed:
                continue

            date_str = parsed["date"]

            # Check if already indexed and unchanged
            cursor = conn.execute(
                "SELECT id, char_count FROM global_sessions WHERE date = ? AND workspace = ?",
                (date_str, ws_path)
            )
            existing = cursor.fetchone()

            if existing and existing[1] == parsed["char_count"]:
                ws_skipped += 1
                continue

            # Upsert session record
            if existing:
                conn.execute(
                    "UPDATE global_sessions SET char_count = ?, indexed_at = ? WHERE id = ?",
                    (parsed["char_count"], datetime.now().isoformat(), existing[0])
                )
                session_id = existing[0]
                conn.execute("DELETE FROM global_fts WHERE session_id = ?", (str(session_id),))
            else:
                cursor = conn.execute(
                    "INSERT INTO global_sessions (date, workspace, workspace_name, file_path, char_count, indexed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (date_str, ws_path, ws_name, str(f), parsed["char_count"],
                     datetime.now().isoformat())
                )
                session_id = cursor.lastrowid

            # Insert into FTS
            segmented_content = segment_for_fts(parsed["content"])
            segmented_topic = segment_for_fts(parsed["topic"])
            segmented_decisions = segment_for_fts(parsed["decisions"])

            conn.execute(
                "INSERT INTO global_fts (session_id, workspace_name, date, topic, content, decisions) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (str(session_id), ws_name, date_str, segmented_topic, segmented_content, segmented_decisions)
            )
            ws_indexed += 1

        conn.commit()
        total_indexed += ws_indexed
        total_skipped += ws_skipped
        if ws_indexed > 0 or ws_skipped > 0:
            workspace_stats.append({
                "workspace": ws_name,
                "indexed": ws_indexed,
                "skipped": ws_skipped,
                "total": len(files),
            })

    conn.close()

    return {
        "indexed": total_indexed,
        "skipped": total_skipped,
        "total_workspaces": len(workspaces),
        "db_path": str(db_path),
        "workspaces": workspace_stats,
        "message": f"Global index: {total_indexed} new/updated, {total_skipped} skipped across {len(workspaces)} workspaces"
    }


def search_global(query: str, limit: int = 5, use_cache: bool = True) -> list:
    """Search across all workspaces using the global FTS5 index."""
    db_path = get_global_db_path()

    if not db_path.exists():
        return [{"error": "Global index not built. Run with --global first."}]

    # Check context cache if enabled
    if use_cache:
        cached = _check_context_cache(db_path, f"search:{query}_limit={limit}")
        if cached is not None:
            return cached

    conn = sqlite3.connect(str(db_path))
    prepared_query = prepare_search_query(query)
    has_cjk = any(is_cjk(c) for c in query)

    # Try FTS5 search
    results = []
    try:
        cursor = conn.execute(
            """
            SELECT
                session_id,
                workspace_name,
                date,
                topic,
                snippet(global_fts, -1, '<mark>', '</mark>', '...', 60) as preview,
                decisions,
                length(content) as content_len
            FROM global_fts
            WHERE global_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (prepared_query, limit)
        )
        results = list(cursor.fetchall())
    except sqlite3.OperationalError:
        pass

    # CJK fallback: individual character matching
    if not results and has_cjk:
        try:
            cjk_chars = [c for c in query if is_cjk(c)]
            if cjk_chars:
                or_query = " OR ".join(cjk_chars)
                cursor = conn.execute(
                    """
                    SELECT
                        session_id,
                        workspace_name,
                        date,
                        topic,
                        snippet(global_fts, -1, '<mark>', '</mark>', '...', 60) as preview,
                        decisions,
                        length(content) as content_len
                    FROM global_fts
                    WHERE global_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (or_query, limit)
                )
                results = list(cursor.fetchall())
        except sqlite3.OperationalError:
            pass

    conn.close()

    if not results:
        return []

    formatted = []
    for row in results:
        formatted.append({
            "session_id": row[0],
            "workspace": row[1],
            "date": row[2],
            "topic": row[3] or "(无标题)",
            "preview": row[4],
            "decisions": row[5] or "",
            "content_length": row[6],
        })

    # Store in cache if enabled
    if use_cache:
        _set_context_cache(db_path, f"search:{query}_limit={limit}", formatted)

    return formatted


def _hash_context(query: str) -> str:
    """Generate SHA256 hash key for context cache."""
    return hashlib.sha256(query.encode('utf-8')).hexdigest()[:32]


def _check_context_cache(db_path: Path, query: str) -> list | None:
    """Check context cache for a previous result.

    Returns cached result if valid (within TTL), None otherwise.
    Stale entries are cleaned on check.
    """
    cache_key = _hash_context(query)
    try:
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT result_json, created_at, ttl_seconds FROM context_cache WHERE cache_key = ?",
            (cache_key,)
        ).fetchone()
        conn.close()

        if row is None:
            return None

        result_json, created_at, ttl_seconds = row
        created_dt = datetime.fromisoformat(created_at)
        age_seconds = (datetime.now() - created_dt).total_seconds()

        if age_seconds > ttl_seconds:
            # Stale — clean up
            conn = sqlite3.connect(str(db_path))
            conn.execute("DELETE FROM context_cache WHERE cache_key = ?", (cache_key,))
            conn.commit()
            conn.close()
            return None

        return json.loads(result_json)
    except Exception:
        return None


def _set_context_cache(db_path: Path, query: str, result: list, ttl_seconds: int = 3600):
    """Store a result in the context cache."""
    if not result:
        return  # Don't cache empty results
    cache_key = _hash_context(query)
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """INSERT OR REPLACE INTO context_cache
               (cache_key, query, context_hash, result_json, created_at, ttl_seconds)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (cache_key, query, cache_key,
             json.dumps(result, ensure_ascii=False),
             datetime.now().isoformat(), ttl_seconds)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _cleanup_context_cache(db_path: Path) -> int:
    """Remove all stale cache entries. Returns count of removed entries."""
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute(
            "DELETE FROM context_cache WHERE "
            "datetime(created_at, '+' || ttl_seconds || ' seconds') < datetime('now')"
        )
        removed = cursor.rowcount
        conn.commit()
        conn.close()
        return removed
    except Exception:
        return 0


def get_recent_global(days: int = 7, limit: int = 10, use_cache: bool = True) -> list:
    """Get recent memory entries across all workspaces from the global index.

    Returns most recent entries ordered by date DESC, then indexed_at DESC.
    Uses the global_sessions metadata table (not FTS) for efficient date-range scan.
    """
    db_path = get_global_db_path()

    if not db_path.exists():
        return [{"error": "Global index not built. Run with --global first."}]

    # Check context cache if enabled
    if use_cache:
        cache_context = f"recent_days={days}_limit={limit}"
        cached = _check_context_cache(db_path, cache_context)
        if cached is not None:
            return cached

    conn = sqlite3.connect(str(db_path))

    cursor = conn.execute(
        """
        SELECT
            gs.id as session_id,
            gs.workspace_name,
            gs.date,
            COALESCE(
                (SELECT topic FROM global_fts WHERE session_id = CAST(gs.id AS TEXT)),
                ''
            ) as topic,
            COALESCE(
                (SELECT decisions FROM global_fts WHERE session_id = CAST(gs.id AS TEXT)),
                ''
            ) as decisions,
            gs.char_count as content_length
        FROM global_sessions gs
        WHERE gs.date >= date('now', ?)
        ORDER BY gs.date DESC, gs.indexed_at DESC
        LIMIT ?
        """,
        (f'-{days} days', limit)
    )
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return []

    formatted = []
    for row in rows:
        formatted.append({
            "session_id": row[0],
            "workspace": row[1],
            "date": row[2],
            "topic": row[3] or "(无标题)",
            "decisions": row[4] or "",
            "content_length": row[5],
        })

    # Store in cache if enabled
    if use_cache:
        _set_context_cache(db_path, f"recent_days={days}_limit={limit}", formatted)

    return formatted


def get_global_status() -> dict:
    """Get global index statistics."""
    db_path = get_global_db_path()

    if not db_path.exists():
        return {"status": "not_initialized", "message": "Global index not yet created"}

    conn = sqlite3.connect(str(db_path))

    total = conn.execute("SELECT COUNT(*) FROM global_sessions").fetchone()[0]
    total_fts = conn.execute("SELECT COUNT(*) FROM global_fts").fetchone()[0]
    workspaces = conn.execute(
        "SELECT COUNT(DISTINCT workspace) FROM global_sessions"
    ).fetchone()[0]
    latest = conn.execute(
        "SELECT date, workspace_name, indexed_at FROM global_sessions ORDER BY date DESC LIMIT 1"
    ).fetchone()

    db_size = db_path.stat().st_size if db_path.exists() else 0

    conn.close()

    return {
        "status": "active",
        "total_sessions": total,
        "total_fts_entries": total_fts,
        "total_workspaces": workspaces,
        "latest_date": latest[0] if latest else None,
        "latest_workspace": latest[1] if latest else None,
        "last_indexed": latest[2] if latest else None,
        "db_size_bytes": db_size,
        "db_size_mb": round(db_size / (1024 * 1024), 2),
        "db_path": str(db_path),
    }


def get_index_status(workspace: str) -> dict:
    """Get index statistics."""
    db_path = get_db_path(workspace)

    if not db_path.exists():
        return {"status": "not_initialized", "message": "Index not yet created"}

    conn = sqlite3.connect(str(db_path))

    total = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    total_fts = conn.execute("SELECT COUNT(*) FROM session_fts").fetchone()[0]
    latest = conn.execute(
        "SELECT date, indexed_at FROM sessions ORDER BY date DESC LIMIT 1"
    ).fetchone()

    # Get total indexed characters
    total_chars = conn.execute(
        "SELECT COALESCE(SUM(char_count), 0) FROM sessions"
    ).fetchone()[0]

    db_size = db_path.stat().st_size if db_path.exists() else 0

    conn.close()

    return {
        "status": "active",
        "total_sessions": total,
        "total_fts_entries": total_fts,
        "latest_date": latest[0] if latest else None,
        "last_indexed": latest[1] if latest else None,
        "total_chars_indexed": total_chars,
        "db_size_bytes": db_size,
        "db_size_mb": round(db_size / (1024 * 1024), 2),
        "db_path": str(db_path),
    }


# ── CLI ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="FTS5 Session Search Index for WorkBuddy (Hermes-inspired)"
    )
    parser.add_argument("--workspace", "-w",
                        help="WorkBuddy workspace path (required unless --global)")
    parser.add_argument("--search", "-s", type=str,
                        help="Search query for cross-session recall")
    parser.add_argument("--recent", "-r", type=int, default=None, metavar="DAYS",
                        help="Get recent memory entries from last N days (requires --global, overrides --search)")
    parser.add_argument("--limit", "-l", type=int, default=5,
                        help="Max search results (default: 5)")
    parser.add_argument("--rebuild", action="store_true",
                        help="Rebuild index from scratch")
    parser.add_argument("--status", action="store_true",
                        help="Show index status")
    parser.add_argument("--shared", action="store_true",
                        help="Index the shared memory pool (Phase 3D)")
    parser.add_argument("--global", dest="global_mode", action="store_true",
                        help="Operate on global cross-workspace index")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    parser.add_argument("--no-cache", action="store_true",
                        help="Bypass context cache (force fresh search)")

    args = parser.parse_args()

    # Validate: --workspace or --global must be provided
    if not args.workspace and not args.global_mode and not args.shared:
        parser.error("either --workspace or --global (or --shared) is required")

    # ── Global mode ──
    if args.global_mode:
        use_cache = not args.no_cache
        if args.status:
            status = get_global_status()
            if args.json:
                print(json.dumps(status, ensure_ascii=False, indent=2))
            else:
                print(f"🌐 Global FTS5 Index Status")
                print(f"   Status:         {status['status']}")
                print(f"   Workspaces:     {status.get('total_workspaces', 0)}")
                print(f"   Sessions:       {status.get('total_sessions', 0)}")
                print(f"   Latest:         {status.get('latest_date', 'N/A')} ({status.get('latest_workspace', '')})")
                print(f"   DB Size:        {status.get('db_size_mb', 0)} MB")
                print(f"   Path:           {status.get('db_path', 'N/A')}")
            return

        if args.recent is not None:
            results = get_recent_global(days=args.recent, limit=args.limit, use_cache=use_cache)
            if args.json:
                print(json.dumps(results, ensure_ascii=False, indent=2))
            else:
                if not results:
                    print(f"📅 No recent entries found (last {args.recent} days).")
                elif isinstance(results[0], dict) and "error" in results[0]:
                    print(f"❌ {results[0]['error']}")
                else:
                    print(f"📅 Recent memory entries (last {args.recent} days, {len(results)} results):\n")
                    for i, r in enumerate(results, 1):
                        print(f"  [{i}] {r['workspace']} / {r['date']} — {r['topic']}")
                        if r.get('decisions'):
                            print(f"      📋 {r['decisions'][:120]}...")
                        print()
            return

        if args.search:
            results = search_global(args.search, args.limit, use_cache=use_cache)
            if args.json:
                print(json.dumps(results, ensure_ascii=False, indent=2))
            else:
                if not results:
                    print("🔍 No results found across workspaces.")
                elif isinstance(results[0], dict) and "error" in results[0]:
                    print(f"❌ {results[0]['error']}")
                else:
                    print(f"🌐 Found {len(results)} relevant sessions across workspaces:\n")
                    for i, r in enumerate(results, 1):
                        print(f"  [{i}] {r['workspace']} / {r['date']} — {r['topic']}")
                        print(f"      {r['preview']}")
                        if r.get('decisions'):
                            print(f"      📋 Decisions: {r['decisions'][:100]}...")
                        print()
            return

        # Index (default for --global)
        result = index_all_workspaces(rebuild=args.rebuild)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"🌐 {result['message']}")
            print(f"   DB: {result.get('db_path', 'N/A')}")
            if result.get('workspaces'):
                for ws in result['workspaces']:
                    print(f"   📁 {ws['workspace']}: {ws['indexed']} indexed, {ws['skipped']} skipped")
        return

    # Show status
    if args.status:
        status = get_index_status(args.workspace)
        if args.json:
            print(json.dumps(status, ensure_ascii=False, indent=2))
        else:
            print(f"📊 FTS5 Index Status")
            print(f"   Status:    {status['status']}")
            print(f"   Sessions:  {status.get('total_sessions', 0)}")
            print(f"   Latest:    {status.get('latest_date', 'N/A')}")
            print(f"   DB Size:   {status.get('db_size_mb', 0)} MB")
            print(f"   Path:      {status.get('db_path', 'N/A')}")
        return

    # Search
    if args.search:
        results = search_sessions(args.workspace, args.search, args.limit)
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            if not results:
                print("🔍 No results found.")
            elif "error" in results[0]:
                print(f"❌ {results[0]['error']}")
            else:
                print(f"🔍 Found {len(results)} relevant sessions:\n")
                for i, r in enumerate(results, 1):
                    print(f"  [{i}] {r['date']} — {r['topic']}")
                    print(f"      {r['preview']}")
                    if r['decisions']:
                        print(f"      📋 Decisions: {r['decisions'][:100]}...")
                    print()
        return

    # Index shared memory pool (Phase 3D)
    if args.shared:
        result = build_shared_index()
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"📑 Shared pool indexed: {result['indexed']} entries")
            print(f"   DB: {result.get('db_path', 'N/A')}")
        return

    # Index (default)
    result = index_all(args.workspace, rebuild=args.rebuild)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"📑 {result['message']}")
        print(f"   DB: {result.get('db_path', 'N/A')}")


if __name__ == "__main__":
    main()
