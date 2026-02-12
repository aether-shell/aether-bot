"""TaskModeHook — integrates Task-Mode into the agent pipeline via hooks."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from nanobot.agent.hooks.base import (
    HookAction,
    HookContext,
    HookPoint,
    HookResult,
    MessageHook,
)
from nanobot.agent.taskmode.control import execute_control, parse_control_command
from nanobot.agent.taskmode.detector import TaskDetector
from nanobot.agent.taskmode.planner import TaskPlanner
from nanobot.agent.taskmode.runner import TaskRunner
from nanobot.agent.taskmode.state import StateManager

if TYPE_CHECKING:
    from nanobot.providers.base import LLMProvider


class TaskModeHook(MessageHook):
    """Task-Mode hook: self-driven task execution integration.

    Listens on ``PRE_PROCESS`` and ``PRE_LLM``:

    * **PRE_PROCESS** — intercepts control commands (``/approve``,
      ``/cancel``, ``/status``, ``/resume``) and explicit ``/task``
      invocations.
    * **PRE_LLM** — injects active-task context and optionally detects
      task requests via LLM classification.
    """

    def __init__(
        self,
        workspace: Path,
        provider: LLMProvider,
        model: str | None = None,
        auto_detect: bool = True,
    ):
        self.workspace = workspace
        self.provider = provider
        self.model = model
        self.auto_detect = auto_detect

        self.state_mgr = StateManager()
        self.planner = TaskPlanner(
            workspace=workspace,
            provider=provider,
            state_mgr=self.state_mgr,
            model=model,
        )
        self.detector = TaskDetector(provider=provider, model=model)

    # ------ MessageHook interface ------

    @property
    def name(self) -> str:
        return "task_mode"

    @property
    def priority(self) -> int:
        return 50

    @property
    def hook_points(self) -> list[HookPoint]:
        return [HookPoint.PRE_PROCESS, HookPoint.PRE_LLM]

    async def execute(self, point: HookPoint, ctx: HookContext) -> HookResult:
        if point == HookPoint.PRE_PROCESS:
            return await self._pre_process(ctx)
        if point == HookPoint.PRE_LLM:
            return await self._pre_llm(ctx)
        return HookResult()

    # ------ PRE_PROCESS ------

    async def _pre_process(self, ctx: HookContext) -> HookResult:
        content = ctx.msg.content.strip()

        # 1. Control commands (/approve, /cancel, /status, /resume)
        cmd = parse_control_command(content)
        if cmd:
            cmd.requested_by = ctx.msg.sender_id

            def _runner_factory():
                return TaskRunner(
                    workspace=self.workspace,
                    state_mgr=self.state_mgr,
                    provider=self.provider,
                )

            result_text = await execute_control(
                cmd, self.workspace, runner_factory=_runner_factory
            )
            return HookResult(action=HookAction.RESPOND, response=result_text)

        # 2. Explicit /task command
        if content.startswith("/task "):
            description = content[6:].strip()
            if not description:
                return HookResult(
                    action=HookAction.RESPOND,
                    response="Usage: `/task <description>`",
                )
            plan = await self.planner.generate_plan(description, ctx.session)
            return HookResult(action=HookAction.RESPOND, response=plan.summary)

        return HookResult()  # CONTINUE

    # ------ PRE_LLM ------

    async def _pre_llm(self, ctx: HookContext) -> HookResult:
        # 1. Inject active task context
        active = self.state_mgr.list_active(self.workspace)
        if active:
            task_context = self._format_task_context(active)
            ctx.extra["task_context"] = task_context

        # 2. Auto-detect task requests
        if self.auto_detect:
            try:
                is_task = await self.detector.should_enter_task_mode(
                    ctx.msg, ctx.session
                )
                if is_task:
                    plan = await self.planner.generate_plan(
                        ctx.msg.content, ctx.session
                    )
                    return HookResult(
                        action=HookAction.RESPOND, response=plan.summary
                    )
            except Exception:
                logger.exception("Task-Mode auto-detection failed")

        return HookResult()

    # ------ helpers ------

    @staticmethod
    def _format_task_context(active_states: list) -> str:
        lines = ["## Active Tasks\n"]
        for s in active_states:
            lines.append(f"- `{s.change_id}`: **{s.status}**")
            if s.execution.current_task:
                lines.append(f"  Current: {s.execution.current_task}")
            if s.error.message:
                lines.append(f"  Error: {s.error.message}")
        return "\n".join(lines)
