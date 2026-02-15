---
name: claude
description: Run Claude Code CLI in non-interactive mode (print/json), with safe defaults and optional tmux fallback. Use when the user asks to run Claude for execution, analysis, or automation tasks.
metadata: {"nanobot":{"emoji":"🧠","os":["darwin","linux"],"requires":{"bins":["claude","tmux"]},"aliases":["claude-code","anthropic-claude"],"triggers":["claude","claude code","anthropic claude","use claude","run claude cli","claude runner","用claude","调用claude","claude执行"],"allowed_tools":["claude","exec","read_file","list_dir"]}}
---

# claude Skill

This skill is meant to be used by nanobot (not manually by the user).

Default to non-interactive `claude -p` with parseable output.
If a TTY is needed (login flows, long interactive sessions), nanobot automatically creates and manages a *private tmux socket/session* for Claude, uses tmux for input only, and reads assistant output from Claude Code session logs (JSONL) instead of `tmux capture-pane`.

## How nanobot uses this skill

nanobot should call the runner (preferred):

```bash
python3 -m nanobot.skills.claude.runner \
  --mode auto \
  --cwd /path/to/repo \
  --prompt "Return a JSON object with keys: ok, ts"
```

Modes:
- `--mode auto`: try `claude -p` first; if it fails/returns empty, fall back to private tmux + JSONL event stream.
- `--mode print`: force `claude -p`.
- `--mode tty`: force private tmux + JSONL event stream.

Output:
- default `--output-format json` for `--mode print/auto` when `claude -p` succeeds.
- `--mode tty` streams assistant events; use `--json-stream` for machine-readable JSONL.

## Recommended conventions

- Always set a timeout in your wrapper script (e.g. `timeout 120s` on Linux).
- Log both stdout and stderr.
- Use `--output-format json` when you plan to parse results.
- Prefer short prompts and explicit output contracts.

## Minimal wrapper example

```bash
#!/usr/bin/env bash
set -euo pipefail

PROMPT=${1:-}
if [[ -z "${PROMPT}" ]]; then
  echo "usage: $0 <prompt>" >&2
  exit 2
fi

claude -p "$PROMPT" --output-format json
```

## TTY mode (tmux) + observable output (recommended)

Problem: Claude Code's TUI output is often not reliably observable via `tmux capture-pane` (redraw/control sequences can yield blank or misleading output).

Solution: treat tmux as an input transport only; read assistant output from Claude Code's on-disk session logs:

- `~/.claude/projects/<project-key>/*.jsonl`
- optional index: `~/.claude/projects/<project-key>/sessions-index.json`

This repo provides a small bridge script that does exactly that.

### Use the bridge

1) Ensure Claude Code is running inside a tmux pane.

2) Discover the pane id:

```bash
tmux list-panes -a -F '#{pane_id} #{pane_current_command} #{pane_title}'
```

3) Ask a question and stream assistant output from JSONL events:

```bash
python3 scripts/claude_tty_bridge.py \
  --pane %12 \
  --cwd /path/to/repo \
  --ask "Say only: OK" \
  --timeout 300 \
  --idle-timeout 300 \
  --abort-on-idle
```

If session selection is ambiguous, force it:

```bash
python3 scripts/claude_tty_bridge.py \
  --pane %12 \
  --cwd /path/to/repo \
  --prefer-session ~/.claude/projects/<key>/<session>.jsonl \
  --ask "Say only: OK"
```

For machine-readable streaming output:

```bash
python3 scripts/claude_tty_bridge.py \
  --pane %12 \
  --cwd /path/to/repo \
  --ask "..." \
  --json
```

## tmux fallback (legacy)

If you must run interactive `claude` in a private tmux socket, you can still do so; but avoid using `capture-pane` for observability.

```bash
SOCKET_DIR="${NANOBOT_TMUX_SOCKET_DIR:-${TMPDIR:-/tmp}/nanobot-tmux-sockets}"
mkdir -p "$SOCKET_DIR"
SOCKET="$SOCKET_DIR/claude.sock"
SESSION=claude

tmux -S "$SOCKET" new -d -s "$SESSION" -n shell

# Start claude interactive
tmux -S "$SOCKET" send-keys -t "$SESSION":0.0 -l -- "claude" C-m

# Attach (human debugging)
# tmux -S "$SOCKET" attach -t "$SESSION"
```
