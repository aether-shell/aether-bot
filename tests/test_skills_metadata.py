from pathlib import Path

from nanobot.agent.skills import SkillsLoader


def test_all_builtin_skills_have_routing_metadata() -> None:
    loader = SkillsLoader(Path.cwd())
    skills = loader.list_skills(filter_unavailable=False)
    assert skills, "Expected at least one skill"

    for item in skills:
        name = item["name"]
        meta = loader._get_skill_meta(name)
        assert isinstance(meta, dict), f"{name} should expose parsed nanobot metadata"

        emoji = meta.get("emoji")
        assert isinstance(emoji, str) and emoji.strip(), f"{name} should define metadata.nanobot.emoji"

        triggers = meta.get("triggers")
        assert isinstance(triggers, list) and triggers, f"{name} should define non-empty metadata.nanobot.triggers"

        allowed_tools = meta.get("allowed_tools")
        assert isinstance(allowed_tools, list) and allowed_tools, (
            f"{name} should define non-empty metadata.nanobot.allowed_tools"
        )
        assert all(isinstance(tool, str) and tool.strip() for tool in allowed_tools), (
            f"{name} metadata.nanobot.allowed_tools should contain non-empty strings"
        )
