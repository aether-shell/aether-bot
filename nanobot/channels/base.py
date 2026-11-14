"""聊天平台渠道的基类接口

本模块定义了所有聊天渠道的统一接口（BaseChannel）。

渠道（Channel）是nanobot与外部聊天平台的桥梁，负责:
- 连接到聊天平台（Telegram、Discord、Slack等）
- 接收用户消息并转换为InboundMessage
- 发送代理响应并转换为平台特定格式
- 管理认证和权限控制

架构模式:
    聊天平台 ← → 渠道实现 ← → MessageBus ← → AgentLoop

所有渠道必须实现:
1. start(): 启动渠道，开始监听消息
2. stop(): 停止渠道，清理资源
3. send(): 发送出站消息到聊天平台

已实现的渠道:
- Telegram, Discord, WhatsApp
- Feishu, DingTalk, Slack, QQ
- Email, SMS, CLI, Web
"""

import time
import uuid
from abc import ABC, abstractmethod
from typing import Any

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus


class BaseChannel(ABC):
    """聊天渠道的抽象基类

    定义了所有聊天渠道的统一接口，每个渠道（如Telegram、Discord）
    都必须实现这个接口才能与消息总线集成。

    渠道的生命周期:
    1. **初始化**: __init__() - 保存配置和消息总线引用
    2. **启动**: start() - 连接到聊天平台，开始监听
    3. **运行**: 持续接收消息，发送到MessageBus
    4. **停止**: stop() - 断开连接，清理资源

    消息流转:
    ```
    接收: 聊天平台 → start()轮询 → _handle_message() → MessageBus
    发送: MessageBus → 订阅回调 → send() → 聊天平台
    ```

    权限控制:
    - 通过config.allow_from白名单限制可访问用户
    - 未授权用户的消息会被拒绝

    Attributes:
        name: 渠道名称（如"telegram"、"slack"）
        config: 渠道特定配置（API密钥、Webhook URL等）
        bus: 消息总线实例
        _running: 渠道运行状态标志
    """

    name: str = "base"

    def __init__(self, config: Any, bus: MessageBus):
        """初始化渠道

        Args:
            config: 渠道特定的配置对象（包含API密钥、webhook等）
            bus: 消息总线实例（用于发布和订阅消息）
        """
        self.config = config
        self.bus = bus
        self._running = False

    @abstractmethod
    async def start(self) -> None:
        """启动渠道并开始监听消息

        这是一个长期运行的异步任务，应该:
        1. 连接到聊天平台（认证、建立WebSocket等）
        2. 开始监听入站消息（轮询或WebSocket）
        3. 将接收到的消息通过_handle_message()转发到消息总线

        实现方式:
        - **轮询模式**: while循环 + 定期调用API获取新消息
        - **WebSocket模式**: 建立长连接，监听消息事件
        - **Webhook模式**: 启动HTTP服务器，接收推送

        典型实现:
        ```python
        async def start(self):
            self._running = True
            # 订阅出站消息
            self.bus.subscribe_outbound(self.name, self.send)

            while self._running:
                # 获取新消息
                messages = await self._fetch_messages()
                for msg in messages:
                    await self._handle_message(
                        sender_id=msg.from_id,
                        chat_id=msg.chat_id,
                        content=msg.text
                    )
                await asyncio.sleep(1)
        ```
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """停止渠道并清理资源

        应该:
        1. 设置_running标志为False
        2. 关闭与聊天平台的连接
        3. 清理临时文件和缓存
        4. 取消订阅（如需要）
        """
        pass

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        """通过此渠道发送消息

        将代理生成的OutboundMessage转换为聊天平台的特定格式并发送。

        转换步骤:
        1. 提取msg.content、msg.chat_id、msg.media等
        2. 转换为平台API格式（如Telegram的sendMessage）
        3. 调用平台API发送
        4. 处理错误（重试、记录日志）

        特殊处理:
        - **流式响应**: 如果msg.metadata["stream"]=True，需要编辑之前的消息
        - **媒体文件**: 处理msg.media列表（上传图片、文件等）
        - **线程回复**: 使用msg.reply_to或metadata中的thread_id

        Args:
            msg: 待发送的出站消息

        Example:
            ```python
            async def send(self, msg: OutboundMessage):
                # Telegram示例
                await bot.send_message(
                    chat_id=int(msg.chat_id),
                    text=msg.content
                )
            ```
        """
        pass

    def is_allowed(self, sender_id: str) -> bool:
        """检查发送者是否被允许使用机器人

        实现基于白名单的权限控制，只有在allow_from列表中的用户
        才能与机器人交互。

        权限检查逻辑:
        1. 如果allow_from为空，允许所有人
        2. 如果sender_id在allow_from中，允许
        3. 如果sender_id包含"|"（组合ID），检查每个部分

        组合ID场景:
        某些平台使用"user_id|workspace_id"格式，
        可以在allow_from中只列出user_id部分。

        Args:
            sender_id: 发送者的标识符（平台特定格式）

        Returns:
            bool: True表示允许，False表示拒绝
        """
        allow_list = getattr(self.config, "allow_from", [])

        # 如果没有白名单，允许所有人
        if not allow_list:
            return True

        sender_str = str(sender_id)
        if sender_str in allow_list:
            return True
        # 支持组合ID（如"user|workspace"）
        if "|" in sender_str:
            for part in sender_str.split("|"):
                if part and part in allow_list:
                    return True
        return False

    async def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None
    ) -> None:
        """处理来自聊天平台的入站消息（内部方法）

        这是一个辅助方法，由子类的start()方法调用。
        负责权限检查、元数据添加和消息转发。

        处理流程:
        1. 检查发送者权限（is_allowed）
        2. 如果未授权，记录警告并拒绝
        3. 添加trace_id（用于日志追踪）
        4. 添加接收时间戳（用于性能监控）
        5. 构建InboundMessage
        6. 发布到消息总线

        Args:
            sender_id: 发送者标识符
            chat_id: 聊天/频道标识符
            content: 消息文本内容
            media: 可选的媒体文件URL列表
            metadata: 可选的渠道特定元数据（如Slack的thread_ts）

        Example:
            ```python
            # 在Telegram渠道中调用
            await self._handle_message(
                sender_id=str(update.message.from_user.id),
                chat_id=str(update.message.chat.id),
                content=update.message.text,
                metadata={"message_id": update.message.message_id}
            )
            ```
        """
        if not self.is_allowed(sender_id):
            logger.warning(
                f"Access denied for sender {sender_id} on channel {self.name}. "
                f"Add them to allowFrom list in config to grant access."
            )
            return

        # 构建元数据
        meta = dict(metadata or {})
        if "trace_id" not in meta:
            meta["trace_id"] = f"{self.name}-{uuid.uuid4().hex[:8]}"
        meta.setdefault("_received_at", time.monotonic())

        # 创建入站消息
        msg = InboundMessage(
            channel=self.name,
            sender_id=str(sender_id),
            chat_id=str(chat_id),
            content=content,
            media=media or [],
            metadata=meta
        )

        # 发布到消息总线
        await self.bus.publish_inbound(msg)

    @property
    def is_running(self) -> bool:
        """检查渠道是否正在运行

        Returns:
            bool: True表示渠道正在运行
        """
        return self._running
