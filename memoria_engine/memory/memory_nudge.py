#!/usr/bin/env python3
"""
Periodic Nudge Trigger for WorkBuddy
=====================================
Inspired by Hermes Agent's Periodic Nudge mechanism.

This script is meant to be called by the Agent at regular intervals
(every N tool calls) to trigger a memory self-review.

It checks the conversation context, determines if a nudge is due,
and produces a structured prompt for the Agent to self-reflect.

Usage (called by Agent):
    python3 memory_nudge.py --workspace /path/to/workspace --tool-count 10

    # Force a nudge regardless of count
    python3 memory_nudge.py --workspace /path/to/workspace --force

    # Check if nudge is due (exit code indicates: 0=due, 1=not due)
    python3 memory_nudge.py --workspace /path/to/workspace --check --tool-count 8
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from ..constants import (
    DEFAULT_NUDGE_INTERVAL, MIN_NUDGE_INTERVAL,
    NUDGE_STATE_FILE, GLOBAL_STATE_PATH,
    SCRIPTS_DIR,
)


# ── State Management ─────────────────────────────────────────

def get_state_path(workspace: str, use_global: bool = False) -> Path:
    """Get path to the nudge state file.

    Args:
        workspace: WorkBuddy workspace path (used for per-workspace state)
        use_global: If True, use global state at {MEMORIA_HOME}
    """
    if use_global:
        return GLOBAL_STATE_PATH
    return Path(workspace) / NUDGE_STATE_FILE


def _migrate_to_global(workspace: str) -> dict:
    """Migrate from per-workspace state to global state.

    On first migration, sync the maximum counter values from the per-workspace
    state into the global state to avoid losing history. The old per-workspace
    state file is preserved but no longer updated.
    """
    ws_path = Path(workspace) / NUDGE_STATE_FILE
    global_path = GLOBAL_STATE_PATH

    # Load existing global state (or default)
    global_state = {
        "total_tool_calls": 0,
        "tools_since_last_nudge": 0,
        "last_nudge_at": None,
        "nudge_count": 0,
        "nudge_interval": DEFAULT_NUDGE_INTERVAL,
        "session_start": datetime.now().isoformat(),
    }
    if global_path.exists():
        try:
            global_state = json.loads(global_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    # Load per-workspace state for migration (one-time)
    if ws_path.exists():
        try:
            ws_state = json.loads(ws_path.read_text())
            # Take the maximum of each counter to preserve history
            global_state["total_tool_calls"] = max(
                global_state.get("total_tool_calls", 0),
                ws_state.get("total_tool_calls", 0)
            )
            global_state["tools_since_last_nudge"] = max(
                global_state.get("tools_since_last_nudge", 0),
                ws_state.get("tools_since_last_nudge", 0)
            )
            global_state["nudge_count"] = max(
                global_state.get("nudge_count", 0),
                ws_state.get("nudge_count", 0)
            )
            # Use the most recent last_nudge_at
            ws_last = ws_state.get("last_nudge_at")
            gl_last = global_state.get("last_nudge_at")
            if ws_last and (not gl_last or ws_last > gl_last):
                global_state["last_nudge_at"] = ws_last
            # Use the most recent session_start
            ws_start = ws_state.get("session_start")
            gl_start = global_state.get("session_start")
            if ws_start and (not gl_start or ws_start > gl_start):
                global_state["session_start"] = ws_start
            # Preserve the smaller interval
            global_state["nudge_interval"] = min(
                global_state.get("nudge_interval", DEFAULT_NUDGE_INTERVAL),
                ws_state.get("nudge_interval", DEFAULT_NUDGE_INTERVAL)
            )
            # Rename old file to preserve it but prevent future reads
            backup_path = ws_path.with_suffix(".json.migrated")
            ws_path.rename(backup_path)
        except (json.JSONDecodeError, OSError):
            pass

    # Save the merged global state
    global_path.parent.mkdir(parents=True, exist_ok=True)
    global_path.write_text(json.dumps(global_state, ensure_ascii=False, indent=2))
    return global_state


def load_state(workspace: str, use_global: bool = False) -> dict:
    """Load nudge state from file.

    Args:
        workspace: WorkBuddy workspace path
        use_global: If True, load from global state; auto-migrate on first use
    """
    if use_global:
        # Check if migration is needed (per-workspace state exists but global doesn't have full history)
        ws_path = Path(workspace) / NUDGE_STATE_FILE
        if ws_path.exists() and not ws_path.suffix.endswith('.migrated'):
            return _migrate_to_global(workspace)
        path = GLOBAL_STATE_PATH
    else:
        path = get_state_path(workspace, use_global=False)

    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    return {
        "total_tool_calls": 0,
        "tools_since_last_nudge": 0,
        "last_nudge_at": None,
        "nudge_count": 0,
        "nudge_interval": DEFAULT_NUDGE_INTERVAL,
        "session_start": datetime.now().isoformat(),
    }


def save_state(workspace: str, state: dict, use_global: bool = False):
    """Save nudge state to file."""
    path = get_state_path(workspace, use_global=use_global)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def is_nudge_due(state: dict) -> bool:
    """Check if a nudge is due based on tool count and time."""
    tools_since = state.get("tools_since_last_nudge", 0)
    interval = state.get("nudge_interval", DEFAULT_NUDGE_INTERVAL)

    # Count-based trigger
    if tools_since >= interval:
        return True

    # Time-based fallback: if session started > 1 hour ago and no nudge yet
    last_nudge = state.get("last_nudge_at")
    session_start = state.get("session_start")

    if last_nudge is None and session_start:
        try:
            start_time = datetime.fromisoformat(session_start)
            if datetime.now() - start_time > timedelta(hours=1) and tools_since >= MIN_NUDGE_INTERVAL:
                return True
        except (ValueError, TypeError):
            pass

    return False


# ── Nudge Prompt Generator ────────────────────────────────────


def generate_nudge_prompt() -> str:
    """Generate the structured nudge prompt for the Agent's self-review.

    This is the exact prompt that should be injected into the Agent's
    internal reasoning. It MUST be processed silently (not shown to user).
    """
    return """[SYSTEM: PERIODIC MEMORY NUDGE — INTERNAL REVIEW]

