# 🌌 Hermes-Nexus: Step 1 — 脱敏同步引擎设计图纸

> **Architect's Blueprint** — 仅设计，不编码  
> **待 CTO 审批后开始实现**  
> **版本**: v1.0-draft | **日期**: 2026-05-21 | **AEGIS MODE**

---

## 零、设计语境回顾

**CTO 决议**：
- 项目名：**Hermes-Nexus**，核心包：**Memoria Engine**（`memoria_engine`）
- 提取范围：核心铁三角（`enhanced-memory` + `hermes-cron` + `hermes-kanban`），冻结 `hermes-exec`
- 公开仓库标语（所有 README/白皮书头部必用）：
  > # 🌌 Hermes-Nexus: Memoria Engine for OpenClaw
  > *A Self-Evolving Memory & Workflow Architecture. Inspired by NousResearch Hermes Agent.*

**已知硬伤**（20/24 脚本依赖 `constants.py`，其中 12 个路径常量硬编码了 `~/.workbuddy/`）：
- `constants.py:41` — `WORKBUDDY_ROOT = Path.home() / "WorkBuddy"`
- `constants.py:44` — `WORKBUDDY_DIR = Path.home() / ".workbuddy"`
- `constants.py:47` — `SCRIPTS_DIR = WORKBUDDY_DIR / "skills" / "enhanced-memory" / "scripts"`
- `constants.py:50` — `SKILLS_DIR = WORKBUDDY_DIR / "skills"`
- `constants.py:53` — `SHARED_DIR = WORKBUDDY_DIR / "shared_memory"`
- `constants.py:56` — `LOG_DIR = WORKBUDDY_DIR / "logs"`
- `constants.py:59` — `HEARTBEAT_DIR = WORKBUDDY_DIR / "health" / "heartbeats"`
- `constants.py:78` — `DB_PATH = WORKBUDDY_DIR / "user_model.db"`
- `constants.py:79` — `WORKBUDDY_DB_PATH = WORKBUDDY_DIR / "workbuddy.db"`
- `hermes-kanban` SKILL.md — 硬编码 `~/.workbuddy/data/kanban.db`
- `hermes-kanban` SKILL.md — 硬编码 `~/Library/LaunchAgents/com.workbuddy.hermes-kanban.plist`

**20/24 脚本 import constants**（依赖链集中度 83%），意味着改造 `constants.py` → `config.py` 即可解决 83% 的硬编码问题。

---

## 壹、`security_scan.py` 脱敏正则清单

> **设计哲学**：正则不是银弹。我们采用「三级防线」策略——正则粗筛 → AST 白名单 → 人工最终确认。

### 1.1 防线一：正则模式矩阵（7 类）

#### 第 1 类：LLM API Key（最高优先级 — 泄露 = 账单被盗）

| # | 正则模式 | 匹配对象 | 替换策略 | 误报风险 |
|---|---------|---------|---------|---------|
| 1.1 | `sk-[a-zA-Z0-9]{20,}` | OpenAI / DeepSeek / DashScope / 百炼标准 Key | → `sk-<REDACTED>` | 🔴 中（纯代码中 `sk-` 前缀罕见但非零） |
| 1.2 | `sk-ant-[a-zA-Z0-9]{20,}` | Anthropic API Key | → `sk-ant-<REDACTED>` | 🟢 极低 |
| 1.3 | `DASHSCOPE_API_KEY\s*[:=]\s*['"]?[a-zA-Z0-9_-]{16,}` | 百炼 DashScope 环境变量赋值 | → `DASHSCOPE_API_KEY=<REDACTED>` | 🟢 极低 |
| 1.4 | `OPENAI_API_KEY\s*[:=]\s*['"]?sk-[a-zA-Z0-9]{20,}` | OpenAI 环境变量赋值 | → `OPENAI_API_KEY=sk-<REDACTED>` | 🟢 极低 |
| 1.5 | `api[_-]?key\s*[:=]\s*['"]?[a-zA-Z0-9_-]{20,}` | 泛用 API Key 模式（不区分大小写） | → `api_key=<REDACTED>` | 🔴 高（需排除明显非 Key 的值，如 base64 不含 `=` 的短串） |

**补充约束**：
- 1.1 及 1.5 需加**假阳性排除**：如果匹配字符串出现在 `.py` 文件中且上下文为 `import` / `class` / `def` 行 → 标记为 "需人工审查" 而非自动替换
- 1.1 的 `{20,}` 下限基于：最短 OpenAI Key 约 51 字符，DeepSeek Key 约 32 字符，但设为 20 是为了捕获异常短 Key 变体

#### 第 2 类：Bot / Channel Token（泄露 = 渠道接管）

