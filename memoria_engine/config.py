#!/usr/bin/env python3
"""
config.py — Hermes-Nexus Memoria Engine 统一配置层
==================================================

Design: Step 1 蓝图 §伍·Q3 — 路径常量全部迁入此文件，constants.py 保留纯常量

All filesystem paths derive from a single root: MEMORIA_HOME
- Environment variable `MEMORIA_HOME` takes priority
- Default: ~/.memoria_engine/

Usage:
    from memoria_engine.config import MEMORIA_HOME, DATA_DIR
    from memoria_engine.config import HermesConfig
"""

import os
from pathlib import Path

# ═══════════════════════════════════════════════════════════════
# Root
# ═══════════════════════════════════════════════════════════════

MEMORIA_HOME = Path(
    os.environ.get("MEMORIA_HOME", Path.home() / ".memoria_engine")
).expanduser().resolve()

# ═══════════════════════════════════════════════════════════════
# Primary subdirectories
# ═══════════════════════════════════════════════════════════════

DATA_DIR       = MEMORIA_HOME / "data"
LOG_DIR        = MEMORIA_HOME / "logs"
HEARTBEAT_DIR  = MEMORIA_HOME / "health" / "heartbeats"
DB_DIR         = MEMORIA_HOME / "db"
SKILLS_DIR     = MEMORIA_HOME / "skills"
SHARED_DIR     = MEMORIA_HOME / "shared_memory"
CACHE_DIR      = MEMORIA_HOME / "cache"

# ═══════════════════════════════════════════════════════════════
# Workspace-relative convention
# ═══════════════════════════════════════════════════════════════

WORKSPACE_MEMORY_SUBDIR = ".memoria"  # subdirectory inside a user workspace

# ═══════════════════════════════════════════════════════════════
# Key file paths
# ═══════════════════════════════════════════════════════════════

DB_PATH            = DB_DIR / "memoria.db"
USER_MODEL_PATH    = DB_DIR / "user_model.db"
NUDGE_STATE_PATH   = DATA_DIR / "nudge_state.json"
ROUTE_LOG_PATH     = SHARED_DIR / "route_log.jsonl"
HEARTBEAT_MEMORY   = HEARTBEAT_DIR / "memory-nudge.json"
LOG_PATH_MEMORY    = LOG_DIR / "memory-daemon.log"
ERR_PATH_MEMORY    = LOG_DIR / "memory-daemon.err"

# ═══════════════════════════════════════════════════════════════
# Auto-create directories on first import
# ═══════════════════════════════════════════════════════════════

for _d in [DATA_DIR, LOG_DIR, HEARTBEAT_DIR, DB_DIR, SKILLS_DIR,
           SHARED_DIR, CACHE_DIR]:
    _d.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
# HermesConfig — runtime interface
# ═══════════════════════════════════════════════════════════════

class HermesConfig:
    """
    Runtime configuration facade.

    All paths are lazily resolved from MEMORIA_HOME at class-access time,
    so changing os.environ["MEMORIA_HOME"] mid-runtime takes effect.

    Usage:
        cfg = HermesConfig()
        db = sqlite3.connect(str(cfg.DB_PATH))

    Programmatic override:
        HermesConfig.override_home("/custom/path")
    """

    _home: Path | None = None

    # ── Properties (lazy, respects _home override) ──

    @classmethod
    def override_home(cls, path: str | Path):
        """Override MEMORIA_HOME for this session (e.g., testing)."""
        cls._home = Path(path).expanduser().resolve()

    @classmethod
    def _get_home(cls) -> Path:
        if cls._home is not None:
            return cls._home
        return MEMORIA_HOME

    @property
    def MEMORIA_HOME(self) -> Path:
        return self._get_home()

    @property
    def DATA_DIR(self) -> Path:       return self._get_home() / "data"
    @property
    def LOG_DIR(self) -> Path:        return self._get_home() / "logs"
    @property
    def HEARTBEAT_DIR(self) -> Path:  return self._get_home() / "health" / "heartbeats"
    @property
    def DB_DIR(self) -> Path:         return self._get_home() / "db"
    @property
    def SKILLS_DIR(self) -> Path:     return self._get_home() / "skills"
    @property
    def SHARED_DIR(self) -> Path:     return self._get_home() / "shared_memory"
    @property
    def CACHE_DIR(self) -> Path:      return self._get_home() / "cache"
    @property
    def DB_PATH(self) -> Path:        return self.DB_DIR / "memoria.db"
    @property
    def USER_MODEL_PATH(self) -> Path: return self.DB_DIR / "user_model.db"

    @staticmethod
    def workspace_memory_dir(workspace_path: str | Path) -> Path:
        """Given workspace root, return its .memoria subdirectory."""
        return Path(workspace_path) / WORKSPACE_MEMORY_SUBDIR

    @staticmethod
    def workspace_memory_file(workspace_path: str | Path) -> Path:
        """Return MEMORY.md path inside a workspace."""
        return HermesConfig.workspace_memory_dir(workspace_path) / "MEMORY.md"

# ── Clean up helper variable ──
del _d
