#!/usr/bin/env bash
#
# Build a reproducible offline release directory and zip for clean-Mac testing.
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
ZIP_PATH="$BUILD_ROOT/$RELEASE_NAME.zip"
WHEELHOUSE="$RELEASE_DIR/wheelhouse"

rm -rf "$RELEASE_DIR" "$ZIP_PATH"
mkdir -p "$WHEELHOUSE" "$RELEASE_DIR/scripts"

"$PROJECT_DIR/scripts/build_launcher.sh" --force >/dev/null
cp -R "$PROJECT_DIR/Jordana Billing.app" "$RELEASE_DIR/Jordana Billing.app"
cp "$PROJECT_DIR/scripts/install_release.sh" "$RELEASE_DIR/scripts/install_release.sh"
cp "$PROJECT_DIR/scripts/update_release.sh" "$RELEASE_DIR/scripts/update_release.sh"
cp "$PROJECT_DIR/scripts/verify_installation.sh" "$RELEASE_DIR/scripts/verify_installation.sh"
cp "$PROJECT_DIR/requirements-production.lock" "$RELEASE_DIR/requirements-production.lock"
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
        "type": "zip",
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

(cd "$BUILD_ROOT" && zip -qr "$ZIP_PATH" "$RELEASE_NAME")
shasum -a 256 "$ZIP_PATH" > "$ZIP_PATH.sha256"
echo "$ZIP_PATH"
