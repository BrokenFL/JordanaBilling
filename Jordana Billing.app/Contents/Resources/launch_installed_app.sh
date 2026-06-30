#!/usr/bin/env bash
#
# Production daily launcher for an installed Jordana Billing.app.
#
# Normal launch validates an existing installed runtime, private config, and
# private database, then starts the local server. It does not install, repair,
# upgrade, download, or create private data.
#
set -euo pipefail

APP_SUPPORT_DIR="${JORDANA_APP_SUPPORT_DIR:-$HOME/Library/Application Support/Jordana Billing}"
CONFIG_FILE="${JORDANA_CONFIG_FILE:-$APP_SUPPORT_DIR/config/.env}"
DB_PATH="${JORDANA_DATABASE_PATH:-$APP_SUPPORT_DIR/data/jordana_invoice.sqlite3}"
LOG_DIR="$APP_SUPPORT_DIR/logs"
RUNTIME_DIR="$APP_SUPPORT_DIR/runtime"
PORT="${JORDANA_PORT:-8765}"
REVIEW_URL="http://127.0.0.1:${PORT}/review"
HEALTH_URL="http://127.0.0.1:${PORT}/api/health"
MAX_HEALTH_WAIT="${JORDANA_MAX_HEALTH_WAIT:-30}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_RESOURCES_DIR="$SCRIPT_DIR"
APP_DIR="$(cd "$APP_RESOURCES_DIR/../.." && pwd)"
VENV_PYTHON="$APP_RESOURCES_DIR/runtime/venv/bin/python"
PID_FILE="$RUNTIME_DIR/review_server.pid"
METADATA_FILE="$RUNTIME_DIR/review_server.meta"
LOG_FILE="$LOG_DIR/launch.log"

mkdir -p "$LOG_DIR" "$RUNTIME_DIR"

sanitize_message() {
  sed -E \
    -e 's/[j]b_[0-9a-fA-F]{8,}/[REDACTED]/g' \
    -e 's/[A]KIA[0-9A-Z]{16}/[REDACTED]/g' \
    -e 's#https://[^[:space:]]+#[URL]#g'
}

log_message() {
  local msg="$1"
  msg="$(printf '%s' "$msg" | sanitize_message)"
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$msg" >> "$LOG_FILE"
}

show_error_dialog() {
  local title="$1"
  local message="$2"
  message="$(printf '%s' "$message" | sanitize_message)"
  log_message "ERROR: $title - $message"
  if [[ -t 0 ]]; then
    printf 'ERROR: %s - %s\n' "$title" "$message" >&2
  else
    /usr/bin/osascript -e "display dialog \"$message\" with title \"$title\" buttons {\"OK\"} default button \"OK\" with icon stop" >/dev/null 2>&1 || true
  fi
}

fail_launcher() {
  show_error_dialog "$1" "$2"
  exit 1
}

load_private_env() {
  "$VENV_PYTHON" - "$CONFIG_FILE" "$DB_PATH" "$APP_SUPPORT_DIR" <<'PY'
import os
import sqlite3
import sys
from pathlib import Path

config = Path(sys.argv[1]).expanduser()
db_path = Path(sys.argv[2]).expanduser()
support = Path(sys.argv[3]).expanduser()
if not config.is_file():
    raise SystemExit("MISSING_CONFIG\tPrivate configuration is missing at ~/Library/Application Support/Jordana Billing/config/.env.")
values = {}
for raw in config.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    values[key.strip()] = value.strip().strip('"').strip("'")
for key in ("JORDANA_APPS_SCRIPT_URL", "JORDANA_INGEST_API_KEY"):
    if not values.get(key):
        raise SystemExit(f"MISSING_CONFIG\t{key} is missing or empty in private configuration.")
if not db_path.is_file():
    raise SystemExit("MISSING_DATABASE\tThe operational SQLite database is missing from Application Support. Transfer it or run the installer with --init-empty-db for a disposable test install.")
try:
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
    finally:
        conn.close()
except sqlite3.Error:
    raise SystemExit("DATABASE_UNREADABLE\tThe operational SQLite database could not be opened read-only.")
if not integrity or integrity[0] != "ok":
    raise SystemExit("DATABASE_UNREADABLE\tThe operational SQLite database did not pass integrity_check.")
values["JORDANA_DATABASE_PATH"] = str(db_path.resolve())
values.setdefault("JORDANA_REPORTS_DIR", str((support / "Reports").resolve()))
values.setdefault("JORDANA_BACKUP_DIR", str((support / "backups").resolve()))
for key, value in sorted(values.items()):
    print(f"{key}={value}")
PY
}

export_private_env() {
  local line key value
  while IFS= read -r line; do
    key="${line%%=*}"
    value="${line#*=}"
    export "$key=$value"
  done < <(load_private_env)
}

is_numeric_pid() {
  [[ "${1:-}" =~ ^[0-9]+$ ]]
}

pid_is_running() {
  local pid="${1:-}"
  is_numeric_pid "$pid" && kill -0 "$pid" >/dev/null 2>&1
}

pid_command_line() {
  ps -p "$1" -o command= 2>/dev/null || true
}

pid_looks_like_jordana() {
  local command
  command="$(pid_command_line "$1")"
  [[ "$command" == *"jordana_invoice"* && "$command" == *"serve-review"* && "$command" == *"$DB_PATH"* ]]
}

