#!/usr/bin/env python3
"""
Enhanced Memory — 集中常量注册表 (Single Source of Truth)
==========================================================
C' 方案 Phase 2 (C2) 产出。

所有 enhanced-memory 脚本共享的常量统一定义在此，
其他脚本通过 `from constants import X` 引用，消除硬编码分散问题。

命名约定:
    - 容量限制: *_SOFT_LIMIT / *_HARD_LIMIT
    - 阈值: *_THRESHOLD / MIN_* / MAX_*
    - 路径: *_DIR / *_FILE / *_PATTERN / *_ROOT
    - 超时/间隔: *_TIMEOUT / *_INTERVAL / *_HOURS / *_MINUTES

与 SKILL.md 同步: 本文件是 capacity/timeout/interval 等常量的权威来源。
SKILL.md 中对应值须标注 "与 constants.py 同步"。

最后更新: 2026-05-20 (Phase 3: 复合评分阈值增强 + Task #3)
"""

from pathlib import Path
import re

# ═══════════════════════════════════════════════════════════════
# 1. 容量限制 (Capacity Limits)
#    控制 MEMORY.md 和 USER.md 的字符数上限
#    与 SKILL.md 同步
# ═══════════════════════════════════════════════════════════════

MEMORY_SOFT_LIMIT = 3500   # 触发压缩的建议阈值
MEMORY_HARD_LIMIT = 3500   # 强制压缩的硬性上限
USER_SOFT_LIMIT = 2500     # USER.md 建议上限
USER_HARD_LIMIT = 3000     # USER.md 硬性上限

# ═══════════════════════════════════════════════════════════════
# 2. 路径常量 (Path Constants)
#══════════════════════════════════════════════════════════════

# 工作区根目录
WORKBUDDY_ROOT = Path.home() / "WorkBuddy"

# WorkBuddy 配置目录 (Git 仓库)
WORKBUDDY_DIR = MEMORIA_HOME  # config.MEMORIA_HOME  # ← TO_MIGRATE: use config.MEMORIA_HOME

# 脚本目录
SCRIPTS_DIR = WORKBUDDY_DIR / "skills" / "enhanced-memory" / "scripts"

# Skills 目录
SKILLS_DIR = WORKBUDDY_DIR / "skills"

# 共享记忆目录 (跨工作区共享)
SHARED_DIR = WORKBUDDY_DIR / "shared_memory"

# 日志目录
LOG_DIR = WORKBUDDY_DIR / "logs"

# 守护心跳目录
HEARTBEAT_DIR = WORKBUDDY_DIR / "health" / "heartbeats"

# ═══════════════════════════════════════════════════════════════
# 3. 文件名/模式常量 (File & Pattern Constants)
#══════════════════════════════════════════════════════════════

MEMORY_DIR = ".workbuddy/memory"              # 工作区记忆子目录 (相对路径)
MEMORY_FILE = "MEMORY.md"                     # L2 长期记忆文件
USER_FILE = "USER.md"                         # 用户画像文件
SOUL_FILE = "SOUL.md"                         # Agent 灵魂文件
IDENTITY_FILE = "IDENTITY.md"                 # Agent 身份文件
SESSION_FILE = ".session_state.json"          # L0 即时记忆状态
NUDGE_STATE_FILE = ".nudge_state.json"        # Nudge 计数器状态
ROUTE_LOG_FILE = "route_log.json"             # Agent 路由日志
HERMES_STATE_FILE = ".hermes-upstream-state"  # Hermes 上游同步状态

DAILY_LOG_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")  # YYYY-MM-DD.md

GLOBAL_STATE_PATH = WORKBUDDY_DIR / NUDGE_STATE_FILE
DB_PATH = WORKBUDDY_DIR / "user_model.db"             # user_model.db (意图学习等)
WORKBUDDY_DB_PATH = WORKBUDDY_DIR / "workbuddy.db"    # WorkBuddy 主数据库 (sessions/automations)
ROUTE_LOG_PATH = SHARED_DIR / ROUTE_LOG_FILE

# ═══════════════════════════════════════════════════════════════
# 4. Nudge 协议常量
#══════════════════════════════════════════════════════════════

DEFAULT_NUDGE_INTERVAL = 10  # 默认每 N 次工具调用触发一次审查
MIN_NUDGE_INTERVAL = 5       # 最小间隔（防止过于频繁）

# ═══════════════════════════════════════════════════════════════
# 5. 会话与超时常量
#══════════════════════════════════════════════════════════════

SESSION_TIMEOUT_HOURS = 24   # 会话超时（小时），超时后自动归档
GRACE_MINUTES = 5            # 守护心跳宽限期（分钟）

# ═══════════════════════════════════════════════════════════════
# 6. 质量评分阈值
#══════════════════════════════════════════════════════════════

