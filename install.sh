#!/usr/bin/env bash
# ============================================================
# 🌌 Hermes-Nexus: Universal Installer
# ============================================================
# Auto-detects host environment and delegates to the correct
# platform-specific adapter.
#
# Detection priority:
#   1. WorkBuddy  (~/.workbuddy)     → adapters/workbuddy/install.sh
#   2. OpenClaw   (~/.openclaw)      → adapters/openclaw/install.sh
#   3. Generic    (neither)          → pip install -e . (standalone)
#
# If multiple platforms are detected, installs ALL of them.
#
# Usage:
#   bash install.sh                    # auto-detect
#   bash install.sh --local            # force local dev install
#   bash install.sh --remote           # force remote GitHub install
#   bash install.sh --platform openclaw # force specific platform
#   bash install.sh --memoria-home /custom/path
# ============================================================

set -euo pipefail

# ── Colors ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

banner() {
    echo ""
    echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}${BOLD}║                                                              ║${NC}"
    echo -e "${CYAN}${BOLD}║   🌌 Hermes-Nexus: Memoria Engine — Universal Installer     ║${NC}"
    echo -e "${CYAN}${BOLD}║   v0.1.0-alpha  |  BSL 1.1                                  ║${NC}"
    echo -e "${CYAN}${BOLD}║                                                              ║${NC}"
    echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[✓]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[!]${NC}    $*"; }
error()   { echo -e "${RED}[✗]${NC}    $*"; exit 1; }

# ── Defaults ──
FORCE_PLATFORM=""
INSTALL_MODE="auto"
MEMORIA_HOME="${MEMORIA_HOME:-$HOME/.memoria_engine}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASS_THROUGH_ARGS=()

# ── Parse args ──
while [[ $# -gt 0 ]]; do
    case "$1" in
        --platform)   FORCE_PLATFORM="$2"; PASS_THROUGH_ARGS+=("$1" "$2"); shift 2 ;;
        --local)      INSTALL_MODE="local"; PASS_THROUGH_ARGS+=("$1"); shift ;;
        --remote)     INSTALL_MODE="remote"; PASS_THROUGH_ARGS+=("$1"); shift ;;
        --memoria-home) MEMORIA_HOME="$2"; PASS_THROUGH_ARGS+=("$1" "$2"); shift 2 ;;
        --help|-h)
            echo "Usage: bash install.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --platform <name>    Force a specific platform (workbuddy | openclaw | generic)"
            echo "  --local              Force local dev install (pip install -e .)"
            echo "  --remote             Force remote GitHub install"
            echo "  --memoria-home PATH  Custom data root (default: ~/.memoria_engine)"
            echo "  --help, -h           Show this help"
            echo ""
            echo "Without --platform, auto-detects from environment."
            exit 0
            ;;
        *) warn "Unknown option: $1"; shift ;;
    esac
done

# ────────── Step 1: Environment Detection ──────────
banner

info "Scanning host environment..."

DETECTED_WORKBUDDY=false
DETECTED_OPENCLAW=false
DETECTED_GENERIC=false

if [ -d "$HOME/.workbuddy" ]; then
    DETECTED_WORKBUDDY=true
    info "  ✓ WorkBuddy detected  (~/.workbuddy)"
fi

if [ -d "$HOME/.openclaw" ]; then
    DETECTED_OPENCLAW=true
    info "  ✓ OpenClaw detected   (~/.openclaw)"
fi

if ! $DETECTED_WORKBUDDY && ! $DETECTED_OPENCLAW; then
    DETECTED_GENERIC=true
    info "  → No platform detected — installing standalone (Generic CLI)"
fi

# ── Resolve platform(s) to install ──
PLATFORMS_TO_INSTALL=()

if [ -n "$FORCE_PLATFORM" ]; then
    case "$FORCE_PLATFORM" in
        workbuddy) PLATFORMS_TO_INSTALL=("workbuddy") ;;
        openclaw)  PLATFORMS_TO_INSTALL=("openclaw") ;;
        generic)   PLATFORMS_TO_INSTALL=("generic") ;;
        all)       PLATFORMS_TO_INSTALL=("all") ;;
        *)         error "Unknown platform: $FORCE_PLATFORM. Valid: workbuddy, openclaw, generic, all" ;;
    esac
    info "Platform override: ${PLATFORMS_TO_INSTALL[*]}"
