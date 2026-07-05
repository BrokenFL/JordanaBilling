#!/usr/bin/env bash
#
# Build a versioned offline-installable release directory and DMG for clean-Mac testing.
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

BUILD_PYTHON="${JORDANA_RELEASE_PYTHON:-$(command -v python3)}"
RELEASE_LABEL="${JORDANA_RELEASE_LABEL:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --release-label)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --release-label" >&2
        exit 2
      fi
      RELEASE_LABEL="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done
if [[ -n "$RELEASE_LABEL" && ! "$RELEASE_LABEL" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
  echo "Unsafe release label: $RELEASE_LABEL" >&2
  exit 2
fi
VERSION="$("$BUILD_PYTHON" - <<'PY'
import tomllib
print(tomllib.loads(open("pyproject.toml", "rb").read().decode())["project"]["version"])
PY
)"
BUNDLE_VERSION_INFO="$("$BUILD_PYTHON" - "$VERSION" <<'PY'
import re
import sys

version = sys.argv[1]
match = re.fullmatch(r"(\d+\.\d+\.\d+)(?:\.post(\d+))?", version)
if not match:
    raise SystemExit(f"Unsupported release version for bundle metadata: {version}")
short_version = match.group(1)
build_version = match.group(2) or "1"
print(f"{short_version}\t{build_version}")
PY
)"
IFS=$'\t' read -r BUNDLE_SHORT_VERSION BUNDLE_BUILD_VERSION <<< "$BUNDLE_VERSION_INFO"
COMMIT="$(git rev-parse --short=12 HEAD)"
FULL_COMMIT="$(git rev-parse HEAD)"
SOURCE_TREE_DIRTY=false
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  SOURCE_TREE_DIRTY=true
fi
ARTIFACT_VERSION="${RELEASE_LABEL:-$VERSION}"
BUILD_ROOT="$PROJECT_DIR/build/release"
RELEASE_NAME="JordanaBilling-${ARTIFACT_VERSION}-${COMMIT}-macos-arm64"
RELEASE_DIR="$BUILD_ROOT/$RELEASE_NAME"
DMG_PATH="$BUILD_ROOT/$RELEASE_NAME.dmg"
DMG_ROOT="$BUILD_ROOT/$RELEASE_NAME-dmg"
SETUP_APP="$BUILD_ROOT/Install Jordana Billing.app"
LAUNCHER_APP="$BUILD_ROOT/Jordana Billing.app"
PAYLOAD_DIR="$SETUP_APP/Contents/Resources/ReleasePayload"
WHEELHOUSE="$RELEASE_DIR/wheelhouse"
BUILD_SRC="$BUILD_ROOT/$RELEASE_NAME-source"
BUILD_ID="${RELEASE_LABEL:-v$VERSION}-${COMMIT}"

clean_and_sign_app() {
  local app_path="$1"
  xattr -cr "$app_path" 2>/dev/null || true
  xattr -dr com.apple.FinderInfo "$app_path" 2>/dev/null || true
  xattr -dr com.apple.fileprovider.fpfs#P "$app_path" 2>/dev/null || true
  rm -rf "$app_path/Contents/_CodeSignature"
  dot_clean -m "$app_path" 2>/dev/null || true
  xattr -c "$app_path" 2>/dev/null || true
  xattr -dr com.apple.FinderInfo "$app_path" 2>/dev/null || true
  xattr -dr com.apple.fileprovider.fpfs#P "$app_path" 2>/dev/null || true
  xattr -dr com.apple.provenance "$app_path" 2>/dev/null || true
  xattr -dr com.apple.quarantine "$app_path" 2>/dev/null || true
  codesign --force --deep --sign - --timestamp=none "$app_path" >/dev/null 2>&1 || true
  xattr -cr "$app_path" 2>/dev/null || true
  xattr -dr com.apple.FinderInfo "$app_path" 2>/dev/null || true
  xattr -dr com.apple.fileprovider.fpfs#P "$app_path" 2>/dev/null || true
  xattr -dr com.apple.provenance "$app_path" 2>/dev/null || true
  xattr -c "$app_path" 2>/dev/null || true
  codesign --verify --deep --strict "$app_path"
}

prepare_setup_app() {
  local app_path="$1"
  xattr -cr "$app_path" 2>/dev/null || true
  xattr -dr com.apple.FinderInfo "$app_path" 2>/dev/null || true
  xattr -dr com.apple.fileprovider.fpfs#P "$app_path" 2>/dev/null || true
  xattr -dr com.apple.provenance "$app_path" 2>/dev/null || true
  xattr -c "$app_path" 2>/dev/null || true
  rm -rf "$app_path/Contents/_CodeSignature"
  dot_clean -m "$app_path" 2>/dev/null || true
  xattr -cr "$app_path" 2>/dev/null || true
  xattr -dr com.apple.FinderInfo "$app_path" 2>/dev/null || true
  xattr -dr com.apple.fileprovider.fpfs#P "$app_path" 2>/dev/null || true
  xattr -dr com.apple.provenance "$app_path" 2>/dev/null || true
  xattr -dr com.apple.quarantine "$app_path" 2>/dev/null || true
  xattr -c "$app_path" 2>/dev/null || true
}

rm -rf "$RELEASE_DIR" "$DMG_ROOT" "$DMG_PATH" "$DMG_PATH.sha256" "$BUILD_SRC" "$LAUNCHER_APP"
rm -rf "$PROJECT_DIR/build/lib" "$PROJECT_DIR/build/bdist."* "$PROJECT_DIR/build/temp."*
mkdir -p "$WHEELHOUSE" "$RELEASE_DIR/scripts" "$RELEASE_DIR/docs" "$RELEASE_DIR/config"