| # | 正则模式 | 匹配对象 | 替换策略 | 误报风险 |
|---|---------|---------|---------|---------|
| 2.1 | `(Bot|bot|BOT)[A-Za-z0-9]*Token\s*[:=]\s*['"]?[a-zA-Z0-9_-]{16,}` | 飞书/企业微信/元宝 Bot Token | → `BotToken=<REDACTED>` | 🟢 低 |
| 2.2 | `(App|app|APP)Secret\s*[:=]\s*['"]?[a-zA-Z0-9_-]{16,}` | 飞书 App Secret / 微信 AppSecret | → `AppSecret=<REDACTED>` | 🟢 低 |
| 2.3 | `(webhook|WEBHOOK)[_-]?url\s*[:=]\s*['"]?https?://[^'"]*key=[a-zA-Z0-9_-]{16,}` | Webhook URL（含 Key 参数） | → URL 保留域名部分，Key 替换为 `<REDACTED>` | 🟢 低 |
| 2.4 | `t-[a-zA-Z0-9]{30,}` | Telegram Bot Token（特征：`数字:字母数字串`） | → `t-<REDACTED>` | 🟡 中极（30+ 纯字母数字串在代码中稀有但存在） |
| 2.5 | `\d{8,10}:[a-zA-Z0-9_-]{30,}` | Telegram Bot Token 数字:串格式 | → `<REDACTED>:<REDACTED>` | 🟢 极低 |

#### 第 3 类：个人身份路径（泄露 = 暴露用户名和目录结构）

| # | 正则模式 | 匹配对象 | 替换策略 | 误报风险 |
|---|---------|---------|---------|---------|
| 3.1 | `/Users/siriuscyber[^'"]*` | 你的 macOS Home 目录路径 | → `{MEMORIA_HOME}` | 🟢 极低（此路径在公开代码中不应出现） |
| 3.2 | `~/WorkBuddy[^'"]*` | WorkBuddy 目录路径 | → `{WORKSPACES_ROOT}` | 🟡 中（需区分文档说明性引用 vs 代码路径） |
| 3.3 | `~/.workbuddy[^'"]*` | WorkBuddy 配置目录 | → `{MEMORIA_HOME}` | 🟡 中 |
| 3.4 | `solarspring13` | 你的 GitHub 用户名 | → `{GITHUB_USER}` | 🟡 中（可能在注释/文档中引用，需人工审查） |
| 3.5 | `Path\.home\(\)` | 动态 Home 路径（本身不泄露，但配合后续路径可能泄露） | 不替换，但标记 + 人工审查 | 🟢 极低（在公开代码中合理使用） |

**关于 3.2/3.3 的辩证**：
- 如果路径出现在 **Python 代码字符串**中（如 `"~/.workbuddy"`）→ 必须替换
- 如果路径出现在 **Markdown 文档**的示例/说明中（如 QUICKSTART.md 的 `~/.workbuddy/skills/` 安装指南）→ 保留，但需确认上下文中没有真实凭证
- 判断逻辑：检查文件扩展名（`.py` → 严格替换；`.md` → 标记审查）

#### 第 4 类：个人配置文件（泄露 = 整体暴露）

| # | 正则模式 | 匹配对象 | 替换策略 | 误报风险 |
|---|---------|---------|---------|---------|
| 4.1 | 文件名匹配 `settings.json` / `models.json` / `SOUL.md` / `IDENTITY.md` / `USER.md` / `MEMORY.md` | 核心配置文件 | → **阻止同步**（直接拒绝，不替换） | 🟢 零（这些文件绝对不能出现在公开仓库） |
| 4.2 | `workbuddy\.db` / `session_index\.db` / `global_index\.db` / `user_model\.db` | 个人数据库 | → **阻止同步** | 🟢 零 |

#### 第 5 类：数据库连接串（泄露 = 数据访问）

| # | 正则模式 | 匹配对象 | 替换策略 | 误报风险 |
|---|---------|---------|---------|---------|
| 5.1 | `(mysql|postgres|mongodb|redis)://[^@]+@[^/\s'"]+` | 数据库连接字符串 | → `<REDACTED_DB_URL>` | 🟢 极低 |
| 5.2 | `sqlite.*\.db['"]` | SQLite 数据库路径 | → `{MEMORIA_HOME}/data/<REDACTED>.db` | 🟡 中（开源代码可能引用自身 DB） |

#### 第 6 类：邮箱与 PII

