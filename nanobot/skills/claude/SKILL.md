---
name: claude
description: Run Claude Code CLI in non-interactive mode (print/json), with safe defaults and optional tmux fallback.
metadata: {"nanobot":{"emoji":"🧠","os":["darwin","linux"],"requires":{"bins":["claude"]}}}
---

# claude Skill

Use this skill to run the `claude` (Claude Code) CLI in a stable, non-interactive way.

Prefer `-p/--print` over interactive sessions. Use tmux only if you truly need a TTY.

## Quickstart

Text output:

```bash
claude -p "Say hello" \
  --output-format text
```

JSON output (recommended for automation):

```bash
claude -p "Return a JSON object with keys: ok, ts" \
  --output-format json
```

## Recommended conventions

- Always set a timeout in your wrapper script (e.g. `timeout 120s` on Linux).
- Log both stdout and stderr.
- Use `--output-format json` when you plan to parse results.

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

## tmux fallback (only when needed)

If `claude` requires interactive flows in your environment, run it inside a private tmux socket to keep automation isolated.

```bash
SOCKET_DIR="${NANOBOT_TMUX_SOCKET_DIR:-${TMPDIR:-/tmp}/nanobot-tmux-sockets}"
mkdir -p "$SOCKET_DIR"
SOCKET="$SOCKET_DIR/claude.sock"
SESSION=claude

tmux -S "$SOCKET" new -d -s "$SESSION" -n shell

# Start claude interactive
tmux -S "$SOCKET" send-keys -t "$SESSION":0.0 -l -- "claude" C-m

# Monitor
# tmux -S "$SOCKET" attach -t "$SESSION"
# tmux -S "$SOCKET" capture-pane -p -J -t "$SESSION":0.0 -S -200
```
