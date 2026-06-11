# v0.1.0 Beta: The Amnesiac Prophet Awakens

> **失忆先知的觉醒**

---

## The Paradox That Started It All

Imagine the most brilliant prophet who has ever lived. She speaks with unmatched precision. She reasons across law, medicine, code, poetry — without hesitation. Give her a problem and she will dissect it, layer by layer, until the truth collapses into clarity.

But the moment you leave the room, she forgets *everything*. Not just your name — the problem, the solution, the reasoning chain, the three hard-won insights you spent an hour extracting.

**This is the Amnesiac Prophet Paradox: infinite intelligence, zero continuity.** Every AI agent today lives in this state. Every session is a cold start. Every insight evaporates.

Hermes-Nexus exists to break this paradox. By physically decoupling intelligence (the LLM) from memory (the engine), we create an architecture where switching models does not mean losing your past, restarting a process does not mean a blank slate, and migrating platforms does not mean rebuilding your knowledge graph from scratch.

> **Memory is sovereign. Compute is tribal. We bridge them.**

---

## Core Features

### 🧠 Three-Tier Memory Engine (L0 → L1 → L2)

| Tier | Name | What It Does |
|------|------|--------------|
| **L0** | Instant Memory | Per-session state capture — decisions, facts, tasks, open questions. Auto-captured in real-time. |
| **L1** | Daily Log | Structured daily summary distilled from L0. Human-readable. 30-day window. |
| **L2** | Long-term Memory | Curated, compressed, persistent. FTS5 full-text search across sessions and workspaces. |

All orchestrated by the **Hermes Nudge Protocol** — periodic self-review that checks if MEMORY.md needs updating, then compresses old entries to prevent bloat.

### 🔀 Cross-Platform Adapters (Thin-Shell Delegation)

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  WorkBuddy   │  │  OpenClaw    │  │  Generic CLI │
│  SKILL.md     │  │  SKILL.md    │  │  pip/gh      │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                 │
       └─────────────────┼─────────────────┘
                         │ python3 -m memoria_engine.*
                         │ (all logic lives here)
               ┌─────────▼──────────┐
               │  Memoria Engine    │
               │  (single source    │
               │   of truth)        │
               └────────────────────┘
```

Adapters are *thin shells*. Zero business logic lives in them. Every command routes to `python3 -m memoria_engine.X.Y --json`. Install once, run anywhere.

### ⏰ Natural-Language Cron Scheduler

Write cron jobs in plain Chinese or English:

```
"每周一上午 9 点运行"
"every Monday at 9 AM"
"每天 23:59 发送 Token 报告"
```

The parser auto-converts to RFC 5545 RRULE strings and registers with the host automation system. Supports `at-most-once` execution semantics, job chaining, and `no_concurrent` guarantees.

### 🔍 Semantic Search & Intent Learning

- **BGE-M3 embeddings** for concept-level cross-session recall (not just keyword matching)
- **Intent Preload** — recognizes intent fingerprints (12 seed intents: stock analysis, investment DD, travel planning, code debugging, etc.) and preloads relevant context before you even finish typing
- **Vector memory** for finding "that thing I discussed three weeks ago" without remembering the exact words

### 🛡️ QA Sentinel CI

Every Mon / Wed / Fri at 02:00 UTC, the CI pipeline fetches the latest platform specs, diffs them against cached baselines, and alerts if breaking changes are detected. Adapters stay in sync, not in decay.

### 📦 OTA Smooth Upgrade Engine

```
python3 -m memoria_engine.utils.updater           # check + upgrade
python3 -m memoria_engine.utils.updater --check    # check only
python3 -m memoria_engine.utils.updater --json     # CI-friendly JSON output
```

Pure stdlib, zero dependencies. SemVer comparison, 1-hour cache TTL for GitHub API, auto-backup before upgrade. Exit codes: 0 = latest/upgraded, 1 = error, 2 = network error.

---

## Quick Start

```bash
git clone https://github.com/solarspring13-spec/Hermes-Nexus.git
cd Hermes-Nexus
bash install.sh
```

The universal installer auto-detects your host environment (WorkBuddy / OpenClaw / Generic) and delegates to the right adapter.

**Verify installation:**

```bash
python3 -c "from memoria_engine.config import MEMORIA_HOME; print(MEMORIA_HOME)"
# → /Users/you/.memoria_engine
```

**Run your first memory nudge:**

```bash
python3 -m memoria_engine.memory.memory_nudge --help
```

---

## Subsystems at a Glance

| Subsystem | Module | Description |
|-----------|--------|-------------|
| Memory | `memory/` | L0/L1/L2 tiered memory, FTS5 search, compress & dedup |
| Cron | `cron/` | Natural-language scheduler → RRULE |
| Kanban | `kanban/` | Multi-agent task board, zombie detection |
| Semantic | `semantic/` | BGE-M3 embeddings, intent learning, vector memory |
| Daemon | `daemon/` | Health heartbeat, multi-signal cross-validation |
| Skills | `skills/` | Auto-detect skill creation, confidence scoring |
| Utils | `utils/` | Agent router, session recovery, OTA updater |

---

## License

BSL 1.1 — Free for personal use, research, and internal tooling. Converts to MIT on **May 22, 2030**.

[Full license →](LICENSE)

---

## What's Next

- **v0.2.0**: BGE-M3 model packaging for offline semantic search
- **v0.3.0**: Multi-agent collaboration protocol (Hermes Kanban v2)
- **v1.0.0**: Stable API, formalized plugin system, comprehensive test coverage

---

## v0.2.0: Full Sync, FTS5 Optimize & Session Fork Prevention

> **2026-06-11 | WorkBuddy enhanced-memory v2.5.0 sync**

### 1. Sync: Full child->parent sync

26 memoria_engine/ files synced via sync.py to WorkBuddy enhanced-memory 2026-06-11 state. 23 backlog files converged. 0 BLOCKER / 49 WARNING (path redaction auto-applied).

### 2. Added: FTS5 Optimize + Session Fork Prevention

- **FTS5 segment merge** (`--optimize`): SQLite FTS5 periodic segment merge. Daemon auto-runs every 6 cycles (~3h). Upstream: nousresearch/hermes-agent PR #34596.
- **Session fork prevention** (fcntl.flock): Kernel-level file lock prevents concurrent distill/close session-id forks. Upstream: nousresearch/hermes-agent PR #34351.

### 3. Fixed: Kanban Worker Import

- `kanban/worker.py` broken import fixed: `from kanban_db import` -> `from .db import`. sync.py IMPORT_REWRITE +1 rule.

### Verification

- All 26 files py_compile: PASS
- Private absolute path scan: 0 hits
- Concurrent lock test: 1 success / 1 LOCK_CONFLICT (expected)

---

> *"算力与记忆的物理隔离。Compute is tribal — it belongs to the session. Memory is sovereign — it belongs to the agent."*
>
> — Hermes-Nexus Project, May 2026
