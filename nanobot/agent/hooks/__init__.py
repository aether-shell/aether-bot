"""Hook framework for extending the agent message processing pipeline."""

from nanobot.agent.hooks.base import (
    HookAction,
    HookContext,
    HookPoint,
    HookResult,
    MessageHook,
)
from nanobot.agent.hooks.manager import HookManager

__all__ = [
    "HookAction",
    "HookContext",
    "HookManager",
    "HookPoint",
    "HookResult",
    "MessageHook",
]
