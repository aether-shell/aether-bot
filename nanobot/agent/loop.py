"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nanobot.config.schema import ContextConfig, ExecToolConfig, WebSearchConfig
    from nanobot.cron.service import CronService

    ExecToolConfigT = ExecToolConfig
    ContextConfigT = ContextConfig
    WebSearchConfigT = WebSearchConfig
else:
    ExecToolConfigT = Any
    ContextConfigT = Any
    WebSearchConfigT = Any

from loguru import logger

from nanobot.agent.context import ContextBuilder
from nanobot.agent.context_manager import ContextManager
from nanobot.agent.memory import MemoryStore
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
from nanobot.session.manager import Session, SessionManager

_REALTIME_QUERY_HINTS = re.compile(
    (
        r"(今天|今日|最新|刚刚|实时|新闻|头条|突发|当下|现在|目前|"
        r"搜索|查一下|查一查|查查|链接|来源|source|sources?|"
        r"today|latest|breaking|news|headline|current|now|live|price|quote|score|schedule)"
    ),
    re.IGNORECASE,
)

_ATTACHMENT_REQUEST_HINTS = re.compile(
    (
        r"(作为文件|发文件|发送文件|附件|把.*文件发给我|发给我.*文件|上传文件|"
        r"send (?:me )?(?:the )?(?:file|document)|"
        r"(?:send|share) .* as (?:a )?(?:file|attachment)|"
        r"attachment|attach(?:ed|ment)?)"
    ),
    re.IGNORECASE,
)

_ATTACHMENT_SENT_CLAIMS = re.compile(
    r"(已发|已发送|发你了|附件就是|sent|attached|uploaded)",
    re.IGNORECASE,
)

_ATTACHMENT_ACK_HINTS = re.compile(
    r"(查收|已发|已发送|发你了|sent|attached|uploaded)",
    re.IGNORECASE,
)

_ATTACHMENT_FILE_TOKEN = re.compile(
    (
        r"(?<![\w/.-])"
        r"([A-Za-z0-9_./-]+\.(?:md|txt|pdf|docx?|csv|xlsx?|xls|zip|json|png|jpe?g|gif|webp))"
        r"(?![\w/.-])"
    ),
    re.IGNORECASE,
)

