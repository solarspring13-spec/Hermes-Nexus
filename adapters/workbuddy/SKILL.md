---
name: memoria-engine
description: >-
  Memoria Engine — Hermes-Nexus 核心记忆引擎 (WorkBuddy 适配器)。
  提供三层自治记忆架构（L0 会话状态 / L1 日志 / L2 长期记忆）、
  Periodic Nudge 记忆自审查、FTS5/LanceDB 语义搜索、
  Cron 调度引擎、Kanban 多 Agent 协作看板。
  所有重度逻辑委托给底层 `memoria_engine` Python 包执行，
  本 SKILL.md 仅作为薄壳路由层。
  触发词：记忆、回忆、nudge、memory、审查、session、cron、kanban、install-memoria
metadata:
  hermes:
    tags: [memory, nudge, fts5, semantic, cron, kanban, daemon]
    platforms: [workbuddy]
    upstream: https://github.com/solarspring13-spec/Hermes-Nexus
agent_created: true
allowed-tools:
  - Bash
  - Read
  - Write
  - Glob
---

# Memoria Engine — WorkBuddy Adapter

**Hermes-Nexus 核心记忆引擎的 WorkBuddy 薄壳适配层。**

> 架构原则：SKILL.md 只做「路由 + 参数校验」，所有重度逻辑委托给底层 `memoria_engine` Python 包。
> 这保证了：
> - 核心逻辑跨平台复用（OpenClaw / WorkBuddy / Generic 共享同一套 Python 代码）
> - 适配器轻薄、易维护（本文件 <200 行）
> - 引擎升级不影响适配器（import 接口稳定）

---

## Phase 0：自举与健康检查 (Bootstrap)

**每次会话启动时静默执行。**

### Step 0.1：验证引擎安装
```bash
python3 -c "import memoria_engine; print(memoria_engine.__file__)" 2>&1
```
若失败 → 提示用户运行：
```bash
bash ~/.workbuddy/skills/memoria-engine/install.sh --local
```

### Step 0.2：守护健康检查
```bash
python3 -m memoria_engine.daemon.health --json 2>&1
```
- 所有 daemon 为 `ok` → 静默
- 任何 daemon 为 `stale` 或 `missing` → 通知用户

---

## Phase 1：会话记忆捕获 (Session Memory Capture)

**触发条件**：用户开始新任务 / 做出决策 / 发现新事实时自动调用。

### 委托调用
```bash
python3 -m memoria_engine.memory.session_state \
  --workspace "<CURRENT_WORKSPACE>" \
  --add-decision "<DECISION_TEXT>" \
  --json
```

```bash
python3 -m memoria_engine.memory.session_state \
  --workspace "<CURRENT_WORKSPACE>" \
  --add-fact "<FACT_TEXT>" \
  --json
```

```bash
python3 -m memoria_engine.memory.session_state \
  --workspace "<CURRENT_WORKSPACE>" \
  --complete-task "<TASK_ID>" \
  --json
```

**参数说明**：
- `--workspace`：当前 WorkBuddy 工作区路径
- `--add-decision` / `--add-fact` / `--add-question` / `--add-task` / `--complete-task`：L0 即时记忆写入
- `--json`：静默返回，仅在出错时输出

**本阶段不包含任何业务逻辑** — 所有记忆写入、去重、容量检查均由 `session_state.py` 内部处理。

---

## Phase 2：Periodic Nudge 记忆审查 (Nudge Review)

**触发条件**：每 10 次工具调用后自动触发（由调用方计数）。

### 委托调用
```bash
python3 -m memoria_engine.memory.memory_nudge \
  --workspace "<CURRENT_WORKSPACE>" \
  --global \
  --session-startup \
  --json
```

**返回解读**：
- `needs_review: true` → 静默审查 MEMORY.md / USER.md 是否需要更新
- `nudge_due: true` → 执行写入更新
- `action: skip` → 无操作

**后续压缩检查**：
```bash
python3 -m memoria_engine.memory.memory_compress \
  --workspace "<CURRENT_WORKSPACE>" \
  --json
```

> **委托哲学**：Nudge 的触发、计数、审查判断、写入策略全部在 `memory_nudge.py` 中实现。
> 本 SKILL.md 只负责在正确的时间点调用正确的命令。

---

## Phase 3：跨会话记忆搜索 (Cross-Session Search)

**触发条件**：用户提及"上次"、"之前"、"恢复"、"继续"等关键词。

### 委托调用

**近期记忆回顾（FTS5 全局）**：
```bash
python3 -m memoria_engine.memory.memory_index \
  --global --recent 7 --limit 5 --json
```

