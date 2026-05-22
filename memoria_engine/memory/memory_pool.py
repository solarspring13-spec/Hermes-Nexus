#!/usr/bin/env python3
"""
Shared Memory Pool for Hermes WorkBuddy
=========================================
Phase 3D: Multi-Agent Shared Memory Pool — 智能路由池

Manages a cross-workspace shared memory pool at {MEMORIA_HOME}
Supports CRUD, conflict detection (content_hash + topic overlap), and compaction.

Usage:
    # Add a memory entry
    python3 memory_pool.py --add --content "..." --topics "topic1,topic2" --workspace <cwd>

    # Search shared pool
    python3 memory_pool.py --search "keywords" --limit 10

    # Compact pool (dedup + remove P2)
    python3 memory_pool.py --compact

    # Show pool status
    python3 memory_pool.py --status
"""

import argparse
import hashlib
import json
import os
import sys
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path


# ── Constants ────────────────────────────────────────────────

from ..constants import SHARED_DIR, ROUTE_LOG_PATH

POOL_PATH = SHARED_DIR / "pool.json"
MAX_POOL_ENTRIES = 500


# ── MemoryProvider Abstract Base Class ────────────────────────

class MemoryProvider(ABC):
    """Abstract base class for memory storage providers.

    Defines the contract that all memory backends must implement.
    Enables pluggable storage: JSON files, SQLite, remote APIs, etc.
    """

    @abstractmethod
    def read(self, key: str) -> dict | None:
        """Read a single memory entry by key.

        Args:
            key: Unique identifier for the memory entry.

        Returns:
            Entry dict if found, None otherwise.
        """
        ...

    @abstractmethod
    def write(self, key: str, value: dict) -> bool:
        """Write (create or update) a memory entry.

        Args:
            key: Unique identifier for the memory entry.
            value: Entry data dict.

        Returns:
            True on success, False on failure.
        """
        ...

    @abstractmethod
    def search(self, query: str, limit: int = 10) -> list:
        """Search memory entries by query.

        Args:
            query: Search keywords string.
            limit: Maximum number of results.

        Returns:
            List of matching entry dicts, ordered by relevance.
        """
        ...

    @abstractmethod
    def close(self) -> None:
        """Release any held resources (connections, file handles, etc.)."""
        ...


class JSONPoolMemoryProvider(MemoryProvider):
    """JSON file-backed memory provider implementing the MemoryProvider interface.

    Wraps the existing pool.json CRUD operations behind the ABC contract.
    This is the default provider for the shared memory pool.
    """

    def __init__(self, pool_path: Path = None):
        self._pool_path = pool_path or POOL_PATH

    def read(self, key: str) -> dict | None:
        """Read a memory entry by its ID."""
        pool = _load_pool()
        for entry in pool:
            if entry.get("id") == key:
                return entry
        return None

    def write(self, key: str, value: dict) -> bool:
        """Write a memory entry (upsert by ID)."""
        pool = _load_pool()
        value["id"] = key
        value.setdefault("timestamp", datetime.now().isoformat())
        value.setdefault("content_hash", _hash_content(value.get("content", "")))

        # Upsert: replace existing or append
        for i, entry in enumerate(pool):
            if entry.get("id") == key:
                pool[i] = value
                _save_pool(pool)
                return True

        pool.append(value)
        _save_pool(pool)
        return True

    def search(self, query: str, limit: int = 10) -> list:
        """Search the pool by keywords (delegates to pool_search)."""
        return pool_search(query, limit=limit)

    def close(self) -> None:
        """No-op for JSON file backend (no persistent connection)."""
        pass


# ── Helpers ───────────────────────────────────────────────────

def _ensure_dir():
    SHARED_DIR.mkdir(parents=True, exist_ok=True)


def _load_pool() -> list:
    """Load pool.json, return list of entries."""
    _ensure_dir()
    if not POOL_PATH.exists():
        return []
    try:
        with open(POOL_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def _save_pool(pool: list):
    """Save pool.json atomically."""
    _ensure_dir()
    tmp = POOL_PATH.with_suffix(".tmp")
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)
    os.replace(tmp, POOL_PATH)


def _hash_content(content: str) -> str:
    """SHA256 hash of content string."""
    return hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]


def _topic_overlap(topics_a: list, topics_b: list) -> float:
    """Calculate topic overlap ratio (Jaccard)."""
    if not topics_a or not topics_b:
        return 0.0
    set_a = set(t.lower() for t in topics_a)
    set_b = set(t.lower() for t in topics_b)
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


# ── Core Operations ──────────────────────────────────────────

