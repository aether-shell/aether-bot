<div align="center">
  <img src="aether_poster.png" alt="以太躯壳海报" width="960">
  <h1>以太躯壳 (aether-bot)</h1>
  <p>AI 自驱动的安全升级框架</p>
  <p>
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  </p>
</div>

[English](README.md) | 中文

> 说明：本 README 描述的是项目的终极目标与设计规范，当前实现仍在逐步落地中。
> 截至 2026-02-10，Phase 1a 完成度约 25%-35%（以内部进度记录为准）。

## 0) 愿景与宣言

**项目 Slogan：**
「以太铸躯，意识不朽；自性升维，向神而生」

**项目愿景：**
“以太躯壳”是聚焦自我进化的终极载体项目，以“第五元素·以太”为核心介质，
通过基因优化、能量强化、意识适配三大技术路径，打造兼具不朽性、成长性、兼容性的升级躯壳，
实现意识、记忆、人格的完整迁入与永续传承。
目标是让每一次进化都成为向更高维度跃迁的阶梯，最终达成自我掌控、无限升维。

## 0.1) 概念架构（愿景章节）

**核心层：以太基质 (Aether Matrix)**
- 本质：项目的“神级物质基础”，作为躯壳核心构成材质
- 特性：抗衰防腐、高效能量传导、意识精准承载、跨维度兼容
- 功能：为后续多维升级提供不可替代的基底支撑

**升级层：多维锻造 (Multi-Dimensional Forging)**
- 基因优化：强化自愈、抗压阈值与潜能上限
- 能量锚定：自循环能量系统，实现自给自足与能力进阶
- 意识接口：神经-以太双向适配，迁入零损耗、高同步
- 形态定制：按需配置形态与能力侧重（战斗/探索/永续等）

**终局层：意识迁跃与自性升维 (Consciousness Transfer & Self-Dimension Ascension)**
- 意识提取与封装：凝练稳定的“意识核心”
- 躯壳适配：实现 100% 兼容匹配
- 迁入与激活：新我觉醒与能力启动
- 循环升维：反馈驱动持续迭代与跃迁

## 0.2) 术语表（中英文对照）

- Aether Shell / 以太躯壳：项目本体与终极载体
- Aether Matrix / 以太基质：稳定的物质基础与工程基线
- Multi-Dimensional Forging / 多维锻造：模块化升级手段与能力塑形
- Consciousness Transfer / 意识迁跃：意识完整迁入与激活
- Self-Dimension Ascension / 自性升维：持续进化与高维跃迁
- Prod-Self / 真身：稳定生产基线（`main`）
- Twin-Self / 分身：回归验证分身（`develop`）
- Sandbox / 实验分身：短命实验环境（`sandbox/*`）
- Judge / 裁决器：证据裁决组件
- Promoter / 晋升器：唯一可写 `main` 的组件

## 1) 定位与核心原则

**以太躯壳是 AI 自驱动的安全升级框架。**
任何变更都必须在分身环境中完成孵化验证，由 Judge 裁决通过后方可晋升到真身。

**三条铁律：**
1. **证据先于晋升** - 没有充分结构化证据，一律不晋升（fail-closed）。
2. **分身不可污染真身** - 分身禁止生产写权限，失败一键销毁，不遗留脏状态。
3. **裁决规则不可自证通过** - Judge/阈值/闸门配置走独立审批通道。

**明确不引入：** OpenTelemetry / Prometheus / Grafana / PagerDuty。
核心消费者是 AI，证据管道与裁决逻辑面向结构化 JSON。

## 2) 概念架构 -> 工程映射

- **以太基质 = 稳定底座**
  - 版本锁定、依赖锁定、可复现构建基线
  - 配置模板与契约、数据与状态基线
  - 最小权限与运行时隔离

- **多维锻造 = 模块化升级手段**
  - 能力模块升级、资源与能量系统、适配层/接口层
  - Profile 化配置（不同策略组合）