| # | 正则模式 | 匹配对象 | 替换策略 | 误报风险 |
|---|---------|---------|---------|---------|
| 6.1 | `[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}` | 邮箱地址 | → `<REDACTED_EMAIL>` | 🔴 高（开源项目常含 maintainer 邮箱） |
| 6.2 | 文件名匹配 `*.pem` / `*.key` / `*.p12` / `*.jks` / `.env`（非 `.env.example`） | 私钥/证书文件 | → **阻止同步** | 🟢 零 |

**第 6.1 的特殊处理**：
- 如果邮箱出现在 `CONTRIBUTING.md` / `CODEOWNERS` / `pyproject.toml` 的 `authors` 字段中 → 属于公开维护信息，标记跳过
- 如果邮箱出现在代码注释或字符串中 → 替换

#### 第 7 类：内部服务地址

| # | 正则模式 | 匹配对象 | 替换策略 | 误报风险 |
|---|---------|---------|---------|---------|
| 7.1 | `https?://(localhost|127\.0\.0\.1|0\.0\.0\.0):\d{4,5}[^'"]*` | 本地服务 URL（如果包含 token 或密钥参数） | 仅当含 token 参数时替换 | 🟡 中 |
| 7.2 | `https?://[^/'"]*\.internal[^'"]*` | 内部域名 | → `<REDACTED_ENDPOINT>` | 🟢 低 |

### 1.2 防线二：AST 白名单扫描（针对 Python 文件）

正则只能匹配字符串——但 Python 代码中的路径可能来自 `Path()` / `os.path.join()` / f-string 组合。为此，我们加入 AST 静态分析作为第二层防线：

**扫描策略**：
1. 对 `.py` 文件做 `ast.parse()`
2. 遍历所有 `ast.Constant` 节点（Python 3.8+），提取所有字符串常量
3. 将提取的字符串常量依次过防线一的 7 类正则
4. 发现任何匹配 → 记录文件名 + 行号 + 匹配类型

**优势**：不依赖正则去解析 Python 语法（`Path.home() / ".workbuddy"` 中的 `".workbuddy"` 会被 AST 准确提取为独立字符串常量），也不用担心 f-string 和拼接绕过。

### 1.3 防线三：文件级黑名单（不可绕过）

以下文件及扩展名**绝对不可同步**，无论内容如何：

```
黑名单（文件级）：
  settings.json       — 飞书/微信/元宝 Bot Token
  models.json         — DeepSeek/DashScope API Key
  SOUL.md             — Agent 灵魂（你的角色、偏好、协议）
  IDENTITY.md         — Agent 身份
  USER.md             — 用户画像 + 投资判断
  MEMORY.md           — 长期记忆
  .env                — 环境变量（包含真实 Key）
  *.db / *.sqlite     — 个人数据库
  *.pem / *.key       — 私钥/证书
  *.p12 / *.jks       — Java 密钥库
  *.plist             — macOS LaunchAgent（含硬编码路径）
  .DS_Store           — macOS 系统文件
  __pycache__/        — Python 缓存
  *.pyc               — 编译缓存
  .git/               — 嵌套 Git 仓库
```

### 1.4 审查结果分级

```python
# 每条发现的安全问题分级
Severity:
  BLOCKER  — 文件级黑名单命中 / API Key 明文 → 拒绝同步，不可绕过
  CRITICAL — Bot Token / 数据库连接串 / PII → 拒绝同步，需人工确认后放行
  WARNING  — 个人路径 / 用户名 → 自动替换，记录日志
  INFO     — 可疑但不明确 → 标记，不阻止
```

### 1.5 安全扫描流程图

```
[源文件] → 1. 文件黑名单检查（BLOCKER? → 拒绝）
              ↓ pass
          2. AST 字符串常量提取（仅 .py）
              ↓
          3. 正则模式矩阵扫描（7类 × 约25个正则）
              ↓
          4. 假阳性排除：
             - 邮箱在 CONTRIBUTING.md → 跳过
             - `Path.home()` 无后续路径拼接 → 放过
             - `import` 行中的 `sk-` → 标记审查
              ↓
          5. 分级报告：
             - BLOCKER/CRITICAL → 拒绝 + 详细位置
             - WARNING → 替换 + 日志
             - INFO → 标记
              ↓
          [输出: security_report.json + 脱敏后的内容]
```

---

## 贰、`sync.py` 目录映射字典

> **设计哲学**：源目录（`~/.workbuddy/skills/`）是「研发实验室」；目标目录（`hermes-nexus/memoria_engine/`）是「量产工厂」。映射不是简单复制——需要重组、重命名、去敏。

### 2.1 完整映射字典