def pool_add(workspace: str, content: str, topics: list = None,
             priority: str = "P1") -> dict:
    """Add an entry to the shared memory pool.

    Returns result dict with status and any conflict info.
    """
    pool = _load_pool()
    content_hash = _hash_content(content)
    topics = topics or []

    # Check for exact duplicate by content_hash
    for entry in pool:
        if entry.get("content_hash") == content_hash:
            return {
                "added": False,
                "reason": "duplicate",
                "existing_id": entry["id"],
                "message": "Exact content duplicate found, skipped",
            }

    # Check for topic conflicts (>70% overlap)
    conflicts = []
    for entry in pool:
        overlap = _topic_overlap(topics, entry.get("topics", []))
        if overlap > 0.7:
            conflicts.append({
                "existing_id": entry["id"],
                "existing_topics": entry["topics"],
                "overlap_ratio": round(overlap, 2),
            })

    # Create new entry
    entry = {
        "id": str(uuid.uuid4())[:8],
        "source_workspace": Path(workspace).name if workspace else "unknown",
        "content": content,
        "topics": topics,
        "content_hash": content_hash,
        "timestamp": datetime.now().isoformat(),
        "priority": priority.upper(),
    }

    pool.append(entry)
    _save_pool(pool)

    result = {
        "added": True,
        "entry_id": entry["id"],
        "pool_size": len(pool),
    }
    if conflicts:
        result["conflicts"] = conflicts[:3]  # Report top 3
        result["warning"] = f"Topic overlap detected with {len(conflicts)} existing entries"

    # Log route
    _log_route("add", entry["id"], workspace, topics)

    return result


def pool_search(keywords: str, limit: int = 10) -> list:
    """Search shared pool by keywords (simple substring + topic matching)."""
    pool = _load_pool()
    if not pool or not keywords:
        return pool[-limit:] if limit > 0 else pool

    kw_set = set(keywords.lower().split())
    scored = []

    for entry in pool:
        content_lower = entry.get("content", "").lower()
        topics_lower = [t.lower() for t in entry.get("topics", [])]

        # Score: keyword hits in content + topic matches
        content_hits = sum(1 for kw in kw_set if kw in content_lower)
        topic_hits = sum(1 for kw in kw_set if any(kw in t for t in topics_lower))
        score = content_hits * 2 + topic_hits * 3  # Topic match weighted higher

        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in scored[:limit]]


def pool_compact() -> dict:
    """Compact the pool: remove P2 entries, deduplicate, enforce max size."""
    pool = _load_pool()
    original_size = len(pool)

    # Remove P2 entries (keep P0 and P1)
    pool = [e for e in pool if e.get("priority", "P1") in ("P0", "P1")]
    removed_p2 = original_size - len(pool)

    # Enforce max size: remove oldest entries
    if len(pool) > MAX_POOL_ENTRIES:
        pool.sort(key=lambda e: e.get("timestamp", ""))
        overflow = len(pool) - MAX_POOL_ENTRIES
        pool = pool[overflow:]
    else:
        overflow = 0

    _save_pool(pool)

    return {
        "compacted": True,
        "original_size": original_size,
        "new_size": len(pool),
        "removed_p2": removed_p2,
        "overflow_removed": overflow,
    }


def pool_status() -> dict:
    """Get pool statistics."""
    pool = _load_pool()
    if not pool:
        return {"entries": 0, "workspaces": 0, "priorities": {}, "oldest": None, "newest": None}

    workspaces = set()
    priorities = {}
    for entry in pool:
        workspaces.add(entry.get("source_workspace", "unknown"))
        p = entry.get("priority", "P1")
        priorities[p] = priorities.get(p, 0) + 1

    timestamps = [e.get("timestamp", "") for e in pool if e.get("timestamp")]
    timestamps.sort()

    return {
        "entries": len(pool),
        "workspaces": len(workspaces),
        "workspace_list": sorted(workspaces),
        "priorities": priorities,
        "oldest": timestamps[0] if timestamps else None,
        "newest": timestamps[-1] if timestamps else None,
        "max_entries": MAX_POOL_ENTRIES,
    }


# ── Route Logging ─────────────────────────────────────────────

def _log_route(action: str, entry_id: str, workspace: str, topics: list):
    """Append a routing event to route_log.json."""
    _ensure_dir()
    logs = []
    if ROUTE_LOG_PATH.exists():
        try:
            with open(ROUTE_LOG_PATH, 'r', encoding='utf-8') as f:
                logs = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            logs = []

    logs.append({
        "action": action,
        "entry_id": entry_id,
        "workspace": Path(workspace).name if workspace else "unknown",
        "topics": topics,
        "timestamp": datetime.now().isoformat(),
    })

    # Keep only last 200 log entries
    if len(logs) > 200:
        logs = logs[-200:]

    tmp = ROUTE_LOG_PATH.with_suffix(".tmp")
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ROUTE_LOG_PATH)


