# 🏛️ Architecture

The Memoria Engine implements a 3-tier memory architecture inspired by the Greek messenger god Hermes — guardian of boundaries, travelers, and memory.

```
┌─────────────────────────────────────────────────────────┐
│                    MEMORIA ENGINE                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │
│  │  L0 INSTANT │→│ L1 SHORT    │→│  L2 LONG-TERM    │  │
│  │  Session    │  │ Daily Logs  │  │  Curated Memory  │  │
│  │  State      │  │ 30-day win  │  │  Persistent      │  │
│  └─────────────┘  └─────────────┘  └─────────────────┘  │
│         ↑ distill       ↑ compress        ↑              │
│         └───────────────┴─────────────────┘              │
│                   AUTOMATIC LIFECYCLE                     │
└─────────────────────────────────────────────────────────┘
```

## L0 — Instant Memory (`session_state.json`)

**What it is**: Session-level working memory, machine-readable.

**Captures in real-time**:
- Active plans and current phase
- Recent decisions with rationale
- Discovered facts
- Open questions
- Pending and completed tasks

**Lifecycle**:
1. Auto-initialized at session start
2. Updated throughout the session
3. Distilled → L1 at session end
4. Archived after distillation

## L1 — Short-Term Memory (`YYYY-MM-DD.md`)

**What it is**: Daily logs, human-readable Markdown.

**Contains**:
- Completed work summaries
- Key decisions made
- Environment changes
- File modifications

**Lifecycle**:
- Generated from L0 distillation
- Manual appends supported
- Retained for **30 days**
- Then compressed → L2

## L2 — Long-Term Memory (`MEMORY.md`)

**What it is**: Curated, compressed, persistent knowledge.

**Contains**:
- Project conventions and preferences
- Recurring facts
- Architectural decisions
- User profile data (`USER.md`)

**Compression**: When L2 exceeds capacity, entries are triaged:
- **P0**: Keep always (critical preferences, security)
- **P1**: Merge/summarize (useful context)
- **P2**: Discard (transient, outdated)

## Cross-Cutting Mechanisms

### Periodic Nudge Protocol
Every 10 tool calls, silently checks if MEMORY.md / USER.md need updates. Self-healing, no user intervention.

### FTS5 Cross-Session Search
Full-text search across all workspaces and time periods using SQLite FTS5. Instant recall of past decisions.

### Intent Preload
Pre-loads relevant memory sections based on detected user intent, reducing context-switching cost.

## File Layout

```
.workbuddy/memory/
├── .session_state.json    # L0: Current session
├── 2026-05-23.md           # L1: Today's log
├── 2026-05-22.md           # L1: Yesterday's log
├── ...                     # L1: Up to 30 days
├── MEMORY.md               # L2: Curated long-term
└── archive/                # Archived session states
```

---

**[⬆ Back to Home](./Home.md)**
