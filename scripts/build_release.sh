#!/usr/bin/env bash
#
# Build a versioned offline-installable release directory and DMG for clean-Mac testing.
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

BUILD_PYTHON="${JORDANA_RELEASE_PYTHON:-$(command -v python3)}"
VERSION="$("$BUILD_PYTHON" - <<'PY'
import tomllib
print(tomllib.loads(open("pyproject.toml", "rb").read().decode())["project"]["version"])
PY
)"
COMMIT="$(git rev-parse --short=12 HEAD)"
BUILD_ROOT="$PROJECT_DIR/build/release"
RELEASE_NAME="JordanaBilling-${VERSION}-${COMMIT}-macos-arm64"
RELEASE_DIR="$BUILD_ROOT/$RELEASE_NAME"
DMG_PATH="$BUILD_ROOT/$RELEASE_NAME.dmg"
DMG_ROOT="$BUILD_ROOT/$RELEASE_NAME-dmg"
SETUP_APP="$BUILD_ROOT/Install Jordana Billing.app"
PAYLOAD_DIR="$SETUP_APP/Contents/Resources/ReleasePayload"
WHEELHOUSE="$RELEASE_DIR/wheelhouse"

clean_and_sign_app() {
  local app_path="$1"
  xattr -cr "$app_path" 2>/dev/null || true
  xattr -dr com.apple.FinderInfo "$app_path" 2>/dev/null || true
  xattr -dr com.apple.fileprovider.fpfs#P "$app_path" 2>/dev/null || true
  rm -rf "$app_path/Contents/_CodeSignature"
  dot_clean -m "$app_path" 2>/dev/null || true
  xattr -c "$app_path" 2>/dev/null || true
  codesign --force --deep --sign - --timestamp=none "$app_path" >/dev/null 2>&1 || true
  xattr -cr "$app_path" 2>/dev/null || true
  xattr -dr com.apple.FinderInfo "$app_path" 2>/dev/null || true
  xattr -dr com.apple.fileprovider.fpfs#P "$app_path" 2>/dev/null || true
  xattr -dr com.apple.provenance "$app_path" 2>/dev/null || true
  xattr -c "$app_path" 2>/dev/null || true
  codesign --verify --deep --strict "$app_path"
}

rm -rf "$RELEASE_DIR" "$DMG_ROOT" "$DMG_PATH" "$DMG_PATH.sha256"
mkdir -p "$WHEELHOUSE" "$RELEASE_DIR/scripts" "$RELEASE_DIR/docs" "$RELEASE_DIR/config"

"$PROJECT_DIR/scripts/build_launcher.sh" --force >/dev/null
"$PROJECT_DIR/scripts/build_setup_wizard.sh" "$SETUP_APP" >/dev/null
ditto --norsrc "$PROJECT_DIR/Jordana Billing.app" "$RELEASE_DIR/Jordana Billing.app"
cp "$PROJECT_DIR/scripts/install_release.sh" "$RELEASE_DIR/scripts/install_release.sh"
cp "$PROJECT_DIR/scripts/create_private_config.sh" "$RELEASE_DIR/scripts/create_private_config.sh"
cp "$PROJECT_DIR/scripts/launch_installed_app.sh" "$RELEASE_DIR/scripts/launch_installed_app.sh"
cp "$PROJECT_DIR/scripts/update_release.sh" "$RELEASE_DIR/scripts/update_release.sh"
cp "$PROJECT_DIR/scripts/verify_installation.sh" "$RELEASE_DIR/scripts/verify_installation.sh"
cp "$PROJECT_DIR/requirements-production.lock" "$RELEASE_DIR/requirements-production.lock"
cp "$PROJECT_DIR/config/example.env" "$RELEASE_DIR/config/example.env"
cp "$PROJECT_DIR/docs/PRODUCTION_PACKAGING.md" "$RELEASE_DIR/docs/PRODUCTION_PACKAGING.md"
cp "$PROJECT_DIR/docs/TEST_MAC_ACCEPTANCE.md" "$RELEASE_DIR/docs/TEST_MAC_ACCEPTANCE.md"
cp "$PROJECT_DIR/docs/FRESH_INSTALL.md" "$RELEASE_DIR/docs/FRESH_INSTALL.md"
cp "$PROJECT_DIR/docs/TEST_RELEASE_NOTES.md" "$RELEASE_DIR/docs/TEST_RELEASE_NOTES.md"
chmod +x "$RELEASE_DIR/scripts/"*.sh

