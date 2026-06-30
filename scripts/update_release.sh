#!/usr/bin/env bash
#
# Deliberate update entrypoint. It preserves private data and delegates to the
# offline release installer after creating a verified SQLite backup when a DB exists.
#
set -euo pipefail

APP_SUPPORT_DIR="${JORDANA_APP_SUPPORT_DIR:-$HOME/Library/Application Support/Jordana Billing}"
DB_PATH="$APP_SUPPORT_DIR/data/jordana_invoice.sqlite3"
BACKUP_DIR="$APP_SUPPORT_DIR/backups"
mkdir -p "$BACKUP_DIR"

if [[ -f "$DB_PATH" ]]; then
  python3 - "$DB_PATH" "$BACKUP_DIR" <<'PY'
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

source = Path(sys.argv[1]).expanduser()
backup_dir = Path(sys.argv[2]).expanduser()
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup = backup_dir / f"jordana_invoice.before-update.{stamp}.sqlite3"
src = sqlite3.connect(str(source))
try:
    dest = sqlite3.connect(str(backup))
    try:
        src.backup(dest)
        integrity = dest.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        dest.close()
finally:
    src.close()
if integrity != "ok":
    backup.unlink(missing_ok=True)
    raise SystemExit("Backup integrity_check failed")
print(f"Verified pre-update backup: {backup}")
PY
fi

"$(cd "$(dirname "$0")" && pwd)/install_release.sh" "$@"