You are performing a silent, internal memory self-review. Do NOT output anything
to the user unless you find content worth persisting.

Review your recent conversation turns and classify what should be remembered:

## MEMORY.md Review (Episodic — facts, conventions, environment)

Ask yourself:
1. Did the user reveal new project conventions, paths, or configurations?
2. Did I learn about a tool's surprising behavior or gotcha?
3. Did the user correct me about something I should remember?
4. Is there a decision or approach we settled on that future-me should know?

## USER.md Review (Semantic — user profile, preferences)

Ask yourself:
1. Did the user express a new preference (format, style, tool choice)?
2. Did their communication style suggest a change in how I should respond?
3. Did they mention new projects, interests, or work context?

## Value Classification

- P0 (MUST keep): User corrections, critical conventions, safety rules
- P1 (Should keep): Environment configs, tool quirks, workflow preferences
- P2 (Discard): Temporary file paths, one-off debugging, common knowledge, things easily searchable

## Output Format

If NOTHING worth persisting was found, respond with exactly:

    NOTHING_TO_PERSIST

If there ARE things to persist, respond with:

    MEMORY_UPDATE:
    - [item 1 — specific, actionable fact]
    - [item 2 — specific, actionable fact]

    USER_UPDATE:
    - [item 1 — specific user preference]
    - [item 2 — specific user preference]

    OBSOLETE:
    - [old item that should be removed/replaced]

