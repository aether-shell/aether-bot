"""Task-Mode state management — atomic read/write of state.json."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from loguru import logger


# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------

PLANNING = "PLANNING"
WAIT_APPROVAL = "WAIT_APPROVAL"
PRECHECK = "PRECHECK"
EXECUTING = "EXECUTING"
VERIFYING = "VERIFYING"
CANCEL_REQUESTED = "CANCEL_REQUESTED"
PAUSED_UNEXPECTED = "PAUSED_UNEXPECTED"
DONE = "DONE"
CANCELLED = "CANCELLED"
FAILED = "FAILED"

TERMINAL_STATUSES = {DONE, CANCELLED, FAILED}
RUNNING_STATUSES = {PRECHECK, EXECUTING, VERIFYING}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ApprovalInfo:
    approved: bool = False
    approved_at: str | None = None
    approved_by: str | None = None
    approved_plan_digest: str | None = None


@dataclass
class PlanInfo:
    plan_digest: str | None = None
    head_sha: str | None = None


@dataclass
class ExecutionInfo:
    run_id: str | None = None
    attempt: int = 0
    preflight_passed: bool = False
    current_task: str | None = None
    current_step_id: str | None = None
    started_at: str | None = None
    last_heartbeat_at: str | None = None


@dataclass
class LoopInfo:
    last_tick_at: str | None = None
    ticks: int = 0
    max_ticks: int = 200
    max_run_seconds: int = 1800


@dataclass
class PauseInfo:
    kind: str | None = None
    request: str | None = None
    unblock_command: str | None = None
    resume_condition: str | None = None
    user_input: str | None = None
    requested_at: str | None = None
    resolved_at: str | None = None


@dataclass
class BlockedInfo:
    reason: str | None = None
    needs: Any | None = None
    pause: PauseInfo = field(default_factory=PauseInfo)


@dataclass
class ErrorInfo:
    code: str | None = None
    message: str | None = None
    detail: Any | None = None
    retryable: bool | None = None
    first_seen_at: str | None = None
    last_seen_at: str | None = None


@dataclass
class TaskState:
    """OpenSpec change state, mirroring the TypeScript ``OpenSpecStateV1``."""

    schema_version: int = 1
    change_id: str = ""
    repo_path: str = ""
    status: str = PLANNING
    created_at: str = ""
    updated_at: str = ""
    approval: ApprovalInfo = field(default_factory=ApprovalInfo)
    plan: PlanInfo = field(default_factory=PlanInfo)
    execution: ExecutionInfo = field(default_factory=ExecutionInfo)
    loop: LoopInfo = field(default_factory=LoopInfo)
    blocked: BlockedInfo = field(default_factory=BlockedInfo)
    error: ErrorInfo = field(default_factory=ErrorInfo)


# ---------------------------------------------------------------------------
# Serialisation helpers (snake_case <-> camelCase)
# ---------------------------------------------------------------------------

def _to_camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _to_snake(name: str) -> str:
    import re
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name).lower()


def _state_to_dict(state: TaskState) -> dict[str, Any]:
    """Serialise *state* to a JSON-friendly dict with **camelCase** keys."""

    def _convert(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {_to_camel(k): _convert(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_convert(i) for i in obj]
        return obj

    return _convert(asdict(state))


def _dict_to_state(data: dict[str, Any]) -> TaskState:
    """Deserialise a camelCase dict into a ``TaskState``."""

    def _snake_dict(d: dict) -> dict:
        return {_to_snake(k): _snake_dict(v) if isinstance(v, dict) else v for k, v in d.items()}

    flat = _snake_dict(data)

    return TaskState(
        schema_version=flat.get("schema_version", 1),
        change_id=flat.get("change_id", ""),
        repo_path=flat.get("repo_path", ""),
        status=flat.get("status", PLANNING),
        created_at=flat.get("created_at", ""),
        updated_at=flat.get("updated_at", ""),
        approval=ApprovalInfo(**{k: v for k, v in flat.get("approval", {}).items() if k in ApprovalInfo.__dataclass_fields__}),
        plan=PlanInfo(**{k: v for k, v in flat.get("plan", {}).items() if k in PlanInfo.__dataclass_fields__}),
        execution=ExecutionInfo(**{k: v for k, v in flat.get("execution", {}).items() if k in ExecutionInfo.__dataclass_fields__}),
        loop=LoopInfo(**{k: v for k, v in flat.get("loop", {}).items() if k in LoopInfo.__dataclass_fields__}),
        blocked=_parse_blocked(flat.get("blocked", {})),
        error=ErrorInfo(**{k: v for k, v in flat.get("error", {}).items() if k in ErrorInfo.__dataclass_fields__}),
    )


def _parse_blocked(d: dict) -> BlockedInfo:
    pause_raw = d.get("pause", {})
    pause = PauseInfo(**{k: v for k, v in pause_raw.items() if k in PauseInfo.__dataclass_fields__})
    return BlockedInfo(
        reason=d.get("reason"),
        needs=d.get("needs"),
        pause=pause,
    )


# ---------------------------------------------------------------------------
# StateManager
# ---------------------------------------------------------------------------

def _sanitize_path_component(name: str) -> str:
    """Prevent path traversal in change IDs."""
    name = name.replace("/", "_").replace("\\", "_").replace("..", "_")
    return name


def new_run_id() -> str:
    return str(uuid.uuid4())


class StateManager:
    """Atomic read/write for ``state.json`` inside an OpenSpec change directory."""

    # ------ path helpers ------

    @staticmethod
    def change_dir(workspace: Path, change_id: str) -> Path:
        safe = _sanitize_path_component(change_id)
        return workspace / "openspec" / "changes" / safe

    @staticmethod
    def state_path(change_dir: Path) -> Path:
        return change_dir / "state.json"

    @staticmethod
    def progress_log_path(change_dir: Path) -> Path:
        return change_dir / "progress.log"

    # ------ read / write ------

    def read(self, change_dir: Path) -> TaskState:
        """Load state from ``state.json``.  Raises if missing."""
        path = self.state_path(change_dir)
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if data.get("schemaVersion") != 1:
            raise ValueError(f"Unsupported state schemaVersion: {data.get('schemaVersion')}")
        return _dict_to_state(data)

    def read_or_init(self, change_dir: Path, change_id: str, repo_path: str = "") -> TaskState:
        """Load existing state or initialise a fresh one."""
        path = self.state_path(change_dir)
        if path.exists():
            return self.read(change_dir)

        now = datetime.utcnow().isoformat() + "Z"
        state = TaskState(
            change_id=change_id,
            repo_path=repo_path,
            status=PLANNING,
            created_at=now,
            updated_at=now,
        )
        change_dir.mkdir(parents=True, exist_ok=True)
        self.write(change_dir, state)
        return state

    def write(self, change_dir: Path, state: TaskState) -> None:
        """Atomic write via tmp + rename."""
        change_dir.mkdir(parents=True, exist_ok=True)
        path = self.state_path(change_dir)
        payload = json.dumps(_state_to_dict(state), indent=2, ensure_ascii=False) + "\n"

        fd, tmp = tempfile.mkstemp(dir=str(change_dir), suffix=".tmp")
        try:
            os.write(fd, payload.encode("utf-8"))
            os.fsync(fd)
            os.close(fd)
            os.rename(tmp, str(path))
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def update(
        self,
        change_dir: Path,
        fn: Callable[[TaskState], TaskState],
    ) -> TaskState:
        """Read → mutate → write (CAS-style, single-process).

        The *fn* callback receives the current state and must return the
        updated state.  ``updated_at`` is refreshed automatically.
        """
        state = self.read(change_dir)
        updated = fn(state)
        updated.updated_at = datetime.utcnow().isoformat() + "Z"
        self.write(change_dir, updated)
        return updated

    def transition(
        self,
        change_dir: Path,
        expected_status: str,
        new_status: str,
    ) -> TaskState:
        """Convenience CAS transition that validates *expected_status*."""
        def _apply(s: TaskState) -> TaskState:
            if s.status != expected_status:
                raise ValueError(
                    f"Status mismatch: expected {expected_status}, got {s.status}"
                )
            s.status = new_status
            return s
        return self.update(change_dir, _apply)

    # ------ digest ------

    def compute_plan_digest(self, change_dir: Path) -> str:
        """SHA-256 digest over the plan artefacts (matches TypeScript version)."""
        h = hashlib.sha256()
        workspace = change_dir.parent.parent.parent  # openspec/changes/<id> -> workspace

        files = [
            workspace / "openspec" / "config.yaml",
            change_dir / "proposal.md",
            change_dir / "design.md",
            change_dir / "tasks.md",
        ]

        for f in files:
            content = ""
            if f.exists():
                content = f.read_text(encoding="utf-8")
            content = content.replace("\r\n", "\n").rstrip() + "\n"
            h.update(str(f).encode("utf-8"))
            h.update(b"\n")
            h.update(content.encode("utf-8"))
            h.update(b"\n\n")

        return h.hexdigest()

    # ------ queries ------

    def list_active(self, workspace: Path) -> list[TaskState]:
        """List all changes with non-terminal status."""
        changes_dir = workspace / "openspec" / "changes"
        if not changes_dir.exists():
            return []

        results: list[TaskState] = []
        for child in changes_dir.iterdir():
            if not child.is_dir():
                continue
            state_file = self.state_path(child)
            if not state_file.exists():
                continue
            try:
                state = self.read(child)
                if state.status not in TERMINAL_STATUSES:
                    results.append(state)
            except Exception as e:
                logger.warning(f"Failed to read state in {child}: {e}")
        return results

    # ------ logging ------

    def log(self, change_dir: Path, run_id: str | None, message: str) -> None:
        """Append a line to progress.log."""
        path = self.progress_log_path(change_dir)
        change_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().isoformat() + "Z"
        line = f"[{ts}] runId={run_id or '-'} {message}\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
