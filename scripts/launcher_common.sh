#!/usr/bin/env bash
#
# Shared macOS launcher helpers for Jordana Billing.
#
# This file intentionally keeps startup checks in shell so the double-click
# app can fail before importing application code when Python or the venv is
# missing. It never sources .env; private configuration is parsed as data by
# jordana_invoice.google_sync.load_env_file.
#

set -euo pipefail

JORDANA_PORT="${JORDANA_PORT:-8765}"
JORDANA_REVIEW_URL="http://127.0.0.1:${JORDANA_PORT}/review"
JORDANA_HEALTH_URL="http://127.0.0.1:${JORDANA_PORT}/api/health"
JORDANA_MAX_HEALTH_WAIT="${JORDANA_MAX_HEALTH_WAIT:-30}"
JORDANA_APP_NAME="Jordana Billing"

ensure_runtime_paths() {
  PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  LOG_DIR="$PROJECT_DIR/logs"
  PID_FILE="$LOG_DIR/review_server.pid"
  METADATA_FILE="$LOG_DIR/review_server.meta"
  ENV_FILE="$PROJECT_DIR/.env"
  DEFAULT_DB_PATH="$PROJECT_DIR/data/jordana_invoice.sqlite3"
  mkdir -p "$LOG_DIR"
}

sanitize_message() {
  sed -E \
    -e 's/jb_[0-9a-fA-F]{8,}/[REDACTED]/g' \
    -e 's/AKIA[0-9A-Z]{16}/[REDACTED]/g' \
    -e 's#https://[^[:space:]]+#[URL]#g'
}

log_message() {
  local msg="$1"
  msg="$(printf '%s' "$msg" | sanitize_message)"
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$msg" >> "$LOG_FILE"
  if [[ -t 1 ]]; then
    printf '%s\n' "$msg"
  fi
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

open_review_url() {
  /usr/bin/open "$JORDANA_REVIEW_URL" >/dev/null 2>&1 || true
}

find_python() {
  local candidates=(
    "/opt/homebrew/bin/python3"
    "/usr/local/bin/python3"
    "/usr/bin/python3"
  )
  local path_python
  path_python="$(command -v python3 2>/dev/null || true)"
  if [[ -n "$path_python" && -x "$path_python" ]]; then
    candidates+=("$path_python")
  fi
  for candidate in "${candidates[@]}"; do
    if [[ -x "$candidate" ]]; then
      if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
        printf '%s\n' "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

resolve_project_placeholders() {
  if [[ -f "$ENV_FILE" ]] && grep -q '__PROJECT_DIR__' "$ENV_FILE" 2>/dev/null; then
    log_message "Resolving __PROJECT_DIR__ placeholders in .env."
    sed -i '' "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$ENV_FILE"
  fi
}

ensure_env_file() {
  if [[ -f "$ENV_FILE" ]]; then
    return 0
  fi
  if [[ -f "$PROJECT_DIR/.env.example" ]]; then
    sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$PROJECT_DIR/.env.example" > "$ENV_FILE"
    /usr/bin/open -a TextEdit "$ENV_FILE" >/dev/null 2>&1 || /usr/bin/open "$ENV_FILE" >/dev/null 2>&1 || true
    fail_launcher "Configuration Created" "A .env file was created and opened in TextEdit.

Fill in the private configuration values, securely transfer the operational database, save the file, then double-click Jordana Billing.app again."
  fi
  fail_launcher "Configuration Missing" "No .env file was found and no .env.example template exists. The installation needs Brooke's attention."
}

validate_private_configuration() {
  local result status message db_path
  result="$("$VENV_PYTHON" "$PROJECT_DIR/scripts/validate_launcher_environment.py" "$PROJECT_DIR" "$ENV_FILE" 2>/dev/null || true)"
  status="${result%%$'\t'*}"
  message="${result#*$'\t'}"
  if [[ "$status" != "OK" ]]; then
    case "$status" in
      MISSING_CONFIG)
        fail_launcher "Required Private Configuration Missing" "$message"
        ;;
      MISSING_DATABASE)
        fail_launcher "Configured Database Not Found" "$message"
        ;;
      DATABASE_UNREADABLE)
        fail_launcher "Configured Database Could Not Be Opened" "$message"
        ;;
      *)
        fail_launcher "Installation Needs Brooke's Attention" "$message"
        ;;
    esac
  fi
  db_path="${message#DB_PATH=}"
  if [[ "$db_path" == "$message" || -z "$db_path" ]]; then
    fail_launcher "Installation Needs Brooke's Attention" "The launcher could not resolve the configured database path."
  fi
  DB_PATH="$db_path"
  export JORDANA_DATABASE_PATH="$DB_PATH"
  log_message "Private configuration and database checks passed."
}