"$BUILD_PYTHON" -m pip wheel --wheel-dir "$WHEELHOUSE" -r "$PROJECT_DIR/requirements-production.lock"
"$BUILD_PYTHON" -m pip wheel --no-deps --wheel-dir "$WHEELHOUSE" "$PROJECT_DIR"

"$BUILD_PYTHON" - "$RELEASE_DIR" "$VERSION" "$(git rev-parse HEAD)" <<'PY'
import hashlib
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

release = Path(sys.argv[1])
version = sys.argv[2]
commit = sys.argv[3]
checksums = {}
for path in sorted(p for p in release.rglob("*") if p.is_file()):
    rel = path.relative_to(release).as_posix()
    if rel in {"release_manifest.json", "SHA256SUMS"}:
        continue
    checksums[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
manifest = {
    "application": "Jordana Billing",
    "version": version,
    "git_commit": commit,
    "build_timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "supported_platform": "macOS",
    "supported_architecture": "arm64",
    "runtime": {
        "strategy": "offline private virtualenv created during one-time install",
        "requires_python": f"{platform.python_version_tuple()[0]}.{platform.python_version_tuple()[1]}.x",
        "builder_python": platform.python_version(),
    },
    "artifact": {
        "type": "dmg",
        "contains_private_data": False,
    },
    "checksums": checksums,
}
(release / "release_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
with (release / "SHA256SUMS").open("w", encoding="utf-8") as fh:
    for rel, digest in checksums.items():
        fh.write(f"{digest}  {rel}\n")
PY

python3 - "$RELEASE_DIR" <<'PY'
import sys
from pathlib import Path

release = Path(sys.argv[1])
for forbidden in (".env", "jordana_invoice.sqlite3", "Invoices", "Receipts", "Reports", "data/private"):
    matches = list(release.rglob(forbidden))
    if matches:
        raise SystemExit(f"Private artifact path found: {matches[0]}")
for path in release.rglob("*"):
    if path.is_file() and path.suffix in {".sqlite3", ".pdf"}:
        raise SystemExit(f"Forbidden private artifact file type: {path}")
PY

mkdir -p "$DMG_ROOT"
rm -rf "$PAYLOAD_DIR"
mv "$RELEASE_DIR" "$PAYLOAD_DIR"
clean_and_sign_app "$PAYLOAD_DIR/Jordana Billing.app"
clean_and_sign_app "$SETUP_APP"
ditto --norsrc "$SETUP_APP" "$DMG_ROOT/Install Jordana Billing.app"
clean_and_sign_app "$DMG_ROOT/Install Jordana Billing.app"
cat > "$DMG_ROOT/README.txt" <<EOF
Jordana Billing test release

Double-click "Install Jordana Billing.app" to install. The release payload is embedded inside the installer app.

This test release is not notarized. Gatekeeper may require right-click Open.
EOF

COPYFILE_DISABLE=1 hdiutil create -volname "Jordana Billing" -srcfolder "$DMG_ROOT" -ov -format UDZO "$DMG_PATH" >/dev/null
(cd "$BUILD_ROOT" && shasum -a 256 "$(basename "$DMG_PATH")" > "$(basename "$DMG_PATH").sha256")
python3 - "$DMG_PATH.sha256" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
target = text.split(maxsplit=1)[1].strip()
if target.startswith("/") or "/Users/" in text:
    raise SystemExit(f"Malformed checksum path: {text!r}")
PY
echo "$DMG_PATH"
