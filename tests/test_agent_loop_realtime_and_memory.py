import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from nanobot.agent.loop import AgentLoop
from nanobot.agent.memory import MemoryStore
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import ContextConfig
from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from nanobot.session.manager import Session


class _SequenceProvider(LLMProvider):
    def __init__(self, responses: list[LLMResponse]):
        super().__init__()
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def chat(
        self,
        messages,
        tools=None,
        tool_choice=None,
        model=None,
        max_tokens=4096,
        temperature=0.7,
        session_state=None,
        on_delta=None,
    ) -> LLMResponse:
        self.calls.append({
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "session_state": session_state,
        })
        if not self._responses:
            raise AssertionError("Unexpected extra provider.chat call")
        return self._responses.pop(0)

    def get_default_model(self) -> str:
        return "test-model"


def _web_tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "exec",
                "description": "Run shell commands.",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search web.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_fetch",
                "description": "Fetch URL.",
                "parameters": {
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                },
            },
        },
    ]


def test_realtime_query_forces_web_tools_before_final_answer(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    provider = _SequenceProvider([
        LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    id="call_1",
                    name="web_search",
                    arguments={"query": "today ai news"},
                )
            ],
        ),
        LLMResponse(content="top 3 news with links"),
    ])
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=workspace,
        context_config=ContextConfig(enable_native_session=False),
    )

    test_session = Session(key="web:test_chat:default#realtime")
    loop.sessions.get_or_create = lambda key: test_session  # type: ignore[method-assign]
    loop.sessions.save = lambda session: None  # type: ignore[method-assign]

    async def _fake_build_context(*, session, current_message, media, channel, chat_id):
        return SimpleNamespace(
            messages=[
                {"role": "system", "content": "system"},
                {"role": "user", "content": current_message},
            ],
            session_state={},
            stats={"mode": "stateless", "matched_skills": []},
        )

    loop.context_manager.build_context = _fake_build_context  # type: ignore[method-assign]
    loop.context_manager.update_after_response = lambda session, response: None  # type: ignore[method-assign]
    loop.context.skills.get_tool_round_limited_skills = lambda skill_names: []  # type: ignore[method-assign]
    loop.tools.get_definitions = _web_tool_definitions  # type: ignore[method-assign]

    async def _fake_execute(name: str, arguments: dict[str, Any]) -> str:
        return "Results for: today ai news (provider: openai_hosted)"

    loop.tools.execute = _fake_execute  # type: ignore[method-assign]

    outbound = asyncio.run(
        loop._process_message(
            InboundMessage(
                channel="web",
                sender_id="user",
                chat_id="test_chat",
                content="帮我搜索今天 AI 领域最重要的三条新闻",
                metadata={
                    "trace_id": "trace-realtime-force",
                    "session_key": "web:test_chat:default#realtime",
                },
            )
        )
    )

    assert outbound is not None
    assert outbound.content == "top 3 news with links"
    assert len(provider.calls) == 2
    assert provider.calls[0]["tool_choice"] == "required"
    tool_names = [tool["function"]["name"] for tool in provider.calls[0]["tools"]]
    assert tool_names == ["web_search", "web_fetch"]


