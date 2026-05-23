# Hermes-Nexus：记忆引擎

<p align="center">
  <em>为 AI Agent 打造的自进化记忆与工作流架构</em>
</p>

<p align="center">
  <a href="https://github.com/solarspring13-spec/Hermes-Nexus/actions/workflows/qa_sentinel.yml"><img src="https://github.com/solarspring13-spec/Hermes-Nexus/actions/workflows/qa_sentinel.yml/badge.svg" alt="QA Sentinel"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-BSL%201.1-blueviolet" alt="BSL 1.1"></a>
  <a href="#"><img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+"></a>
  <a href="#"><img src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey" alt="Platform"></a>
  <img src="https://img.shields.io/badge/status-alpha-orange" alt="Alpha">
</p>

<p align="center">
  <a href="README.md">English</a>
</p>

---

## 失忆先知的悖论

想象一位举世无双的先知。

她言出必准，跨域推演——法律、医学、代码、诗歌——信手拈来，从不迟疑。给她一个问题，她会层层拆解，直到真相在思辨中结晶。

但只要你离开房间，她就忘记**一切**。不只是你的名字——问题、解法、推理链条、你花一小时才提炼出的三条洞见，全部归零。第二天早上你回来，她再次对你微笑——依然聪慧，依然空白。

**这就是"失忆先知悖论"：无限的智能，零连续的记忆。** 今天的每一个 AI Agent 都活在这个悖论里。每一次会话都是冷启动，每一份洞见都在对话结束时蒸发。

而 Hermes-Nexus 的存在，就是为了打破这个悖论。通过将智能（LLM）与记忆（引擎）**物理解耦**，我们构建了一个架构，在这个架构中：切换模型不会丢失过去，重启进程不会回到白板，迁移平台不需要从零重建知识图谱。

> **算力是部落的，记忆是王国的。我们在两者之间架桥。**

---

## Hermes-Nexus 解决什么问题

| 问题 | 解法 |
|------|------|
| AI Agent 跨会话遗忘 | **记忆引擎**自动蒸馏 L0 → L1 → L2，让上下文持续积累 |
| 多 Agent 平台，记忆不互通 | **跨平台适配器**（WorkBuddy、OpenClaw），薄壳委托，一套引擎到处跑 |
| 记忆无限膨胀，变成噪音 | **智能生命周期**：L1 保留 30 天窗口，L2 按 P0/P1/P2 优先级压缩 |
| Agent 记忆缺乏升级路径 | **OTA 升级引擎**，SemVer 版本比对 + GitHub Releases 自动拉取 |

---

## 核心能力

### 三层记忆引擎（L0 → L1 → L2）

| 层级 | 名称 | 做什么 |
|------|------|--------|
| **L0** | 即时记忆 | 会话级状态捕获——决策、事实、任务、开放问题。实时自动记录。 |
| **L1** | 短期记忆 | 从 L0 蒸馏的结构化日总结。人类可读。保留 30 天。 |
| **L2** | 长期记忆 | 精选、压缩、持久化。FTS5 全文检索，跨会话跨工作区秒级召回。 |

全部由 **Hermes Nudge 协议**编排——周期性静默自审，检查 MEMORY.md 是否需要更新，然后压缩旧条目防止膨胀。

### 跨平台适配器（薄壳委托）

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  WorkBuddy   │  │  OpenClaw    │  │  Generic CLI │
│  SKILL.md    │  │  SKILL.md    │  │  pip/gh      │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                 │
       └─────────────────┼─────────────────┘
                         │ python3 -m memoria_engine.*
                         │ （所有逻辑在此）
               ┌─────────▼──────────┐
               │  记忆引擎           │
               │  （唯一事实来源）     │
               └────────────────────┘
