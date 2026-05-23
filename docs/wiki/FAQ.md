# ❓ FAQ

## General

### What is Hermes-Nexus?

Hermes-Nexus is an open-source memory engine for AI agents. It gives agents persistent, cross-session memory using a 3-tier architecture (L0 Instant → L1 Short-Term → L2 Long-Term), inspired by human cognition.

### Why "Hermes-Nexus"?

**Hermes** — Greek messenger god, guardian of memory and boundaries. **Nexus** — a central connecting point. Together: the bridge between AI agents and persistent memory.

### What's the difference between Hermes-Nexus and LangChain Memory?

LangChain Memory is a library for building memory into LLM applications at the code level. Hermes-Nexus is a **standalone engine** that plugs into existing agent platforms (WorkBuddy, OpenClaw) with zero code changes — it operates at the platform/agent level, not the application level.

## Installation

### Do I need WorkBuddy or OpenClaw to use this?

No. Hermes-Nexus can be used standalone via `pip install`. The adapters are for deeper platform integration.

### What Python version do I need?

Python 3.10 or higher.

### Installation fails. What should I do?

1. Check Python version: `python3 --version`
2. Try pip install directly: `pip install git+https://github.com/solarspring13-spec/Hermes-Nexus.git`
3. Open a [Bug Report](https://github.com/solarspring13-spec/Hermes-Nexus/issues/new?template=bug_report.yml)

## Memory System

### Where is my memory stored?

```
.workbuddy/memory/
├── .session_state.json    # L0: Current session
├── YYYY-MM-DD.md          # L1: Daily logs (30-day window)
└── MEMORY.md              # L2: Curated long-term
```

### How do I search old memories?

```bash
# WorkBuddy
/memory_search "investment decision about Tesla"

# OpenClaw
/memory:search "architecture decision"
```

Uses SQLite FTS5 for full-text search across all sessions and workspaces.

### What happens to old daily logs?

After 30 days, L1 daily logs are automatically compressed into L2 (MEMORY.md). P0 entries (critical preferences) are kept verbatim; P1 entries are summarized; P2 entries are discarded.

### Can I manually trigger memory compression?

Yes:
```bash
python3 -m memoria_engine.scripts.memory_compress
# or: /memory_compress (WorkBuddy), /memory:compress (OpenClaw)
```

## Upgrades

### How do I upgrade?

```bash
python3 -m memoria_engine.utils.updater
```

This checks GitHub Releases for newer versions and auto-installs via pip. See [Upgrade Guide](./Upgrade-Guide.md) for details.

### Will upgrading break my existing memory?

No. The memory file format (`.session_state.json`, `YYYY-MM-DD.md`, `MEMORY.md`) is designed to be forward-compatible. Breaking changes to the format will only happen on MAJOR version bumps, with clear migration paths.

## Security

### Does Hermes-Nexus send my data anywhere?

No. All memory is stored **locally** on your machine. The only external connection is the OTA updater querying GitHub Releases API for version checks (read-only, cached for 1 hour).

### How does desensitization work?

The `sync.py` tool (used when extracting the engine from a source codebase) applies three-tier security scanning:
1. Regex matrix for API keys, tokens, passwords
2. AST-based `StringConstantVisitor` for hardcoded sensitive strings
3. File blacklist for known sensitive paths

## License

### Can I use Hermes-Nexus commercially?

Hermes-Nexus is under **BSL 1.1**. Free for non-production use. Production/commercial use requires a license. See the [LICENSE](https://github.com/solarspring13-spec/Hermes-Nexus/blob/main/LICENSE) file for details.

---

**[⬆ Back to Home](./Home.md)**
