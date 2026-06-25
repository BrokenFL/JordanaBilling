#!/usr/bin/env bash
#
# Start Jordana Billing review server.
# Subsequent launches: applies pending migrations, starts server,
# waits for health check, opens browser. Does NOT run full sync.
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/start.log"
PID_FILE="$PROJECT_DIR/logs/review_server.pid"
DB_PATH="$PROJECT_DIR/data/jordana_invoice.sqlite3"
PORT=8765
HEALTH_URL="http://127.0.0.1:${PORT}/api/health"
MAX_HEALTH_WAIT=15

mkdir -p "$LOG_DIR"

log() {
  local msg="$1"
  msg="$(echo "$msg" | sed -E 's/(jb_[0-9a-fA-F]{8,})/[REDACTED]/g; s/(AKIA[0-9A-Z]{16})/[REDACTED]/g; s/(https:\/\/[^ ]+)/[URL]/g')"
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $msg" | tee -a "$LOG_FILE"
}

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

# --- Activate venv ---
if [[ ! -d "$PROJECT_DIR/.venv" ]]; then
  show_error_dialog "Not Installed" "Virtual environment not found. Run scripts/bootstrap.sh first."
  exit 1
fi
. "$PROJECT_DIR/.venv/bin/activate"

# --- Validate .env ---
if [[ ! -f "$PROJECT_DIR/.env" ]]; then
  show_error_dialog "Configuration Missing" "No .env file found. Copy .env.example to .env and fill in credentials."
  exit 1
fi

# --- Apply pending migrations ---
log "Applying pending migrations..."
PYTHONPATH=app python -m jordana_invoice --db "$DB_PATH" init-db

# --- Start server ---
log "Starting review server on port $PORT..."
lsof -ti ":${PORT}" 2>/dev/null | xargs kill 2>/dev/null || true

PYTHONPATH=app nohup python -m jordana_invoice --db "$DB_PATH" serve-review --host 127.0.0.1 --port "$PORT" >>"$LOG_FILE" 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"
log "Server started (PID $SERVER_PID)."

# --- Wait for health check ---
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
  show_error_dialog "Startup Failed" "The review server did not become healthy within ${MAX_HEALTH_WAIT} seconds. Check logs/start.log."
  kill "$SERVER_PID" 2>/dev/null || true
  rm -f "$PID_FILE"
  exit 1
fi
log "Health check passed."

# --- Open browser ---
log "Opening browser..."
open "http://127.0.0.1:${PORT}/review" 2>/dev/null || true
log "Start complete."
