"""Command security for Task-Mode execution.

Ports the safety checks from aetherctl/src/openspec/security.ts and
reuses patterns from nanobot/agent/tools/shell.py.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Whitelist of allowed base commands
# ---------------------------------------------------------------------------

ALLOWED_COMMANDS: list[str] = [
    # Package managers
    "npm", "pnpm", "yarn", "pip", "pip3", "uv", "poetry", "pipx",
    # Version control
    "git",
    # Build
    "make", "cmake", "cargo", "go", "mvn", "gradle",
    # Runtimes / interpreters
    "python", "python3", "node", "deno", "bun",
    # Linters / formatters
    "tsc", "eslint", "prettier", "ruff", "black", "isort", "mypy", "pylint",
    # Test runners
    "pytest", "vitest", "jest",
    # AI CLI tools
    "claude", "codex", "gemini", "opencode",
    # Common utilities
    "echo", "cat", "ls", "cp", "mv", "mkdir", "touch", "chmod",
    "grep", "find", "sed", "awk", "head", "tail", "wc", "sort", "uniq",
    "curl", "wget",
    "docker", "docker-compose",
    "bash", "sh", "zsh",
]

# ---------------------------------------------------------------------------
# Dangerous patterns — block regardless of whitelist
# ---------------------------------------------------------------------------

_DANGEROUS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+-[rf]{1,2}\s+/(?!\S*/openspec)"),   # rm -rf / (but allow within openspec)
    re.compile(r"\bdel\s+/[fq]", re.IGNORECASE),             # Windows del /f
    re.compile(r"\b(?:format|mkfs|diskpart)\b", re.IGNORECASE),
    re.compile(r"\bdd\s+if="),                                 # dd
    re.compile(r">\s*/dev/sd"),                                # write to raw disk
    re.compile(r"\b(?:shutdown|reboot|poweroff|init\s+0)\b"),  # power control
    re.compile(r":\(\)\s*\{.*\};\s*:"),                        # fork bomb
    re.compile(r"\bchmod\s+777\s+/"),                          # recursive world-writable root
    re.compile(r"\bcurl\b.*\|\s*(?:ba)?sh"),                   # curl | sh
    re.compile(r"\bwget\b.*\|\s*(?:ba)?sh"),                   # wget | sh
]

# Shell meta-characters that indicate injection risk
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"`"),            # backtick substitution
    re.compile(r"\$\("),         # $() subshell
    re.compile(r"\n|\r"),        # newlines
    re.compile(r">\s*/dev/"),    # device writes
]


@dataclass
class CommandValidation:
    """Result of command validation."""
    ok: bool
    command: str = ""
    args: list[str] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_command(cmd: str) -> CommandValidation:
    """Validate a command string from tasks.md.

    Checks:
    1. Not empty
    2. No dangerous patterns (rm -rf /, fork bomb, etc.)
    3. No shell injection patterns (backticks, $(), newlines)
    4. Base command is in the whitelist
    5. Parses into command + args for shell=False execution

    Returns a ``CommandValidation`` with either parsed command/args or
    an error message.
    """
    if not cmd or not cmd.strip():
        return CommandValidation(ok=False, error="Command cannot be empty")

    trimmed = cmd.strip()

    # Check dangerous patterns
    for pattern in _DANGEROUS_PATTERNS:
        if pattern.search(trimmed):
            return CommandValidation(
                ok=False,
                error=f"Command blocked (dangerous pattern): {trimmed[:100]}",
            )

    # Check injection patterns
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(trimmed):
            return CommandValidation(
                ok=False,
                error=f"Command blocked (shell injection pattern): {trimmed[:100]}",
            )

    # Reject chaining operators: ; && ||
    # But allow them inside quoted strings by using shlex
    # Simple heuristic: check unquoted occurrences
    if _has_unquoted_chaining(trimmed):
        return CommandValidation(
            ok=False,
            error=f"Command blocked (chaining operators not allowed): {trimmed[:100]}",
        )

    # Parse command into parts
    try:
        parts = shlex.split(trimmed)
    except ValueError as e:
        return CommandValidation(
            ok=False,
            error=f"Command parse error: {e}",
        )

    if not parts:
        return CommandValidation(ok=False, error="Command is empty after parsing")

    base_cmd = parts[0]

    # Allow path-qualified commands if the basename is whitelisted
    # e.g. /usr/bin/python3 -> python3, ./node_modules/.bin/eslint -> eslint
    base_name = os.path.basename(base_cmd)

    if base_name not in ALLOWED_COMMANDS:
        return CommandValidation(
            ok=False,
            error=f"Command not in whitelist: `{base_name}`. "
                  f"Allowed: {', '.join(sorted(ALLOWED_COMMANDS)[:20])}...",
        )

    return CommandValidation(
        ok=True,
        command=base_cmd,
        args=parts[1:],
    )


def sanitize_env() -> dict[str, str]:
    """Build a minimal safe environment for subprocess execution."""
    keys = ["PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "SHELL",
            "TMPDIR", "XDG_RUNTIME_DIR"]
    return {k: os.environ[k] for k in keys if k in os.environ}


def sanitize_log_message(s: str, max_len: int = 10_000) -> str:
    """Strip newlines/tabs to prevent log injection."""
    if not s:
        return ""
    return s.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")[:max_len]


def redact_sensitive(s: str) -> str:
    """Mask passwords, tokens, keys, and user paths in log output."""
    if not s:
        return ""
    result = s
    result = re.sub(r"(?i)password[=:]\s*\S+", "password=***", result)
    result = re.sub(r"(?i)token[=:]\s*\S+", "token=***", result)
    result = re.sub(r"(?i)api[_-]?key[=:]\s*\S+", "api_key=***", result)
    result = re.sub(r"(?i)secret[=:]\s*\S+", "secret=***", result)
    result = re.sub(r"/Users/[^/\s]+", "/Users/***", result)
    result = re.sub(r"/home/[^/\s]+", "/home/***", result)
    return result


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _has_unquoted_chaining(cmd: str) -> bool:
    """Check for ;, &&, || outside of quotes."""
    in_single = False
    in_double = False
    i = 0
    n = len(cmd)
    while i < n:
        c = cmd[i]
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if c == ";":
                return True
            if c == "&" and i + 1 < n and cmd[i + 1] == "&":
                return True
            if c == "|" and i + 1 < n and cmd[i + 1] == "|":
                return True
        i += 1
    return False
