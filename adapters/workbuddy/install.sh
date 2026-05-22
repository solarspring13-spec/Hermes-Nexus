#!/usr/bin/env bash
# ============================================================
# Hermes-Nexus WorkBuddy Adapter — One-Click Installer
# ============================================================
# Installs the Memoria Engine into a WorkBuddy environment.
#
# Modes:
#   1. Local dev:  pip install -e ../../  (you're in the monorepo)
#   2. Remote:     pip install git+https://github.com/solarspring13-spec/Hermes-Nexus.git
#   3. PyPI (future): pip install memoria-engine
#
# After install, initializes:
#   - ~/.memoria_engine/      (data root)
#   - ~/.memoria_engine/config.yaml  (default config)
#   - WorkBuddy Skill registration
#
# Usage:
#   bash install.sh                    # auto-detect mode
#   bash install.sh --local            # force local dev install
#   bash install.sh --remote           # force remote GitHub install
#   bash install.sh --memoria-home /custom/path
# ============================================================

set -euo pipefail

# ── Colors ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

banner() {
    echo ""
    echo -e "${BLUE}${BOLD}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}${BOLD}║   Hermes-Nexus WorkBuddy Adapter — Installer            ║${NC}"
    echo -e "${BLUE}${BOLD}║   Memoria Engine v0.1.0                                 ║${NC}"
    echo -e "${BLUE}${BOLD}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[✓]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[!]${NC}    $*"; }
error()   { echo -e "${RED}[✗]${NC}    $*"; exit 1; }

# ── Defaults ──
INSTALL_MODE="auto"
MEMORIA_HOME="${MEMORIA_HOME:-$HOME/.memoria_engine}"
REPO_URL="https://github.com/solarspring13-spec/Hermes-Nexus.git"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." 2>/dev/null && pwd || echo "")"

# ── Parse args ──
while [[ $# -gt 0 ]]; do
    case "$1" in
        --local)   INSTALL_MODE="local";  shift ;;
        --remote)  INSTALL_MODE="remote"; shift ;;
        --memoria-home) MEMORIA_HOME="$2"; shift 2 ;;
        --help|-h)
            echo "Usage: bash install.sh [--local|--remote] [--memoria-home PATH]"
            exit 0
            ;;
        *) warn "Unknown option: $1"; shift ;;
    esac
done

# ────────── Step 0: Pre-flight Checks ──────────
banner

info "Checking environment..."
PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" &>/dev/null; then
    error "python3 not found. Please install Python 3.10+ first."
fi
PYTHON_VER=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
info "Python version: $PYTHON_VER"

# ────────── Step 1: Select Install Mode ──────────
if [ "$INSTALL_MODE" = "auto" ]; then
    # Auto-detect: if we're inside the Hermes-Nexus repo, use local; else remote
    if [ -n "$PROJECT_ROOT" ] && [ -f "$PROJECT_ROOT/memoria_engine/__init__.py" ]; then
        INSTALL_MODE="local"
        info "Detected local monorepo → using local dev install"
    else
        INSTALL_MODE="remote"
        info "Not in monorepo → using remote GitHub install"
    fi
fi

# ────────── Step 2: Install Memoria Engine ──────────
info "Installing Memoria Engine (mode: $INSTALL_MODE)..."

case "$INSTALL_MODE" in
    local)
        if [ ! -f "$PROJECT_ROOT/memoria_engine/__init__.py" ]; then
            error "memoria_engine/ package not found at $PROJECT_ROOT/memoria_engine/"
        fi
        info "Running: pip install -e $PROJECT_ROOT"
        "$PYTHON_BIN" -m pip install -e "$PROJECT_ROOT" --quiet 2>&1 | tail -3
        ;;
    remote)
        # Check if git is available
        if command -v git &>/dev/null; then
            info "Running: pip install git+${REPO_URL}"
            "$PYTHON_BIN" -m pip install "git+${REPO_URL}" --quiet 2>&1 | tail -3
        else
            warn "git not found; falling back to pip download"
            "$PYTHON_BIN" -m pip install "memoria-engine" --quiet 2>&1 || \
                error "Remote install failed. Try --local if you have the source."
        fi
        ;;
