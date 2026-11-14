"""异步消息队列，用于解耦渠道与代理的通信

本模块实现了基于发布-订阅模式的异步消息总线（MessageBus），
它在聊天渠道（Channels）和代理核心（AgentLoop）之间提供了一个解耦层。

架构流程:
    渠道接收消息 → publish_inbound → 消息队列 → AgentLoop消费
    AgentLoop处理 → publish_outbound → 消息队列 → 渠道订阅者接收

优势:
- 解耦: 渠道和代理互不感知，可独立开发和测试
- 异步: 所有操作都是非阻塞的
- 可扩展: 支持多个订阅者
"""

import asyncio
import time
from typing import Awaitable, Callable

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage


class MessageBus:
    """异步消息总线，解耦聊天渠道与代理核心

    消息总线维护两个队列:
    1. inbound队列: 存储来自渠道的用户消息，AgentLoop从此队列消费
    2. outbound队列: 存储代理的响应，通过订阅者分发到各个渠道

    渠道将消息推送到入站队列，代理处理后将响应推送到出站队列。
    出站队列通过订阅机制将消息分发到对应的渠道。

    Attributes:
        inbound: 入站消息队列（InboundMessage）
        outbound: 出站消息队列（OutboundMessage）
        _outbound_subscribers: 出站消息的订阅者映射（channel -> callbacks）
        _running: 消息分发循环的运行状态
    """

    def __init__(self):
        """初始化消息总线

        创建入站和出站的异步队列，初始化订阅者字典。
        """
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()
        self._outbound_subscribers: dict[str, list[Callable[[OutboundMessage], Awaitable[None]]]] = {}
        self._running = False

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """发布入站消息到队列

        渠道调用此方法将用户消息推送到队列中，AgentLoop会从队列中消费。

        Args:
            msg: 从渠道接收的用户消息
        """
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        """消费下一条入站消息

        AgentLoop调用此方法从队列中获取待处理的用户消息。
        如果队列为空，此方法会阻塞等待直到有新消息到达。

        Returns:
            InboundMessage: 待处理的用户消息
        """
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """发布出站消息到队列

        AgentLoop调用此方法将生成的响应推送到队列中，等待分发到渠道。
        会自动添加入队时间戳到metadata中，用于性能监控。

        Args:
            msg: 代理生成的响应消息
        """
        if not msg.metadata:
            msg.metadata = {}
        msg.metadata.setdefault("_enqueued_at", time.monotonic())
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        """消费下一条出站消息

        从出站队列中获取待发送的响应消息。
        如果队列为空，此方法会阻塞等待。

        Returns:
            OutboundMessage: 待发送的响应消息
        """
        return await self.outbound.get()

    def subscribe_outbound(
        self,
        channel: str,
        callback: Callable[[OutboundMessage], Awaitable[None]]
    ) -> None:
        """订阅特定渠道的出站消息

        渠道调用此方法注册一个回调函数，当有发往该渠道的消息时会被调用。
        支持多个订阅者订阅同一渠道。

        Args:
            channel: 渠道名称（如 telegram, slack, whatsapp）
            callback: 异步回调函数，接收 OutboundMessage 参数
        """
        if channel not in self._outbound_subscribers:
            self._outbound_subscribers[channel] = []
        self._outbound_subscribers[channel].append(callback)

    async def dispatch_outbound(self) -> None:
        """分发出站消息到订阅的渠道

        这是一个长期运行的后台任务，持续从出站队列中获取消息，
        并调用对应渠道的订阅者回调函数。

        消息分发流程:
        1. 从outbound队列获取消息（超时1秒）
        2. 查找该渠道的所有订阅者
        3. 异步调用每个订阅者的回调函数
        4. 捕获并记录任何分发错误，不影响其他订阅者

        建议作为 asyncio.create_task() 的后台任务运行。
        """
        self._running = True
        while self._running:
            try:
                msg = await asyncio.wait_for(self.outbound.get(), timeout=1.0)
                subscribers = self._outbound_subscribers.get(msg.channel, [])
                for callback in subscribers:
                    try:
                        await callback(msg)
                    except Exception as e:
                        logger.error(f"Error dispatching to {msg.channel}: {e}")
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        """停止消息分发循环

        设置运行标志为False，dispatch_outbound方法将在下一次循环时退出。
        """
        self._running = False

    @property
    def inbound_size(self) -> int:
        """获取待处理的入站消息数量

        Returns:
            int: 入站队列中的消息数量
        """
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        """获取待发送的出站消息数量

        Returns:
            int: 出站队列中的消息数量
        """
        return self.outbound.qsize()
