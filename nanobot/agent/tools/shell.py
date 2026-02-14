"""Shell execution tool."""

import asyncio
import os
import re
import shlex
import time
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool


class ExecTool(Tool):
    """Tool to execute shell commands."""

    def __init__(
        self,
        timeout: int = 60,
        working_dir: str | None = None,
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        restrict_to_workspace: bool = False,
    ):
        self.timeout = timeout
        self.working_dir = working_dir
        self.deny_patterns = deny_patterns or [
            r"\bdel\s+/[fq]\b",              # del /f, del /q
            r"\brmdir\s+/s\b",               # rmdir /s
            r"\b(mkfs|diskpart)\b",          # disk operations
            r"(?:^|[;&|]\s*)format\s+[a-z]:",  # windows format command
            r"\bdd\s+if=",                   # dd
            r">\s*/dev/sd",                  # write to disk
            r"\b(shutdown|reboot|poweroff)\b",  # system power
            r":\(\)\s*\{.*\};\s*:",          # fork bomb
        ]
        self.allow_patterns = allow_patterns or []
        self.restrict_to_workspace = restrict_to_workspace

    @property
    def name(self) -> str:
        return "exec"

    @property
    def description(self) -> str:
        return "Execute a shell command and return its output. Use with caution."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute"
                },
                "working_dir": {
                    "type": "string",
                    "description": "Optional working directory for the command"
                }
            },
            "required": ["command"]
        }

    async def execute(self, command: str, working_dir: str | None = None, **kwargs: Any) -> str:
        t_start = time.monotonic()
        cwd = working_dir or self.working_dir or os.getcwd()
        logger.debug(f"ExecTool start cwd={cwd} command={command[:200]}")
        guard_error = self._guard_command(command, cwd)
        if guard_error:
            logger.debug(
                f"ExecTool blocked elapsed={(time.monotonic() - t_start):.3f}s reason={guard_error}"
            )
            return guard_error

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                logger.warning(
                    f"ExecTool timeout after {(time.monotonic() - t_start):.3f}s command={command[:120]}"
                )
                return f"Error: Command timed out after {self.timeout} seconds"

            output_parts = []

            if stdout:
                output_parts.append(stdout.decode("utf-8", errors="replace"))

            if stderr:
                stderr_text = stderr.decode("utf-8", errors="replace")
                if stderr_text.strip():
                    output_parts.append(f"STDERR:\n{stderr_text}")

            if process.returncode != 0:
                output_parts.append(f"\nExit code: {process.returncode}")

            result = "\n".join(output_parts) if output_parts else "(no output)"

            # Truncate very long output
            max_len = 10000
            if len(result) > max_len:
                result = result[:max_len] + f"\n... (truncated, {len(result) - max_len} more chars)"

            logger.debug(
                f"ExecTool done exit={process.returncode} chars={len(result)} "
                f"elapsed={(time.monotonic() - t_start):.3f}s"
            )
            return result

        except Exception as e:
            logger.warning(
                f"ExecTool failed after {(time.monotonic() - t_start):.3f}s error={e}"
            )
            return f"Error executing command: {str(e)}"

    def _guard_command(self, command: str, cwd: str) -> str | None:
        """Best-effort safety guard for potentially destructive commands."""
        cmd = command.strip()
        lower = cmd.lower()

        rm_error = self._guard_recursive_rm(cmd, cwd)
        if rm_error:
            return rm_error

        for pattern in self.deny_patterns:
            if re.search(pattern, lower):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"

        if self.allow_patterns:
            if not any(re.search(p, lower) for p in self.allow_patterns):
                return "Error: Command blocked by safety guard (not in allowlist)"

        if self.restrict_to_workspace:
            if "..\\" in cmd or "../" in cmd:
                return "Error: Command blocked by safety guard (path traversal detected)"

            cwd_path = Path(cwd).resolve()

            win_paths = re.findall(r"[A-Za-z]:\\[^\\\"']+", cmd)
            # Only match absolute paths — avoid false positives on relative
            # paths like ".venv/bin/python" where "/bin/python" would be
            # incorrectly extracted by the old pattern.
            posix_paths = re.findall(r"(?:^|[\s|>])(/[^\s\"'>]+)", cmd)

            for raw in win_paths + posix_paths:
                try:
                    p = Path(raw.strip()).resolve()
                except Exception:
                    continue
                if p.is_absolute() and cwd_path not in p.parents and p != cwd_path:
                    return "Error: Command blocked by safety guard (path outside working dir)"

        return None

    def _guard_recursive_rm(self, command: str, cwd: str) -> str | None:
        """Allow recursive rm only for paths strictly inside cwd."""
        cwd_path = Path(cwd).resolve()
        for segment in re.split(r"(?:\|\||&&|[;|])", command):
            segment = segment.strip()
            if not segment:
                continue

            try:
                tokens = shlex.split(segment, posix=True)
            except ValueError:
                continue
            if not tokens:
                continue

            cmd_index = self._find_rm_index(tokens)
            if cmd_index is None:
                continue

            has_recursive = False
            targets: list[str] = []
            parse_as_targets = False
            for token in tokens[cmd_index + 1:]:
                if not parse_as_targets and token == "--":
                    parse_as_targets = True
                    continue
                if not parse_as_targets and token.startswith("-") and token != "-":
                    if "r" in token:
                        has_recursive = True
                    continue
                targets.append(token)

            if not has_recursive:
                continue
            if not targets:
                return "Error: Command blocked by safety guard (dangerous pattern detected)"

            for target in targets:
                if target in {"/", "~", ".", ".."}:
                    return "Error: Command blocked by safety guard (dangerous pattern detected)"
                if any(ch in target for ch in "*?[]"):
                    return "Error: Command blocked by safety guard (dangerous pattern detected)"

                path = Path(target).expanduser()
                if not path.is_absolute():
                    path = cwd_path / path
                try:
                    resolved = path.resolve()
                except Exception:
                    return "Error: Command blocked by safety guard (dangerous pattern detected)"

                if resolved == cwd_path or cwd_path not in resolved.parents:
                    return "Error: Command blocked by safety guard (dangerous pattern detected)"

        return None

    @staticmethod
    def _find_rm_index(tokens: list[str]) -> int | None:
        if not tokens:
            return None

        if Path(tokens[0]).name == "rm":
            return 0

        if Path(tokens[0]).name != "sudo":
            return None

        i = 1
        while i < len(tokens):
            token = tokens[i]
            if token == "--":
                i += 1
                break
            if not token.startswith("-"):
                break
            i += 1
            if token in {"-u", "-g", "-h", "-p", "-r", "-t", "-C", "-T"} and i < len(tokens):
                i += 1
        if i < len(tokens) and Path(tokens[i]).name == "rm":
            return i
        return None
