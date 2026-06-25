#!/usr/bin/env bash
#
# Backup the Jordana Billing SQLite database.
# Creates a timestamped copy in data/backups/ and verifies integrity.
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

DB_PATH="${1:-$PROJECT_DIR/data/jordana_invoice.sqlite3}"
BACKUP_DIR="$PROJECT_DIR/data/backups"
mkdir -p "$BACKUP_DIR"

if [[ ! -f "$DB_PATH" ]]; then
  echo "ERROR: Database not found at $DB_PATH" >&2
  exit 1
fi

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_PATH="$BACKUP_DIR/$(basename "${DB_PATH%.sqlite3}").backup-${TIMESTAMP}.sqlite3"

cp "$DB_PATH" "$BACKUP_PATH"

# Verify integrity
INTEGRITY="$(python3 -c "
import sqlite3
conn = sqlite3.connect('$BACKUP_PATH')
print(conn.execute('PRAGMA integrity_check').fetchone()[0])
conn.close()
")"

if [[ "$INTEGRITY" != "ok" ]]; then
  echo "ERROR: Backup integrity check failed: $INTEGRITY" >&2
  rm -f "$BACKUP_PATH"
  exit 1
fi

echo "Backup created: $BACKUP_PATH"
echo "Integrity: $INTEGRITY"