# ── CLI ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Shared Memory Pool for WorkBuddy (Phase 3D)"
    )
    parser.add_argument("--add", action="store_true",
                        help="Add a memory entry to the shared pool")
    parser.add_argument("--content", type=str,
                        help="Memory content (for --add)")
    parser.add_argument("--topics", type=str, default="",
                        help="Comma-separated topics (for --add)")
    parser.add_argument("--workspace", "-w", type=str, default="",
                        help="Source workspace path")
    parser.add_argument("--priority", type=str, default="P1",
                        choices=["P0", "P1", "P2"],
                        help="Priority: P0 (critical), P1 (keep), P2 (discardable)")
    parser.add_argument("--search", type=str,
                        help="Search shared pool by keywords")
    parser.add_argument("--limit", type=int, default=10,
                        help="Max search results")
    parser.add_argument("--compact", action="store_true",
                        help="Compact pool (dedup + remove P2 + enforce max size)")
    parser.add_argument("--status", action="store_true",
                        help="Show pool statistics")
    parser.add_argument("--provider", type=str, default="json",
                        choices=["json", "vector"],
                        help="Memory backend: json (default) or vector (LanceDB)")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")

    args = parser.parse_args()

    # ── Provider selection ──────────────────────────────────
    if args.provider == "vector":
        try:
            from ..semantic.vector_memory import VectorMemoryProvider, VECTOR_DB_DIR
            provider = VectorMemoryProvider()
        except ImportError as e:
            print(f"ERROR: Cannot load vector provider: {e}", file=sys.stderr)
            print("Ensure lancedb, FlagEmbedding are installed (see requirements_semantic.txt)",
                  file=sys.stderr)
            sys.exit(1)
    else:
        provider = None  # use default JSON operations below
        VECTOR_DB_DIR = None  # not used

    if args.add:
        if not args.content:
            print("ERROR: --content is required with --add", file=sys.stderr)
            sys.exit(1)
        topics = [t.strip() for t in args.topics.split(",") if t.strip()]

        if provider:
            # Vector backend
            import uuid as _uuid
            entry_key = str(_uuid.uuid4())[:8]
            ok = provider.write(entry_key, {
                "content": args.content,
                "topics": topics,
                "workspace": args.workspace,
                "priority": args.priority,
            })
            result = {"added": ok, "entry_id": entry_key if ok else None}
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                if ok:
                    print(f"✅ Added to vector store: {result['entry_id']}")
                    print(f"   Total entries: {provider.count()}")
                else:
                    print("❌ Write failed")
            return

        result = pool_add(
            workspace=args.workspace,
            content=args.content,
            topics=topics,
            priority=args.priority,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            if result["added"]:
                print(f"✅ Added to shared pool: {result['entry_id']}")
                print(f"   Pool size: {result['pool_size']}")
                if result.get("conflicts"):
                    print(f"   ⚠️  Topic overlap with {len(result['conflicts'])} entries")
            else:
                print(f"⏭️  Skipped: {result['message']}")
        return

    if args.search:
        if provider:
            # Vector backend: semantic search
            results = provider.search(args.search, limit=args.limit)
        else:
            results = pool_search(args.search, limit=args.limit)
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            if not results:
                print("🔍 No results in shared pool.")
            else:
                print(f"🔍 Found {len(results)} result(s) in shared pool:\n")
                for i, entry in enumerate(results, 1):
                    topics = ", ".join(entry.get("topics", []))
                    print(f"  [{i}] [{entry.get('priority', 'P1')}] {entry['content'][:80]}")
                    print(f"      Source: {entry.get('source_workspace', '?')} | Topics: {topics}")
                    print()
        return

    if args.compact:
        if provider:
            # Vector backend: no compact yet, just show count
            n = provider.count()
            result = {"compacted": True, "original_size": n, "new_size": n, "removed_p2": 0, "overflow_removed": 0}
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"🧹 Vector store: {n} entries (compact not yet implemented)")
            return
        result = pool_compact()
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"🧹 Pool compacted: {result['original_size']} → {result['new_size']}")
            if result["removed_p2"]:
                print(f"   Removed {result['removed_p2']} P2 entries")
            if result["overflow_removed"]:
                print(f"   Removed {result['overflow_removed']} oldest entries (overflow)")
        return

    if args.status:
        if provider:
            n = provider.count()
            status = {"entries": n, "workspaces": 0, "priorities": {}, "oldest": None, "newest": None, "provider": "vector"}
            if args.json:
                print(json.dumps(status, ensure_ascii=False, indent=2))
            else:
                print(f"📊 Vector Memory Store (LanceDB)")
                print(f"   Entries:     {n}")
                print(f"   Backend:     LanceDB @ {VECTOR_DB_DIR}")
            return
        status = pool_status()
        if args.json:
            print(json.dumps(status, ensure_ascii=False, indent=2))
        else:
            print(f"📊 Shared Memory Pool Status")
            print(f"   Entries:     {status['entries']}/{status.get('max_entries', '?')}")
            print(f"   Workspaces:  {status.get('workspaces', 0)}")
            if status.get("priorities"):
                pp = status["priorities"]
                print(f"   Priorities:  P0={pp.get('P0', 0)} P1={pp.get('P1', 0)} P2={pp.get('P2', 0)}")
            if status.get("newest"):
                print(f"   Newest:      {status['newest'][:19]}")
        return

    # Default: show status
    status = pool_status()
    print(f"📊 Shared Pool: {status['entries']} entries across {status.get('workspaces', 0)} workspaces")
    print(f"   Use --status for details, --search to query, --add to contribute.")


if __name__ == "__main__":
    main()