def test_realtime_query_retries_once_when_model_skips_tool_calls(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    provider = _SequenceProvider([
        LLMResponse(content="I cannot browse right now."),
        LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    id="call_2",
                    name="web_search",
                    arguments={"query": "latest ai news"},
                )
            ],
        ),
        LLMResponse(content="verified answer with links"),
    ])
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=workspace,
        context_config=ContextConfig(enable_native_session=False),
    )

    test_session = Session(key="web:test_chat:default#realtime-retry")
    loop.sessions.get_or_create = lambda key: test_session  # type: ignore[method-assign]
    loop.sessions.save = lambda session: None  # type: ignore[method-assign]

    async def _fake_build_context(*, session, current_message, media, channel, chat_id):
        return SimpleNamespace(
            messages=[
                {"role": "system", "content": "system"},
                {"role": "user", "content": current_message},
            ],
            session_state={},
            stats={"mode": "stateless", "matched_skills": []},
        )

    loop.context_manager.build_context = _fake_build_context  # type: ignore[method-assign]
    loop.context_manager.update_after_response = lambda session, response: None  # type: ignore[method-assign]
    loop.context.skills.get_tool_round_limited_skills = lambda skill_names: []  # type: ignore[method-assign]
    loop.tools.get_definitions = _web_tool_definitions  # type: ignore[method-assign]
    loop.tools.execute = lambda name, arguments: asyncio.sleep(0, result="ok")  # type: ignore[method-assign]

    outbound = asyncio.run(
        loop._process_message(
            InboundMessage(
                channel="web",
                sender_id="user",
                chat_id="test_chat",
                content="today ai news with links",
                metadata={
                    "trace_id": "trace-realtime-retry",
                    "session_key": "web:test_chat:default#realtime-retry",
                },
            )
        )
    )

    assert outbound is not None
    assert outbound.content == "verified answer with links"
    assert len(provider.calls) == 3
    assert provider.calls[0]["tool_choice"] == "required"
    assert provider.calls[1]["tool_choice"] == "required"
    system_reminders = [
        msg
        for msg in provider.calls[1]["messages"]
        if msg.get("role") == "system"
    ]
    assert any("Realtime verification retry" in str(msg.get("content")) for msg in system_reminders)


def test_workflow_metadata_enforcement_retries_until_completion_rule_is_met(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    provider = _SequenceProvider([
        LLMResponse(content="主人，我先给你一个计划。"),
        LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    id="call_workflow_1",
                    name="write_file",
                    arguments={
                        "path": "memory/learnings/python-performance-optimization.md",
                        "content": "# Python Performance Optimization\n",
                    },
                )
            ],
        ),
        LLMResponse(content="主人，研究已完成并已落盘。"),
    ])
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=workspace,
        context_config=ContextConfig(enable_native_session=False),
    )

    workflow_policy = {
        "kickoff": {
            "require_substantive_action": True,
            "substantive_tools": ["web_search", "web_fetch", "write_file", "spawn"],
            "forbid_as_first_only": ["list_dir", "exec"],
        },
        "completion": {
            "require_tool_calls": [
                {
                    "name": "write_file",
                    "args": {"path_regex": r"^memory/learnings/[^/]+\.md$"},
                }
            ]
        },
        "retry": {"enforcement_retries": 1, "failure_mode": "explain_missing"},
        "progress": {"claim_requires_actions": True, "claim_patterns": ["完成", "completed"]},
    }

    test_session = Session(key="web:test_chat:default#workflow-enforce-pass")
    loop.sessions.get_or_create = lambda key: test_session  # type: ignore[method-assign]
    loop.sessions.save = lambda session: None  # type: ignore[method-assign]

    async def _fake_build_context(*, session, current_message, media, channel, chat_id):
        return SimpleNamespace(
            messages=[
                {"role": "system", "content": "system"},
                {"role": "user", "content": current_message},
            ],
            session_state={},
            stats={"mode": "stateless", "matched_skills": ["deep-learn"]},
        )

    loop.context_manager.build_context = _fake_build_context  # type: ignore[method-assign]
    loop.context_manager.update_after_response = lambda session, response: None  # type: ignore[method-assign]
    loop.context.skills.get_tool_round_limited_skills = lambda skill_names: []  # type: ignore[method-assign]
    loop.context.skills.get_workflow_policy_for_skills = lambda skill_names: workflow_policy  # type: ignore[method-assign]

    execute_calls: list[tuple[str, dict[str, Any]]] = []

    async def _fake_execute(name: str, arguments: dict[str, Any]) -> str:
        execute_calls.append((name, arguments))
        return "Successfully wrote 34 bytes to memory/learnings/python-performance-optimization.md"

    loop.tools.execute = _fake_execute  # type: ignore[method-assign]

    outbound = asyncio.run(
        loop._process_message(
            InboundMessage(
                channel="web",
                sender_id="user",
                chat_id="test_chat",
                content="深入研究一下 Python 性能优化",
                metadata={
                    "trace_id": "trace-workflow-enforce-pass",
                    "session_key": "web:test_chat:default#workflow-enforce-pass",
                },
            )
        )
    )

    assert outbound is not None
    assert outbound.content == "主人，研究已完成并已落盘。"
    assert execute_calls == [
        (
            "write_file",
            {
                "path": "memory/learnings/python-performance-optimization.md",
                "content": "# Python Performance Optimization\n",
            },
        )
    ]
    assert len(provider.calls) == 3
    retry_messages = [
        msg
        for msg in provider.calls[1]["messages"]
        if msg.get("role") == "system" and "Workflow enforcement retry" in str(msg.get("content"))
    ]
    assert retry_messages