QUALITY_P0_THRESHOLD = 10    # 核心知识 — 永久保留
QUALITY_P1_THRESHOLD = 5     # 有价值 — 可合并

# ═══════════════════════════════════════════════════════════════
# 7. 序列/模式检测常量
#══════════════════════════════════════════════════════════════

MIN_SEQUENCE_LENGTH = 2      # 最短工具调用序列
MIN_PATTERN_FREQ = 3         # 跨会话最低频率

# ═══════════════════════════════════════════════════════════════
# 8. Skill 自动检测常量（复合评分系统）
#    复合评分 = 加权求和 (工具调用数 × w_tools + 文件操作数 × w_files
#               + 错误恢复 × w_recovery + 相似度 × w_similarity)
#    与 SKILL.md 同步: 2026-05-20 Phase 3 阈值增强
#══════════════════════════════════════════════════════════════

# ── 8a. 单维度触发阈值（各维度独立最低门槛）──
MIN_REPEATED_TOOLS = 3       # [保留] 同一工具使用 ≥ 3 次触发检测
MIN_SESSION_OCCURRENCES = 2  # [保留] 模式出现 ≥ 2 次会话
MIN_COMPLEXITY_SCORE = 3     # [保留] 工具调用 ≥ 3 视为复杂任务

# ── 8b. 复合评分各维度硬阈值（任一不达标即跳过）──
MIN_COMPLEX_TOOL_CALLS = 8       # 单会话总工具调用 ≥ 8 次
MIN_FILE_OPERATIONS = 5          # 单会话文件操作 (Read/Write/Edit/Glob/Bash) ≥ 5 次
MIN_ERROR_RECOVERY = 1           # 单会话错误恢复 ≥ 1 次（retry/fallback）
MIN_PATTERN_SIMILARITY = 0.7     # 跨会话模式相似度 ≥ 0.7（0-1 余弦相似度）

# ── 8c. 复合评分权重（加权求和）──
WEIGHT_TOOL_CALLS = 0.35         # 工具调用数的权重
WEIGHT_FILE_OPERATIONS = 0.30    # 文件操作数的权重
WEIGHT_ERROR_RECOVERY = 0.15     # 错误恢复的权重
WEIGHT_SIMILARITY = 0.20         # 模式相似度的权重

# ── 8d. 复合评分触发阈值 ──
COMPOSITE_SCORE_THRESHOLD = 0.60 # 加权综合分 ≥ 0.60 触发 Skill 创建建议
AUTO_INSTALL_THRESHOLD = 0.75    # 加权综合分 ≥ 0.75 可自动安装（需用户确认）

# ── 8e. 归一化参考值（用于将原始计数映射到 0-1）──
NORM_TOOL_CALLS = 20             # 20 次工具调用 → score=1.0
NORM_FILE_OPERATIONS = 12        # 12 次文件操作 → score=1.0
NORM_ERROR_RECOVERY = 3          # 3 次错误恢复 → score=1.0

# ═══════════════════════════════════════════════════════════════
# 9. Agent 路由常量
#══════════════════════════════════════════════════════════════

DEFAULT_ROUTE_THRESHOLD = 0.3  # 路由相似度阈值

# ═══════════════════════════════════════════════════════════════
# 10. 守护健康常量
#══════════════════════════════════════════════════════════════

HEARTBEAT_FILE_MEMORY = HEARTBEAT_DIR / "memory-nudge.json"
LOG_PATH_MEMORY = LOG_DIR / "memory-daemon.log"
ERR_PATH_MEMORY = LOG_DIR / "memory-daemon.err"


# ═══════════════════════════════════════════════════════════════
# 辅助函数
#══════════════════════════════════════════════════════════════

def workspace_memory_dir(workspace_path: str | Path) -> Path:
    """给定工作区路径，返回其 .workbuddy/memory 目录的绝对路径。"""
    return Path(workspace_path) / MEMORY_DIR


def workspace_memory_file(workspace_path: str | Path) -> Path:
    """给定工作区路径，返回其 MEMORY.md 的绝对路径。"""
    return workspace_memory_dir(workspace_path) / MEMORY_FILE


def workspace_user_file(workspace_path: str | Path) -> Path:
    """给定工作区路径，返回其 USER.md 的绝对路径。"""
    return workspace_memory_dir(workspace_path) / USER_FILE


def workspace_session_file(workspace_path: str | Path) -> Path:
    """给定工作区路径，返回其 .session_state.json 的绝对路径。"""
    return workspace_memory_dir(workspace_path) / SESSION_FILE


# ═══════════════════════════════════════════════════════════════
# 自检：导入时打印加载的常量数量（仅 debug 模式下可见）
#══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import json
    constants = {k: str(v) for k, v in globals().items()
                 if k.isupper() and not k.startswith("_")}
    print(f"constants.py loaded: {len(constants)} constants across 10 categories")
    print(json.dumps(constants, indent=2, ensure_ascii=False))
