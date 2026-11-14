"""代理循环: 核心处理引擎

这是nanobot的心脏，负责处理所有用户消息并生成响应。

主要职责:
1. 从消息总线消费入站消息
2. 加载或创建会话（含历史对话）
3. 构建LLM上下文（系统提示词 + 历史 + 当前消息）
4. 调用LLM生成响应
5. 执行工具调用（如有）
6. 流式返回响应（如启用）
7. 保存会话历史
8. 发送最终响应到消息总线

关键特性:
- 支持流式响应（实时推送部分内容）
- 工具调用循环（最多max_iterations次）
- 原生会话模式（Claude的会话状态管理）
- 性能监控（记录各阶段耗时）
- 子代理支持（通过SpawnTool）
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nanobot.config.schema import ContextConfig, ExecToolConfig
    from nanobot.cron.service import CronService

    ExecToolConfigT = ExecToolConfig
    ContextConfigT = ContextConfig
else:
    ExecToolConfigT = Any
    ContextConfigT = Any

from loguru import logger

from nanobot.agent.context import ContextBuilder
from nanobot.agent.context_manager import ContextManager
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.claude import ClaudeTool
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider
from nanobot.session.manager import SessionManager


class _StreamState:
    """流式响应的状态管理器（内部类）

    管理LLM流式输出的缓冲和发送逻辑，避免频繁发送小块内容。

    缓冲策略:
    - 累积到达min_chars个字符
    - 或距离上次发送超过min_interval_s秒
    - 满足条件之一时flush（发送）

    流式响应的优势:
    - 用户体验更好（实时看到进度）
    - 减少等待焦虑
    - 适合长响应

    Attributes:
        bus: 消息总线（用于发送部分响应）
        channel: 目标渠道
        chat_id: 目标聊天ID
        base_metadata: 基础元数据（会附加stream标志）
        min_chars: 最小缓冲字符数
        min_interval_s: 最小发送间隔（秒）
        buffer: 当前缓冲的文本
        sent_any: 是否已发送过内容
        last_flush: 上次发送的时间戳
        stream_id: 流式响应的唯一ID
    """

    def __init__(
        self,
        bus: MessageBus,
        channel: str,
        chat_id: str,
        base_metadata: dict[str, Any] | None,
        min_chars: int,
        min_interval_s: float,
    ):
        """初始化流式状态管理器

        Args:
            bus: 消息总线实例
            channel: 目标渠道名称
            chat_id: 目标聊天ID
            base_metadata: 基础元数据（会被复制）
            min_chars: 最小缓冲字符数（默认120）
            min_interval_s: 最小发送间隔秒数（默认0.5）
        """
        self.bus = bus
        self.channel = channel
        self.chat_id = chat_id
        self.base_metadata = dict(base_metadata or {})
        self.min_chars = min_chars
        self.min_interval_s = min_interval_s
        self.buffer = ""
        self.sent_any = False
        self.last_flush = 0.0
        stamp = int(time.time() * 1000)
        self.stream_id = f"{channel}:{chat_id}:{stamp}"

    async def on_delta(self, delta: str) -> None:
        """处理LLM的增量输出

        每当LLM生成一小块文本时，此方法会被调用。
        文本会被添加到缓冲区，如果满足发送条件则flush。

        发送条件（满足其一）:
        - 缓冲区字符数 >= min_chars
        - 距离上次发送时间 >= min_interval_s

        Args:
            delta: LLM生成的文本增量
        """
        if not delta:
            return
        self.buffer += delta
        now = time.monotonic()
        if len(self.buffer) >= self.min_chars and (now - self.last_flush) >= self.min_interval_s:
            await self.flush(final=False)

    async def flush(self, final: bool) -> None:
        """发送缓冲区内容到消息总线

        将当前缓冲的文本作为OutboundMessage发送，并清空缓冲区。
        消息的metadata中会包含流式响应标志。

        Args:
            final: 是否是最后一块内容（True表示流式响应结束）
        """
        if not self.buffer:
            return
        metadata = dict(self.base_metadata)
        metadata["stream"] = True
        metadata["stream_id"] = self.stream_id
        metadata["final"] = final
        await self.bus.publish_outbound(OutboundMessage(
            channel=self.channel,
            chat_id=self.chat_id,
            content=self.buffer,
            metadata=metadata,
        ))
        self.sent_any = True
        self.buffer = ""
        self.last_flush = time.monotonic()


class AgentLoop:
    """代理循环 - 核心处理引擎

    这是nanobot的中枢神经系统，负责:

    1. **消息接收**: 从MessageBus消费InboundMessage
    2. **会话管理**: 加载或创建会话，维护对话历史
    3. **上下文构建**: 组装系统提示词、技能、记忆、历史
    4. **LLM调用**: 调用LLM Provider生成响应
    5. **工具执行**: 处理工具调用请求，执行工具
    6. **流式输出**: 实时推送部分响应（可选）
    7. **会话保存**: 持久化对话历史
    8. **响应发送**: 将最终响应发布到MessageBus

    工具调用循环:
    ```
    while iteration < max_iterations:
        response = await llm.chat(...)
        if response.has_tool_calls:
            for tool_call in response.tool_calls:
                result = await tools.execute(...)
                messages.append(tool_result)
        else:
            break  # 没有工具调用，返回文本响应
    ```

    原生会话模式（Native Session）:
    - 支持Claude的会话状态管理（使用previous_response_id）
    - 减少上下文token消耗
    - 自动处理会话失效并回退到完整上下文模式

    性能监控:
    - 记录入站等待时间（inbound_wait）
    - 记录上下文构建时间（context_time）
    - 记录LLM调用时间（llm_total）
    - 记录工具执行时间（tool_total）
    - 记录会话保存时间（save_time）
    """

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 20,
        brave_api_key: str | None = None,
        exec_config: ExecToolConfigT | None = None,
        cron_service: "CronService" | None = None,
        stream: bool = False,
        stream_min_chars: int = 120,
        stream_min_interval_s: float = 0.5,
        context_config: ContextConfigT | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
    ):
        """初始化代理循环

        设置所有核心组件:
        - 消息总线: 用于接收和发送消息
        - LLM提供者: 用于调用AI模型
        - 工作空间: 存放会话、技能、记忆等
        - 工具注册表: 管理所有可用工具
        - 会话管理器: 持久化对话历史
        - 子代理管理器: 创建和管理子代理

        Args:
            bus: 消息总线实例
            provider: LLM提供者（如LiteLLMProvider）
            workspace: 工作空间路径
            model: 模型名称（默认使用provider的默认模型）
            max_iterations: 工具调用的最大迭代次数（防止无限循环）
            brave_api_key: Brave搜索API密钥（用于WebSearchTool）
            exec_config: Shell命令执行配置（超时、环境变量等）
            cron_service: 定时任务服务（可选）
            stream: 是否启用流式响应
            stream_min_chars: 流式响应的最小缓冲字符数
            stream_min_interval_s: 流式响应的最小发送间隔（秒）
            context_config: 上下文管理配置（窗口大小、摘要策略等）
            restrict_to_workspace: 是否限制文件操作在工作空间内
            session_manager: 自定义会话管理器（可选）
        """
        from nanobot.config.schema import ContextConfig, ExecToolConfig
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.stream = stream
        self.stream_min_chars = stream_min_chars
        self.stream_min_interval_s = stream_min_interval_s
        self.context_config = context_config or ContextConfig()
        self.restrict_to_workspace = restrict_to_workspace

        # 初始化核心组件
        self.context = ContextBuilder(workspace)  # 上下文构建器
        self.context_manager = ContextManager(  # 上下文管理器（处理摘要、窗口管理）
            provider=provider,
            config=self.context_config,
            builder=self.context,
            default_model=self.model,
        )
        self.sessions = session_manager or SessionManager(workspace)  # 会话管理器
        self.tools = ToolRegistry()  # 工具注册表
        self.subagents = SubagentManager(  # 子代理管理器
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            brave_api_key=brave_api_key,
            exec_config=self.exec_config,
            restrict_to_workspace=self.restrict_to_workspace,
        )

        self._running = False
        self._register_default_tools()  # 注册默认工具集

    def _register_default_tools(self) -> None:
        """注册默认工具集

        注册7类核心工具:
        1. **文件工具**: read_file, write_file, edit_file, list_dir
        2. **Shell工具**: exec（执行命令）
        3. **Web工具**: web_search, web_fetch
        4. **Claude工具**: claude（调用Claude Code）
        5. **消息工具**: message（发送消息到其他聊天）
        6. **子代理工具**: spawn（创建子代理）
        7. **定时任务工具**: cron（可选，需要cron_service）

        工作空间限制:
        如果restrict_to_workspace=True，文件工具只能访问工作空间内的文件。
        """
        """Register the default set of tools."""
        # File tools (restrict to workspace if configured)
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        self.tools.register(ReadFileTool(allowed_dir=allowed_dir))
        self.tools.register(WriteFileTool(allowed_dir=allowed_dir))
        self.tools.register(EditFileTool(allowed_dir=allowed_dir))
        self.tools.register(ListDirTool(allowed_dir=allowed_dir))

        # Shell tool
        self.tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=self.exec_config.timeout,
            restrict_to_workspace=self.restrict_to_workspace,
        ))

        # Web tools
        self.tools.register(WebSearchTool(api_key=self.brave_api_key))
        self.tools.register(WebFetchTool())

        # Claude tool (Claude Code runner wrapper)
        self.tools.register(ClaudeTool())

        # Message tool
        message_tool = MessageTool(send_callback=self.bus.publish_outbound)
        self.tools.register(message_tool)

        # Spawn tool (for subagents)
        spawn_tool = SpawnTool(manager=self.subagents)
        self.tools.register(spawn_tool)

        # Cron tool (for scheduling)
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))

    def _should_stream(self, channel: str) -> bool:
        if not self.stream:
            return False
        return channel != "cli"

    def _create_stream_state(self, msg: InboundMessage) -> _StreamState:
        return _StreamState(
            bus=self.bus,
            channel=msg.channel,
            chat_id=msg.chat_id,
            base_metadata=msg.metadata,
            min_chars=self.stream_min_chars,
            min_interval_s=self.stream_min_interval_s,
        )

    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus."""
        self._running = True
        logger.info("Agent loop started")

        while self._running:
            try:
                # Wait for next message
                msg = await asyncio.wait_for(
                    self.bus.consume_inbound(),
                    timeout=1.0
                )

                # Process it
                try:
                    response = await self._process_message(msg)
                    if response:
                        await self.bus.publish_outbound(response)
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    # Send error response
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=f"Sorry, I encountered an error: {str(e)}"
                    ))
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a single inbound message.

        Args:
            msg: The inbound message to process.

        Returns:
            The response message, or None if no response needed.
        """
        # Handle system messages (subagent announces)
        # The chat_id contains the original "channel:chat_id" to route back to
        if msg.channel == "system":
            return await self._process_system_message(msg)

        if self._is_new_session_command(msg.content):
            session = self.sessions.start_new(msg.session_key)
            self.sessions.save(session)
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="✅ 已开启新会话（历史已保留）。你好！我能帮你做什么？",
                metadata=dict(msg.metadata) if msg.metadata else None,
            )

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info(f"Processing message from {msg.channel}:{msg.sender_id}: {preview}")
        trace_id = None
        if isinstance(msg.metadata, dict):
            trace_id = msg.metadata.get("trace_id")
        if not trace_id:
            trace_id = f"{msg.channel}-{int(time.time() * 1000)}"

        t_start = time.monotonic()
        inbound_wait = None
        received_at = msg.metadata.get("_received_at") if isinstance(msg.metadata, dict) else None
        if isinstance(received_at, (int, float)):
            inbound_wait = t_start - received_at

        # Get or create session
        session = self.sessions.get_or_create(msg.session_key)

        # Update tool contexts
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(msg.channel, msg.chat_id)

        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(msg.channel, msg.chat_id)

        cron_tool = self.tools.get("cron")
        if isinstance(cron_tool, CronTool):
            cron_tool.set_context(msg.channel, msg.chat_id)

        # Build initial messages (summary + recent history + optional native session state)
        t_ctx = time.monotonic()
        ctx_bundle = await self.context_manager.build_context(
            session=session,
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
        )
        messages = ctx_bundle.messages
        session_state = ctx_bundle.session_state
        ctx_stats = ctx_bundle.stats
        ctx_time = time.monotonic() - t_ctx

        # Agent loop
        iteration = 0
        final_content = None
        final_streamed = False
        llm_total = 0.0
        tool_total = 0.0

        last_response = None
        native_mode = ctx_stats.get("mode") == "native"

        # Native session recovery: if first LLM call in native mode fails,
        # clear stale previous_response_id and retry with full context (reset mode).
        if native_mode:
            t_llm = time.monotonic()
            first_response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model,
                session_state=session_state,
                on_delta=None,  # No streaming on probe
            )
            llm_time = time.monotonic() - t_llm
            llm_total += llm_time

            if first_response.finish_reason == "error":
                logger.warning(
                    f"Trace {trace_id} native session failed, resetting: "
                    f"{first_response.content[:200]}"
                )
                # Clear stale session state and rebuild context as reset
                llm_meta = session.metadata.setdefault("llm_session", {})
                llm_meta["previous_response_id"] = None
                llm_meta["pending_reset"] = False

                ctx_bundle = await self.context_manager.build_context(
                    session=session,
                    current_message=msg.content,
                    media=msg.media if msg.media else None,
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                )
                messages = ctx_bundle.messages
                session_state = ctx_bundle.session_state
                ctx_stats = ctx_bundle.stats
                native_mode = ctx_stats.get("mode") == "native"
            else:
                # First call succeeded — feed it into the normal loop
                last_response = first_response
                if first_response.has_tool_calls:
                    if first_response.response_id and first_response.response_id.startswith("resp_"):
                        session_state = {"previous_response_id": first_response.response_id}
                    messages = []
                    for tool_call in first_response.tool_calls:
                        args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                        logger.info(f"Tool call: {tool_call.name}({args_str[:200]})")
                        t_tool = time.monotonic()
                        result = await self.tools.execute(tool_call.name, tool_call.arguments)
                        messages = self.context.add_tool_result(
                            messages, tool_call.id, tool_call.name, result
                        )
                        tool_total += time.monotonic() - t_tool
                    iteration = 1  # Count this as first iteration
                else:
                    final_content = first_response.content
                    iteration = self.max_iterations  # Skip main loop

        while iteration < self.max_iterations:
            iteration += 1

            # Call LLM
            stream_state = None
            on_delta = None
            if self._should_stream(msg.channel):
                stream_state = self._create_stream_state(msg)
                on_delta = stream_state.on_delta

            t_llm = time.monotonic()
            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model,
                session_state=session_state,
                on_delta=on_delta,
            )
            last_response = response
            llm_time = time.monotonic() - t_llm
            llm_total += llm_time
            if stream_state:
                await stream_state.flush(final=not response.has_tool_calls)

            # Handle tool calls
            if response.has_tool_calls:
                if not native_mode:
                    # Add assistant message with tool calls
                    tool_call_dicts = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments)  # Must be JSON string
                            }
                        }
                        for tc in response.tool_calls
                    ]
                    messages = self.context.add_assistant_message(
                        messages, response.content, tool_call_dicts,
                        reasoning_content=response.reasoning_content,
                    )


                tool_time = 0.0
                if native_mode:
                    messages = []
                    if response.response_id and response.response_id.startswith("resp_"):
                        session_state = {"previous_response_id": response.response_id}
                for tool_call in response.tool_calls:
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info(f"Tool call: {tool_call.name}({args_str[:200]})")
                    t_tool = time.monotonic()
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
                    tool_time += time.monotonic() - t_tool
                tool_total += tool_time
            else:
                # No tool calls, we're done
                final_content = response.content
                if stream_state:
                    final_streamed = stream_state.sent_any
                break

        # When the agent exhausted iterations without producing a text reply,
        # make one final LLM call without tools to force a summary response.
        if final_content is None:
            logger.info(f"Max iterations reached without text reply, forcing summary call for {msg.channel}:{msg.sender_id}")
            stream_state = None
            on_delta = None
            if self._should_stream(msg.channel):
                stream_state = self._create_stream_state(msg)
                on_delta = stream_state.on_delta

            t_llm = time.monotonic()
            summary_response = await self.provider.chat(
                messages=messages,
                tools=[],
                model=self.model,
                session_state=session_state,
                on_delta=on_delta,
            )
            llm_total += time.monotonic() - t_llm
            if stream_state:
                await stream_state.flush(final=True)
                if stream_state.sent_any:
                    final_streamed = True

            final_content = summary_response.content or ""
            if summary_response.response_id and summary_response.response_id.startswith("resp_"):
                last_response = summary_response

        # Update session with LLM context info
        if last_response is not None:
            self.context_manager.update_after_response(session, last_response)

        # Log response preview
        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info(f"Response to {msg.channel}:{msg.sender_id}: {preview}")

        # Save to session
        t_save = time.monotonic()
        session.add_message("user", msg.content)
        # Persist any messages the agent sent via the message tool (e.g. file
        # attachments) as separate assistant entries so that catchUpMessages /
        # session reload on the web frontend can re-render them.
        _msg_tool = self.tools.get("message")
        if isinstance(_msg_tool, MessageTool):
            for sent in _msg_tool.drain_sent_messages():
                kwargs: dict[str, Any] = {}
                if sent.get("media"):
                    kwargs["media"] = sent["media"]
                session.add_message("assistant", sent["content"], **kwargs)
        if final_content:
            session.add_message("assistant", final_content)
        self.sessions.save(session)
        save_time = time.monotonic() - t_save

        total_time = time.monotonic() - t_start
        slow_threshold = 5.0
        try:
            slow_threshold = float(os.getenv("NANOBOT_SLOW_LOG_S", "5"))
        except ValueError:
            slow_threshold = 5.0

        def _fmt(val: float | None) -> str:
            if val is None:
                return "n/a"
            return f"{val:.3f}s"

        log_line = (
            f"Trace {trace_id} timings: inbound_wait={_fmt(inbound_wait)}, "
            f"context={_fmt(ctx_time)}, llm={_fmt(llm_total)}, tools={_fmt(tool_total)}, "
            f"save={_fmt(save_time)}, total={_fmt(total_time)}"
        )
        if total_time >= slow_threshold:
            logger.info(log_line)
        else:
            logger.debug(log_line)

        if final_streamed:
            return None

        out_metadata = dict(msg.metadata or {})
        out_metadata.setdefault("trace_id", trace_id)
        out_metadata.setdefault("_agent_total_s", round(total_time, 3))
        out_metadata.setdefault("_agent_llm_s", round(llm_total, 3))
        out_metadata.setdefault("_agent_tools_s", round(tool_total, 3))
        out_metadata.setdefault("_context_mode", ctx_stats.get("mode"))
        if ctx_stats.get("synced_reset") is not None:
            out_metadata.setdefault("_context_synced_reset", ctx_stats.get("synced_reset"))

        context_tokens = None
        context_ratio = None
        if last_response is not None and last_response.usage:
            prompt_tokens = last_response.usage.get("prompt_tokens") or last_response.usage.get("input_tokens")
            if isinstance(prompt_tokens, int):
                context_tokens = prompt_tokens
                window = max(1, int(self.context_config.window_tokens))
                reserve = max(0, int(self.context_config.reserve_tokens))
                effective = max(1, window - reserve)
                context_ratio = round(prompt_tokens / effective, 4)
                out_metadata.setdefault("_context_source", "usage")

        if context_tokens is None:
            context_tokens = ctx_stats.get("estimated_tokens")
            context_ratio = ctx_stats.get("estimated_ratio")
            out_metadata.setdefault("_context_source", "estimate")

        out_metadata.setdefault("_context_est_tokens", context_tokens)
        out_metadata.setdefault("_context_est_ratio", context_ratio)
        if ctx_stats.get("summarized"):
            out_metadata.setdefault("_context_summarized", True)

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            metadata=msg.metadata or {},  # Pass through for channel-specific needs (e.g. Slack thread_ts)
        )

    @staticmethod
    def _is_new_session_command(content: str) -> bool:
        if not content:
            return False
        first = content.strip().split()[0]
        if not first:
            return False
        if "@" in first:
            first = first.split("@", 1)[0]
        return first == "/new"

    async def _process_system_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a system message (e.g., subagent announce).

        The chat_id field contains "original_channel:original_chat_id" to route
        the response back to the correct destination.
        """
        logger.info(f"Processing system message from {msg.sender_id}")

        # Parse origin from chat_id (format: "channel:chat_id")
        if ":" in msg.chat_id:
            parts = msg.chat_id.split(":", 1)
            origin_channel = parts[0]
            origin_chat_id = parts[1]
        else:
            # Fallback
            origin_channel = "cli"
            origin_chat_id = msg.chat_id

        # Use the origin session for context
        session_key = f"{origin_channel}:{origin_chat_id}"
        session = self.sessions.get_or_create(session_key)

        # Update tool contexts
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(origin_channel, origin_chat_id)

        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(origin_channel, origin_chat_id)

        cron_tool = self.tools.get("cron")
        if isinstance(cron_tool, CronTool):
            cron_tool.set_context(origin_channel, origin_chat_id)

        ctx_bundle = await self.context_manager.build_context(
            session=session,
            current_message=msg.content,
            media=None,
            channel=origin_channel,
            chat_id=origin_chat_id,
        )
        messages = ctx_bundle.messages
        session_state = ctx_bundle.session_state
        ctx_stats = ctx_bundle.stats

        # Agent loop (limited for announce handling)
        iteration = 0
        final_content = None
        final_streamed = False

        last_response = None
        native_mode = ctx_stats.get("mode") == "native"

        while iteration < self.max_iterations:
            iteration += 1

            stream_state = None
            on_delta = None
            if self._should_stream(origin_channel):
                stream_state = _StreamState(
                    bus=self.bus,
                    channel=origin_channel,
                    chat_id=origin_chat_id,
                    base_metadata=msg.metadata,
                    min_chars=self.stream_min_chars,
                    min_interval_s=self.stream_min_interval_s,
                )
                on_delta = stream_state.on_delta

            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model,
                session_state=session_state,
                on_delta=on_delta,
            )
            last_response = response
            if stream_state:
                await stream_state.flush(final=not response.has_tool_calls)

            if response.has_tool_calls:
                if not native_mode:
                    tool_call_dicts = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments)
                            }
                        }
                        for tc in response.tool_calls
                    ]
                    messages = self.context.add_assistant_message(
                        messages, response.content, tool_call_dicts,
                        reasoning_content=response.reasoning_content,
                    )

                if native_mode:
                    messages = []
                    if response.response_id and response.response_id.startswith("resp_"):
                        session_state = {"previous_response_id": response.response_id}

                for tool_call in response.tool_calls:
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info(f"Tool call: {tool_call.name}({args_str[:200]})")
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
            else:
                final_content = response.content
                if stream_state:
                    final_streamed = stream_state.sent_any
                break

        # When the agent exhausted iterations without producing a text reply,
        # make one final LLM call without tools to force a summary response.
        if final_content is None:
            logger.info(f"Max iterations reached without text reply, forcing summary call for system:{msg.sender_id}")
            stream_state = None
            on_delta = None
            if self._should_stream(origin_channel):
                stream_state = _StreamState(
                    bus=self.bus,
                    channel=origin_channel,
                    chat_id=origin_chat_id,
                    base_metadata=msg.metadata,
                    min_chars=self.stream_min_chars,
                    min_interval_s=self.stream_min_interval_s,
                )
                on_delta = stream_state.on_delta

            summary_response = await self.provider.chat(
                messages=messages,
                tools=[],
                model=self.model,
                session_state=session_state,
                on_delta=on_delta,
            )
            if stream_state:
                await stream_state.flush(final=True)
                if stream_state.sent_any:
                    final_streamed = True

            final_content = summary_response.content or ""
            if summary_response.response_id and summary_response.response_id.startswith("resp_"):
                last_response = summary_response

        if last_response is not None:
            self.context_manager.update_after_response(session, last_response)

        # Save to session (mark as system message in history)
        session.add_message("user", f"[System: {msg.sender_id}] {msg.content}")
        if final_content:
            session.add_message("assistant", final_content)
        self.sessions.save(session)

        if final_streamed:
            return None

        return OutboundMessage(
            channel=origin_channel,
            chat_id=origin_chat_id,
            content=final_content
        )

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        return_message: bool = False,
    ) -> str | OutboundMessage:
        """
        Process a message directly (for CLI or cron usage).

        Args:
            content: The message content.
            session_key: Session identifier.
            channel: Source channel (for context).
            chat_id: Source chat ID (for context).
            return_message: If True, return the full OutboundMessage.

        Returns:
            The agent's response (string) or OutboundMessage.
        """
        msg = InboundMessage(
            channel=channel,
            sender_id="user",
            chat_id=chat_id,
            content=content
        )

        response = await self._process_message(msg)
        if return_message:
            return response
        return response.content if response else ""
