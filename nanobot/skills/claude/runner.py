#!/usr/bin/env python3
"""Claude skill runner.

Goal: make Claude usable by nanobot without any user manual tmux operations.

Strategy:
- Default: run non-interactive `claude -p` for fastest, most reliable automation.
- Optional TTY mode: run `claude` inside a private tmux socket/session managed by nanobot,
  then read assistant output from Claude Code JSONL session logs using nanobot's bridge.

This runner is designed to be invoked by the nanobot skill system.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from nanobot.claude_tty_bridge import CLAUDE_PROJECTS_ROOT, ClaudeCodeLogReader


def _now_ts() -> float:
    return time.time()


def _safe_filename(s: str) -> str:
    out = []
    for ch in (s or ""):
        if ch.isalnum() or ch in ("-", "_", "."):
            out.append(ch)
        else:
            out.append("-")
    v = "".join(out).strip("-")
    return v or "audit"


def _open_audit(audit_dir: str | None, audit_tag: str | None):
    if not audit_dir:
        return None, None
    p = Path(audit_dir).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    tag = _safe_filename(audit_tag or "")
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    pid = os.getpid()
    path = p / f"claude_{stamp}_{pid}_{tag}.jsonl"
    try:
        f = path.open("a", encoding="utf-8")
    except Exception:
        return None, None
    return f, str(path)


def _audit_write(f, obj: dict) -> None:
    if not f:
        return
    try:
        f.write(json.dumps(obj, ensure_ascii=True) + "\n")
        f.flush()
    except Exception:
        return


@dataclass
class RunResult:
    code: int
    stdout: str
    stderr: str


def _run_subprocess(cmd: list[str], timeout: int | None = None, cwd: str | None = None) -> RunResult:
    p = subprocess.run(
        cmd,
        cwd=cwd,
        timeout=timeout,
        text=True,
        capture_output=True,
        check=False,
    )
    return RunResult(code=p.returncode, stdout=p.stdout, stderr=p.stderr)


def _default_socket_dir() -> Path:
    base = os.environ.get("NANOBOT_TMUX_SOCKET_DIR")
    if base:
        return Path(base)
    tmp = os.environ.get("TMPDIR") or "/tmp"
    return Path(tmp) / "nanobot-tmux-sockets"


def _tmux_socket_path() -> Path:
    sock_dir = _default_socket_dir()
    sock_dir.mkdir(parents=True, exist_ok=True)
    return sock_dir / "claude.sock"


def _tmux(cmd: list[str], timeout: int | None = None) -> RunResult:
    return _run_subprocess(cmd, timeout=timeout)


def _tmux_has_session(socket: Path, session: str) -> bool:
    r = _tmux(["tmux", "-S", str(socket), "has-session", "-t", session])
    return r.code == 0


def _tmux_new_session(socket: Path, session: str) -> None:
    _tmux(["tmux", "-S", str(socket), "new", "-d", "-s", session, "-n", "shell"], timeout=10)


def _tmux_pane_target(session: str) -> str:
    return f"{session}:0.0"


def _tmux_pane_current_command(socket: Path, target: str) -> str:
    r = _tmux(["tmux", "-S", str(socket), "display-message", "-p", "-t", target, "#{pane_current_command}"])
    return (r.stdout or "").strip()


def _tmux_send_keys(socket: Path, target: str, *keys: str) -> None:
    _tmux(["tmux", "-S", str(socket), "send-keys", "-t", target, *keys], timeout=10)


def _tmux_load_buffer(socket: Path, data: str) -> None:
    p = subprocess.Popen(
        ["tmux", "-S", str(socket), "load-buffer", "-"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    out, err = p.communicate(data)
    if p.returncode != 0:
        raise RuntimeError(f"tmux load-buffer failed: {err or out}")


def _tmux_paste_buffer(socket: Path, target: str) -> None:
    _tmux(["tmux", "-S", str(socket), "paste-buffer", "-p", "-t", target], timeout=10)


def _ensure_tmux_session(socket: Path, session: str) -> str:
    if not _tmux_has_session(socket, session):
        _tmux_new_session(socket, session)
    return _tmux_pane_target(session)


def _start_claude_if_needed(socket: Path, target: str) -> None:
    cmd = _tmux_pane_current_command(socket, target)
    if cmd == "claude":
        return
    # Start interactive claude
    _tmux_send_keys(socket, target, "claude")
    _tmux_send_keys(socket, target, "C-m")
    time.sleep(0.8)


def run_print(prompt: str, output_format: str, cwd: str | None, timeout: int) -> RunResult:
    cmd = ["claude", "-p", prompt, "--output-format", output_format]
    return _run_subprocess(cmd, timeout=timeout, cwd=cwd)


def _print_diag(reader: ClaudeCodeLogReader, state, reason: str) -> None:
    diag = {
        "reason": reason,
        "session": str(state.session_path) if state.session_path else None,
        "offset": state.offset,
        "last_event_ts": getattr(state, "last_event_ts", None),
        "projects_root": str(CLAUDE_PROJECTS_ROOT),
    }
    sys.stderr.write(json.dumps(diag, ensure_ascii=True) + "\n")
    sys.stderr.flush()


def run_tty(
    prompt: str,
    cwd: str,
    timeout: int,
    idle_timeout: int,
    abort_on_idle: bool,
    json_stream: bool,
    retries: int,
    audit_f=None,
    request_id: str | None = None,
) -> int:
    socket = _tmux_socket_path()
    session = "claude"

    attempt = 0
    while True:
        attempt += 1
        target = _ensure_tmux_session(socket, session)
        _start_claude_if_needed(socket, target)

        _audit_write(
            audit_f,
            {
                "type": "tty_attempt_start",
                "request_id": request_id,
                "ts": _now_ts(),
                "attempt": attempt,
                "socket": str(socket),
                "session": session,
                "target": target,
            },
        )

        reader = ClaudeCodeLogReader(work_dir=Path(cwd))
        state = reader.capture_event_state()
        _audit_write(
            audit_f,
            {
                "type": "tty_reader_state",
                "request_id": request_id,
                "ts": _now_ts(),
                "session_path": str(state.session_path) if state.session_path else None,
                "offset": state.offset,
            },
        )

        # Send prompt via paste-buffer.
        _tmux_load_buffer(socket, prompt)
        _tmux_paste_buffer(socket, target)
        _tmux_send_keys(socket, target, "C-m")

        start = time.time()
        last_assistant_ts: float | None = None
        saw_any = False

        while True:
            now = time.time()
            if (now - start) > timeout:
                if abort_on_idle:
                    _tmux_send_keys(socket, target, "C-c")
                _print_diag(reader, state, reason="timeout")
                _audit_write(
                    audit_f,
                    {
                        "type": "tty_exit",
                        "request_id": request_id,
                        "ts": _now_ts(),
                        "attempt": attempt,
                        "reason": "timeout",
                    },
                )
                return 2

            events, state = reader.wait_for_events(state, timeout_s=0.5)
            for role, text in events:
                if role != "assistant":
                    continue
                saw_any = True
                last_assistant_ts = time.time()
                if json_stream:
                    sys.stdout.write(
                        json.dumps(
                            {
                                "role": role,
                                "text": text,
                                "session": str(state.session_path) if state.session_path else None,
                                "offset": state.offset,
                                "ts": None,
                            },
                            ensure_ascii=True,
                        )
                        + "\n"
                    )
                else:
                    sys.stdout.write(text)
                    if not text.endswith("\n"):
                        sys.stdout.write("\n")
                sys.stdout.flush()
                _audit_write(
                    audit_f,
                    {
                        "type": "tty_assistant_chunk",
                        "request_id": request_id,
                        "ts": _now_ts(),
                        "attempt": attempt,
                        "chars": len(text or ""),
                        "offset": state.offset,
                    },
                )

            if last_assistant_ts is None:
                if (time.time() - start) > idle_timeout:
                    if abort_on_idle:
                        _tmux_send_keys(socket, target, "C-c")
                    _print_diag(reader, state, reason="idle_no_output")
                    _audit_write(
                        audit_f,
                        {
                            "type": "tty_exit",
                            "request_id": request_id,
                            "ts": _now_ts(),
                            "attempt": attempt,
                            "reason": "idle_no_output",
                            "retries_left": max(0, retries - attempt + 1),
                        },
                    )

                    if attempt <= retries:
                        # Restart claude session/pane and try again.
                        _tmux(["tmux", "-S", str(socket), "kill-session", "-t", session], timeout=10)
                        time.sleep(0.5)
                        break

                    return 3
            else:
                if (time.time() - last_assistant_ts) > idle_timeout:
                    return 0 if saw_any else 3


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["auto", "print", "tty"], default="auto")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--idle-timeout", type=int, default=300)
    ap.add_argument("--abort-on-idle", action="store_true")
    ap.add_argument("--output-format", choices=["text", "json"], default="json")
    ap.add_argument("--json-stream", action="store_true")
    ap.add_argument("--retries", type=int, default=1)

    ap.add_argument(
        "--audit-dir",
        default=None,
        help="Directory to write audit logs (jsonl). If omitted, auditing is disabled.",
    )
    ap.add_argument(
        "--audit-tag",
        default=None,
        help="Optional string tag to include in the audit filename.",
    )

    args = ap.parse_args()

    audit_f, audit_path = _open_audit(args.audit_dir, args.audit_tag)
    request_id = f"rq_{int(_now_ts() * 1000)}_{os.getpid()}"
    _audit_write(
        audit_f,
        {
            "type": "start",
            "request_id": request_id,
            "ts": _now_ts(),
            "mode": args.mode,
            "cwd": args.cwd,
            "timeout": args.timeout,
            "idle_timeout": args.idle_timeout,
            "abort_on_idle": bool(args.abort_on_idle),
            "output_format": args.output_format,
            "json_stream": bool(args.json_stream),
            "retries": args.retries,
            "audit_path": audit_path,
        },
    )

    def _finish(code: int, stdout: str = "", stderr: str = "") -> int:
        _audit_write(
            audit_f,
            {
                "type": "finish",
                "request_id": request_id,
                "ts": _now_ts(),
                "code": code,
                "stdout_chars": len(stdout or ""),
                "stderr_chars": len(stderr or ""),
            },
        )
        if audit_f:
            try:
                audit_f.close()
            except Exception:
                pass
        return code

    if args.mode in ("auto", "print"):
        r = run_print(args.prompt, args.output_format, args.cwd, args.timeout)
        _audit_write(
            audit_f,
            {
                "type": "print_result",
                "request_id": request_id,
                "ts": _now_ts(),
                "code": r.code,
                "stdout_chars": len(r.stdout or ""),
                "stderr_chars": len(r.stderr or ""),
            },
        )
        if r.code == 0 and (r.stdout or "").strip() != "":
            sys.stdout.write(r.stdout)
            if r.stderr:
                sys.stderr.write(r.stderr)
            return _finish(0, stdout=r.stdout, stderr=r.stderr)
        if args.mode == "print":
            sys.stdout.write(r.stdout)
            sys.stderr.write(r.stderr)
            return _finish(r.code or 1, stdout=r.stdout, stderr=r.stderr)

    if not args.cwd:
        sys.stderr.write("--cwd required for --mode tty/auto fallback\n")
        return _finish(2, stderr="--cwd required")

    code = run_tty(
        prompt=args.prompt,
        cwd=args.cwd,
        timeout=args.timeout,
        idle_timeout=args.idle_timeout,
        abort_on_idle=args.abort_on_idle,
        json_stream=args.json_stream,
        retries=args.retries,
        audit_f=audit_f,
        request_id=request_id,
    )
    return _finish(code)


if __name__ == "__main__":
    raise SystemExit(main())
