#!/usr/bin/env python3
"""
QA-Sentinel · 模拟环境工厂 (Mock Environments)
══════════════════════════════════════════════════════

Purpose:
    Provide mock environments that simulate different target platforms
    (WorkBuddy, OpenClaw, MaxClaw, etc.) so Pytest can validate that
    Memoria Engine's adapters correctly handle each platform's quirks.

Design Philosophy (from Hermes-Exodus QA Architecture):
    ┌─────────────────────────────────────────────────────────┐
    │  "Test against the interface, not the implementation."  │
    │                                                         │
    │  Each Mock* class injects platform-specific:            │
    │    • Environment variables (paths, tokens, config)       │
    │    • Filesystem layout (dirs that platform expects)      │
    │    • Fake data (simulated user profile, memory files)    │
    │                                                         │
    │  The adapter under test should NOT know it's mocked.    │
    └─────────────────────────────────────────────────────────┘

Mock Platform Profiles:
    - MockWorkBuddyEnv:  Simulates WorkBuddy's ~/.workbuddy/ layout
    - MockOpenClawEnv:   Simulates OpenClaw's ~/.openclaw/ layout
    - MockGenericEnv:    Simulates a clean "first install" environment

Usage (in pytest tests):
    ```python
    from tests.qa_sentinel.mock_environments import MockWorkBuddyEnv

    def test_adapter_detects_workbuddy():
        with MockWorkBuddyEnv() as env:
            cfg = HermesConfig()
            assert cfg.MEMORIA_HOME == env.expected_home
            assert env.skills_dir.exists()
    ```

Author: Hermes-Nexus QA Corps
License: BSL 1.1
"""

import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import patch


# ═══════════════════════════════════════════════════════════
# Data Structures
# ═══════════════════════════════════════════════════════════

@dataclass
class PlatformProfile:
    """
    Defines what a target platform's environment looks like.

    This is the "ground truth" that adapters must conform to.
    When a platform updates its spec (detected by doc_fetcher.py),
    this profile should be updated and re-validated against.
    """
    name: str                     # "workbuddy" | "openclaw" | ...
    env_home_var: str             # e.g., "WORKBUDDY_HOME" or "OPENCLAW_HOME"
    default_home_subdir: str      # e.g., ".workbuddy" or ".openclaw"
    skills_subdir: str            # e.g., "skills" or "plugins"
    memory_subdir: str            # e.g., "memory" or "data/memory"
    config_files: List[str]       # e.g., ["models.json", "mcp.json"]
    expected_db_name: str         # e.g., "workbuddy.db" or "openclaw.db"
    skill_format: str             # "SKILL.md" (WorkBuddy) or "plugin.yaml" (OpenClaw)


# ═══════════════════════════════════════════════════════════
# Platform Profiles (Ground Truth)
# ═══════════════════════════════════════════════════════════

WORKBUDDY_PROFILE = PlatformProfile(
    name="workbuddy",
    env_home_var="WORKBUDDY_HOME",
    default_home_subdir=".workbuddy",
    skills_subdir="skills",
    memory_subdir="memory",
    config_files=["models.json", "mcp.json", "settings.json"],
    expected_db_name="workbuddy.db",
    skill_format="SKILL.md",
)

OPENCLAW_PROFILE = PlatformProfile(
    name="openclaw",
    env_home_var="OPENCLAW_HOME",
    default_home_subdir=".openclaw",
    skills_subdir="plugins",
    memory_subdir="data/memory",
    config_files=["openclaw.yaml", "channels.json"],
    expected_db_name="openclaw.db",
    skill_format="plugin.yaml",
)

# Reserved for future:
# MAXCLAW_PROFILE = PlatformProfile(...)
# AUTOCLAW_PROFILE = PlatformProfile(...)
# QCLAW_PROFILE = PlatformProfile(...)


# ═══════════════════════════════════════════════════════════
# Mock Environment Base
# ═══════════════════════════════════════════════════════════

