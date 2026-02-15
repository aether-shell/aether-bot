import asyncio
import os
from pathlib import Path

from nanobot.agent.tools.filesystem import ListDirTool, ReadFileTool, WriteFileTool


def test_write_file_relative_path_is_resolved_from_workspace_base(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    launcher_cwd = tmp_path / "launcher_cwd"
    launcher_cwd.mkdir(parents=True, exist_ok=True)

    tool = WriteFileTool(base_dir=workspace)
    target_rel = "memory/learnings/topic.md"

    old_cwd = Path.cwd()
    try:
        os.chdir(launcher_cwd)
        result = asyncio.run(tool.execute(path=target_rel, content="# hello\n"))
    finally:
        os.chdir(old_cwd)

    assert result.startswith("Successfully wrote")
    assert (workspace / target_rel).exists()
    assert not (launcher_cwd / target_rel).exists()


def test_read_and_list_relative_path_use_workspace_base(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    learnings_dir = workspace / "memory" / "learnings"
    learnings_dir.mkdir(parents=True, exist_ok=True)
    (learnings_dir / "abc.md").write_text("abc", encoding="utf-8")

    read_tool = ReadFileTool(base_dir=workspace)
    list_tool = ListDirTool(base_dir=workspace)

    listed = asyncio.run(list_tool.execute(path="memory/learnings"))
    content = asyncio.run(read_tool.execute(path="memory/learnings/abc.md"))

    assert "abc.md" in listed
    assert content == "abc"


def test_write_file_prevents_escape_when_allowed_dir_is_set(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside.md"

    tool = WriteFileTool(base_dir=workspace, allowed_dir=workspace)
    result = asyncio.run(tool.execute(path="../outside.md", content="escape"))

    assert result.startswith("Error:")
    assert "outside allowed directory" in result
    assert not outside.exists()
