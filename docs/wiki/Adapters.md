# 🔌 Adapters

Hermes-Nexus uses a **thin-shell delegation** pattern. The adapter (a SKILL.md file) is just a router — all logic lives inside `memoria_engine/`.

```
┌──────────────────┐       ┌──────────────────┐
│   WORKBUDDY      │       │    OPENCLAW      │
│  (CodeBuddy CN)  │       │  (Nous Research) │
└────────┬─────────┘       └────────┬─────────┘
         │                          │
    ┌────▼────┐                ┌────▼────┐
    │SKILL.md │                │SKILL.md │
    │(Router) │                │(Router) │
    └────┬────┘                └────┬────┘
         │                          │
         └──────────┬───────────────┘
                    │
           ┌────────▼────────┐
           │ MEMORIA ENGINE  │
           │ (Core Logic)    │
           └─────────────────┘
```

## WorkBuddy Adapter

**Location**: `adapters/workbuddy/`

### Available Commands

| Command | Function |
|---------|----------|
| `memory_status` | Show memory health (L0/L1/L2 state) |
| `memory_search <query>` | FTS5 cross-session search |
| `memory_review` | Trigger Nudge protocol |
| `memory_distill` | Force L0→L1 distillation |
| `memory_compress` | Force L1→L2 compression |
| `session_init` | Initialize new session |
| `session_close` | Close & archive session |
| `upgrade_engine` | OTA upgrade to latest |

### Installation

```bash
# From Hermes-Nexus root
bash adapters/workbuddy/install.sh
```

This registers `SKILL.md` with WorkBuddy's skill system. After installation, all commands delegate to `python3 -m memoria_engine.X.Y`.

## OpenClaw Adapter

**Location**: `adapters/openclaw/`

### Available Commands

| Command | Function |
|---------|----------|
| `memory:status` | Show memory health |
| `memory:search <query>` | FTS5 cross-session search |
| `memory:review` | Trigger Nudge protocol |
| `memory:distill` | Force L0→L1 distillation |
| `session:init` | Initialize new session |
| `session:close` | Close & archive session |
| `engine:upgrade` | OTA upgrade to latest |

### Installation

```bash
# From Hermes-Nexus root
bash adapters/openclaw/install.sh
```

## Adding a New Adapter

To add support for a new agent platform:

1. Create `adapters/<platform>/SKILL.md` with routing commands
2. Create `adapters/<platform>/install.sh` with platform-specific setup
3. Follow the thin-shell pattern: SKILL.md delegates to `python3 -m memoria_engine`

See existing adapters as templates.

---

**[⬆ Back to Home](./Home.md)**
