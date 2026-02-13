from pathlib import Path

from nanobot.agent.tools.shell import ExecTool


def _guard(command: str, cwd: Path) -> str | None:
    tool = ExecTool()
    return tool._guard_command(command, str(cwd))


def test_allows_format_query_parameter_in_url(tmp_path: Path) -> None:
    err = _guard("curl -s 'https://wttr.in/Hangzhou?format=j1'", tmp_path)
    assert err is None


def test_blocks_windows_format_command(tmp_path: Path) -> None:
    err = _guard("format c:", tmp_path)
    assert err is not None
    assert "blocked by safety guard" in err


def test_allows_recursive_rm_inside_cwd(tmp_path: Path) -> None:
    (tmp_path / "build").mkdir()
    err = _guard("rm -rf build", tmp_path)
    assert err is None


def test_blocks_recursive_rm_outside_cwd(tmp_path: Path) -> None:
    err = _guard("rm -rf /tmp", tmp_path)
    assert err is not None
    assert "blocked by safety guard" in err


def test_blocks_recursive_rm_of_cwd(tmp_path: Path) -> None:
    err = _guard("rm -rf .", tmp_path)
    assert err is not None
    assert "blocked by safety guard" in err


def test_blocks_recursive_rm_with_wildcard(tmp_path: Path) -> None:
    err = _guard("rm -rf *", tmp_path)
    assert err is not None
    assert "blocked by safety guard" in err
