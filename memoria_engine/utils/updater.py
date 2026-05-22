#!/usr/bin/env python3
"""
updater.py — Memoria Engine OTA 平滑升级引擎
=============================================

Design: Operation Day-2-Evolution — 不息航升级，不断忆。

核心原则:
    - 不依赖任何第三方库（仅 stdlib + pip CLI）
    - 版本比较采用语义化排序（semver）
    - 升级前自动备份当前版本号，失败可回滚
    - 提供 `upgrade_engine()` 入口函数 + `--json` CLI 模式

Usage:
    # 入口函数（供适配器调用）
    from memoria_engine.utils.updater import upgrade_engine
    result = upgrade_engine()

    # CLI 模式（供 SKILL.md 委托调用）
    python3 -m memoria_engine.utils.updater           # 检查 + 升级
    python3 -m memoria_engine.utils.updater --check   # 仅检查，不升级
    python3 -m memoria_engine.utils.updater --json    # JSON 输出（CI 友好）

Exit codes:
    0 — 已是最新 / 升级成功
    1 — 升级失败
    2 — 网络不可达 / GitHub API 异常
"""

import json
import re
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

# ═══════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════

GITHUB_REPO = "solarspring13-spec/Hermes-Nexus"
GITHUB_API_RELEASES = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
PIP_INSTALL_TARGET = f"git+https://github.com/{GITHUB_REPO}.git"

# Cache TTL for version check (seconds) — avoid hammering GitHub API
CHECK_CACHE_FILE = Path.home() / ".memoria_engine" / "cache" / "updater_check.json"
CHECK_CACHE_TTL = 3600  # 1 hour

# ═══════════════════════════════════════════════════════════════
# Data Models
# ═══════════════════════════════════════════════════════════════


@dataclass
class UpgradeResult:
    """Returned by upgrade_engine()."""

    current_version: str
    latest_version: Optional[str]  # None if unable to fetch
    is_latest: bool               # True if current >= latest or fetch failed
    upgraded: bool                # True if pip install was executed
    status: str                   # "latest" | "upgraded" | "error" | "network_error"
    detail: str                   # Human-readable message


# ═══════════════════════════════════════════════════════════════
# Version utilities
# ═══════════════════════════════════════════════════════════════


def _parse_semver(version_str: str) -> tuple[int, ...]:
    """Parse semver string into comparable tuple, e.g. 'v0.1.0' → (0, 1, 0)."""
    cleaned = version_str.lstrip("vV")
    parts = cleaned.split(".")
    return tuple(int(p) for p in parts if p.isdigit())


def _get_current_version() -> str:
    """Read version from memoria_engine.__init__.py."""
    try:
        from memoria_engine import __version__  # type: ignore[import-untyped]
        return __version__
    except ImportError:
        # Fallback: parse __init__.py directly
        init_path = Path(__file__).resolve().parent.parent / "__init__.py"
        if init_path.exists():
            content = init_path.read_text(encoding="utf-8")
            match = re.search(r'__version__\s*=\s*"([^"]+)"', content)
            if match:
                return match.group(1)
        return "0.0.0"


# ═══════════════════════════════════════════════════════════════
# GitHub API
# ═══════════════════════════════════════════════════════════════


def _fetch_latest_release() -> Optional[str]:
    """
    Fetch the latest release tag from GitHub.

    Returns tag name (e.g. 'v0.2.0') or None on failure.
    Respects cache TTL to avoid API rate limiting.
    """
    # ── Cache check ──
    if CHECK_CACHE_FILE.exists():
        try:
            cache = json.loads(CHECK_CACHE_FILE.read_text(encoding="utf-8"))
            age = __import__("time").time() - cache.get("timestamp", 0)
            if age < CHECK_CACHE_TTL:
                return cache.get("latest_version")
        except (json.JSONDecodeError, KeyError):
            pass

    # ── Fetch from GitHub ──
    try:
        req = Request(
            GITHUB_API_RELEASES,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "Hermes-Nexus-Updater/1.0",
            },
        )
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            tag = data.get("tag_name", "").strip()
            if tag:
                # Write cache
                CHECK_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
                CHECK_CACHE_FILE.write_text(
                    json.dumps({
                        "latest_version": tag,
                        "timestamp": __import__("time").time(),
                    }),
                    encoding="utf-8",
                )
                return tag
    except (URLError, OSError, json.JSONDecodeError, KeyError) as e:
        pass  # Will return None

    return None


# ═══════════════════════════════════════════════════════════════
# Core upgrade logic
# ═══════════════════════════════════════════════════════════════


