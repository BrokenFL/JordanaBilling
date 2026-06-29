#!/usr/bin/env bash
#
# First-run bootstrap for Jordana Billing.
# Creates venv, installs deps, creates .env from template if missing,
# resolves __PROJECT_DIR__ automatically, validates .env, inits DB,
# migrates, runs full Google Sheets sync, starts the review server,
# waits for health check, and opens the browser.
#
# Safe to re-run: later launches skip venv creation, dependency
# install, and full sync if data already exists.
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/bootstrap.log"
PID_FILE="$PROJECT_DIR/logs/review_server.pid"
DB_PATH="$PROJECT_DIR/data/jordana_invoice.sqlite3"
PORT=8765
HEALTH_URL="http://127.0.0.1:${PORT}/api/health"
MAX_HEALTH_WAIT=30

mkdir -p "$LOG_DIR" data Reports

# --- Sanitized logging helper (no credentials) ---
log() {
  local msg="$1"
  msg="$(echo "$msg" | sed -E 's/(jb_[0-9a-fA-F]{8,})/[REDACTED]/g; s/(AKIA[0-9A-Z]{16})/[REDACTED]/g; s/(https:\/\/[^ ]+)/[URL]/g')"
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $msg" | tee -a "$LOG_FILE"
}

# --- macOS error dialog helper ---
show_error_dialog() {
  local title="$1"
  local message="$2"
  if [[ -t 0 ]]; then
    echo "ERROR: $title — $message" >&2
  else
    /usr/bin/osascript -e "display dialog \"$message\" with title \"$title\" buttons {\"OK\"} default button \"OK\" with icon stop" 2>/dev/null || true
  fi
}

# --- Prevent duplicate instances ---
if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  log "Server already running (PID $(cat "$PID_FILE")). Opening browser."
  open "http://127.0.0.1:${PORT}/review" 2>/dev/null || true
  exit 0
fi

# --- Step 1: Discover Python 3.11+ ---
# When launched from Finder, PATH is minimal and may not include Homebrew.
# Search known locations in priority order, then fall back to PATH.
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
      if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        echo "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

log "Searching for Python 3.11+..."
PYTHON_BIN="$(find_python || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  show_error_dialog "Python Required" "Python 3.11 or newer is required but was not found.

Checked locations:
  /opt/homebrew/bin/python3
  /usr/local/bin/python3
  /usr/bin/python3

Install Python from python.org or run: brew install python@3.12"
  exit 1
fi
log "Using Python: $PYTHON_BIN ($("$PYTHON_BIN" --version 2>&1))"

# --- Step 2: Create virtual environment if missing ---
if [[ ! -d "$PROJECT_DIR/.venv" ]]; then
  log "Creating virtual environment..."
  "$PYTHON_BIN" -m venv "$PROJECT_DIR/.venv"
fi

# --- Step 3: Install pinned dependencies ---
log "Installing dependencies..."
. "$PROJECT_DIR/.venv/bin/activate"
python -m pip install --upgrade pip >/dev/null 2>&1
python -m pip install -e "$PROJECT_DIR" >/dev/null 2>&1

# --- Step 4: Create .env from template if missing ---
if [[ ! -f "$PROJECT_DIR/.env" ]]; then
  if [[ -f "$PROJECT_DIR/.env.example" ]]; then
    # Copy template and auto-resolve __PROJECT_DIR__
    sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$PROJECT_DIR/.env.example" > "$PROJECT_DIR/.env"
    open -a TextEdit "$PROJECT_DIR/.env" 2>/dev/null || open "$PROJECT_DIR/.env" 2>/dev/null || true
    show_error_dialog "Configuration Created" "A .env file was created and opened in TextEdit:

$PROJECT_DIR/.env

Fill in:
  • JORDANA_APPS_SCRIPT_URL — your Google Apps Script web app URL
  • JORDANA_INGEST_API_KEY — your Apps Script ingest API key

