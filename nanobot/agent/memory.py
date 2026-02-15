"""Memory system for persistent agent memory."""

import re
from datetime import datetime, timedelta
from pathlib import Path

from nanobot.utils.helpers import ensure_dir

_TRANSIENT_MEMORY_LINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b[A-Z][A-Z0-9_]*_API_KEY\b.*\bnot configured\b", re.IGNORECASE),
    re.compile(r"\bBRAVE_API_KEY\b", re.IGNORECASE),
    re.compile(r"\bTAVILY_API_KEY\b", re.IGNORECASE),
    re.compile(r"\bSEARXNG_BASE_URL\b", re.IGNORECASE),
    re.compile(r"\bOPENAI_API_KEY\b", re.IGNORECASE),
    re.compile(r"\bnot configured\b", re.IGNORECASE),
    re.compile(r"\bweb_search failed\b", re.IGNORECASE),
)


def _today_date() -> str:
    """Get today's date in YYYY-MM-DD format."""
    return datetime.now().strftime("%Y-%m-%d")


def _sanitize_memory_for_prompt(content: str) -> str:
    """Filter transient runtime diagnostics from prompt-facing memory context."""
    if not content:
        return ""
    kept_lines: list[str] = []
    for line in content.splitlines():
        if any(pattern.search(line) for pattern in _TRANSIENT_MEMORY_LINE_PATTERNS):
            continue
        kept_lines.append(line)
    return "\n".join(kept_lines).strip()


class MemoryStore:
    """
    Memory system for the agent.

    Primary storage is two-layer:
    - long-term facts: memory/MEMORY.md
    - searchable event log: memory/HISTORY.md

    Daily notes (memory/YYYY-MM-DD.md) are kept for backward compatibility.
    """

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "HISTORY.md"
        self.learnings_dir = self.memory_dir / "learnings"

    def get_today_file(self) -> Path:
        """Get path to today's memory file."""
        return self.memory_dir / f"{_today_date()}.md"

    def read_today(self) -> str:
        """Read today's memory notes."""
        today_file = self.get_today_file()
        if today_file.exists():
            return today_file.read_text(encoding="utf-8")
        return ""

    def append_today(self, content: str) -> None:
        """Append content to today's memory notes."""
        today_file = self.get_today_file()

        if today_file.exists():
            existing = today_file.read_text(encoding="utf-8")
            content = existing + "\n" + content
        else:
            header = f"# {_today_date()}\n\n"
            content = header + content

        today_file.write_text(content, encoding="utf-8")

    def read_long_term(self) -> str:
        """Read long-term memory (MEMORY.md)."""
        if self.memory_file.exists():
            return self.memory_file.read_text(encoding="utf-8")
        return ""

    def write_long_term(self, content: str) -> None:
        """Write to long-term memory (MEMORY.md)."""
        self.memory_file.write_text(content, encoding="utf-8")

    def append_history(self, entry: str) -> None:
        """Append an event entry to the searchable history log."""
        with open(self.history_file, "a", encoding="utf-8") as handle:
            handle.write(entry.rstrip() + "\n\n")

    def get_recent_memories(self, days: int = 7) -> str:
        """
        Get memories from the last N days.

        Args:
            days: Number of days to look back.

        Returns:
            Combined memory content.
        """
        memories = []
        today = datetime.now().date()

        for i in range(days):
            date = today - timedelta(days=i)
            file_path = self.memory_dir / f"{date.strftime('%Y-%m-%d')}.md"
            if file_path.exists():
                memories.append(file_path.read_text(encoding="utf-8"))

        return "\n\n---\n\n".join(memories)

    def list_memory_files(self) -> list[Path]:
        """List all memory files sorted by date (newest first)."""
        if not self.memory_dir.exists():
            return []

        files = list(self.memory_dir.glob("????-??-??.md"))
        return sorted(files, reverse=True)

    def list_learnings(self) -> list[dict[str, str]]:
        """List all learning files with topic names."""
        if not self.learnings_dir.exists():
            return []
        learnings = []
        for f in sorted(self.learnings_dir.glob("*.md")):
            if f.name.startswith("."):
                continue
            learnings.append({"name": f.stem, "path": str(f)})
        return learnings

    def get_memory_context(self) -> str:
        """
        Get memory context for the agent.

        Returns:
            Formatted memory context including long-term and today's notes.
        """
        parts = []

        long_term = _sanitize_memory_for_prompt(self.read_long_term())
        if long_term:
            parts.append("## Long-term Memory\n" + long_term)

        today = self.read_today()
        if today:
            parts.append("## Today's Notes\n" + today)

        # Knowledge Base listing
        learnings = self.list_learnings()
        if learnings:
            names = ", ".join(item["name"] for item in learnings)
            parts.append(
                f"## Knowledge Base\n"
                f"{len(learnings)} learned topics in memory/learnings/: {names}\n"
                f"Use recall skill or read_file to load specific knowledge."
            )

        return "\n\n".join(parts) if parts else ""
