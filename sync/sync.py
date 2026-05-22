#!/usr/bin/env python3
"""
sync.py — Hermes-Nexus 本地同步引擎
=====================================

Design: Step 1 设计图纸 §贰 & §叁
- §贰：29 源文件 → 8 子系统 映射字典 + Import 路径重写
- §叁：四阶段 HITL 交互流水线（收集 → 差异 → 安全 → 确认）

Core API:
    python3 sync.py --diff              # 预览差异（默认，安全只读）
    python3 sync.py --diff --full       # 预览差异 + 详细 diff
    python3 sync.py --apply             # 执行同步（需二次确认）
    python3 sync.py --security-only     # 仅运行安全扫描
    python3 sync.py --export            # 导出状态报告到 JSON

安全原则：
    - BLOCKER 硬性拒绝，不可绕过
    - 临时副本 → 脱敏 → 二次验证 → 原子复制
    - 自动 commit，不自动 push

边界裁决（CTO 已确认）：
    1. 手动运行同步
    2. 一次性全量同步
    3. constants.py 保留纯常量，路径剥离至 config.py
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Import our security scanner (same directory)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from security_scan import (
    Finding,
    ScanResult,
    scan_and_redact,
    scan_file,
    scan_directory,
)

# Replacement constants — used for display in HITL summaries
REDACTED_API_KEY = "<REDACTED_API_KEY>"
REDACTED_BOT_TOKEN = "<REDACTED_BOT_TOKEN>"
REDACTED_PATH = "<REDACTED_PATH>"
REDACTED_EMAIL = "<REDACTED_EMAIL>"
REDACTED_DB_URL = "<REDACTED_DB_URL>"

# ═══════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════

# ── Source root: all Hermes skills live here ──
SKILLS_ROOT = Path.home() / ".workbuddy" / "skills"

# ── Target root: Hermes-Nexus project (open-source repo on Desktop) ──
PROJECT_ROOT = Path.home() / "Desktop" / "Hermes-Nexus"
MEMORIA_ENGINE = PROJECT_ROOT / "memoria_engine"

# ── Directories to create in target ──
TARGET_SUBDIRS = [
    "memory",
    "semantic",
    "cron",
    "kanban",
    "daemon",
    "skills",
    "models",
    "utils",
]


# ═══════════════════════════════════════════════════════════════
# §贰·2.1 — SYNC_MAP: 源路径 → 目标路径 + 转换规则
# ═══════════════════════════════════════════════════════════════

SYNC_MAP = {
    # ── A. 引擎入口 & 配置 ──
    "enhanced-memory/scripts/constants.py": {
        "target": "memoria_engine/constants.py",
        "transform": "rewrite_paths",
        "note": "仅保留纯常量（容量/阈值/权重），路径常量迁移到 config.py",
    },

    # ── B. 记忆子系统 (8 脚本) ──
    "enhanced-memory/scripts/session_state.py": {
        "target": "memoria_engine/memory/session_state.py",
        "transform": "rewrite_imports",
    },
    "enhanced-memory/scripts/memory_pool.py": {
        "target": "memoria_engine/memory/memory_pool.py",
        "transform": "rewrite_imports",
    },
    "enhanced-memory/scripts/memory_index.py": {
        "target": "memoria_engine/memory/memory_index.py",
        "transform": "rewrite_imports",
    },
    "enhanced-memory/scripts/memory_compress.py": {
        "target": "memoria_engine/memory/memory_compress.py",
        "transform": "rewrite_imports",
    },
    "enhanced-memory/scripts/memory_nudge.py": {
        "target": "memoria_engine/memory/memory_nudge.py",
        "transform": "rewrite_imports",
    },
    "enhanced-memory/scripts/memory_quality.py": {
        "target": "memoria_engine/memory/memory_quality.py",
        "transform": "rewrite_imports",
    },
    "enhanced-memory/scripts/git_sync.py": {
        "target": "memoria_engine/memory/git_sync.py",
        "transform": "rewrite_imports",
    },
    "enhanced-memory/scripts/write_verifier.py": {
        "target": "memoria_engine/memory/write_verifier.py",
        "transform": "rewrite_imports",
    },

    # ── C. 语义子系统 (4 脚本) ──
    "enhanced-memory/scripts/embeddings.py": {
        "target": "memoria_engine/semantic/embeddings.py",
        "transform": "rewrite_imports",
    },
    "enhanced-memory/scripts/vector_memory_provider.py": {
        "target": "memoria_engine/semantic/vector_memory.py",
        "rename": True,
        "transform": "rewrite_imports",
    },
    "enhanced-memory/scripts/intent_learner.py": {
        "target": "memoria_engine/semantic/intent_learner.py",
        "transform": "rewrite_imports",
    },
    "enhanced-memory/scripts/intent_embedder.py": {
        "target": "memoria_engine/semantic/intent_embedder.py",
        "transform": "rewrite_imports",
    },

    # ── D. 调度子系统 (2 脚本) ──
    "hermes-cron/scripts/cron_parser.py": {
        "target": "memoria_engine/cron/parser.py",
        "rename": True,
        "transform": "rewrite_imports",
    },
    "hermes-cron/scripts/cron_scheduler.py": {
        "target": "memoria_engine/cron/scheduler.py",
        "rename": True,
        "transform": "rewrite_imports",
    },

    # ── E. 看板子系统 (3 脚本) ──
    "hermes-kanban/scripts/kanban_db.py": {
        "target": "memoria_engine/kanban/db.py",
        "rename": True,
        "transform": "rewrite_imports + rewrite_paths",
    },
    "hermes-kanban/scripts/kanban_worker.py": {
        "target": "memoria_engine/kanban/worker.py",
        "rename": True,
        "transform": "rewrite_imports",
    },
    "hermes-kanban/scripts/kanban_scheduler.py": {
        "target": "memoria_engine/kanban/scheduler.py",
        "rename": True,
        "transform": "rewrite_imports",
    },

    # ── F. 守护进程子系统 (3 脚本) ──
    "enhanced-memory/scripts/daemon_health.py": {
        "target": "memoria_engine/daemon/health.py",
        "rename": True,
        "transform": "rewrite_imports + rewrite_paths",
    },
    "enhanced-memory/scripts/memory_daemon.py": {
        "target": "memoria_engine/daemon/memory_daemon.py",
        "rename": True,
        "transform": "rewrite_imports + rewrite_paths",
    },
    "enhanced-memory/scripts/health_test_battery.py": {
        "target": "memoria_engine/daemon/health_test_battery.py",
        "rename": False,
        "transform": "rewrite_imports + rewrite_paths",
    },

    # ── G. 技能子系统 (3 脚本) ──
    "enhanced-memory/scripts/skill_detector.py": {
        "target": "memoria_engine/skills/detector.py",
        "rename": True,
        "transform": "rewrite_imports",
    },
    "enhanced-memory/scripts/skill_creator.py": {
        "target": "memoria_engine/skills/creator.py",
        "rename": True,
        "transform": "rewrite_imports",
    },
    "enhanced-memory/scripts/confidence_scorer.py": {
        "target": "memoria_engine/skills/confidence_scorer.py",
        "rename": False,
        "transform": "rewrite_imports",
    },

    # ── H. 模型子系统 (2 脚本) ──
    "enhanced-memory/scripts/user_model.py": {
        "target": "memoria_engine/models/user_model.py",
        "transform": "rewrite_imports",
    },
    "enhanced-memory/scripts/sequence_analyzer.py": {
        "target": "memoria_engine/models/sequence_analyzer.py",
        "transform": "rewrite_imports",
    },

    # ── I. 工具子系统 (3 脚本) ──
    "enhanced-memory/scripts/correction_tracker.py": {
        "target": "memoria_engine/utils/correction_tracker.py",
        "transform": "rewrite_imports",
    },
    "enhanced-memory/scripts/agent_router.py": {
        "target": "memoria_engine/utils/agent_router.py",
        "transform": "rewrite_imports",
    },
    "enhanced-memory/scripts/session_recovery.py": {
        "target": "memoria_engine/utils/session_recovery.py",
        "transform": "rewrite_imports",
    },
}


# ═══════════════════════════════════════════════════════════════
# §贰·2.3 — IMPORT_REWRITE: 原 import → 新 import 映射表
# ═══════════════════════════════════════════════════════════════

IMPORT_REWRITE = {
    # ── 自引用（包内相对导入）──
    # 所有内部 import 改为显式相对导入（不依赖 sys.path 技巧）
    "from constants import":      "from ..constants import",
    "from memory_pool import":    "from ..memory.memory_pool import",
    "from embeddings import":     "from ..semantic.embeddings import",
    "from user_model import":     "from ..models.user_model import",
    "from skill_detector import": "from ..skills.detector import",
    "from vector_memory_provider import": "from ..semantic.vector_memory import",
    "from agent_router import":   "from ..utils.agent_router import",
    "from session_state import":  "from ..memory.session_state import",
    "from session_recovery import": "from ..utils.session_recovery import",
    "from write_verifier import": "from ..memory.write_verifier import",
    "from correction_tracker import": "from ..utils.correction_tracker import",
    "from confidence_scorer import": "from ..skills.confidence_scorer import",
    "from sequence_analyzer import": "from ..models.sequence_analyzer import",
}


# ═══════════════════════════════════════════════════════════════
# §贰·2.4 — 已明确排除的脚本
# ═══════════════════════════════════════════════════════════════

EXCLUDED_SCRIPTS = [
    "hermes-exec/scripts/execute_code.py",
    "hermes-exec/scripts/sandbox.py",
    "hermes-exec/scripts/rpc_server.py",
    "hermes-portable-bootstrap/scripts/bootstrap.py",
]


# ═══════════════════════════════════════════════════════════════
# Phase 1: 收集源状态
# ═══════════════════════════════════════════════════════════════

def collect_source_files() -> Dict[str, Path]:
    """Collect all source files from SYNC_MAP, verifying they exist."""
    files = {}
    for rel_path in SYNC_MAP:
        full_path = SKILLS_ROOT / rel_path
        if full_path.exists():
            files[rel_path] = full_path
        else:
            files[rel_path] = None  # missing — will be reported in diff
    return files


def collect_target_files() -> Dict[str, Path]:
    """Collect existing target files in memoria_engine/."""
    files = {}
    for src_rel, cfg in SYNC_MAP.items():
        tgt_rel = cfg["target"]
        full_path = PROJECT_ROOT / tgt_rel
        if full_path.exists():
            files[tgt_rel] = full_path
        else:
            files[tgt_rel] = None
    return files


# ═══════════════════════════════════════════════════════════════
# Phase 2: 差异分析
# ═══════════════════════════════════════════════════════════════

def file_checksum(path: Path) -> str:
    """Compute SHA256 hash of a file."""
    if not path or not path.exists():
        return ""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def compute_diff(
    source_files: Dict[str, Path],
    target_files: Dict[str, Path],
) -> Dict[str, list]:
    """
    Compare source and target files to determine sync status.

    Returns:
        {
            "new":       [(src_rel, tgt_rel, reason), ...]
            "modified":  [(src_rel, tgt_rel, reason), ...]
            "deleted":   [(tgt_rel, reason), ...]
            "unchanged": [(src_rel, tgt_rel), ...]
            "missing":   [(src_rel, reason), ...]
        }
    """
    new = []
    modified = []
    deleted = []
    unchanged = []
    missing = []

    for src_rel, cfg in SYNC_MAP.items():
        tgt_rel = cfg["target"]
        src_path = source_files.get(src_rel)
        tgt_path = target_files.get(tgt_rel)

        # Source file missing
        if src_path is None:
            reason = f"源文件不存在: {SKILLS_ROOT / src_rel}"
            missing.append((src_rel, reason))
            continue

        # Target file doesn't exist yet — NEW
        if tgt_path is None:
            reason = f"新文件: {src_rel} → {tgt_rel}"
            new.append((src_rel, tgt_rel, reason))
            continue

        # Both exist — compare checksums
        src_hash = file_checksum(src_path)
        tgt_hash = file_checksum(tgt_path)

        if src_hash == tgt_hash:
            unchanged.append((src_rel, tgt_rel))
        else:
            reason = f"内容已变更: {src_rel} → {tgt_rel}"
            modified.append((src_rel, tgt_rel, reason))

    # Check for orphaned target files (in target but no longer in SYNC_MAP)
    # This is rare for initial sync but matters for ongoing use
    all_targets = {cfg["target"] for cfg in SYNC_MAP.values()}
    for tgt_rel in target_files:
        if tgt_rel not in all_targets and target_files[tgt_rel] is not None:
            deleted.append((tgt_rel, "目标文件已从 SYNC_MAP 中移除"))

    return {
        "new": new,
        "modified": modified,
        "deleted": deleted,
        "unchanged": unchanged,
        "missing": missing,
    }


# ═══════════════════════════════════════════════════════════════
# Phase 3: 安全扫描
# ═══════════════════════════════════════════════════════════════

def security_scan(
    source_files: Dict[str, Path],
) -> Dict[str, list]:
    """
    Run security scanner on all source files.

    Returns:
        {
            "blocked":  [(file, line, severity, match), ...],
            "warnings": [(file, line, severity, match, replacement), ...],
            "clean":    [file, ...],
        }
    """
    blocked = []
    warnings = []
    clean = []

    for src_rel, src_path in source_files.items():
        if src_path is None:
            continue

        result: ScanResult = scan_file(str(src_path))

        # Check for BLOCKER findings
        file_blockers = [f for f in result.findings if f.severity == "BLOCKER"]
        if file_blockers:
            for f in file_blockers:
                blocked.append((str(src_path), f.line, f.severity, f.match))
        elif result.status == "blocked":
            # Entire file blocked (e.g., blacklisted extension)
            reason = result.findings[0].match if result.findings else "未知原因"
            blocked.append((
                str(src_path), 0, "BLOCKER",
                f"文件被黑名单拦截: {reason}"
            ))
        # Check for WARNING findings
        elif any(f.severity in ("WARNING",) for f in result.findings):
            for f in result.findings:
                if f.severity == "WARNING":
                    replacement = _get_replacement_for_finding(f)
                    warnings.append((
                        str(src_path), f.line, f.severity,
                        f.match, replacement
                    ))
        else:
            clean.append(str(src_path))

    return {
        "blocked": blocked,
        "warnings": warnings,
        "clean": clean,
    }


def _get_replacement_for_finding(f: Finding) -> str:
    """Map finding category to its replacement string."""
    replacements = {
        "personal_path": REDACTED_PATH,
        "api_key": REDACTED_API_KEY,
        "bot_token": REDACTED_BOT_TOKEN,
        "email_pii": REDACTED_EMAIL,
        "db_conn": REDACTED_DB_URL,
    }
    return replacements.get(f.category, "<REDACTED>")


# ═══════════════════════════════════════════════════════════════
# Import & Path Rewriting
# ═══════════════════════════════════════════════════════════════

def apply_import_rewrites(content: str, target_rel: str) -> str:
    """
    Rewrite inter-module imports for the memoria_engine package.

    Handles these patterns:
        from constants import X   →   from ..constants import X
        from memory_pool import X →   from ..memory.memory_pool import X

    Design: §贰·2.3 — all internal imports become explicit relative imports.
    """
    lines = content.split("\n")
    rewritten_lines = []

    for line_no, line in enumerate(lines):
        stripped = line.lstrip()
        indent = line[:len(line) - len(stripped)]

        # Check against IMPORT_REWRITE patterns
        rewritten = False
        for old_pattern, new_pattern in IMPORT_REWRITE.items():
            if stripped.startswith(old_pattern):
                # Determine the correct relative import depth
                # Files in memoria_engine/ → use "from .xxx import"
                # Files in memoria_engine/<sub>/ → use "from ..xxx import"
                depth = target_rel.count("/")
                if depth == 1:
                    # Top-level file in memoria_engine/ (e.g., constants.py)
                    # "from constants import WORKBUDDY_DIR" → already correct, skip
                    if "constants" in old_pattern:
                        rewritten_lines.append(line)  # keep as-is
                        rewritten = True
                        break
                    # Other imports at top level
                    if old_pattern.startswith("from constants"):
                        # At top level, relative to package root
                        pass

                # Standard rewrite: add relative prefix
                new_line = stripped.replace(old_pattern, new_pattern, 1)
                rewritten_lines.append(indent + new_line)
                rewritten = True
                break

        if not rewritten:
            rewritten_lines.append(line)

    return "\n".join(rewritten_lines)


def apply_path_rewrites(content: str, target_rel: str) -> str:
    """
    Rewrite hardcoded ~/.workbuddy paths to config-based references.

    This handles patterns like:
        WORKBUDDY_DIR = Path.home() / ".workbuddy"
        HEARTBEAT_DIR = WORKBUDDY_DIR / "data" / "heartbeats"

    Design: §贰·2.1 transform="rewrite_paths"
    """
    lines = content.split("\n")
    rewritten_lines = []

    for line in lines:
        # ── Pattern 1: Path.home() / ".workbuddy" ──
        if 'Path.home' in line and '.workbuddy' in line:
            # Replace with: config.MEMORIA_HOME
            line = line.replace(
                'Path.home() / ".workbuddy"',
                "MEMORIA_HOME  # config.MEMORIA_HOME"
            )
            # Insert a comment to guide the developer
            line = line.rstrip() + "  # ← TO_MIGRATE: use config.MEMORIA_HOME"

        # ── Pattern 2: Literal ~/.workbuddy strings ──
        if "~/.workbuddy" in line:
            line = line.replace("~/.workbuddy", "{MEMORIA_HOME}")
            line = line.rstrip() + "  # ← TO_MIGRATE: use config.MEMORIA_HOME"

        # ── Pattern 3: /Users/<user>/.workbuddy ──
        if "/Users/" in line and ".workbuddy" in line:
            # Tag for manual review
            line = line.rstrip() + "  # ← REVIEW: contains user path"

        rewritten_lines.append(line)

    return "\n".join(rewritten_lines)


# ═══════════════════════════════════════════════════════════════
# Phase 4: HITL 交互确认 & 执行
# ═══════════════════════════════════════════════════════════════

def display_change_summary(changes: Dict[str, list]) -> None:
    """Display a color-coded summary of pending changes."""
    new_count = len(changes["new"])
    mod_count = len(changes["modified"])
    del_count = len(changes["deleted"])
    miss_count = len(changes["missing"])

    print(f"\n  📋 变更摘要：")
    print()

    if new_count:
        print(f"  ✨ NEW ({new_count})")
        for src, tgt, reason in changes["new"]:
            print(f"    {changes['new'].index((src, tgt, reason)) + 1}. {tgt}")
            print(f"       原因: {reason}")

    if mod_count:
        print(f"\n  📝 MODIFIED ({mod_count})")
        for src, tgt, reason in changes["modified"]:
            print(f"    {changes['modified'].index((src, tgt, reason)) + 1}. {tgt}")
            print(f"       原因: {reason}")

    if del_count:
        print(f"\n  ❌ DELETED ({del_count})")
        for tgt, reason in changes["deleted"]:
            print(f"    - {tgt}")
            print(f"      原因: {reason}")

    if miss_count:
        print(f"\n  ⚠️  MISSING ({miss_count})")
        for src, reason in changes["missing"]:
            print(f"    - {src}")
            print(f"      原因: {reason}")

    if not any([new_count, mod_count, del_count]):
        print("  ✅ 无变更。所有文件已同步。")


def display_warnings_summary(warnings: list) -> None:
    """Display security warnings that will be auto-replaced."""
    if warnings:
        print(f"\n  ⚠️  WARNINGS ({len(warnings)})")
        for file_path, line, severity, match, replacement in warnings:
            fname = Path(file_path).name
            print(f"    {fname}:{line} — [{severity}]")
            print(f"      匹配: {match}")
            print(f"      替换为: {replacement}")


# ═══════════════════════════════════════════════════════════════
# 执行流水线：临时副本 → 脱敏 → 二次验证 → 原子复制 → commit
# ═══════════════════════════════════════════════════════════════

def _create_temp_staging(
    changes: Dict[str, list],
    source_files: Dict[str, Path],
) -> Path:
    """Create temporary staging directory with all files to sync."""
    temp_dir = Path(tempfile.mkdtemp(prefix="hermes_nexus_sync_"))

    # Create subdirectories in temp
    for subdir in TARGET_SUBDIRS:
        (temp_dir / "memoria_engine" / subdir).mkdir(parents=True, exist_ok=True)

    return temp_dir


def _stage_file(
    changes: Dict[str, list],
    source_files: Dict[str, Path],
    temp_dir: Path,
    security_results: Dict[str, list],
) -> List[str]:
    """
    Stage one file: read source → security redact → import rewrite → path rewrite → write to temp.

    Returns list of files successfully staged.
    """
    staged = []

    # Build a lookup from warnings for this specific file
    warnings_lookup = {}  # file_path → [(line, match, replacement), ...]
    for file_path, line, severity, match, replacement in security_results["warnings"]:
        if file_path not in warnings_lookup:
            warnings_lookup[file_path] = []
        warnings_lookup[file_path].append((line, match, replacement))

    for src_rel, tgt_rel, reason in changes["new"] + changes["modified"]:
        src_path = source_files[src_rel]
        if src_path is None:
            continue

        cfg = SYNC_MAP.get(src_rel, {})
        transform = cfg.get("transform", "rewrite_imports")

        try:
            content = src_path.read_text()
        except Exception as e:
            print(f"  ✗ {tgt_rel}: 读取失败 — {e}")
            continue

        # Step 2a: Security redact
        if str(src_path) in security_results["clean"]:
            pass  # No sensitive content — skip redaction
        else:
            redacted_content, findings = scan_and_redact(content, str(src_path))
            # Only apply replacement for WARNING-level findings
            # (Blocker-level files are already excluded from staging)
            content = redacted_content

        # Step 2b: Import path rewrites
        if "rewrite_imports" in transform:
            content = apply_import_rewrites(content, tgt_rel)

        # Step 2c: Path rewrites (constants → config)
        if "rewrite_paths" in transform:
            content = apply_path_rewrites(content, tgt_rel)

        # Step 2d: Write to temp staging
        temp_path = temp_dir / tgt_rel
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(content)

        staged.append(tgt_rel)
        print(f"  ✓ {tgt_rel} (脱敏完成)")

    return staged


def _secondary_verify(temp_dir: Path) -> Dict[str, list]:
    """
    Run security scan on temp staging directory.
    If any BLOCKER found, the staging is tainted and sync must abort.
    """
    results = scan_directory(str(temp_dir))
    blocked = []

    for result in results:
        file_blockers = [f for f in result.findings if f.severity == "BLOCKER"]
        for f in file_blockers:
            blocked.append((result.file_path, f.line, f.severity, f.match))

    return {
        "blocked": blocked,
        "passed": [r.file_path for r in results if not blocked],
    }


def _atomic_copy_to_project(temp_dir: Path) -> bool:
    """Copy files from temp staging → hermes-nexus/memoria_engine/ atomically."""
    temp_memoria = temp_dir / "memoria_engine"
    if not temp_memoria.exists():
        return False

    try:
        # Ensure target directories exist
        for subdir in TARGET_SUBDIRS:
            (MEMORIA_ENGINE / subdir).mkdir(parents=True, exist_ok=True)

        # Copy each file
        for root, _, files in os.walk(temp_memoria):
            for fname in files:
                src_file = Path(root) / fname
                rel = src_file.relative_to(temp_dir)
                tgt_file = PROJECT_ROOT / rel
                tgt_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, tgt_file)

        return True
    except Exception as e:
        print(f"  ⛔ 原子复制失败: {e}")
        return False


def _generate_commit_message(changes: Dict[str, list]) -> str:
    """Generate a structured commit message."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    new_count = len(changes["new"])
    mod_count = len(changes["modified"])

    lines = [f"sync: enhanced-memory → memoria_engine ({date_str})", ""]

    if new_count:
        lines.append(f"New files: {new_count}")
    if mod_count:
        lines.append(f"Modified files: {mod_count}")
    lines.append("")

    for src, tgt, reason in changes["new"] + changes["modified"]:
        # Get short description from the change reason
        desc = reason.split(":", 1)[0].strip() if ":" in reason else reason
        fname = Path(tgt).name
        lines.append(f"- {fname}: {desc}")

    lines.append("")
    lines.append("Security: automated redaction applied via security_scan.py")

    return "\n".join(lines)


