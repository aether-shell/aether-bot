"""Control commands: /approve, /cancel, /status, /resume."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.taskmode.state import (
    CANCELLED,
    CANCEL_REQUESTED,
    DONE,
    EXECUTING,
    FAILED,
    PAUSED_UNEXPECTED,
    PLANNING,
    PRECHECK,
    RUNNING_STATUSES,
    TERMINAL_STATUSES,
    VERIFYING,
    WAIT_APPROVAL,
    StateManager,
    new_run_id,
)


@dataclass
class ControlCommand:
    action: str                 # approve | cancel | status | resume
    change_id: str | None = None
    user_input: str | None = None
    requested_by: str | None = None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_control_command(text: str) -> ControlCommand | None:
    """Parse a control slash command.  Returns ``None`` if not a control cmd."""
    text = text.strip()

    if text.startswith("/approve"):
        parts = text.split(maxsplit=1)
        cid = parts[1].strip() if len(parts) > 1 else None
        return ControlCommand(action="approve", change_id=cid)

    if text.startswith("/cancel"):
        parts = text.split(maxsplit=1)
        cid = parts[1].strip() if len(parts) > 1 else None
        return ControlCommand(action="cancel", change_id=cid)

    if text.startswith("/status"):
        parts = text.split(maxsplit=1)
        cid = parts[1].strip() if len(parts) > 1 else None
        return ControlCommand(action="status", change_id=cid)

    if text.startswith("/resume"):
        parts = text.split(maxsplit=2)
        cid = parts[1].strip() if len(parts) > 1 else None
        user_input = parts[2].strip() if len(parts) > 2 else ""
        return ControlCommand(action="resume", change_id=cid, user_input=user_input)

    return None


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

async def execute_control(
    cmd: ControlCommand,
    workspace: Path,
    runner_factory: Any = None,
) -> str:
    """Dispatch a control command and return a human-readable response."""
    state_mgr = StateManager()

    if cmd.action == "approve":
        return await _handle_approve(cmd, workspace, state_mgr, runner_factory)
    if cmd.action == "cancel":
        return _handle_cancel(cmd, workspace, state_mgr)
    if cmd.action == "status":
        return _handle_status(cmd, workspace, state_mgr)
    if cmd.action == "resume":
        return await _handle_resume(cmd, workspace, state_mgr, runner_factory)

    return f"Unknown control command: {cmd.action}"


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _resolve_change_id(cmd: ControlCommand, workspace: Path, state_mgr: StateManager) -> str | None:
    """Resolve change_id from command or auto-detect the single active change."""
    if cmd.change_id:
        return cmd.change_id

    active = state_mgr.list_active(workspace)
    if len(active) == 1:
        return active[0].change_id
    if len(active) == 0:
        return None
    return None  # Ambiguous


async def _handle_approve(
    cmd: ControlCommand,
    workspace: Path,
    state_mgr: StateManager,
    runner_factory: Any,
) -> str:
    change_id = _resolve_change_id(cmd, workspace, state_mgr)
    if not change_id:
        active = state_mgr.list_active(workspace)
        if not active:
            return "No active changes to approve."
        ids = ", ".join(s.change_id for s in active)
        return f"Multiple active changes. Specify one: {ids}"

    change_dir = state_mgr.change_dir(workspace, change_id)
    if not state_mgr.state_path(change_dir).exists():
        return f"Change `{change_id}` not found."

    state = state_mgr.read(change_dir)
    if state.status != WAIT_APPROVAL:
        return f"Cannot approve: current status is **{state.status}**."

    # Prevent concurrent runs
    running = [s for s in state_mgr.list_active(workspace) if s.status in RUNNING_STATUSES]
    if running:
        return f"Cannot approve: change `{running[0].change_id}` is currently running."

    # Set approval
    from datetime import datetime
    digest = state_mgr.compute_plan_digest(change_dir)
    now = datetime.utcnow().isoformat() + "Z"

    state.approval.approved = True
    state.approval.approved_at = now
    state.approval.approved_by = cmd.requested_by
    state.approval.approved_plan_digest = digest
    state.plan.plan_digest = digest
    state.plan.head_sha = _git_head_sha(workspace)
    state.execution.run_id = new_run_id()
    state.execution.attempt += 1
    state.execution.started_at = now
    state.execution.last_heartbeat_at = now
    state.status = PRECHECK
    # Clear blocked
    from nanobot.agent.taskmode.state import BlockedInfo, PauseInfo
    state.blocked = BlockedInfo()
    state_mgr.write(change_dir, state)

    # Run the task
    if runner_factory:
        runner = runner_factory()
        result = await runner.run(change_id)
        return result

    return f"Change `{change_id}` approved and transitioned to PRECHECK."


def _handle_cancel(
    cmd: ControlCommand,
    workspace: Path,
    state_mgr: StateManager,
) -> str:
    change_id = _resolve_change_id(cmd, workspace, state_mgr)
    if not change_id:
        active = state_mgr.list_active(workspace)
        if not active:
            return "No active changes to cancel."
        ids = ", ".join(s.change_id for s in active)
        return f"Multiple active changes. Specify one: {ids}"

    change_dir = state_mgr.change_dir(workspace, change_id)
    if not state_mgr.state_path(change_dir).exists():
        return f"Change `{change_id}` not found."

    state = state_mgr.read(change_dir)

    if state.status in TERMINAL_STATUSES:
        return f"Change `{change_id}` is already **{state.status}**."

    # Immediate cancel for non-running states
    if state.status in {WAIT_APPROVAL, PLANNING, PAUSED_UNEXPECTED}:
        state.status = CANCELLED
        state_mgr.write(change_dir, state)
        return f"Change `{change_id}` cancelled."

    # For running states, request cancellation (loop will pick it up)
    state.status = CANCEL_REQUESTED
    state_mgr.write(change_dir, state)
    return f"Cancellation requested for `{change_id}` (currently {state.status})."


def _handle_status(
    cmd: ControlCommand,
    workspace: Path,
    state_mgr: StateManager,
) -> str:
    # If a specific change is requested
    if cmd.change_id:
        change_dir = state_mgr.change_dir(workspace, cmd.change_id)
        if not state_mgr.state_path(change_dir).exists():
            return f"Change `{cmd.change_id}` not found."
        state = state_mgr.read(change_dir)
        return _format_status(state)

    # Otherwise list all active changes
    active = state_mgr.list_active(workspace)
    if not active:
        return "No active changes."

    parts = []
    for s in active:
        parts.append(_format_status(s))
    return "\n\n---\n\n".join(parts)


async def _handle_resume(
    cmd: ControlCommand,
    workspace: Path,
    state_mgr: StateManager,
    runner_factory: Any,
) -> str:
    change_id = _resolve_change_id(cmd, workspace, state_mgr)
    if not change_id:
        active = state_mgr.list_active(workspace)
        if not active:
            return "No active changes to resume."
        ids = ", ".join(s.change_id for s in active)
        return f"Multiple active changes. Specify one: {ids}"

    change_dir = state_mgr.change_dir(workspace, change_id)
    if not state_mgr.state_path(change_dir).exists():
        return f"Change `{change_id}` not found."

    state = state_mgr.read(change_dir)

    if state.status in {DONE, CANCELLED}:
        return f"Change `{change_id}` is already **{state.status}**."
    if state.status == FAILED:
        return f"Change `{change_id}` has **FAILED** and cannot be resumed."
    if state.status != PAUSED_UNEXPECTED:
        return f"Cannot resume: current status is **{state.status}** (expected PAUSED_UNEXPECTED)."

    # Resolve pause
    from datetime import datetime
    now = datetime.utcnow().isoformat() + "Z"

    state.blocked.pause.user_input = cmd.user_input
    state.blocked.pause.resolved_at = now
    state.error.code = None
    state.error.message = None
    state.error.detail = None
    state.error.retryable = None
    state.error.last_seen_at = None
    state.execution.last_heartbeat_at = now
    state.status = PRECHECK
    state_mgr.write(change_dir, state)

    if runner_factory:
        runner = runner_factory()
        result = await runner.run(change_id)
        return result

    return f"Change `{change_id}` resumed and transitioned to PRECHECK."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_status(state: Any) -> str:
    """Format a TaskState into a readable status string."""
    lines = [
        f"**Change**: `{state.change_id}`",
        f"**Status**: {state.status}",
    ]
    if state.execution.current_task:
        lines.append(f"**Current task**: {state.execution.current_task}")
    if state.loop.ticks:
        lines.append(f"**Progress**: tick {state.loop.ticks}/{state.loop.max_ticks}")
    if state.error.message:
        lines.append(f"**Error**: {state.error.message}")
    if state.blocked.reason:
        lines.append(f"**Blocked**: {state.blocked.reason}")
    return "\n".join(lines)


def _git_head_sha(workspace: Path) -> str | None:
    """Get the current git HEAD SHA, or None."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None