**语义搜索（BGE-M3 + LanceDB）**：
```bash
python3 -m memoria_engine.semantic.intent_learner \
  --query "<USER_QUERY>" \
  --preload \
  --mode hybrid \
  --json
```

**未完成任务扫描**：
```bash
python3 -m memoria_engine.memory.session_state \
  --pending-tasks --global --json
```

> 三阶段结果静默注入推理上下文中。仅在输出中有明确相关上下文时才在回复中引用。

---

## Phase 4：Cron 调度引擎 (Cron Scheduler)

**触发条件**：用户设置定时任务时。

### 委托调用
```bash
# 解析自然语言 Cron 表达式
python3 -m memoria_engine.cron.parser \
  --natural "<NATURAL_LANGUAGE_SCHEDULE>" \
  --json

# 注册定时任务
python3 -m memoria_engine.cron.scheduler \
  --register \
  --name "<JOB_NAME>" \
  --rrule "<RRULE_STRING>" \
  --command "<SHELL_COMMAND>" \
  --json
```

---

## Phase 5：Kanban 多 Agent 看板 (Kanban Board)

**触发条件**：多 Agent 协作任务编排时。

### 委托调用
```bash
# 创建任务
python3 -m memoria_engine.kanban.db \
  --create-task \
  --title "<TASK_TITLE>" \
  --worker "<WORKER_NAME>" \
  --json

# 查询看板状态
python3 -m memoria_engine.kanban.db \
  --status --json

# 检测僵尸 Worker
python3 -m memoria_engine.kanban.worker \
  --zombie-scan --json
```

---

## Phase 6：质量保障 (Quality Guardians)

### 写入校验
```bash
python3 -m memoria_engine.memory.write_verifier \
  --file "<MEMORY_FILE_PATH>" \
  --json
```
验证记忆写入是否成功、格式是否正确。

### 语义质量评分
```bash
python3 -m memoria_engine.memory.memory_quality \
  --workspace "<CURRENT_WORKSPACE>" \
  --json
```
输出记忆质量报告：覆盖率、去重率、压缩建议。

---

## 安装指引

### 一键安装（推荐）
```bash
# 在 Hermes-Nexus monorepo 内：
bash adapters/workbuddy/install.sh --local

# 独立安装（从 GitHub）：
bash <(curl -s https://raw.githubusercontent.com/solarspring13-spec/Hermes-Nexus/main/adapters/workbuddy/install.sh)
```

### 手动安装
```bash
pip install git+https://github.com/solarspring13-spec/Hermes-Nexus.git
mkdir -p ~/.memoria_engine/{data,logs,health/heartbeats,db,cache/embeddings}
```

### Skill 注册
```bash
mkdir -p ~/.workbuddy/skills/memoria-engine
cp adapters/workbuddy/SKILL.md ~/.workbuddy/skills/memoria-engine/SKILL.md
```

---

## 架构图

```
┌──────────────────────────────────────────────────────┐
│  WorkBuddy Agent                                     │
│  ┌────────────────────────────────────────────────┐  │
│  │  SKILL.md (thin wrapper, ~180 lines)           │  │
│  │  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐          │  │
│  │  │Phase1│ │Phase2│ │Phase3│ │Phase4│  ...     │  │
│  │  │路由  │ │路由  │ │搜索  │ │Cron  │          │  │
│  │  └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘          │  │
│  └─────┼────────┼────────┼────────┼───────────────┘  │
│        │        │        │        │                   │
│        ▼        ▼        ▼        ▼                   │
│  ┌─────────────────────────────────────────────────┐ │
│  │  memoria_engine (Python package)                │ │
│  │  ┌────────┐ ┌──────────┐ ┌──────────┐          │ │
│  │  │ memory │ │ semantic │ │  cron    │  ...     │ │
│  │  │  L0-L2 │ │ BGE-M3   │ │ parser   │          │ │
│  │  └────────┘ └──────────┘ └──────────┘          │ │
│  └─────────────────────────────────────────────────┘ │
│                                                      │
│  ┌─────────────────────────────────────────────────┐ │
│  │  ~/.memoria_engine/  (data root)                │ │
│  │  data/  logs/  db/  health/  cache/  config.yaml│ │
│  └─────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────┘
```

---

## 版本与兼容性

| 版本 | 日期 | 变更 |
|:--|:--|:--|
| v0.1.0 | 2026-05-22 | 初始 WorkBuddy 适配器 — 薄壳路由 + 全量委托 |