def _git_commit(commit_msg: str) -> bool:
    """Stage all changes and create a commit. Does NOT push."""
    try:
        os.chdir(PROJECT_ROOT)
        subprocess.run(
            ["git", "add", "memoria_engine/"],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            check=True,
            capture_output=True,
            text=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"  ✗ Git commit 失败: {e.stderr}")
        return False


def execute_sync(
    changes: Dict[str, list],
    source_files: Dict[str, Path],
    security_results: Dict[str, list],
) -> bool:
    """
    Execute the full sync pipeline.

    Flow (§叁·3.3):
        1. Create temp staging
        2. Stage files (redact + rewrite + write)
        3. Secondary security verification
        4. Atomic copy to hermes-nexus/
        5. Git commit (no push)
        6. Cleanup

    Returns True on success, False on failure.
    """
    # ── Step 1: Create temp staging ──
    print("\n📦 正在创建脱敏副本...")
    temp_dir = _create_temp_staging(changes, source_files)

    # ── Step 2: Stage files ──
    print("\n✂️  正在脱敏 & 转换...")
    staged = _stage_file(changes, source_files, temp_dir, security_results)

    if not staged:
        print("\n⚠️  没有文件被成功暂存。")
        shutil.rmtree(temp_dir, ignore_errors=True)
        return False

    # ── Step 3: Secondary verification ──
    print("\n🔒 正在执行二次安全验证...")
    final_scan = _secondary_verify(temp_dir)

    if final_scan["blocked"]:
        print("\n⛔ 二次验证失败！脱敏过程存在遗漏。同步已中止。")
        print("   请手动检查以下文件:")
        for file_path, line, severity, match in final_scan["blocked"]:
            print(f"   {file_path}:{line} — {match}")
        shutil.rmtree(temp_dir, ignore_errors=True)
        return False

    print(f"  ✅ 二次验证通过 ({len(final_scan['passed'])} 个文件)")

    # ── Step 4: Atomic copy ──
    print("\n📋 正在写入目标目录...")
    success = _atomic_copy_to_project(temp_dir)

    if not success:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return False

    # ── Step 5: Git commit ──
    print("\n📝 正在生成提交...")
    commit_msg = _generate_commit_message(changes)
    if _git_commit(commit_msg):
        first_line = commit_msg.split("\n")[0]
        print(f"  ✓ 提交完成: {first_line}")
    else:
        print("  ⚠️  Git commit 失败，但文件已写入。请手动检查。")

    # ── Step 6: Cleanup ──
    shutil.rmtree(temp_dir, ignore_errors=True)

    print("\n✅ 同步完成。")
    print("   提示: 运行 `cd ~/WorkBuddy/hermes-nexus && git push` 推送到 GitHub。")
    print("   提示: 如需审查，运行 `git diff HEAD~1`")
    return True


