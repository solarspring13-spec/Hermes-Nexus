#!/usr/bin/env python3
"""
QA-Sentinel · 自动化情报侦察兵 (Doc Fetcher)
═══════════════════════════════════════════════════════

Purpose:
    Periodically fetch the latest developer documentation and API specs
    from target platforms (OpenClaw, WorkBuddy, MaxClaw, etc.) to detect
    breaking changes before they break our adapter layer.

Design Philosophy (from Hermes-Exodus QA Architecture):
    ┌──────────────────────────────────────────────────────┐
    │  "The Sentinel doesn't guess — it fetches evidence." │
    │                                                      │
    │  Each target platform is a "Watchtower".             │
    │  Each Watchtower has:                                │
    │    • A source URL (GitHub API / docs site)            │
    │    • A schema extractor (what to look for)            │
    │    • A diff engine (what changed vs. last snapshot)   │
    │    • An alert dispatcher (how to report)              │
    └──────────────────────────────────────────────────────┘

Target Platforms (Phase 0):
    - OpenClaw: GitHub repo API → SKILL.md schema, config format, plugin API
    - WorkBuddy: documentation site → models.json schema, SKILL.md fields
    - MaxClaw / AutoClaw / QClaw: reserved for future expansion

Fetch Strategy:
    Phase 0: GitHub Contents API (free, no auth for public repos)
    Phase 1: WebFetch for rendered docs pages (semantic extraction)
    Phase 2: RSS/Atom feed monitoring for changelogs

Usage:
    python3 -m tests.qa_sentinel.doc_fetcher --platform openclaw
    python3 -m tests.qa_sentinel.doc_fetcher --platform all --output-dir snapshots/

Author: Hermes-Nexus QA Corps
License: BSL 1.1
"""

import argparse
import json
import os
import sys
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


# ═══════════════════════════════════════════════════════════
# Data Structures
# ═══════════════════════════════════════════════════════════

@dataclass
class Watchtower:
    """
    A single target platform to monitor.

    Schema:
        name            — Human-readable platform name
        repo            — GitHub owner/repo (for API-based fetch)
        docs_spec_path  — Path within repo where SKILL.md / schema lives
        docs_url        — Fallback: rendered docs URL
        plugin_api_path — Optional: plugin/extension API spec path
        fetch_method    — "github_api" | "webfetch" | "rss"
    """
    name: str
    repo: str
    docs_spec_path: str
    docs_url: str = ""
    plugin_api_path: str = ""
    fetch_method: str = "github_api"

    @property
    def api_url(self) -> str:
        """GitHub Contents API URL for the primary spec file."""
        return f"https://api.github.com/repos/{self.repo}/contents/{self.docs_spec_path}"


@dataclass
class Snapshot:
    """A captured state of a target platform's spec at a point in time."""
    platform: str
    fetched_at: str          # ISO 8601
    source_url: str
    content_hash: str        # SHA-256 of raw content
    raw_spec: str            # Raw spec content
    extracted_fields: Dict[str, list] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


@dataclass
class DiffReport:
    """Difference between two snapshots."""
    platform: str
    old_snapshot_at: str
    new_snapshot_at: str
    added_fields: List[str] = field(default_factory=list)
    removed_fields: List[str] = field(default_factory=list)
    modified_fields: List[str] = field(default_factory=list)
    breaking_changes: List[str] = field(default_factory=list)
    is_breaking: bool = False


# ═══════════════════════════════════════════════════════════
# Platform Registry
# ═══════════════════════════════════════════════════════════

# Phase 0: Known target platforms
# Each entry maps to a GitHub repo where the SKILL.md or equivalent lives.
# Extend this registry as new platforms emerge.

PLATFORM_REGISTRY: Dict[str, Watchtower] = {
    "openclaw": Watchtower(
        name="OpenClaw",
        repo="openclaw/openclaw",
        docs_spec_path="docs/PLUGIN_API.md",
        docs_url="https://docs.openclaw.dev/plugin-api",
        plugin_api_path="src/plugin/types.ts",
        fetch_method="github_api",
    ),
    "workbuddy": Watchtower(
        name="WorkBuddy",
        repo="workbuddy/workbuddy",
        docs_spec_path="docs/SKILL.md",
        docs_url="https://docs.workbuddy.dev/skills",
        plugin_api_path="docs/MCP.md",
        fetch_method="github_api",
    ),
    # Reserved for future expansion:
    # "maxclaw": Watchtower(...),
    # "autoclaw": Watchtower(...),
    # "qclaw": Watchtower(...),
}