JORDANA_LAUNCHER_APP_DIR="$LAUNCHER_APP" JORDANA_BUNDLE_SHORT_VERSION="$BUNDLE_SHORT_VERSION" JORDANA_BUNDLE_BUILD_VERSION="$BUNDLE_BUILD_VERSION" "$PROJECT_DIR/scripts/build_launcher.sh" --force >/dev/null
JORDANA_BUNDLE_SHORT_VERSION="$BUNDLE_SHORT_VERSION" JORDANA_BUNDLE_BUILD_VERSION="$BUNDLE_BUILD_VERSION" "$PROJECT_DIR/scripts/build_setup_wizard.sh" "$SETUP_APP" >/dev/null
ditto --norsrc "$LAUNCHER_APP" "$RELEASE_DIR/Jordana Billing.app"
cp "$PROJECT_DIR/scripts/install_release.sh" "$RELEASE_DIR/scripts/install_release.sh"
cp "$PROJECT_DIR/scripts/create_private_config.sh" "$RELEASE_DIR/scripts/create_private_config.sh"
cp "$PROJECT_DIR/scripts/launch_installed_app.sh" "$RELEASE_DIR/scripts/launch_installed_app.sh"
cp "$PROJECT_DIR/scripts/sign_and_notarize_release.sh" "$RELEASE_DIR/scripts/sign_and_notarize_release.sh"
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
mkdir -p "$BUILD_SRC"
git archive HEAD | tar -x -C "$BUILD_SRC"
"$BUILD_PYTHON" - "$BUILD_SRC/app/jordana_invoice/build_info.py" "$VERSION" "$FULL_COMMIT" "$RELEASE_LABEL" "$BUILD_ID" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
version = sys.argv[2]
commit = sys.argv[3]
release_label = sys.argv[4]
build_id = sys.argv[5]
path.write_text(
    "from __future__ import annotations\n\n"
    "from importlib import metadata\n\n\n"
    'APPLICATION_NAME = "Jordana Billing"\n'
    f'GIT_COMMIT = "{commit}"\n'
    f'BUILD_ID = "{build_id}"\n'
    f'RELEASE_LABEL = "{release_label}"\n\n\n'
    "def package_version() -> str:\n"
    "    try:\n"
    '        return metadata.version("jordana-invoice")\n'
    "    except metadata.PackageNotFoundError:\n"
    f'        return "{version}"\n\n\n'
    "def current_build_info() -> dict[str, str]:\n"
    "    return {\n"
    '        "application": APPLICATION_NAME,\n'
    '        "package": "jordana-invoice",\n'
    '        "version": package_version(),\n'
    '        "git_commit": GIT_COMMIT,\n'
    '        "build_id": BUILD_ID,\n'
    '        "release_label": RELEASE_LABEL,\n'
    "    }\n",
    encoding="utf-8",
)
PY
"$BUILD_PYTHON" -m pip wheel --no-deps --wheel-dir "$WHEELHOUSE" "$BUILD_SRC"
APP_WHEEL_REL="$("$BUILD_PYTHON" - "$WHEELHOUSE" "$VERSION" <<'PY'
import sys
from pathlib import Path

wheelhouse = Path(sys.argv[1])
version = sys.argv[2]
matches = sorted(wheelhouse.glob(f"jordana_invoice-{version}-*.whl"))
if len(matches) != 1:
    raise SystemExit(f"Expected exactly one jordana_invoice wheel for {version}, found {len(matches)}")
print(f"wheelhouse/{matches[0].name}")
PY
)"
clean_and_sign_app "$RELEASE_DIR/Jordana Billing.app"

"$BUILD_PYTHON" - "$RELEASE_DIR" "$VERSION" "$FULL_COMMIT" "$RELEASE_LABEL" "$SOURCE_TREE_DIRTY" "$BUILD_ID" "$APP_WHEEL_REL" <<'PY'
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
release_label = sys.argv[4]
source_tree_dirty = sys.argv[5] == "true"
build_id = sys.argv[6]
app_wheel_rel = sys.argv[7]
checksums = {}
for path in sorted(p for p in release.rglob("*") if p.is_file()):
    rel = path.relative_to(release).as_posix()
    if rel in {"release_manifest.json", "SHA256SUMS"}:
        continue
    checksums[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
manifest = {
    "application": "Jordana Billing",
    "version": version,
    "application_version": version,
    "build_id": build_id,
    "git_commit": commit,
    "source_tree_dirty": source_tree_dirty,
    "build_timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "supported_platform": "macOS",
    "supported_architecture": "arm64",
    "runtime": {
        "strategy": "offline private virtualenv created during one-time install",
        "requires_python": f"{platform.python_version_tuple()[0]}.{platform.python_version_tuple()[1]}.x",
        "builder_python": platform.python_version(),
    },
    "package": {
        "name": "jordana-invoice",
        "version": version,
        "wheel": app_wheel_rel,
    },
    "artifact": {
        "type": "dmg",
        "contains_private_data": False,
    },
    "checksums": checksums,
}
if release_label:
    manifest["release_label"] = release_label
(release / "release_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
with (release / "SHA256SUMS").open("w", encoding="utf-8") as fh:
    for rel, digest in checksums.items():
        fh.write(f"{digest}  {rel}\n")
PY
rm -rf "$BUILD_SRC"

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
prepare_setup_app "$SETUP_APP"
ditto --norsrc "$SETUP_APP" "$DMG_ROOT/Install Jordana Billing.app"
prepare_setup_app "$DMG_ROOT/Install Jordana Billing.app"
cat > "$DMG_ROOT/README.txt" <<EOF
Jordana Billing test release

Release label: ${RELEASE_LABEL:-$VERSION}
Application version: $VERSION

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