Save the file, then double-click Jordana Billing.app again."
    exit 1
  else
    show_error_dialog "Configuration Missing" "No .env file found and no .env.example template. Create a file at:

$PROJECT_DIR/.env

with JORDANA_APPS_SCRIPT_URL and JORDANA_INGEST_API_KEY."
    exit 1
  fi
fi

# --- Step 5: Auto-resolve __PROJECT_DIR__ in .env if present ---
if grep -q '__PROJECT_DIR__' "$PROJECT_DIR/.env" 2>/dev/null; then
  log "Resolving __PROJECT_DIR__ in .env..."
  sed -i '' "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$PROJECT_DIR/.env"
fi

# --- Step 6: Validate .env without executing it as shell code ---
log "Validating .env..."
MISSING_VAR="$(PYTHONPATH=app python - "$PROJECT_DIR/.env" <<'PY'
import os
import sys
from pathlib import Path

from jordana_invoice.google_sync import load_env_file

load_env_file(Path(sys.argv[1]))
for key in ("JORDANA_APPS_SCRIPT_URL", "JORDANA_INGEST_API_KEY"):
    if not os.environ.get(key):
        print(key)
        break
PY
)"

if [[ "$MISSING_VAR" == "JORDANA_APPS_SCRIPT_URL" ]]; then
  show_error_dialog "Configuration Incomplete" "JORDANA_APPS_SCRIPT_URL is empty.

Edit this file:
  $PROJECT_DIR/.env

Fill in the Google Apps Script /exec web app URL."
  exit 1
fi
if [[ "$MISSING_VAR" == "JORDANA_INGEST_API_KEY" ]]; then
  show_error_dialog "Configuration Incomplete" "JORDANA_INGEST_API_KEY is empty.

Edit this file:
  $PROJECT_DIR/.env

Fill in the Apps Script ingest API key."
  exit 1
fi

# Keep the installer database path authoritative for this checkout.
export JORDANA_DATABASE_PATH="$DB_PATH"
log ".env validated."

# --- Step 7: Create blank SQLite database if missing ---
if [[ ! -f "$DB_PATH" ]]; then
  log "No existing database found — creating new database."
  PYTHONPATH=app python -m jordana_invoice --db "$DB_PATH" init-db
else
  log "Existing database found at $DB_PATH — preserving (not a clean install)."
fi

# --- Step 8: Apply pending migrations safely ---
log "Applying migrations..."
PYTHONPATH=app python -m jordana_invoice --db "$DB_PATH" init-db

# --- Step 9: Start the local app ---
# The review server opens first, then runs intelligent Sheet sync in the background
# based on durable sync_state cursor state.
log "Starting review server on port $PORT..."

# Stop any stale server on the port
lsof -ti ":${PORT}" 2>/dev/null | xargs kill 2>/dev/null || true

PYTHONPATH=app nohup python -m jordana_invoice --db "$DB_PATH" serve-review --host 127.0.0.1 --port "$PORT" >>"$LOG_FILE" 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"
log "Server started (PID $SERVER_PID)."

# --- Step 10: Wait for successful health check ---
log "Waiting for health check..."
HEALTH_OK=0
for i in $(seq 1 $MAX_HEALTH_WAIT); do
  if curl -sf "$HEALTH_URL" >/dev/null 2>&1; then
    HEALTH_OK=1
    break
  fi
  sleep 1
done

if [[ "$HEALTH_OK" -ne 1 ]]; then
  show_error_dialog "Startup Failed" "The review server did not become healthy within ${MAX_HEALTH_WAIT} seconds. Check logs/bootstrap.log for details."
  kill "$SERVER_PID" 2>/dev/null || true
  rm -f "$PID_FILE"
  exit 1
fi
log "Health check passed."

# --- Step 12: Open the review UI in the default browser ---
log "Opening browser..."
open "http://127.0.0.1:${PORT}/review" 2>/dev/null || true
log "Bootstrap complete."