# ═══════════════════════════════════════════════════════════
# Core Fetch Logic
# ═══════════════════════════════════════════════════════════

def fetch_latest_openclaw_specs() -> Optional[Snapshot]:
    """
    Fetch the latest OpenClaw plugin API specs from GitHub.

    Strategy:
        1. Hit GitHub Contents API for the primary spec file (PLUGIN_API.md)
        2. Decode base64 content
        3. Extract structured fields (plugin interface, hook signatures, config schema)
        4. Compute content hash for diff comparison
        5. Return a Snapshot dataclass

    Returns:
        Snapshot if successful, None if fetch fails (network, 404, rate limit).

    Note:
        GitHub API has a 60 req/hr rate limit for unauthenticated requests.
        For production use, add a GITHUB_TOKEN env var to raise the limit to 5000/hr.
    """
    tower = PLATFORM_REGISTRY.get("openclaw")
    if not tower:
        return None

    url = tower.api_url
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "Hermes-Nexus-QA-Sentinel/0.1"}

    # Use GitHub token if available (raises rate limit from 60 → 5000/hr)
    github_token = os.environ.get("GITHUB_TOKEN", "")
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        # GitHub API returns content as base64-encoded
        import base64
        raw_content = base64.b64decode(data["content"]).decode("utf-8")

        content_hash = hashlib.sha256(raw_content.encode()).hexdigest()

        # ── Field Extraction (platform-specific) ──
        # OpenClaw's PLUGIN_API.md is expected to contain:
        #   - Plugin interface definitions (TypeScript interfaces)
        #   - Hook signatures
        #   - Config schema (JSON Schema or TypeScript type)
        extracted = _extract_openclaw_fields(raw_content)

        snapshot = Snapshot(
            platform="openclaw",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            source_url=url,
            content_hash=content_hash,
            raw_spec=raw_content,
            extracted_fields=extracted,
            warnings=[],
        )

        # ── Also try to fetch secondary specs ──
        if tower.plugin_api_path:
            try:
                type_url = f"https://api.github.com/repos/{tower.repo}/contents/{tower.plugin_api_path}"
                type_req = Request(type_url, headers=headers)
                with urlopen(type_req, timeout=15) as type_resp:
                    type_data = json.loads(type_resp.read().decode("utf-8"))
                    type_content = base64.b64decode(type_data["content"]).decode("utf-8")
                    snapshot.extracted_fields["plugin_types"] = type_content.split("\n")
                    snapshot.warnings.append(f"Secondary spec fetched: {tower.plugin_api_path}")
            except (HTTPError, URLError, KeyError) as e:
                snapshot.warnings.append(f"Secondary spec fetch failed ({tower.plugin_api_path}): {e}")

        return snapshot

    except HTTPError as e:
        print(f"⚠️  OpenClaw fetch HTTP {e.code}: {e.reason}", file=sys.stderr)
        return None
    except URLError as e:
        print(f"⚠️  OpenClaw fetch network error: {e.reason}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"⚠️  OpenClaw fetch unexpected error: {e}", file=sys.stderr)
        return None


def fetch_latest_workbuddy_specs() -> Optional[Snapshot]:
    """
    Fetch the latest WorkBuddy SKILL.md schema from GitHub.

    Mirrors fetch_latest_openclaw_specs() structure for consistency.
    Target: SKILL.md schema, models.json format, MCP config spec.
    """
    tower = PLATFORM_REGISTRY.get("workbuddy")
    if not tower:
        return None

    url = tower.api_url
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "Hermes-Nexus-QA-Sentinel/0.1"}

    github_token = os.environ.get("GITHUB_TOKEN", "")
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        import base64
        raw_content = base64.b64decode(data["content"]).decode("utf-8")
        content_hash = hashlib.sha256(raw_content.encode()).hexdigest()

        extracted = _extract_workbuddy_fields(raw_content)

        return Snapshot(
            platform="workbuddy",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            source_url=url,
            content_hash=content_hash,
            raw_spec=raw_content,
            extracted_fields=extracted,
        )

    except HTTPError as e:
        print(f"⚠️  WorkBuddy fetch HTTP {e.code}: {e.reason}", file=sys.stderr)
        return None
    except URLError as e:
        print(f"⚠️  WorkBuddy fetch network error: {e.reason}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"⚠️  WorkBuddy fetch unexpected error: {e}", file=sys.stderr)
        return None


def fetch_all() -> Dict[str, Optional[Snapshot]]:
    """
    Fetch specs for all registered platforms in PLATFORM_REGISTRY.

    Returns:
        Dict mapping platform name → Snapshot or None (per platform).
        A None value means fetch failed for that platform — non-fatal.
    """
    results = {}
    # Dispatch per-platform fetch functions
    # Phase 0: manual dispatch; Phase 1: use PLATFORM_REGISTRY's fetch_method for generic dispatch
    fetchers = {
        "openclaw": fetch_latest_openclaw_specs,
        "workbuddy": fetch_latest_workbuddy_specs,
    }
    for name, fetcher in fetchers.items():
        try:
            results[name] = fetcher()
        except Exception as e:
            print(f"⚠️  {name} fetch crashed: {e}", file=sys.stderr)
            results[name] = None
    return results


# ═══════════════════════════════════════════════════════════
# Field Extraction (Platform-Specific Parsers)
# ═══════════════════════════════════════════════════════════

def _extract_openclaw_fields(raw: str) -> Dict[str, list]:
    """
    Extract structured fields from OpenClaw PLUGIN_API.md.

    Looks for:
        - ### Interface headers (plugin interfaces)
        - Function signatures (def / async def for Python, function for TS)
        - YAML frontmatter blocks (config schemas)
        - JSON Schema blocks

    Returns:
        Dict with keys: "interfaces", "functions", "config_keys", "hooks"
    """
    fields = {"interfaces": [], "functions": [], "config_keys": [], "hooks": []}

    lines = raw.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()

        # Detect interface/class definitions (TypeScript style)
        if stripped.startswith("interface ") or stripped.startswith("export interface "):
            fields["interfaces"].append(stripped)
        elif stripped.startswith("class ") or stripped.startswith("export class "):
            fields["interfaces"].append(stripped)

        # Detect function signatures
        if "function " in stripped and ("(" in stripped) and not stripped.startswith("//"):
            fields["functions"].append(stripped[:120])

        # Detect hook signatures (convention: on* or before* or after* prefix)
        if ("async def on_" in stripped or "def on_" in stripped or
            "async def before_" in stripped or "def before_" in stripped):
            fields["hooks"].append(stripped[:120])

        # Detect config keys (YAML/JSON-like)
        if ":" in stripped and not stripped.startswith("#") and not stripped.startswith("http"):
            key = stripped.split(":")[0].strip().strip('"').strip("'")
            if key and not key.startswith("http") and len(key) < 50:
                fields["config_keys"].append(key)

    return fields


def _extract_workbuddy_fields(raw: str) -> Dict[str, list]:
    """
    Extract structured fields from WorkBuddy SKILL.md schema.

    Looks for:
        - YAML frontmatter fields (name, description, allowed-tools, etc.)
        - ### Section headers (skill structure)
        - Code blocks (```yaml, ```json)
    """
    fields = {"frontmatter_keys": [], "sections": [], "code_blocks": []}

    lines = raw.split("\n")
    in_frontmatter = False
    in_code_block = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Track YAML frontmatter
        if stripped == "---":
            if not in_frontmatter:
                in_frontmatter = True
                continue
            else:
                in_frontmatter = False
                continue

        if in_frontmatter and ":" in stripped:
            key = stripped.split(":")[0].strip()
            fields["frontmatter_keys"].append(key)

        # Track sections
        if stripped.startswith("### "):
            fields["sections"].append(stripped[4:])

        # Track code blocks
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            fields["code_blocks"].append(stripped[:120])

    return fields


# ═══════════════════════════════════════════════════════════
# Diff Engine
# ═══════════════════════════════════════════════════════════

def diff_snapshots(old: Snapshot, new: Snapshot) -> DiffReport:
    """
    Compare two snapshots of the same platform and produce a DiffReport.

    Detects:
        - Added/removed interfaces, functions, hooks, config keys
        - Breaking changes (removed interfaces or functions = P0 alert)

    Note:
        This is a structural (field-level) diff, not a textual (line-level) diff.
        It compares extracted_fields dictionaries, not raw spec text.
    """
    report = DiffReport(
        platform=old.platform,
        old_snapshot_at=old.fetched_at,
        new_snapshot_at=new.fetched_at,
    )

    # Compare each extracted field category
    all_categories = set(old.extracted_fields.keys()) | set(new.extracted_fields.keys())

    for category in sorted(all_categories):
        old_set = set(old.extracted_fields.get(category, []))
        new_set = set(new.extracted_fields.get(category, []))

        added = sorted(new_set - old_set)
        removed = sorted(old_set - new_set)

        if added:
            for item in added:
                report.added_fields.append(f"[{category}] {item}")
        if removed:
            for item in removed:
                report.removed_fields.append(f"[{category}] {item}")

            # Breaking change detection:
            # If interfaces or functions are removed, that's a P0 breaking change
            if category in ("interfaces", "functions", "hooks"):
                report.breaking_changes.extend(
                    f"REMOVED {category}: {item}" for item in removed
                )
                report.is_breaking = True

    return report


# ═══════════════════════════════════════════════════════════
# Snapshot Persistence
# ═══════════════════════════════════════════════════════════

def save_snapshot(snapshot: Snapshot, output_dir: Path) -> Path:
    """Save a snapshot to disk as JSON. Returns the file path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{snapshot.platform}_{snapshot.fetched_at[:10]}.json"
    filepath = output_dir / filename

    with open(filepath, "w") as f:
        json.dump(asdict(snapshot), f, indent=2, ensure_ascii=False)

    return filepath


def load_latest_snapshot(platform: str, snapshots_dir: Path) -> Optional[Snapshot]:
    """Load the most recent snapshot for a platform from disk."""
    if not snapshots_dir.exists():
        return None

    # Find all snapshots for this platform, sorted by filename (date)
    candidates = sorted(snapshots_dir.glob(f"{platform}_*.json"), reverse=True)
    if not candidates:
        return None

    with open(candidates[0]) as f:
        data = json.load(f)
        return Snapshot(**data)


# ═══════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="QA-Sentinel Doc Fetcher — fetch latest platform specs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --platform openclaw         Fetch OpenClaw specs
  %(prog)s --platform all              Fetch all platforms
  %(prog)s --platform all --diff       Fetch all + diff vs last snapshot
  %(prog)s --platform openclaw --output-dir snapshots/
        """,
    )
    parser.add_argument("--platform", default="all", choices=["all", "openclaw", "workbuddy"])
    parser.add_argument("--output-dir", default="tests/qa_sentinel/snapshots")
    parser.add_argument("--diff", action="store_true", help="Diff against last snapshot")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    snapshots_dir = Path(args.output_dir)

    if args.platform == "all":
        results = fetch_all()
    elif args.platform == "openclaw":
        results = {"openclaw": fetch_latest_openclaw_specs()}
    elif args.platform == "workbuddy":
        results = {"workbuddy": fetch_latest_workbuddy_specs()}
    else:
        print(f"Unknown platform: {args.platform}")
        sys.exit(1)

    for name, snapshot in results.items():
        if snapshot is None:
            print(f"❌ {name}: fetch failed")
            continue

        print(f"✅ {name}: fetched ({len(snapshot.raw_spec)} chars, hash={snapshot.content_hash[:12]})")
        print(f"   interfaces: {len(snapshot.extracted_fields.get('interfaces', []))}")
        print(f"   functions:  {len(snapshot.extracted_fields.get('functions', []))}")
        print(f"   hooks:      {len(snapshot.extracted_fields.get('hooks', []))}")

        if args.diff:
            prev = load_latest_snapshot(name, snapshots_dir)
            if prev:
                report = diff_snapshots(prev, snapshot)
                if report.is_breaking:
                    print(f"   ⚠️  BREAKING CHANGES DETECTED:")
                    for bc in report.breaking_changes:
                        print(f"      - {bc}")
                else:
                    print(f"   ✅ No breaking changes")
                if report.added_fields:
                    print(f"   ➕ {len(report.added_fields)} new fields")
                if report.removed_fields:
                    print(f"   ➖ {len(report.removed_fields)} removed fields")
            else:
                print(f"   ℹ️  No previous snapshot to diff against")

        filepath = save_snapshot(snapshot, snapshots_dir)
        print(f"   💾 saved: {filepath}")

        if args.json:
            print(json.dumps(asdict(snapshot), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
