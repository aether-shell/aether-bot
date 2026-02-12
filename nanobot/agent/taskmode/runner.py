"""Task execution engine — runs through tasks.md steps."""

from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.taskmode.artifacts import (
    atomic_write_text,
    find_missing_artifacts,
    find_next_unchecked,
    mark_task_checked,
)
from nanobot.agent.taskmode.security import (
    redact_sensitive,
    sanitize_env,
    sanitize_log_message,
    validate_command,
)
from nanobot.agent.taskmode.state import (
    CANCELLED,
    CANCEL_REQUESTED,
    DONE,
    EXECUTING,
    FAILED,
    PAUSED_UNEXPECTED,
    PRECHECK,
    VERIFYING,
    WAIT_APPROVAL,
    BlockedInfo,
    ErrorInfo,
    PauseInfo,
    StateManager,
)

if TYPE_CHECKING:
    from nanobot.providers.base import LLMProvider

# Command execution limits
CMD_TIMEOUT = 300   # 5 minutes per command
MAX_OUTPUT = 10_000  # characters


class TaskRunner:
    """Phase-1 synchronous task execution engine.

    Walks through ``tasks.md`` checkboxes, executing ``cmd:`` items
    as shell commands and marking them done.
    """

    def __init__(
        self,
        workspace: Path,
        state_mgr: StateManager | None = None,
        provider: LLMProvider | None = None,
    ):
        self.workspace = workspace
        self.state_mgr = state_mgr or StateManager()
        self.provider = provider

    async def run(self, change_id: str) -> str:
        """Execute the full runner loop for *change_id*.

        Returns a human-readable status summary.
        """
        change_dir = self.state_mgr.change_dir(self.workspace, change_id)
        started_at = time.monotonic()

        # 1. Plan drift check
        try:
            drift_msg = self._check_plan_drift(change_dir)
            if drift_msg:
                return drift_msg
        except Exception as e:
            return f"Error checking plan drift: {e}"

        # 2. Preflight gate
        try:
            preflight_msg = self._run_preflight(change_dir, change_id)
            if preflight_msg:
                return preflight_msg
        except Exception as e:
            return f"Preflight error: {e}"

        # Transition to EXECUTING
        state = self.state_mgr.read(change_dir)
        state.status = EXECUTING
        state.execution.preflight_passed = True
        self._heartbeat(state)
        self.state_mgr.write(change_dir, state)

        # 3. Apply loop
        tasks_path = change_dir / "tasks.md"
        max_ticks = state.loop.max_ticks
        max_run_seconds = state.loop.max_run_seconds

        for tick in range(max_ticks):
            # Runtime limit
            elapsed = time.monotonic() - started_at
            if elapsed > max_run_seconds:
                self._fail(change_dir, "TIMEOUT", f"maxRunSeconds={max_run_seconds}")
                return f"Execution timed out after {int(elapsed)}s."

            # Cancellation check
            current_state = self.state_mgr.read(change_dir)
            if current_state.status == CANCEL_REQUESTED:
                current_state.status = CANCELLED
                self.state_mgr.write(change_dir, current_state)
                self.state_mgr.log(change_dir, current_state.execution.run_id, "CANCELLED")
                return "Cancelled."

            # Read tasks and find next unchecked
            tasks_content = tasks_path.read_text(encoding="utf-8")
            next_task = find_next_unchecked(tasks_content)

            # Update tick and heartbeat
            current_state.loop.ticks = tick + 1
            from datetime import datetime
            now = datetime.utcnow().isoformat() + "Z"
            current_state.loop.last_tick_at = now
            current_state.execution.current_task = next_task.text if next_task else None
            current_state.execution.current_step_id = next_task.step_id if next_task else None
            self._heartbeat(current_state)
            self.state_mgr.write(change_dir, current_state)

            if not next_task:
                break  # All tasks complete

            # Execute task
            if next_task.cmd:
                result = await self._execute_cmd(
                    change_dir, change_id, current_state.execution.run_id, next_task
                )
                if not result:
                    # Command failed — paused
                    return f"Paused: command failed at step {next_task.step_id}. Use `/resume {change_id}` after fixing."
            else:
                # No-op task (manual or description-only)
                self.state_mgr.log(
                    change_dir, current_state.execution.run_id,
                    f"STEP {next_task.step_id} COMPLETE (no-op)"
                )

            # Mark done
            tasks_content = tasks_path.read_text(encoding="utf-8")
            updated_content = mark_task_checked(tasks_content, next_task.index)
            atomic_write_text(tasks_path, updated_content)

        # 4. Verify
        state = self.state_mgr.read(change_dir)
        state.status = VERIFYING
        self._heartbeat(state)
        self.state_mgr.write(change_dir, state)

        verify_ok = await self._run_verify(change_dir, change_id)
        if verify_ok:
            state = self.state_mgr.read(change_dir)
            state.status = DONE
            state.execution.current_task = None
            state.execution.current_step_id = None
            self._heartbeat(state)
            self.state_mgr.write(change_dir, state)
            self.state_mgr.log(change_dir, state.execution.run_id, "DONE")
            return f"All tasks complete and verified. Change `{change_id}` is **DONE**."
        else:
            self._fail(change_dir, "VERIFY_FAILED", "Verification step failed")
            return f"Verification failed for `{change_id}`."

    # ------ internal steps ------

    def _check_plan_drift(self, change_dir: Path) -> str | None:
        """Return an error message if plan has drifted, else None."""
        state = self.state_mgr.read(change_dir)
        current_digest = self.state_mgr.compute_plan_digest(change_dir)
        approved_digest = state.approval.approved_plan_digest

        if not approved_digest or approved_digest != current_digest:
            state.status = WAIT_APPROVAL
            state.blocked.reason = "PLAN_DRIFT"
            state.blocked.needs = {
                "approvedPlanDigest": approved_digest,
                "currentPlanDigest": current_digest,
            }
            self.state_mgr.write(change_dir, state)
            return "Plan drift detected. Re-approval required."
        return None

    def _run_preflight(self, change_dir: Path, change_id: str) -> str | None:
        """Return an error message if preflight fails, else None."""
        missing = find_missing_artifacts(change_dir)
        if missing:
            state = self.state_mgr.read(change_dir)
            state.status = WAIT_APPROVAL
            state.execution.preflight_passed = False
            state.blocked.reason = "PREFLIGHT_FAILED"
            state.blocked.needs = {"missingArtifacts": missing}
            self.state_mgr.write(change_dir, state)
            self.state_mgr.log(
                change_dir, state.execution.run_id,
                f"PREFLIGHT_FAILED missing={','.join(missing)}"
            )
            return f"Preflight failed: missing {', '.join(missing)}"
        return None

    async def _execute_cmd(
        self,
        change_dir: Path,
        change_id: str,
        run_id: str | None,
        task: Any,
    ) -> bool:
        """Execute a ``cmd:`` task.  Returns True on success, False on failure."""
        raw_cmd = task.cmd

        # Validate command against whitelist and injection patterns
        validation = validate_command(raw_cmd)
        if not validation.ok:
            self.state_mgr.log(
                change_dir, run_id,
                f"STEP {task.step_id} BLOCKED {validation.error}"
            )
            self._pause_unexpected(
                change_dir,
                "CMD_BLOCKED",
                f"Command blocked: {redact_sensitive(raw_cmd)}",
                validation.error or "Command validation failed",
            )
            return False

        self.state_mgr.log(
            change_dir, run_id,
            f"STEP {task.step_id} RUN {redact_sensitive(raw_cmd)}"
        )

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [validation.command] + (validation.args or []),
                shell=False,
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=CMD_TIMEOUT,
                env=sanitize_env(),
            )

            if result.stdout.strip():
                output = sanitize_log_message(result.stdout.strip()[:MAX_OUTPUT])
                self.state_mgr.log(change_dir, run_id, f"STDOUT {output}")
            if result.stderr.strip():
                output = sanitize_log_message(result.stderr.strip()[:MAX_OUTPUT])
                self.state_mgr.log(change_dir, run_id, f"STDERR {output}")

            if result.returncode != 0:
                error_msg = redact_sensitive(
                    result.stderr.strip()[:500] or f"Exit code {result.returncode}"
                )
                self._pause_unexpected(
                    change_dir,
                    "CMD_FAILED",
                    f"Command failed: {redact_sensitive(raw_cmd)}",
                    f"Fix the issue and resume. Error: {error_msg}",
                )
                return False

            return True

        except subprocess.TimeoutExpired:
            self._pause_unexpected(
                change_dir,
                "CMD_TIMEOUT",
                f"Command timed out: {redact_sensitive(raw_cmd)}",
                f"Command exceeded {CMD_TIMEOUT}s timeout.",
            )
            return False
        except Exception as e:
            self._pause_unexpected(
                change_dir,
                "CMD_ERROR",
                f"Command error: {redact_sensitive(raw_cmd)}",
                redact_sensitive(str(e)),
            )
            return False

    async def _run_verify(self, change_dir: Path, change_id: str) -> bool:
        """Minimal verification: check git status succeeds."""
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["git", "status", "--porcelain=v1"],
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.returncode == 0
        except Exception as e:
            logger.warning(f"Verify failed: {e}")
            return False

    # ------ state helpers ------

    def _heartbeat(self, state: Any) -> None:
        from datetime import datetime
        state.execution.last_heartbeat_at = datetime.utcnow().isoformat() + "Z"

    def _pause_unexpected(
        self,
        change_dir: Path,
        code: str,
        request: str,
        resume_condition: str,
    ) -> None:
        from datetime import datetime
        now = datetime.utcnow().isoformat() + "Z"

        state = self.state_mgr.read(change_dir)
        state.status = PAUSED_UNEXPECTED
        state.blocked.reason = code
        state.blocked.needs = None
        state.blocked.pause = PauseInfo(
            kind=code,
            request=request,
            resume_condition=resume_condition,
            requested_at=now,
        )
        state.error = ErrorInfo(
            code=code,
            message=request,
            retryable=True,
            first_seen_at=state.error.first_seen_at or now,
            last_seen_at=now,
        )
        self.state_mgr.write(change_dir, state)

    def _fail(self, change_dir: Path, code: str, detail: str) -> None:
        from datetime import datetime
        now = datetime.utcnow().isoformat() + "Z"

        state = self.state_mgr.read(change_dir)
        state.status = FAILED
        state.error = ErrorInfo(
            code=code,
            message=detail,
            retryable=False,
            first_seen_at=state.error.first_seen_at or now,
            last_seen_at=now,
        )
        self.state_mgr.write(change_dir, state)