_TRANSIENT_MEMORY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b[A-Z][A-Z0-9_]*_API_KEY\b.*\bnot configured\b", re.IGNORECASE),
    re.compile(r"\bBRAVE_API_KEY\b", re.IGNORECASE),
    re.compile(r"\bTAVILY_API_KEY\b", re.IGNORECASE),
    re.compile(r"\bSEARXNG_BASE_URL\b", re.IGNORECASE),
    re.compile(r"\bOPENAI_API_KEY\b", re.IGNORECASE),
    re.compile(r"\bnot configured\b", re.IGNORECASE),
    re.compile(r"\bcannot (?:access|reach) internet\b", re.IGNORECASE),
    re.compile(r"\bnetwork (?:error|timeout|unavailable)\b", re.IGNORECASE),
    re.compile(r"\bweb_search failed\b", re.IGNORECASE),
)


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
        max_tokens: int = 4096,
        memory_window: int = 50,
        brave_api_key: str | None = None,
        web_search_config: WebSearchConfigT | None = None,
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
        self.max_tokens = max_tokens
        self.memory_window = memory_window
        self.brave_api_key = brave_api_key
        self.web_search_config = web_search_config
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
        self._memory_consolidation_inflight: set[str] = set()
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            brave_api_key=brave_api_key,
            web_search_config=web_search_config,
            exec_config=self.exec_config,
            restrict_to_workspace=self.restrict_to_workspace,
        )

        self._running = False
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        # File tools (restrict to workspace if configured)
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        self.tools.register(ReadFileTool(allowed_dir=allowed_dir, base_dir=self.workspace))
        self.tools.register(WriteFileTool(allowed_dir=allowed_dir, base_dir=self.workspace))
        self.tools.register(EditFileTool(allowed_dir=allowed_dir, base_dir=self.workspace))
        self.tools.register(ListDirTool(allowed_dir=allowed_dir, base_dir=self.workspace))

        # Shell tool
        self.tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=self.exec_config.timeout,
            restrict_to_workspace=self.restrict_to_workspace,
        ))

        # Web tools
        self.tools.register(
            WebSearchTool.from_config(
                self.web_search_config,
                legacy_brave_api_key=self.brave_api_key,
            )
        )
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
    def _is_realtime_query(message: str) -> bool:
        """Heuristic: whether the prompt likely needs live verification."""
        text = (message or "").strip()
        if not text:
            return False
        return bool(_REALTIME_QUERY_HINTS.search(text))

    @staticmethod
    def _is_attachment_delivery_request(message: str) -> bool:
        """Heuristic: whether the user is asking to deliver a real file attachment."""
        text = (message or "").strip()
        if not text:
            return False
        return bool(_ATTACHMENT_REQUEST_HINTS.search(text))

    @staticmethod
    def _claims_attachment_sent(message: str) -> bool:
        """Heuristic: whether assistant text claims a file was already sent."""
        text = (message or "").strip()
        if not text:
            return False
        return bool(_ATTACHMENT_SENT_CLAIMS.search(text))

    @staticmethod
    def _is_redundant_attachment_ack(message: str) -> bool:
        """Whether a short follow-up message is only a duplicate attachment ack."""
        text = (message or "").strip()
        if not text:
            return False
        if len(text) > 80:
            return False
        if not AgentLoop._claims_attachment_sent(text):
            return False
        if _ATTACHMENT_FILE_TOKEN.search(text):
            return False
        return bool(_ATTACHMENT_ACK_HINTS.search(text))

    @staticmethod
    def _extract_attachment_candidates(*texts: str) -> list[str]:
        """Extract likely file/path tokens from free text."""
        candidates: list[str] = []
        for text in texts:
            value = str(text or "")
            if not value:
                continue

            # Quoted/backticked segments often contain exact paths.
            for pattern in (r"`([^`]+)`", r'"([^"]+)"', r"'([^']+)'"):
                for match in re.finditer(pattern, value):
                    token = (match.group(1) or "").strip()
                    if token:
                        candidates.append(token)

            # Bare file-like tokens.
            for match in _ATTACHMENT_FILE_TOKEN.finditer(value):
                token = (match.group(1) or "").strip()
                if token:
                    candidates.append(token)

        deduped: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            key = item.strip()
            if not key:
                continue
            if key in seen:
                continue
            seen.add(key)
            deduped.append(key)
        return deduped

    def _resolve_attachment_candidate(self, candidate: str) -> str | None:
        """Resolve a candidate file token to an existing local file path."""
        raw = (candidate or "").strip().strip("`'\"").strip()
        if not raw:
            return None

        token_path = Path(raw).expanduser()
        checks: list[Path] = []
        if token_path.is_absolute():
            checks.append(token_path)
        else:
            checks.append((self.workspace / token_path).resolve())
            checks.append((self.workspace / "memory" / "learnings" / token_path.name).resolve())
            checks.append((self.workspace / "memory" / token_path.name).resolve())
            checks.append((self.workspace / token_path.name).resolve())

        for path in checks:
            try:
                if path.exists() and path.is_file():
                    return str(path)
            except OSError:
                continue
        return None

    def _infer_attachment_media_paths(
        self,
        user_content: str,
        assistant_content: str,
        max_items: int = 3,
    ) -> list[str]:
        """Infer existing local file paths from user/assistant text."""
        resolved: list[str] = []
        for candidate in self._extract_attachment_candidates(user_content, assistant_content):
            path_str = self._resolve_attachment_candidate(candidate)
            if not path_str:
                continue
            if path_str in resolved:
                continue
            resolved.append(path_str)
            if len(resolved) >= max_items:
                break
        return resolved

    @staticmethod
    def _contains_transient_memory_issue(text: str) -> bool:
        value = (text or "").strip()
        if not value:
            return False
        return any(pattern.search(value) for pattern in _TRANSIENT_MEMORY_PATTERNS)

    def _sanitize_history_entry(self, entry: str) -> str:
        """
        Remove transient environment failures from history summaries.

        Keeps stable user intent/preferences while dropping volatile runtime errors.
        """
        text = (entry or "").strip()
        if not text:
            return ""
        parts = [
            segment.strip()
            for segment in re.split(r"(?<=[。！？.!?])\s+", text)
            if segment.strip()
        ]
        kept = [segment for segment in parts if not self._contains_transient_memory_issue(segment)]
        return " ".join(kept).strip()

    def _sanitize_memory_update(self, memory_update: str) -> str:
        """Drop volatile environment diagnostics from MEMORY.md updates."""
        text = (memory_update or "").strip()
        if not text:
            return ""
        kept_lines: list[str] = []
        for line in text.splitlines():
            if self._contains_transient_memory_issue(line):
                continue
            kept_lines.append(line)
        cleaned = "\n".join(kept_lines).strip()
        return cleaned

    @staticmethod
    def _hash_text(value: str) -> str:
        """Return a short stable hash for potentially large text blobs."""
        return hashlib.sha1((value or "").encode("utf-8", errors="replace")).hexdigest()[:16]

    @staticmethod
    def _canonical_tool_arguments(arguments: Any) -> str:
        """Return a stable, comparable representation for tool arguments."""
        try:
            return json.dumps(arguments, ensure_ascii=False, sort_keys=True)
        except Exception:
            return str(arguments)

    @staticmethod
    def _workflow_retry_limit(policy: dict[str, Any]) -> int:
        """Return retry count from merged workflow policy."""
        retry_cfg = policy.get("retry") if isinstance(policy, dict) else None
        if not isinstance(retry_cfg, dict):
            return 0
        try:
            return max(0, int(retry_cfg.get("enforcement_retries") or 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _workflow_failure_mode(policy: dict[str, Any]) -> str:
        retry_cfg = policy.get("retry") if isinstance(policy, dict) else None
        if not isinstance(retry_cfg, dict):
            return "explain_missing"
        mode = str(retry_cfg.get("failure_mode") or "explain_missing").strip().lower()
        return mode if mode in {"explain_missing", "hard_fail"} else "explain_missing"

    @staticmethod
    def _workflow_rule_matches_event(rule: dict[str, Any], event: dict[str, Any]) -> bool:
        """Return True when a tool event satisfies one workflow rule."""
        if not isinstance(rule, dict) or not isinstance(event, dict):
            return False
        rule_name = str(rule.get("name") or "").strip().lower()
        event_name = str(event.get("name") or "").strip().lower()
        if not rule_name or rule_name != event_name:
            return False

        args_matchers = rule.get("args")
        if not isinstance(args_matchers, dict) or not args_matchers:
            return True

        event_args = event.get("arguments")
        if not isinstance(event_args, dict):
            return False

        for matcher_key, matcher_value in args_matchers.items():
            matcher_key_text = str(matcher_key).strip()
            if not matcher_key_text:
                continue
            matcher_text = str(matcher_value or "")

            if matcher_key_text.endswith("_regex"):
                arg_name = matcher_key_text[:-6]
                candidate = str(event_args.get(arg_name, ""))
                try:
                    if not re.search(matcher_text, candidate):
                        return False
                except re.error:
                    return False
            else:
                candidate = event_args.get(matcher_key_text)
                if str(candidate) != matcher_text:
                    return False
        return True

    @staticmethod
    def _workflow_tool_rule_label(rule: dict[str, Any]) -> str:
        """Render a compact human-readable workflow rule summary."""
        if not isinstance(rule, dict):
            return "invalid_tool_rule"
        name = str(rule.get("name") or "unknown")
        args = rule.get("args")
        if not isinstance(args, dict) or not args:
            return f"{name}()"
        parts: list[str] = []
        for key, value in args.items():
            parts.append(f"{key}={value}")
        return f"{name}({', '.join(parts)})"

    def _validate_workflow_requirements(
        self,
        policy: dict[str, Any],
        tool_events: list[dict[str, Any]],
        draft_content: str | None,
    ) -> list[str]:
        """
        Validate workflow requirements against current tool events and draft output.
        Returns missing requirement messages (empty means pass).
        """
        if not policy:
            return []

        missing: list[str] = []

        kickoff = policy.get("kickoff")
        if isinstance(kickoff, dict):
            require_substantive = bool(kickoff.get("require_substantive_action"))
            substantive_tools = [
                str(name).strip().lower()
                for name in kickoff.get("substantive_tools", [])
                if str(name).strip()
            ]
            forbid_first = {
                str(name).strip().lower()
                for name in kickoff.get("forbid_as_first_only", [])
                if str(name).strip()
            }

            first_tool_name = ""
            if tool_events:
                first_tool_name = str(tool_events[0].get("name") or "").strip().lower()

            substantive_seen = False
            if substantive_tools:
                substantive_seen = any(
                    str(event.get("name") or "").strip().lower() in substantive_tools
                    for event in tool_events
                )
            elif tool_events:
                substantive_seen = True

            if require_substantive and not substantive_seen:
                if substantive_tools:
                    missing.append(
                        "missing substantive tool action from: "
                        + ", ".join(sorted(set(substantive_tools)))
                    )
                else:
                    missing.append("missing substantive tool action")

            if require_substantive and first_tool_name and first_tool_name in forbid_first:
                missing.append(f"first tool call `{first_tool_name}` is disallowed for kickoff")

        completion = policy.get("completion")
        if isinstance(completion, dict):
            raw_rules = completion.get("require_tool_calls")
            if isinstance(raw_rules, list):
                for raw_rule in raw_rules:
                    if not isinstance(raw_rule, dict):
                        continue
                    matched = any(
                        self._workflow_rule_matches_event(raw_rule, event)
                        for event in tool_events
                    )
                    if not matched:
                        missing.append(
                            "required tool call not satisfied: "
                            + self._workflow_tool_rule_label(raw_rule)
                        )

        progress = policy.get("progress")
        if isinstance(progress, dict) and bool(progress.get("claim_requires_actions")):
            claim_patterns = [
                str(p).strip()
                for p in progress.get("claim_patterns", [])
                if str(p).strip()
            ]
            text = str(draft_content or "")
            claim_found = False
            if claim_patterns and text:
                lowered = text.lower()
                claim_found = any(pattern.lower() in lowered for pattern in claim_patterns)
            elif text:
                claim_found = True

            if claim_found:
                substantive_tools = [
                    str(name).strip().lower()
                    for name in (policy.get("kickoff", {}) or {}).get("substantive_tools", [])
                    if str(name).strip()
                ]
                if substantive_tools:
                    substantive_seen = any(
                        str(event.get("name") or "").strip().lower() in substantive_tools
                        for event in tool_events
                    )
                else:
                    substantive_seen = bool(tool_events)
                if not substantive_seen:
                    missing.append("progress/completion claim present without substantive actions")

        return missing

    @staticmethod
    def _format_workflow_missing(missing: list[str]) -> str:
        if not missing:
            return ""
        lines = "\n".join(f"- {item}" for item in missing)
        return lines

    def _apply_workflow_failure(
        self,
        content: str | None,
        missing: list[str],
        policy: dict[str, Any],
    ) -> str:
        """Return a user-visible message when workflow requirements are unmet."""
        details = self._format_workflow_missing(missing)
        mode = self._workflow_failure_mode(policy)
        if mode == "hard_fail":
            if details:
                return (
                    "Workflow requirements were not satisfied. This task is not complete.\n"
                    f"{details}"
                )
            return "Workflow requirements were not satisfied. This task is not complete."

        base = (content or "").strip()
        warning_header = "Workflow requirements not yet satisfied:"
        if warning_header in base:
            return base
        if details:
            if base:
                return f"{base}\n\n{warning_header}\n{details}"
            return f"{warning_header}\n{details}"
        if base:
            return f"{base}\n\n{warning_header}"
        return warning_header

    @staticmethod
    def _workflow_substantive_tools(policy: dict[str, Any]) -> set[str]:
        kickoff = policy.get("kickoff") if isinstance(policy, dict) else None
        if not isinstance(kickoff, dict):
            return set()
        return {
            str(name).strip().lower()
            for name in kickoff.get("substantive_tools", [])
            if str(name).strip()
        }

    def _workflow_completion_rules(self, policy: dict[str, Any]) -> list[dict[str, Any]]:
        completion = policy.get("completion") if isinstance(policy, dict) else None
        if not isinstance(completion, dict):
            return []
        raw_rules = completion.get("require_tool_calls")
        if not isinstance(raw_rules, list):
            return []
        return [rule for rule in raw_rules if isinstance(rule, dict)]

    def _workflow_completion_progress(
        self,
        policy: dict[str, Any],
        tool_events: list[dict[str, Any]],
    ) -> tuple[int, int]:
        rules = self._workflow_completion_rules(policy)
        if not rules:
            return 0, 0
        satisfied = 0
        for rule in rules:
            if any(self._workflow_rule_matches_event(rule, event) for event in tool_events):
                satisfied += 1
        return satisfied, len(rules)

    @staticmethod
    def _workflow_progress_milestones(policy: dict[str, Any]) -> dict[str, Any]:
        progress = policy.get("progress") if isinstance(policy, dict) else None
        if not isinstance(progress, dict):
            return {}
        raw_milestones = progress.get("milestones")
        if not isinstance(raw_milestones, dict):
            return {}
        if not bool(raw_milestones.get("enabled")):
            return {}

        try:
            interval = int(raw_milestones.get("tool_call_interval") or 0)
        except (TypeError, ValueError):
            interval = 0
        interval = max(0, interval)

        try:
            max_messages = int(raw_milestones.get("max_messages") or 3)
        except (TypeError, ValueError):
            max_messages = 3
        max_messages = max(1, max_messages)

        default_templates = {
            "kickoff": (
                "进度：已开始执行，正在检索权威资料。"
            ),
            "researching": (
                "进度：资料检索中，已获取 {source_calls} 个来源。"
            ),
            "synthesizing": (
                "进度：正在整理关键信息并归纳结论。"
            ),
            "saving": (
                "进度：正在写入知识库文档。"
            ),
            "completion_ready": (
                "进度：文档已保存，正在生成最终答复。"
            ),
        }
        templates: dict[str, str] = {}
        raw_templates = raw_milestones.get("templates")
        for key, default_text in default_templates.items():
            custom = ""
            if isinstance(raw_templates, dict):
                custom = str(raw_templates.get(key) or "").strip()
            templates[key] = custom or default_text

        return {
            "enabled": True,
            "tool_call_interval": interval,
            "max_messages": max_messages,
            "templates": templates,
        }

    @staticmethod
    def _workflow_stage_template(
        templates: dict[str, Any],
        stage: str,
    ) -> str:
        if not isinstance(templates, dict):
            return ""
        aliases: dict[str, list[str]] = {
            "kickoff": ["kickoff"],
            # Backward compatible alias for older metadata.
            "researching": ["researching", "tool_progress"],
            "synthesizing": ["synthesizing"],
            "saving": ["saving"],
            "completion_ready": ["completion_ready"],
        }
        keys = aliases.get(stage, [stage])
        for key in keys:
            value = str(templates.get(key) or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _format_progress_template(template: str, values: dict[str, Any]) -> str:
        class _SafeDict(dict[str, Any]):
            def __missing__(self, key: str) -> str:
                return "{" + key + "}"

        return template.format_map(_SafeDict(values))

    async def _maybe_emit_workflow_milestone(
        self,
        msg: InboundMessage,
        trace_id: str,
        policy: dict[str, Any],
        tool_events: list[dict[str, Any]],
        state: dict[str, Any],
    ) -> None:
        config = state.get("config")
        if not isinstance(config, dict) or not config:
            return
        if not tool_events:
            return

        sent_count = int(state.get("sent_count") or 0)
        max_messages = int(config.get("max_messages") or 0)
        if max_messages > 0 and sent_count >= max_messages:
            return

        sent_keys = state.get("sent_keys")
        if not isinstance(sent_keys, set):
            sent_keys = set()
            state["sent_keys"] = sent_keys

        tool_calls = len(tool_events)
        last_tool = str(tool_events[-1].get("name") or "").strip().lower()
        source_tools = {"web_search", "web_fetch"}
        source_calls = sum(
            1 for event in tool_events
            if str(event.get("name") or "").strip().lower() in source_tools
        )
        synthesis_tools = {"read_file", "edit_file"}
        synthesis_calls = sum(
            1 for event in tool_events
            if str(event.get("name") or "").strip().lower() in synthesis_tools
        )
        save_calls = sum(
            1 for event in tool_events
            if str(event.get("name") or "").strip().lower() == "write_file"
        )

        substantive_tools = self._workflow_substantive_tools(policy)
        substantive_count = 0
        if substantive_tools:
            substantive_count = sum(
                1
                for event in tool_events
                if str(event.get("name") or "").strip().lower() in substantive_tools
            )
        else:
            substantive_count = len(tool_events)
        substantive_seen = substantive_count > 0

        satisfied_rules, total_rules = self._workflow_completion_progress(policy, tool_events)
        completion_ready = total_rules > 0 and satisfied_rules >= total_rules
        completion_progress = (
            f"{satisfied_rules}/{total_rules}" if total_rules > 0 else "n/a"
        )

        stage = ""

        if completion_ready and "completion_ready" not in sent_keys:
            stage = "completion_ready"
        elif save_calls > 0 and "saving" not in sent_keys:
            stage = "saving"
        elif source_calls >= 2 and "researching" not in sent_keys:
            stage = "researching"
        elif source_calls > 0 and synthesis_calls > 0 and "synthesizing" not in sent_keys:
            stage = "synthesizing"
        elif substantive_seen and "kickoff" not in sent_keys:
            stage = "kickoff"

        if not stage:
            return
        if stage in sent_keys:
            return

        templates = config.get("templates")
        template = self._workflow_stage_template(
            templates if isinstance(templates, dict) else {},
            stage,
        )
        if not template:
            return

        message_tool = self.tools.get("message")
        if not isinstance(message_tool, MessageTool):
            logger.debug(
                f"Trace {trace_id} workflow milestone skipped: MessageTool unavailable "
                f"stage={stage}"
            )
            return

        content = self._format_progress_template(
            template,
            {
                "stage": stage,
                "tool_calls": tool_calls,
                "source_calls": source_calls,
                "synthesis_calls": synthesis_calls,
                "save_calls": save_calls,
                "substantive_calls": substantive_count,
                "last_tool": last_tool or "unknown",
                "completion_satisfied": satisfied_rules,
                "completion_total": total_rules,
                "completion_progress": completion_progress,
            },
        ).strip()
        if not content:
            return

        send_result = await message_tool.execute(
            content=content,
            channel=msg.channel,
            chat_id=msg.chat_id,
        )
        if send_result.lower().startswith("error"):
            logger.warning(
                f"Trace {trace_id} workflow milestone send failed stage={stage} "
                f"result={send_result}"
            )
            return

        sent_keys.add(stage)
        state["sent_count"] = sent_count + 1
        logger.debug(
            f"Trace {trace_id} workflow milestone sent stage={stage} "
            f"tool_calls={tool_calls} source_calls={source_calls} "
            f"completion={completion_progress} sent={state['sent_count']}/{max_messages or 'unlimited'}"
        )

    def _schedule_memory_consolidation(self, session: Session, archive_all: bool = False) -> None:
        """
        Launch memory consolidation in the background.

        Deduplicates by session key so bursts of inbound messages do not create
        duplicated HISTORY.md entries for the same session.
        """
        run_key = f"{session.key}#archive" if archive_all else session.key
        if run_key in self._memory_consolidation_inflight:
            logger.debug(
                f"Skip memory consolidation schedule: already running key={run_key}"
            )
            return
        self._memory_consolidation_inflight.add(run_key)

        async def _runner() -> None:
            try:
                await self._consolidate_memory(session, archive_all=archive_all)
            except Exception as e:
                logger.warning(
                    f"Memory consolidation task failed key={run_key}: {e}"
                )
            finally:
                self._memory_consolidation_inflight.discard(run_key)

        asyncio.create_task(_runner())

    async def _consolidate_memory(self, session: Session, archive_all: bool = False) -> None:
        """
        Consolidate older conversation into memory/HISTORY.md and memory/MEMORY.md.

        For regular sessions, only consolidated offsets are updated (messages stay append-only).
        For archive_all mode (used when creating /new session), all provided
        messages are archived without mutating session state.
        """
        if not session.messages:
            return

        memory = MemoryStore(self.workspace)
        total_messages = len(session.messages)
        keep_count = 0 if archive_all else max(1, self.memory_window // 2)

        if archive_all:
            target_messages = list(session.messages)
            logger.info(
                f"Memory consolidation archive_all key={session.key} total={total_messages}"
            )
        else:
            if total_messages <= keep_count:
                return
            offset = session.last_consolidated
            if offset > total_messages:
                offset = 0
                session.last_consolidated = 0
            upper = total_messages - keep_count
            if upper <= offset:
                logger.debug(
                    f"Memory consolidation skipped key={session.key} "
                    f"offset={offset} total={total_messages} keep={keep_count}"
                )
                return
            target_messages = session.messages[offset:upper]
            logger.info(
                f"Memory consolidation started key={session.key} total={total_messages} "
                f"process={len(target_messages)} keep={keep_count} offset={offset}"
            )

        lines: list[str] = []
        for item in target_messages:
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            role = str(item.get("role") or "unknown").upper()
            stamp = str(item.get("timestamp") or "?")[:16]
            lines.append(f"[{stamp}] {role}: {content}")

        if not lines:
            if not archive_all:
                session.last_consolidated = max(0, total_messages - keep_count)
                self.sessions.save(session)
            return

        conversation = "\n".join(lines)
        # Keep consolidation prompt bounded for stability.
        if len(conversation) > 20000:
            conversation = "[truncated to latest 20000 chars]\n" + conversation[-20000:]

        current_memory = memory.read_long_term()
        prompt = f"""You are a memory consolidation agent. Return JSON with exactly these keys:
- history_entry: 2-5 sentence event summary starting with [YYYY-MM-DD HH:MM]
- memory_update: fully updated long-term memory text

Current long-term memory:
{current_memory or "(empty)"}

Conversation chunk:
{conversation}

Respond with valid JSON only."""

        try:
            result = await self.provider.chat(
                messages=[
                    {
                        "role": "system",
                        "content": "You consolidate conversation history. Output JSON only.",
                    },
                    {"role": "user", "content": prompt},
                ],
                tools=[],
                model=self.model,
                temperature=min(self.temperature, 0.3),
                max_tokens=min(self.max_tokens, 1200),
            )

            raw = (result.content or "").strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("consolidation response is not a JSON object")

            history_entry = str(payload.get("history_entry") or "").strip()
            memory_update = str(payload.get("memory_update") or "").strip()

            sanitized_history_entry = self._sanitize_history_entry(history_entry)
            sanitized_memory_update = self._sanitize_memory_update(memory_update)
            if not sanitized_memory_update:
                sanitized_memory_update = current_memory.strip()

            if sanitized_history_entry:
                memory.append_history(sanitized_history_entry)
            elif history_entry:
                logger.debug(
                    f"Memory consolidation dropped transient history entry key={session.key}"
                )

            if sanitized_memory_update and sanitized_memory_update != current_memory.strip():
                memory.write_long_term(sanitized_memory_update)
            elif memory_update and sanitized_memory_update != memory_update:
                logger.debug(
                    f"Memory consolidation sanitized transient memory_update key={session.key}"
                )

            if not archive_all:
                session.last_consolidated = max(0, total_messages - keep_count)
                self.sessions.save(session)
                logger.info(
                    f"Memory consolidation done key={session.key} "
                    f"offset={session.last_consolidated} total={total_messages}"
                )
            else:
                logger.info(
                    f"Memory consolidation archive_all done key={session.key} "
                    f"archived={len(target_messages)}"
                )
        except Exception as e:
            logger.warning(f"Memory consolidation failed key={session.key}: {e}")

    def _select_iteration_tools(
        self,
        matched_skills: list[str],
        has_tool_results: bool = False,
        force_realtime_tools: bool = False,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """
        Select visible tools and tool-choice policy for this iteration.

        Skill-driven behavior:
        - No matched skill: expose all tools, keep tool_choice=auto.
        - Matched skills without tool results yet: enforce one tool call (tool_choice=required).
        - After at least one tool result exists: switch back to auto so the model can finalize.
        - If skills provide metadata.nanobot.allowed_tools, restrict tool set.
        - Realtime queries without tool results: force one web tool call first.
        """
        all_tools = self.tools.get_definitions()
        if force_realtime_tools and not has_tool_results:
            realtime_tools = [
                schema
                for schema in all_tools
                if self._tool_schema_name(schema) in {"web_search", "web_fetch"}
            ]
            if realtime_tools:
                return realtime_tools, "required"
            logger.warning(
                "Realtime-tool enforcement active but web tools missing; "
                "falling back to full toolset with required tool call."
            )
            return all_tools, ("required" if all_tools else None)

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
            resolved_session_key = msg.session_key
            base_session_key = resolved_session_key.split("#", 1)[0]
            logger.info(
                f"Trace {trace_id} new-session command detected channel={msg.channel} "
                f"chat_id={msg.chat_id} base_session_key={base_session_key} "
                f"resolved_session_key={resolved_session_key}"
            )
            old_session = self.sessions.get_or_create(resolved_session_key)
            archived_messages = list(old_session.messages)
            archived_metadata = dict(old_session.metadata or {})
            session = self.sessions.start_new(base_session_key)
            self.sessions.save(session)
            if archived_messages:
                archived = Session(
                    key=old_session.key,
                    messages=archived_messages,
                    created_at=old_session.created_at,
                    updated_at=old_session.updated_at,
                    metadata=archived_metadata,
                )
                self._schedule_memory_consolidation(archived, archive_all=True)
            logger.info(
                f"Trace {trace_id} new-session command completed session_key={session.key} "
                f"messages={len(session.messages)}"
            )
            greeting = "⚛ 新会话已就绪～有什么需要我做的吗？"
            logger.info(
                f"Trace {trace_id} new-session greeting content={greeting}"
            )
            out_meta = dict(msg.metadata or {})
            out_meta.setdefault("trace_id", trace_id)
            out_meta.setdefault("session_key", session.key)
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=greeting,
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
        if len(session.messages) > self.memory_window:
            self._schedule_memory_consolidation(session)

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
        workflow_policy = self.context.skills.get_workflow_policy_for_skills(matched_skills)
        workflow_retry_limit = self._workflow_retry_limit(workflow_policy)
        workflow_retries_used = 0
        workflow_tool_events: list[dict[str, Any]] = []
        workflow_progress_state: dict[str, Any] = {
            "config": self._workflow_progress_milestones(workflow_policy),
            "sent_count": 0,
            "sent_keys": set(),
        }
        force_realtime_tools = self._is_realtime_query(msg.content)
        attachment_requested = self._is_attachment_delivery_request(msg.content)
        tool_round_limited_skills = self.context.skills.get_tool_round_limited_skills(matched_skills)
        skill_enforcement_attempted = False
        realtime_enforcement_attempted = False
        web_search_result_cache: dict[str, str] = {}
        raw_skill_tool_round_limit = int(getattr(self.context_config, "skill_tool_round_limit", 0) or 0)
        skill_tool_round_limit = max(0, raw_skill_tool_round_limit)
        if not tool_round_limited_skills:
            skill_tool_round_limit = 0
        skill_tool_rounds = 0
        last_tool_round_fingerprint: str | None = None
        stagnant_tool_rounds = 0
        raw_stagnation_limit = int(getattr(self.context_config, "skill_tool_stagnation_limit", 0) or 0)
        stagnation_limit = max(0, raw_stagnation_limit)
        if matched_skills:
            logger.debug(
                f"Trace {trace_id} skill loop guards matched_skills={matched_skills} "
                f"tool_round_limited_skills={tool_round_limited_skills} "
                f"round_limit={skill_tool_round_limit} stagnation_limit={stagnation_limit}"
            )
        if workflow_policy:
            logger.debug(
                f"Trace {trace_id} workflow policy enabled for matched_skills={matched_skills} "
                f"retry_limit={workflow_retry_limit} policy={workflow_policy}"
            )
            if workflow_progress_state.get("config"):
                logger.debug(
                    f"Trace {trace_id} workflow milestone push enabled "
                    f"config={workflow_progress_state.get('config')}"
                )
        logger.debug(
            f"Trace {trace_id} realtime_tool_enforcement={force_realtime_tools} "
            f"attachment_requested={attachment_requested} "
            f"matched_skills={matched_skills}"
        )

        # Native session recovery: if first LLM call in native mode fails,
        # clear stale previous_response_id and retry with full context (reset mode).
        if native_mode:
            probe_tools, probe_tool_choice = self._select_iteration_tools(
                matched_skills,
                has_tool_results=False,
                force_realtime_tools=force_realtime_tools,
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
                temperature=self.temperature,
                max_tokens=self.max_tokens,
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
                        args_signature = self._canonical_tool_arguments(tool_call.arguments)
                        logger.info(f"Tool call: {tool_call.name}({args_str[:200]})")
                        t_tool = time.monotonic()
                        if tool_call.name == "web_search" and args_signature in web_search_result_cache:
                            result = web_search_result_cache[args_signature]
                            logger.debug(
                                f"Trace {trace_id} native probe tool=web_search "
                                "dedupe_hit=True reused previous result"
                            )
                        else:
                            result = await self.tools.execute(tool_call.name, tool_call.arguments)
                            if tool_call.name == "web_search":
                                web_search_result_cache[args_signature] = result
                        messages = self.context.add_tool_result(
                            messages, tool_call.id, tool_call.name, result
                        )
                        workflow_tool_events.append({
                            "name": tool_call.name,
                            "arguments": tool_call.arguments,
                            "result": result,
                        })
                        await self._maybe_emit_workflow_milestone(
                            msg=msg,
                            trace_id=trace_id,
                            policy=workflow_policy,
                            tool_events=workflow_tool_events,
                            state=workflow_progress_state,
                        )
                        tool_elapsed = time.monotonic() - t_tool
                        tool_total += tool_elapsed
                        logger.debug(
                            f"Trace {trace_id} native probe tool={tool_call.name} "
                            f"elapsed={tool_elapsed:.3f}s result_chars={len(result)}"
                        )
                    iteration = 1  # Count this as first iteration
                    self._log_messages_for_trace(trace_id, "native-probe post-tools", messages)
                    if tool_round_limited_skills and skill_tool_round_limit > 0:
                        skill_tool_rounds = 1
                        if skill_tool_rounds >= skill_tool_round_limit:
                            logger.warning(
                                f"Trace {trace_id} reached skill tool-round limit during native probe: "
                                f"rounds={skill_tool_rounds} limit={skill_tool_round_limit} "
                                f"matched_skills={tool_round_limited_skills}. Forcing summary."
                            )
                            iteration = self.max_iterations
                else:
                    if force_realtime_tools:
                        realtime_enforcement_attempted = True
                        messages = [
                            {
                                "role": "user",
                                "content": (
                                    "Realtime verification retry. Call web_search first "
                                    "(and web_fetch if needed), then answer with links."
                                ),
                            }
                        ]
                        iteration = 1  # Count native probe call
                        logger.debug(
                            f"Trace {trace_id} native probe had no tool calls for realtime query; "
                            "queued explicit realtime retry"
                        )
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
                matched_skills,
                has_tool_results=self._has_tool_messages(messages),
                force_realtime_tools=force_realtime_tools,
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
                temperature=self.temperature,
                max_tokens=self.max_tokens,
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
                    args_signature = self._canonical_tool_arguments(tool_call.arguments)
                    logger.info(f"Tool call: {tool_call.name}({args_str[:200]})")
                    t_tool = time.monotonic()
                    if tool_call.name == "web_search" and args_signature in web_search_result_cache:
                        result = web_search_result_cache[args_signature]
                        logger.debug(
                            f"Trace {trace_id} iteration={iteration} tool=web_search "
                            "dedupe_hit=True reused previous result"
                        )
                    else:
                        result = await self.tools.execute(tool_call.name, tool_call.arguments)
                        if tool_call.name == "web_search":
                            web_search_result_cache[args_signature] = result
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
                    workflow_tool_events.append({
                        "name": tool_call.name,
                        "arguments": tool_call.arguments,
                        "result": result,
                    })
                    await self._maybe_emit_workflow_milestone(
                        msg=msg,
                        trace_id=trace_id,
                        policy=workflow_policy,
                        tool_events=workflow_tool_events,
                        state=workflow_progress_state,
                    )
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

                if tool_round_limited_skills and skill_tool_round_limit > 0:
                    skill_tool_rounds += 1
                    if skill_tool_rounds >= skill_tool_round_limit:
                        logger.warning(
                            f"Trace {trace_id} reached skill tool-round limit: "
                            f"rounds={skill_tool_rounds} limit={skill_tool_round_limit} "
                            f"matched_skills={tool_round_limited_skills}. Forcing summary."
                        )
                        break
            else:
                # No tool calls, we're done
                workflow_missing = self._validate_workflow_requirements(
                    workflow_policy,
                    workflow_tool_events,
                    response.content,
                )
                if workflow_missing:
                    logger.debug(
                        f"Trace {trace_id} iteration={iteration} workflow requirements missing={workflow_missing} "
                        f"retry_used={workflow_retries_used}/{workflow_retry_limit}"
                    )
                    if workflow_retries_used < workflow_retry_limit and stream_state is None:
                        workflow_retries_used += 1
                        missing_lines = self._format_workflow_missing(workflow_missing)
                        retry_prompt = (
                            "Workflow enforcement retry: before finalizing, satisfy all missing "
                            "requirements by calling the necessary tools.\n"
                        )
                        if missing_lines:
                            retry_prompt += f"Missing requirements:\n{missing_lines}"
                        if not native_mode:
                            messages = self.context.add_assistant_message(
                                messages,
                                response.content,
                                reasoning_content=response.reasoning_content,
                            )
                            messages.append(
                                {
                                    "role": "system",
                                    "content": retry_prompt,
                                }
                            )
                        else:
                            messages = [
                                {
                                    "role": "user",
                                    "content": retry_prompt,
                                }
                            ]
                        continue

                    final_content = self._apply_workflow_failure(
                        response.content,
                        workflow_missing,
                        workflow_policy,
                    )
                    if stream_state:
                        final_streamed = stream_state.sent_any
                    logger.debug(
                        f"Trace {trace_id} iteration={iteration} workflow failed finalization "
                        f"content_chars={len(final_content or '')}"
                    )
                    break

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

                if (
                    force_realtime_tools
                    and not realtime_enforcement_attempted
                    and not self._has_tool_messages(messages)
                    and stream_state is None
                ):
                    realtime_enforcement_attempted = True
                    logger.debug(
                        f"Trace {trace_id} iteration={iteration} realtime query had no tool calls; "
                        "enforcing one retry with explicit web_search requirement"
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
                                    "Realtime verification retry: this request needs up-to-date, "
                                    "source-backed facts. Before finalizing your answer, call "
                                    "web_search first (and web_fetch if needed), then answer "
                                    "based on tool results with links."
                                ),
                            }
                        )
                    else:
                        messages = [
                            {
                                "role": "user",
                                "content": (
                                    "Realtime verification retry. Call web_search first "
                                    "(and web_fetch if needed), then answer with links."
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
                temperature=self.temperature,
                max_tokens=self.max_tokens,
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
            workflow_missing = self._validate_workflow_requirements(
                workflow_policy,
                workflow_tool_events,
                final_content,
            )
            if workflow_missing:
                final_content = self._apply_workflow_failure(
                    final_content,
                    workflow_missing,
                    workflow_policy,
                )
            if summary_response.response_id and summary_response.response_id.startswith("resp_"):
                last_response = summary_response

        if final_content is not None:
            workflow_missing = self._validate_workflow_requirements(
                workflow_policy,
                workflow_tool_events,
                final_content,
            )
            if workflow_missing:
                final_content = self._apply_workflow_failure(
                    final_content,
                    workflow_missing,
                    workflow_policy,
                )

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
        delivered_media_paths: list[str] = []
        sent_message_count = 0
        _msg_tool = self.tools.get("message")
        if isinstance(_msg_tool, MessageTool):
            for sent in _msg_tool.drain_sent_messages():
                sent_message_count += 1
                kwargs: dict[str, Any] = {}
                media_items = [str(p) for p in (sent.get("media") or []) if str(p).strip()]
                if media_items:
                    kwargs["media"] = media_items
                    for item in media_items:
                        if item not in delivered_media_paths:
                            delivered_media_paths.append(item)
                session.add_message("assistant", sent["content"], **kwargs)

        inferred_media_paths: list[str] = []
        if attachment_requested and not delivered_media_paths:
            inferred_media_paths = self._infer_attachment_media_paths(msg.content, final_content or "")
            if inferred_media_paths:
                delivered_media_paths.extend(inferred_media_paths)
                logger.warning(
                    f"Trace {trace_id} attachment fallback inferred media paths: "
                    f"{[Path(p).name for p in inferred_media_paths]}"
                )
            elif self._claims_attachment_sent(final_content or ""):
                final_content = (
                    "我这次还没有真正发出附件（消息里没有携带 `media`）。"
                    "请再给我一次文件路径，或直接确认要发送的文件名。"
                )
                logger.warning(
                    f"Trace {trace_id} attachment claim detected without media; "
                    "rewrote final response to explicit failure"
                )

        suppress_redundant_attachment_ack = (
            attachment_requested
            and sent_message_count > 0
            and bool(delivered_media_paths)
            and self._is_redundant_attachment_ack(final_content or "")
        )
        if suppress_redundant_attachment_ack:
            logger.debug(
                f"Trace {trace_id} suppressing redundant attachment follow-up "
                f"after message tool delivery content_preview={self._preview_text(final_content or '')}"
            )

        if final_content and not suppress_redundant_attachment_ack:
            kwargs: dict[str, Any] = {}
            if inferred_media_paths:
                kwargs["media"] = inferred_media_paths
            session.add_message("assistant", final_content, **kwargs)
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

        if suppress_redundant_attachment_ack and not inferred_media_paths:
            logger.debug(
                f"Trace {trace_id} redundant attachment ack suppressed, "
                "skip outbound message object"
            )
            return None

        if final_streamed and not inferred_media_paths:
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
            f"context_mode={out_metadata.get('_context_mode')} "
            f"media={len(inferred_media_paths)}"
        )

        outbound = OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            media=inferred_media_paths,
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
        return first in {"/new", "/reset"}

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
                temperature=self.temperature,
                max_tokens=self.max_tokens,
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
                temperature=self.temperature,
                max_tokens=self.max_tokens,
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
            content=content,
            metadata={"session_key": session_key},
        )

        response = await self._process_message(msg)
        logger.debug(
            f"Direct process session={session_key} channel={channel} "
            f"elapsed={(time.monotonic() - t_start):.3f}s"
        )
        if return_message:
            return response
        return response.content if response else ""