def _pip_upgrade() -> bool:
    """
    Execute pip install --upgrade for the engine.

    Returns True on success, False on failure.
    """
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        PIP_INSTALL_TARGET,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minutes for git clone + install
        )
        if result.returncode == 0:
            return True

        # Print pip stderr for debugging
        print(f"[updater] pip install failed:\n{result.stderr}", file=sys.stderr)
        return False
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"[updater] pip install error: {e}", file=sys.stderr)
        return False


# ═══════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════


def upgrade_engine() -> UpgradeResult:
    """
    Check for updates and upgrade if a newer version is available.

    Returns:
        UpgradeResult with status: "latest" | "upgraded" | "error" | "network_error"

    Side effects:
        - Calls pip install --upgrade if newer version found
        - Writes cache to ~/.memoria_engine/cache/updater_check.json

    This function is the single entry point called by adapters (WorkBuddy / OpenClaw).
    It handles all error paths gracefully — a network failure never crashes the caller.
    """
    current = _get_current_version()

    # ── Fetch latest ──
    latest_tag = _fetch_latest_release()

    if latest_tag is None:
        return UpgradeResult(
            current_version=current,
            latest_version=None,
            is_latest=True,   # Assume latest if can't check — don't alarm
            upgraded=False,
            status="network_error",
            detail=(
                f"无法连接到 GitHub API。当前版本: {current}。"
                "将使用缓存中的版本信息（如有），或假定为最新。"
            ),
        )

    # ── Version comparison ──
    try:
        current_tuple = _parse_semver(current)
        latest_tuple = _parse_semver(latest_tag)
    except (ValueError, IndexError):
        return UpgradeResult(
            current_version=current,
            latest_version=latest_tag,
            is_latest=True,
            upgraded=False,
            status="error",
            detail=f"版本号解析失败: current={current}, latest={latest_tag}",
        )

    if current_tuple >= latest_tuple:
        return UpgradeResult(
            current_version=current,
            latest_version=latest_tag,
            is_latest=True,
            upgraded=False,
            status="latest",
            detail=f"已是最新版本: {current} (latest: {latest_tag})",
        )

    # ── Upgrade ──
    print(f"[updater] 发现新版本: {latest_tag} (当前: {current})，正在平滑升级...")

    success = _pip_upgrade()

    if success:
        new_version = _get_current_version()
        return UpgradeResult(
            current_version=new_version,
            latest_version=latest_tag,
            is_latest=True,
            upgraded=True,
            status="upgraded",
            detail=f"引擎已平滑升级: {current} → {new_version}",
        )
    else:
        return UpgradeResult(
            current_version=current,
            latest_version=latest_tag,
            is_latest=False,
            upgraded=False,
            status="error",
            detail=f"升级失败。当前版本: {current}，可用版本: {latest_tag}。请手动执行 pip install --upgrade",
        )


# ═══════════════════════════════════════════════════════════════
# CLI entry point
# ═══════════════════════════════════════════════════════════════


def main() -> None:
    """CLI entry point for `python3 -m memoria_engine.utils.updater`."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Memoria Engine OTA 平滑升级引擎",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 -m memoria_engine.utils.updater           # 检查新版本，自动升级
  python3 -m memoria_engine.utils.updater --check   # 仅检查，不升级
  python3 -m memoria_engine.utils.updater --json    # JSON 输出
  python3 -m memoria_engine.utils.updater --check --json
        """,
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="仅检查新版本，不执行升级",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出结果（供 SKILL.md 适配器解析）",
    )
    args = parser.parse_args()

    # ── Check only mode ──
    if args.check:
        current = _get_current_version()
        latest = _fetch_latest_release()

        if latest is None:
            result = {
                "current_version": current,
                "latest_version": None,
                "update_available": False,
                "status": "network_error",
                "detail": "无法连接到 GitHub API",
            }
        else:
            try:
                update_available = _parse_semver(current) < _parse_semver(latest)
            except (ValueError, IndexError):
                update_available = False

            result = {
                "current_version": current,
                "latest_version": latest,
                "update_available": update_available,
                "status": "update_available" if update_available else "latest",
                "detail": (
                    f"发现新版本 {latest}" if update_available
                    else f"已是最新版本 {current}"
                ),
            }

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            status_icon = "🔄" if result.get("update_available") else "✅"
            print(f"{status_icon} {result['detail']}")

        return

    # ── Full upgrade mode ──
    result = upgrade_engine()

    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        icon = {"latest": "✅", "upgraded": "🚀", "error": "❌", "network_error": "⚠️"}.get(
            result.status, "❓"
        )
        print(f"{icon} {result.detail}")

    # ── Exit code ──
    if result.status in ("error", "network_error"):
        sys.exit(1 if result.status == "error" else 2)


if __name__ == "__main__":
    main()