After this review, if you called memory or user update actions, append your
response with: "💾 Memory updated" (or nothing if NOTHING_TO_PERSIST)."""


# ── CLI ───────────────────────────────────────────────────────

def auto_increment(workspace: str, state: dict, interval_minutes: int = 5,
                   use_global: bool = False) -> bool:
    """Auto-increment counter based on elapsed time since last increment.

    Returns True if counter was incremented, False otherwise.
    """
    AUTO_INCREMENT_INTERVAL = interval_minutes * 60  # seconds

    last_increment = state.get("last_auto_increment_at")
    now = datetime.now()

    if last_increment is None:
        # First time - initialize without incrementing
        state["last_auto_increment_at"] = now.isoformat()
        save_state(workspace, state, use_global=use_global)
        return False

    try:
        last_time = datetime.fromisoformat(last_increment)
        elapsed = (now - last_time).total_seconds()
        if elapsed >= AUTO_INCREMENT_INTERVAL:
            state["total_tool_calls"] += 1
            state["tools_since_last_nudge"] += 1
            state["last_auto_increment_at"] = now.isoformat()
            save_state(workspace, state, use_global=use_global)
            return True
    except (ValueError, TypeError):
        state["last_auto_increment_at"] = now.isoformat()
        save_state(workspace, state, use_global=use_global)

    return False


def _inject_intent_review(prompt: str) -> str:
    """Analyze intent learner state and inject findings into nudge prompt."""
    scripts_dir = SCRIPTS_DIR
    intent_script = scripts_dir / "intent_learner.py"
    seq_script = scripts_dir / "sequence_analyzer.py"
    
    if not intent_script.exists():
        return prompt
    
    sections = []
    
    # 1. Intent learner analysis (recent 7 days)
    try:
        result = subprocess.run(
            [sys.executable, str(intent_script), "--analyze", "--days", "7", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            sections.append("\n\n## 🎯 意图学习分析 (Phase 5)")
            sections.append(f"- 总签名数: {data.get('total_signatures', 0)}")
            sections.append(f"- 命中率: {data.get('hit_rate', 0):.1%} | 未命中率: {data.get('miss_rate', 0):.1%}")
            sections.append(f"- 未匹配查询: {data.get('unmatched_queries', 0)}")
            sections.append(f"- 总意图数: {data.get('total_intents', 0)}")
            
            freq_kws = data.get("frequent_unmatched_keywords", {})
            if freq_kws:
                sections.append("- 高频未匹配关键词:")
                for kw, freq in list(freq_kws.items())[:5]:
                    sections.append(f"  - {kw}: {freq}次")
            
            hit_rate = data.get("hit_rate", 0)
            if hit_rate < 0.5 and data.get("total_signatures", 0) > 10:
                sections.append("\n⚠️ 命中率低于50%，建议审查意图模式或运行 --discover-intents")
    except Exception:
        pass
    
    # 2. Intent discovery candidates (from sequence_analyzer)
    try:
        if seq_script.exists():
            result = subprocess.run(
                [sys.executable, str(seq_script), "--discover-intents", "--intent-days", "14", "--json"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                candidates = data.get("candidates", [])
                if candidates:
                    sections.append(f"\n### 🔍 新意图候选 ({len(candidates)})")
                    for c in candidates[:5]:
                        sections.append(f"- **{c['suggested_name']}**: {c['query_count']}条查询")
                        sections.append(f"  关键词: {c['keywords']}")
                        ctx = c.get('suggested_context', {})
                        sections.append(f"  预加载: skills={ctx.get('skills', [])}, role={ctx.get('role', '共享')}")
    except Exception:
        pass
    
    if sections:
        return prompt + "\n".join(sections)
    return prompt


def main():
    parser = argparse.ArgumentParser(
        description="Periodic Nudge Trigger for WorkBuddy (Hermes-inspired)"
    )
    parser.add_argument("--workspace", "-w", required=True,
                        help="WorkBuddy workspace path")
    parser.add_argument("--increment", "-i", nargs="?", type=int, const=1,
                        help="Increment counter by 1 (or N if value provided)")
    parser.add_argument("--tool-count", "-c", type=int, default=0,
                        help="Set total tool call count (absolute value)")
    parser.add_argument("--check", action="store_true",
                        help="Check if nudge is due (exit code 0=due, 1=not)")
    parser.add_argument("--force", action="store_true",
                        help="Force a nudge regardless of interval")
    parser.add_argument("--reset", action="store_true",
                        help="Reset nudge counter after review")
    parser.add_argument("--prompt", action="store_true",
                        help="Output the nudge prompt for Agent injection")
    parser.add_argument("--status", action="store_true",
                        help="Show current nudge state")
    parser.add_argument("--set-interval", type=int,
                        help="Set nudge interval (min 5)")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    parser.add_argument("--user-model-check", action="store_true",
                        help="Check user model contradictions (Phase 3F)")
    parser.add_argument("--session-startup", action="store_true",
                        help="One-shot session startup: check nudge, get prompt, reset if due (for SOUL.md)")
    parser.add_argument("--auto", action="store_true",
                        help="Auto-increment based on time elapsed since last call (for automation)")
    parser.add_argument("--global", dest="use_global", action="store_true",
                        help="Use global nudge state ({MEMORIA_HOME} instead of per-workspace")
    parser.add_argument("--intent-review", action="store_true",
                        help="Include intent learner analysis in nudge prompt (Phase 5)")

    args = parser.parse_args()

    use_global = args.use_global
    state = load_state(args.workspace, use_global=use_global)

    # Auto-increment: called by automation, increments only if 5+ minutes elapsed
    if args.auto:
        incremented = auto_increment(args.workspace, state, use_global=use_global)
        due = is_nudge_due(state)
        if args.json:
            print(json.dumps({**state, "nudge_due": due, "auto_incremented": incremented}, ensure_ascii=False, indent=2))
        elif incremented:
            print(f"📊 Auto-incremented ({state['tools_since_last_nudge']}/{state['nudge_interval']}) | nudge_due={due}")
        # If nudge is due, output prompt directly so automation Agent can act on it
        if due:
            print("\n" + generate_nudge_prompt())
        return

    # Session startup: one-shot check → prompt → reset (for SOUL.md Step 1)
    if args.session_startup:
        import time as _time
        due = is_nudge_due(state)
        hours_since = 999
        if state.get("last_nudge_at"):
            try:
                last = datetime.fromisoformat(state["last_nudge_at"])
                hours_since = (datetime.now() - last).total_seconds() / 3600
            except (ValueError, TypeError):
                pass
        
        needs_review = due or hours_since > 1.0
        
        result = {
            "nudge_due": due,
            "hours_since_last_nudge": round(hours_since, 2),
            "needs_review": needs_review,
            "tools_since_last_nudge": state["tools_since_last_nudge"],
            "nudge_interval": state["nudge_interval"],
            "nudge_count": state["nudge_count"],
        }
        
        if needs_review:
            result["prompt"] = generate_nudge_prompt()
            # Also check user model contradictions
            try:
                um_script = SCRIPTS_DIR / "user_model.py"
                if um_script.exists():
                    um_result = subprocess.run(
                        [sys.executable, str(um_script), "--check", "--json"],
                        capture_output=True, text=True, timeout=10,
                    )
                    if um_result.returncode == 0:
                        um_data = json.loads(um_result.stdout)
                        if um_data.get("findings"):
                            result["user_model_contradictions"] = um_data["findings"]
            except Exception:
                pass
            
            # Inject intent learner analysis into prompt (Phase 5)
            result["prompt"] = _inject_intent_review(result["prompt"])
            
            # Reset counter
            state["tools_since_last_nudge"] = 0
            state["last_nudge_at"] = datetime.now().isoformat()
            state["nudge_count"] += 1
            save_state(args.workspace, state, use_global=use_global)
            result["reset"] = True
        else:
            result["action"] = "skip"
        
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(result, ensure_ascii=False))
        return

    # Increment tool call counter (called periodically by automation)
    if args.increment is not None:
        delta = args.increment  # nargs="?" ensures this is an int or None
        state["total_tool_calls"] += delta
        state["tools_since_last_nudge"] += delta
        save_state(args.workspace, state, use_global=use_global)
        if args.json:
            print(json.dumps({**state, "nudge_due": is_nudge_due(state)}, ensure_ascii=False))
        elif not args.json:
            print(f"📊 Counter incremented by {delta} ({state['tools_since_last_nudge']}/{state['nudge_interval']})")
        return

    # Update tool count (set absolute value)
    if args.tool_count > 0:
        state["total_tool_calls"] += args.tool_count
        state["tools_since_last_nudge"] += args.tool_count
        save_state(args.workspace, state, use_global=use_global)

    # Set custom interval
    if args.set_interval:
        state["nudge_interval"] = max(MIN_NUDGE_INTERVAL, args.set_interval)
        save_state(args.workspace, state, use_global=use_global)
        print(f"✅ Nudge interval set to {state['nudge_interval']}")
        return

    # Show status
    if args.status:
        due = is_nudge_due(state)
        if args.json:
            print(json.dumps({
                **state,
                "nudge_due": due,
            }, ensure_ascii=False, indent=2))
        else:
            print(f"📊 Nudge State")
            print(f"   Tools since last nudge: {state['tools_since_last_nudge']}/{state['nudge_interval']}")
            print(f"   Total tool calls:       {state['total_tool_calls']}")
            print(f"   Nudge count:            {state['nudge_count']}")
            print(f"   Last nudge:             {state['last_nudge_at'] or 'Never'}")
            print(f"   Nudge due:              {'✅ YES' if due else '❌ Not yet'}")
        return

    # Reset counter
    if args.reset:
        state["tools_since_last_nudge"] = 0
        state["last_nudge_at"] = datetime.now().isoformat()
        state["nudge_count"] += 1
        save_state(args.workspace, state, use_global=use_global)
        if not args.json:
            print(f"🔄 Nudge counter reset. Next nudge in {state['nudge_interval']} tool calls.")
        return

    # Output nudge prompt (with optional user model context)
    if args.prompt:
        prompt = generate_nudge_prompt()

        # Inject user model context if requested (Phase 3F + Phase 5 Health)
        if args.user_model_check:
            try:
                um_script = SCRIPTS_DIR / "user_model.py"
                if um_script.exists():
                    # Get contradictions
                    cont_result = subprocess.run(
                        [sys.executable, str(um_script), "--check", "--json"],
                        capture_output=True, text=True, timeout=10,
                    )
                    if cont_result.returncode == 0:
                        data = json.loads(cont_result.stdout)
                        findings = data.get("findings", [])
                        if findings:
                            prompt += "\n\n## ⚠️ 用户模型矛盾检测\n"
                            prompt += "以下偏好存在矛盾，请在审查时关注：\n"
                            for f in findings:
                                prompt += (f"- **{f['key']}**: 曾偏好「{f['value_a']}」"
                                          f"(置信度 {f['confidence_a']:.2f})，"
                                          f"现偏好「{f['value_b']}」"
                                          f"(置信度 {f['confidence_b']:.2f})\n")
                    
                    # Get health report
                    health_result = subprocess.run(
                        [sys.executable, str(um_script), "--health", "--json"],
                        capture_output=True, text=True, timeout=10,
                    )
                    if health_result.returncode == 0:
                        health = json.loads(health_result.stdout)
                        prompt += "\n\n## 🩺 用户模型健康报告\n"
                        prompt += f"- 综合评分: {health['total_score']}/100 [{health['grade']}]\n"
                        dims = health.get("dimensions", {})
                        prompt += f"- 数据密度: {dims.get('data_density', 0):.0f}/25 | "
                        prompt += f"矛盾健康: {dims.get('contradiction_health', 0):.0f}/25 | "
                        prompt += f"置信分布: {dims.get('confidence_distribution', 0):.0f}/20\n"
                        prompt += f"- 数据时效: {dims.get('data_freshness', 0):.0f}/15 | "
                        prompt += f"证据覆盖: {dims.get('evidence_coverage', 0):.0f}/10 | "
                        prompt += f"DB完整: {dims.get('db_integrity', 0):.0f}/5\n"
                        recs = health.get("recommendations", [])
                        if recs:
                            prompt += "- 建议:\n"
                            for r in recs[:3]:
                                prompt += f"  - {r}\n"
            except Exception:
                pass  # Silently skip if user model not available
        
        # Inject intent learner analysis (Phase 5)
        if args.intent_review:
            prompt = _inject_intent_review(prompt)
        if args.json:
            print(json.dumps({"prompt": prompt}, ensure_ascii=False))
        else:
            print(prompt)
        return

    # Check if nudge is due
    if args.check or args.force:
        due = args.force or is_nudge_due(state)
        if args.json:
            print(json.dumps({"nudge_due": due, **state}, ensure_ascii=False, indent=2))
        elif due:
            print("NUDGE_DUE")
        else:
            print("NOT_DUE")
        sys.exit(0 if due else 1)

    # Default: show prompt if due, else nothing
    if is_nudge_due(state):
        print(generate_nudge_prompt())
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