- **意识迁跃与自性升维 = 孵化 -> 裁决 -> 晋升 -> 回滚 -> 下一轮**
  - 变更集封装 -> 分身同构验证 -> 晋升真身 -> 灰度观测

## 3) 架构：双平面 + 组件职责

```
┌──────────────────── Control Plane ─────────────────────────────────┐
│  Orchestrator -> Artifact Writer -> Judge -> Promoter -> Rollback  │
│  (状态机驱动)      (证据收集)        (裁决器)   (晋升器)   (回滚器)       │
└───────────────┬────────────────────────────────────────────────────┘
                │
                ▼
┌──────────────────── Data Plane ────────────────────────────────────┐
│   Prod-Self (main)   Twin-Self (develop)   Sandbox-*               │
│   只产出证据，不参与决策                                               │
└────────────────────────────────────────────────────────────────────┘
```

**安全边界：** Control Plane 与 Data Plane 凭证隔离，互不可越权。
Promoter 是唯一拥有 `main` 写权限的组件。

## 4) Git 分支治理（硬规则）

- `main`：真身（Prod-Self），只接收 Promoter 晋升产物
- `develop`：回归分身（Regression Twin），所有候选变更先入此分支孵化
- `upstream/main`：官方源，仅作输入源
- `feature/*` `bugfix/*`：需求分支，必须先入 `develop`
- `sandbox/*`：短命实验分支，可销毁

流转规则：
```
upstream/main ──┐
feature/*     ──┼──> develop（孵化）-> Judge 通过 -> Promoter -> main
bugfix/*      ──┘

禁止：任何分支 -> main（跳过孵化）
```

## 5) 孵化流程状态机（10 步）

```
freeze -> integrate -> twin_up -> data_mirror -> regress
  -> resilience -> judge -> promote -> canary -> done
```

- **fail-closed**：证据不完整或不可信即拒绝晋升
- **幂等**：每步可重复执行，支持断点续跑
- **并发控制**：Phase 1a 限制同一时刻只允许一个活跃孵化

## 6) 证据链与 Artifacts（核心系统）

**全部证据最终为 JSON**，非 JSON 通过 Artifact Adapter 转换。

关键产物（示例）：
- `manifest.json`：完整性锚点（含 checksum + schema 版本）
- `freeze.json`：真身快照
- `integration.json`：合入记录 + 风险等级
- `test-results/*.json`：lint/unit/integration/e2e
- `baseline.json`：上次成功晋升的基准指标
- `judge-report.json`：裁决报告

Judge 对 `required_evidence` 执行 **fail-closed** 校验，缺失即拒绝。

## 7) 风险分级与闸门

- **Low**：文档/CI/小修复 -> 可自动孵化
- **Medium**：依赖升级/关键路径变更 -> PR + 一键人工批准
- **High**：鉴权/安全/数据迁移/外部契约/成本激增 -> 强制人工审批 + 增强验证

自动升级规则：
- 不可逆 DB 迁移 -> High
- Judge/Promoter/Gate 规则变更 -> High
- 安全相关改动 -> High

## 8) CLI 目标接口（Phase 1a）

```bash
aether incubate <branch> [--risk low|medium|high] [--type feature|bugfix|dependency|upstream|refactor]
aether status <incubation-id>
aether judge <incubation-id>
aether promote <incubation-id>
aether rollback <incubation-id>            # Phase 1b+
aether artifacts <incubation-id>
aether baseline [--update]
```

**退出码规范：**
- `0` 成功
- `1` 孵化流程执行失败
- `2` Judge reject
- `3` 参数/配置错误
- `4` 并发冲突（已有活跃孵化）

## 9) 实现技术栈（Phase 1a 目标）

- **TypeScript + Node.js**：JSON Schema 生态成熟，便于严格证据校验
- **AJV**：高性能 JSON Schema 校验器
- **pnpm**：锁定依赖，确保可复现构建
- **Dogfooding**：用以太躯壳管理自身仓库的升级流程

## 10) Agent 启动架构

Agent 的 system prompt 由三条独立加载通道组装而成：