class MockPlatformEnv:
    """
    Base class for platform-specific mock environments.

    Creates a temporary directory structure that mimics the target
    platform's expected layout. Inject MEMORIA_HOME and platform-specific
    env vars so that Memoria Engine's config resolution works correctly.

    Subclasses MUST override:
        profile: PlatformProfile

    Subclasses MAY override:
        _populate_files() — to add platform-specific fake data
    """

    profile: PlatformProfile = None  # Set in subclass

    def __init__(self, base_dir: Optional[Path] = None):
        """
        Args:
            base_dir: If None, creates a temp dir. Otherwise, uses given path.
                     Useful for inspecting mock state after test.
        """
        self.base_dir = base_dir or Path(tempfile.mkdtemp(prefix=f"qa_sentinel_{self.profile.name}_"))
        self.home_dir = self.base_dir / self.profile.default_home_subdir
        self.expected_home = self.home_dir

        # Sub-paths
        self.skills_dir = self.home_dir / self.profile.skills_subdir
        self.memory_dir = self.home_dir / self.profile.memory_subdir
        self.db_path = self.home_dir / self.profile.expected_db_name

        # Track original env for restoration
        self._original_env: Dict[str, str] = {}

    def setup(self):
        """Create directory structure and populate fake data."""
        # Create directories
        for d in [self.home_dir, self.skills_dir, self.memory_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # Create config files
        for config_file in self.profile.config_files:
            (self.home_dir / config_file).touch()

        # Populate platform-specific fake data
        self._populate_files()

    def teardown(self):
        """Clean up temporary directory and restore environment."""
        if self.base_dir.exists():
            shutil.rmtree(self.base_dir, ignore_errors=True)

    def _populate_files(self):
        """
        Override in subclass to add platform-specific fake data.

        Examples:
            - Create a fake SKILL.md for WorkBuddy
            - Create a fake plugin.yaml for OpenClaw
            - Write fake memory files
        """
        pass

    def inject_env(self):
        """
        Inject environment variables that Memoria Engine's config.py reads.

        Sets MEMORIA_HOME to point at our mock home_dir.
        Also sets platform-specific env vars (WORKBUDDY_HOME, OPENCLAW_HOME).
        """
        self._original_env["MEMORIA_HOME"] = os.environ.get("MEMORIA_HOME", "")
        self._original_env[self.profile.env_home_var] = os.environ.get(self.profile.env_home_var, "")

        os.environ["MEMORIA_HOME"] = str(self.home_dir)
        os.environ[self.profile.env_home_var] = str(self.home_dir)

    def restore_env(self):
        """Restore original environment variables."""
        for key, original_value in self._original_env.items():
            if original_value:
                os.environ[key] = original_value
            else:
                os.environ.pop(key, None)

    def __enter__(self):
        """Context manager entry."""
        self.setup()
        self.inject_env()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.restore_env()
        self.teardown()
        return False  # Don't suppress exceptions


# ═══════════════════════════════════════════════════════════
# Platform-Specific Mock Environments
# ═══════════════════════════════════════════════════════════

class MockWorkBuddyEnv(MockPlatformEnv):
    """
    Simulates WorkBuddy's ~/.workbuddy/ environment.

    Creates:
        ~/.workbuddy/
        ├── models.json          (fake model config)
        ├── mcp.json             (fake MCP config)
        ├── settings.json        (fake settings)
        ├── skills/
        │   └── test_skill/
        │       └── SKILL.md     (fake skill definition)
        ├── memory/
        │   ├── MEMORY.md        (fake curated memory)
        │   └── 2026-05-21.md    (fake daily log)
        └── workbuddy.db         (empty SQLite placeholder)

    Environment variables injected:
        MEMORIA_HOME   → ~/.workbuddy/
        WORKBUDDY_HOME → ~/.workbuddy/
    """

    profile = WORKBUDDY_PROFILE

    def _populate_files(self):
        """Create fake WorkBuddy files for adapter testing."""
        # Fake SKILL.md
        skill_dir = self.skills_dir / "test_skill"
        skill_dir.mkdir(exist_ok=True)
        (skill_dir / "SKILL.md").write_text("""---
name: test_skill
description: A test skill for QA validation
version: 1.0.0
allowed-tools:
  - Read
  - Write
---

# Test Skill

This is a mock skill for testing the WorkBuddy adapter.
""")

        # Fake MEMORY.md
        (self.memory_dir / "MEMORY.md").write_text("""# MEMORY.md (Mock)

- User prefers concise responses
- Project: QA-Sentinel testing
""")

        # Fake daily log
        (self.memory_dir / "2026-05-21.md").write_text("""# 2026-05-21 (Mock)

- Mock daily log entry for adapter testing
""")

        # Fake models.json (WorkBuddy format)
        (self.home_dir / "models.json").write_text(json.dumps({
            "models": [
                {"id": "gpt-4o", "provider": "openai"},
                {"id": "deepseek-v3", "provider": "deepseek"},
            ]
        }, indent=2))

        # Fake mcp.json
        (self.home_dir / "mcp.json").write_text(json.dumps({
            "mcpServers": {
                "test-server": {
                    "command": "echo",
                    "args": ["test"]
                }
            }
        }, indent=2))

        # Fake workbuddy.db (empty placeholder)
        self.db_path.touch()


class MockOpenClawEnv(MockPlatformEnv):
    """
    Simulates OpenClaw's ~/.openclaw/ environment.

    Creates:
        ~/.openclaw/
        ├── openclaw.yaml        (fake OpenClaw config)
        ├── channels.json        (fake channel config)
        ├── plugins/
        │   └── test_plugin/
        │       └── plugin.yaml  (fake plugin definition)
        ├── data/
        │   └── memory/
        │       └── memory.json  (fake memory store)
        └── openclaw.db          (empty SQLite placeholder)

    Environment variables injected:
        MEMORIA_HOME   → ~/.openclaw/
        OPENCLAW_HOME  → ~/.openclaw/
    """

    profile = OPENCLAW_PROFILE

    def _populate_files(self):
        """Create fake OpenClaw files for adapter testing."""
        # Fake plugin.yaml
        plugin_dir = self.skills_dir / "test_plugin"
        plugin_dir.mkdir(exist_ok=True)
        (plugin_dir / "plugin.yaml").write_text("""name: test_plugin
description: A test plugin for QA validation
version: 1.0.0
hooks:
  - on_message
  - on_startup
config:
  api_key: ""
  model: gpt-4o
""")

        # Fake memory.json (OpenClaw format)
        (self.memory_dir / "memory.json").write_text(json.dumps({
            "facts": [
                {"key": "user_name", "value": "QA Tester"},
                {"key": "preferred_language", "value": "en"},
            ],
            "sessions": []
        }, indent=2))

        # Fake openclaw.yaml
        (self.home_dir / "openclaw.yaml").write_text("""bot:
  name: test-bot
  model: gpt-4o
channels:
  - type: cli
    enabled: true
""")

        # Fake channels.json
        (self.home_dir / "channels.json").write_text(json.dumps({
            "channels": [
                {"type": "discord", "enabled": False},
                {"type": "telegram", "enabled": False},
            ]
        }, indent=2))

        # Fake openclaw.db (empty placeholder)
        self.db_path.touch()


class MockGenericEnv(MockPlatformEnv):
    """
    Simulates a clean "first install" environment — no prior platform detected.

    This is the baseline for testing Memoria Engine's ability to start
    from scratch with zero pre-existing config.

    Creates:
        ~/.memoria_engine/       (bare minimum — just the directory itself)

    Environment variables injected:
        MEMORIA_HOME   → ~/.memoria_engine/
    """

    profile = PlatformProfile(
        name="generic",
        env_home_var="MEMORIA_HOME",
        default_home_subdir=".memoria_engine",
        skills_subdir="skills",
        memory_subdir="memory",
        config_files=["config.yaml"],
        expected_db_name="memoria.db",
        skill_format="SKILL.md",
    )

    def _populate_files(self):
        """Generic env: no pre-existing data — just an empty home."""
        pass


# ═══════════════════════════════════════════════════════════
# Pytest Fixtures (Ready-to-Use)
# ═══════════════════════════════════════════════════════════

# These are designed to be imported directly in conftest.py:
#
#   from tests.qa_sentinel.mock_environments import (
#       workbuddy_env,
#       openclaw_env,
#       generic_env,
#   )
#
# Usage in test:
#   def test_workbuddy_adapter(workbuddy_env):
#       from memoria_engine.config import HermesConfig
#       cfg = HermesConfig()
#       assert cfg.MEMORIA_HOME == workbuddy_env.expected_home

@contextmanager
def workbuddy_env():
    """Pytest-compatible context manager for WorkBuddy mock environment."""
    with MockWorkBuddyEnv() as env:
        yield env


@contextmanager
def openclaw_env():
    """Pytest-compatible context manager for OpenClaw mock environment."""
    with MockOpenClawEnv() as env:
        yield env


@contextmanager
def generic_env():
    """Pytest-compatible context manager for generic (clean) environment."""
    with MockGenericEnv() as env:
        yield env


# ═══════════════════════════════════════════════════════════
# Quick Smoke Test (run directly)
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("🧪 QA-Sentinel Mock Environment Smoke Test\n")

    for env_class in [MockWorkBuddyEnv, MockOpenClawEnv, MockGenericEnv]:
        name = env_class.__name__
        with env_class() as env:
            print(f"✅ {name}:")
            print(f"   home_dir   = {env.home_dir}")
            print(f"   skills_dir = {env.skills_dir} (exists={env.skills_dir.exists()})")
            print(f"   memory_dir = {env.memory_dir} (exists={env.memory_dir.exists()})")
            print(f"   MEMORIA_HOME env = {os.environ.get('MEMORIA_HOME', 'NOT SET')}")
            print(f"   {env.profile.env_home_var} = {os.environ.get(env.profile.env_home_var, 'NOT SET')}")

            # Verify skills dir has content
            if env.skills_dir.exists():
                contents = list(env.skills_dir.rglob("*"))
                print(f"   skills contents: {len(contents)} files/dirs")
                for item in contents[:5]:
                    print(f"     - {item.relative_to(env.home_dir)}")

            print()

    print("🎉 All mock environments initialized and cleaned up successfully.")