```

适配器不包含任何业务逻辑。每条命令都路由到 `python3 -m memoria_engine.X.Y --json`。一次安装，到处运行。

### 自然语言 Cron 调度器

用中文或英文写定时任务：

```
"每周一上午 9 点运行"
"every Monday at 9 AM"
"每天 23:59 发送 Token 报告"
```

解析器自动转换为 RFC 5545 RRULE 字符串并注册到宿主自动化系统。支持 `at-most-once` 执行语义、任务链、`no_concurrent` 保证。

### 语义搜索与意图学习

- **BGE-M3 嵌入**实现概念级跨会话召回（而非仅靠关键词匹配）
- **意图预载**——识别意图指纹（12 种种子意图：股票分析、投资尽调、旅行规划、代码调试等），在你输入完成前就预加载相关上下文
- **向量记忆**让你不记得具体用词也能找回"三周前讨论过的那件事"

### QA Sentinel 持续集成

每周一/三/五 UTC 02:00，CI 流水线拉取最新平台规范，与缓存基线做 diff，检测到破坏性变更时告警。适配器保持同步，而非腐化。

### OTA 平滑升级引擎

```bash
python3 -m memoria_engine.utils.updater           # 检查并升级
python3 -m memoria_engine.utils.updater --check    # 仅检查
python3 -m memoria_engine.utils.updater --json     # CI 友好的 JSON 输出
```

纯标准库，零依赖。SemVer 版本比对，GitHub API 缓存 1 小时，升级前自动备份。退出码：0 = 已最新/升级成功，1 = 升级错误，2 = 网络错误。

---

## 快速开始

```bash
git clone https://github.com/solarspring13-spec/Hermes-Nexus.git
cd Hermes-Nexus
bash install.sh
```

通用安装器自动检测宿主环境（WorkBuddy / OpenClaw / Generic）并委托给对应适配器。

**验证安装：**

```bash
python3 -c "from memoria_engine.config import MEMORIA_HOME; print(MEMORIA_HOME)"
# → /Users/you/.memoria_engine
```

**第一次执行记忆 Nudge：**

```bash
python3 -m memoria_engine.memory.memory_nudge --help
```

---

## 架构

```
┌─────────────────────────────────────────────────────────┐
│                    记忆引擎                               │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │
│  │  L0 即时记忆  │→│ L1 短期记忆  │→│  L2 长期记忆     │  │
│  │  会话状态    │  │  日总结     │  │  精选持久化      │  │
│  └─────────────┘  └─────────────┘  └─────────────────┘  │
│         ↑ 蒸馏         ↑ 压缩             ↑               │
│         └───────────────┴─────────────────┘               │
│                   自动生命周期                             │
└─────────────────────────────────────────────────────────┘
```

**设计准则：** 适配器是薄壳。每条命令委托给引擎。引擎是唯一事实来源。

**数据根目录：** `~/.memoria_engine/`

```
.memoria_engine/
├── data/          （记忆池、日总结、MEMORY.md）
├── health/        （守护进程心跳）
├── db/            （SQLite：FTS5 索引、看板、用户模型）
├── cache/         （BGE-M3 嵌入、意图签名）
└── config.yaml    （全局配置）
```

---

## 子系统一览

| 子系统 | 模块 | 说明 |
|--------|------|------|
| 记忆 | `memory/` | L0/L1/L2 三层记忆，FTS5 搜索，压缩与去重 |
| Cron | `cron/` | 自然语言调度器 → RRULE |
| 看板 | `kanban/` | 多 Agent 任务板，僵尸检测 |
| 语义 | `semantic/` | BGE-M3 嵌入，意图学习，向量记忆 |
| 守护 | `daemon/` | 健康心跳，多信号交叉验证 |
| 技能 | `skills/` | 自动检测技能创建，置信度评分 |
| 工具 | `utils/` | Agent 路由，会话恢复，OTA 升级 |

---

## 项目源流

Hermes-Nexus 汲取了两股灵感的汇流：

- **NousResearch Hermes Agent**——Agent 记忆架构的原始愿景：让 AI Agent 在交互之间携带持久状态。
- **赫尔墨斯（神）**——神界信使，冥界向导，边界的守护者。为一个跨越会话、平台与模型搬运记忆的系统，没有比这更贴切的守护神了。

记忆引擎将这两种理念拓展为一个独立、平台无关的实现——记忆独立于任何单一 Agent 运行时而存在。

---

## 许可证

BSL 1.1 —— 个人使用、研究、内部工具免费。**2030 年 5 月 22 日**自动转为 MIT。

[完整许可证 →](LICENSE)

---

## 路线图

- **v0.2.0**：BGE-M3 模型打包，支持离线语义搜索
- **v0.3.0**：多 Agent 协作协议（Hermes Kanban v2）
- **v1.0.0**：稳定 API，正式化插件系统，全面测试覆盖

---

<p align="center">
  <em>Hermes-Nexus 项目组出品。</em><br>
  <em>算力是部落的，记忆是王国的。我们在两者之间架桥。</em>
</p>
