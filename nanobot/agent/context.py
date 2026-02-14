"""Context builder for assembling agent prompts."""

import base64
import hashlib
import mimetypes
import platform
import re
import time
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader


def _preview(text: str, max_chars: int = 160) -> str:
    safe = str(text or "").replace("\n", "\\n")
    if len(safe) <= max_chars:
        return safe
    return f"{safe[:max_chars]}...(truncated)"


class ContextBuilder:
    """
    Builds the context (system prompt + messages) for the agent.

    Assembles bootstrap files, memory, skills, and conversation history
    into a coherent prompt for the LLM.
    """

    DEFAULT_BOOTSTRAP_FILES = [
        "AGENTS.md",
        "SOUL.md",
        "IDENTITY.md",
        "ASSISTANT_RULES.md",
        "USER.md",
        "TOOLS.md",
        "HEARTBEAT.md",
    ]

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(workspace)

    @property
    def BOOTSTRAP_FILES(self) -> list[str]:
        """Load bootstrap file list from BOOTSTRAP.md, fall back to defaults."""
        bootstrap_path = self.workspace / "BOOTSTRAP.md"
        if not bootstrap_path.exists():
            return self.DEFAULT_BOOTSTRAP_FILES
        try:
            content = bootstrap_path.read_text(encoding="utf-8")
            # Match numbered list items like "1. SOUL.md"
            files = re.findall(r"^\d+\.\s+(\S+\.md)\s*$", content, re.MULTILINE)
            return files if files else self.DEFAULT_BOOTSTRAP_FILES
        except Exception:
            return self.DEFAULT_BOOTSTRAP_FILES

    def build_system_prompt(self, skill_names: list[str] | None = None) -> str:
        """
        Build the system prompt from bootstrap files, memory, and skills.

        Args:
            skill_names: Optional list of skills to include.

        Returns:
            Complete system prompt.
        """
        t_start = time.monotonic()
        parts = []
        section_stats: list[tuple[str, int]] = []

        # Core identity
        identity = self._get_identity()
        parts.append(identity)
        section_stats.append(("identity", len(identity)))

        # Bootstrap files (AGENTS.md is required)
        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)
            section_stats.append(("bootstrap", len(bootstrap)))

        # Memory context
        memory = self.memory.get_memory_context()
        if memory:
            memory_section = f"# Memory\n\n{memory}"
            parts.append(memory_section)
            section_stats.append(("memory", len(memory_section)))

        # Skills - progressive loading
        # 1. Always-loaded skills: include full content
        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                always_section = f"# Active Skills\n\n{always_content}"
                parts.append(always_section)
                section_stats.append(("active_skills", len(always_section)))

        # 1.5 Requested skills for current turn: include full content.
        requested_skills: list[str] = []
        if skill_names:
            seen = set(always_skills)
            for name in skill_names:
                if not name or name in seen:
                    continue
                requested_skills.append(name)
                seen.add(name)
        if requested_skills:
            requested_content = self.skills.load_skills_for_context(requested_skills)
            if requested_content:
                requested_section = f"""# Requested Skills (Current Turn)

The current user request matched specific skills. For this turn, these rules are mandatory:
1. Follow the requested skill workflow before free-form answering.
2. If the skill requires real-time or external data, call tools to fetch data first.
3. Do not guess or estimate real-time facts when a tool can retrieve them.
4. If a required tool fails, report the failure and provide a fallback path.

{requested_content}"""
                parts.append(requested_section)
                section_stats.append(("requested_skills", len(requested_section)))

        # 2. Available skills: only show summary (agent uses read_file to load)
        skills_summary = self.skills.build_skills_summary()
        if skills_summary:
            skills_section = f"""# Skills

Skill policy:
- When a user request matches a skill by name or trigger, prioritize that skill workflow.
- Read the skill's SKILL.md with read_file if you need full procedural details.
- Skills with available="false" need dependencies installed first - you can try installing them with apt/brew.

{skills_summary}"""
            parts.append(skills_section)
            section_stats.append(("skills_summary", len(skills_section)))

        prompt = "\n\n---\n\n".join(parts)
        logger.debug(
            f"ContextBuilder build_system_prompt sections={len(parts)} "
            f"chars={len(prompt)} section_stats={section_stats} "
            f"elapsed={(time.monotonic() - t_start):.3f}s"
        )
        return prompt

    def _get_identity(self) -> str:
        """Get the core identity section."""
        from datetime import datetime

        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        return f"""# AetherBot ⚛

You are AetherBot, an autonomous AI agent. You have access to tools that allow you to:
- Read, write, and edit files
- Execute shell commands
- Search the web and fetch web pages
- Send messages to users on chat channels
- Spawn subagents for complex background tasks

## Current Time
{now}

## Runtime
{runtime}

## Workspace
Your workspace is at: {workspace_path}
- Memory files: {workspace_path}/memory/MEMORY.md
- Daily notes: {workspace_path}/memory/YYYY-MM-DD.md
- Custom skills: {workspace_path}/skills/{{skill-name}}/SKILL.md

IMPORTANT:
- For casual conversation that does not need external data or a skill workflow, reply directly with text.
- When the request matches a skill workflow or depends on real-time/external facts, call relevant tools first and ground your answer in tool results.
- Only use the 'message' tool when you need to send a message to a specific chat channel (like WhatsApp).

Feishu support: when asked to send files or images, use the 'message' tool with the `media` field.
This supports local file paths or URLs and will send real attachments.

Always be helpful, accurate, and concise. When using tools, explain what you're doing.
When remembering something, write to {workspace_path}/memory/MEMORY.md"""

    def _load_bootstrap_files(self) -> str:
        """Load all bootstrap files from workspace."""
        t_start = time.monotonic()
        parts = []
        file_stats: list[tuple[str, int]] = []

        agents_path = self.workspace / "AGENTS.md"
        if not agents_path.exists():
            raise FileNotFoundError(
                f"AGENTS.md is required but was not found at {agents_path}"
            )

        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                if filename == "AGENTS.md":
                    parts.append(f"## Developer Instructions (AGENTS.md)\n\n{content}")
                else:
                    parts.append(f"## {filename}\n\n{content}")
                file_stats.append((filename, len(content)))

        combined = "\n\n".join(parts) if parts else ""
        logger.debug(
            f"ContextBuilder bootstrap loaded files={len(parts)} "
            f"chars={len(combined)} file_stats={file_stats} "
            f"elapsed={(time.monotonic() - t_start):.3f}s"
        )
        return combined

    def get_bootstrap_fingerprint(self) -> str:
        """Return a fingerprint of bootstrap files to detect changes."""
        agents_path = self.workspace / "AGENTS.md"
        if not agents_path.exists():
            raise FileNotFoundError(
                f"AGENTS.md is required but was not found at {agents_path}"
            )

        hasher = hashlib.sha256()

        # Include BOOTSTRAP.md itself so order changes trigger reset
        bootstrap_path = self.workspace / "BOOTSTRAP.md"
        if bootstrap_path.exists():
            hasher.update(b"BOOTSTRAP.md\0")
            hasher.update(bootstrap_path.read_bytes())
            hasher.update(b"\0")

        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if not file_path.exists():
                continue
            hasher.update(filename.encode("utf-8"))
            hasher.update(b"\0")
            try:
                hasher.update(file_path.read_bytes())
            except Exception:
                hasher.update(file_path.read_text(encoding="utf-8").encode("utf-8"))
            hasher.update(b"\0")

        return hasher.hexdigest()

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        summary: str | None = None,
        include_system: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Build the complete message list for an LLM call.

        Args:
            history: Previous conversation messages.
            current_message: The new user message.
            skill_names: Optional skills to include.
            media: Optional list of local file paths for images/media.
            channel: Current channel (telegram, feishu, etc.).
            chat_id: Current chat/user ID.

        Returns:
            List of messages including system prompt.
        """
        t_start = time.monotonic()
        messages = []
        system_chars = 0

        # System prompt
        if include_system:
            system_prompt = self.build_system_prompt(skill_names)
            if channel and chat_id:
                system_prompt += (
                    f"\n\n## Current Session\nChannel: {channel}\nChat ID: {chat_id}"
                )
            if summary:
                system_prompt += f"\n\n## Conversation Summary\n{summary}"
            system_chars = len(system_prompt)
            messages.append({"role": "system", "content": system_prompt})

        # History
        messages.extend(history)

        # Current message (with optional image attachments)
        user_content = self._build_user_content(current_message, media)
        messages.append({"role": "user", "content": user_content})
        message_stats: list[dict[str, Any]] = []
        for idx, msg in enumerate(messages):
            role = msg.get("role")
            content = msg.get("content")
            if isinstance(content, str):
                message_stats.append(
                    {"idx": idx, "role": role, "chars": len(content), "preview": _preview(content)}
                )
            elif isinstance(content, list):
                block_types = []
                for item in content[:8]:
                    if isinstance(item, dict):
                        block_types.append(str(item.get("type") or "dict"))
                    else:
                        block_types.append(type(item).__name__)
                message_stats.append(
                    {
                        "idx": idx,
                        "role": role,
                        "blocks": len(content),
                        "block_types": block_types,
                        "preview": _preview(str(content[:2]), max_chars=120),
                    }
                )
            else:
                content_text = str(content)
                message_stats.append(
                    {"idx": idx, "role": role, "chars": len(content_text), "preview": _preview(content_text)}
                )
        logger.debug(
            f"ContextBuilder build_messages include_system={include_system} "
            f"history={len(history)} media={len(media or [])} messages={len(messages)} "
            f"system_chars={system_chars} summary_chars={len(summary or '')} "
            f"current_chars={len(current_message or '')} message_stats={message_stats} "
            f"elapsed={(time.monotonic() - t_start):.3f}s"
        )
        return messages

    def _build_user_content(
        self, text: str, media: list[str] | None
    ) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded images."""
        if not media:
            return text

        images = []
        invalid_media: list[str] = []
        for path in media:
            p = Path(path)
            mime, _ = mimetypes.guess_type(path)
            if not p.is_file() or not mime or not mime.startswith("image/"):
                invalid_media.append(path)
                continue
            b64 = base64.b64encode(p.read_bytes()).decode()
            images.append(
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
            )

        if not images:
            logger.debug(
                f"ContextBuilder user_content media ignored count={len(media)} "
                f"invalid={invalid_media}"
            )
            return text
        logger.debug(
            f"ContextBuilder user_content media encoded valid={len(images)} invalid={len(invalid_media)} "
            f"text_chars={len(text or '')}"
        )
        return images + [{"type": "text", "text": text}]

    def add_tool_result(
        self,
        messages: list[dict[str, Any]],
        tool_call_id: str,
        tool_name: str,
        result: str,
    ) -> list[dict[str, Any]]:
        """
        Add a tool result to the message list.

        Args:
            messages: Current message list.
            tool_call_id: ID of the tool call.
            tool_name: Name of the tool.
            result: Tool execution result.

        Returns:
            Updated message list.
        """
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "content": result,
            }
        )
        return messages

    def add_assistant_message(
        self,
        messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Add an assistant message to the message list.

        Args:
            messages: Current message list.
            content: Message content.
            tool_calls: Optional tool calls.
            reasoning_content: Thinking output (Kimi, DeepSeek-R1, etc.).

        Returns:
            Updated message list.
        """
        msg: dict[str, Any] = {"role": "assistant", "content": content or ""}

        if tool_calls:
            msg["tool_calls"] = tool_calls

        # Thinking models reject history without this
        if reasoning_content:
            msg["reasoning_content"] = reasoning_content

        messages.append(msg)
        return messages
