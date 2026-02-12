"""Claude tool.

This tool provides an executable integration for Claude Code via the
`nanobot.skills.claude.runner` wrapper.

Rationale:
- The agent needs a reliable way to run Claude Code without asking the user to
  manually operate tmux.
- Prefer non-interactive `claude -p` when possible; fall back to a private tmux
  socket/session + JSONL event stream when TTY is required.

The heavy lifting is done by `nanobot/skills/claude/runner.py`.
"""

from __future__ import annotations

import os
import shlex
from typing import Any

from nanobot.agent.tools.base import Tool


class ClaudeTool(Tool):
    def __init__(
        self,
        timeout: int = 600,
        idle_timeout: int = 300,
        abort_on_idle: bool = True,
        mode: str = "auto",
    ):
        self.timeout = timeout
        self.idle_timeout = idle_timeout
        self.abort_on_idle = abort_on_idle
        self.mode = mode

    @property
    def name(self) -> str:
        return "claude"

    @property
    def description(self) -> str:
        return (
            "Run Claude Code via an internal runner. "
            "Uses `claude -p` when possible; falls back to a private tmux session when needed."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Prompt to send to Claude"},
                "cwd": {
                    "type": "string",
                    "description": "Working directory for Claude (defaults to current dir)",
                },
                "mode": {
                    "type": "string",
                    "enum": ["auto", "print", "tty"],
                    "description": "Execution mode (default: auto)",
                },
                "timeout": {"type": "integer", "description": "Overall timeout seconds"},
                "idle_timeout": {
                    "type": "integer",
                    "description": "Idle timeout seconds (TTY stream only)",
                },
                "abort_on_idle": {
                    "type": "boolean",
                    "description": "Send Ctrl+C to Claude when idle timeout triggers (TTY mode)",
                },
                "json": {
                    "type": "boolean",
                    "description": "Emit JSONL events instead of plain text",
                },
            },
            "required": ["prompt"],
        }

    async def execute(
        self,
        prompt: str,
        cwd: str | None = None,
        mode: str | None = None,
        timeout: int | None = None,
        idle_timeout: int | None = None,
        abort_on_idle: bool | None = None,
        json: bool | None = None,
        **kwargs: Any,
    ) -> str:
        # Local import to avoid importing asyncio subprocess helpers here.
        import asyncio

        cwd_ = cwd or os.getcwd()
        mode_ = mode or self.mode
        timeout_ = int(timeout or self.timeout)
        idle_timeout_ = int(idle_timeout or self.idle_timeout)
        abort_on_idle_ = self.abort_on_idle if abort_on_idle is None else bool(abort_on_idle)
        json_ = bool(json) if json is not None else False

        audit_dir = os.environ.get(
            "NANOBOT_CLAUDE_AUDIT_DIR",
            "/Users/macmini_no1/.aether-bot/workspace/agents/claude_audit/",
        )

        cmd = [
            "python3",
            "-m",
            "nanobot.skills.claude.runner",
            "--mode",
            mode_,
            "--cwd",
            cwd_,
            "--prompt",
            prompt,
            "--timeout",
            str(timeout_),
            "--idle-timeout",
            str(idle_timeout_),
            "--retries",
            "1",
            "--audit-dir",
            audit_dir,
            "--audit-tag",
            "tool",
        ]
        if abort_on_idle_:
            cmd.append("--abort-on-idle")
        if json_:
            cmd.append("--json")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd_,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_ + 10)
        except asyncio.TimeoutError:
            proc.kill()
            return "Error: Claude tool timed out (runner did not exit)"

        out = (stdout or b"").decode("utf-8", errors="replace")
        err = (stderr or b"").decode("utf-8", errors="replace")

        if proc.returncode != 0:
            tail = ""
            if err.strip():
                tail = "\nSTDERR:\n" + err
            elif out.strip():
                tail = "\nOUTPUT:\n" + out
            return f"Error: Claude runner failed (exit {proc.returncode}){tail}"

        if err.strip():
            # Preserve stderr as context but don't fail.
            return out + ("\n\n[stderr]\n" + err)

        return out if out.strip() else "(no output)"
