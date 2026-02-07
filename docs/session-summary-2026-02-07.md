# nanobot 会话总结（2026-02-07）

## 目标与需求
- 将 nanobot 本地会话与大模型会话绑定，避免每条消息创建新会话。
- 当上下文接近上限时可感知并压缩，尽量不“失忆”。
- 需要可验证的上下文状态输出（CLI/飞书）。
- 飞书里显示“会话状态”时要更易读，并支持来源标注。
- 本地窗口策略可配置，最终设置为 128K 预算。

## 已实现功能
1) 原生会话绑定（Responses API）
- 在 provider 层支持 previous_response_id，并在响应中记录 response_id/usage。
- nanobot 会话元数据记录 LLM 会话状态。

2) 上下文管理 + 自动压缩
- 新增 ContextManager：
  - 滚动摘要（summary + summary_index）。
  - 基于窗口阈值触发摘要。
  - 压缩时同步重置模型会话（避免本地/模型不一致）。
  - 估算 tokens/占比，且尽量使用 API usage。

3) CLI 输出上下文状态
- 新增 `nanobot agent --show-context`，显示 context mode/估算 tokens/占比/是否压缩。

4) 飞书输出上下文状态（可选）
- 新增 `feishu.showContext` 开关，飞书消息末尾附加状态行。
- 状态内容为：
  - 会话模式（模型连续/重新绑定/本地拼接）
  - LLM 会话压缩（是/否）
  - 同步重置（是/否）
  - 数据来源（API/估算）
  - 估算Tokens
  - LLM Context 百分比

## 主要代码改动
- 新增：`nanobot/agent/context_manager.py`
  - 摘要生成、预算控制、原生会话复用、同步重置。
- 修改：`nanobot/agent/context.py`
  - 支持注入摘要与可选 system prompt。
- 修改：`nanobot/agent/loop.py`
  - 使用 ContextManager；传入 session_state；输出元数据；
  - 优先使用 API usage 计算 tokens/占比；
  - 记录 _context_source (usage/estimate)。
- 修改：`nanobot/providers/base.py`、`nanobot/providers/litellm_provider.py`
  - 支持 session_state 与 response_id/conversation_id/usage。
- 修改：`nanobot/cli/commands.py`
  - 新增 `--show-context` 显示上下文状态。
- 修改：`nanobot/channels/feishu.py`
  - 追加上下文状态行（可配置）。
- 修改：`nanobot/config/schema.py`
  - 新增 ContextConfig；新增 FeishuConfig.show_context。

## 飞书状态字段含义（当前显示）
- 会话模式：
  - 模型连续：模型端会话在复用（native）。
  - 重新绑定：发生重置后重新绑定。
  - 本地拼接：不走模型端会话，仅本地拼接。
- LLM 会话压缩：
  - 是/否，表示本地是否触发“摘要压缩”。
- 同步重置：
  - 是/否，表示是否已同步重置模型会话。
- 数据来源：
  - API：usage 来自模型 API。
  - 估算：无 usage 时本地估算。
- 估算Tokens / LLM Context：
  - 由 API usage 或本地估算得到。

## 配置更新（~/.nanobot/config.json）
- 模型：gpt-5.2
- 上下文窗口：
  - windowTokens = 128000
  - reserveTokens = 4096
  - summarizeThreshold = 0.7
  - hardLimitThreshold = 0.85
- 飞书：showContext = true

## 验证方式
- CLI：`nanobot agent -m "你好" --show-context`
  - 应显示 context mode 等状态行。
- 飞书：消息末尾应出现“会话模式/LLM会话压缩/数据来源”等行。
- session 元数据：
  - `metadata.context.summary` / `summary_index` 有值时表示压缩生效；
  - `metadata.llm_session.previous_response_id` 表示会话绑定。

## 备注
- “LLM 会话压缩”是本地逻辑（摘要机制），API 不会直接返回此状态。
- tokens/占比优先使用 API usage，否则回退到估算。