```python
# sync.py — SYNC_MAP: 源路径 → 目标路径 + 转换规则
#
# 结构：
#   key:   相对于 ~/.workbuddy/skills/ 的源路径
#   value: { target, transform, rename, skip_patterns }
#
# 命名约定（memoria_engine 包内）：
#   - 去掉冗长的前缀，保留语义核心
#   - 模块间 import 路径同步更新（见 2.3）

SYNC_MAP = {
    # ═══════════════════════════════════════════════════════════
    # A. 引擎入口 & 配置
    # ═══════════════════════════════════════════════════════════

    "enhanced-memory/scripts/constants.py": {
        "target": "memoria_engine/constants.py",
        "transform": "rewrite_paths",   # 重写所有路径常量 → 使用 config.py
        "note": "仅保留纯常量（容量/阈值/权重），路径常量迁移到 config.py",
    },

    # ═══════════════════════════════════════════════════════════
    # B. 记忆子系统 (8 个脚本 → memoria_engine/memory/)
    # ═══════════════════════════════════════════════════════════

    "enhanced-memory/scripts/session_state.py": {
        "target": "memoria_engine/memory/session_state.py",
        "transform": "rewrite_imports",  # from constants → from ..constants
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
        "note": "imports memory_pool → from ..memory.memory_pool",
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

    # ═══════════════════════════════════════════════════════════
    # C. 语义子系统 (4 个脚本 → memoria_engine/semantic/)
    # ═══════════════════════════════════════════════════════════

    "enhanced-memory/scripts/embeddings.py": {
        "target": "memoria_engine/semantic/embeddings.py",
        "transform": "rewrite_imports",
        "note": "BGE-M3 嵌入模型 — 高级功能，需 pip install memoria-engine[semantic]",
    },
    "enhanced-memory/scripts/vector_memory_provider.py": {
        "target": "memoria_engine/semantic/vector_memory.py",
        "rename": True,  # FILE RENAME: vector_memory_provider → vector_memory
        "transform": "rewrite_imports",
    },
    "enhanced-memory/scripts/intent_learner.py": {
        "target": "memoria_engine/semantic/intent_learner.py",
        "transform": "rewrite_imports",
        "note": "imports embeddings, user_model, vector_memory_provider → 路径重写",
    },
    "enhanced-memory/scripts/intent_embedder.py": {
        "target": "memoria_engine/semantic/intent_embedder.py",
        "transform": "rewrite_imports",
    },

    # ═══════════════════════════════════════════════════════════
    # D. 调度子系统 (2 个脚本 → memoria_engine/cron/)
    # ═══════════════════════════════════════════════════════════

    "hermes-cron/scripts/cron_parser.py": {
        "target": "memoria_engine/cron/parser.py",
        "rename": True,  # FILE RENAME: cron_parser → parser
        "transform": "rewrite_imports",
    },
    "hermes-cron/scripts/cron_scheduler.py": {
        "target": "memoria_engine/cron/scheduler.py",
        "rename": True,  # FILE RENAME: cron_scheduler → scheduler
        "transform": "rewrite_imports",
    },

    # ═══════════════════════════════════════════════════════════
    # E. 看板子系统 (3 个脚本 → memoria_engine/kanban/)
    # ═══════════════════════════════════════════════════════════

    "hermes-kanban/scripts/kanban_db.py": {
        "target": "memoria_engine/kanban/db.py",
        "rename": True,    # FILE RENAME: kanban_db → db
        "transform": "rewrite_imports + rewrite_paths",
        "note": "硬编码路径 ~/.workbuddy/data/kanban.db → config.MEMORIA_HOME / 'data' / 'kanban.db'",
    },
    "hermes-kanban/scripts/kanban_worker.py": {
        "target": "memoria_engine/kanban/worker.py",
        "rename": True,    # FILE RENAME: kanban_worker → worker
        "transform": "rewrite_imports",
    },
    "hermes-kanban/scripts/kanban_scheduler.py": {
        "target": "memoria_engine/kanban/scheduler.py",
        "rename": True,    # FILE RENAME: kanban_scheduler → scheduler
        "transform": "rewrite_imports",
    },

    # ═══════════════════════════════════════════════════════════
    # F. 守护进程子系统 (3 个脚本 → memoria_engine/daemon/)
    # ═══════════════════════════════════════════════════════════

    "enhanced-memory/scripts/daemon_health.py": {
        "target": "memoria_engine/daemon/health.py",
        "rename": True,    # FILE RENAME: daemon_health → health
        "transform": "rewrite_imports + rewrite_paths",
        "note": "硬编码 HEARTBEAT_DIR/LOG_DIR → config 注入",
    },
    "enhanced-memory/scripts/memory_daemon.py": {
        "target": "memoria_engine/daemon/memory_daemon.py",
        "rename": True,    # FILE RENAME: memory_daemon → memory_daemon (保留原名以区分)
        "transform": "rewrite_imports + rewrite_paths",
    },
    "enhanced-memory/scripts/health_test_battery.py": {
        "target": "memoria_engine/daemon/health_test_battery.py",
        "rename": False,   # 保留原名
        "transform": "rewrite_imports + rewrite_paths",
    },

    # ═══════════════════════════════════════════════════════════
    # G. 技能子系统 (3 个脚本 → memoria_engine/skills/)
    # ═══════════════════════════════════════════════════════════

    "enhanced-memory/scripts/skill_detector.py": {
        "target": "memoria_engine/skills/detector.py",
        "rename": True,    # FILE RENAME: skill_detector → detector
        "transform": "rewrite_imports",
    },
    "enhanced-memory/scripts/skill_creator.py": {
        "target": "memoria_engine/skills/creator.py",
        "rename": True,    # FILE RENAME: skill_creator → creator
        "transform": "rewrite_imports",
    },
    "enhanced-memory/scripts/confidence_scorer.py": {
        "target": "memoria_engine/skills/confidence_scorer.py",
        "rename": False,   # 保留原名
        "transform": "rewrite_imports",
    },

    # ═══════════════════════════════════════════════════════════
    # H. 模型子系统 (2 个脚本 → memoria_engine/models/)
    # ═══════════════════════════════════════════════════════════

    "enhanced-memory/scripts/user_model.py": {
        "target": "memoria_engine/models/user_model.py",
        "transform": "rewrite_imports",
    },
    "enhanced-memory/scripts/sequence_analyzer.py": {
        "target": "memoria_engine/models/sequence_analyzer.py",
        "transform": "rewrite_imports",
    },

    # ═══════════════════════════════════════════════════════════
    # I. 工具子系统 (4 个脚本 → memoria_engine/utils/)
    # ═══════════════════════════════════════════════════════════

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
    "enhanced-memory/scripts/write_verifier.py": {
        "target": "memoria_engine/memory/write_verifier.py",
        "transform": "rewrite_imports",
        "note": "已有映射（B 节），此处不重复。若出现冲突以 B 节为准",
    },
}
```

