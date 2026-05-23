# Hermes-Nexus: Memoria Engine

<p align="center">
  <em>A Self-Evolving Memory &amp; Workflow Architecture for AI Agents</em>
</p>

<p align="center">
  <a href="https://github.com/solarspring13-spec/Hermes-Nexus/actions/workflows/qa_sentinel.yml"><img src="https://github.com/solarspring13-spec/Hermes-Nexus/actions/workflows/qa_sentinel.yml/badge.svg" alt="QA Sentinel"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-BSL%201.1-blueviolet" alt="BSL 1.1"></a>
  <a href="#"><img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+"></a>
  <a href="#"><img src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey" alt="Platform"></a>
  <img src="https://img.shields.io/badge/status-alpha-orange" alt="Alpha">
</p>

<p align="center">
  <a href="README_zh.md">中文</a>
</p>

---

## The Amnesiac Prophet Paradox

Imagine the most brilliant prophet who has ever lived.

She speaks with unmatched precision. She reasons across domains — law, medicine, code, poetry — without hesitation. Give her a problem and she will dissect it, layer by layer, until the truth collapses into clarity.

But the moment you leave the room, she forgets *everything*. Not just your name — the problem, the solution, the reasoning chain, the three hard-won insights you spent an hour extracting.

**This is the Amnesiac Prophet Paradox: infinite intelligence, zero continuity.** Every AI agent today lives in this state. Every session is a cold start. Every insight evaporates.

Hermes-Nexus exists to break this paradox. By physically decoupling intelligence (the LLM) from memory (the engine), we create an architecture where switching models does not mean losing your past, restarting a process does not mean a blank slate, and migrating platforms does not mean rebuilding your knowledge graph from scratch.

> **Memory is sovereign. Compute is tribal. We bridge them.**

---

## What Hermes-Nexus Does

| Problem | Solution |
|---------|----------|
| AI agents forget across sessions | **Memoria Engine** persists context with automatic distillation L0 → L1 → L2 |
| Multiple agent platforms, no shared memory | **Cross-platform adapters** (WorkBuddy, OpenClaw) with thin-shell delegation |
| Memory grows unbounded, becomes noise | **Intelligent lifecycle**: 30-day L1 window, L2 compression with P0/P1/P2 priority |
| No upgrade path for agent memory | **OTA upgrade engine** with SemVer comparison + GitHub Releases |

---

## Core Features

### Three-Tier Memory Engine (L0 → L1 → L2)

| Tier | Name | What It Does |
|------|------|--------------|
| **L0** | Instant Memory | Per-session state capture — decisions, facts, tasks, open questions. Auto-captured in real-time. |
| **L1** | Daily Log | Structured daily summary distilled from L0. Human-readable. 30-day window. |
| **L2** | Long-term Memory | Curated, compressed, persistent. FTS5 full-text search across sessions and workspaces. |

All orchestrated by the **Hermes Nudge Protocol** — periodic self-review that checks if memory needs updating, then compresses old entries to prevent bloat.

### Cross-Platform Adapters (Thin-Shell Delegation)

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  WorkBuddy   │  │  OpenClaw    │  │  Generic CLI │
│  SKILL.md    │  │  SKILL.md    │  │  pip/gh      │
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

### Natural-Language Cron Scheduler

Write cron jobs in plain Chinese or English:

```
"every Monday at 9 AM"
"every day at 23:59 send token report"
```

The parser auto-converts to RFC 5545 RRULE strings and registers with the host automation system. Supports `at-most-once` execution semantics, job chaining, and `no_concurrent` guarantees.

### Semantic Search & Intent Learning

- **BGE-M3 embeddings** for concept-level cross-session recall (not just keyword matching)
- **Intent Preload** — recognizes intent fingerprints (12 seed intents: stock analysis, investment DD, travel planning, code debugging, etc.) and preloads relevant context before you finish typing
- **Vector memory** for finding "that thing I discussed three weeks ago" without remembering the exact words

### QA Sentinel CI

Every Mon / Wed / Fri at 02:00 UTC, the CI pipeline fetches the latest platform specs, diffs them against cached baselines, and alerts if breaking changes are detected. Adapters stay in sync, not in decay.

### OTA Smooth Upgrade Engine

```bash
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

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    MEMORIA ENGINE                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │
│  │  L0 INSTANT │→│ L1 SHORT    │→│  L2 LONG-TERM    │  │
│  │  Session    │  │ Daily Logs  │  │  Curated Memory  │  │
│  │  State      │  │ 30-day win  │  │  Persistent      │  │
│  └─────────────┘  └─────────────┘  └─────────────────┘  │
│         ↑ distill       ↑ compress        ↑               │
│         └───────────────┴─────────────────┘               │
│                   AUTOMATIC LIFECYCLE                     │
└─────────────────────────────────────────────────────────┘
```

**Design maxim:** adapters are *thin shells*. Every command delegates to the engine. The engine is the single source of truth.

**Data root:** `~/.memoria_engine/`

```
.memoria_engine/
├── data/          (memory pool, daily logs, MEMORY.md)
├── health/        (heartbeats for daemon monitoring)
├── db/            (SQLite: FTS5 index, kanban, user model)
├── cache/         (BGE-M3 embeddings, intent signatures)
└── config.yaml    (global configuration)
```

---

## Subsystems

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

## Project Lineage

Hermes-Nexus draws inspiration from two sources:

- **NousResearch Hermes Agent** — the original vision of an agentic memory architecture, where an AI agent carries persistent state across interactions.
- **Hermes (the god)** — messenger between realms, guide to the underworld, keeper of boundaries. An apt patron for a system that moves memory across sessions, platforms, and models.

The Memoria Engine extends these ideas into a standalone, platform-agnostic implementation — memory that lives independently of any single agent runtime.

---

## License

BSL 1.1 — Free for personal use, research, and internal tooling. Converts to MIT on **May 22, 2030**.

[Full license →](LICENSE)

---

## Roadmap

- **v0.2.0**: BGE-M3 model packaging for offline semantic search
- **v0.3.0**: Multi-agent collaboration protocol (Hermes Kanban v2)
- **v1.0.0**: Stable API, formalized plugin system, comprehensive test coverage

---

<p align="center">
  <em>Built by the Hermes-Nexus Project.</em><br>
  <em>Memory is sovereign. Compute is tribal. We bridge them.</em>
</p>