def test_workflow_metadata_enforcement_reports_missing_when_retry_exhausted(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    provider = _SequenceProvider([
        LLMResponse(content="主人，我现在开始执行。"),
        LLMResponse(content="主人，已完成。"),
    ])
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=workspace,
        context_config=ContextConfig(enable_native_session=False),
    )

    workflow_policy = {
        "kickoff": {
            "require_substantive_action": True,
            "substantive_tools": ["web_search", "web_fetch", "write_file", "spawn"],
            "forbid_as_first_only": ["list_dir", "exec"],
        },
        "completion": {
            "require_tool_calls": [
                {
                    "name": "write_file",
                    "args": {"path_regex": r"^memory/learnings/[^/]+\.md$"},
                }
            ]
        },
        "retry": {"enforcement_retries": 1, "failure_mode": "explain_missing"},
        "progress": {"claim_requires_actions": True, "claim_patterns": ["执行", "完成", "completed"]},
    }

    test_session = Session(key="web:test_chat:default#workflow-enforce-fail")
    loop.sessions.get_or_create = lambda key: test_session  # type: ignore[method-assign]
    loop.sessions.save = lambda session: None  # type: ignore[method-assign]

    async def _fake_build_context(*, session, current_message, media, channel, chat_id):
        return SimpleNamespace(
            messages=[
                {"role": "system", "content": "system"},
                {"role": "user", "content": current_message},
            ],
            session_state={},
            stats={"mode": "stateless", "matched_skills": ["deep-learn"]},
        )

    loop.context_manager.build_context = _fake_build_context  # type: ignore[method-assign]
    loop.context_manager.update_after_response = lambda session, response: None  # type: ignore[method-assign]
    loop.context.skills.get_tool_round_limited_skills = lambda skill_names: []  # type: ignore[method-assign]
    loop.context.skills.get_workflow_policy_for_skills = lambda skill_names: workflow_policy  # type: ignore[method-assign]
    loop.tools.execute = lambda name, arguments: asyncio.sleep(0, result="ok")  # type: ignore[method-assign]

    outbound = asyncio.run(
        loop._process_message(
            InboundMessage(
                channel="web",
                sender_id="user",
                chat_id="test_chat",
                content="深入研究一下 Python 性能优化",
                metadata={
                    "trace_id": "trace-workflow-enforce-fail",
                    "session_key": "web:test_chat:default#workflow-enforce-fail",
                },
            )
        )
    )

    assert outbound is not None
    assert "Workflow requirements not yet satisfied" in outbound.content
    assert "required tool call not satisfied" in outbound.content
    assert "write_file(path_regex=" in outbound.content
    assert len(provider.calls) == 2


