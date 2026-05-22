---
name: memoria-engine
description: >-
  Memoria Engine — Hermes-Nexus Core Memory Engine (OpenClaw Adapter).
  Three-tier autonomous memory architecture (L0 session / L1 daily log / L2 long-term),
  Periodic Nudge self-review, FTS5/LanceDB semantic search,
  Cron scheduling engine, Kanban multi-agent task board.
  All heavy logic delegates to the underlying `memoria_engine` Python package.
  This SKILL.md is a thin-shell routing layer only.
  TRIGGERS: memory, recall, nudge, review, session, cron, kanban, install-memoria, upgrade engine, update memoria, /update
metadata:
  hermes:
    tags: [memory, nudge, fts5, semantic, cron, kanban, daemon]
    platforms: [openclaw]
    upstream: https://github.com/solarspring13-spec/Hermes-Nexus
  openclaw:
    # OpenClaw-specific metadata — platform registry for auto-discovery
    skill_type: core
    min_python: "3.10"
    install_script: install.sh
    memory_root: ~/.memoria_engine/
agent_created: true
allowed-tools:
  - Bash
  - Read
  - Write
  - Glob
---

# Memoria Engine — OpenClaw Adapter

**Thin-shell adapter for OpenClaw. Zero business logic. All work delegated to `memoria_engine`.**

> Architecture principle: SKILL.md handles only routing + parameter validation.
> Every heavy operation is delegated to `python3 -m memoria_engine.X.Y --json`.
>
> This ensures:
> - Core logic is platform-agnostic (shared across WorkBuddy / OpenClaw / Generic)
> - Adapters are paper-thin and trivial to maintain
> - Engine upgrades never break adapters (stable import interface)

---

## Phase 0: Bootstrap & Health Check

**Executed silently at session startup.**

### Step 0.1: Verify engine installation
```bash
python3 -c "import memoria_engine; print(memoria_engine.__file__)" 2>&1
```
If it fails → prompt the user to run:
```bash
bash ~/.openclaw/skills/memoria-engine/install.sh --local
```

### Step 0.2: Daemon health check
```bash
python3 -m memoria_engine.daemon.health --json 2>&1
```
- All daemons `ok` → silent
- Any daemon `stale` or `missing` → notify user

---

## Phase 1: Session Memory Capture

**Trigger:** User starts a new task / makes a decision / discovers a fact.

### Delegated calls
```bash
python3 -m memoria_engine.memory.session_state \
  --workspace "<CURRENT_WORKSPACE>" \
  --add-decision "<DECISION_TEXT>" \
  --json
```

```bash
python3 -m memoria_engine.memory.session_state \
  --workspace "<CURRENT_WORKSPACE>" \
  --add-fact "<FACT_TEXT>" \
  --json
```

```bash
python3 -m memoria_engine.memory.session_state \
  --workspace "<CURRENT_WORKSPACE>" \
  --complete-task "<TASK_ID>" \
  --json
```

**Parameters:**
- `--workspace`: Current OpenClaw workspace path
- `--add-decision` / `--add-fact` / `--add-question` / `--add-task` / `--complete-task`: L0 immediate memory writes
- `--json`: Silent return, output only on error

**No business logic here** — all memory writes, dedup, capacity checks are handled inside `session_state.py`.

---

## Phase 2: Periodic Nudge Memory Review

**Trigger:** Every 10 tool calls (counted by caller).

### Delegated call
```bash
python3 -m memoria_engine.memory.memory_nudge \
  --workspace "<CURRENT_WORKSPACE>" \
  --global \
  --session-startup \
  --json
```

**Response interpretation:**
- `needs_review: true` → silently review MEMORY.md / USER.md for updates
- `nudge_due: true` → perform write update
- `action: skip` → no-op

**Post-compression check:**
```bash
python3 -m memoria_engine.memory.memory_compress \
  --workspace "<CURRENT_WORKSPACE>" \
  --json
```

---

## Phase 3: Cross-Session Memory Search

**Trigger:** User mentions "last time", "earlier", "resume", "continue", etc.

### Delegated calls

**Recent memory review (FTS5 global):**
```bash
python3 -m memoria_engine.memory.memory_index \
  --global --recent 7 --limit 5 --json
```

