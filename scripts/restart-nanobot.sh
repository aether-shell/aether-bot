#!/usr/bin/env bash
set -euo pipefail

MODE="${NANOBOT_MODE:-local}"
DATA_DIR="${NANOBOT_DATA_DIR:-$HOME/.aether-shell}"
LOG_DIR="${NANOBOT_LOG_DIR:-$DATA_DIR/logs}"
RUN_DIR="${NANOBOT_RUN_DIR:-$DATA_DIR/run}"
LOG_FILE="${NANOBOT_LOG_FILE:-$LOG_DIR/gateway.log}"
PID_FILE="${NANOBOT_PID_FILE:-$RUN_DIR/gateway.pid}"

if [[ "$MODE" == "docker" ]]; then
  IMAGE="${NANOBOT_IMAGE:-nanobot}"
  CONTAINER="${NANOBOT_CONTAINER:-nanobot-gateway}"
  PORT="${NANOBOT_PORT:-18790}"

  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  docker run -d --name "$CONTAINER" -v "$DATA_DIR":/root/.aether-shell -p "${PORT}:${PORT}" "$IMAGE" gateway
  echo "nanobot gateway (docker) started: $CONTAINER"
  exit 0
fi

mkdir -p "$LOG_DIR" "$RUN_DIR"

if [[ -f "$PID_FILE" ]]; then
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    for _ in {1..20}; do
      if kill -0 "$pid" 2>/dev/null; then
        sleep 0.5
      else
        break
      fi
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  fi
  rm -f "$PID_FILE"
fi

if [[ "${NANOBOT_FORCE_STOP:-0}" == "1" ]]; then
  pgrep -f "nanobot gateway" | xargs -r kill || true
fi

CMD="${NANOBOT_CMD:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PY="$REPO_ROOT/.venv/bin/python"

if [[ -z "$CMD" ]]; then
  if [[ -x "$VENV_PY" ]]; then
    CMD="$VENV_PY -m nanobot gateway"
  elif command -v nanobot >/dev/null 2>&1; then
    CMD="nanobot gateway"
  elif command -v python3 >/dev/null 2>&1; then
    CMD="python3 -m nanobot gateway"
  else
    CMD="python -m nanobot gateway"
  fi
fi

echo "nanobot restart: using command: $CMD"
echo "nanobot restart: using command: $CMD" >> "$LOG_FILE"

RUN_CMD="cd \"$REPO_ROOT\" && $CMD"
nohup bash -c "$RUN_CMD" >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
echo "nanobot gateway started (pid $(cat "$PID_FILE"))."