def test_workflow_milestone_progress_pushes_intermediate_messages(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    provider = _SequenceProvider([
        LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    id="call_m1",
                    name="web_search",
                    arguments={"query": "python performance profile"},
                )
            ],
        ),
        LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    id="call_m2",
                    name="web_fetch",
                    arguments={"url": "https://docs.python.org/3/library/profile.html"},
                )
            ],
        ),
        LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    id="call_m3",
                    name="write_file",
                    arguments={
                        "path": "memory/learnings/python-performance-optimization.md",
                        "content": "# Python Performance Optimization\n",
                    },
                )
            ],
        ),
        LLMResponse(content="主人，研究已完成并已落盘。"),
    ])
    bus = MessageBus()
    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=workspace,
        context_config=ContextConfig(enable_native_session=False),
    )

    workflow_policy = {
        "kickoff": {
            "require_substantive_action": True,
            "substantive_tools": ["web_search", "web_fetch", "write_file", "spawn"],
            "forbid_as_first_only": ["list_dir", "exec"],
        },
        "completion": {
            "require_tool_calls": [
                {
                    "name": "write_file",
                    "args": {"path_regex": r"^memory/learnings/[^/]+\.md$"},
                }
            ]
        },
        "retry": {"enforcement_retries": 1, "failure_mode": "explain_missing"},
        "progress": {
            "claim_requires_actions": True,
            "claim_patterns": ["完成", "completed"],
            "milestones": {
                "enabled": True,
                "tool_call_interval": 2,
                "max_messages": 3,
                "templates": {
                    "kickoff": "MILESTONE kickoff",
                    "researching": "MILESTONE researching {source_calls} {last_tool}",
                    "completion_ready": "MILESTONE completion",
                },
            },
        },
    }

    test_session = Session(key="web:test_chat:default#workflow-milestones")
    loop.sessions.get_or_create = lambda key: test_session  # type: ignore[method-assign]
    loop.sessions.save = lambda session: None  # type: ignore[method-assign]

    async def _fake_build_context(*, session, current_message, media, channel, chat_id):
        return SimpleNamespace(
            messages=[
                {"role": "system", "content": "system"},
                {"role": "user", "content": current_message},
            ],
            session_state={},
            stats={"mode": "stateless", "matched_skills": ["deep-learn"]},
        )

    loop.context_manager.build_context = _fake_build_context  # type: ignore[method-assign]
    loop.context_manager.update_after_response = lambda session, response: None  # type: ignore[method-assign]
    loop.context.skills.get_tool_round_limited_skills = lambda skill_names: []  # type: ignore[method-assign]
    loop.context.skills.get_workflow_policy_for_skills = lambda skill_names: workflow_policy  # type: ignore[method-assign]
    loop.tools.execute = lambda name, arguments: asyncio.sleep(0, result="ok")  # type: ignore[method-assign]

    outbound = asyncio.run(
        loop._process_message(
            InboundMessage(
                channel="web",
                sender_id="user",
                chat_id="test_chat",
                content="深入研究一下 Python 性能优化",
                metadata={
                    "trace_id": "trace-workflow-milestones",
                    "session_key": "web:test_chat:default#workflow-milestones",
                },
            )
        )
    )

    assert outbound is not None
    assert outbound.content == "主人，研究已完成并已落盘。"
    assert bus.outbound_size == 3
    pushed = [asyncio.run(bus.consume_outbound()) for _ in range(3)]
    assert pushed[0].content == "MILESTONE kickoff"
    assert pushed[1].content == "MILESTONE researching 2 web_fetch"
    assert pushed[2].content == "MILESTONE completion"

    assistant_messages = [m for m in test_session.messages if m["role"] == "assistant"]
    assistant_texts = [str(m.get("content")) for m in assistant_messages]
    assert "MILESTONE kickoff" in assistant_texts
    assert "MILESTONE completion" in assistant_texts