esac

# Verify import
if "$PYTHON_BIN" -c "import memoria_engine" 2>/dev/null; then
    success "memoria_engine package imported successfully"
else
    error "memoria_engine import failed after install"
fi

# ────────── Step 3: Initialize Config Directory ──────────
info "Initializing Memoria Engine home: $MEMORIA_HOME"

"$PYTHON_BIN" -c "
import os, sys
from pathlib import Path

memoria_home = Path('$MEMORIA_HOME').expanduser()
dirs = [
    memoria_home,
    memoria_home / 'data',
    memoria_home / 'logs',
    memoria_home / 'health' / 'heartbeats',
    memoria_home / 'db',
    memoria_home / 'cache' / 'embeddings',
]
for d in dirs:
    d.mkdir(parents=True, exist_ok=True)
print(f'Created {len(dirs)} directories under {memoria_home}')
"

# Generate default config.yaml if not exists
CONFIG_YAML="$MEMORIA_HOME/config.yaml"
if [ ! -f "$CONFIG_YAML" ]; then
    cat > "$CONFIG_YAML" << 'YAMLEOF'
# Memoria Engine — Default Configuration
# Generated by WorkBuddy Adapter installer

memoria_home: "~/.memoria_engine"
platform: "workbuddy"

# Memory subsystem
memory:
  pool_max_entries: 1000
  compress_threshold_bytes: 1048576   # 1 MB
  nudge_interval_calls: 10
  index_engine: "fts5"               # fts5 | lance

# Semantic subsystem (BGE-M3 embeddings)
semantic:
  model: "BAAI/bge-m3"
  dimension: 1024
  similarity_threshold: 0.65
  cache_dir: "~/.memoria_engine/cache/embeddings"

# Cron scheduler
cron:
  max_concurrent_jobs: 3
  heartbeat_timeout_sec: 300

# Kanban board
kanban:
  max_workers: 5
  zombie_timeout_sec: 600

# Daemon health
daemon:
  heartbeat_interval_sec: 60
  stale_threshold_sec: 300
  log_retention_days: 7
YAMLEOF
    success "Default config.yaml written to $CONFIG_YAML"
else
    info "config.yaml already exists, skipping"
fi

# ────────── Step 4: Skill Registration Hint ──────────
info "WorkBuddy Skill file location:"
SKILL_MD="$SCRIPT_DIR/SKILL.md"
if [ -f "$SKILL_MD" ]; then
    success "SKILL.md found at $SKILL_MD"
    echo ""
    echo -e "  ${YELLOW}To register in WorkBuddy:${NC}"
    echo -e "  ${BOLD}cp $SKILL_MD ~/.workbuddy/skills/memoria-engine/SKILL.md${NC}"
    echo "  Then restart WorkBuddy or reload skills."
else
    warn "SKILL.md not found — skill registration skipped"
fi

# ────────── Step 5: Summary ──────────
echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║   Installation Complete!                                ║${NC}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}Package:${NC}     memoria_engine (mode: $INSTALL_MODE)"
echo -e "  ${BOLD}Home:${NC}        $MEMORIA_HOME"
echo -e "  ${BOLD}Config:${NC}      $CONFIG_YAML"
echo -e "  ${BOLD}Python:${NC}      $PYTHON_BIN ($PYTHON_VER)"
echo ""
echo -e "  ${BOLD}Quick test:${NC}"
echo -e "    python3 -c 'from memoria_engine.config import MEMORIA_HOME; print(MEMORIA_HOME)'"
echo ""
echo -e "  ${BOLD}Next:${NC} Register the skill in WorkBuddy to activate memory tracking."
echo ""
