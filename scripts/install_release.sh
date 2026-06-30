#!/usr/bin/env bash
#
# Offline production installer for a versioned Jordana Billing release bundle.
#
set -euo pipefail

RELEASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="Jordana Billing.app"
SOURCE_APP="$RELEASE_DIR/$APP_NAME"
WHEELHOUSE="$RELEASE_DIR/wheelhouse"
MANIFEST="$RELEASE_DIR/release_manifest.json"
APP_DEST="${JORDANA_INSTALL_APP_DEST:-$HOME/Applications/$APP_NAME}"
APP_SUPPORT_DIR="${JORDANA_APP_SUPPORT_DIR:-$HOME/Library/Application Support/Jordana Billing}"
CONFIG_DEST="$APP_SUPPORT_DIR/config/.env"
DB_DEST="$APP_SUPPORT_DIR/data/jordana_invoice.sqlite3"
CONFIG_SOURCE=""
DB_SOURCE=""
INIT_EMPTY_DB=0
YES=0

usage() {
  cat <<'EOF'
Usage: scripts/install_release.sh [options]

Options:
  --config PATH       Copy private .env to Application Support when missing.
  --database PATH     Copy an existing SQLite database when missing.
  --init-empty-db     Explicitly initialize a new empty test database when no DB exists.
  --app-dest PATH     Install app bundle at PATH (default: ~/Applications/Jordana Billing.app).
  --support-dir PATH  Use alternate private data directory for testing.
  --yes               Do not prompt for --init-empty-db confirmation.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG_SOURCE="$2"; shift 2 ;;
    --database) DB_SOURCE="$2"; shift 2 ;;
    --init-empty-db) INIT_EMPTY_DB=1; shift ;;
    --app-dest) APP_DEST="$2"; shift 2 ;;
    --support-dir) APP_SUPPORT_DIR="$2"; CONFIG_DEST="$APP_SUPPORT_DIR/config/.env"; DB_DEST="$APP_SUPPORT_DIR/data/jordana_invoice.sqlite3"; shift 2 ;;
    --yes) YES=1; shift ;;
    --help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

fail() {
  echo "ERROR: $1" >&2
  exit 1
}

[[ "$(uname -s)" == "Darwin" ]] || fail "This release installer supports macOS only."
[[ "$(uname -m)" == "arm64" ]] || fail "This V1 release is intended for Apple Silicon Macs."
[[ -d "$SOURCE_APP" ]] || fail "Release app bundle is missing."
[[ -d "$WHEELHOUSE" ]] || fail "Release wheelhouse is missing."
[[ -f "$MANIFEST" ]] || fail "Release manifest is missing."

PYTHON_BIN="${JORDANA_INSTALL_PYTHON:-$(command -v python3 || true)}"
[[ -n "$PYTHON_BIN" && -x "$PYTHON_BIN" ]] || fail "Python is required for this V1 offline runtime install."
"$PYTHON_BIN" - "$MANIFEST" <<'PY' || fail "Installed Python does not match the release wheelhouse runtime."
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
required = manifest.get("runtime", {}).get("requires_python", "")
major_minor = ".".join(str(part) for part in sys.version_info[:2])
if not required.startswith(major_minor + "."):
    raise SystemExit(
        f"release requires Python {required}, but installer is using Python {major_minor}.x"
    )
PY

mkdir -p "$(dirname "$APP_DEST")" "$APP_SUPPORT_DIR/config" "$APP_SUPPORT_DIR/data" "$APP_SUPPORT_DIR/backups" "$APP_SUPPORT_DIR/logs" "$APP_SUPPORT_DIR/runtime" "$APP_SUPPORT_DIR/Reports"

if [[ -n "$CONFIG_SOURCE" ]]; then
  [[ -f "$CONFIG_SOURCE" ]] || fail "Private config source does not exist."
  if [[ -f "$CONFIG_DEST" ]]; then
    echo "Preserved existing private configuration: $CONFIG_DEST"
  else
    cp "$CONFIG_SOURCE" "$CONFIG_DEST"
    chmod 600 "$CONFIG_DEST"
    echo "Installed private configuration: $CONFIG_DEST"
  fi
elif [[ ! -f "$CONFIG_DEST" ]]; then
  fail "Private configuration is missing. Re-run with --config PATH after Brooke supplies .env."
fi

if [[ -n "$DB_SOURCE" ]]; then
  [[ -f "$DB_SOURCE" ]] || fail "Database source does not exist."
  if [[ -f "$DB_DEST" ]]; then
    echo "Preserved existing database: $DB_DEST"
  else
    cp "$DB_SOURCE" "$DB_DEST"
    chmod 600 "$DB_DEST"
    echo "Installed existing database: $DB_DEST"
  fi
elif [[ ! -f "$DB_DEST" && "$INIT_EMPTY_DB" -ne 1 ]]; then
  fail "Operational database is missing. Supply --database PATH or explicitly use --init-empty-db for a disposable first-time test install."
fi

if [[ "$INIT_EMPTY_DB" -eq 1 && ! -f "$DB_DEST" && "$YES" -ne 1 ]]; then
  printf 'Type INIT_EMPTY_DB to create a new empty test database at %s: ' "$DB_DEST"
  read -r confirmation
  [[ "$confirmation" == "INIT_EMPTY_DB" ]] || fail "Empty database initialization was not confirmed."
fi

TMP_APP="${APP_DEST}.installing"
rm -rf "$TMP_APP"
cp -R "$SOURCE_APP" "$TMP_APP"
rm -rf "$TMP_APP/Contents/Resources/runtime/venv"
mkdir -p "$TMP_APP/Contents/Resources/runtime"
"$PYTHON_BIN" -m venv "$TMP_APP/Contents/Resources/runtime/venv"
VENV_PYTHON="$TMP_APP/Contents/Resources/runtime/venv/bin/python"
"$VENV_PYTHON" -m pip install --no-index --find-links "$WHEELHOUSE" jordana-invoice==0.1.0 >/dev/null
"$VENV_PYTHON" -c 'import jordana_invoice, reportlab' >/dev/null
rm -rf "$APP_DEST"
mv "$TMP_APP" "$APP_DEST"
xattr -cr "$APP_DEST" 2>/dev/null || true
codesign --force --deep --sign - "$APP_DEST" >/dev/null 2>&1 || true

if [[ "$INIT_EMPTY_DB" -eq 1 && ! -f "$DB_DEST" ]]; then
  JORDANA_DATABASE_PATH="$DB_DEST" "$APP_DEST/Contents/Resources/runtime/venv/bin/python" -m jordana_invoice --db "$DB_DEST" init-db >/dev/null
  chmod 600 "$DB_DEST"
  echo "Initialized empty test database: $DB_DEST"
fi

"$RELEASE_DIR/scripts/verify_installation.sh" --app "$APP_DEST" --support-dir "$APP_SUPPORT_DIR"
echo "Jordana Billing release installed successfully."
