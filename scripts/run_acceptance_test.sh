#!/usr/bin/env bash
#
# scripts/run_acceptance_test.sh
#
# Run the import-csv acceptance test against a TEMPORARY database.
#
# This script NEVER touches data/jordana_invoice.sqlite3 (the operational DB).
# Use it for CI verification, agent workflows, and the AGENTS.md Verification
# section in place of the legacy "rm -f data/jordana_invoice.sqlite3 && import-csv" pattern.
#
# Usage:
#   scripts/run_acceptance_test.sh
#   scripts/run_acceptance_test.sh data/samples/june_calendar_snapshots.csv
#   scripts/run_acceptance_test.sh data/samples/sanitized_demo_calendar_snapshots.csv
#
# The acceptance report is always written to data/acceptance_report.md.
# The temp database is deleted on exit (success or failure).
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

SAMPLE_CSV="${1:-data/samples/june_calendar_snapshots.csv}"
REPORT_PATH="data/acceptance_report.md"

# --- Choose Python binary ---
PYTHON_BIN="python3"
if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
fi

# --- Create a temp database that is clearly NOT the operational path ---
TMPDIR_PATH="$(mktemp -d)"
TEMP_DB="$TMPDIR_PATH/acceptance_test.sqlite3"

cleanup() {
  rm -rf "$TMPDIR_PATH"
}
trap cleanup EXIT

echo "Acceptance test: using temp database at $TEMP_DB"
echo "CSV: $SAMPLE_CSV"
echo "Report: $REPORT_PATH"
echo ""

# --- Run import-csv against the temp DB ---
# Note: the temp DB path contains the system temp-dir token so
# is_operational_db_path() will return False regardless of filename.
IMPORT_RUN_ID="$(
  PYTHONPATH=app "$PYTHON_BIN" -m jordana_invoice \
    --db "$TEMP_DB" \
    import-csv "$SAMPLE_CSV" \
    --report "$REPORT_PATH" \
    2>&1
)"

echo "import_run_id: $IMPORT_RUN_ID"
echo ""

# --- Quick report summary ---
if [[ -f "$REPORT_PATH" ]]; then
  echo "=== Acceptance Report (first 60 lines) ==="
  head -60 "$REPORT_PATH"
  echo "..."
else
  echo "WARNING: Report file was not created at $REPORT_PATH" >&2
  exit 1
fi

# --- Confirm operational database was not touched ---
OPERATIONAL_DB="$PROJECT_DIR/data/jordana_invoice.sqlite3"
if [[ -f "$OPERATIONAL_DB" ]]; then
  # If the file exists, check its mtime hasn't changed in the last 10 seconds
  if [[ "$(uname)" == "Darwin" ]]; then
    MTIME="$(stat -f "%m" "$OPERATIONAL_DB")"
  else
    MTIME="$(stat -c "%Y" "$OPERATIONAL_DB")"
  fi
  NOW="$(date +%s)"
  AGE=$(( NOW - MTIME ))
  if [[ "$AGE" -lt 10 ]]; then
    echo "" >&2
    echo "ERROR: The operational database at $OPERATIONAL_DB was modified" >&2
    echo "       during this acceptance test run. This should never happen." >&2
    exit 1
  fi
fi

echo ""
echo "Acceptance test complete. Temp database deleted. Operational DB untouched."
