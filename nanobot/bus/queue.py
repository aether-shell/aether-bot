"""Async message queue for decoupled channel-agent communication."""

import asyncio
import time
from typing import Awaitable, Callable

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage


class MessageBus:
    """
    Async message bus that decouples chat channels from the agent core.

    Channels push messages to the inbound queue, and the agent processes
    them and pushes responses to the outbound queue.
    """

    def __init__(self):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()
        self._outbound_subscribers: dict[str, list[Callable[[OutboundMessage], Awaitable[None]]]] = {}
        self._running = False

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish a message from a channel to the agent."""
        t_start = time.monotonic()
        await self.inbound.put(msg)
        trace_id = msg.metadata.get("trace_id") if isinstance(msg.metadata, dict) else None
        logger.debug(
            f"Bus inbound enqueue channel={msg.channel} chat_id={msg.chat_id} "
            f"trace={trace_id or 'n/a'} size={self.inbound.qsize()} "
            f"elapsed={(time.monotonic() - t_start):.3f}s"
        )

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message (blocks until available)."""
        t_start = time.monotonic()
        msg = await self.inbound.get()
        trace_id = msg.metadata.get("trace_id") if isinstance(msg.metadata, dict) else None
        logger.debug(
            f"Bus inbound dequeue channel={msg.channel} chat_id={msg.chat_id} "
            f"trace={trace_id or 'n/a'} size={self.inbound.qsize()} "
            f"wait={(time.monotonic() - t_start):.3f}s"
        )
        return msg

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish a response from the agent to channels."""
        t_start = time.monotonic()
        if not msg.metadata:
            msg.metadata = {}
        msg.metadata.setdefault("_enqueued_at", time.monotonic())
        await self.outbound.put(msg)
        trace_id = msg.metadata.get("trace_id") if isinstance(msg.metadata, dict) else None
        logger.debug(
            f"Bus outbound enqueue channel={msg.channel} chat_id={msg.chat_id} "
            f"trace={trace_id or 'n/a'} size={self.outbound.qsize()} "
            f"elapsed={(time.monotonic() - t_start):.3f}s"
        )

    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message (blocks until available)."""
        t_start = time.monotonic()
        msg = await self.outbound.get()
        trace_id = msg.metadata.get("trace_id") if isinstance(msg.metadata, dict) else None
        logger.debug(
            f"Bus outbound dequeue channel={msg.channel} chat_id={msg.chat_id} "
            f"trace={trace_id or 'n/a'} size={self.outbound.qsize()} "
            f"wait={(time.monotonic() - t_start):.3f}s"
        )
        return msg

    def subscribe_outbound(
        self,
        channel: str,
        callback: Callable[[OutboundMessage], Awaitable[None]]
    ) -> None:
        """Subscribe to outbound messages for a specific channel."""
        if channel not in self._outbound_subscribers:
            self._outbound_subscribers[channel] = []
        self._outbound_subscribers[channel].append(callback)
        logger.debug(
            f"Bus subscriber added channel={channel} "
            f"subscribers={len(self._outbound_subscribers[channel])}"
        )

    async def dispatch_outbound(self) -> None:
        """
        Dispatch outbound messages to subscribed channels.
        Run this as a background task.
        """
        self._running = True
        logger.debug("Bus outbound dispatcher loop started")
        while self._running:
            try:
                msg = await asyncio.wait_for(self.outbound.get(), timeout=1.0)
                subscribers = self._outbound_subscribers.get(msg.channel, [])
                trace_id = msg.metadata.get("trace_id") if isinstance(msg.metadata, dict) else None
                logger.debug(
                    f"Bus dispatch channel={msg.channel} subscribers={len(subscribers)} "
                    f"trace={trace_id or 'n/a'}"
                )
                for callback in subscribers:
                    t_cb = time.monotonic()
                    try:
                        await callback(msg)
                        logger.debug(
                            f"Bus dispatch callback channel={msg.channel} "
                            f"elapsed={(time.monotonic() - t_cb):.3f}s"
                        )
                    except Exception as e:
                        logger.error(f"Error dispatching to {msg.channel}: {e}")
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        """Stop the dispatcher loop."""
        self._running = False
        logger.debug("Bus outbound dispatcher loop stopping")

    @property
    def inbound_size(self) -> int:
        """Number of pending inbound messages."""
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        """Number of pending outbound messages."""
        return self.outbound.qsize()
