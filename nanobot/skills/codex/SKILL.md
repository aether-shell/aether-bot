---
name: codex
description: Run Codex CLI in non-interactive mode (exec/review) for stable automation; optional tmux usage for interactive mode. Use when the user asks to run Codex for coding or review workflows.
metadata: {"nanobot":{"emoji":"🤖","os":["darwin","linux"],"requires":{"bins":["codex"]},"aliases":["codex-cli","openai-codex"],"triggers":["codex","codex cli","codex exec","codex review","use codex","run codex","用codex","调用codex","codex评审"],"allowed_tools":["exec","read_file","list_dir"]}}
---

# codex Skill

Use this skill to run `codex` (codex-cli) in a stable, scriptable way.

Prefer non-interactive subcommands like `codex exec` and `codex review`. Use tmux only if you must drive an interactive TTY.

## Quickstart

Run a one-shot prompt:

```bash
codex exec "Say hello in one sentence."
```

Review a diff or repo (example):

```bash
codex review --help
```

## Recommended conventions

- Wrap `codex` calls in scripts with explicit working directory.
- Capture logs (stdout/stderr) to files.
- Keep prompts deterministic and specify desired output format.

## Minimal wrapper example

```bash
#!/usr/bin/env bash
set -euo pipefail

PROMPT=${1:-}
if [[ -z "${PROMPT}" ]]; then
  echo "usage: $0 <prompt>" >&2
  exit 2
fi

codex exec "$PROMPT"
```

## tmux usage (only when needed)

```bash
SOCKET_DIR="${NANOBOT_TMUX_SOCKET_DIR:-${TMPDIR:-/tmp}/nanobot-tmux-sockets}"
mkdir -p "$SOCKET_DIR"
SOCKET="$SOCKET_DIR/codex.sock"
SESSION=codex

tmux -S "$SOCKET" new -d -s "$SESSION" -n shell

# Start codex interactive (if you use it that way)
tmux -S "$SOCKET" send-keys -t "$SESSION":0.0 -l -- "codex" C-m

# Monitor
# tmux -S "$SOCKET" attach -t "$SESSION"
# tmux -S "$SOCKET" capture-pane -p -J -t "$SESSION":0.0 -S -200
```
