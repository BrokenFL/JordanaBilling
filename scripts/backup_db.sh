#!/usr/bin/env bash
#
# Backup the Jordana Billing SQLite database with a verified manifest.
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

DB_PATH="${1:-$PROJECT_DIR/data/jordana_invoice.sqlite3}"
export DB_PATH

if [[ ! -f "$DB_PATH" ]]; then
  echo "ERROR: Database not found at $DB_PATH" >&2
  exit 1
fi

PYTHONPATH=app "${PYTHON:-python3}" <<'PY'
import os
from pathlib import Path

from jordana_invoice.backups import create_verified_backup

db_path = Path(os.environ["DB_PATH"])
result = create_verified_backup(db_path, reason="manual_script_backup", protected=True)
print(f"Backup created: {result.backup_path}")
print(f"Manifest: {result.manifest_path}")
print(f"Integrity: {result.integrity_status}")
print(f"SHA-256: {result.sha256}")
print(f"Secondary: {result.secondary_status}")
if result.secondary_path:
    print(f"Secondary backup: {result.secondary_path}")
PY