**Semantic search (BGE-M3 + LanceDB):**
```bash
python3 -m memoria_engine.semantic.intent_learner \
  --query "<USER_QUERY>" \
  --preload \
  --mode hybrid \
  --json
```

**Pending task scan:**
```bash
python3 -m memoria_engine.memory.session_state \
  --pending-tasks --global --json
```

> Three-phase results silently injected into reasoning context.
> Only referenced in reply when explicitly relevant.

---

## Phase 4: Cron Scheduling Engine

**Trigger:** User sets up a scheduled task.

### Delegated calls
```bash
# Parse natural-language cron expression
python3 -m memoria_engine.cron.parser \
  --natural "<NATURAL_LANGUAGE_SCHEDULE>" \
  --json

# Register scheduled job
python3 -m memoria_engine.cron.scheduler \
  --register \
  --name "<JOB_NAME>" \
  --rrule "<RRULE_STRING>" \
  --command "<SHELL_COMMAND>" \
  --json
```

---

## Phase 5: Kanban Multi-Agent Board

**Trigger:** Multi-agent task orchestration.

### Delegated calls
```bash
# Create task
python3 -m memoria_engine.kanban.db \
  --create-task \
  --title "<TASK_TITLE>" \
  --worker "<WORKER_NAME>" \
  --json

# Query board status
python3 -m memoria_engine.kanban.db \
  --status --json

# Zombie worker scan
python3 -m memoria_engine.kanban.worker \
  --zombie-scan --json
```

---

## Phase 6: Quality Guardians

### Write verification
```bash
python3 -m memoria_engine.memory.write_verifier \
  --file "<MEMORY_FILE_PATH>" \
  --json
```
Validates memory writes were successful and correctly formatted.

### Semantic quality scoring
```bash
python3 -m memoria_engine.memory.memory_quality \
  --workspace "<CURRENT_WORKSPACE>" \
  --json
```
Outputs memory quality report: coverage, dedup rate, compression suggestions.

---

## Phase 7: System Upgrade

**Trigger**: User says `upgrade engine`, `update memoria`, `/update`, etc.

### Delegated call

**Check (no upgrade)**:
```bash
python3 -m memoria_engine.utils.updater --check --json
```

**Smooth upgrade**:
```bash
python3 -m memoria_engine.utils.updater --json
```

Flow: GitHub Releases API → SemVer comparison → `pip install --upgrade` → verify.

**Response interpretation**:
- `status: "latest"` → Already at latest
- `status: "upgraded"` → Success → reply: **"Engine upgraded to v{version}"**
- `status: "network_error"` → GitHub unreachable → suggest retry
- `status: "error"` → Upgrade failed → suggest manual install

---

## Installation

### One-click (recommended)
```bash
# Inside Hermes-Nexus monorepo:
bash adapters/openclaw/install.sh --local

# Standalone (from GitHub):
bash <(curl -s https://raw.githubusercontent.com/solarspring13-spec/Hermes-Nexus/main/adapters/openclaw/install.sh)
```

### Skill registration (OpenClaw)
```bash
mkdir -p ~/.openclaw/skills/memoria-engine
cp adapters/openclaw/SKILL.md ~/.openclaw/skills/memoria-engine/SKILL.md
```

### Verify
```bash
python3 -c "from memoria_engine.config import MEMORIA_HOME; print(MEMORIA_HOME)"
# → /Users/you/.memoria_engine
```

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│  OpenClaw Agent                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │  SKILL.md (thin wrapper, ~180 lines)           │  │
│  │             │  python3 -m memoria_engine.*     │  │
│  │             ▼                                   │  │
│  │  ┌──────────────────────────────────────────┐  │  │
│  │  │  memoria_engine (Python package)          │  │  │
│  │  │  memory/  semantic/  cron/  kanban/       │  │  │
│  │  └──────────────────────────────────────────┘  │  │
│  │             │                                   │  │
│  │             ▼                                   │  │
│  │  ~/.memoria_engine/  (data root)               │  │
│  │  data/  logs/  db/  health/  cache/  config.yaml│  │
│  └────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

---

## Version & Compatibility

| Version | Date | Changes |
|:--|:--|:--|
| v0.1.0 | 2026-05-23 | Initial OpenClaw adapter — thin-shell routing + full delegation |
