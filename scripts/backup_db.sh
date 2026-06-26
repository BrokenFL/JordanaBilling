#!/usr/bin/env bash
#
# Backup the Jordana Billing SQLite database.
# Creates a timestamped copy in data/backups/ and verifies integrity.
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

DB_PATH="${1:-$PROJECT_DIR/data/jordana_invoice.sqlite3}"

export JORDANA_BACKUP_DIR
BACKUP_DIR="$(python3 <<'PY'
import os

print(os.path.expanduser(os.environ.get("JORDANA_BACKUP_DIR", "~/.jordana_invoice/backups")))
PY
)"

mkdir -p "$BACKUP_DIR"

if [[ ! -f "$DB_PATH" ]]; then
  echo "ERROR: Database not found at $DB_PATH" >&2
  exit 1
fi

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_PATH="$BACKUP_DIR/$(basename "${DB_PATH%.sqlite3}").backup-${TIMESTAMP}.sqlite3"

export DB_PATH BACKUP_PATH

python3 <<'PY'
import os
import sqlite3

source_path = os.environ["DB_PATH"]
backup_path = os.environ["BACKUP_PATH"]

source = sqlite3.connect(source_path, timeout=5.0)
backup = sqlite3.connect(backup_path, timeout=5.0)
try:
    source.execute("PRAGMA busy_timeout = 5000")
    backup.execute("PRAGMA busy_timeout = 5000")
    source.backup(backup)
    backup.commit()
finally:
    backup.close()
    source.close()
PY

# Verify integrity
INTEGRITY="$(python3 <<'PY'
import os
import sqlite3

conn = sqlite3.connect(os.environ["BACKUP_PATH"])
try:
    print(conn.execute('PRAGMA integrity_check').fetchone()[0])
finally:
    conn.close()
PY
)"

if [[ "$INTEGRITY" != "ok" ]]; then
  echo "ERROR: Backup integrity check failed: $INTEGRITY" >&2
  rm -f "$BACKUP_PATH"
  exit 1
fi

echo "Backup created: $BACKUP_PATH"
echo "Integrity: $INTEGRITY"