else
    # ── Auto-detect ──
    if $DETECTED_WORKBUDDY; then
        PLATFORMS_TO_INSTALL+=("workbuddy")
    fi
    if $DETECTED_OPENCLAW; then
        PLATFORMS_TO_INSTALL+=("openclaw")
    fi
    if $DETECTED_GENERIC; then
        PLATFORMS_TO_INSTALL+=("generic")
    fi
    info "Auto-detected platforms: ${PLATFORMS_TO_INSTALL[*]:-none}"
fi

echo ""

# ────────── Step 2: Delegate to Platform Installers ──────────

install_platform() {
    local platform="$1"
    local adapter_dir="$SCRIPT_DIR/adapters/$platform"
    local install_script="$adapter_dir/install.sh"

    echo -e "${CYAN}${BOLD}┌──────────────────────────────────────────────────────────────┐${NC}"
    echo -e "${CYAN}${BOLD}│  Installing: ${platform}                                      ${NC}"
    echo -e "${CYAN}${BOLD}└──────────────────────────────────────────────────────────────┘${NC}"
    echo ""

    if [ ! -f "$install_script" ]; then
        warn "No install script found at $install_script — skipping ${platform}"
        return 0
    fi

    # Delegate: pass through all original args
    bash "$install_script" "${PASS_THROUGH_ARGS[@]}"
    local exit_code=$?

    if [ $exit_code -eq 0 ]; then
        success "${platform} adapter installed successfully"
    else
        warn "${platform} adapter install exited with code $exit_code"
    fi

    echo ""
}

install_generic() {
    echo -e "${CYAN}${BOLD}┌──────────────────────────────────────────────────────────────┐${NC}"
    echo -e "${CYAN}${BOLD}│  Installing: Generic CLI (standalone)                        ${NC}"
    echo -e "${CYAN}${BOLD}└──────────────────────────────────────────────────────────────┘${NC}"
    echo ""

    local PYTHON_BIN="${PYTHON_BIN:-python3}"

    info "Python: $($PYTHON_BIN --version)"

    if [ "$INSTALL_MODE" = "local" ] || [ -f "$SCRIPT_DIR/memoria_engine/__init__.py" ]; then
        info "Running: pip install -e $SCRIPT_DIR"
        "$PYTHON_BIN" -m pip install -e "$SCRIPT_DIR" --quiet 2>&1 | tail -3
    else
        info "Running: pip install git+https://github.com/solarspring13-spec/Hermes-Nexus.git"
        "$PYTHON_BIN" -m pip install "git+https://github.com/solarspring13-spec/Hermes-Nexus.git" --quiet 2>&1 | tail -3
    fi

    # Verify
    if "$PYTHON_BIN" -c "import memoria_engine" 2>/dev/null; then
        success "memoria_engine package imported successfully"
    else
        warn "memoria_engine import failed — check pip install output"
    fi

    # Init data dir
    "$PYTHON_BIN" -c "
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
print(f'Initialized {len(dirs)} directories under {memoria_home}')
" 2>&1

    echo ""
}

# ── Execute installations ──

if [ ${#PLATFORMS_TO_INSTALL[@]} -eq 0 ]; then
    warn "No platforms detected and none forced. Installing generic CLI..."
    install_generic
else
    for platform in "${PLATFORMS_TO_INSTALL[@]}"; do
        case "$platform" in
            workbuddy)  install_platform "workbuddy" ;;
            openclaw)   install_platform "openclaw" ;;
            generic|all) install_generic ;;
        esac
    done
fi

# ────────── Step 3: Summary ──────────
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║                                                            ║${NC}"
echo -e "${GREEN}${BOLD}║   🌌 Installation Complete                                 ║${NC}"
echo -e "${GREEN}${BOLD}║                                                            ║${NC}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}Platforms installed:${NC} ${PLATFORMS_TO_INSTALL[*]:-generic}"
echo -e "  ${BOLD}Data root:${NC}          $MEMORIA_HOME"
echo -e "  ${BOLD}Config:${NC}            $MEMORIA_HOME/config.yaml"
echo ""
echo -e "  ${BOLD}Quick test:${NC}"
echo -e "    python3 -c 'from memoria_engine.config import MEMORIA_HOME; print(MEMORIA_HOME)'"
echo ""
echo -e "  ${BOLD}Docs:${NC}  https://github.com/solarspring13-spec/Hermes-Nexus#readme"
echo ""
