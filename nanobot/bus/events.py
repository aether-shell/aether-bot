"""消息总线的事件类型定义

本模块定义了消息总线中流转的两种核心消息类型:
- InboundMessage: 从聊天渠道接收的用户消息
- OutboundMessage: 发送回聊天渠道的代理响应
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class InboundMessage:
    """从聊天渠道接收的入站消息

    此数据类封装了用户发来的消息，包含渠道信息、发送者、内容、媒体文件等。
    消息将被发布到消息总线，由AgentLoop消费处理。

    Attributes:
        channel: 渠道名称（如 telegram, discord, slack, whatsapp）
        sender_id: 用户标识符（渠道特定的用户ID）
        chat_id: 聊天/频道标识符（用于回复路由）
        content: 消息文本内容
        timestamp: 消息时间戳（默认为当前时��）
        media: 媒体文件URL列表（图片、音频、视频等）
        metadata: 渠道特定的元数据（如Slack的thread_ts、Telegram的message_id等）
    """

    channel: str  # 渠道名称: telegram, discord, slack, whatsapp 等
    sender_id: str  # 用户标识符（渠道内的唯一ID）
    chat_id: str  # 聊天/频道标识符（个人聊天或群组ID）
    content: str  # 消息文本内容
    timestamp: datetime = field(default_factory=datetime.now)  # 消息时间戳
    media: list[str] = field(default_factory=list)  # 媒体文件URL列表
    metadata: dict[str, Any] = field(default_factory=dict)  # 渠道特定元数据

    @property
    def session_key(self) -> str:
        """生成用于会话识别的唯一键

        会话键决定了对话历史的分组方式。默认使用 "channel:chat_id" 格式，
        但渠道可以通过 metadata["session_key"] 提供自定义的会话键。

        这允许:
        - 跨渠道共享会话（例如 Telegram 和 Web 共享同一个会话）
        - 在同一渠道内创建多个独立会话

        Returns:
            str: 会话唯一标识符（格式: "channel:chat_id" 或自定义值）
        """
        override = None
        if isinstance(self.metadata, dict):
            override = self.metadata.get("session_key")
        if override:
            return str(override)
        return f"{self.channel}:{self.chat_id}"


@dataclass
class OutboundMessage:
    """发送到聊天渠道的出站消息

    此数据类封装了代理生成的响应，将被消息总线分发到对应的渠道。
    每个渠道的实现会将此消息转换为渠道特定的格式并发送。

    Attributes:
        channel: 目标渠道名称
        chat_id: 目标聊天/频道标识符
        content: 响应文本内容
        reply_to: 可选的原始消息ID（用于回复特定消息）
        media: 媒体文件列表（本地路径或URL）
        metadata: 渠道特定的元数据（如流式响应标志、线程ID等）
    """

    channel: str  # 目标渠道名称
    chat_id: str  # 目标聊天/频道标识符
    content: str  # 响应文本内容
    reply_to: str | None = None  # 可选的原始消息ID（用于回复功能）
    media: list[str] = field(default_factory=list)  # 媒体文件列表
    metadata: dict[str, Any] = field(default_factory=dict)  # 渠道特定元数据


