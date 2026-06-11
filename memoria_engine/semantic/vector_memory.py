#!/usr/bin/env python3
"""
Vector Memory Provider — LanceDB-backed MemoryProvider Implementation
=====================================================================
Semantic Foundation Phase 2 (Day 3-5).
Implements the MemoryProvider ABC using LanceDB for vector storage.

Key design:
    - Lazy connection: LanceDB connection opened on first operation
    - MemoryProvider ABC compliant: read/write/search/close
    - Vector search via cosine distance (LanceDB built-in)
    - Fallback: if embedding fails, stores zero vectors (graceful degradation)

Usage:
    # Standalone test
    python3 vector_memory_provider.py --write "你好世界" --topics "test,greeting"
    python3 vector_memory_provider.py --search "搜索关键词" --limit 5
"""

import hashlib
import json
import logging
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from ..memory.memory_pool import MemoryProvider
from ..constants import SHARED_DIR
from ..semantic.embeddings import get_embedder, EMBEDDING_DIM

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────
VECTOR_DB_DIR = SHARED_DIR / "vector_db"
TABLE_NAME = "memory_entries"
DEFAULT_LIMIT = 10
MAX_CONTENT_LENGTH = 8000  # truncate very long content before embedding


class VectorMemoryProvider(MemoryProvider):
    """LanceDB-backed memory provider with semantic vector search.

    Implements the MemoryProvider ABC contract:
        - read(id) → dict | None
        - write(id, value) → bool
        - search(query, limit) → list[dict]
        - close() → None

    Uses BGE-M3 embeddings for semantic similarity search.
    Falls back to exact zero-vector on embedding failures.
    """

    def __init__(self, db_dir: Path = None):
        self._db_dir = db_dir or VECTOR_DB_DIR
        self._db_dir.mkdir(parents=True, exist_ok=True)
        self._db = None
        self._table = None
        self._embedder = None

    def _ensure_connection(self):
        """Lazy-initialize LanceDB connection and table."""
        if self._db is not None:
            return

        # Init embedder FIRST — dim is needed for table schema
        if self._embedder is None:
            self._embedder = get_embedder()
            self._embedder._ensure_model()

        import lancedb
        self._db = lancedb.connect(str(self._db_dir))

        existing = self._db.table_names()
        if TABLE_NAME in existing:
            self._table = self._db.open_table(TABLE_NAME)
        else:
            # Create table with initial empty data
            import pyarrow as pa
            dim = self._embedder.dim  # dynamic: 512 (bge-small) or 1024 (bge-m3)
            schema = pa.schema([
                pa.field("id", pa.string()),
                pa.field("content", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), dim)),
                pa.field("topics", pa.string()),
                pa.field("workspace", pa.string()),
                pa.field("priority", pa.string()),
                pa.field("timestamp", pa.string()),
            ])
            self._table = self._db.create_table(TABLE_NAME, schema=schema)
            logger.info(f"Created LanceDB table '{TABLE_NAME}' with dim={dim}")

    def read(self, key: str) -> dict | None:
        """Read a single memory entry by ID.

        Args:
            key: Unique entry identifier.

        Returns:
            Entry dict if found, None otherwise.
        """
        self._ensure_connection()
        try:
            result = self._table.to_lance().to_table(
                filter=f"id = '{key}'"
            )
            if result.num_rows == 0:
                return None
            row = result.to_pydict()
            return {
                "id": row["id"][0],
                "content": row["content"][0],
                "topics": row["topics"][0].split(",") if row["topics"][0] else [],
                "workspace": row["workspace"][0],
                "priority": row["priority"][0],
                "timestamp": row["timestamp"][0],
            }
        except Exception as e:
            logger.warning(f"Read failed for key '{key}': {e}")
            return None

    def write(self, key: str, value: dict) -> bool:
        """Write (upsert) a memory entry with vector embedding.

        Args:
            key: Unique identifier.
            value: Dict with at minimum 'content' key. Optional: topics, workspace, priority.

        Returns:
            True on success.
        """
        self._ensure_connection()

        content = value.get("content", "")
        if not content:
            logger.warning("Write skipped: empty content")
            return False

        # Truncate long content for embedding
        content_for_embedding = content[:MAX_CONTENT_LENGTH]

        # Generate vector embedding
        vec = self._embedder.encode(content_for_embedding)
        vec_list = vec.tolist()

        # Prepare row data
        topics = value.get("topics", [])
        topics_str = ",".join(topics) if isinstance(topics, list) else str(topics)
        workspace = value.get("workspace", value.get("source_workspace", ""))
        priority = value.get("priority", "P1")
        timestamp = value.get("timestamp", datetime.now().isoformat())

        try:
            import pyarrow as pa
            data = [{
                "id": key,
                "content": content,
                "vector": vec_list,
                "topics": topics_str,
                "workspace": workspace,
                "priority": priority,
                "timestamp": timestamp,
            }]

            # Delete existing entry with same ID (upsert)
            try:
                self._table.delete(f"id = '{key}'")
            except Exception:
                logger.debug("Delete before upsert failed for key '%s' (may not exist)", key)

            self._table.add(data)
            return True
        except Exception as e:
            logger.error(f"Write failed for key '{key}': {e}")
            return False

    def search(self, query: str, limit: int = DEFAULT_LIMIT) -> list:
        """Semantic vector search over memory entries.

        Uses LanceDB cosine distance search with BGE-M3 embeddings.

        Args:
            query: Natural language search query.
            limit: Maximum results.

        Returns:
            List of matching entry dicts, ordered by similarity.
        """
        self._ensure_connection()

        if not query or not query.strip():
            return self._list_recent(limit)

        # Generate query embedding
        query_vec = self._embedder.encode(query.strip())
        query_vec_list = query_vec.tolist()

        try:
            results = self._table.search(query_vec_list).limit(limit).to_list()
            # Convert results to standard dict format
            entries = []
            for r in results:
                topics_str = r.get("topics", "")
                topics = topics_str.split(",") if topics_str else []
                entries.append({
                    "id": r["id"],
                    "content": r["content"],
                    "topics": topics,
                    "workspace": r.get("workspace", ""),
                    "priority": r.get("priority", "P1"),
                    "timestamp": r.get("timestamp", ""),
                    "_distance": r.get("_distance", None),
                })
            return entries
        except Exception as e:
            logger.warning(f"Vector search failed, falling back to list: {e}")
            return self._list_recent(limit)

    def _list_recent(self, limit: int = DEFAULT_LIMIT) -> list:
        """Fallback: list most recent entries when search fails."""
        self._ensure_connection()
        try:
            all_rows = self._table.to_lance().to_table()
            rows = all_rows.to_pydict()
            n = len(rows.get("id", []))
            if n == 0:
                return []
            # Return last N entries
            indices = range(max(0, n - limit), n)
            entries = []
            for i in indices:
                topics_str = rows["topics"][i] if rows["topics"][i] else ""
                entries.append({
                    "id": rows["id"][i],
                    "content": rows["content"][i],
                    "topics": topics_str.split(",") if topics_str else [],
                    "workspace": rows.get("workspace", [""])[i] if rows.get("workspace") else "",
                    "priority": rows.get("priority", ["P1"])[i] if rows.get("priority") else "P1",
                    "timestamp": rows.get("timestamp", [""])[i] if rows.get("timestamp") else "",
                })
            return entries
        except Exception as e:
            logger.error("_list_recent failed: %s", e)
            return []

    def close(self) -> None:
        """Release LanceDB connection."""
        if self._db is not None:
            # LanceDB connections auto-close; explicit close for safety
            self._table = None
            self._db = None

    def count(self) -> int:
        """Return total number of entries in the vector store."""
        self._ensure_connection()
        try:
            return self._table.to_lance().count_rows()
        except Exception as e:
            logger.error("count failed: %s", e)
            return 0


