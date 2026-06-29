#!/usr/bin/env bash
#
# Full Google Sheets sync wrapper.
# Fetches all available raw staged rows from Apps Script.
# Idempotent: snapshot_key uniqueness prevents duplicate imports.
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/sync.log"
mkdir -p "$LOG_DIR"

log() {
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $1" | tee -a "$LOG_FILE"
}

if [[ ! -f "$PROJECT_DIR/.env" ]]; then
  echo "ERROR: .env not found. Copy .env.example to .env and fill in credentials." >&2
  exit 1
fi

if [[ ! -d "$PROJECT_DIR/.venv" ]]; then
  echo "ERROR: .venv not found. Run scripts/bootstrap.sh first." >&2
  exit 1
fi

. "$PROJECT_DIR/.venv/bin/activate"

log "Starting full sync..."
if PYTHONPATH=app python -m jordana_invoice sync --full --env "$PROJECT_DIR/.env" 2>&1 | tee -a "$LOG_FILE"; then
  log "Full sync completed successfully."
else
  log "Full sync failed."
  exit 1
fi

log "Sync status:"
PYTHONPATH=app python -m jordana_invoice sync-status --env "$PROJECT_DIR/.env" 2>&1 | tee -a "$LOG_FILE"