ensure_venv_exists() {
  if [[ ! -x "$PROJECT_DIR/.venv/bin/python" ]]; then
    fail_launcher "Python Environment Not Found" "The local Python environment is missing. Run scripts/bootstrap.sh once, or double-click Jordana Billing.app after Python 3.11+ is installed."
  fi
  VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
}

ensure_package_importable() {
  if ! "$VENV_PYTHON" -c 'import jordana_invoice' >/dev/null 2>&1; then
    fail_launcher "Jordana Billing Package Not Installed" "The Python package is not installed in .venv. Run scripts/bootstrap.sh to repair the installation."
  fi
}

prepare_bootstrap_environment() {
  local python_bin
  python_bin="$(find_python || true)"
  if [[ -z "$python_bin" ]]; then
    fail_launcher "Python Required" "Python 3.11 or newer was not found. Install Python from python.org or Homebrew, then launch again."
  fi
  log_message "Using Python: $("$python_bin" --version 2>&1)"
  if [[ ! -d "$PROJECT_DIR/.venv" ]]; then
    log_message "Creating project-local Python environment."
    "$python_bin" -m venv "$PROJECT_DIR/.venv"
  fi
  VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
  log_message "Installing Jordana Billing package into .venv."
  "$VENV_PYTHON" -m pip install --upgrade pip >/dev/null
  "$VENV_PYTHON" -m pip install -e "$PROJECT_DIR" >/dev/null
  ensure_package_importable
}

is_numeric_pid() {
  [[ "${1:-}" =~ ^[0-9]+$ ]]
}

pid_is_running() {
  local pid="${1:-}"
  is_numeric_pid "$pid" && kill -0 "$pid" >/dev/null 2>&1
}

pid_command_line() {
  local pid="$1"
  ps -p "$pid" -o command= 2>/dev/null || true
}

pid_looks_like_jordana() {
  local pid="$1"
  local command
  command="$(pid_command_line "$pid")"
  [[ "$command" == *"jordana_invoice"* && "$command" == *"serve-review"* && "$command" == *"$PROJECT_DIR"* ]]
}

pid_metadata_matches() {
  local pid="$1"
  [[ -f "$METADATA_FILE" ]] || return 1
  grep -qx "pid=$pid" "$METADATA_FILE" || return 1
  grep -qx "project_dir=$PROJECT_DIR" "$METADATA_FILE" || return 1
  grep -qx "port=$JORDANA_PORT" "$METADATA_FILE" || return 1
}

pid_is_app_owned() {
  local pid="$1"
  pid_is_running "$pid" && pid_metadata_matches "$pid" && pid_looks_like_jordana "$pid"
}

remove_stale_pid_files() {
  rm -f "$PID_FILE" "$METADATA_FILE"
}

health_is_jordana() {
  local body
  body="$(curl -fsS --max-time 2 "$JORDANA_HEALTH_URL" 2>/dev/null || true)"
  [[ "$body" == *'"ok": true'* || "$body" == *'"ok":true'* ]] && [[ "$body" == *'"healthy"'* ]]
}

http_service_status() {
  "$VENV_PYTHON" - "$JORDANA_HEALTH_URL" <<'PY'
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
  "$VENV_PYTHON" - "$JORDANA_PORT" <<'PY' >/dev/null 2>&1
import socket
import sys

with socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=2):
    pass
PY
}

pid_on_port() {
  lsof -nP -tiTCP:"$JORDANA_PORT" -sTCP:LISTEN 2>/dev/null | head -n 1 || true
}

