#!/usr/bin/env bash
#
# Verify an installed production Jordana Billing app without exposing secrets.
#
set -euo pipefail

APP_PATH="${JORDANA_INSTALL_APP_DEST:-$HOME/Applications/Jordana Billing.app}"
APP_SUPPORT_DIR="${JORDANA_APP_SUPPORT_DIR:-$HOME/Library/Application Support/Jordana Billing}"
DOCUMENTS_ROOT="${JORDANA_DOCUMENTS_ROOT:-$HOME/Documents/Jordana Billing}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app) APP_PATH="$2"; shift 2 ;;
    --support-dir) APP_SUPPORT_DIR="$2"; shift 2 ;;
    --help) echo "Usage: scripts/verify_installation.sh [--app PATH] [--support-dir PATH]"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

VENV_PYTHON="$APP_PATH/Contents/Resources/runtime/venv/bin/python"
CONFIG_FILE="$APP_SUPPORT_DIR/config/.env"
DB_PATH="$APP_SUPPORT_DIR/data/jordana_invoice.sqlite3"
REPORTS_DIR="$DOCUMENTS_ROOT/Session Lists"
CLIENT_FILES_DIR="$DOCUMENTS_ROOT/Client Files"

[[ -d "$APP_PATH" ]] || { echo "Missing app bundle" >&2; exit 1; }
[[ -x "$APP_PATH/Contents/MacOS/launcher" ]] || { echo "Missing app launcher" >&2; exit 1; }
[[ -x "$VENV_PYTHON" ]] || { echo "Missing installed Python runtime" >&2; exit 1; }
[[ -f "$CONFIG_FILE" ]] || { echo "Missing private configuration" >&2; exit 1; }
[[ -f "$DB_PATH" ]] || { echo "Missing private database" >&2; exit 1; }
[[ -d "$REPORTS_DIR" && -w "$REPORTS_DIR" ]] || { echo "Session Lists folder is missing or not writable" >&2; exit 1; }
[[ -d "$CLIENT_FILES_DIR" && -w "$CLIENT_FILES_DIR" ]] || { echo "Client Files folder is missing or not writable" >&2; exit 1; }

"$VENV_PYTHON" - <<PY
import importlib.resources
import sqlite3
from pathlib import Path

import jordana_invoice
import pypdf
import reportlab
from PIL import Image

db_path = Path(r"$DB_PATH")
conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
try:
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
finally:
    conn.close()
if integrity != "ok":
    raise SystemExit("SQLite integrity check failed")
static_root = importlib.resources.files("jordana_invoice") / "static"
for name in ("review.html", "review.js", "review.css", "js/api.js", "js/overlay_manager.js"):
    if not (static_root / name).is_file():
        raise SystemExit(f"Missing static asset: {name}")
print("Installed app verification passed.")
PY
