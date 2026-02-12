"""Artefact helpers — tasks.md parsing and manipulation."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Safety limits (mirroring TypeScript security.ts)
MAX_TASKS_FILE_SIZE = 512_000   # 512 KB
MAX_TASK_COUNT = 2000
MAX_LINE_LENGTH = 4000


@dataclass
class TaskItem:
    """A single checkbox task parsed from tasks.md."""

    index: int          # line index (0-based)
    text: str           # task text (after ``- [ ]``)
    step_id: str        # e.g. ``L42``
    checked: bool
    cmd: str | None     # extracted command if ``cmd: <command>``


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_UNCHECKED_RE = re.compile(r"^\s*-\s*\[ \]\s*(.+)\s*$")
_CHECKED_RE = re.compile(r"^\s*-\s*\[x\]\s*(.+)\s*$", re.IGNORECASE)
_CMD_RE = re.compile(r"^cmd:\s*(.+)$")


def parse_tasks(content: str) -> list[TaskItem]:
    """Parse all checkbox items from *content* (tasks.md)."""
    if len(content) > MAX_TASKS_FILE_SIZE:
        raise ValueError(f"Tasks file too large: {len(content)} bytes")

    lines = content.split("\n")
    if len(lines) > MAX_TASK_COUNT:
        raise ValueError(f"Too many lines in tasks file: {len(lines)}")

    items: list[TaskItem] = []
    for i, line in enumerate(lines):
        if len(line) > MAX_LINE_LENGTH:
            raise ValueError(f"Line too long at {i + 1}: {len(line)} chars")

        unchecked = _UNCHECKED_RE.match(line)
        checked_match = _CHECKED_RE.match(line) if not unchecked else None

        if unchecked:
            text = unchecked.group(1).strip()
            items.append(TaskItem(
                index=i,
                text=text,
                step_id=f"L{i + 1}",
                checked=False,
                cmd=_parse_cmd(text),
            ))
        elif checked_match:
            text = checked_match.group(1).strip()
            items.append(TaskItem(
                index=i,
                text=text,
                step_id=f"L{i + 1}",
                checked=True,
                cmd=_parse_cmd(text),
            ))

    return items


def find_next_unchecked(content: str) -> TaskItem | None:
    """Return the first unchecked task item, or ``None`` if all done."""
    for item in parse_tasks(content):
        if not item.checked:
            return item
    return None


def _parse_cmd(text: str) -> str | None:
    m = _CMD_RE.match(text)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------------------
# Mutation
# ---------------------------------------------------------------------------

def mark_task_checked(content: str, line_index: int) -> str:
    """Return *content* with the task at *line_index* checked."""
    lines = content.split("\n")
    lines[line_index] = lines[line_index].replace("[ ]", "[x]", 1)
    return "\n".join(lines)


def atomic_write_text(path: Path, content: str) -> None:
    """Atomically write *content* to *path* via tmp + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not content.endswith("\n"):
        content += "\n"

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        os.write(fd, content.encode("utf-8"))
        os.fsync(fd)
        os.close(fd)
        os.rename(tmp, str(path))
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Missing artefact check
# ---------------------------------------------------------------------------

REQUIRED_ARTIFACTS = ["proposal.md", "design.md", "tasks.md", "state.json"]


def find_missing_artifacts(change_dir: Path) -> list[str]:
    """Return list of required but missing artefact filenames."""
    return [f for f in REQUIRED_ARTIFACTS if not (change_dir / f).exists()]
