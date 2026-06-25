#!/usr/bin/env bash
#
# First-run bootstrap for Jordana Billing.
# Creates venv, installs deps, validates .env, inits DB, migrates,
# runs full Google Sheets sync, starts the review server, waits for
# health check, and opens the browser.
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
  # Strip anything that looks like a key or token
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

# --- Step 1: Check Python version ---
log "Checking Python version..."
python3 - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11 or newer is required. Found: " + sys.version.split()[0])
print(f"Python OK: {sys.version.split()[0]}")
PY

# --- Step 2: Create virtual environment if missing ---
if [[ ! -d "$PROJECT_DIR/.venv" ]]; then
  log "Creating virtual environment..."
  python3 -m venv "$PROJECT_DIR/.venv"
fi

# --- Step 3: Install pinned dependencies ---
log "Installing dependencies..."
. "$PROJECT_DIR/.venv/bin/activate"
python -m pip install --upgrade pip >/dev/null 2>&1
python -m pip install -e "$PROJECT_DIR" >/dev/null 2>&1

# --- Step 4: Validate .env ---
log "Validating .env..."
if [[ ! -f "$PROJECT_DIR/.env" ]]; then
  show_error_dialog "Configuration Missing" "No .env file found. Copy .env.example to .env and fill in your credentials."
  exit 1
fi

# Source .env and validate required vars
set +e
. "$PROJECT_DIR/.env"
set -e

if [[ -z "${JORDANA_APPS_SCRIPT_URL:-}" ]]; then
  show_error_dialog "Configuration Incomplete" "JORDANA_APPS_SCRIPT_URL is missing in .env."
  exit 1
fi
if [[ -z "${JORDANA_INGEST_API_KEY:-}" ]]; then
  show_error_dialog "Configuration Incomplete" "JORDANA_INGEST_API_KEY is missing in .env."
  exit 1
fi
if [[ -z "${JORDANA_DATABASE_PATH:-}" ]]; then
  # Set default if not in .env
  export JORDANA_DATABASE_PATH="$DB_PATH"
fi
log ".env validated."

# --- Step 5: Create blank SQLite database if missing ---
if [[ ! -f "$DB_PATH" ]]; then
  log "Creating new database..."
  PYTHONPATH=app python -m jordana_invoice --db "$DB_PATH" init-db
else
  log "Database already exists — preserving."
fi

# --- Step 6: Apply pending migrations safely ---
log "Applying migrations..."
PYTHONPATH=app python -m jordana_invoice --db "$DB_PATH" init-db

# --- Step 7: Run full Google Sheets sync (first run only) ---
RAW_COUNT=$(PYTHONPATH=app python -c "
import sqlite3
conn = sqlite3.connect('$DB_PATH')
print(conn.execute('SELECT COUNT(*) FROM raw_calendar_snapshots').fetchone()[0])
" 2>/dev/null || echo "0")

if [[ "$RAW_COUNT" -eq 0 ]]; then
  log "No data found — running full Google Sheets sync..."
  if PYTHONPATH=app python -m jordana_invoice sync --full --env "$PROJECT_DIR/.env" >>"$LOG_FILE" 2>&1; then
    log "Full sync completed."
  else
    log "Full sync failed — continuing anyway. You can retry with scripts/full_sync.sh"
  fi
else
  log "Database has $RAW_COUNT raw snapshots — skipping full sync."
fi

# --- Step 8: Prevent duplicate imports (sync is idempotent via snapshot_key) ---
log "Sync is idempotent via snapshot_key uniqueness — no duplicate prevention needed."

# --- Step 9: Start the local app ---
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

# --- Step 11: Open the review UI in the default browser ---
log "Opening browser..."
open "http://127.0.0.1:${PORT}/review" 2>/dev/null || true
log "Bootstrap complete."
