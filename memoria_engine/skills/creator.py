#!/usr/bin/env python3
"""
skill_creator.py — 自主 Skill 创建器

Phase 3 核心组件 — 基于 skill_detector.py 检测到的模式，自动生成 SKILL.md。
支持质量评分、pending_review 门控、创世架构师委托（复杂模式）。

上游参考: nousresearch/hermes-agent 自主 Skill 创建闭环
依赖: skill_detector.py (模式检测), skill-creator (基础模板), 创世架构师 (复杂 Prompt 生成)

用法:
    # 从检测器输出创建 Skill
    python3 skill_creator.py --from-detector <detector_output.json>

    # 直接指定模式数据
    python3 skill_creator.py --create --name "my-skill" --pattern '{"tools": [...], "workflow": "..."}'

    # 审查 pending_review 的 Skill
    python3 skill_creator.py --review --skill "my-skill"

    # 批量质量评分
    python3 skill_creator.py --score-all

输出: {MEMORIA_HOME} (pending_review 状态)
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

SKILLS_DIR = os.path.expanduser("{MEMORIA_HOME}")
DRAFTS_DIR = os.path.join(SKILLS_DIR, ".drafts")
TZ_OFFSET = timedelta(hours=8)

# 复杂度阈值 — 超过此值委托 创世架构师
COMPLEXITY_THRESHOLD_FOR_META_AGENT = 0.75

# 质量评分权重
QUALITY_WEIGHTS = {
    "completeness": 0.25,    # 必填字段完整度
    "specificity": 0.25,     # 触发词/描述具体程度
    "uniqueness": 0.20,      # 与已有 Skill 的差异化
    "actionability": 0.15,   # 可执行性（allowed-tools 合理）
    "brevity": 0.15,         # 简洁性（不冗余）
}

# YAML frontmatter 必填字段
REQUIRED_FIELDS = ["name", "description", "trigger", "allowed-tools"]


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _safe_write_skill(filepath: str, content: str, slug: str) -> Dict:
    """
    通过 write_verifier 安全写入 SKILL.md。
    验证失败 → 自动修复 → 写入 .broken/ → 返回错误状态。
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    try:
        from ..memory.write_verifier import verify_and_write
    except ImportError:
        # write_verifier 不可用 — 回退到直接写入（向后兼容）
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return {"success": True, "via": "direct_fallback"}

    result = verify_and_write(filepath, content, "markdown", max_retries=2)
    if result["success"]:
        return result

    # 验证失败 — 保存到 .broken/ 并报警
    broken_dir = os.path.join(SKILLS_DIR, ".broken")
    os.makedirs(broken_dir, exist_ok=True)
    broken_path = os.path.join(broken_dir, f"{slug}_{int(time.time())}.md")
    try:
        with open(broken_path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError:
        broken_path = "(写入 .broken/ 失败)"

    result["broken_path"] = broken_path
    return result


def _now_iso() -> str:
    """返回当前 ISO 时间戳"""
    return datetime.now(timezone(TZ_OFFSET)).isoformat()


def _slugify(name: str) -> str:
    """将名称转为 kebab-case slug"""
    slug = re.sub(r'[^a-z0-9\-\u4e00-\u9fff]', '-', name.lower().strip())
    slug = re.sub(r'-{2,}', '-', slug).strip('-')
    return slug or "unnamed-skill"


def _load_existing_skill_names() -> List[str]:
    """扫描已有 Skill 名称"""
    if not os.path.isdir(SKILLS_DIR):
        return []
    names = []
    for entry in os.scandir(SKILLS_DIR):
        if entry.is_dir() and os.path.isfile(os.path.join(entry.path, "SKILL.md")):
            names.append(entry.name)
    return names


# ---------------------------------------------------------------------------
# YAML Frontmatter 生成
# ---------------------------------------------------------------------------

def generate_frontmatter(
    name: str,
    description: str,
    triggers: List[str],
    allowed_tools: List[str],
    complexity: float = 0.0,
) -> str:
    """
    生成 SKILL.md YAML frontmatter。

    必填字段: name, description, trigger, allowed-tools
    可选字段: agent_created (true), review_status (pending_review), complexity, created_at
    """
    slug = _slugify(name)
    lines = [
        "---",
        f"name: {slug}",
        f"description: {description}",
        "trigger:",
    ]
    for t in triggers:
        lines.append(f"  - {t}")
    lines.append("allowed-tools:")
    for tool in allowed_tools:
        lines.append(f"  - {tool}")
    lines.extend([
        "agent_created: true",
        f"review_status: pending_review",
        f"complexity: {complexity:.2f}",
        f"created_at: \"{_now_iso()}\"",
        "---",
    ])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Skill Body 生成
# ---------------------------------------------------------------------------

def generate_body(
    name: str,
    description: str,
    pattern: Dict[str, Any],
    complexity: float = 0.0,
) -> str:
    """
    生成 SKILL.md 正文内容。

    包括: 概述、使用场景、典型工作流、前置条件、质量门
    """
    tools_used = pattern.get("tools_used", [])
    tool_calls = pattern.get("tool_calls", 0)
    workflow_desc = pattern.get("workflow_description", "")
    error_recovery = pattern.get("had_error_recovery", False)
    file_ops = pattern.get("file_operations", 0)

    # 触发词从 pattern 推断
    detected_triggers = pattern.get("detected_triggers", [])

    body = f"""# {name}

> **状态**: pending_review — 由 skill_creator.py 自动生成，等待人工审查  
> **复杂度**: {complexity:.2f} | **生成时间**: {_now_iso()}  
> **触发模式**: 检测到 {tool_calls} 次工具调用 | {file_ops} 次文件操作

---

## 概述

{description}

## 检测到的模式

- **工具调用数**: {tool_calls}
- **文件操作数**: {file_ops}
- **使用的工具**: {', '.join(tools_used) if tools_used else '（未检测到）'}
- **错误恢复**: {'是' if error_recovery else '否'}
- **检测到的触发词**: {', '.join(detected_triggers) if detected_triggers else '（自动推断）'}

## 典型工作流

{workflow_desc if workflow_desc else '（待补充 — 请基于实际使用场景完善）'}

## 使用方式

### 触发条件
当用户请求涉及以下操作时自动加载：
{chr(10).join(f'- {t}' for t in detected_triggers) if detected_triggers else '- （待补充触发词）'}

### 前置条件
- Python 3.11+
- （待补充具体依赖）

## 质量门

在激活前需要确认：
- [ ] 此 Skill 覆盖的场景是否确实重复出现？
- [ ] 触发词是否足够精确（不会误触发）？
- [ ] allowed-tools 列表是否最小必要？
- [ ] 是否需要委托 创世架构师 优化 Prompt？

---

> ⚠️ 此 Skill 由 autonomous skill_creator 生成，尚未经过人工审查。  
> 审查通过后，将 review_status 改为 `active` 即可激活。
"""
    return body


# ---------------------------------------------------------------------------
# 质量评分
# ---------------------------------------------------------------------------

def score_skill(skill_content: str, existing_names: List[str]) -> Dict[str, Any]:
    """
    对生成的 SKILL.md 内容进行质量评分。

    返回: {total_score, dimensions: {completeness, specificity, ...}, warnings: [...]}
    """
    dimensions = {}
    warnings = []

    # 1. 完整度 — 必填字段是否都有值
    field_presence = 0
    for field in REQUIRED_FIELDS:
        if re.search(rf'^{field}:\s*\S', skill_content, re.MULTILINE):
            field_presence += 1
    dimensions["completeness"] = field_presence / len(REQUIRED_FIELDS)
    if field_presence < len(REQUIRED_FIELDS):
        missing = [f for f in REQUIRED_FIELDS if not re.search(rf'^{f}:\s*\S', skill_content, re.MULTILINE)]
        warnings.append(f"缺失必填字段: {', '.join(missing)}")

    # 2. 具体度 — 触发词和描述的长度
    trigger_section = re.search(r'trigger:\n((?:\s{2}- .+\n?)+)', skill_content)
    if trigger_section:
        trigger_count = len(re.findall(r'^\s{2}- ', trigger_section.group(1), re.MULTILINE))
        dimensions["specificity"] = min(trigger_count / 5, 1.0)
    else:
        dimensions["specificity"] = 0.0
        warnings.append("未找到触发词列表")

    # 3. 独特性 — 名称是否与已有 Skill 冲突
    name_match = re.search(r'^name:\s*(\S+)', skill_content, re.MULTILINE)
    skill_name = name_match.group(1) if name_match else ""
    if skill_name in existing_names:
        dimensions["uniqueness"] = 0.0
        warnings.append(f"名称 '{skill_name}' 与已有 Skill 冲突")
    else:
        # 检查部分重叠
        overlapping = [n for n in existing_names if skill_name in n or n in skill_name]
        dimensions["uniqueness"] = max(0.2, 1.0 - len(overlapping) * 0.2)

    # 4. 可执行性 — allowed-tools 是否合理
    tools_match = re.search(r'allowed-tools:\n((?:\s{2}- .+\n?)+)', skill_content)
    if tools_match:
        tool_count = len(re.findall(r'^\s{2}- ', tools_match.group(1), re.MULTILINE))
        # 1-5 个工具是合理范围
        dimensions["actionability"] = 1.0 if 1 <= tool_count <= 5 else 0.5
    else:
        dimensions["actionability"] = 0.0

    # 5. 简洁性 — 内容长度适中
    body_start = skill_content.find("---", 10)
    body = skill_content[body_start:] if body_start > 0 else skill_content
    body_len = len(body.strip())
    if 500 <= body_len <= 3000:
        dimensions["brevity"] = 1.0
    elif body_len < 500:
        dimensions["brevity"] = 0.5
    else:
        dimensions["brevity"] = max(0.3, 1.0 - (body_len - 3000) / 5000)

    # 加权总分
    total = sum(
        dimensions.get(k, 0) * v
        for k, v in QUALITY_WEIGHTS.items()
    )

    return {
        "total_score": round(total, 3),
        "dimensions": {k: round(v, 3) for k, v in dimensions.items()},
        "warnings": warnings,
        "pass_threshold": total >= 0.6,
    }


# ---------------------------------------------------------------------------
# 主入口: 创建 Skill
# ---------------------------------------------------------------------------

def create_skill(
    name: str,
    pattern: Dict[str, Any],
    force_overwrite: bool = False,
    use_meta_agent: Optional[bool] = None,
    background: bool = False,
) -> Dict[str, Any]:
    """
    基于检测到的模式创建 Skill。

    参数:
        name: Skill 名称
        pattern: skill_detector.py 输出的模式数据
        force_overwrite: 是否覆盖已有 Skill
        use_meta_agent: 是否委托 创世架构师 (None = 自动判断, True/False = 强制)
        background: 后台模式 (Genesis Protocol) — 写入 .drafts/ 而非 skills/

    返回: {skill_name, path, quality, status, meta_agent_used, hitl_card (if background)}
    """
    slug = _slugify(name)

    # 后台模式：目标路径为 .drafts/
    if background:
        skill_dir = os.path.join(DRAFTS_DIR, slug)
        skill_path = os.path.join(skill_dir, "SKILL.md")
    else:
        skill_dir = os.path.join(SKILLS_DIR, slug)
        skill_path = os.path.join(skill_dir, "SKILL.md")

    # 冲突检查（前台模式检查 skills/，后台模式检查 .drafts/）
    if os.path.isfile(skill_path) and not force_overwrite:
        # 后台模式：自动添加 _v2 后缀
        if background:
            counter = 2
            base_slug = slug
            while os.path.isfile(os.path.join(skill_dir, "SKILL.md")):
                slug = f"{base_slug}-v{counter}"
                skill_dir = os.path.join(DRAFTS_DIR, slug)
                skill_path = os.path.join(skill_dir, "SKILL.md")
                counter += 1
        else:
            return {
                "skill_name": slug,
                "path": skill_path,
                "quality": None,
                "status": "conflict",
                "error": f"Skill '{slug}' 已存在，使用 --force 覆盖",
            }

    # 提取模式数据
    complexity = pattern.get("composite_score", 0.0)
    tools_used = pattern.get("tools_used", [])
    triggers = pattern.get("detected_triggers", _infer_triggers(name, pattern))
    allowed_tools = _infer_allowed_tools(tools_used)
    description = _generate_description(name, pattern)

    # 判断是否委托 创世架构师
    if use_meta_agent is None:
        use_meta_agent = complexity >= COMPLEXITY_THRESHOLD_FOR_META_AGENT

    # 生成内容
    frontmatter = generate_frontmatter(slug, description, triggers, allowed_tools, complexity)

    # 后台模式：使用 draft 状态
    if background:
        frontmatter = frontmatter.replace("review_status: pending_review", "review_status: draft")

    body = generate_body(name, description, pattern, complexity)

    # 后台模式：追加 [Background Mode] 静默生成标记
    if background:
        body = body.replace(
            "> **状态**: pending_review",
            "> **状态**: draft — Genesis Protocol 后台生成，等待 HITL 审批"
        )

    full_content = frontmatter + "\n" + body

    # 如果复杂度高，追加 Meta-Agent 委托提示
    if use_meta_agent:
        delegation = _meta_agent_delegation_note(name, complexity)
        # 后台模式：追加 [Background Mode] 指令
        if background:
            delegation += _background_mode_instruction(name, slug)
        full_content += delegation

    # 质量评分
    existing_names = _load_existing_skill_names()
    quality = score_skill(full_content, existing_names)

    # 写入磁盘（通过 write_verifier 验证，AEGIS-Patch 防线）
    write_result = _safe_write_skill(skill_path, full_content, slug)
    if not write_result.get("success"):
        return {
            "skill_name": slug,
            "path": skill_path,
            "quality": quality,
            "status": "verification_failed",
            "error": f"格式验证失败: {write_result.get('error')}",
            "broken_path": write_result.get("broken_path"),
            "auto_fixed": write_result.get("auto_fixed", 0),
        }

    result = {
        "skill_name": slug,
        "path": skill_path,
        "quality": quality,
        "status": "draft" if background else "pending_review",
        "meta_agent_recommended": use_meta_agent,
        "created_at": _now_iso(),
    }

    # 后台模式：生成 HITL 确认卡片
    if background:
        hitl_card = _write_hitl_card(slug, skill_path, quality, use_meta_agent)
        result["hitl_card"] = hitl_card

    return result


# ---------------------------------------------------------------------------
# 辅助: 推断触发词
# ---------------------------------------------------------------------------

def _infer_triggers(name: str, pattern: Dict[str, Any]) -> List[str]:
    """从模式数据推断触发词"""
    triggers = pattern.get("detected_triggers", [])
    if triggers:
        return triggers

    # 回退推断
    inferred = []
    workflow = pattern.get("workflow_description", "").lower()
    tools = [t.lower() for t in pattern.get("tools_used", [])]

    # 基于工具推断
    tool_map = {
        "websearch": "搜索",
        "webfetch": "抓取",
        "write": "创建",
        "bash": "执行",
        "read": "读取",
        "edit": "编辑",
        "skill": "技能",
        "task": "任务",
        "agent": "代理",
        "python": "脚本",
    }
    for tool in tools:
        if tool in tool_map:
            inferred.append(tool_map[tool])

    # 基于名称推断
    name_lower = name.lower()
    name_keywords = {
        "report": "报告",
        "analysis": "分析",
        "sync": "同步",
        "deploy": "部署",
        "test": "测试",
        "build": "构建",
        "check": "检查",
        "monitor": "监控",
        "backup": "备份",
        "convert": "转换",
    }
    for kw, cn in name_keywords.items():
        if kw in name_lower:
            inferred.append(cn)

    # 添加中文通用触发词
    if "搜索" in inferred or "抓取" in inferred:
        inferred.append("查找")
    if "创建" in inferred or "生成" in inferred:
        inferred.append("新建")

    return inferred[:6] if inferred else [name, "自动"]


def _infer_allowed_tools(tools_used: List[str]) -> List[str]:
    """从使用的工具推断允许的工具列表"""
    # 标准化工具名
    standard = {"bash", "read", "write", "edit", "glob", "grep",
                "websearch", "webfetch", "skill", "task", "agent",
                "web_search", "web_fetch", "askuserquestion"}
    allowed = []
    for t in tools_used:
        t_lower = t.lower().replace("_", "").replace("-", "")
        if t_lower in {"bash", "execute_command"}:
            allowed.append("Bash")
        elif t_lower == "read":
            allowed.append("Read")
        elif t_lower in {"write", "edit"}:
            allowed.append("Write")
        elif t_lower in {"glob", "grep"}:
            allowed.append("Glob")
        elif t_lower in {"websearch", "web_search"}:
            allowed.append("WebSearch")
        elif t_lower in {"webfetch", "web_fetch"}:
            allowed.append("WebFetch")
        elif t_lower == "skill":
            allowed.append("Skill")

    # 最小集: 至少 Read + Write
    if not allowed:
        allowed = ["Read", "Write", "Bash"]
    return list(dict.fromkeys(allowed))  # 去重保序


def _generate_description(name: str, pattern: Dict[str, Any]) -> str:
    """生成 Skill 描述"""
    workflow = pattern.get("workflow_description", "")
    tool_calls = pattern.get("tool_calls", 0)
    if workflow:
        # 截断到 150 字符
        if len(workflow) > 150:
            workflow = workflow[:147] + "..."
        return workflow
    # 回退
    return f"自动生成的 Skill: {name}（检测到 {tool_calls} 次工具调用模式）"


def _meta_agent_delegation_note(name: str, complexity: float) -> str:
    """生成创世架构师委托提示"""
    return f"""

---

## 🤖 创世架构师委托建议

> **复杂度 {complexity:.2f}** ≥ 阈值 {COMPLEXITY_THRESHOLD_FOR_META_AGENT} — 建议委托 **创世架构师 (Meta-Agent Gen)** 优化此 Skill 的 Prompt 质量。

创世架构师可通过以下方式介入：
```
用户: "用创世架构师优化 {name} Skill"
```
创世架构师将基于第一性原理推演 + 12 模块架构重新设计 Prompt，提升：
- 边界清晰度
- 触发精确度  
- 输出质量标准
"""


def _background_mode_instruction(name: str, slug: str) -> str:
    """生成后台模式附加指令 — 供上游调用创世架构师时注入"""
    return f"""

---

## 🔇 [Background Mode] — Genesis Protocol 后台生成指令

> 此 Skill 由 `skill_creator.py --background` 自动生成，委托创世架构师在后台模式下优化。

**委托指令**（供上游 Agent 使用）：
```
[Background Mode] 请为以下 Skill 设计完整的 SKILL.md：
- 名称：{name}
- Slug：{slug}
- 目标：优化当前草稿的 Prompt 质量至 ≥ 0.70（11 维评审标准）
- 约束：不输出问候语，不执行 Phase 1 对齐，直接进入 Phase 2/3
- 产出：写入 .drafts/{slug}/SKILL.md 并附带 HITL 卡片 JSON
```

上游 Agent 调用创世架构师时，将 `[Background Mode]` 注入请求前缀即可触发后台模式。
"""


def _write_hitl_card(
    slug: str,
    draft_path: str,
    quality: Dict[str, Any],
    meta_agent_used: bool,
) -> Dict[str, Any]:
    """生成并写入 HITL 确认卡片到草稿目录"""
    card = {
        "genesis_protocol": "v1.0",
        "mode": "background",
        "skill_name": slug,
        "slug": slug,
        "draft_path": draft_path,
        "quality_score": quality.get("total_score", 0.0),
        "dimensions": quality.get("dimensions", {}),
        "warnings": quality.get("warnings", []),
        "pass_threshold": quality.get("pass_threshold", False),
        "meta_agent_used": meta_agent_used,
        "created_at": _now_iso(),
        "actions": [
            {"id": 1, "label": "查看完整 SKILL.md", "action": "view"},
            {"id": 2, "label": "微调描述/触发词", "action": "edit"},
            {"id": 3, "label": "激活 Skill", "action": "activate"},
        ],
    }

    # 写入 .drafts/<slug>/hitl_card.json
    card_dir = os.path.join(DRAFTS_DIR, slug)
    os.makedirs(card_dir, exist_ok=True)
    card_path = os.path.join(card_dir, "hitl_card.json")
    with open(card_path, "w") as f:
        json.dump(card, f, ensure_ascii=False, indent=2)

    return card


def _activate_draft(slug: str) -> Dict[str, Any]:
    """激活草稿：从 .drafts/ 移至 skills/"""
    draft_dir = os.path.join(DRAFTS_DIR, slug)
    draft_path = os.path.join(draft_dir, "SKILL.md")
    target_dir = os.path.join(SKILLS_DIR, slug)
    target_path = os.path.join(target_dir, "SKILL.md")

    if not os.path.isfile(draft_path):
        return {"status": "not_found", "error": f"草稿 '{slug}' 不存在于 .drafts/"}

    # 读取草稿内容
    with open(draft_path, "r") as f:
        content = f.read()

    existing_names = _load_existing_skill_names()
    quality = score_skill(content, existing_names)

    # 检查质量
    if not quality.get("pass_threshold", False):
        return {
            "status": "rejected",
            "error": f"质量评分 {quality['total_score']} 未达到阈值 0.6",
            "quality": quality,
        }

    # 修改状态: draft → active
    content = content.replace("review_status: draft", "review_status: active")
    content = content.replace(
        "> **状态**: draft — Genesis Protocol 后台生成，等待 HITL 审批",
        "> **状态**: active — 已通过 HITL 审批"
    )

    # 写入目标目录（通过 write_verifier 验证）
    write_result = _safe_write_skill(target_path, content, slug)
    if not write_result.get("success"):
        return {
            "status": "verification_failed",
            "error": f"激活写入验证失败: {write_result.get('error')}",
            "broken_path": write_result.get("broken_path"),
        }

    # 归档草稿
    archive_dir = os.path.join(DRAFTS_DIR, "archive")
    os.makedirs(archive_dir, exist_ok=True)
    archive_target = os.path.join(archive_dir, slug)
    if os.path.exists(archive_target):
        # 已存在归档，添加时间戳
        ts = datetime.now(timezone(TZ_OFFSET)).strftime("%Y%m%d_%H%M%S")
        archive_target = os.path.join(archive_dir, f"{slug}_{ts}")
    os.rename(draft_dir, archive_target)

    return {
        "status": "active",
        "skill_name": slug,
        "draft_path": draft_path,
        "active_path": target_path,
        "quality": quality,
        "archived_at": archive_target,
        "message": f"Skill '{slug}' 已从草稿激活至 {target_path}",
    }


# ---------------------------------------------------------------------------
# Review 模式
# ---------------------------------------------------------------------------

def review_skill(skill_name: str, activate: bool = False) -> Dict[str, Any]:
    """审查 pending_review 的 Skill"""
    slug = _slugify(skill_name)
    skill_path = os.path.join(SKILLS_DIR, slug, "SKILL.md")

    if not os.path.isfile(skill_path):
        return {"status": "not_found", "error": f"Skill '{slug}' 不存在"}

    with open(skill_path, "r") as f:
        content = f.read()

    existing_names = [n for n in _load_existing_skill_names() if n != slug]
    quality = score_skill(content, existing_names)

    result = {
        "skill_name": slug,
        "path": skill_path,
        "quality": quality,
        "status": "pending_review",
    }

    if activate and quality.get("pass_threshold", False):
        # 激活: 移除 review_status: pending_review
        new_content = re.sub(
            r'^review_status:\s*pending_review',
            'review_status: active',
            content,
            flags=re.MULTILINE
        )
        write_result = _safe_write_skill(skill_path, new_content, slug)
        if not write_result.get("success"):
            return {
                "skill_name": slug,
                "path": skill_path,
                "quality": quality,
                "status": "verification_failed",
                "error": f"激活写入验证失败: {write_result.get('error')}",
            }
        result["status"] = "active"
        result["message"] = f"Skill '{slug}' 已激活"
    elif activate:
        result["status"] = "rejected"
        result["message"] = f"质量评分 {quality['total_score']} 未达到阈值 0.6，无法激活"

    return result


# ---------------------------------------------------------------------------
# 批量评分
# ---------------------------------------------------------------------------

def score_all_pending() -> List[Dict[str, Any]]:
    """评分所有 pending_review 的 Skill"""
    results = []
    existing_names = _load_existing_skill_names()

    for name in existing_names:
        skill_path = os.path.join(SKILLS_DIR, name, "SKILL.md")
        with open(skill_path, "r") as f:
            content = f.read()

        if "review_status: pending_review" in content:
            quality = score_skill(content, [n for n in existing_names if n != name])
            results.append({
                "skill_name": name,
                "path": skill_path,
                "quality": quality,
                "status": "pending_review",
            })

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="skill_creator.py — Phase 3 自主 Skill 创建器 (v2.0 Genesis Protocol)"
    )
    parser.add_argument(
        "--from-detector", type=str,
        help="从 skill_detector.py 的 JSON 输出创建 Skill"
    )
    parser.add_argument(
        "--create", action="store_true",
        help="直接创建 Skill"
    )
    parser.add_argument(
        "--name", type=str,
        help="Skill 名称"
    )
    parser.add_argument(
        "--pattern", type=str, default="{}",
        help="模式数据 (JSON 字符串)"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="覆盖已有 Skill"
    )
    parser.add_argument(
        "--use-meta-agent", action="store_true", default=None,
        help="强制使用创世架构师"
    )
    parser.add_argument(
        "--no-meta-agent", action="store_false", dest="use_meta_agent",
        help="禁止使用创世架构师"
    )
    parser.add_argument(
        "--review", action="store_true",
        help="审查 pending_review 的 Skill"
    )
    parser.add_argument(
        "--skill", type=str,
        help="目标 Skill 名称 (用于 --review / --activate)"
    )
    parser.add_argument(
        "--activate", action="store_true",
        help="通过审查后激活 Skill (需配合 --review)；或从 .drafts/ 激活草稿"
    )
    parser.add_argument(
        "--background", action="store_true",
        help="后台模式 (Genesis Protocol)：写入 .drafts/ 而非 skills/，生成 HITL 卡片"
    )
    parser.add_argument(
        "--score-all", action="store_true",
        help="评分所有 pending_review 的 Skill"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="JSON 输出"
    )

    args = parser.parse_args()

    # --from-detector
    if args.from_detector:
        if not os.path.isfile(args.from_detector):
            print(json.dumps({"error": f"文件不存在: {args.from_detector}"}, ensure_ascii=False))
            sys.exit(1)
        with open(args.from_detector, "r") as f:
            detector_output = json.load(f)

        # 期望格式: {"patterns": [{"name": ..., "composite_score": ..., ...}]}
        patterns = detector_output.get("patterns", [detector_output])
        results = []
        for pat in patterns:
            name = pat.get("name", pat.get("pattern_name", "auto-detected-skill"))
            result = create_skill(name, pat, args.force, args.use_meta_agent, args.background)
            results.append(result)

        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            for r in results:
                status_icon = {"draft": "📝", "pending_review": "📝", "active": "✅", "conflict": "⚠️"}.get(r["status"], "❓")
                print(f"{status_icon} {r['skill_name']}: {r['status']} → {r['path']}")
                if r.get("quality"):
                    q = r["quality"]
                    print(f"   质量: {q['total_score']:.2f} | {'✅ 通过' if q['pass_threshold'] else '⚠️ 未达标'}")
                if r.get("meta_agent_recommended"):
                    print(f"   🤖 建议委托创世架构师优化")
                if r.get("hitl_card"):
                    hc = r["hitl_card"]
                    print(f"   📋 HITL 卡片已生成 → {DRAFTS_DIR}/{r['skill_name']}/hitl_card.json")
                    print(f"   🔲 [1] 查看 | [2] 微调 | [3] 激活")
        return

    # --create
    if args.create and args.name:
        pattern = json.loads(args.pattern)
        result = create_skill(args.name, pattern, args.force, args.use_meta_agent, args.background)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # --review
    if args.review and args.skill:
        # 如果指定 --background + --activate，从 .drafts/ 激活
        if args.background and args.activate:
            result = _activate_draft(args.skill)
        else:
            result = review_skill(args.skill, args.activate)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # --activate from draft (standalone)
    if args.activate and args.skill and not args.review:
        result = _activate_draft(args.skill)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # --score-all
    if args.score_all:
        results = score_all_pending()
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            if not results:
                print("没有 pending_review 的 Skill")
            for r in results:
                print(f"{r['skill_name']}: {r['quality']['total_score']:.2f} "
                      f"({'✅' if r['quality']['pass_threshold'] else '⚠️'})")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