def test_workflow_milestone_progress_respects_max_messages_cap(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    provider = _SequenceProvider([
        LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(id="call_cap_1", name="web_search", arguments={"query": "x"})
            ],
        ),
        LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(id="call_cap_2", name="web_fetch", arguments={"url": "https://x"})
            ],
        ),
        LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    id="call_cap_3",
                    name="write_file",
                    arguments={"path": "memory/learnings/x.md", "content": "# x\n"},
                )
            ],
        ),
        LLMResponse(content="completed"),
    ])
    bus = MessageBus()
    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=workspace,
        context_config=ContextConfig(enable_native_session=False),
    )

    workflow_policy = {
        "kickoff": {
            "require_substantive_action": True,
            "substantive_tools": ["web_search", "web_fetch", "write_file"],
            "forbid_as_first_only": [],
        },
        "completion": {
            "require_tool_calls": [
                {"name": "write_file", "args": {"path_regex": r"^memory/learnings/[^/]+\.md$"}}
            ]
        },
        "retry": {"enforcement_retries": 0, "failure_mode": "explain_missing"},
        "progress": {
            "claim_requires_actions": True,
            "claim_patterns": ["completed"],
            "milestones": {
                "enabled": True,
                "tool_call_interval": 1,
                "max_messages": 2,
                "templates": {
                    "kickoff": "CAP kickoff",
                    "researching": "CAP researching {source_calls}",
                    "completion_ready": "CAP completion",
                },
            },
        },
    }

    test_session = Session(key="web:test_chat:default#workflow-milestones-cap")
    loop.sessions.get_or_create = lambda key: test_session  # type: ignore[method-assign]
    loop.sessions.save = lambda session: None  # type: ignore[method-assign]

    async def _fake_build_context(*, session, current_message, media, channel, chat_id):
        return SimpleNamespace(
            messages=[
                {"role": "system", "content": "system"},
                {"role": "user", "content": current_message},
            ],
            session_state={},
            stats={"mode": "stateless", "matched_skills": ["deep-learn"]},
        )

    loop.context_manager.build_context = _fake_build_context  # type: ignore[method-assign]
    loop.context_manager.update_after_response = lambda session, response: None  # type: ignore[method-assign]
    loop.context.skills.get_tool_round_limited_skills = lambda skill_names: []  # type: ignore[method-assign]
    loop.context.skills.get_workflow_policy_for_skills = lambda skill_names: workflow_policy  # type: ignore[method-assign]
    loop.tools.execute = lambda name, arguments: asyncio.sleep(0, result="ok")  # type: ignore[method-assign]

    outbound = asyncio.run(
        loop._process_message(
            InboundMessage(
                channel="web",
                sender_id="user",
                chat_id="test_chat",
                content="deep research x",
                metadata={
                    "trace_id": "trace-workflow-milestones-cap",
                    "session_key": "web:test_chat:default#workflow-milestones-cap",
                },
            )
        )
    )

    assert outbound is not None
    assert outbound.content == "completed"
    assert bus.outbound_size == 2
    pushed = [asyncio.run(bus.consume_outbound()) for _ in range(2)]
    assert pushed[0].content == "CAP kickoff"
    assert pushed[1].content.startswith("CAP researching")


def test_memory_consolidation_filters_transient_env_failures(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    consolidation_payload = {
        "history_entry": (
            "[2026-02-15 11:12] 用户要求最新新闻并强调要链接。"
            "助手遇到 BRAVE_API_KEY not configured。"
        ),
        "memory_update": (
            "# Long-term Memory\n\n"
            "- 用户偏好：涉及最新新闻时必须附来源链接\n"
            "- 环境限制：BRAVE_API_KEY not configured\n"
        ),
    }
    provider = _SequenceProvider([
        LLMResponse(content=json.dumps(consolidation_payload, ensure_ascii=False)),
    ])
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=workspace,
        context_config=ContextConfig(enable_native_session=False),
    )

    session = Session(
        key="web:test_chat:default#memory",
        messages=[
            {
                "role": "user",
                "content": "帮我搜索今天 AI 新闻并给链接",
                "timestamp": "2026-02-15T11:12:00",
            },
            {
                "role": "assistant",
                "content": "暂时无法检索。",
                "timestamp": "2026-02-15T11:12:05",
            },
        ],
    )

    asyncio.run(loop._consolidate_memory(session, archive_all=True))

    memory = MemoryStore(workspace)
    history_text = (
        memory.history_file.read_text(encoding="utf-8")
        if memory.history_file.exists()
        else ""
    )
    memory_text = memory.read_long_term()

    assert "BRAVE_API_KEY" not in history_text
    assert "not configured" not in history_text.lower()
    assert "BRAVE_API_KEY" not in memory_text
    assert "not configured" not in memory_text.lower()
    assert "附来源链接" in memory_text


