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
DOCUMENTS_ROOT="${JORDANA_DOCUMENTS_ROOT:-$HOME/Documents/Jordana Billing}"
REPORTS_DIR="$DOCUMENTS_ROOT/Session Lists"
CLIENT_FILES_DIR="$DOCUMENTS_ROOT/Client Files"
CONFIG_DEST="$APP_SUPPORT_DIR/config/.env"
DB_DEST="$APP_SUPPORT_DIR/data/jordana_invoice.sqlite3"
CONFIG_SOURCE=""
DB_SOURCE=""
INIT_EMPTY_DB=0
YES=0
VERIFY_RUNNING_SERVER=1

usage() {
  cat <<'EOF'
Usage: scripts/install_release.sh [options]

Options:
  --config PATH       Copy private .env to Application Support when missing.
  --database PATH     Copy an existing SQLite database when missing.
  --init-empty-db     Explicitly initialize a new empty test database when no DB exists.
  --app-dest PATH     Install app bundle at PATH (default: ~/Applications/Jordana Billing.app).
  --support-dir PATH  Use alternate private data directory for testing.
  --skip-launch-verify  Test-only: do not launch the installed app after install.
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
    --skip-launch-verify) VERIFY_RUNNING_SERVER=0; shift ;;
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

manifest_info="$("$PYTHON_BIN" - "$MANIFEST" "__installer_manifest__" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
package = manifest.get("package") or {}
version = str(package.get("version") or manifest.get("application_version") or manifest.get("version") or "")
wheel = str(package.get("wheel") or "")
build_id = str(manifest.get("build_id") or "")
git_commit = str(manifest.get("git_commit") or "")
release_label = str(manifest.get("release_label") or "")
if not version:
    raise SystemExit("release manifest is missing package version")
if not wheel.startswith("wheelhouse/") or "/" in wheel[len("wheelhouse/"):] or ".." in Path(wheel).parts:
    raise SystemExit("release manifest is missing exact package wheel")
if not build_id:
    raise SystemExit("release manifest is missing build_id")
print("\t".join([version, wheel, build_id, git_commit, release_label]))
PY
)" || fail "Release manifest is missing exact install identity."
IFS=$'\t' read -r PACKAGE_VERSION APP_WHEEL_REL EXPECTED_BUILD_ID EXPECTED_GIT_COMMIT EXPECTED_RELEASE_LABEL <<< "$manifest_info"
APP_WHEEL="$RELEASE_DIR/$APP_WHEEL_REL"
[[ -f "$APP_WHEEL" ]] || fail "Exact application wheel from release manifest is missing."

mkdir -p "$(dirname "$APP_DEST")" "$APP_SUPPORT_DIR/config" "$APP_SUPPORT_DIR/data" "$APP_SUPPORT_DIR/backups" "$APP_SUPPORT_DIR/logs" "$APP_SUPPORT_DIR/runtime" "$REPORTS_DIR" "$CLIENT_FILES_DIR"

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
  fail "Private configuration is missing. Run scripts/create_private_config.sh, then run this installer again."
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
PREVIOUS_APP="${APP_DEST}.previous"
FAILED_APP="${APP_DEST}.failed-install"
PID_FILE="$APP_SUPPORT_DIR/runtime/review_server.pid"
METADATA_FILE="$APP_SUPPORT_DIR/runtime/review_server.meta"
PORT="${JORDANA_PORT:-8765}"
rm -rf "$TMP_APP"

is_numeric_pid() {
  [[ "${1:-}" =~ ^[0-9]+$ ]]
}

pid_is_running() {
  local pid="${1:-}"
  is_numeric_pid "$pid" && kill -0 "$pid" >/dev/null 2>&1
}

pid_command_line() {
  ps -p "$1" -o command= 2>/dev/null || true
}

pid_looks_like_jordana() {
  local command
  command="$(pid_command_line "$1")"
  [[ "$command" == *"jordana_invoice"* && "$command" == *"serve-review"* ]]
}

