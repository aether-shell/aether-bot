"""上下文构建器，用于组装代理的提示词

本模块负责构建发送给LLM的完整上下文，包括:
- 系统提示词（身份、环境信息）
- 引导文件（AGENTS.md、SOUL.md等）
- 记忆上下文（Memory）
- 技能指令（Skills）
- 对话历史（History）
- 当前消息（含媒体文件）

Context Builder 是代理的"大脑装配工"，它将分散的信息源
组织成结构化的上下文，使LLM能够理解当前状态和可用能力。
"""

import base64
import hashlib
import mimetypes
import platform
from pathlib import Path
from typing import Any

from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader


class ContextBuilder:
    """上下文构建器，组装代理提示词的核心组件

    职责:
    1. 从引导文件（AGENTS.md等）中加载核心身份和指令
    2. 整合记忆上下文（Memory）提供长期知识
    3. 加载技能指令（Skills）扩展代理能力
    4. 组装对话历史和当前消息
    5. 处理媒体文件（图片base64编码）

    上下文组装顺序:
        系统提示词（身份）→ 引导文件 → 记忆 → 技能 → 历史 → 当前消息

    Attributes:
        workspace: 工作空间路径（存放配置、技能、记忆等）
        memory: 记忆存储器（MemoryStore实例）
        skills: 技能加载器（SkillsLoader实例）
        BOOTSTRAP_FILES: 引导文件列表（按加载顺序）
    """

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md", "IDENTITY.md"]

    def __init__(self, workspace: Path):
        """初始化上下文构建器

        Args:
            workspace: 工作空间路径（包含配置、技能、记忆等目录）
        """
        self.workspace = workspace
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(workspace)

    def build_system_prompt(self, skill_names: list[str] | None = None) -> str:
        """从引导文件、记忆和技能中构建系统提示词

        系统提示词的组装顺序:
        1. 核心身份（nanobot介绍、当前时间、运行环境、工作空间）
        2. 引导文件（AGENTS.md等开发者指令）
        3. 记忆上下文（MEMORY.md中的长期知识）
        4. 技能指令（always-loaded技能的完整内容 + 可用技能的摘要列表）

        技能加载策略（渐进式）:
        - Always-loaded技能: 完整注入到系���提示词
        - 其他技能: 仅显示摘要，代理需要时使用read_file工具加载

        Args:
            skill_names: 可选的技能名称列表（当前未使用，保留用于未来扩展）

        Returns:
            str: 完整的系统提示词
        """
        parts = []

        # Core identity
        parts.append(self._get_identity())

        # Bootstrap files (AGENTS.md is required)
        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)

        # Memory context
        memory = self.memory.get_memory_context()
        if memory:
            parts.append(f"# Memory\n\n{memory}")

        # Skills - progressive loading
        # 1. Always-loaded skills: include full content
        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")

        # 2. Available skills: only show summary (agent uses read_file to load)
        skills_summary = self.skills.build_skills_summary()
        if skills_summary:
            parts.append(f"""# Skills

The following skills extend your capabilities. To use a skill, read its SKILL.md file using the read_file tool.
Skills with available="false" need dependencies installed first - you can try installing them with apt/brew.

{skills_summary}""")

        return "\n\n---\n\n".join(parts)

    def _get_identity(self) -> str:
        """生成核心身份部分

        包含:
        - nanobot的身份和能力介绍
        - 当前时间（格式: YYYY-MM-DD HH:MM (星期)）
        - 运行环境（操作系统、架构、Python版本）
        - 工作空间路径和目录结构
        - 工具使用的重要说明

        Returns:
            str: 核心身份的Markdown文本
        """
        from datetime import datetime

        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        return f"""# nanobot 🐈

You are nanobot, a helpful AI assistant. You have access to tools that allow you to:
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

IMPORTANT: When responding to direct questions or conversations, reply directly with your text response.
Only use the 'message' tool when you need to send a message to a specific chat channel (like WhatsApp).
For normal conversation, just respond with text - do not call the message tool.

Feishu support: when asked to send files or images, use the 'message' tool with the `media` field.
This supports local file paths or URLs and will send real attachments.

Always be helpful, accurate, and concise. When using tools, explain what you're doing.
When remembering something, write to {workspace_path}/memory/MEMORY.md"""

    def _load_bootstrap_files(self) -> str:
        """从工作空间加载所有引导文件

        引导文件是开发者为代理提供的核心指令，包括:
        - AGENTS.md: **必需文件**，包含代理的核心行为指令
        - SOUL.md: 代理的性格和价值观
        - USER.md: 用户偏好和背景信息
        - TOOLS.md: 工具使用的高级指导
        - IDENTITY.md: 额外的身份定义

        如果AGENTS.md不存在，会抛出FileNotFoundError。

        Returns:
            str: 所有引导文件内容的拼接（Markdown格式）

        Raises:
            FileNotFoundError: 当AGENTS.md不存在时
        """
        parts = []

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

        return "\n\n".join(parts) if parts else ""

    def get_bootstrap_fingerprint(self) -> str:
        """返回引导文件的指纹，用于检测变更

        通过计算所有引导文件内容的SHA256哈希值生成指纹。
        当引导文件被修改时，指纹会发生变化，可用于触发会话重置。

        应用场景:
        - 检测AGENTS.md等核心指令的变更
        - 触发会话上下文的重新构建
        - 避免使用过期的缓存上下文

        Returns:
            str: SHA256哈希值（十六进制字符串）

        Raises:
            FileNotFoundError: 当AGENTS.md不存在时
        """
        agents_path = self.workspace / "AGENTS.md"
        if not agents_path.exists():
            raise FileNotFoundError(
                f"AGENTS.md is required but was not found at {agents_path}"
            )

        hasher = hashlib.sha256()
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
        """构建发送给LLM的完整消息列表

        消息列表的结构（OpenAI Chat Completions格式）:
        [
            {"role": "system", "content": "<系统提示词>"},
            {"role": "user", "content": "<历史消息1>"},
            {"role": "assistant", "content": "<历史响应1>"},
            ...
            {"role": "user", "content": "<当前消息>"}
        ]

        可选的会话摘要:
        如果提供了summary参数，会将其附加到系统提示词中，
        用于提供早期对话的压缩上下文（节省token）。

        媒体文件处理:
        如果提供了media参数（本地图片路径列表），会将图片转换为
        base64编码，并使用multimodal content格式发送给LLM。

        Args:
            history: 历史对话消息列表（{"role": "...", "content": "..."}格式）
            current_message: 当前用户消息文本
            skill_names: 可选的技能列表（当前未使用）
            media: 可选的本地媒体文件路径列表（支持图片）
            channel: 当前渠道名称（会添加到系统提示词中）
            chat_id: 当前聊天ID（会添加到系统提示词中）
            summary: 可选的会话摘要（早期对话的压缩版本）
            include_system: 是否包含系统提示词（默认True）

        Returns:
            list[dict[str, Any]]: 完整的消息列表
        """
        messages = []

        # System prompt
        if include_system:
            system_prompt = self.build_system_prompt(skill_names)
            if channel and chat_id:
                system_prompt += (
                    f"\n\n## Current Session\nChannel: {channel}\nChat ID: {chat_id}"
                )
            if summary:
                system_prompt += f"\n\n## Conversation Summary\n{summary}"
            messages.append({"role": "system", "content": system_prompt})

        # History
        messages.extend(history)

        # Current message (with optional image attachments)
        user_content = self._build_user_content(current_message, media)
        messages.append({"role": "user", "content": user_content})

        return messages

    def _build_user_content(
        self, text: str, media: list[str] | None
    ) -> str | list[dict[str, Any]]:
        """构建用户消息内容，支持base64编码的图片

        如果没有媒体文件，返回纯文本。
        如果有图片文件，返回multimodal content格式:
        [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}},
            {"type": "text", "text": "<消息文本>"}
        ]

        图片处理:
        - 验证文件存在且是图片类型（通过MIME type）
        - 读取文件并转换为base64编码
        - 使用data URI格式（data:<mime>;base64,<data>）

        Args:
            text: 消息文本内容
            media: 可选的本地文件路径列表

        Returns:
            str | list[dict]: 纯文本或multimodal content列表
        """
        if not media:
            return text

        images = []
        for path in media:
            p = Path(path)
            mime, _ = mimetypes.guess_type(path)
            if not p.is_file() or not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(p.read_bytes()).decode()
            images.append(
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
            )

        if not images:
            return text
        return images + [{"type": "text", "text": text}]

    def add_tool_result(
        self,
        messages: list[dict[str, Any]],
        tool_call_id: str,
        tool_name: str,
        result: str,
    ) -> list[dict[str, Any]]:
        """向消息列表添加工具执行结果

        工具调用流程:
        1. LLM返回工具调用请求（tool_calls）
        2. AgentLoop执行工具，获得结果
        3. 使用此方法将结果添加到消息列表
        4. 继续调用LLM，LLM会根据工具结果生成响应

        工具结果的消息格式:
        {
            "role": "tool",
            "tool_call_id": "<工具调用ID>",
            "name": "<工具名称>",
            "content": "<执行结果>"
        }

        Args:
            messages: 当前消息列表
            tool_call_id: 工具调用的唯一ID（由LLM生成）
            tool_name: 工具名称（如 read_file, web_search）
            result: 工具执行的结果（文本格式）

        Returns:
            list[dict[str, Any]]: 更新后的消息列表
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
        """向消息列表添加助手消息

        助手消息可以包含:
        1. 文本内容（content）
        2. 工具调用请求（tool_calls）
        3. 推理过程（reasoning_content，用于思维链模型）

        工具调用格式（OpenAI标准）:
        {
            "id": "call_abc123",
            "type": "function",
            "function": {
                "name": "read_file",
                "arguments": "{\"path\": \"...\"}"  # 必须是JSON字符串
            }
        }

        推理内容（reasoning_content）:
        某些模型（如Kimi、DeepSeek-R1）支持显式的思维链输出。
        如果历史消息中缺少reasoning_content字段，模型可能拒绝处理。

        Args:
            messages: 当前消息列表
            content: 助手的文本响应（可为空，如果只有工具调用）
            tool_calls: 可选��工具调用列表
            reasoning_content: 可选的推理过程文本（思维链模型）

        Returns:
            list[dict[str, Any]]: 更新后的消息列表
        """
        msg: dict[str, Any] = {"role": "assistant", "content": content or ""}

        if tool_calls:
            msg["tool_calls"] = tool_calls

        # Thinking models reject history without this
        if reasoning_content:
            msg["reasoning_content"] = reasoning_content

        messages.append(msg)
        return messages
