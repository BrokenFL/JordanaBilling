#!/usr/bin/env bash
#
# Stop the Jordana Billing review server.
# Reads PID from logs/review_server.pid and sends SIGTERM.
# Falls back to killing any process on port 8765.
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

PID_FILE="$PROJECT_DIR/logs/review_server.pid"
PORT=8765

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE")"
  if kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null || true
    # Wait up to 5 seconds for graceful shutdown
    for i in $(seq 1 5); do
      if ! kill -0 "$PID" 2>/dev/null; then
        break
      fi
      sleep 1
    done
    # Force kill if still running
    if kill -0 "$PID" 2>/dev/null; then
      kill -9 "$PID" 2>/dev/null || true
    fi
    echo "Stopped server (PID $PID)."
  else
    echo "PID $PID is not running — cleaning up stale PID file."
  fi
  rm -f "$PID_FILE"
else
  # Fallback: kill anything on the port
  PID_ON_PORT="$(lsof -ti ":${PORT}" 2>/dev/null || true)"
  if [[ -n "$PID_ON_PORT" ]]; then
    kill $PID_ON_PORT 2>/dev/null || true
    echo "Stopped process on port $PORT (PID $PID_ON_PORT)."
  else
    echo "No server running on port $PORT."
  fi
fi
