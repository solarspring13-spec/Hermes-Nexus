#!/usr/bin/env python3
"""
write_verifier.py — 文件格式验证器（AEGIS-Patch 防线补全）

在落盘前验证 AI 生成内容的格式正确性，防止破损配置文件污染系统。
支持 YAML / JSON / Python / Markdown 四种格式的结构化校验。

与 skill_creator.py 集成：所有 SKILL.md 写入操作经过本模块验证后方可落盘。
验证失败 → 自动修复重试 → 不写入破损文件 → 报警通知。

上游参考: nousresearch/hermes-agent 自主 Skill 创建闭环 — 验证门禁
Phase: v4.0 Architecture Master Plan — AEGIS-Patch D2

用法:
    from ..memory.write_verifier import verify_and_write, verify_content, VERIFIERS
    result = verify_and_write("/path/to/file.yaml", content, "yaml")
    # → {"success": True, "path": ..., "verified": True, "error": None}
"""

import ast
import json
import os
import re
import sys
from typing import Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 格式验证器
# ---------------------------------------------------------------------------

def _verify_yaml(content: str) -> Optional[str]:
    """
    验证 YAML 格式 — 使用 yaml.safe_load() 尝试解析。

    返回 None → 通过；返回 str → 错误信息。
    """
    try:
        import yaml
        yaml.safe_load(content)
        return None
    except ImportError:
        return "yaml 模块未安装，无法验证 YAML 格式"
    except yaml.YAMLError as e:
        # 提取关键行号信息
        if hasattr(e, 'problem_mark') and e.problem_mark:
            mark = e.problem_mark
            return f"YAML 解析错误 (line {mark.line + 1}, col {mark.column + 1}): {e.problem}"
        return f"YAML 解析错误: {e}"


def _verify_json(content: str) -> Optional[str]:
    """
    验证 JSON 格式 — 使用 json.loads() 尝试解析。
    """
    try:
        json.loads(content)
        return None
    except json.JSONDecodeError as e:
        return f"JSON 解析错误 (line {e.lineno}, col {e.colno}): {e.msg}"


def _verify_python(content: str) -> Optional[str]:
    """
    验证 Python 语法 — 使用 ast.parse() 进行语法树编译。

    能捕获：缩进错误、语法错误、未闭合括号、无效运算符等。
    """
    try:
        ast.parse(content)
        return None
    except SyntaxError as e:
        return f"Python 语法错误 (line {e.lineno}, col {e.offset}): {e.msg}"
    except Exception as e:
        return f"Python AST 解析失败: {e}"


def _verify_markdown(content: str) -> Optional[str]:
    """
    验证 Markdown 格式 — 检查未闭合的代码块和 YAML frontmatter。

    检查项:
      1. 三反引号代码块 (```) 是否配对
      2. YAML frontmatter 分隔符 (---) 是否配对
    """
    errors: List[str] = []

    # 检查 1: 三反引号代码块配对
    # 匹配行首的三个反引号（允许后跟语言标识）
    fences = re.findall(r'^```', content, re.MULTILINE)
    if len(fences) % 2 != 0:
        errors.append(
            f"Markdown 代码块未闭合: 发现 {len(fences)} 个 ``` 标记（应为偶数）"
        )

    # 检查 2: YAML frontmatter 分隔符配对
    # 匹配行首的 `---`（独立成行）
    dashes = re.findall(r'^---\s*$', content, re.MULTILINE)
    if len(dashes) % 2 != 0:
        errors.append(
            f"YAML frontmatter 分隔符未闭合: 发现 {len(dashes)} 个 --- 标记（应为偶数）"
        )

    return "; ".join(errors) if errors else None


# ---------------------------------------------------------------------------
# 验证器注册表
# ---------------------------------------------------------------------------

