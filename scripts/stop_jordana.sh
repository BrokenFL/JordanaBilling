#!/usr/bin/env bash
#
# Stop only a verified Jordana Billing review server owned by this checkout.
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_FILE="$PROJECT_DIR/logs/stop.log"
mkdir -p "$PROJECT_DIR/logs"

# shellcheck source=launcher_common.sh
. "$PROJECT_DIR/scripts/launcher_common.sh"

ensure_runtime_paths
cd "$PROJECT_DIR"
LOG_FILE="$PROJECT_DIR/logs/stop.log"

if [[ ! -f "$PID_FILE" ]]; then
  echo "No Jordana Billing PID file found. No process was stopped."
  exit 0
fi

PID="$(tr -dc '0-9' < "$PID_FILE")"
if pid_is_app_owned "$PID"; then
  kill "$PID" >/dev/null 2>&1 || true
  for _ in $(seq 1 5); do
    if ! pid_is_running "$PID"; then
      break
    fi
    sleep 1
  done
  if pid_is_running "$PID"; then
    echo "Jordana Billing did not stop gracefully. Leaving PID file for Brooke to inspect."
    exit 1
  fi
  remove_stale_pid_files
  echo "Stopped Jordana Billing server (PID $PID)."
  exit 0
fi

remove_stale_pid_files
echo "Removed stale or untrusted Jordana Billing PID metadata. No process was stopped."