handle_existing_server_or_port() {
  local pid port_pid status
  if [[ -f "$PID_FILE" ]]; then
    pid="$(tr -dc '0-9' < "$PID_FILE")"
    if pid_is_app_owned "$pid"; then
      if health_is_jordana; then
        log_message "Jordana Billing is already running on port $JORDANA_PORT; opening browser."
        open_review_url
        exit 0
      fi
      fail_launcher "Local Server Not Ready" "A Jordana Billing process is running, but the health check did not pass. The installation needs Brooke's attention."
    fi
    log_message "Removing stale or untrusted Jordana Billing PID metadata."
    remove_stale_pid_files
  fi

  status="$(http_service_status 2>/dev/null || true)"
  if [[ "$status" == "jordana" ]]; then
    port_pid="$(pid_on_port)"
    if [[ -n "$port_pid" ]] && pid_looks_like_jordana "$port_pid"; then
      log_message "Found an existing Jordana Billing server on port $JORDANA_PORT; recording ownership and opening browser."
      write_pid_metadata "$port_pid"
      open_review_url
      exit 0
    fi
    fail_launcher "Jordana Billing Already Running" "Jordana Billing is already running under another macOS user account. Log out of that account or stop the other session, then try again."
  elif [[ "$status" == "http_other" ]]; then
    fail_launcher "Port 8765 Is In Use" "Port 8765 is already being used by another application. Jordana Billing did not stop or reuse that process. Ask Brooke to close the other app or change the port."
  elif port_accepts_tcp; then
    fail_launcher "Port 8765 Is In Use" "Port 8765 is already occupied, but it did not return a Jordana Billing health response. Jordana Billing did not stop or reuse that process. Ask Brooke to close the other app or change the port."
  fi

  port_pid="$(pid_on_port)"
  if [[ -n "$port_pid" ]]; then
    if pid_looks_like_jordana "$port_pid" && health_is_jordana; then
      log_message "Found an existing Jordana Billing server on port $JORDANA_PORT; recording ownership and opening browser."
      write_pid_metadata "$port_pid"
      open_review_url
      exit 0
    fi
    fail_launcher "Port 8765 Is In Use" "Port 8765 is already being used by another application. Jordana Billing did not stop or reuse that process. Ask Brooke to close the other app or change the port."
  fi
}

write_pid_metadata() {
  local pid="$1"
  printf '%s\n' \
    "pid=$pid" \
    "project_dir=$PROJECT_DIR" \
    "port=$JORDANA_PORT" \
    "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    > "$METADATA_FILE"
  printf '%s\n' "$pid" > "$PID_FILE"
}

apply_pending_migrations() {
  log_message "Applying pending migrations with the configured database."
  "$VENV_PYTHON" -m jordana_invoice --db "$DB_PATH" init-db >> "$LOG_FILE" 2>&1
}

start_review_server() {
  log_message "Starting Jordana Billing review server on 127.0.0.1:$JORDANA_PORT."
  PYTHONPATH="$PROJECT_DIR/app" nohup "$VENV_PYTHON" -m jordana_invoice --db "$DB_PATH" serve-review --host 127.0.0.1 --port "$JORDANA_PORT" >> "$LOG_FILE" 2>&1 &
  SERVER_PID=$!
  write_pid_metadata "$SERVER_PID"
  log_message "Started Jordana Billing server with PID $SERVER_PID."
}

wait_for_health_or_fail() {
  local i
  for i in $(seq 1 "$JORDANA_MAX_HEALTH_WAIT"); do
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
  fail_launcher "Local Server Did Not Become Ready" "Jordana Billing started but did not become ready within ${JORDANA_MAX_HEALTH_WAIT} seconds. No unrelated processes were stopped."
}

launch_jordana_billing() {
  ensure_runtime_paths
  cd "$PROJECT_DIR"
  ensure_venv_exists
  ensure_package_importable
  ensure_env_file
  resolve_project_placeholders
  validate_private_configuration
  handle_existing_server_or_port
  apply_pending_migrations
  handle_existing_server_or_port
  start_review_server
  wait_for_health_or_fail
  open_review_url
  log_message "Jordana Billing launch complete."
}