# ═══════════════════════════════════════════════════════════════
# CLI Interface
# ═══════════════════════════════════════════════════════════════

def print_header(phase: str, title: str) -> None:
    """Print a phase header."""
    print(f"\n{'─' * 60}")
    print(f"  {phase} · {title}")
    print(f"{'─' * 60}")


def print_banner() -> None:
    """Print ASCII art banner."""
    print("""
  🌌  Hermes-Nexus: 本地同步引擎
  ══════════════════════════════════════════════════════════════
  """)
    print(f"  Source:  {SKILLS_ROOT}")
    print(f"  Target:  {MEMORIA_ENGINE}")
    print(f"  Scripts: {len(SYNC_MAP)} mapped | "
          f"{len(EXCLUDED_SCRIPTS)} excluded")


def main():
    parser = argparse.ArgumentParser(
        description="Hermes-Nexus 本地同步引擎 — 四阶段 HITL 安全同步流水线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --diff              Preview pending changes (safe, read-only)
  %(prog)s --diff --full       Preview with detailed code diffs
  %(prog)s --apply             Execute sync after review
  %(prog)s --security-only     Only run security scan, no sync
  %(prog)s --export            Export sync status report to JSON
        """
    )
    parser.add_argument(
        "--diff", action="store_true", default=False,
        help="Preview pending changes (default if no flag specified)"
    )
    parser.add_argument(
        "--full", action="store_true", default=False,
        help="Show detailed code diffs (requires --diff)"
    )
    parser.add_argument(
        "--apply", action="store_true", default=False,
        help="Execute sync (requires secondary confirmation)"
    )
    parser.add_argument(
        "--security-only", action="store_true", default=False,
        help="Only run security scan on source files"
    )
    parser.add_argument(
        "--export", action="store_true", default=False,
        help="Export full status report to JSON (sync_report_YYYYMMDD.json)"
    )

    args = parser.parse_args()

    # Default to --diff if no flag specified
    if not any([args.diff, args.apply, args.security_only, args.export]):
        args.diff = True

    print_banner()

    # ═════════════════════════════════════════════════════════
    # Phase 1: Collect source state
    # ═════════════════════════════════════════════════════════
    print_header("Phase 1/4", "收集源状态")
    source_files = collect_source_files()
    target_files = collect_target_files()

    src_count = sum(1 for v in source_files.values() if v is not None)
    tgt_count = sum(1 for v in target_files.values() if v is not None)
    miss_count = sum(1 for v in source_files.values() if v is None)
    print(f"  源文件: {src_count} (来自 ~/.workbuddy/skills/)")
    print(f"  目标文件: {tgt_count} (来自 hermes-nexus/memoria_engine/)")
    if miss_count:
        print(f"  缺失: {miss_count} 个源文件不存在")

    # Check excluded scripts
    for excl in EXCLUDED_SCRIPTS:
        excl_path = SKILLS_ROOT / excl
        if excl_path.exists():
            print(f"  ⊘ 已排除: {excl}")

    # ═════════════════════════════════════════════════════════
    # Phase 2: Diff analysis
    # ═════════════════════════════════════════════════════════
    print_header("Phase 2/4", "差异分析")
    changes = compute_diff(source_files, target_files)

    total_changes = (len(changes["new"]) + len(changes["modified"])
                     + len(changes["deleted"]))
    if total_changes == 0 and len(changes["missing"]) == 0:
        print("\n✅ 无可同步变更。本地与开源仓库已完全同步。")
        return

    print(f"  🔍 发现 {total_changes} 处变更 "
          f"(NEW: {len(changes['new'])}, "
          f"MODIFIED: {len(changes['modified'])}, "
          f"DELETED: {len(changes['deleted'])})")

    # ═════════════════════════════════════════════════════════
    # Phase 3: Security scan
    # ═════════════════════════════════════════════════════════
    print_header("Phase 3/4", "安全扫描")
    security_results = security_scan(source_files)

    blocked_count = len(security_results["blocked"])
    warnings_count = len(security_results["warnings"])
    clean_count = len(security_results["clean"])

    if blocked_count:
        print("\n⛔ 安全扫描发现 BLOCKER 级别问题，同步被拒绝：")
        for file_path, line, sev, match in security_results["blocked"]:
            print(f"  ❌ {Path(file_path).name}:{line} — [{sev}] {match}")
        print("\n  请先处理以上问题，然后重新运行 sync.py --diff。")
        print(f"\n  状态: {blocked_count} BLOCKER, "
              f"{warnings_count} WARNING, "
              f"{clean_count} CLEAN")
        return

    print(f"  ✅ 安全扫描通过 "
          f"({blocked_count} BLOCKER, "
          f"{warnings_count} WARNING, "
          f"{clean_count} CLEAN)")

    if warnings_count:
        print(f"  ⚠️  检测到 {warnings_count} 处需要自动替换的内容（见 Phase 4）")

    # ═════════════════════════════════════════════════════════
    # Phase 4: HITL Review
    # ═════════════════════════════════════════════════════════
    print_header("Phase 4/4", "变更预览 & 审批")

    display_change_summary(changes)
    display_warnings_summary(security_results["warnings"])

    # Summary line
    print(f"\n{'─' * 60}")
    print(f"提案摘要：")
    print(f"  新增文件: {len(changes['new'])}")
    print(f"  修改文件: {len(changes['modified'])}")
    print(f"  删除文件: {len(changes['deleted'])}")
    print(f"  安全替换: {len(security_results['warnings'])} 处")
    print(f"{'─' * 60}")

    # ── Export mode ──
    if args.export:
        export_data = {
            "timestamp": datetime.now().isoformat(),
            "source_root": str(SKILLS_ROOT),
            "target_root": str(PROJECT_ROOT),
            "changes": {
                "new": [(a, b) for a, b, _ in changes["new"]],
                "modified": [(a, b) for a, b, _ in changes["modified"]],
                "deleted": [(a, b) for a, b in changes["deleted"]],
                "missing": [(a, b) for a, b in changes["missing"]],
            },
            "security": {
                "blockers": [(Path(f).name, l, s, m) for f, l, s, m in security_results["blocked"]],
                "warnings": [(Path(f).name, l, s, m, r) for f, l, s, m, r in security_results["warnings"]],
                "clean_count": len(security_results["clean"]),
            },
        }
        report_name = f"sync_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        report_path = PROJECT_ROOT / "sync" / report_name
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(export_data, indent=2, ensure_ascii=False))
        print(f"\n📄 状态报告已导出: sync/{report_name}")
        return

    # ── Security-only mode ──
    if args.security_only:
        print("\n--security-only 模式：不执行同步，仅展示安全扫描结果。")
        return

    # ── Apply mode ──
    if args.apply:
        print("\n⚠️  --apply 已指定，但同步是不可逆操作。")
        answer = input("确认执行同步？输入 'yes' 继续: ").strip()
        if answer.lower() != "yes":
            print("已取消。")
            return
        execute_sync(changes, source_files, security_results)
        return

    # ── Default: interactive confirmation ──
    answer = input("\n是否脱敏并合并到开源仓库？(y/n) [n]: ").strip().lower()
    if answer in ("y", "yes"):
        execute_sync(changes, source_files, security_results)
    else:
        print("\n已取消。下次运行 sync.py --apply 可直接执行。")
        print("提示: 如需查看详细 diff，运行 sync.py --diff --full")


if __name__ == "__main__":
    main()
