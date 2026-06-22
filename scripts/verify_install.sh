#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

[[ -d .venv ]] || { echo "Missing .venv" >&2; exit 1; }
[[ -f .env ]] || { echo "Missing .env" >&2; exit 1; }
[[ -d backups ]] || mkdir -p backups
[[ -w backups ]] || { echo "backups is not writable" >&2; exit 1; }

. .venv/bin/activate
python - <<'PY'
import importlib.util, sqlite3, sys
from pathlib import Path

if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10 or newer is required.")
db_path = Path("data/jordana_invoice.sqlite3")
if not db_path.exists():
    raise SystemExit("Missing data/jordana_invoice.sqlite3")
conn = sqlite3.connect(db_path)
required = {
    "import_runs", "raw_calendar_snapshots", "calendar_event_candidates",
    "people", "client_accounts", "account_members", "billing_parties",
    "calendar_aliases", "rate_rules", "sessions", "review_items",
    "audit_log", "sync_state",
}
existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
missing = sorted(required - existing)
if missing:
    raise SystemExit("Missing tables: " + ", ".join(missing))
integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
if integrity != "ok":
    raise SystemExit("SQLite integrity check failed: " + integrity)
for table in sorted(required):
    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(f"{table}: {count}")
print("Install verification passed.")
PY

if git ls-files | grep -E '(\.env$|\.sqlite3($|-)|Reports/|logs/|credentials/)' >/tmp/jordana_verify_tracked.txt; then
  echo "Private files are tracked by Git:" >&2
  cat /tmp/jordana_verify_tracked.txt >&2
  exit 1
fi
