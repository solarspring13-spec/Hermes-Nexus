#!/usr/bin/env python3
"""
security_scan.py — Hermes-Nexus 三级防线脱敏扫描引擎
=====================================================

Design: Step 1 设计图纸 §壹 — 三级防线策略
- 防线一：正则模式矩阵（7 类 × ~25 个正则）
- 防线二：AST 白名单提取（仅 .py 文件 — 提取所有字符串常量再过正则）
- 防线三：文件级黑名单（不可绕过）

Core API:
    scan_and_redact(file_content, file_path) -> (redacted_content, list_of_findings)
    scan_file(file_path) -> ScanResult
    scan_directory(directory_path) -> list[ScanResult]

Severity levels:
    BLOCKER  — 文件级黑名单命中 / API Key 明文 → 拒绝同步，不可绕过
    CRITICAL — Bot Token / 数据库连接串 / PII → 拒绝同步
    WARNING  — 个人路径 / 用户名 → 自动替换，记录日志
    INFO     — 可疑但不明确 → 标记，不阻止

Usage:
    python3 security_scan.py --file path/to/script.py
    python3 security_scan.py --dir path/to/directory/
    python3 security_scan.py --file path/to/script.py --json
"""

import argparse
import ast
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set


# ═══════════════════════════════════════════════════════════════
# Data Structures
# ═══════════════════════════════════════════════════════════════

@dataclass
class Finding:
    """A single security finding during scan."""
    severity: str          # BLOCKER | CRITICAL | WARNING | INFO
    category: str          # api_key | bot_token | personal_path | config_file | db_conn | pii | internal_url
    pattern_id: str        # e.g., "1.1", "3.1", "4.1"
    line: int              # 1-based line number (0 if file-level)
    match: str             # The matched text (truncated to 80 chars for display)
    replacement: str = ""  # What to replace with (empty = no auto-replace)
    context: str = ""      # Surrounding context for human review


@dataclass
class ScanResult:
    """Result of scanning a single file."""
    file_path: str
    status: str            # blocked | warned | clean
    findings: List[Finding] = field(default_factory=list)
    redacted_content: str = ""  # Content after replacements (WARNING level only)
    error: str = ""             # Parse error, if any


# ═══════════════════════════════════════════════════════════════
# 防线三：文件级黑名单（优先级最高 — 先于内容扫描执行）
# ═══════════════════════════════════════════════════════════════

FILE_BLACKLIST_PATTERNS = [
    (re.compile(r'(^|/)settings\.json$', re.IGNORECASE), "settings.json — 含飞书/微信/元宝 Bot Token"),
    (re.compile(r'(^|/)models\.json$', re.IGNORECASE), "models.json — 含 DeepSeek/DashScope API Key"),
    (re.compile(r'(^|/)SOUL\.md$', re.IGNORECASE), "SOUL.md — Agent 灵魂定义"),
    (re.compile(r'(^|/)IDENTITY\.md$', re.IGNORECASE), "IDENTITY.md — Agent 身份"),
    (re.compile(r'(^|/)USER\.md$', re.IGNORECASE), "USER.md — 用户画像与投资判断"),
    (re.compile(r'(^|/)MEMORY\.md$', re.IGNORECASE), "MEMORY.md — 长期记忆"),
    (re.compile(r'\.env$', re.IGNORECASE), ".env — 环境变量（含真实 Key）"),
    (re.compile(r'\.db$', re.IGNORECASE), "*.db — SQLite 数据库"),
    (re.compile(r'\.sqlite$', re.IGNORECASE), "*.sqlite — SQLite 数据库"),
    (re.compile(r'\.pem$', re.IGNORECASE), "*.pem — 私钥/证书"),
    (re.compile(r'\.key$', re.IGNORECASE), "*.key — 私钥文件"),
    (re.compile(r'\.p12$', re.IGNORECASE), "*.p12 — Java 密钥库"),
    (re.compile(r'\.jks$', re.IGNORECASE), "*.jks — Java 密钥库"),
    (re.compile(r'\.plist$', re.IGNORECASE), "*.plist — macOS LaunchAgent"),
    (re.compile(r'(^|/)\.DS_Store$'), ".DS_Store — macOS 系统文件"),
    (re.compile(r'\.pyc$'), "*.pyc — Python 编译缓存"),
    (re.compile(r'__pycache__/'), "__pycache__/ — Python 缓存目录"),
]