### 2.2 映射统计

| 子系统 | 源文件数 | 目标目录 | 重命名文件 | 文件重命名清单 |
|--------|:------:|---------|:--------:|-------------|
| memory | 8 | `memoria_engine/memory/` | 0 | — |
| semantic | 4 | `memoria_engine/semantic/` | 1 | `vector_memory_provider.py` → `vector_memory.py` |
| cron | 2 | `memoria_engine/cron/` | 2 | `cron_parser.py` → `parser.py`; `cron_scheduler.py` → `scheduler.py` |
| kanban | 3 | `memoria_engine/kanban/` | 3 | `kanban_db.py` → `db.py`; `kanban_worker.py` → `worker.py`; `kanban_scheduler.py` → `scheduler.py` |
| daemon | 3 | `memoria_engine/daemon/` | 2 | `daemon_health.py` → `health.py`; `memory_daemon.py` 保留 |
| skills | 3 | `memoria_engine/skills/` | 2 | `skill_detector.py` → `detector.py`; `skill_creator.py` → `creator.py` |
| models | 2 | `memoria_engine/models/` | 0 | — |
| utils | 3 | `memoria_engine/utils/` | 0 | — |
| config | 1 | `memoria_engine/` | 0 | `constants.py` 保留（但内容重写） |
| **合计** | **29** | — | **10** | — |

> 注：`write_verifier.py` 在 B 节和 I 节都出现了映射。以 B 节 (`memoria_engine/memory/`) 为准——它本质上属于记忆写入验证，放在 memory 子模块更合理。

### 2.3 Import 路径重写规则

在 `memoria_engine` 包内部，原脚本中的 inter-module imports 需要重写：

```python
# ── 原 import → 新 import 映射表 ──
IMPORT_REWRITE = {
    # 自引用（包内相对导入）
    "from constants import":      "from ..constants import",       # 子模块 → 包根
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
```

**设计原则**：
- 所有内部 import 改为**显式相对导入**（`from ..memory.xxx`），不依赖 `sys.path` 技巧
- 外部依赖（`lancedb`, `numpy`, `pyarrow`, `sentence_transformers` 等）**不变**——它们通过 `pip install memoria-engine[semantic]` 安装
- `from constants import WORKBUDDY_DIR` → 这一类需要**额外处理**：不仅改写 import 路径，还需要把被引用的常量替换为 `config` 调用

### 2.4 已明确排除的脚本（不在映射中）