def test_memory_context_hides_transient_runtime_noise(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    memory = MemoryStore(workspace)
    memory.write_long_term(
        "# Long-term Memory\n\n"
        "- 用户偏好：新闻回答要有来源链接\n"
        "- 环境限制：BRAVE_API_KEY not configured\n"
    )

    context = memory.get_memory_context()

    assert "新闻回答要有来源链接" in context
    assert "BRAVE_API_KEY" not in context
    assert "not configured" not in context.lower()


def test_realtime_query_dedupes_identical_web_search_calls(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    provider = _SequenceProvider([
        LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    id="call_1",
                    name="web_search",
                    arguments={"query": "today ai news", "count": 5},
                )
            ],
        ),
        LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    id="call_2",
                    name="web_search",
                    arguments={"query": "today ai news", "count": 5},
                )
            ],
        ),
        LLMResponse(content="final answer"),
    ])
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=workspace,
        context_config=ContextConfig(enable_native_session=False),
    )

    test_session = Session(key="web:test_chat:default#realtime-dedupe")
    loop.sessions.get_or_create = lambda key: test_session  # type: ignore[method-assign]
    loop.sessions.save = lambda session: None  # type: ignore[method-assign]

    async def _fake_build_context(*, session, current_message, media, channel, chat_id):
        return SimpleNamespace(
            messages=[
                {"role": "system", "content": "system"},
                {"role": "user", "content": current_message},
            ],
            session_state={},
            stats={"mode": "stateless", "matched_skills": []},
        )

    loop.context_manager.build_context = _fake_build_context  # type: ignore[method-assign]
    loop.context_manager.update_after_response = lambda session, response: None  # type: ignore[method-assign]
    loop.context.skills.get_tool_round_limited_skills = lambda skill_names: []  # type: ignore[method-assign]
    loop.tools.get_definitions = _web_tool_definitions  # type: ignore[method-assign]

    execute_calls = {"count": 0}

    async def _fake_execute(name: str, arguments: dict[str, Any]) -> str:
        execute_calls["count"] += 1
        return "Results for: today ai news (provider: openai_hosted)"

    loop.tools.execute = _fake_execute  # type: ignore[method-assign]

    outbound = asyncio.run(
        loop._process_message(
            InboundMessage(
                channel="web",
                sender_id="user",
                chat_id="test_chat",
                content="帮我搜索今天 AI 领域最重要的三条新闻",
                metadata={
                    "trace_id": "trace-realtime-dedupe",
                    "session_key": "web:test_chat:default#realtime-dedupe",
                },
            )
        )
    )

    assert outbound is not None
    assert outbound.content == "final answer"
    assert execute_calls["count"] == 1


def test_attachment_request_autoinfers_existing_file_when_model_skips_tools(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "memory" / "learnings").mkdir(parents=True, exist_ok=True)
    file_path = workspace / "memory" / "learnings" / "js-performance-optimization.md"
    file_path.write_text("# notes\n", encoding="utf-8")

    provider = _SequenceProvider([
        LLMResponse(content="主人，已发你了，附件就是 `js-performance-optimization.md`。"),
    ])
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=workspace,
        context_config=ContextConfig(enable_native_session=False),
    )

    test_session = Session(key="web:test_chat:default#attachment-fallback")
    loop.sessions.get_or_create = lambda key: test_session  # type: ignore[method-assign]
    loop.sessions.save = lambda session: None  # type: ignore[method-assign]

    async def _fake_build_context(*, session, current_message, media, channel, chat_id):
        return SimpleNamespace(
            messages=[
                {"role": "system", "content": "system"},
                {"role": "user", "content": current_message},
            ],
            session_state={},
            stats={"mode": "stateless", "matched_skills": []},
        )

    loop.context_manager.build_context = _fake_build_context  # type: ignore[method-assign]
    loop.context_manager.update_after_response = lambda session, response: None  # type: ignore[method-assign]
    loop.context.skills.get_tool_round_limited_skills = lambda skill_names: []  # type: ignore[method-assign]

    outbound = asyncio.run(
        loop._process_message(
            InboundMessage(
                channel="web",
                sender_id="user",
                chat_id="test_chat",
                content="你把 `memory/learnings/js-performance-optimization.md` 作为文件发给我",
                metadata={
                    "trace_id": "trace-attachment-fallback",
                    "session_key": "web:test_chat:default#attachment-fallback",
                },
            )
        )
    )

    assert outbound is not None
    assert outbound.media == [str(file_path.resolve())]
    assert "已发你了" in outbound.content
    assert test_session.messages[-1]["media"] == [str(file_path.resolve())]


