"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import hashlib
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
    def __init__(
        self,
        bus: MessageBus,
        channel: str,
        chat_id: str,
        base_metadata: dict[str, Any] | None,
        min_chars: int,
        min_interval_s: float,
    ):
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
        if not delta:
            return
        self.buffer += delta
        now = time.monotonic()
        if len(self.buffer) >= self.min_chars and (now - self.last_flush) >= self.min_interval_s:
            await self.flush(final=False)

    async def flush(self, final: bool) -> None:
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
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 20,
        temperature: float = 0.7,
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
        from nanobot.config.schema import ContextConfig, ExecToolConfig
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.temperature = temperature
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.stream = stream
        self.stream_min_chars = stream_min_chars
        self.stream_min_interval_s = stream_min_interval_s
        self.context_config = context_config or ContextConfig()
        self.restrict_to_workspace = restrict_to_workspace

        self.context = ContextBuilder(workspace)
        self.context_manager = ContextManager(
            provider=provider,
            config=self.context_config,
            builder=self.context,
            default_model=self.model,
        )
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            brave_api_key=brave_api_key,
            exec_config=self.exec_config,
            restrict_to_workspace=self.restrict_to_workspace,
        )

        self._running = False
        self._register_default_tools()

    def _register_default_tools(self) -> None:
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

    @staticmethod
    def _preview_text(value: Any, max_chars: int = 180) -> str:
        try:
            if isinstance(value, str):
                text = value
            else:
                text = json.dumps(value, ensure_ascii=False)
        except Exception:
            text = str(value)
        text = text.replace("\n", "\\n")
        if len(text) <= max_chars:
            return text
        return f"{text[:max_chars]}...(truncated)"

    def _summarize_content_for_log(self, content: Any) -> tuple[int, str]:
        if isinstance(content, str):
            return len(content), self._preview_text(content)
        if isinstance(content, list):
            block_types: list[str] = []
            text_samples: list[str] = []
            for item in content[:8]:
                if isinstance(item, dict):
                    item_type = str(item.get("type") or "dict")
                    block_types.append(item_type)
                    text_val = item.get("text")
                    if isinstance(text_val, str) and text_val:
                        text_samples.append(text_val)
                else:
                    block_types.append(type(item).__name__)
                    if isinstance(item, str):
                        text_samples.append(item)
            try:
                chars = len(json.dumps(content, ensure_ascii=False))
            except Exception:
                chars = len(str(content))
            preview = f"list[{len(content)}] block_types={block_types}"
            if text_samples:
                preview += f" sample={self._preview_text(' '.join(text_samples), max_chars=120)}"
            return chars, preview
        if isinstance(content, dict):
            try:
                serialized = json.dumps(content, ensure_ascii=False)
            except Exception:
                serialized = str(content)
            return len(serialized), self._preview_text(serialized)
        text = str(content)
        return len(text), self._preview_text(text)

    @staticmethod
    def _tool_schema_name(tool_schema: dict[str, Any]) -> str:
        """Extract a tool name from OpenAI-style tool schema."""
        if not isinstance(tool_schema, dict):
            return ""
        function_def = tool_schema.get("function")
        if isinstance(function_def, dict):
            return str(function_def.get("name") or "")
        return str(tool_schema.get("name") or "")

    @staticmethod
    def _has_tool_messages(messages: list[dict[str, Any]]) -> bool:
        """Return True when context already contains at least one tool output."""
        for message in messages:
            if str(message.get("role") or "").lower() == "tool":
                return True
        return False

    @staticmethod
    def _hash_text(value: str) -> str:
        """Return a short stable hash for potentially large text blobs."""
        return hashlib.sha1((value or "").encode("utf-8", errors="replace")).hexdigest()[:16]

    def _select_iteration_tools(
        self,
        matched_skills: list[str],
        has_tool_results: bool = False,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """
        Select visible tools and tool-choice policy for this iteration.

        Skill-driven behavior:
        - No matched skill: expose all tools, keep tool_choice=auto.
        - Matched skills without tool results yet: enforce one tool call (tool_choice=required).
        - After at least one tool result exists: switch back to auto so the model can finalize.
        - If skills provide metadata.nanobot.allowed_tools, restrict tool set.
        """
        all_tools = self.tools.get_definitions()
        if not matched_skills:
            return all_tools, ("auto" if all_tools else None)

        choice = "auto" if has_tool_results else "required"
        allowed = self.context.skills.get_allowed_tools_for_skills(matched_skills)
        if not allowed:
            return all_tools, (choice if all_tools else None)

        allowed_set = {name for name in allowed if isinstance(name, str) and name}
        scoped_tools = [
            schema
            for schema in all_tools
            if self._tool_schema_name(schema) in allowed_set
        ]
        if scoped_tools:
            return scoped_tools, choice

        logger.warning(
            "Skill-matched request had allowed_tools metadata but none were registered; "
            f"matched_skills={matched_skills} allowed_tools={allowed}. Falling back to full toolset."
        )
        return all_tools, (choice if all_tools else None)

    def _log_messages_for_trace(
        self,
        trace_id: str,
        stage: str,
        messages: list[dict[str, Any]],
    ) -> None:
        logger.debug(
            f"Trace {trace_id} {stage} messages_snapshot count={len(messages)}"
        )
        for idx, message in enumerate(messages):
            role = message.get("role")
            content_chars, preview = self._summarize_content_for_log(message.get("content"))
            tool_calls = message.get("tool_calls") or []
            logger.debug(
                f"Trace {trace_id} {stage} msg[{idx}] role={role} "
                f"content_chars={content_chars} tool_calls={len(tool_calls)} "
                f"keys={list(message.keys())} preview={preview}"
            )

    def _log_response_for_trace(
        self,
        trace_id: str,
        stage: str,
        response: Any,
    ) -> None:
        if response is None:
            logger.debug(f"Trace {trace_id} {stage} response is None")
            return
        content_chars, content_preview = self._summarize_content_for_log(response.content)
        usage = response.usage or {}
        logger.debug(
            f"Trace {trace_id} {stage} response_summary finish={response.finish_reason} "
            f"tool_calls={len(response.tool_calls)} content_chars={content_chars} "
            f"response_id={response.response_id or 'n/a'} conversation_id={response.conversation_id or 'n/a'} "
            f"model={response.model or 'n/a'} usage={usage}"
        )
        if response.reasoning_content:
            logger.debug(
                f"Trace {trace_id} {stage} reasoning_chars={len(response.reasoning_content)} "
                f"reasoning_preview={self._preview_text(response.reasoning_content)}"
            )
        for idx, tool_call in enumerate(response.tool_calls):
            args_preview = self._preview_text(tool_call.arguments)
            logger.debug(
                f"Trace {trace_id} {stage} tool_call[{idx}] id={tool_call.id} "
                f"name={tool_call.name} args_preview={args_preview}"
            )
        if response.content:
            logger.debug(
                f"Trace {trace_id} {stage} content_preview={content_preview}"
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
        trace_id = None
        if isinstance(msg.metadata, dict):
            trace_id = msg.metadata.get("trace_id")
        if not trace_id:
            trace_id = f"{msg.channel}-{int(time.time() * 1000)}"

        metadata_keys = sorted(list((msg.metadata or {}).keys()))
        logger.debug(
            f"Trace {trace_id} inbound received channel={msg.channel} chat_id={msg.chat_id} "
            f"sender={msg.sender_id} session_key={msg.session_key} "
            f"chars={len(msg.content)} media={len(msg.media or [])} metadata_keys={metadata_keys}"
        )

        # Handle system messages (subagent announces)
        # The chat_id contains the original "channel:chat_id" to route back to
        if msg.channel == "system":
            return await self._process_system_message(msg)

        if self._is_new_session_command(msg.content):
            logger.info(
                f"Trace {trace_id} new-session command detected channel={msg.channel} "
                f"chat_id={msg.chat_id} base_session_key={msg.session_key}"
            )
            session = self.sessions.start_new(msg.session_key)
            self.sessions.save(session)
            logger.info(
                f"Trace {trace_id} new-session command completed session_key={session.key} "
                f"messages={len(session.messages)}"
            )
            out_meta = dict(msg.metadata or {})
            out_meta.setdefault("trace_id", trace_id)
            out_meta.setdefault("session_key", session.key)
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="⚛ 新会话已就绪～有什么需要我做的吗？",
                metadata=out_meta,
            )

        if self._is_help_command(msg.content):
            out_meta = dict(msg.metadata or {})
            out_meta.setdefault("trace_id", trace_id)
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="🐈 nanobot commands:\n/new — Start a new conversation\n/help — Show available commands",
                metadata=out_meta,
            )

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info(f"Trace {trace_id} processing message from {msg.channel}:{msg.sender_id}: {preview}")

        t_start = time.monotonic()
        inbound_wait = None
        received_at = msg.metadata.get("_received_at") if isinstance(msg.metadata, dict) else None
        if isinstance(received_at, (int, float)):
            inbound_wait = t_start - received_at

        # Get or create session
        session_key_source = "metadata.override" if (msg.metadata or {}).get("session_key") else "default"
        logger.debug(
            f"Trace {trace_id} session resolve requested_key={msg.session_key} "
            f"source={session_key_source}"
        )
        session = self.sessions.get_or_create(msg.session_key)
        logger.debug(
            f"Trace {trace_id} session resolved key={session.key} "
            f"history_messages={len(session.messages)} metadata_keys={list(session.metadata.keys())}"
        )

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
        logger.debug(
            f"Trace {trace_id} context built mode={ctx_stats.get('mode')} "
            f"messages={len(messages)} session_state={'yes' if session_state else 'no'} "
            f"elapsed={ctx_time:.3f}s"
        )
        logger.debug(
            f"Trace {trace_id} context stats details={ctx_stats} "
            f"session_state_keys={list((session_state or {}).keys()) if isinstance(session_state, dict) else []}"
        )
        self._log_messages_for_trace(trace_id, "context-ready", messages)

        # Agent loop
        iteration = 0
        final_content = None
        final_streamed = False
        llm_total = 0.0
        tool_total = 0.0

        last_response = None
        native_mode = ctx_stats.get("mode") == "native"
        matched_skills = [
            skill for skill in (ctx_stats.get("matched_skills") or [])
            if isinstance(skill, str) and skill
        ]
        skill_enforcement_attempted = False
        last_tool_round_fingerprint: str | None = None
        stagnant_tool_rounds = 0
        raw_stagnation_limit = int(getattr(self.context_config, "skill_tool_stagnation_limit", 0) or 0)
        stagnation_limit = max(0, raw_stagnation_limit)

        # Native session recovery: if first LLM call in native mode fails,
        # clear stale previous_response_id and retry with full context (reset mode).
        if native_mode:
            probe_tools, probe_tool_choice = self._select_iteration_tools(
                matched_skills, has_tool_results=False
            )
            probe_tool_names = [
                (tool.get("function", {}) or {}).get("name") or tool.get("name") or "unknown"
                for tool in probe_tools
            ]
            logger.debug(
                f"Trace {trace_id} native probe request model={self.model} "
                f"messages={len(messages)} tools={len(probe_tools)} "
                f"tool_names={probe_tool_names} tool_choice={probe_tool_choice or 'none'} "
                f"session_state={session_state or {}}"
            )
            self._log_messages_for_trace(trace_id, "native-probe request", messages)
            t_llm = time.monotonic()
            first_response = await self.provider.chat(
                messages=messages,
                tools=probe_tools,
                tool_choice=probe_tool_choice,
                model=self.model,
                session_state=session_state,
                on_delta=None,  # No streaming on probe
            )
            llm_time = time.monotonic() - t_llm
            llm_total += llm_time
            self._log_response_for_trace(trace_id, "native-probe response", first_response)
            logger.debug(
                f"Trace {trace_id} native probe finish={first_response.finish_reason} "
                f"tool_calls={len(first_response.tool_calls)} llm={llm_time:.3f}s"
            )

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
                logger.debug(
                    f"Trace {trace_id} native reset rebuild context mode={ctx_stats.get('mode')} "
                    f"session_state={session_state or {}} stats={ctx_stats}"
                )
                self._log_messages_for_trace(trace_id, "native-reset context", messages)
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
                        tool_elapsed = time.monotonic() - t_tool
                        tool_total += tool_elapsed
                        logger.debug(
                            f"Trace {trace_id} native probe tool={tool_call.name} "
                            f"elapsed={tool_elapsed:.3f}s result_chars={len(result)}"
                        )
                    iteration = 1  # Count this as first iteration
                    self._log_messages_for_trace(trace_id, "native-probe post-tools", messages)
                else:
                    final_content = first_response.content
                    iteration = self.max_iterations  # Skip main loop

        while iteration < self.max_iterations:
            iteration += 1
            logger.debug(f"Trace {trace_id} iteration={iteration} start")

            # Call LLM
            stream_state = None
            on_delta = None
            if self._should_stream(msg.channel):
                stream_state = self._create_stream_state(msg)
                on_delta = stream_state.on_delta

            iter_tools, iter_tool_choice = self._select_iteration_tools(
                matched_skills, has_tool_results=self._has_tool_messages(messages)
            )
            iter_tool_names = [
                (tool.get("function", {}) or {}).get("name") or tool.get("name") or "unknown"
                for tool in iter_tools
            ]
            logger.debug(
                f"Trace {trace_id} iteration={iteration} llm request model={self.model} "
                f"messages={len(messages)} tools={len(iter_tools)} tool_names={iter_tool_names} "
                f"tool_choice={iter_tool_choice or 'none'} "
                f"session_state={session_state or {}} stream={on_delta is not None}"
            )
            self._log_messages_for_trace(trace_id, f"iteration={iteration} request", messages)
            t_llm = time.monotonic()
            response = await self.provider.chat(
                messages=messages,
                tools=iter_tools,
                tool_choice=iter_tool_choice,
                model=self.model,
                session_state=session_state,
                on_delta=on_delta,
                temperature=self.temperature
            )
            last_response = response
            llm_time = time.monotonic() - t_llm
            llm_total += llm_time
            self._log_response_for_trace(trace_id, f"iteration={iteration} response", response)
            logger.debug(
                f"Trace {trace_id} iteration={iteration} llm finish={response.finish_reason} "
                f"tool_calls={len(response.tool_calls)} llm={llm_time:.3f}s"
            )
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
                round_signatures: list[str] = []
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
                    try:
                        args_signature = json.dumps(
                            tool_call.arguments, ensure_ascii=False, sort_keys=True
                        )
                    except Exception:
                        args_signature = str(tool_call.arguments)
                    round_signatures.append(
                        f"{tool_call.name}:{args_signature}:{self._hash_text(result)}"
                    )
                    tool_elapsed = time.monotonic() - t_tool
                    logger.debug(
                        f"Trace {trace_id} iteration={iteration} tool={tool_call.name} "
                        f"elapsed={tool_elapsed:.3f}s result_chars={len(result)}"
                    )
                    tool_time += tool_elapsed
                tool_total += tool_time
                logger.debug(
                    f"Trace {trace_id} iteration={iteration} tools_total={tool_time:.3f}s "
                    f"running_tools={tool_total:.3f}s"
                )
                self._log_messages_for_trace(trace_id, f"iteration={iteration} post-tools", messages)

                if stagnation_limit > 0 and round_signatures:
                    round_fingerprint = "||".join(round_signatures)
                    if round_fingerprint == last_tool_round_fingerprint:
                        stagnant_tool_rounds += 1
                    else:
                        last_tool_round_fingerprint = round_fingerprint
                        stagnant_tool_rounds = 0

                    if stagnant_tool_rounds >= stagnation_limit:
                        logger.warning(
                            f"Trace {trace_id} detected tool stagnation: identical "
                            f"tool-call+result rounds repeated {stagnant_tool_rounds} times "
                            f"(limit={stagnation_limit}). Forcing summary."
                        )
                        break
            else:
                # No tool calls, we're done
                if (
                    matched_skills
                    and not skill_enforcement_attempted
                    and bool(self.context_config.skill_enforcement_retry)
                    and stream_state is None
                ):
                    skill_enforcement_attempted = True
                    logger.debug(
                        f"Trace {trace_id} iteration={iteration} matched_skills={matched_skills} "
                        "no tool calls from LLM; enforcing one retry"
                    )
                    if not native_mode:
                        messages = self.context.add_assistant_message(
                            messages,
                            response.content,
                            reasoning_content=response.reasoning_content,
                        )
                        messages.append(
                            {
                                "role": "system",
                                "content": (
                                    "Skill enforcement retry: the request matched skills "
                                    f"{', '.join(matched_skills)}. Before finalizing your answer, "
                                    "call the necessary tools to execute the matched skill workflow. "
                                    "Do not provide estimated real-time facts without tool results."
                                ),
                            }
                        )
                    else:
                        messages = [
                            {
                                "role": "user",
                                "content": (
                                    "Skill enforcement retry. Matched skills: "
                                    f"{', '.join(matched_skills)}. "
                                    "Before finalizing, call required tools for the matched skill workflow. "
                                    "Do not estimate real-time facts."
                                ),
                            }
                        ]
                    continue

                final_content = response.content
                if stream_state:
                    final_streamed = stream_state.sent_any
                logger.debug(
                    f"Trace {trace_id} iteration={iteration} finalized "
                    f"content_chars={len(final_content or '')}"
                )
                break

        # When the agent exhausted iterations without producing a text reply,
        # make one final LLM call without tools to force a summary response.
        if final_content is None:
            logger.info(
                f"Trace {trace_id} max iterations reached without text reply, "
                f"forcing summary call for {msg.channel}:{msg.sender_id}"
            )
            stream_state = None
            on_delta = None
            if self._should_stream(msg.channel):
                stream_state = self._create_stream_state(msg)
                on_delta = stream_state.on_delta

            logger.debug(
                f"Trace {trace_id} forced-summary request model={self.model} "
                f"messages={len(messages)} session_state={session_state or {}} "
                f"stream={on_delta is not None}"
            )
            self._log_messages_for_trace(trace_id, "forced-summary request", messages)
            t_llm = time.monotonic()
            summary_response = await self.provider.chat(
                messages=messages,
                tools=[],
                model=self.model,
                session_state=session_state,
                on_delta=on_delta,
            )
            llm_total += time.monotonic() - t_llm
            self._log_response_for_trace(trace_id, "forced-summary response", summary_response)
            logger.debug(
                f"Trace {trace_id} forced summary call done "
                f"elapsed={(time.monotonic() - t_llm):.3f}s"
            )
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

        if final_content is None:
            if iteration >= self.max_iterations:
                final_content = f"Reached {self.max_iterations} iterations without completion."
            else:
                final_content = "I've completed processing but have no response to give."

        # Log response preview
        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info(f"Trace {trace_id} response to {msg.channel}:{msg.sender_id}: {preview}")

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
        logger.debug(
            f"Trace {trace_id} session save done messages={len(session.messages)} "
            f"session_key={session.key} metadata_keys={list(session.metadata.keys())} "
            f"elapsed={save_time:.3f}s"
        )

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
            logger.debug(f"Trace {trace_id} final response already streamed, skip outbound message object")
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

        logger.debug(
            f"Trace {trace_id} outbound payload prepared channel={msg.channel} chat_id={msg.chat_id} "
            f"content_chars={len(final_content or '')} metadata_keys={sorted(out_metadata.keys())} "
            f"context_source={out_metadata.get('_context_source')} "
            f"context_mode={out_metadata.get('_context_mode')}"
        )

        outbound = OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            metadata=out_metadata,  # Preserve passthrough metadata and add timing/context stats
        )

        return outbound

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

    @staticmethod
    def _is_help_command(content: str) -> bool:
        if not content:
            return False
        first = content.strip().split()[0]
        if not first:
            return False
        if "@" in first:
            first = first.split("@", 1)[0]
        return first == "/help"

    async def _process_system_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a system message (e.g., subagent announce).

        The chat_id field contains "original_channel:original_chat_id" to route
        the response back to the correct destination.
        """
        trace_id = None
        if isinstance(msg.metadata, dict):
            trace_id = msg.metadata.get("trace_id")
        if not trace_id:
            trace_id = f"system-{int(time.time() * 1000)}"

        logger.info(f"Trace {trace_id} processing system message from {msg.sender_id}")
        t_start = time.monotonic()

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
        logger.debug(
            f"Trace {trace_id} system origin resolved origin_channel={origin_channel} "
            f"origin_chat_id={origin_chat_id} session_key={session_key}"
        )
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
        logger.debug(
            f"Trace {trace_id} system context built mode={ctx_stats.get('mode')} "
            f"messages={len(messages)} session_state={'yes' if session_state else 'no'} stats={ctx_stats}"
        )
        self._log_messages_for_trace(trace_id, "system context-ready", messages)

        # Agent loop (limited for announce handling)
        iteration = 0
        final_content = None
        final_streamed = False

        last_response = None
        native_mode = ctx_stats.get("mode") == "native"

        while iteration < self.max_iterations:
            iteration += 1
            logger.debug(f"Trace {trace_id} system iteration={iteration} start sender={msg.sender_id}")

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

            iter_tools = self.tools.get_definitions()
            logger.debug(
                f"Trace {trace_id} system iteration={iteration} llm request "
                f"messages={len(messages)} tools={len(iter_tools)} "
                f"session_state={session_state or {}} stream={on_delta is not None}"
            )
            self._log_messages_for_trace(trace_id, f"system iteration={iteration} request", messages)
            response = await self.provider.chat(
                messages=messages,
                tools=iter_tools,
                model=self.model,
                session_state=session_state,
                on_delta=on_delta,
                temperature=self.temperature
            )
            last_response = response
            self._log_response_for_trace(trace_id, f"system iteration={iteration} response", response)
            logger.debug(
                f"Trace {trace_id} system iteration={iteration} finish={response.finish_reason} "
                f"tool_calls={len(response.tool_calls)}"
            )
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
                    t_tool = time.monotonic()
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
                    logger.debug(
                        f"Trace {trace_id} system iteration={iteration} tool={tool_call.name} "
                        f"elapsed={(time.monotonic() - t_tool):.3f}s result_chars={len(result)}"
                    )
                self._log_messages_for_trace(trace_id, f"system iteration={iteration} post-tools", messages)
            else:
                final_content = response.content
                if stream_state:
                    final_streamed = stream_state.sent_any
                logger.debug(
                    f"Trace {trace_id} system iteration={iteration} finalized "
                    f"content_chars={len(final_content or '')}"
                )
                break

        # When the agent exhausted iterations without producing a text reply,
        # make one final LLM call without tools to force a summary response.
        if final_content is None:
            logger.info(
                f"Trace {trace_id} system max iterations reached without text reply, "
                f"forcing summary call for system:{msg.sender_id}"
            )
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

            t_summary = time.monotonic()
            self._log_messages_for_trace(trace_id, "system forced-summary request", messages)
            summary_response = await self.provider.chat(
                messages=messages,
                tools=[],
                model=self.model,
                session_state=session_state,
                on_delta=on_delta,
            )
            self._log_response_for_trace(trace_id, "system forced-summary response", summary_response)
            logger.debug(
                f"Trace {trace_id} system forced summary call elapsed={(time.monotonic() - t_summary):.3f}s "
                f"sender={msg.sender_id}"
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
        logger.debug(
            f"Trace {trace_id} system complete sender={msg.sender_id} "
            f"elapsed={(time.monotonic() - t_start):.3f}s"
        )

        if final_streamed:
            return None

        out_metadata = dict(msg.metadata or {})
        out_metadata.setdefault("trace_id", trace_id)
        return OutboundMessage(
            channel=origin_channel,
            chat_id=origin_chat_id,
            content=final_content,
            metadata=out_metadata,
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
        t_start = time.monotonic()
        msg = InboundMessage(
            channel=channel,
            sender_id="user",
            chat_id=chat_id,
            content=content
        )

        response = await self._process_message(msg)
        logger.debug(
            f"Direct process session={session_key} channel={channel} "
            f"elapsed={(time.monotonic() - t_start):.3f}s"
        )
        if return_message:
            return response
        return response.content if response else ""
