"""Hook base classes and data types for the message processing pipeline."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nanobot.bus.events import InboundMessage, OutboundMessage
    from nanobot.session.manager import Session


class HookPoint(Enum):
    """Lifecycle event points in the message processing pipeline."""

    PRE_PROCESS = "pre_process"
    PRE_LLM = "pre_llm"
    POST_LLM = "post_llm"
    POST_PROCESS = "post_process"


class HookAction(Enum):
    """Action type returned by a hook to control pipeline flow."""

    CONTINUE = "continue"
    RESPOND = "respond"
    SKIP = "skip"


@dataclass
class HookContext:
    """Mutable context passed through the hook pipeline.

    Fields are populated progressively as the message moves through
    the pipeline stages.
    """

    msg: InboundMessage
    session: Session
    messages: list[dict[str, Any]] | None = None
    response_content: str | None = None
    outbound: OutboundMessage | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookResult:
    """Result returned by a hook execution."""

    action: HookAction = HookAction.CONTINUE
    response: str | None = None
    context: HookContext | None = None


class MessageHook(ABC):
    """Base class for message processing hooks.

    Subclasses register for one or more ``HookPoint`` events and are
    executed in priority order (lower number = earlier execution).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Hook name used for logging and debugging."""

    @property
    def priority(self) -> int:
        """Execution priority.  Lower numbers run first.  Default 100."""
        return 100

    @property
    def hook_points(self) -> list[HookPoint]:
        """Lifecycle event points this hook listens to."""
        return [HookPoint.PRE_PROCESS]

    @abstractmethod
    async def execute(self, point: HookPoint, ctx: HookContext) -> HookResult:
        """Execute hook logic at the given *point*.

        Args:
            point: The current lifecycle event.
            ctx: The pipeline context.

        Returns:
            A ``HookResult`` indicating how the pipeline should proceed.
        """