def test_attachment_claim_is_rewritten_when_no_media_can_be_resolved(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    provider = _SequenceProvider([
        LLMResponse(content="主人，已发你了，附件就是 `missing-file.md`。"),
    ])
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=workspace,
        context_config=ContextConfig(enable_native_session=False),
    )

    test_session = Session(key="web:test_chat:default#attachment-missing")
    loop.sessions.get_or_create = lambda key: test_session  # type: ignore[method-assign]
    loop.sessions.save = lambda session: None  # type: ignore[method-assign]

    async def _fake_build_context(*, session, current_message, media, channel, chat_id):
        return SimpleNamespace(
            messages=[
                {"role": "system", "content": "system"},
                {"role": "user", "content": current_message},
            ],
            session_state={},
            stats={"mode": "stateless", "matched_skills": []},
        )

    loop.context_manager.build_context = _fake_build_context  # type: ignore[method-assign]
    loop.context_manager.update_after_response = lambda session, response: None  # type: ignore[method-assign]
    loop.context.skills.get_tool_round_limited_skills = lambda skill_names: []  # type: ignore[method-assign]

    outbound = asyncio.run(
        loop._process_message(
            InboundMessage(
                channel="web",
                sender_id="user",
                chat_id="test_chat",
                content="把 missing-file.md 作为文件发给我",
                metadata={
                    "trace_id": "trace-attachment-missing",
                    "session_key": "web:test_chat:default#attachment-missing",
                },
            )
        )
    )

    assert outbound is not None
    assert outbound.media == []
    assert "还没有真正发出附件" in outbound.content


def test_attachment_followup_ack_is_suppressed_after_message_tool_delivery(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "memory" / "learnings").mkdir(parents=True, exist_ok=True)
    file_path = workspace / "memory" / "learnings" / "llm-memory-deep-research.md"
    file_path.write_text("# report\n", encoding="utf-8")

    provider = _SequenceProvider([
        LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    id="call_msg_1",
                    name="message",
                    arguments={
                        "content": "主人，已将报告文件发你，请查收。",
                        "media": [str(file_path.resolve())],
                    },
                )
            ],
        ),
        LLMResponse(content="主人，已发你附件，请查收。"),
    ])
    bus = MessageBus()
    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=workspace,
        context_config=ContextConfig(enable_native_session=False),
    )

    test_session = Session(key="web:test_chat:default#attachment-dedupe")
    loop.sessions.get_or_create = lambda key: test_session  # type: ignore[method-assign]
    loop.sessions.save = lambda session: None  # type: ignore[method-assign]

    async def _fake_build_context(*, session, current_message, media, channel, chat_id):
        return SimpleNamespace(
            messages=[
                {"role": "system", "content": "system"},
                {"role": "user", "content": current_message},
            ],
            session_state={},
            stats={"mode": "stateless", "matched_skills": []},
        )

    loop.context_manager.build_context = _fake_build_context  # type: ignore[method-assign]
    loop.context_manager.update_after_response = lambda session, response: None  # type: ignore[method-assign]
    loop.context.skills.get_tool_round_limited_skills = lambda skill_names: []  # type: ignore[method-assign]

    outbound = asyncio.run(
        loop._process_message(
            InboundMessage(
                channel="web",
                sender_id="user",
                chat_id="test_chat",
                content="把 llm-memory-deep-research.md 作为文件发给我",
                metadata={
                    "trace_id": "trace-attachment-dedupe",
                    "session_key": "web:test_chat:default#attachment-dedupe",
                },
            )
        )
    )

    # The file was already delivered by message tool; skip redundant text ack.
    assert outbound is None
    assert bus.outbound_size == 1
    sent = asyncio.run(bus.consume_outbound())
    assert sent.media == [str(file_path.resolve())]
    assert "发你" in sent.content
    # Session should contain the message-tool assistant entry, not a duplicate ack.
    assistant_messages = [m for m in test_session.messages if m["role"] == "assistant"]
    assert len(assistant_messages) == 1
    assert assistant_messages[0]["content"] == "主人，已将报告文件发你，请查收。"
