# 🌌 Hermes-Nexus: Memoria Engine for OpenClaw

<p align="center">
  <em>A Self-Evolving Memory & Workflow Architecture. Inspired by NousResearch Hermes Agent.</em>
</p>

<p align="center">
  <a href="https://github.com/solarspring13-spec/Hermes-Nexus/actions/workflows/qa_sentinel.yml"><img src="https://github.com/solarspring13-spec/Hermes-Nexus/actions/workflows/qa_sentinel.yml/badge.svg" alt="QA Sentinal"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-BSL%201.1-blueviolet" alt="BSL 1.1"></a>
  <a href="#"><img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+"></a>
  <a href="#"><img src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey" alt="Platform"></a>
  <img src="https://img.shields.io/badge/status-alpha-orange" alt="Alpha">
</p>

---

## 🧠 Cognitive Metaphor: The Amnesiac Prophet and the Eternal Library

Imagine the most brilliant prophet who has ever lived.

She speaks with unmatched precision. She reasons across domains — law, medicine, code, poetry — without hesitation. Give her a problem and she will dissect it, layer by layer, until the truth collapses into clarity.

But the moment you leave the room, she forgets *everything*. Not just your name — she forgets the problem, the solution, the reasoning chain, the three hard-won insights you spent an hour extracting. You return the next morning and she greets you again — bright, brilliant, blank.

This is the state of every AI agent today.

Modern LLMs are **computational savants with zero persistent memory**. Their context windows are scratchpads, not libraries. Every session is a cold start. Every insight, unless captured by an external tool, evaporates the moment the conversation ends.

We call this the **Amnesiac Prophet Paradox**: infinite intelligence, zero continuity.

---

Now imagine the opposite.

A library that never forgets — but cannot think. It remembers every conversation, every decision, every refactored function and abandoned hypothesis. It cross-references, compresses, distills. It whispers to the prophet: *"Last time, you tried approach A and it failed because the API rate-limited at 100 QPS. Here's the stack trace. Also, the user prefers async callbacks, not polling."*

The library cannot generate a single line of code. But without it, the prophet generates in circles.

Combine them — and you have something new: **an intelligence that accumulates**.

---

**This is the first principle of Hermes-Nexus:**

> **算力与记忆的物理隔离。**
>
> Compute is tribal — it belongs to the session, the model, the cloud provider.
> Memory is sovereign — it belongs to the *agent*, not the runtime.

By physically decoupling intelligence (the LLM) from memory (the engine), we create an architecture where:
- Switching models does not mean losing your past.
- Restarting a process does not mean a blank slate.
- Migrating from one platform to another does not mean rebuilding your knowledge graph from scratch.

The Memoria Engine is not a plugin. It is the **second brain** that makes the first brain useful across time.

---

## 🏗️ Architecture: Three Layers of Separation

```
┌──────────────────────────────────────────────────────────────────┐
│  LAYER 3: DISTRIBUTION                                            │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌───────────┐  │
│  │  WorkBuddy  │  │  OpenClaw  │  │  Generic    │  │  pip/gh   │  │
│  │  Skill      │  │  Skill     │  │  CLI        │  │  package  │  │
│  └──────┬──────┘  └──────┬─────┘  └──────┬──────┘  └─────┬─────┘  │
│         │                │               │               │        │
├─────────┼────────────────┼───────────────┼───────────────┼────────┤
│  LAYER 2: ADAPTERS (thin-shell delegation)                        │
│  ┌──────┴──────┐  ┌──────┴─────┐  ┌──────┴──────┐               │
│  │ workbuddy/  │  │ openclaw/  │  │   generic/  │               │
│  │ SKILL.md    │  │ SKILL.md   │  │   cli.py    │               │
│  │ install.sh  │  │ install.sh │  │             │               │
│  └──────┬──────┘  └──────┬─────┘  └──────┬──────┘               │
│         │                │               │                       │
│         └────────────────┼───────────────┘                       │
│                          │  python3 -m memoria_engine.*          │
│                          │  (all logic belongs here)             │
├──────────────────────────┼───────────────────────────────────────┤
│  LAYER 1: CORE ENGINE — Memoria Engine                           │
│                                                                   │
│  ┌─────────┐  ┌──────────┐  ┌────────────┐  ┌───────────────┐   │
│  │ memory/ │  │ cron/     │  │ kanban/     │  │ semantic/     │   │
│  │ Nudge   │  │ Scheduler │  │ Worker Pool │  │ BGE-M3 Embed  │   │
│  │ Index   │  │ Parser    │  │ Zombie Det. │  │ Intent Learn  │   │
│  │ Compress│  │           │  │             │  │ Vector Memory │   │
│  │ Pool    │  │           │  │             │  │               │   │
│  └────┬────┘  └─────┬─────┘  └──────┬──────┘  └───────┬───────┘   │
│       │              │               │                │           │
│  ┌────┴──────────────┴───────────────┴────────────────┴───────┐   │
│  │  config.py — MEMORIA_HOME (env var override, live reload)   │   │
│  │  daemon/ — health heartbeat, launchctl integration         │   │
│  │  models/ — user_model, sequence_analyzer                   │   │
│  │  skills/ — confidence_scorer, creator, detector            │   │
│  │  utils/ — agent_router, session_recovery, correction       │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                   │
│  Data root: ~/.memoria_engine/                                    │
│  ├── data/          (memory pool, daily logs, MEMORY.md)          │
│  ├── health/        (heartbeats for daemon monitoring)            │
│  ├── db/            (SQLite: FTS5 index, kanban, user model)      │
│  ├── cache/         (BGE-M3 embeddings, intent signatures)        │
│  └── config.yaml    (global configuration)                        │
└──────────────────────────────────────────────────────────────────┘
```