以下脚本属于 **`hermes-exec`（CTO 已冻结）** 或不属于核心铁三角，故意排除：

```python
EXCLUDED_SCRIPTS = [
    # hermes-exec — CTO 决议冻结，含安全风险（沙箱 Python 执行）
    "hermes-exec/scripts/execute_code.py",
    "hermes-exec/scripts/sandbox.py",
    "hermes-exec/scripts/rpc_server.py",
    # hermes-portable-bootstrap — 跨设备迁移工具，太特定于个人工作流
    "hermes-portable-bootstrap/scripts/bootstrap.py",
    # nudge-review — 纯 SKILL.md 协议（无独立脚本），不需要映射
]
```

---

## 叁、HITL 交互流伪代码

> **设计哲学**：`sync.py --diff` 是一次「提案 + 审批」过程，不是你一键推送到 GitHub。终端输出像一份干净的差旅报销单——所有条目都可审查，你点头后才执行。

### 3.1 主流程伪代码

```python
#!/usr/bin/env python3
"""
sync.py — Hermes-Nexus 本地同步引擎
=====================================
Usage:
  sync.py --diff              # 预览差异（默认模式，安全只读）
  sync.py --diff --full       # 预览差异 + 详细代码 diff
  sync.py --apply             # 执行同步（需在 --diff 确认后）
  sync.py --security-only     # 仅运行安全扫描，不执行同步
  sync.py --export            # 导出当前状态报告到 JSON
"""

def main():
    args = parse_args()

    # ═══════════════════════════════════════════════════════
    # Phase 1: 收集源状态
    # ═══════════════════════════════════════════════════════
    print_header("Phase 1/4", "收集源状态")
    source_files = collect_source_files(SYNC_MAP)   # 遍历 ~/.workbuddy/skills/
    target_files = collect_target_files(SYNC_MAP)   # 遍历 hermes-nexus/memoria_engine/
    print(f"  源文件: {len(source_files)} | 目标文件: {len(target_files)}")

    # ═══════════════════════════════════════════════════════
    # Phase 2: 差异分析
    # ═══════════════════════════════════════════════════════
    print_header("Phase 2/4", "差异分析")
    changes = compute_diff(source_files, target_files, SYNC_MAP)
    # changes 结构:
    #   { "new": [(src, tgt, reason), ...],
    #     "modified": [(src, tgt, reason), ...],
    #     "deleted": [(tgt, reason), ...],
    #     "unchanged": [...] }

    if not any(changes.values()):
        print("\n✅ 无可同步变更。本地与开源仓库已完全同步。")
        return

    # ═══════════════════════════════════════════════════════
    # Phase 3: 安全扫描
    # ═══════════════════════════════════════════════════════
    print_header("Phase 3/4", "安全扫描")
    security_results = security_scan(source_files, SYNC_MAP)
    # security_results 结构:
    #   { "blocked": [(file, line, severity, match), ...],
    #     "warnings": [(file, line, severity, match, replacement), ...],
    #     "clean": [...] }

    # ── BLOCKER 存在 → 立即中止 ──
    if security_results["blocked"]:
        print("\n⛔ 安全扫描发现 BLOCKER 级别问题，同步被拒绝：")
        for file, line, severity, match in security_results["blocked"]:
            print(f"  ❌ {file}:{line} — [{severity}] {match}")
        print("\n  请先处理以上问题，然后重新运行 sync.py --diff。")
        return  # ← 硬性拒绝，不可绕过

    # ═══════════════════════════════════════════════════════
    # Phase 4: HITL 交互确认
    # ═══════════════════════════════════════════════════════
    print_header("Phase 4/4", "变更预览 & 审批")

    # 展示变更摘要
    display_change_summary(changes)

    # 展示安全警告（如有）
    if security_results["warnings"]:
        print("\n⚠️  安全扫描发现 WARNING 级别问题（将被自动替换）：")
        for file, line, severity, match, replacement in security_results["warnings"]:
            print(f"  ⚠️  {file}:{line} — [{severity}]")
            print(f"      匹配: {match}")
            print(f"      替换为: {replacement}")

    # ── 详细 diff 模式 ──
    if args.full:
        for change in changes["modified"]:
            show_unified_diff(change.src, change.tgt)

    # ── HITL 提问 ──
    if args.apply:
        # 用户已经传了 --apply，但这是危险的——要求二次确认
        print("\n⚠️  --apply 已指定，但同步是不可逆操作。")
        answer = input("确认执行同步？输入 'yes' 继续: ")
        if answer.lower() != "yes":
            print("已取消。")
            return
        execute_sync(changes, security_results)
    else:
        # 默认：只展示差异，询问是否执行
        print(f"\n{'─' * 60}")
        print(f"提案摘要：")
        print(f"  新增文件: {len(changes['new'])}")
        print(f"  修改文件: {len(changes['modified'])}")
        print(f"  安全替换: {len(security_results['warnings'])} 处")
        print(f"{'─' * 60}")

        answer = input("\n是否脱敏并合并到开源仓库？(y/n) [n]: ").strip().lower()
        if answer in ("y", "yes"):
            execute_sync(changes, security_results)
        else:
            print("\n已取消。运行 sync.py --apply 可在下次直接执行。")
            show_tip("如需查看详细 diff，运行 sync.py --diff --full")
```

