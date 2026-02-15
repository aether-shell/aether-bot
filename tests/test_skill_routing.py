import asyncio
import json
from pathlib import Path

from nanobot.agent.context import ContextBuilder
from nanobot.agent.context_manager import ContextManager
from nanobot.agent.skills import SkillsLoader
from nanobot.config.schema import ContextConfig
from nanobot.providers.base import LLMProvider, LLMResponse
from nanobot.session.manager import Session


class _NoopProvider(LLMProvider):
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
        return LLMResponse(content="")

    def get_default_model(self) -> str:
        return "test-model"


def _init_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "AGENTS.md").write_text("# AGENTS\n\nTest instructions.\n", encoding="utf-8")
    memory_dir = workspace / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "MEMORY.md").write_text("# Memory\n", encoding="utf-8")
    return workspace


def _write_skill(
    workspace: Path,
    name: str,
    description: str,
    triggers: list[str],
    allowed_tools: list[str] | None = None,
    extra_nanobot_meta: dict | None = None,
) -> None:
    skill_dir = workspace / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    nanobot_meta = {"triggers": triggers}
    if allowed_tools is not None:
        nanobot_meta["allowed_tools"] = allowed_tools
    if extra_nanobot_meta:
        nanobot_meta.update(extra_nanobot_meta)
    metadata = json.dumps({"nanobot": nanobot_meta}, ensure_ascii=False)
    content = (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"metadata: {metadata}\n"
        "---\n\n"
        f"# {name}\n\n"
        f"Use the {name} skill workflow.\n"
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")


def test_select_skills_for_message_matches_trigger_and_explicit_mention(tmp_path: Path) -> None:
    workspace = _init_workspace(tmp_path)
    _write_skill(
        workspace,
        name="weather",
        description="Get weather and forecast",
        triggers=["weather", "forecast", "天气", "气温"],
    )
    loader = SkillsLoader(workspace, builtin_skills_dir=workspace / "_builtin_skills")

    assert loader.select_skills_for_message("查一下杭州今天的天气") == ["weather"]
    assert loader.select_skills_for_message("please use $weather for this request") == ["weather"]


def test_build_system_prompt_includes_requested_skills_section(tmp_path: Path) -> None:
    workspace = _init_workspace(tmp_path)
    _write_skill(
        workspace,
        name="weather",
        description="Get weather and forecast",
        triggers=["weather", "forecast", "天气", "气温"],
    )

    builder = ContextBuilder(workspace)
    builder.skills = SkillsLoader(workspace, builtin_skills_dir=workspace / "_builtin_skills")

    prompt_without = builder.build_system_prompt(skill_names=None)
    assert "# Requested Skills (Current Turn)" not in prompt_without

    prompt_with = builder.build_system_prompt(skill_names=["weather"])
    assert "# Requested Skills (Current Turn)" in prompt_with
    assert "### Skill: weather" in prompt_with
    assert "Follow the requested skill workflow before free-form answering." in prompt_with
    assert "depends on real-time/external facts, call relevant tools first" in prompt_with
    assert "When responding to direct questions or conversations" not in prompt_with


def test_skill_allowed_tools_are_collected_in_stable_order(tmp_path: Path) -> None:
    workspace = _init_workspace(tmp_path)
    _write_skill(
        workspace,
        name="weather",
        description="Get weather and forecast",
        triggers=["weather"],
        allowed_tools=["exec", "web_fetch", "exec"],
    )
    _write_skill(
        workspace,
        name="github",
        description="Interact with github",
        triggers=["github"],
        allowed_tools=["exec", "list_dir"],
    )
    loader = SkillsLoader(workspace, builtin_skills_dir=workspace / "_builtin_skills")

    allowed_tools = loader.get_allowed_tools_for_skills(["weather", "github"])
    assert allowed_tools == ["exec", "web_fetch", "list_dir"]


def test_tool_round_limit_skills_use_metadata_flags(tmp_path: Path) -> None:
    workspace = _init_workspace(tmp_path)
    _write_skill(
        workspace,
        name="weather",
        description="Get weather and forecast",
        triggers=["weather"],
        allowed_tools=["exec"],
        extra_nanobot_meta={"tool_round_limit": True, "tags": ["realtime", "network"]},
    )
    _write_skill(
        workspace,
        name="github",
        description="Interact with github",
        triggers=["github"],
        allowed_tools=["exec"],
        extra_nanobot_meta={"tags": ["code", "repo"]},
    )
    loader = SkillsLoader(workspace, builtin_skills_dir=workspace / "_builtin_skills")

    limited = loader.get_tool_round_limited_skills(["weather", "github"])
    assert limited == ["weather"]


def test_context_manager_routes_and_exposes_matched_skills(tmp_path: Path) -> None:
    workspace = _init_workspace(tmp_path)
    _write_skill(
        workspace,
        name="weather",
        description="Get weather and forecast",
        triggers=["weather", "forecast", "天气", "气温"],
    )

    provider = _NoopProvider()
    builder = ContextBuilder(workspace)
    builder.skills = SkillsLoader(workspace, builtin_skills_dir=workspace / "_builtin_skills")
    manager = ContextManager(
        provider=provider,
        config=ContextConfig(enable_native_session=False),
        builder=builder,
        default_model="test-model",
    )
    session = Session(key="test:chat")

    bundle = asyncio.run(
        manager.build_context(
            session=session,
            current_message="查一下杭州今天的天气",
            media=None,
            channel="web",
            chat_id="test_chat",
        )
    )

    assert bundle.stats["matched_skills"] == ["weather"]
    system_text = bundle.messages[0]["content"]
    assert "# Requested Skills (Current Turn)" in system_text
    assert "### Skill: weather" in system_text


def test_web_session_prompt_explicitly_allows_attachments(tmp_path: Path) -> None:
    workspace = _init_workspace(tmp_path)
    provider = _NoopProvider()
    builder = ContextBuilder(workspace)
    builder.skills = SkillsLoader(workspace, builtin_skills_dir=workspace / "_builtin_skills")
    manager = ContextManager(
        provider=provider,
        config=ContextConfig(enable_native_session=False),
        builder=builder,
        default_model="test-model",
    )
    session = Session(key="web:test_chat")

    bundle = asyncio.run(
        manager.build_context(
            session=session,
            current_message="Please send the document as a file.",
            media=None,
            channel="web",
            chat_id="test_chat",
        )
    )

    system_text = bundle.messages[0]["content"]
    assert "## Web Channel Capabilities" in system_text
    assert "Attachment delivery is supported in this chat." in system_text
    assert "use the `message` tool with `media` paths/URLs." in system_text
