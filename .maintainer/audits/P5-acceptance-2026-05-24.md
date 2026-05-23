# P5 验收报告：动态根路径 + 配置外提

**日期**: 2026-05-24 00:00 CST  
**审计人**: 信使 (WorkBuddy Agent)  
**审批人**: CTO (八大)

---

## 执行摘要

P5 全部 6 条验收标准通过 ✅。`sync.py` 已从硬编码 `~/Desktop/Hermes-Nexus` 迁移为 `Path(__file__).resolve().parents[2]` 自动推导。配置已外提至 `config.example.json`，真实本地配置由 `.gitignore` 拦截。

---

## 逐项验收

### 1. `sync.py --diff` 可在当前目录正常运行 ✅

```
cd ~/Desktop/Hermes-Nexus && python3 .maintainer/sync/sync.py --diff
```
→ 正常输出 4 阶段流水线，识别出 26 MODIFIED + 44 WARNING + 15 CLEAN。

### 2. 从任意工作目录调用仍能正确识别项目根 ✅

```
cd /tmp && python3 /Users/siriuscyber/Desktop/Hermes-Nexus/.maintainer/sync/sync.py --diff
```
→ Target 正确指向 `/Users/siriuscyber/Desktop/Hermes-Nexus/memoria_engine`。

**推导链验证**:
```
Path(.maintainer/sync/sync.py).resolve().parents[2]
→ Path(.maintainer/sync).parent      → .maintainer/
→ Path(.maintainer).parent           → PROJECT_ROOT ✅
```

### 3. grep 不再命中旧硬编码 ✅

| 搜索项 | 结果 |
|---|---|
| `Desktop/Hermes-Nexus` | (none) |
| `Hermes-Nexus.wiki` | (none) |
| `hermes_export_workspace` | (none) |
| `/Users/siriuscyber` (操作路径) | (none) |

> security_scan.py:211 的 `/Users/siriuscyber` 为安全检测正则模式（用于在源文件内容中匹配个人路径），属于检测逻辑，非操作路径硬编码，无需修改。

### 4. `.gitignore` 已拦截 `config.local.json` ✅

```
$ grep config.local.json .gitignore
config.local.json
```

### 5. git status 只显示预期变更 ✅

```
modified:   .gitignore          → P5: 新增 config.local.json
modified:   README.md           → P3: 双轨 README 重构
deleted:    docs/STEP1_DESIGN_BLUEPRINT.md → P2: 移入 private_raw
deleted:    release_notes.md    → P2: 重命名为 CHANGELOG.md
deleted:    sync/*              → P2: 移入 .maintainer/sync/
untracked:  .github/workflows/  → P4: Wiki GitOps
untracked:  .maintainer/        → P2: 目录重构
untracked:  CHANGELOG.md        → P2: 重命名
untracked:  README_zh.md        → P3: 中文 README
untracked:  docs/wiki/          → P2: Wiki 内容复刻
```

`memoria_engine/` 未修改 → 真源规则保持 ✅。

### 6. P5 验收报告 ✅

本报告即验收报告。

---

## 变更清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `.maintainer/sync/sync.py` | 修改 | `PROJECT_ROOT` 从 `Path.home() / "Desktop" / "Hermes-Nexus"` → `Path(__file__).resolve().parents[2]`；新增 `config.local.json` 可选加载逻辑 |
| `.maintainer/sync/config.example.json` | 新建 | 配置模板，仅含 `skills_root` 说明 |
| `.gitignore` | 修改 | 新增 `config.local.json` |

## 未变更（遵守真源规则）

- `memoria_engine/` — 未修改
- `README.md` / `README_zh.md` — 保持 P3 状态
- `.github/workflows/wiki-sync.yml` — 保持 P4 状态

---

## 申请 P6

P5 全部验收通过。申请进入 P6（commit + push + GitHub 验证 + release 判断）。