# 文件扩展名 → 验证器映射
_EXTENSION_VERIFIERS: Dict[str, Callable[[str], Optional[str]]] = {
    ".yaml": _verify_yaml,
    ".yml": _verify_yaml,
    ".json": _verify_json,
    ".py": _verify_python,
    ".pyw": _verify_python,
    ".md": _verify_markdown,
    ".markdown": _verify_markdown,
}

# 类型名 → 验证器映射（用于显式指定 file_type）
VERIFIERS: Dict[str, Callable[[str], Optional[str]]] = {
    "yaml": _verify_yaml,
    "yml": _verify_yaml,
    "json": _verify_json,
    "python": _verify_python,
    "py": _verify_python,
    "markdown": _verify_markdown,
    "md": _verify_markdown,
}


def _resolve_file_type(filepath: str, file_type: Optional[str] = None) -> Optional[str]:
    """从 file_type 或文件扩展名解析标准化的类型键。"""
    if file_type:
        return file_type.lower()
    ext = os.path.splitext(filepath)[1].lower()
    if ext in _EXTENSION_VERIFIERS:
        return ext  # 直接用扩展名作为键
    return None


def _get_verifier(file_type: str) -> Optional[Callable[[str], Optional[str]]]:
    """获取验证器函数。"""
    return VERIFIERS.get(file_type.lower())


# ---------------------------------------------------------------------------
# 自动修复器（轻量级）
# ---------------------------------------------------------------------------

def _auto_fix(content: str, file_type: str, error_msg: str) -> Optional[str]:
    """
    尝试自动修复常见格式错误。

    返回修复后的内容，或 None（无法自动修复）。
    """
    ft = file_type.lower()

    if ft in ("markdown", "md"):
        # 未闭合代码块 → 追加闭合
        if "代码块未闭合" in error_msg:
            return content.rstrip() + "\n```\n"
        # 未闭合 frontmatter → 追加分隔符
        if "frontmatter 未闭合" in error_msg or "分隔符未闭合" in error_msg:
            return content.rstrip() + "\n---\n"

    if ft in ("json",):
        # JSON: 尝试去除尾部逗号
        if "trailing comma" in error_msg.lower() or "Expecting" in error_msg:
            fixed = re.sub(r',\s*}', '}', content)
            fixed = re.sub(r',\s*]', ']', fixed)
            if fixed != content:
                return fixed

    # 无法自动修复
    return None


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def verify_content(content: str, file_type: str) -> Dict:
    """
    验证内容格式，不写入磁盘。

    参数:
        content: 待验证内容
        file_type: 文件类型 (yaml/json/python/markdown/md/py)

    返回: {"valid": bool, "error": str|None}
    """
    verifier = _get_verifier(file_type)
    if verifier is None:
        return {"valid": False, "error": f"不支持的文件类型: '{file_type}'。支持: {', '.join(VERIFIERS.keys())}"}
    error = verifier(content)
    if error:
        return {"valid": False, "error": error}
    return {"valid": True, "error": None}