pid_metadata_matches() {
  local pid="$1"
  [[ -f "$METADATA_FILE" ]] || return 1
  grep -qx "pid=$pid" "$METADATA_FILE" || return 1
  grep -qx "app_dir=$APP_DIR" "$METADATA_FILE" || return 1
  grep -qx "db_path=$DB_PATH" "$METADATA_FILE" || return 1
  grep -qx "port=$PORT" "$METADATA_FILE" || return 1
}

pid_is_app_owned() {
  local pid="$1"
  pid_is_running "$pid" && pid_metadata_matches "$pid" && pid_looks_like_jordana "$pid"
}

remove_stale_pid_files() {
  rm -f "$PID_FILE" "$METADATA_FILE"
}

health_is_jordana() {
  "$VENV_PYTHON" - "$HEALTH_URL" <<'PY' >/dev/null 2>&1
import json
import sys
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1], timeout=2) as response:
        data = json.loads(response.read().decode("utf-8"))
except Exception:
    raise SystemExit(1)
if data.get("ok") is not True or data.get("status") != "healthy":
    raise SystemExit(1)
PY
}

http_service_status() {
  "$VENV_PYTHON" - "$HEALTH_URL" <<'PY'
import json
import sys
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1], timeout=2) as response:
        body = response.read().decode("utf-8", "replace")
except Exception:
    raise SystemExit(1)
try:
    data = json.loads(body)
except Exception:
    print("http_other")
    raise SystemExit(0)
if data.get("ok") is True and data.get("status") == "healthy":
    print("jordana")
else:
    print("http_other")
PY
}

port_accepts_tcp() {
  "$VENV_PYTHON" - "$PORT" <<'PY' >/dev/null 2>&1
import socket
import sys

with socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=2):
    pass
PY
}

pid_on_port() {
  lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | head -n 1 || true
}

write_pid_metadata() {
  local pid="$1"
  printf '%s\n' \
    "pid=$pid" \
    "app_dir=$APP_DIR" \
    "db_path=$DB_PATH" \
    "port=$PORT" \
    "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    > "$METADATA_FILE"
  printf '%s\n' "$pid" > "$PID_FILE"
}

open_review_url() {
  /usr/bin/open "$REVIEW_URL" >/dev/null 2>&1 || true
}

handle_existing_server_or_port() {
  local pid port_pid status
  if [[ -f "$PID_FILE" ]]; then
    pid="$(tr -dc '0-9' < "$PID_FILE")"
    if pid_is_app_owned "$pid"; then
      if health_is_jordana; then
        log_message "Jordana Billing is already running; opening browser."
        open_review_url
        exit 0
      fi
      fail_launcher "Local Server Not Ready" "A Jordana Billing process is running, but the health check did not pass."
    fi
    remove_stale_pid_files
  fi

  status="$(http_service_status 2>/dev/null || true)"
  if [[ "$status" == "jordana" ]]; then
    port_pid="$(pid_on_port)"
    if [[ -n "$port_pid" ]] && pid_looks_like_jordana "$port_pid"; then
      write_pid_metadata "$port_pid"
      open_review_url
      exit 0
    fi
    fail_launcher "Jordana Billing Already Running" "Jordana Billing is already running under another macOS user account. Log out of that account or stop the other session, then try again."
  elif [[ "$status" == "http_other" ]]; then
    fail_launcher "Port ${PORT} Is In Use" "Port ${PORT} is already being used by another application. Jordana Billing did not stop or reuse that process."
  elif port_accepts_tcp; then
    fail_launcher "Port ${PORT} Is In Use" "Port ${PORT} is already occupied, but it did not return a Jordana Billing health response. Jordana Billing did not stop or reuse that process."
  fi

  port_pid="$(pid_on_port)"
  if [[ -n "$port_pid" ]]; then
    if pid_looks_like_jordana "$port_pid" && health_is_jordana; then
      write_pid_metadata "$port_pid"
      open_review_url
      exit 0
    fi
    fail_launcher "Port ${PORT} Is In Use" "Port ${PORT} is already being used by another application. Jordana Billing did not stop or reuse that process."
  fi
}

wait_for_health_or_fail() {
  local i
  for i in $(seq 1 "$MAX_HEALTH_WAIT"); do
    if pid_is_app_owned "$SERVER_PID" && health_is_jordana; then
      log_message "Health check passed."
      return 0
    fi
    sleep 1
  done
  if pid_is_app_owned "$SERVER_PID"; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
  fi
  remove_stale_pid_files
  fail_launcher "Local Server Did Not Become Ready" "Jordana Billing started but did not become ready within ${MAX_HEALTH_WAIT} seconds. No unrelated processes were stopped."
}

[[ -x "$VENV_PYTHON" ]] || fail_launcher "Installed Runtime Missing" "The installed private Python runtime is missing. Run the versioned release installer again."
"$VENV_PYTHON" -c 'import jordana_invoice' >/dev/null 2>&1 || fail_launcher "Application Package Missing" "Jordana Billing is not installed in the private app runtime."

export_private_env
mkdir -p "$JORDANA_REPORTS_DIR" "$JORDANA_BACKUP_DIR"

log_message "Starting installed Jordana Billing."
handle_existing_server_or_port
"$VENV_PYTHON" -m jordana_invoice --db "$DB_PATH" init-db >> "$LOG_FILE" 2>&1
handle_existing_server_or_port
nohup "$VENV_PYTHON" -m jordana_invoice --db "$DB_PATH" serve-review --host 127.0.0.1 --port "$PORT" >> "$LOG_FILE" 2>&1 &
SERVER_PID=$!
write_pid_metadata "$SERVER_PID"
wait_for_health_or_fail
open_review_url
log_message "Installed launch complete."