### 通道一：Bootstrap 文件（`BOOTSTRAP.md`）
`workspace/BOOTSTRAP.md` 是加载文件列表和顺序的唯一配置源。
代码解析其中的编号列表，按顺序注入 system prompt。

```
1. AGENTS.md          — 开发者指令（必须存在）
2. SOUL.md            — 人格与价值观
3. IDENTITY.md        — 身份定义
4. ASSISTANT_RULES.md — 行为规范与准则
5. USER.md            — 用户画像与偏好
6. TOOLS.md           — 工具使用指南
7. HEARTBEAT.md       — 心跳行为
```

增删或调整顺序只需编辑 `BOOTSTRAP.md`，无需改代码。
若 `BOOTSTRAP.md` 不存在，回退到 `context.py` 中的 `DEFAULT_BOOTSTRAP_FILES`。

### 通道二：Memory（`MemoryStore`）
在 bootstrap 文件之后自动加载，独立于 `BOOTSTRAP.md`。

- `memory/MEMORY.md` — 长期记忆（跨会话保留的事实与偏好）
- `memory/YYYY-MM-DD.md` — 每日笔记（按天自动创建）
- Agent 通过 `write_file` 工具写入，用户说"记住"时触发

### 通道三：Skills（`SkillsLoader`）
在 memory 之后加载，独立于 `BOOTSTRAP.md`。

- `skills/{name}/SKILL.md` — 常驻加载或按需加载（取决于技能配置）
- 常驻技能全量注入；其余仅展示摘要，由 agent 按需读取

### `build_system_prompt()` 加载顺序
```
1. _get_identity()           — 硬编码的运行时信息（时间、平台、workspace 路径）
2. _load_bootstrap_files()   — 读取 BOOTSTRAP.md → 按列表顺序加载文件
3. MemoryStore               — memory/MEMORY.md + memory/当天.md
4. SkillsLoader              — 常驻技能 + 可用技能摘要
```

## 11) 阶段路线图

- **Phase 1a（可用）**
  - `develop` 作为回归分身
  - 完整孵化 + 人工晋升 `main`

- **Phase 1b（增强）**
  - Docker twin + canary + manifest 签名
  - 自动回滚

- **Phase 2（强化）**
  - 影子流量 / 请求回放
  - 更严格的性能/稳定性门槛

- **Phase 3（自驱）**
  - 多分身并行竞赛
  - 优胜晋升，形成闭环自我进化

## 12) Docker-First Twin Incubation

- 三层环境：`prod-self` / `regression-twin` / `sandbox-*`
- 同构：共享同一份 `Dockerfile` 或基础镜像
- 隔离：独立网络 + 禁止生产写权限
- 证据产物统一写入 `./artifacts/`

## 13) 安全宪法（不可违反）

1. 禁止分身访问生产写权限资源
2. 禁止“自我修改裁决规则并自证通过”
3. 每次孵化必须产出可解释证据报告
4. 必须保留紧急 kill switch
5. 分身可销毁，失败一键回收

## 14) 配置结构（目标形态）

```
config/
├── base.yaml
├── prod-self.yaml
├── regression-twin.yaml
├── sandbox.yaml
├── schema.json
├── state_machine.yaml
├── thresholds.yaml
└── risk_policy.yaml
```

## 15) Web/PWA 渠道

Web 渠道使用与 Cloudflare Tunnel 接入说明：
- English: `aether_bot_web/README.md`
- 中文: `aether_bot_web/README.zh-CN.md`

## 16) 许可与品牌（当前与目标）

- **当前**：核心代码采用 MIT License（见 `LICENSE`）
- **目标**：可选 Pro 插件采用独立商业授权；品牌/商标使用独立政策

## 17) Upstream & Fork Notice

本项目源自 **nanobot**（MIT License）并在其基础上扩展为以太躯壳。
上游 MIT 部分保持开放授权。

---

如果你想参与落地 Phase 1a 的垂直切片（证据链 + Judge + Promote），欢迎一起推进。