def verify_and_write(
    filepath: str,
    content: str,
    file_type: Optional[str] = None,
    max_retries: int = 2,
    backup: bool = True,
) -> Dict:
    """
    验证内容格式，通过后方可落盘写入。

    验证流程:
      1. 格式校验 → 失败则尝试自动修复（最多 max_retries 次）
      2. 备份原文件（如果已存在）
      3. 写入磁盘

    参数:
        filepath:   目标文件绝对路径
        content:    待写入的完整内容
        file_type:  文件类型 (yaml/json/python/markdown)，默认从扩展名推断
        max_retries: 格式修复的最大重试次数（默认 2）
        backup:     写入前是否备份原文件（默认 True）

    返回:
        {"success": bool, "path": str, "verified": bool, "auto_fixed": int, "error": str|None}
    """
    # 解析文件类型
    ft = _resolve_file_type(filepath, file_type)
    if ft is None:
        return {
            "success": False,
            "path": filepath,
            "verified": False,
            "auto_fixed": 0,
            "error": f"无法从路径推断文件类型，请显式指定 file_type。支持: {', '.join(VERIFIERS.keys())}",
        }

    # Step 1: 验证 + 自动修复循环
    auto_fixed_count = 0
    current_content = content

    for attempt in range(max_retries + 1):
        verifier = _get_verifier(ft)
        if verifier is None:
            return {"success": False, "path": filepath, "verified": False, "auto_fixed": auto_fixed_count,
                    "error": f"不支持的文件类型: '{ft}'"}

        error = verifier(current_content)
        if error is None:
            # 验证通过
            break

        # 验证失败 — 尝试自动修复
        if attempt < max_retries:
            fixed = _auto_fix(current_content, ft, error)
            if fixed is not None:
                current_content = fixed
                auto_fixed_count += 1
                continue

        # 无法修复 — 拒绝写入
        return {
            "success": False,
            "path": filepath,
            "verified": False,
            "auto_fixed": auto_fixed_count,
            "error": f"格式验证失败（已尝试 {auto_fixed_count} 次自动修复）: {error}",
        }

    # Step 2: 备份原文件
    if backup and os.path.isfile(filepath):
        backup_path = filepath + ".verifier.bak"
        try:
            with open(filepath, "r", encoding="utf-8") as src:
                with open(backup_path, "w", encoding="utf-8") as dst:
                    dst.write(src.read())
        except OSError as e:
            return {
                "success": False, "path": filepath, "verified": True,
                "auto_fixed": auto_fixed_count, "error": f"备份失败: {e}",
            }

    # Step 3: 写入磁盘
    try:
        dirpath = os.path.dirname(filepath)
        if dirpath:
            os.makedirs(dirpath, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(current_content)
    except OSError as e:
        return {
            "success": False, "path": filepath, "verified": True,
            "auto_fixed": auto_fixed_count, "error": f"写入失败: {e}",
        }

    return {
        "success": True,
        "path": filepath,
        "verified": True,
        "auto_fixed": auto_fixed_count,
        "error": None,
    }


# ---------------------------------------------------------------------------
# 便捷函数 — 按类型
# ---------------------------------------------------------------------------

def verify_and_write_yaml(filepath: str, content: str, **kwargs) -> Dict:
    """验证 YAML 格式后写入。"""
    return verify_and_write(filepath, content, "yaml", **kwargs)


def verify_and_write_json(filepath: str, content: str, **kwargs) -> Dict:
    """验证 JSON 格式后写入。"""
    return verify_and_write(filepath, content, "json", **kwargs)


def verify_and_write_python(filepath: str, content: str, **kwargs) -> Dict:
    """验证 Python 语法后写入。"""
    return verify_and_write(filepath, content, "python", **kwargs)


def verify_and_write_markdown(filepath: str, content: str, **kwargs) -> Dict:
    """验证 Markdown 格式后写入。"""
    return verify_and_write(filepath, content, "markdown", **kwargs)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="write_verifier.py — 文件格式验证器（AEGIS-Patch 防线补全）"
    )
    parser.add_argument("filepath", help="目标文件路径")
    parser.add_argument("--type", "-t", dest="file_type",
                        choices=list(VERIFIERS.keys()),
                        help="文件类型（默认从扩展名推断）")
    parser.add_argument("--validate-only", action="store_true",
                        help="仅验证不写入（从 stdin 读取内容）")
    parser.add_argument("--json-output", action="store_true",
                        help="JSON 格式输出")

    args = parser.parse_args()

    if args.validate_only:
        content = sys.stdin.read()
        result = verify_content(content, args.file_type or "auto")
        if args.json_output:
            print(json.dumps(result, ensure_ascii=False))
        else:
            if result["valid"]:
                print(f"✅ 验证通过")
            else:
                print(f"❌ {result['error']}")
            sys.exit(0 if result["valid"] else 1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
