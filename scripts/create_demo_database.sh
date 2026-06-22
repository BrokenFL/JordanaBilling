#!/usr/bin/env bash
set -euo pipefail

DB_PATH="${1:-data/demo/jordana_demo.sqlite3}"
FIXTURE="data/samples/sanitized_demo_calendar_snapshots.csv"

case "$DB_PATH" in
  data/demo/*.sqlite3) ;;
  *)
    echo "DEMO database creation refused: path must be under data/demo/*.sqlite3" >&2
    exit 1
    ;;
esac

if [[ "$DB_PATH" == "data/jordana_invoice.sqlite3" || "$DB_PATH" == *"/jordana_invoice.sqlite3" ]]; then
  echo "DEMO database creation refused: will not touch the default application database" >&2
  exit 1
fi

mkdir -p "$(dirname "$DB_PATH")"
rm -f "$DB_PATH" "$DB_PATH-shm" "$DB_PATH-wal"

PYTHONPATH=app python3 - <<'PY' "$DB_PATH" "$FIXTURE"
import sys
from pathlib import Path

from jordana_invoice.calendar_preferences import upsert_calendar_preference
from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_csv
from jordana_invoice.util import now_iso

db_path = Path(sys.argv[1])
fixture = Path(sys.argv[2])
print(f"DEMO: creating sanitized database at {db_path}")
conn = connect(db_path)
init_db(conn)
upsert_calendar_preference(conn, "Personal", "usually_personal_admin", source="demo")
upsert_calendar_preference(conn, "Family", "review_normally", source="demo")
upsert_calendar_preference(conn, "Uncategorized", "hidden", source="demo")
conn.execute(
    """
    INSERT INTO app_metadata (metadata_key, metadata_value, updated_at)
    VALUES ('demo_mode', 'true', ?)
    ON CONFLICT(metadata_key) DO UPDATE SET metadata_value = excluded.metadata_value, updated_at = excluded.updated_at
    """,
    (now_iso(),),
)
conn.commit()
run_id = import_csv(conn, fixture, source_name="SANITIZED_DEMO")
integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
if integrity != "ok":
    raise SystemExit(f"DEMO integrity check failed: {integrity}")
raw_count = conn.execute("SELECT COUNT(*) FROM raw_calendar_snapshots").fetchone()[0]
candidate_count = conn.execute("SELECT COUNT(*) FROM calendar_event_candidates").fetchone()[0]
session_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
print(f"DEMO: import_run_id={run_id}")
print(f"DEMO: raw_snapshots={raw_count} candidates={candidate_count} sessions={session_count}")
print("DEMO: SQLite integrity_check=ok")
print(f"DEMO: launch review UI with: PYTHONPATH=app python3 -m jordana_invoice --db {db_path} serve-review")
PY