**Design maxim:** adapters are *thin shells*. SKILL.md routes every command to `python3 -m memoria_engine.X.Y --json`. Zero business logic lives in adapters. The engine is the single source of truth.

---

## ⚖️ License: BSL 1.1 — The Open-Source Compact

Hermes-Nexus is released under the **Business Source License 1.1**.

| Use Case | Status |
|----------|--------|
| Personal use, research, education | ✅ Free |
| Internal tooling within your company | ✅ Free |
| Deploying as part of a paid SaaS product | ❌ Requires commercial license |
| Redistributing Memoria Engine as a standalone commercial product | ❌ Prohibited |
| **Change Date: May 22, 2030** | → Converts to MIT automatically |

**Why BSL?** We believe open-source should empower individual builders, not become unpaid R&D for cloud vendors. You can hack, study, and deploy freely — but if you're selling access to the engine itself, we ask you to support its development.

[Full license text →](LICENSE)

---

## 🚀 Quick Start

### Option A: Universal Installer (Recommended)

```bash
git clone https://github.com/solarspring13-spec/Hermes-Nexus.git
cd Hermes-Nexus
bash install.sh
```

The universal installer auto-detects your host environment (WorkBuddy / OpenClaw / Generic) and delegates to the right adapter.

### Option B: Platform-Specific

**WorkBuddy:**
```bash
bash adapters/workbuddy/install.sh
```

**OpenClaw:**
```bash
bash adapters/openclaw/install.sh
```

**Generic CLI (standalone):**
```bash
pip install -e .
python3 -m memoria_engine.memory.memory_nudge --help
```

### Verify Installation

```bash
python3 -c "from memoria_engine.config import MEMORIA_HOME; print(MEMORIA_HOME)"
# → /Users/you/.memoria_engine
```

---

## 🔬 Subsystems

| Subsystem | Module | What It Does |
|-----------|--------|--------------|
| **Memory** | `memory/` | Three-tier memory (L0 session → L1 daily log → L2 curated MEMORY.md), FTS5 cross-session search, intelligent compress & dedup |
| **Cron** | `cron/` | Natural-language scheduler — write "every Monday at 9 AM" in Chinese or English, get an RRULE job |
| **Kanban** | `kanban/` | Multi-agent task board with worker lifecycle, zombie detection, and delegation |
| **Semantic** | `semantic/` | BGE-M3 embeddings, intent learning, vector memory for concept-level recall |
| **Daemon** | `daemon/` | Health heartbeat, multi-signal cross-validation (heartbeat + launchctl + logs) |
| **Skills** | `skills/` | Auto-detect when to create a new skill, confidence scoring for reuse |

---

## 🛡️ QA Sentinel

Every Mon / Wed / Fri at 02:00 UTC, the CI pipeline fetches the latest platform specs from OpenClaw and WorkBuddy, diffs them against cached baselines, and alerts if breaking changes are detected.

Manual trigger: [Run QA Sentinel](https://github.com/solarspring13-spec/Hermes-Nexus/actions/workflows/qa_sentinel.yml)

---

## 📜 Inspirations

- **NousResearch Hermes Agent** — the original vision of an agentic memory architecture
- **Hermes (the god)** — messenger between realms, guide to the underworld, keeper of boundaries. An apt patron for a system that moves memory across sessions, platforms, and models.

---

<p align="center">
  <em>Built with ❤️ by the Hermes-Nexus Project. </em><br>
  <em>Memory is sovereign. Compute is tribal. We bridge them.</em>
</p>
