import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from nanobot.agent.loop import AgentLoop
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


def test_skill_tool_round_limit_forces_no_tool_summary(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    provider = _SequenceProvider([
        LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    id="call_1",
                    name="exec",
                    arguments={"command": "curl -s https://example.com/a"},
                )
            ],
        ),
        LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    id="call_2",
                    name="exec",
                    arguments={"command": "curl -s https://example.com/b"},
                )
            ],
        ),
        LLMResponse(content="forced summary"),
    ])
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=workspace,
        context_config=ContextConfig(
            enable_native_session=False,
            skill_tool_round_limit=2,
            skill_tool_stagnation_limit=0,
        ),
    )

    # Isolate from disk-backed session manager state.
    test_session = Session(key="web:test_chat:default#limit")
    loop.sessions.get_or_create = lambda key: test_session  # type: ignore[method-assign]
    loop.sessions.save = lambda session: None  # type: ignore[method-assign]

    async def _fake_build_context(*, session, current_message, media, channel, chat_id):
        return SimpleNamespace(
            messages=[
                {"role": "system", "content": "system"},
                {"role": "user", "content": current_message},
            ],
            session_state={},
            stats={"mode": "stateless", "matched_skills": ["weather"]},
        )

    loop.context_manager.build_context = _fake_build_context  # type: ignore[method-assign]
    loop.context_manager.update_after_response = lambda session, response: None  # type: ignore[method-assign]
    loop.context.skills.get_tool_round_limited_skills = lambda skill_names: ["weather"]  # type: ignore[method-assign]

    loop.tools.get_definitions = lambda: [  # type: ignore[method-assign]
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
        }
    ]

    executed: list[dict[str, Any]] = []

    async def _fake_execute(name: str, arguments: dict[str, Any]) -> str:
        executed.append({"name": name, "arguments": dict(arguments)})
        return "ok"

    loop.tools.execute = _fake_execute  # type: ignore[method-assign]

    outbound = asyncio.run(
        loop._process_message(
            InboundMessage(
                channel="web",
                sender_id="user",
                chat_id="test_chat",
                content="杭州明天天气如何？",
                metadata={
                    "trace_id": "trace-round-limit",
                    "session_key": "web:test_chat:default#limit",
                },
            )
        )
    )

    assert outbound is not None
    assert outbound.content == "forced summary"
    assert len(executed) == 2
    assert len(provider.calls) == 3
    assert provider.calls[0]["tool_choice"] == "required"
    assert provider.calls[2]["tools"] == []


def test_skill_tool_round_limit_not_applied_for_non_realtime_skill(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    provider = _SequenceProvider([
        LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    id="call_1",
                    name="exec",
                    arguments={"command": "echo first"},
                )
            ],
        ),
        LLMResponse(content="done without forced summary"),
    ])
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=workspace,
        context_config=ContextConfig(
            enable_native_session=False,
            skill_tool_round_limit=1,
            skill_tool_stagnation_limit=0,
        ),
    )

    test_session = Session(key="web:test_chat:default#nonlimit")
    loop.sessions.get_or_create = lambda key: test_session  # type: ignore[method-assign]
    loop.sessions.save = lambda session: None  # type: ignore[method-assign]

    async def _fake_build_context(*, session, current_message, media, channel, chat_id):
        return SimpleNamespace(
            messages=[
                {"role": "system", "content": "system"},
                {"role": "user", "content": current_message},
            ],
            session_state={},
            stats={"mode": "stateless", "matched_skills": ["github"]},
        )

    loop.context_manager.build_context = _fake_build_context  # type: ignore[method-assign]
    loop.context_manager.update_after_response = lambda session, response: None  # type: ignore[method-assign]
    loop.context.skills.get_tool_round_limited_skills = lambda skill_names: []  # type: ignore[method-assign]

    loop.tools.get_definitions = lambda: [  # type: ignore[method-assign]
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
        }
    ]

    async def _fake_execute(name: str, arguments: dict[str, Any]) -> str:
        return "ok"

    loop.tools.execute = _fake_execute  # type: ignore[method-assign]

    outbound = asyncio.run(
        loop._process_message(
            InboundMessage(
                channel="web",
                sender_id="user",
                chat_id="test_chat",
                content="run github command",
                metadata={
                    "trace_id": "trace-non-limit",
                    "session_key": "web:test_chat:default#nonlimit",
                },
            )
        )
    )

    assert outbound is not None
    assert outbound.content == "done without forced summary"
    assert len(provider.calls) == 2