### 3.2 终端输出效果模拟

用户运行 `sync.py --diff` 时的预期终端输出：

```

  🌌 Hermes-Nexus: 本地同步引擎
  ══════════════════════════════════════════════════════════════

  ─── Phase 1/4 · 收集源状态 ───────────────────────────────────
  源文件: 29 (来自 ~/.workbuddy/skills/)
  目标文件: 22 (来自 hermes-nexus/memoria_engine/)

  ─── Phase 2/4 · 差异分析 ─────────────────────────────────────
  🔍 发现 5 处本地优化，其中 3 处影响开源包。

  ─── Phase 3/4 · 安全扫描 ─────────────────────────────────────
  ✅ 安全扫描通过（0 BLOCKER, 1 WARNING, 28 CLEAN）
  ⚠️  检测到 1 处需要自动替换的路径（见下方 Phase 4）

  ─── Phase 4/4 · 变更预览 & 审批 ──────────────────────────────

  📋 变更摘要：

  ✨ NEW (2)
    1. memoria_engine/memory/memory_nudge.py
       原因: 本地 enhanced-memory/scripts/memory_nudge.py 有新增方法
    2. memoria_engine/daemon/platform.py
       原因: 新增跨平台守护抽象层（macOS/linux/Windows）

  📝 MODIFIED (1)
    3. memoria_engine/constants.py
       原因: 阈值常量从 3500 → 4000 (optimize capacity for large workspaces)

  ❌ DELETED (0)

  ⚠️  WARNINGS (1)
    4. memoria_engine/daemon/health.py:23
       匹配: /Users/siriuscyber/Library/LaunchAgents/...
       替换为: {MEMORIA_HOME}/daemon/...

  ──────────────────────────────────────────────────────────────
  提案摘要：
    新增文件: 2
    修改文件: 1
    安全替换: 1 处路径 + 0 处凭证
  ──────────────────────────────────────────────────────────────

  是否脱敏并合并到开源仓库？(y/n) [n]:
```

### 3.3 用户确认后的执行流程

```python
def execute_sync(changes, security_results):
    """
    用户确认后执行的实际同步流程。
    
    关键原则：
    - 先脱敏后写入（绝不把原始文件写入公开目录）
    - 原子操作（所有文件写入成功后才算完成，否则回滚）
    - 自动 commit（生成规范的 commit message）
    """
    
    # Step 1: 准备临时副本
    print("\n📦 正在准备脱敏副本...")
    temp_dir = create_temp_copy(changes)  # 在 /tmp/ 创建临时目录
    
    # Step 2: 对每个文件执行脱敏转换
    for change in changes["new"] + changes["modified"]:
        # 2a: security_scan 正则替换
        content = apply_security_replacements(
            change.src_content, 
            security_results["warnings"]
        )
        # 2b: import 路径重写（memoria_engine 内部）
        content = apply_import_rewrites(content, change.target, IMPORT_REWRITE)
        # 2c: constants → config 路径重写
        content = apply_path_rewrites(content, change.target)
        # 2d: 写入临时副本
        write_to_temp(temp_dir, change.target, content)
        print(f"  ✓ {change.target} (脱敏完成)")
    
    # Step 3: 二次验证（确保临时副本不含任何敏感信息）
    print("\n🔒 正在执行二次安全验证...")
    final_scan = security_scan_files(temp_dir)
    if final_scan["blocked"]:
        print("⛔ 二次验证失败！脱敏过程存在遗漏。同步已中止。")
        print("   请手动检查以下文件:")
        for f, line, sev, match in final_scan["blocked"]:
            print(f"   {f}:{line} — {match}")
        cleanup(temp_dir)
        return False
    
    # Step 4: 原子复制到目标目录
    print("\n📋 正在写入目标目录...")
    backup_original = backup_targets(changes)  # 先备份现有目标文件
    try:
        copy_from_temp(temp_dir, PROJECT_ROOT)  # 从临时目录 → hermes-nexus/
    except Exception as e:
        print(f"⛔ 写入失败: {e}")
        restore_from_backup(backup_original)
        cleanup(temp_dir)
        return False
    
    # Step 5: 生成 commit message 并提交
    print("\n📝 正在生成提交...")
    commit_msg = generate_commit_message(changes)
    # commit_msg 格式:
    #   "sync: enhanced-memory v2.3.1 → memoria_engine (2026-05-21)
    #   
    #    Changes:
    #    - memory_nudge.py: add multi-workspace batch review
    #    - platform.py: new cross-platform daemon abstraction
    #    - constants.py: capacity limit 3500→4000
    #    
    #    Security: 1 path replaced, 0 credentials detected"
    
    git_commit(commit_msg)
    print(f"  ✓ 提交完成: {commit_msg.split(chr(10))[0]}")
    
    # Step 6: 清理
    cleanup(temp_dir)
    print("\n✅ 同步完成。运行 `git push` 推送到 GitHub 公开仓库。")
    print("   提示: 如需在推送前再次审查，运行 `git diff HEAD~1`")
```

