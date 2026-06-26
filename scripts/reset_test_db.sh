#!/usr/bin/env bash
#
# TEST-ONLY database reset.
# Requires explicit confirmation, backs up first, and refuses to
# touch the operational database.
#
# Usage:
#   scripts/reset_test_db.sh /path/to/test.db   # resets a specific test database
#
# This script REFUSES to operate on the configured operational database.
# Use scripts/backup_db.sh and manual SQL for operational database maintenance.
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ $# -lt 1 ]]; then
  echo "Usage: scripts/reset_test_db.sh /path/to/test.db" >&2
  echo "This script requires an explicit database path and refuses to" >&2
  echo "operate on the configured operational database." >&2
  exit 1
fi

DB_PATH="$1"

# --- Refuse the operational database ---
OPERATIONAL_DB="$(PYTHONPATH=app python3 -c "
import os, sys
sys.path.insert(0, 'app')
from jordana_invoice.db import get_configured_operational_db_path
print(get_configured_operational_db_path().resolve())
" 2>/dev/null || echo "")"

if [[ -n "$OPERATIONAL_DB" ]]; then
  CANDIDATE="$(python3 -c "
from pathlib import Path
print(Path('$DB_PATH').resolve())
" 2>/dev/null || echo "")"
  if [[ "$CANDIDATE" == "$OPERATIONAL_DB" ]]; then
    echo "REFUSED: '$DB_PATH' is the configured operational database." >&2
    echo "This script is test-only and will not reset the operational database." >&2
    exit 1
  fi
fi

if [[ ! -f "$DB_PATH" ]]; then
  echo "Database does not exist: $DB_PATH"
  echo "Nothing to reset."
  exit 0
fi

# Check for production indicators
PROD_INDICATORS=$(python3 -c "
import sqlite3, sys
conn = sqlite3.connect('$DB_PATH')
indicators = []
try:
    count = conn.execute('SELECT COUNT(*) FROM sessions WHERE review_status = \"approved\"').fetchone()[0]
    if count > 0:
        indicators.append(f'{count} approved sessions')
except sqlite3.OperationalError:
    pass
try:
    count = conn.execute('SELECT COUNT(*) FROM invoices WHERE status != \"draft\"').fetchone()[0]
    if count > 0:
        indicators.append(f'{count} non-draft invoices')
except sqlite3.OperationalError:
    pass
try:
    count = conn.execute('SELECT COUNT(*) FROM people WHERE active = 1').fetchone()[0]
    if count > 0:
        indicators.append(f'{count} active people')
except sqlite3.OperationalError:
    pass
try:
    count = conn.execute('SELECT COUNT(*) FROM raw_calendar_snapshots').fetchone()[0]
    if count > 100:
        indicators.append(f'{count} raw snapshots')
except sqlite3.OperationalError:
    pass
conn.close()
print('; '.join(indicators) if indicators else '')
")

if [[ -n "$PROD_INDICATORS" ]]; then
  echo "REFUSING to reset: probable production data detected."
  echo "  $PROD_INDICATORS"
  echo ""
  echo "This reset command is test-only. To override, manually delete the file:"
  echo "  rm '$DB_PATH'"
  exit 1
fi

# --- Require explicit confirmation ---
echo "WARNING: This will permanently delete: $DB_PATH"
echo "A backup will be created first."
echo ""
read -r -p "Type RESET to confirm: " CONFIRMATION

if [[ "$CONFIRMATION" != "RESET" ]]; then
  echo "Reset cancelled."
  exit 1
fi

# --- Back up first ---
BACKUP_DIR="$PROJECT_DIR/data/backups"
mkdir -p "$BACKUP_DIR"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_PATH="$BACKUP_DIR/$(basename "${DB_PATH%.sqlite3}").backup-reset-${TIMESTAMP}.sqlite3"
cp "$DB_PATH" "$BACKUP_PATH"
echo "Backup created: $BACKUP_PATH"

# --- Delete and reinitialize ---
rm "$DB_PATH"
PYTHONPATH=app python3 -c "
import sys
sys.path.insert(0, 'app')
from jordana_invoice.db import migrate_database
result = migrate_database('$DB_PATH')
print('Database recreated.' if result['migrated'] else 'Schema already current.')
"

echo "Reset complete. Fresh database at: $DB_PATH"
