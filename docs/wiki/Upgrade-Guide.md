# ⬆️ Upgrade Guide

Hermes-Nexus includes a built-in **OTA (Over-The-Air) upgrade engine** that keeps your Memoria Engine current without manual intervention.

## How It Works

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────┐
│  Local       │────▶│  GitHub Releases  │────▶│  pip install │
│  Version     │     │  API (cached 1h)  │     │  --upgrade   │
└─────────────┘     └──────────────────┘     └─────────────┘
   SemVer compare       Fetch latest tag       Auto-install
```

1. **Version Check**: Reads local version from `memoria_engine/__init__.py`
2. **Remote Query**: Fetches latest release tag from GitHub API (cached 1 hour to avoid rate limiting)
3. **SemVer Compare**: Parses versions using standard `MAJOR.MINOR.PATCH` semantics
4. **Auto-Upgrade**: If newer version found, runs `pip install --upgrade`

## Usage

### Check for Updates

```bash
python3 -m memoria_engine.utils.updater --check
```

Output:
```
✓ Memoria Engine is up to date (v0.1.0)
```
or
```
↑ Update available: v0.1.0 → v0.2.0
  Release notes: https://github.com/solarspring13-spec/Hermes-Nexus/releases/tag/v0.2.0
```

### JSON Output (for scripts)

```bash
python3 -m memoria_engine.utils.updater --check --json
```

```json
{
  "status": "update_available",
  "current": "0.1.0",
  "latest": "0.2.0",
  "release_url": "https://github.com/solarspring13-spec/Hermes-Nexus/releases/tag/v0.2.0"
}
```

### Full Upgrade

```bash
python3 -m memoria_engine.utils.updater
# or via adapter:
# WorkBuddy: /upgrade_engine
# OpenClaw:  /engine:upgrade
```

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Already at latest, or upgrade successful |
| 1 | Upgrade error (pip failed) |
| 2 | Network error (can't reach GitHub API) |

## Cache Behavior

- GitHub API responses are cached for **1 hour** to avoid rate limiting
- Cache file: `~/.hermes-nexus/updater_cache.json`
- Force refresh: delete the cache file, or add `--no-cache` flag

## Version Convention

Hermes-Nexus follows **SemVer 2.0**:

- **MAJOR** (`1.0.0`): Breaking changes to memory format or API
- **MINOR** (`0.2.0`): New features, new adapter support
- **PATCH** (`0.1.1`): Bug fixes, performance improvements

## Manual Upgrade

If OTA fails, you can always upgrade manually:

```bash
pip install --upgrade git+https://github.com/solarspring13-spec/Hermes-Nexus.git
```

---

**[⬆ Back to Home](./Home.md)**