FILE_BLACKLIST_NAMES = {
    "settings.json", "models.json", "SOUL.md", "IDENTITY.md",
    "USER.md", "MEMORY.md", ".env",
}


def check_file_blacklist(file_path: str) -> Optional[Finding]:
    """防线三：检查文件是否在黑名单中。命中 → BLOCKER，不可绕过。"""
    basename = os.path.basename(file_path)
    # 精确文件名匹配
    if basename in FILE_BLACKLIST_NAMES:
        return Finding(
            severity="BLOCKER",
            category="config_file",
            pattern_id="3.filename",
            line=0,
            match=f"文件名: {basename}",
            context=FILE_BLACKLIST_NAMES.get(basename, "")
        )
    # 正则模式匹配（扩展名）
    for pattern, reason in FILE_BLACKLIST_PATTERNS:
        if pattern.search(basename) or pattern.search(file_path):
            return Finding(
                severity="BLOCKER",
                category="config_file",
                pattern_id="3.pattern",
                line=0,
                match=f"匹配: {pattern.pattern}",
                context=reason
            )
    return None


# ═══════════════════════════════════════════════════════════════
# 防线一：正则模式矩阵
# ═══════════════════════════════════════════════════════════════

# Each pattern: (re.compile(...), category, pattern_id, severity, replacement_func_or_str)
# replacement is either a literal string or a callable that takes the match object

