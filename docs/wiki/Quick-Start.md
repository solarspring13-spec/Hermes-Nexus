# 🚀 Quick Start

Get Hermes-Nexus running in **60 seconds**.

## Prerequisites

- Python 3.10+
- Git (optional, for clone)
- One of: [WorkBuddy](https://www.codebuddy.cn) or [OpenClaw](https://github.com/nousresearch/openclaw) (or use standalone)

## Installation

### Option 1: Git Clone (Recommended)

```bash
git clone https://github.com/solarspring13-spec/Hermes-Nexus.git
cd Hermes-Nexus
bash install.sh
```

The installer auto-detects your platform (WorkBuddy or OpenClaw) and delegates to the correct adapter.

### Option 2: GitHub Release Download

```bash
gh release download v0.1.0-beta --repo solarspring13-spec/Hermes-Nexus
tar -xzf source.tar.gz
cd Hermes-Nexus
bash install.sh
```

### Option 3: Pip Install (Standalone)

```bash
pip install git+https://github.com/solarspring13-spec/Hermes-Nexus.git
```

## Verify Installation

```bash
# Check version
python3 -c "import memoria_engine; print(memoria_engine.__version__)"

# Test OTA updater
python3 -m memoria_engine.utils.updater --check
```

## First Run

Once installed, the Memoria Engine integrates automatically with your agent platform. Your AI agent will:

1. **Auto-create** session state on startup (L0 Instant Memory)
2. **Auto-distill** session context into daily logs at session end (L1 Short-Term)
3. **Auto-compress** older daily logs into curated long-term memory (L2 Long-Term)

No manual configuration needed — it just works.

## Next Steps

- 📖 Understand the [Architecture](./Architecture.md)
- 🔌 Connect your [Adapters](./Adapters.md)
- ⬆️ Learn about [OTA Upgrades](./Upgrade-Guide.md)
