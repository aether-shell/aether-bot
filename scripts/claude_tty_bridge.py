#!/usr/bin/env python3
"""Minimal Claude Code tmux bridge.

- Sends text into a tmux pane running Claude Code.
- Reads replies from Claude Code jsonl logs under ~/.claude/projects.

Usage:
  python3 scripts/claude_tty_bridge.py --pane %12 --cwd /path/to/repo --ask "Hello"

Notes:
- This script intentionally avoids scraping tmux output.
- It reads assistant replies by tailing Claude Code's jsonl session file.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Ensure repo root is on sys.path when running as a script.
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from nanobot.claude_tty_bridge import ClaudeCodeLogReader


def _tmux_send(pane: str, text: str, *, enter: bool = True) -> None:
    sanitized = (text or "").replace("\r", "").strip()
    if not sanitized:
        return

    # Prefer paste-buffer to avoid dropping characters.
    buf = f"nanobot-ccb-{int(time.time() * 1000)}"
    subprocess.run(["tmux", "load-buffer", "-b", buf, "-"], check=True, input=sanitized.encode("utf-8"))
    try:
        subprocess.run(["tmux", "paste-buffer", "-p", "-t", pane, "-b", buf], check=True)
        if enter:
            time.sleep(0.25)
            subprocess.run(["tmux", "send-keys", "-t", pane, "Enter"], check=True)
    finally:
        subprocess.run(["tmux", "delete-buffer", "-b", buf], check=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pane", required=True, help="tmux pane id (e.g. %%12)")
    ap.add_argument("--cwd", required=True, help="project directory (used to locate ~/.claude/projects key)")
    ap.add_argument("--ask", required=True, help="prompt to send")
    ap.add_argument("--timeout", type=float, default=300.0, help="overall seconds to wait")
    ap.add_argument(
        "--idle-timeout",
        type=float,
        default=300.0,
        help="seconds of no assistant events before abort (Ctrl+C)",
    )
    ap.add_argument("--prefer-session", default="", help="explicit ~/.claude/.../*.jsonl path (optional)")
    ap.add_argument("--print-user", action="store_true", help="also print user events")
    ap.add_argument("--abort-on-idle", action="store_true", help="send Ctrl+C when idle timeout hits")
    ap.add_argument(
        "--json",
        action="store_true",
        help="print events as JSON lines {role,text,ts,session,offset} instead of plain text",
    )
    args = ap.parse_args()

    reader = ClaudeCodeLogReader(work_dir=Path(args.cwd))
    if args.prefer_session.strip():
        reader.set_preferred_session(Path(args.prefer_session.strip()).expanduser())
    event_state = reader.capture_event_state()

    _tmux_send(args.pane, args.ask)

    # Stream events until overall timeout.
    start = time.time()
    last_assistant = None
    saw_assistant = False
    last_assistant_event_ts = start

    overall_deadline = start + max(0.0, float(args.timeout))
    idle_timeout_s = max(0.0, float(args.idle_timeout))

    while True:
        now = time.time()
        if now >= overall_deadline:
            break

        # Idle timeout: based on last assistant chunk, not "any event".
        if idle_timeout_s and saw_assistant and (now - last_assistant_event_ts) >= idle_timeout_s:
            return 0

        # Idle timeout (error): triggers if we never see any assistant output.
        if idle_timeout_s and not saw_assistant and (now - start) >= idle_timeout_s:
            session_path = reader.current_session_path()
            print(
                "[idle-timeout] no assistant events for %.1fs; session=%s offset=%s last_event_ts=%.3f"
                % (
                    idle_timeout_s,
                    str(session_path) if session_path else "<none>",
                    str(event_state.offset),
                    float(event_state.last_event_ts),
                ),
                file=sys.stderr,
            )
            if args.abort_on_idle:
                subprocess.run(["tmux", "send-keys", "-t", args.pane, "C-c"], check=False)
            return 3

        remaining = overall_deadline - now
        events, event_state = reader.wait_for_events(event_state, timeout_s=min(1.0, remaining))
        for role, text in events:
            if role == "user" and not args.print_user:
                continue
            if role == "assistant":
                saw_assistant = True
                last_assistant_event_ts = time.time()

            if not text:
                continue

            if args.json:
                payload = {
                    "role": role,
                    "text": text,
                    "ts": time.time(),
                    "session": str(event_state.session_path) if event_state.session_path else None,
                    "offset": int(event_state.offset),
                }
                print(json.dumps(payload, ensure_ascii=True))
                continue

            if role == "user":
                print(f"[user] {text}")
                continue

            if role != "assistant":
                continue

            # De-dupe identical assistant payloads.
            if text != last_assistant:
                print(text)
                last_assistant = text

    if not saw_assistant:
        session_path = reader.current_session_path()
        print(
            "[timeout] no assistant events observed; session=%s offset=%s"
            % (str(session_path) if session_path else "<none>", str(event_state.offset)),
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