REGEX_PATTERNS = [
    # ─── 第 1 类：LLM API Key ───
    # 1.2 Anthropic Key（先匹配，避免被 1.1 误抓）
    (
        re.compile(r'sk-ant-[a-zA-Z0-9]{20,}'),
        "api_key", "1.2", "BLOCKER",
        "sk-ant-<REDACTED_ANTHROPIC_KEY>"
    ),
    # 1.1 OpenAI / DeepSeek / DashScope / 百炼标准 Key
    (
        re.compile(r'sk-[a-zA-Z0-9]{20,}'),
        "api_key", "1.1", "BLOCKER",
        "sk-<REDACTED_KEY>"
    ),
    # 1.3 DashScope 环境变量赋值
    (
        re.compile(r'DASHSCOPE_API_KEY\s*[:=]\s*[\'"]?[a-zA-Z0-9_-]{16,}[\'"]?', re.IGNORECASE),
        "api_key", "1.3", "BLOCKER",
        "DASHSCOPE_API_KEY=<REDACTED>"
    ),
    # 1.4 OpenAI 环境变量赋值
    (
        re.compile(r'OPENAI_API_KEY\s*[:=]\s*[\'"]?sk-[a-zA-Z0-9]{20,}[\'"]?', re.IGNORECASE),
        "api_key", "1.4", "BLOCKER",
        "OPENAI_API_KEY=sk-<REDACTED>"
    ),
    # 1.5 泛用 API Key 模式
    (
        re.compile(r'api[_-]?key\s*[:=]\s*[\'"]?[a-zA-Z0-9_-]{20,}[\'"]?', re.IGNORECASE),
        "api_key", "1.5", "BLOCKER",
        "api_key=<REDACTED>"
    ),

    # ─── 第 2 类：Bot / Channel Token ───
    # 2.5 Telegram Bot Token（数字:串格式 — 先匹配，防止被 2.4 误抓）
    (
        re.compile(r'\d{8,10}:[a-zA-Z0-9_-]{30,}'),
        "bot_token", "2.5", "CRITICAL",
        "<REDACTED_TELEGRAM_TOKEN>"
    ),
    # 2.4 Telegram Bot Token（t- 前缀）
    (
        re.compile(r't-[a-zA-Z0-9]{30,}'),
        "bot_token", "2.4", "CRITICAL",
        "t-<REDACTED>"
    ),
    # 2.1 飞书/企业微信/元宝 Bot Token
    (
        re.compile(r'(Bot|bot|BOT)\s*Token\s*[:=]\s*[\'"]?[a-zA-Z0-9_-]{16,}[\'"]?'),
        "bot_token", "2.1", "CRITICAL",
        "<BOT_TOKEN_REDACTED>"
    ),
    # 2.2 App Secret
    (
        re.compile(r'(App|app|APP)\s*Secret\s*[:=]\s*[\'"]?[a-zA-Z0-9_-]{16,}[\'"]?'),
        "bot_token", "2.2", "CRITICAL",
        "<APP_SECRET_REDACTED>"
    ),
    # 2.3 Webhook URL 含 Key
    (
        re.compile(r'(webhook|WEBHOOK)[_-]?url\s*[:=]\s*[\'"]?(https?://[^\'"\s]*key=[a-zA-Z0-9_-]{16,})[\'"]?', re.IGNORECASE),
        "bot_token", "2.3", "CRITICAL",
        lambda m: m.group(0).replace(m.group(2), m.group(2).split('key=')[0] + 'key=<REDACTED>')
    ),

    # ─── 第 5 类：数据库连接串 ───
    # 5.1 数据库连接字符串
    (
        re.compile(r'(mysql|postgres|mongodb|redis)://[^@]+@[^\s\'"]+'),
        "db_conn", "5.1", "CRITICAL",
        "<REDACTED_DB_URL>"
    ),
    # 5.2 SQLite 数据库路径
    (
        re.compile(r'sqlite[^=]*\.db[\'"]'),
        "db_conn", "5.2", "WARNING",
        "{MEMORIA_HOME}/data/<REDACTED>.db"
    ),

    # ─── 第 3 类：个人身份路径 ───
    # 3.1 macOS Home 目录
    (
        re.compile(r'/Users/siriuscyber[^\'"\s]*'),
        "personal_path", "3.1", "WARNING",
        "{MEMORIA_HOME}"
    ),
    # 3.2 WorkBuddy 目录路径
    (
        re.compile(r'~/WorkBuddy[^\'"\s]*'),
        "personal_path", "3.2", "WARNING",
        "{WORKSPACES_ROOT}"
    ),
    # 3.3 WorkBuddy 配置目录
    (
        re.compile(r'~/.workbuddy[^\'"\s]*'),
        "personal_path", "3.3", "WARNING",
        "{MEMORIA_HOME}"
    ),
    # 3.4 GitHub 用户名
    (
        re.compile(r'siriuscyber'),
        "personal_path", "3.4", "INFO",
        "{GITHUB_USER}"
    ),

    # ─── 第 6 类：邮箱与 PII ───
    # 6.1 邮箱地址
    (
        re.compile(r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b'),
        "pii", "6.1", "WARNING",
        "<REDACTED_EMAIL>"
    ),

    # ─── 第 7 类：内部服务地址 ───
    # 7.1 localhost 含 token
    (
        re.compile(r'https?://(localhost|127\.0\.0\.1|0\.0\.0\.0):\d{4,5}[^\'"\s]*token=[^\'"&\s]+', re.IGNORECASE),
        "internal_url", "7.1", "WARNING",
        lambda m: re.sub(r'token=[^\'"&\s]+', 'token=<REDACTED>', m.group(0))
    ),
    # 7.2 内部域名
    (
        re.compile(r'https?://[^\'"\s]*\.internal[^\'"\s]*', re.IGNORECASE),
        "internal_url", "7.2", "INFO",
        "<REDACTED_ENDPOINT>"
    ),
]


# ─── 假阳性排除规则 ───

# Contexts where email is expected and should NOT be flagged
EMAIL_SAFE_FILES = {"CONTRIBUTING.md", "CODEOWNERS", "pyproject.toml"}

# Patterns that, if on the same line as a match, indicate a false positive
FALSE_POSITIVE_INDICATORS = [
    re.compile(r'^\s*(import|from)\s+'),   # Import lines
    re.compile(r'^\s*(class|def)\s+'),      # Class/function definitions
]


def is_false_positive(file_path: str, pattern_id: str, line_text: str) -> bool:
    """Check if a regex match is likely a false positive."""
    basename = os.path.basename(file_path)

    # 邮箱在安全文件中豁免
    if pattern_id == "6.1" and basename in EMAIL_SAFE_FILES:
        return True

    # 3.4 (siriuscyber) 在 .git/config 或 package.json 中可能是合法的
    if pattern_id == "3.4" and basename in (".gitconfig", "package.json", "pyproject.toml"):
        return True

    # API Key 模式出现在 import/class/def 行 → 检查上下文
    if pattern_id in ("1.1", "1.5"):
        for indicator in FALSE_POSITIVE_INDICATORS:
            if indicator.search(line_text):
                return True

    return False


# ═══════════════════════════════════════════════════════════════
# 防线二：AST 白名单扫描
# ═══════════════════════════════════════════════════════════════

class StringConstantVisitor(ast.NodeVisitor):
    """
    AST visitor that extracts ALL string constants from a Python file.
    
    Design rationale (from Step 1 蓝图 §1.2):
    - ast.parse() extracts strings exactly as they appear in source code,
      bypassing regex's inability to distinguish string literals from comments,
      variable names, or f-string interpolations.
    - Even `Path.home() / ".workbuddy"` — the string ".workbuddy" is extracted
      as an independent Constant node, which we then re-feed through the regex matrix.
    - This catches strings that regex alone might miss (e.g., strings assembled
      via os.path.join, or deeply nested in dicts/lists).
    """

    def __init__(self):
        self.strings: List[Tuple[str, int]] = []  # (string_value, line_number)

    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, str) and len(node.value) > 3:
            self.strings.append((node.value, node.lineno))
        self.generic_visit(node)

    # Also handle JoinedStr (f-strings) by visiting their sub-nodes
    def visit_JoinedStr(self, node: ast.JoinedStr):
        # Extract the static parts of f-strings
        parts: List[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
        if parts:
            combined = "".join(parts)
            if len(combined) > 3:
                self.strings.append((combined, node.lineno))
        self.generic_visit(node)


def extract_strings_via_ast(source_code: str) -> List[Tuple[str, int]]:
    """
    Use AST to extract all string constants from Python source.
    Returns list of (string_value, line_number).
    
    This is the core of 防线二 (Defense Line 2).
    """
    try:
        tree = ast.parse(source_code)
        visitor = StringConstantVisitor()
        visitor.visit(tree)
        return visitor.strings
    except SyntaxError:
        # If the file has syntax errors, skip AST analysis — fall through to regex only
        return []


# ═══════════════════════════════════════════════════════════════
# Core Scanning Engine
# ═══════════════════════════════════════════════════════════════

def _apply_replacements(content: str, findings: List[Finding]) -> str:
    """Apply WARNING-level replacements to content. Sorted by position (reverse) to avoid offset drift."""
    # Only apply WARNING-level findings that have replacements
    replacements = [
        (f.match, f.replacement)
        for f in findings
        if f.severity == "WARNING" and f.replacement and f.match and not callable(f.replacement)
    ]
    if not replacements:
        return content

    result = content
    # Sort by length of match descending — replace longer matches first to avoid partial replacements
    for match_str, repl_str in sorted(replacements, key=lambda x: len(x[0]), reverse=True):
        result = result.replace(match_str, repl_str)
    return result


def scan_and_redact(file_content: str, file_path: str) -> Tuple[str, List[Finding]]:
    """
    Core API — scan file content and produce redacted version with findings.
    
    Args:
        file_content: Raw file content as string
        file_path: Path to the file (used for blacklist check and context)
    
    Returns:
        (redacted_content, list_of_findings)
        - redacted_content: Content with WARNING-level replacements applied
        - list_of_findings: All findings (BLOCKER, CRITICAL, WARNING, INFO)
    
    The three defense lines execute in order:
      1. File blacklist check (BLOCKER if hit)
      2. AST string extraction (Python files only)
      3. Regex pattern matrix scan (on all extracted strings + full content)
    """
    findings: List[Finding] = []
    lines = file_content.split('\n')

    # ═══════════════════════════════════════════════════════
    # 防线三：文件黑名单
    # ═══════════════════════════════════════════════════════
    blacklist_finding = check_file_blacklist(file_path)
    if blacklist_finding:
        findings.append(blacklist_finding)
        return file_content, findings  # BLOCKER — immediate return, no content scan

    # ═══════════════════════════════════════════════════════
    # 防线二：AST 字符串常量提取（仅 .py 文件）
    # ═══════════════════════════════════════════════════════
    ast_strings: List[Tuple[str, int]] = []
    if file_path.endswith('.py'):
        ast_strings = extract_strings_via_ast(file_content)

    # ═══════════════════════════════════════════════════════
    # 防线一：正则模式矩阵扫描
    # ═══════════════════════════════════════════════════════
    seen_matches: Set[Tuple[str, int, str]] = set()  # dedup: (line, pattern_id, match)

    def _record_finding(pattern, full_match, line_num, severity, category, pid, replacement, line_text):
        """Record a finding, with deduplication and false-positive check."""
        key = (line_num, pid, full_match[:60])
        if key in seen_matches:
            return
        seen_matches.add(key)

        # False positive check
        if is_false_positive(file_path, pid, line_text):
            return

        # Resolve replacement if callable
        repl = replacement
        if callable(replacement):
            try:
                repl = replacement(pattern)
            except Exception:
                repl = str(full_match)

        findings.append(Finding(
            severity=severity,
            category=category,
            pattern_id=pid,
            line=line_num,
            match=full_match[:80],  # Truncate for display safety
            replacement=str(repl) if repl != full_match else "",
            context=line_text.strip()[:120] if line_text else ""
        ))

    # Phase A: Scan AST-extracted strings first (more precise, less false positives)
    for string_val, line_num in ast_strings:
        for pattern, category, pid, severity, replacement in REGEX_PATTERNS:
            for m in pattern.finditer(string_val):
                if line_num > 0 and line_num <= len(lines):
                    line_text = lines[line_num - 1]
                else:
                    line_text = ""
                _record_finding(pattern, m.group(0), line_num, severity, category, pid, replacement, line_text)

    # Phase B: Scan full content line by line (catch non-string contexts)
    for i, line_text in enumerate(lines, 1):
        for pattern, category, pid, severity, replacement in REGEX_PATTERNS:
            for m in pattern.finditer(line_text):
                full_match = m.group(0)
                _record_finding(pattern, full_match, i, severity, category, pid, replacement, line_text)

    # ═══════════════════════════════════════════════════════
    # Apply WARNING-level replacements
    # ═══════════════════════════════════════════════════════
    redacted = _apply_replacements(file_content, findings)

    return redacted, findings


def scan_file(file_path: str) -> ScanResult:
    """Scan a single file and return a ScanResult."""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
    except Exception as e:
        return ScanResult(
            file_path=file_path,
            status="blocked",
            error=str(e)
        )

    redacted, findings = scan_and_redact(content, file_path)

    # Determine status
    severities = {f.severity for f in findings}
    if "BLOCKER" in severities:
        status = "blocked"
    elif "CRITICAL" in severities:
        status = "blocked"
    elif findings:
        status = "warned"
    else:
        status = "clean"

    return ScanResult(
        file_path=file_path,
        status=status,
        findings=findings,
        redacted_content=redacted,
    )


def scan_directory(directory_path: str) -> List[ScanResult]:
    """Recursively scan all files in a directory."""
    results: List[ScanResult] = []
    root = Path(directory_path)

    # Directories to skip
    skip_dirs = {'.git', '__pycache__', '.venv', 'venv', 'node_modules', '.DS_Store'}

    for entry in root.rglob('*'):
        if entry.is_file():
            # Skip files in blacklisted directories
            parts = set(entry.parts)
            if parts & skip_dirs:
                continue
            # Skip binary-looking files
            suffix = entry.suffix.lower()
            if suffix in ('.pyc', '.pyo', '.so', '.dylib', '.dll', '.bin', '.exe', '.o', '.a'):
                continue
            results.append(scan_file(str(entry)))

    return results


# ═══════════════════════════════════════════════════════════════
# Reporting
# ═══════════════════════════════════════════════════════════════

def format_report(results: List[ScanResult]) -> str:
    """Format scan results as a human-readable report."""
    lines = []
    lines.append("=" * 72)
    lines.append("  🔒 Hermes-Nexus Security Scan Report")
    lines.append("=" * 72)

    blocked = [r for r in results if r.status == "blocked"]
    warned = [r for r in results if r.status == "warned"]
    clean = [r for r in results if r.status == "clean"]

    lines.append(f"\n  Files scanned: {len(results)}")
    lines.append(f"  ❌ BLOCKED: {len(blocked)}")
    lines.append(f"  ⚠️  WARNINGS: {len(warned)}")
    lines.append(f"  ✅ CLEAN: {len(clean)}")

    if blocked:
        lines.append(f"\n  {'─' * 68}")
        lines.append(f"  ❌ BLOCKER / CRITICAL — 以下文件拒绝同步")
        lines.append(f"  {'─' * 68}")
        for r in blocked:
            if r.error:
                lines.append(f"\n  📄 {r.file_path}")
                lines.append(f"     Error: {r.error}")
                continue
            lines.append(f"\n  📄 {r.file_path}")
            for f in r.findings:
                icon = "🛑" if f.severity == "BLOCKER" else "🔴"
                loc = f":{f.line}" if f.line > 0 else ""
                lines.append(f"     {icon} [{f.severity}] [{f.pattern_id}]{loc}")
                lines.append(f"        Match: {f.match}")
                if f.context:
                    lines.append(f"        Context: {f.context}")

    if warned:
        lines.append(f"\n  {'─' * 68}")
        lines.append(f"  ⚠️  WARNING — 以下内容已被自动替换")
        lines.append(f"  {'─' * 68}")
        for r in warned:
            lines.append(f"\n  📄 {r.file_path}")
            for f in r.findings:
                if f.severity == "WARNING":
                    loc = f":{f.line}" if f.line > 0 else ""
                    lines.append(f"     ⚠️  [{f.pattern_id}]{loc}")
                    lines.append(f"        {f.match} → {f.replacement}")

    if not blocked and not warned:
        lines.append(f"\n  ✅ 所有文件通过安全扫描，未发现敏感信息。")

    lines.append(f"\n{'=' * 72}")
    return "\n".join(lines)


def format_json(results: List[ScanResult]) -> str:
    """Format scan results as JSON."""
    output = []
    for r in results:
        output.append({
            "file_path": r.file_path,
            "status": r.status,
            "error": r.error,
            "findings": [asdict(f) for f in r.findings],
        })
    summary = {
        "total": len(results),
        "blocked": sum(1 for r in results if r.status == "blocked"),
        "warned": sum(1 for r in results if r.status == "warned"),
        "clean": sum(1 for r in results if r.status == "clean"),
    }
    return json.dumps({"summary": summary, "results": output}, indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════
# CLI Interface
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Hermes-Nexus Security Scan Engine — 三级防线脱敏扫描"
    )
    parser.add_argument("--file", type=str, help="Scan a single file")
    parser.add_argument("--dir", type=str, help="Scan a directory recursively")
    parser.add_argument("--json", action="store_true", help="Output in JSON format")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show all findings including INFO")
    args = parser.parse_args()

    if not args.file and not args.dir:
        parser.print_help()
        sys.exit(1)

    if args.file:
        result = scan_file(args.file)
        results = [result]
    else:
        results = scan_directory(args.dir)

    # Filter out INFO if not verbose
    if not args.verbose:
        for r in results:
            r.findings = [f for f in r.findings if f.severity != "INFO"]

    if args.json:
        print(format_json(results))
    else:
        print(format_report(results))

    # Exit code: non-zero if any blockers
    has_blockers = any(r.status == "blocked" for r in results)
    sys.exit(1 if has_blockers else 0)


if __name__ == "__main__":
    main()
