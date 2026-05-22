#!/usr/bin/env python3
"""
Cross-Workspace Session Recovery Tool
======================================
Recovers conversation history across all WorkBuddy workspaces.

Combines three data sources:
1. workbuddy.db sessions table (structured session records)
2. .workbuddy/memory/YYYY-MM-DD.md (daily work logs)
3. FTS5 session indexes (full-text searchable content)

Usage:
    # List all sessions across workspaces
    python3 session_recovery.py --list

    # Search for sessions by keyword
    python3 session_recovery.py --search "Hermes"

    # Detail view of a specific session workspace
    python3 session_recovery.py --workspace /path/to/workspace

    # JSON output (for tool integration)
    python3 session_recovery.py --list --json
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Constants ────────────────────────────────────────────────

from ..constants import WORKBUDDY_DIR, WORKBUDDY_ROOT, WORKBUDDY_DB_PATH
CST = timezone(timedelta(hours=8))


# ── Session DB ────────────────────────────────────────────────

def query_sessions() -> list:
    """Query all sessions from workbuddy.db."""
    if not WORKBUDDY_DB_PATH.exists():
        return []
    
    sessions = []
    try:
        conn = sqlite3.connect(str(WORKBUDDY_DB_PATH))
        cursor = conn.execute(
            "SELECT id, cwd, title, status, created_at, updated_at "
            "FROM sessions WHERE deleted_at IS NULL "
            "ORDER BY created_at DESC"
        )
        for row in cursor:
            sessions.append({
                "id": row[0],
                "cwd": row[1],
                "title": row[2],
                "status": row[3],
                "created_at_ms": row[4],
                "updated_at_ms": row[5],
                "created_at": datetime.fromtimestamp(row[4] / 1000, tz=CST).strftime("%Y-%m-%d %H:%M"),
                "date": datetime.fromtimestamp(row[4] / 1000, tz=CST).strftime("%Y-%m-%d"),
                "source": "session_db",
            })
        conn.close()
    except Exception as e:
        pass
    
    return sessions


# ── Memory Files ────────────────────────────────────────────────

def find_memory_entries() -> list:
    """Scan all workspace memory files for daily logs."""
    entries = []
    
    if not WORKBUDDY_ROOT.exists():
        return entries
    
    for ws_dir in sorted(WORKBUDDY_ROOT.iterdir(), reverse=True):
        memory_dir = ws_dir / ".workbuddy" / "memory"
        if not memory_dir.exists():
            continue
        
        # Find daily files
        daily_files = sorted(
            memory_dir.glob("202[0-9]-[0-9][0-9]-[0-9][0-9].md"),
            reverse=True
        )
        
        for df in daily_files[:7]:  # Last 7 days
            try:
                content = df.read_text(encoding="utf-8")
                # Extract sections (## headings)
                sections = re.findall(r'^##\s+(.+)$', content, re.MULTILINE)
                first_line = content.strip().split('\n')[0] if content else ""
                
                date_str = df.stem
                
                entries.append({
                    "workspace": str(ws_dir),
                    "workspace_name": ws_dir.name,
                    "date": date_str,
                    "file": str(df),
                    "size": len(content),
                    "sections": sections[:10],  # First 10 sections
                    "title": first_line.replace("# ", ""),
                    "source": "memory_file",
                })
            except Exception:
                continue
    
    return entries


# ── Merge & Deduplicate ────────────────────────────────────────

def merge_sources(sessions: list, memories: list) -> list:
    """Merge session DB and memory file data into unified timeline."""
    timeline = []
    
    # Add sessions
    for s in sessions:
        timeline.append({
            "type": "session",
            "date": s["date"],
            "datetime": s["created_at"],
            "workspace": s["cwd"],
            "workspace_name": Path(s["cwd"]).name,
            "title": s["title"],
            "status": s["status"],
            "session_id": s["id"],
            "source": s["source"],
        })
    
    # Add memory entries
    for m in memories:
        # Check if this date already has a session entry for the same workspace
        has_session = any(
            t["type"] == "session" 
            and t["workspace"] == m["workspace"] 
            and t["date"] == m["date"]
            for t in timeline
        )
        
        timeline.append({
            "type": "memory_log",
            "date": m["date"],
            "datetime": m["date"],
            "workspace": m["workspace"],
            "workspace_name": m["workspace_name"],
            "title": m["title"],
            "sections": m["sections"],
            "file_size": m["size"],
            "status": "archived" if not has_session else "synced",
            "source": m["source"],
        })
    
    # Sort by date descending
    timeline.sort(key=lambda x: x["date"], reverse=True)
    
    return timeline


# ── Display ────────────────────────────────────────────────────

def display_timeline(timeline: list, as_json: bool = False):
    """Display unified conversation timeline."""
    if as_json:
        print(json.dumps(timeline, indent=2, ensure_ascii=False))
        return
    
    if not timeline:
        print("📭 无历史会话记录")
        return
    
    print(f"\n📋 跨工作区会话历史（{len(timeline)} 条记录）\n")
    
    current_date = None
    for entry in timeline:
        entry_date = entry["date"]
        
        # Date separator
        if entry_date != current_date:
            current_date = entry_date
            print(f"\n{'─' * 70}")
            print(f"  📅 {entry_date}")
            print(f"{'─' * 70}")
        
        # Entry type icon
        icon = "💬" if entry["type"] == "session" else "📝"
        status_icon = {
            "working": "🟢",
            "Completed": "⚪",
            "archived": "📦",
            "synced": "🔗",
            "Pending": "🟡",
        }.get(entry.get("status", ""), "❓")
        
        ws_name = entry["workspace_name"]
        title = entry["title"][:60] + "..." if len(entry.get("title", "")) > 60 else entry.get("title", "")
        
        print(f"  {icon} {status_icon} [{ws_name}] {title}")
        
        # Show sections for memory logs
        if entry["type"] == "memory_log" and entry.get("sections"):
            for sec in entry["sections"][:3]:
                print(f"        └─ {sec}")
    
    print(f"\n{'─' * 70}")
    print(f"\n💡 恢复会话方法：在 WorkBuddy 中打开对应工作区后执行 /resume")
    print(f"   或使用 /resume <session-id> 直接恢复（限当前工作区）")


def display_workspace_detail(workspace_path: str):
    """Display detailed view of a specific workspace's history."""
    ws = Path(workspace_path)
    if not ws.exists():
        print(f"❌ 工作区不存在: {workspace_path}")
        return
    
    memory_dir = ws / ".workbuddy" / "memory"
    if not memory_dir.exists():
        print(f"📭 该工作区无历史记录")
        return
    
    print(f"\n📂 {ws.name}")
    print(f"{'─' * 50}")
    
    # Daily files
    daily_files = sorted(memory_dir.glob("202[0-9]-*.md"), reverse=True)
    for df in daily_files:
        try:
            content = df.read_text(encoding="utf-8")
            sections = re.findall(r'^##\s+(.+)$', content, re.MULTILINE)
            print(f"\n  📅 {df.stem} ({len(content)} 字符)")
            for sec in sections:
                print(f"     ├─ {sec}")
        except Exception:
            continue
    
    # MEMORY.md
    memory_md = memory_dir / "MEMORY.md"
    if memory_md.exists():
        content = memory_md.read_text(encoding="utf-8")
        print(f"\n  🧠 MEMORY.md ({len(content)} 字符)")


# ── Main ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Cross-Workspace Session Recovery Tool"
    )
    parser.add_argument("--list", action="store_true", help="List all sessions across workspaces")
    parser.add_argument("--search", type=str, help="Search sessions by keyword")
    parser.add_argument("--workspace", "-w", type=str, help="Show detail for a specific workspace")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--days", type=int, default=30, help="Days of history to show (default: 30)")
    
    args = parser.parse_args()
    
    # Collect data
    sessions = query_sessions()
    memories = find_memory_entries()
    timeline = merge_sources(sessions, memories)
    
    # Filter by days
    cutoff = (datetime.now(CST) - timedelta(days=args.days)).strftime("%Y-%m-%d")
    timeline = [t for t in timeline if t["date"] >= cutoff]
    
    # Filter by search
    if args.search:
        keyword = args.search.lower()
        timeline = [
            t for t in timeline 
            if keyword in t["title"].lower() 
            or keyword in t["workspace_name"].lower()
            or (t["type"] == "memory_log" and any(
                keyword in s.lower() for s in t.get("sections", [])
            ))
        ]
    
    # Display
    if args.workspace:
        display_workspace_detail(args.workspace)
    else:
        display_timeline(timeline, as_json=args.json)


if __name__ == "__main__":
    main()