wait_for_pid_to_exit() {
  local pid="$1"
  local i
  for i in $(seq 1 20); do
    if ! pid_is_running "$pid"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

stop_existing_app_for_install() {
  local pid port_pid
  if [[ -f "$PID_FILE" ]]; then
    pid="$(tr -dc '0-9' < "$PID_FILE")"
    if pid_is_running "$pid"; then
      if pid_looks_like_jordana "$pid"; then
        echo "Stopping already-running Jordana Billing server before install."
        kill "$pid" >/dev/null 2>&1 || true
        wait_for_pid_to_exit "$pid" || fail "Jordana Billing is still running. Quit the app and run the installer again."
      else
        fail "A PID file exists, but it does not identify a Jordana Billing server. No process was stopped."
      fi
    fi
    rm -f "$PID_FILE" "$METADATA_FILE"
  fi
  if command -v lsof >/dev/null 2>&1; then
    port_pid="$(lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | head -n 1 || true)"
    if [[ -n "$port_pid" ]]; then
      if pid_looks_like_jordana "$port_pid"; then
        echo "Stopping Jordana Billing server on port $PORT before install."
        kill "$port_pid" >/dev/null 2>&1 || true
        wait_for_pid_to_exit "$port_pid" || fail "Jordana Billing on port $PORT is still running. Quit the app and run the installer again."
      else
        fail "Port $PORT is in use by another application. No process was stopped."
      fi
    fi
  fi
}

verify_release_payload_checksums() {
  "$PYTHON_BIN" - "$RELEASE_DIR" "$MANIFEST" <<'PY' || return 1
import hashlib
import json
import sys
from pathlib import Path

release = Path(sys.argv[1])
manifest = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
for rel, expected in sorted((manifest.get("checksums") or {}).items()):
    path = release / rel
    if not path.is_file():
        raise SystemExit(f"Missing release payload file: {rel}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"Release payload checksum mismatch: {rel}")
PY
}

verify_installed_app_manifest() {
  "$PYTHON_BIN" - "$APP_DEST" "$MANIFEST" <<'PY' || return 1
import hashlib
import json
import sys
from pathlib import Path

app = Path(sys.argv[1])
manifest = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
prefix = "Jordana Billing.app/"
for rel, expected in sorted((manifest.get("checksums") or {}).items()):
    if not rel.startswith(prefix):
        continue
    installed_rel = rel[len(prefix):]
    if installed_rel.startswith("Contents/Resources/runtime/"):
        continue
    path = app / installed_rel
    if not path.is_file():
        raise SystemExit(f"Missing installed app file: {installed_rel}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"Installed app checksum mismatch: {installed_rel}")
PY
}

verify_installed_package_identity() {
  "$VENV_PYTHON" - "$PACKAGE_VERSION" "$EXPECTED_BUILD_ID" "$EXPECTED_GIT_COMMIT" <<'PY' || return 1
import sys
from importlib import metadata

from jordana_invoice.build_info import current_build_info

expected_version, expected_build_id, expected_commit = sys.argv[1:4]
actual_version = metadata.version("jordana-invoice")
info = current_build_info()
if actual_version != expected_version or info.get("version") != expected_version:
    raise SystemExit("installed package version does not match release manifest")
if info.get("build_id") != expected_build_id:
    raise SystemExit("installed package build ID does not match release manifest")
if expected_commit and info.get("git_commit") != expected_commit:
    raise SystemExit("installed package git commit does not match release manifest")
PY
}

verify_running_server_build_id() {
  [[ "$VERIFY_RUNNING_SERVER" -eq 1 ]] || return 0
  bash "$APP_DEST/Contents/Resources/launch_installed_app.sh" >/dev/null 2>&1 || return 1
  "$VENV_PYTHON" - "$EXPECTED_BUILD_ID" "$PORT" <<'PY' || return 1
import json
import sys
import time
import urllib.request

expected_build_id = sys.argv[1]
port = int(sys.argv[2])
url = f"http://127.0.0.1:{port}/api/build-info"
last_error = None
for _ in range(30):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            data = json.loads(response.read().decode("utf-8"))
        if data.get("ok") is True and data.get("build_id") == expected_build_id:
            raise SystemExit(0)
        last_error = "running server build ID mismatch"
    except Exception as exc:
        last_error = str(exc)
    time.sleep(1)
raise SystemExit(last_error or "running server build ID could not be verified")
PY
}

recover_interrupted_install() {
  if [[ -e "$PREVIOUS_APP" ]]; then
    if [[ -e "$APP_DEST" ]]; then
      rm -rf "$PREVIOUS_APP"
    else
      mv "$PREVIOUS_APP" "$APP_DEST" || fail "Previous app could not be restored. Keep any Jordana Billing app bundles in ~/Applications for manual recovery."
    fi
  fi
}

cleanup_temp_app() {
  if [[ -e "$TMP_APP" && "$TMP_APP" != "$APP_DEST" ]]; then
    rm -rf "$TMP_APP"
  fi
}
trap cleanup_temp_app EXIT

rollback_replacement() {
  local message="$1"
  local failed_quarantined=0
  if [[ -e "$APP_DEST" ]]; then
    rm -rf "$FAILED_APP"
    if mv "$APP_DEST" "$FAILED_APP" 2>/dev/null; then
      failed_quarantined=1
    fi
  fi
  if [[ -e "$PREVIOUS_APP" ]]; then
    if mv "$PREVIOUS_APP" "$APP_DEST"; then
      rm -rf "$TMP_APP"
      if [[ "$failed_quarantined" -eq 1 ]]; then
        rm -rf "$FAILED_APP"
      fi
      fail "$message Previous app was restored."
    fi
    fail "$message Automatic restore failed. Keep Jordana Billing.app.previous and any failed installed app in ~/Applications for manual recovery."
  fi
  rm -rf "$APP_DEST" "$TMP_APP" "$FAILED_APP"
  fail "$message No previous app existed."
}

replace_app_bundle() {
  rm -rf "$PREVIOUS_APP" "$FAILED_APP"
  if [[ -e "$APP_DEST" ]]; then
    mv "$APP_DEST" "$PREVIOUS_APP" || fail "Existing app could not be prepared for replacement."
  fi
  if ! mv "$TMP_APP" "$APP_DEST"; then
    rollback_replacement "App replacement failed."
  fi
}

recover_interrupted_install
stop_existing_app_for_install
verify_release_payload_checksums || fail "Release payload checksums do not match the manifest."
ditto --norsrc "$SOURCE_APP" "$TMP_APP"
rm -rf "$TMP_APP/Contents/Resources/runtime/venv"
mkdir -p "$TMP_APP/Contents/Resources/runtime"
"$PYTHON_BIN" -m venv "$TMP_APP/Contents/Resources/runtime/venv"
VENV_PYTHON="$TMP_APP/Contents/Resources/runtime/venv/bin/python"
"$VENV_PYTHON" -m pip install --force-reinstall --no-index --find-links "$WHEELHOUSE" "$APP_WHEEL" >/dev/null
"$VENV_PYTHON" -c 'import jordana_invoice, pypdf, reportlab; from PIL import Image' >/dev/null
verify_installed_package_identity || fail "Staged package identity did not match the release manifest."
replace_app_bundle
VENV_PYTHON="$APP_DEST/Contents/Resources/runtime/venv/bin/python"
xattr -cr "$APP_DEST" 2>/dev/null || true
verify_installed_app_manifest || rollback_replacement "Installed app files did not match the release manifest."

if [[ "$INIT_EMPTY_DB" -eq 1 && ! -f "$DB_DEST" ]]; then
  JORDANA_DATABASE_PATH="$DB_DEST" "$APP_DEST/Contents/Resources/runtime/venv/bin/python" -m jordana_invoice --db "$DB_DEST" init-db >/dev/null || rollback_replacement "Database initialization failed."
  chmod 600 "$DB_DEST" || rollback_replacement "Database permissions could not be secured."
  echo "Initialized empty test database: $DB_DEST"
fi

bash "$RELEASE_DIR/scripts/verify_installation.sh" --app "$APP_DEST" --support-dir "$APP_SUPPORT_DIR" >/dev/null 2>&1 || rollback_replacement "Installation verification failed."
verify_running_server_build_id || rollback_replacement "Installed server build ID verification failed."
rm -rf "$PREVIOUS_APP" "$FAILED_APP"
echo "Jordana Billing release installed successfully."
