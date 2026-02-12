"""HookManager: registers and runs hooks at each pipeline stage."""

from __future__ import annotations

from loguru import logger

from nanobot.agent.hooks.base import (
    HookAction,
    HookContext,
    HookPoint,
    HookResult,
    MessageHook,
)


class HookManager:
    """Manages and executes registered message hooks."""

    def __init__(self) -> None:
        self._hooks: dict[HookPoint, list[MessageHook]] = {p: [] for p in HookPoint}

    def register(self, hook: MessageHook) -> None:
        """Register a hook.  Hooks are sorted by priority per hook point."""
        for point in hook.hook_points:
            hooks = self._hooks[point]
            hooks.append(hook)
            hooks.sort(key=lambda h: h.priority)
            logger.debug(f"Hook '{hook.name}' registered at {point.value} (priority {hook.priority})")

    def unregister(self, name: str) -> None:
        """Unregister a hook by name from all hook points."""
        for point in HookPoint:
            before = len(self._hooks[point])
            self._hooks[point] = [h for h in self._hooks[point] if h.name != name]
            if len(self._hooks[point]) < before:
                logger.debug(f"Hook '{name}' unregistered from {point.value}")

    async def run(self, point: HookPoint, ctx: HookContext) -> HookResult:
        """Execute all hooks registered at *point* in priority order.

        If any hook returns ``RESPOND`` or ``SKIP``, the pipeline is
        short-circuited and that result is returned immediately.
        Otherwise, context modifications accumulate and a ``CONTINUE``
        result is returned.
        """
        for hook in self._hooks[point]:
            try:
                result = await hook.execute(point, ctx)
            except Exception:
                logger.exception(f"Hook '{hook.name}' raised at {point.value}")
                continue

            if result.action in (HookAction.RESPOND, HookAction.SKIP):
                logger.debug(f"Hook '{hook.name}' short-circuited at {point.value} with {result.action.value}")
                return result

            if result.context is not None:
                ctx = result.context

        return HookResult(action=HookAction.CONTINUE, context=ctx)