### 3.4 关键设计决策的辩证

#### 为什么需要临时副本？

直接修改 `hermes-nexus/memoria_engine/` 是危险的：
- 如果脱敏不完整，敏感内容就落入了公开目录
- 如果写入中途失败，公开仓库处于半修改状态

用临时副本 `→` 全部脱敏 `→` 二次验证 `→` 原子复制，保证公开仓库永远不会收到未脱敏内容。

#### 为什么自动 commit 但不自动 push？

- 自动 commit：你运行 `y` 时已经表达了「同意合并」的意图，commit 是执行这个意图
- 不自动 push：push 是「公开可见」的最终步骤，给你最后一个 `git diff HEAD~1` 的审查窗口。这和「发送邮件前再看一遍」的道理一样

#### 为什么 `--apply` 需要二次确认？

`--apply` 意味着「跳过预览，直接执行」。这跳过了 HITL 的审查环节——对于涉及安全脱敏的操作，不行。所以即使传了 `--apply`，我们仍然要求输入 `yes`。

---

## 肆、验收标准 (DoD) — Step 1 完成标志

在设计图纸获批后，编写实际代码前，以下条件必须满足：

- [ ] **A: 正则清单已覆盖所有已知凭证类型**（7 类 × 约 25 个正则，无遗漏）
- [ ] **B: 映射字典包含核心铁三角全部 29 个脚本**（enhanced-memory + hermes-cron + hermes-kanban，不含 hermes-exec）
- [ ] **C: 10 个文件重命名在映射字典中已标注**（`rename: True`）
- [ ] **D: 黑名单文件列表完整**（settings.json / models.json / SOUL.md / IDENTITY.md / USER.md / MEMORY.md / .db / .pem / .key / .plist / .DS_Store / __pycache__）
- [ ] **E: HITL 流程包含 Phase 1-4 完整链路**（收集 → 差异 → 安全 → 确认）
- [ ] **F: BLOCKER 级别问题硬性拒绝，不可绕过**
- [ ] **G: WARNING 级别问题展示并自动替换路径，保留日志**
- [ ] **H: 临时副本 → 脱敏 → 二次验证 → 原子复制 的安全流水线已定义**
- [ ] **I: 自动 commit 但不自动 push 的边界已明确**

---

## 伍、待 CTO 确认的边界问题

在开始编码前，以下 3 个边界问题需要你拍板：

**Q1: 同步频率** — 手动运行 `sync.py --diff` 还是定时自动化？
- 推荐: 手动运行。每次你在本地改了 enhanced-memory 等 skill 后，主动跑一次。
- 备选: 做成 weekly cron job，但 HITL 需要你人在终端前，自动化意义有限。

**Q2: 首次同步策略** — 全部 29 个文件一次性同步？还是分批？
- 推荐: 一次性全部同步。这是初始导入，不存在「误覆盖已有公共版本」的问题。
- 备选: 按子系统分批（先 memory → 再 cron/kanban），但增加了复杂度和出错概率。

**Q3: `constants.py` 的公共版本** — 路径常量移除后，公共版本还需要 `constants.py` 吗？
- 推荐: 保留但精简至仅纯常量（容量限制/阈值/权重/超时），不含路径。路径类常量全部迁入新的 `config.py`（由 `memoria_engine/config.py` 通过环境变量 + YAML 加载）。
- 理由: 纯常量（如 `MEMORY_SOFT_LIMIT=3500`）是引擎的「调校参数」，对开源用户有意义——他们可能想调整。路径在 `config.py` 中按用户环境动态注入。

---

*本设计图纸等待 CTO Review & Approval。批准后进入代码实现阶段。*
