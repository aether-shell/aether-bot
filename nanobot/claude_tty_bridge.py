"""Claude Code TTY bridge.

Goal: drive a running Claude Code TUI inside tmux, but *read replies from disk logs*
(~/.claude/projects/<project-key>/<session-id>.jsonl) instead of scraping tmux output.

This follows the approach used by ~/WorkSpace/ai-tools/claude_code_bridge.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


CLAUDE_PROJECTS_ROOT = Path(
    os.environ.get("CLAUDE_PROJECTS_ROOT")
    or os.environ.get("CLAUDE_PROJECT_ROOT")
    or (Path.home() / ".claude" / "projects")
).expanduser()


def _normalize_project_path(value: str | Path) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        path = Path(raw).expanduser()
        try:
            path = path.resolve()
        except Exception:
            path = path.absolute()
        raw = str(path)
    except Exception:
        raw = str(value)
    raw = raw.replace("\\", "/").rstrip("/")
    if os.name == "nt":
        raw = raw.lower()
    return raw


def _candidate_project_paths(work_dir: Path) -> list[str]:
    candidates: list[Path] = []
    env_pwd = os.environ.get("PWD")
    if env_pwd:
        try:
            candidates.append(Path(env_pwd))
        except Exception:
            pass
    candidates.append(work_dir)
    try:
        candidates.append(work_dir.resolve())
    except Exception:
        pass

    out: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = _normalize_project_path(candidate)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _project_key_for_path(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def _extract_content_text(content: Any) -> Optional[str]:
    if content is None:
        return None
    if isinstance(content, str):
        text = content.strip()
        return text or None
    if not isinstance(content, list):
        return None

    texts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()
        if item_type in ("thinking", "thinking_delta"):
            continue
        text = item.get("text")
        if not text and item_type == "text":
            text = item.get("content")
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())

    if not texts:
        return None
    return "\n".join(texts).strip()


def _extract_message(entry: dict, role: str) -> Optional[str]:
    if not isinstance(entry, dict):
        return None

    entry_type = (entry.get("type") or "").strip().lower()

    # Newer shapes
    if entry_type == "response_item":
        payload = entry.get("payload", {})
        if not isinstance(payload, dict) or payload.get("type") != "message":
            return None
        if (payload.get("role") or "").lower() != role:
            return None
        return _extract_content_text(payload.get("content"))

    if entry_type == "event_msg":
        payload = entry.get("payload", {})
        if not isinstance(payload, dict):
            return None
        payload_type = (payload.get("type") or "").lower()
        if payload_type in ("agent_message", "assistant_message", "assistant"):
            if (payload.get("role") or "").lower() != role:
                return None
            msg = payload.get("message") or payload.get("content") or payload.get("text")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()
        return None

    # Default jsonl shape
    message = entry.get("message")
    if isinstance(message, dict):
        msg_role = (message.get("role") or entry_type).strip().lower()
        if msg_role != role:
            return None
        return _extract_content_text(message.get("content"))

    if entry_type != role:
        return None
    return _extract_content_text(entry.get("content"))


@dataclass
class ClaudeLogState:
    session_path: Optional[Path]
    offset: int
    carry: bytes


@dataclass
class ClaudeEventState:
    """State for incremental event reading."""

    session_path: Optional[Path]
    offset: int
    carry: bytes
    last_event_ts: float


class ClaudeCodeLogReader:
    """Incrementally read messages/events from Claude Code's jsonl session logs."""

    def __init__(self, *, work_dir: Path, root: Path = CLAUDE_PROJECTS_ROOT):
        self.work_dir = Path(work_dir)
        self.root = Path(root).expanduser()
        self._preferred_session: Optional[Path] = None
        poll_raw = os.environ.get("CLAUDE_TTY_BRIDGE_POLL_INTERVAL", "0.05")
        try:
            poll = float(poll_raw)
        except Exception:
            poll = 0.05
        self._poll_interval = min(0.5, max(0.02, poll))

    def _project_dir(self) -> Path:
        key = _project_key_for_path(self.work_dir)
        return self.root / key

    def set_preferred_session(self, session_path: Optional[Path]) -> None:
        if not session_path:
            return
        p = Path(session_path).expanduser()
        if p.exists():
            self._preferred_session = p

    def _project_dir(self) -> Path:
        # Follow CCB's approach: accept PWD/work_dir/resolve() candidates and
        # pick the first directory that exists under ~/.claude/projects.
        for candidate in (self.root / _project_key_for_path(Path(p)) for p in _candidate_project_paths(self.work_dir)):
            if candidate.exists():
                return candidate
        return self.root / _project_key_for_path(self.work_dir)

    def _parse_sessions_index(self) -> Optional[Path]:
        if os.environ.get("CLAUDE_TTY_BRIDGE_USE_SESSIONS_INDEX", "1").strip().lower() in (
            "0",
            "false",
            "no",
        ):
            return None
        project_dir = self._project_dir()
        index_path = project_dir / "sessions-index.json"
        if not index_path.exists():
            return None
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return None
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            return None

        candidates = set(_candidate_project_paths(self.work_dir))
        best_path: Optional[Path] = None
        best_mtime = -1
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("isSidechain") is True:
                continue
            project_path = entry.get("projectPath")
            if isinstance(project_path, str) and project_path.strip():
                normalized = _normalize_project_path(project_path)
                if candidates and normalized and normalized not in candidates:
                    continue
            elif candidates:
                continue

            full_path = entry.get("fullPath")
            if not isinstance(full_path, str) or not full_path.strip():
                continue
            try:
                session_path = Path(full_path).expanduser()
            except Exception:
                continue
            if not session_path.is_absolute():
                session_path = (project_dir / session_path).expanduser()
            if not session_path.exists():
                continue

            mtime_raw = entry.get("fileMtime")
            mtime: Optional[int] = None
            if isinstance(mtime_raw, (int, float)):
                mtime = int(mtime_raw)
            elif isinstance(mtime_raw, str) and mtime_raw.strip().isdigit():
                try:
                    mtime = int(mtime_raw.strip())
                except Exception:
                    mtime = None
            if mtime is None:
                try:
                    mtime = int(session_path.stat().st_mtime * 1000)
                except OSError:
                    mtime = None
            if mtime is None:
                continue

            if mtime > best_mtime:
                best_mtime = mtime
                best_path = session_path

        return best_path

    def _scan_latest_session(self) -> Optional[Path]:
        project_dir = self._project_dir()
        if not project_dir.exists():
            return None
        try:
            sessions = sorted(
                (p for p in project_dir.glob("*.jsonl") if p.is_file() and not p.name.startswith(".")),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return None
        return sessions[0] if sessions else None

    def current_session_path(self) -> Optional[Path]:
        if self._preferred_session and self._preferred_session.exists():
            return self._preferred_session

        indexed = self._parse_sessions_index()
        if indexed:
            # If sessions-index.json exists but doesn't point to a valid session,
            # treat it as authoritative to avoid accidentally reading an unrelated
            # jsonl file from a different tmux pane.
            return indexed if indexed.exists() else None

        return self._scan_latest_session()

    def capture_state(self) -> ClaudeLogState:
        session = self.current_session_path()
        offset = 0
        if session and session.exists():
            try:
                offset = session.stat().st_size
            except OSError:
                offset = 0
        return ClaudeLogState(session_path=session, offset=offset, carry=b"")

    def _read_new(self, session: Path, state: ClaudeLogState) -> tuple[Optional[str], ClaudeLogState]:
        offset = int(state.offset or 0)
        carry = state.carry or b""

        try:
            size = session.stat().st_size
        except OSError:
            return None, state

        if size < offset:
            offset = 0
            carry = b""

        try:
            with session.open("rb") as handle:
                handle.seek(offset)
                data = handle.read()
        except OSError:
            return None, state

        new_offset = offset + len(data)
        buf = carry + data
        lines = buf.split(b"\n")
        if buf and not buf.endswith(b"\n"):
            carry = lines.pop()
        else:
            carry = b""

        latest: Optional[str] = None
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            try:
                entry = json.loads(line.decode("utf-8", errors="replace"))
            except Exception:
                continue
            msg = _extract_message(entry, "assistant")
            if msg:
                latest = msg

        return latest, ClaudeLogState(session_path=session, offset=new_offset, carry=carry)

    def wait_for_message(self, state: ClaudeLogState, timeout_s: float) -> tuple[Optional[str], ClaudeLogState]:
        deadline = time.time() + max(0.0, float(timeout_s))
        current = state

        while True:
            session = self.current_session_path()
            if not session or not session.exists():
                if time.time() >= deadline:
                    return None, current
                time.sleep(self._poll_interval)
                continue

            if current.session_path != session:
                current = ClaudeLogState(session_path=session, offset=0, carry=b"")

            msg, current = self._read_new(session, current)
            if msg:
                return msg, current

            if time.time() >= deadline:
                return None, current
            time.sleep(self._poll_interval)

    def capture_event_state(self) -> ClaudeEventState:
        session = self.current_session_path()
        offset = 0
        if session and session.exists():
            try:
                offset = session.stat().st_size
            except OSError:
                offset = 0
        return ClaudeEventState(session_path=session, offset=offset, carry=b"", last_event_ts=time.time())

    def _read_new_events(
        self, session: Path, state: ClaudeEventState
    ) -> tuple[list[tuple[str, str]], ClaudeEventState]:
        offset = int(state.offset or 0)
        carry = state.carry or b""

        try:
            size = session.stat().st_size
        except OSError:
            return [], state

        if size < offset:
            offset = 0
            carry = b""

        try:
            with session.open("rb") as handle:
                handle.seek(offset)
                data = handle.read()
        except OSError:
            return [], state

        new_offset = offset + len(data)
        buf = carry + data
        lines = buf.split(b"\n")
        if buf and not buf.endswith(b"\n"):
            carry = lines.pop()
        else:
            carry = b""

        events: list[tuple[str, str]] = []
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            try:
                entry = json.loads(line.decode("utf-8", errors="replace"))
            except Exception:
                continue

            user_msg = _extract_message(entry, "user")
            if user_msg:
                events.append(("user", user_msg))
                continue
            assistant_msg = _extract_message(entry, "assistant")
            if assistant_msg:
                events.append(("assistant", assistant_msg))

        next_state = ClaudeEventState(
            session_path=session,
            offset=new_offset,
            carry=carry,
            last_event_ts=state.last_event_ts,
        )
        if events:
            next_state.last_event_ts = time.time()
        return events, next_state

    def wait_for_events(
        self, state: ClaudeEventState, timeout_s: float
    ) -> tuple[list[tuple[str, str]], ClaudeEventState]:
        deadline = time.time() + max(0.0, float(timeout_s))
        current = state

        while True:
            session = self.current_session_path()
            if not session or not session.exists():
                if time.time() >= deadline:
                    return [], current
                time.sleep(self._poll_interval)
                continue

            if current.session_path != session:
                current = ClaudeEventState(
                    session_path=session,
                    offset=0,
                    carry=b"",
                    last_event_ts=time.time(),
                )

            events, current = self._read_new_events(session, current)
            if events:
                return events, current

            if time.time() >= deadline:
                return [], current
            time.sleep(self._poll_interval)

    def iter_events(
        self, state: ClaudeEventState, *, timeout_s: float, poll_step_s: float = 1.0
    ) -> Iterable[tuple[str, str]]:
        """Yield events as they appear until timeout.

        This is convenient for "streaming" output without having to implement a separate loop.
        """

        start = time.time()
        current = state
        while True:
            elapsed = time.time() - start
            remaining = float(timeout_s) - elapsed
            if remaining <= 0:
                return
            step = min(max(0.05, float(poll_step_s)), remaining)
            events, current = self.wait_for_events(current, timeout_s=step)
            for evt in events:
                yield evt