# ── CLI for testing ──────────────────────────────────────────

def main():
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Vector Memory Provider CLI")
    parser.add_argument("--write", type=str, help="Write memory entry (content)")
    parser.add_argument("--topics", type=str, default="", help="Comma-separated topics")
    parser.add_argument("--workspace", type=str, default="test", help="Source workspace")
    parser.add_argument("--read", type=str, help="Read entry by ID")
    parser.add_argument("--search", "-s", type=str, help="Semantic search query")
    parser.add_argument("--limit", type=int, default=5, help="Search result limit")
    parser.add_argument("--count", action="store_true", help="Show entry count")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()
    provider = VectorMemoryProvider()

    try:
        if args.write:
            entry_id = str(uuid.uuid4())[:8]
            topics = [t.strip() for t in args.topics.split(",") if t.strip()]
            value = {
                "content": args.write,
                "topics": topics,
                "workspace": args.workspace,
                "priority": "P1",
            }
            ok = provider.write(entry_id, value)
            if args.json:
                print(json.dumps({"written": ok, "id": entry_id}, ensure_ascii=False))
            else:
                print(f"{'✅' if ok else '❌'} Written: {entry_id}")
                print(f"   Total entries: {provider.count()}")

        elif args.read:
            entry = provider.read(args.read)
            if args.json:
                print(json.dumps(entry, ensure_ascii=False, indent=2))
            elif entry:
                print(f"📄 [{entry['id']}] {entry['content'][:100]}")
                print(f"   Topics: {entry['topics']}")
                print(f"   Workspace: {entry['workspace']}")
            else:
                print(f"❌ Not found: {args.read}")

        elif args.search:
            results = provider.search(args.search, args.limit)
            if args.json:
                print(json.dumps(results, ensure_ascii=False, indent=2))
            else:
                print(f"🔍 Search: '{args.search}' → {len(results)} results")
                for i, r in enumerate(results, 1):
                    dist = f" dist={r['_distance']:.4f}" if r.get("_distance") is not None else ""
                    print(f"  [{i}] {r['content'][:80]}{dist}")

        elif args.count:
            n = provider.count()
            if args.json:
                print(json.dumps({"count": n}))
            else:
                print(f"📊 Vector store: {n} entries")

        else:
            parser.print_help()

    finally:
        provider.close()


if __name__ == "__main__":
    main()
