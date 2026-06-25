#!/usr/bin/env bash
#
# Build the "Jordana Billing.app" macOS launcher bundle.
# The app is a thin wrapper that runs bootstrap.sh on first launch
# and start_jordana.sh on subsequent launches.
#
# The .app bundle lives at the project root and is committed to Git.
# Use --force to rebuild an existing bundle.
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

APP_NAME="Jordana Billing"
APP_DIR="$PROJECT_DIR/${APP_NAME}.app"
CONTENTS_DIR="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"

# Preserve existing bundle unless --force
if [[ -d "$APP_DIR" && "${1:-}" != "--force" ]]; then
  echo "Jordana Billing.app already exists. Use --force to rebuild."
  exit 0
fi

rm -rf "$APP_DIR"

# Create bundle structure
mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"

# --- Info.plist ---
cat > "$CONTENTS_DIR/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>Jordana Billing</string>
  <key>CFBundleDisplayName</key>
  <string>Jordana Billing</string>
  <key>CFBundleIdentifier</key>
  <string>com.jordana.billing.launcher</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleExecutable</key>
  <string>launcher</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>LSUIElement</key>
  <false/>
  <key>NSDocumentsFolderUsageDescription</key>
  <string>Jordana Billing needs access to the Documents folder to run setup scripts, read configuration, and manage the local database.</string>
</dict>
</plist>
PLIST

# --- Launcher executable (shell script) ---
cat > "$MACOS_DIR/launcher" <<'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail

# Resolve project directory from the .app bundle location.
# The .app lives at the project root, so project dir is two levels up.
BUNDLE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_DIR="$(cd "$BUNDLE_DIR/../.." && pwd)"

DB_PATH="$PROJECT_DIR/data/jordana_invoice.sqlite3"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/launcher.log"

log() {
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $1" >> "$LOG_FILE"
}

# Detect existing database (may be present in a reused checkout even when
# Git is clean, because *.sqlite3 is gitignored).
DB_EXISTS=0
if [[ -f "$DB_PATH" ]]; then
  DB_EXISTS=1
  log "Existing database found at $DB_PATH — will be preserved."
fi

# Determine if bootstrap is needed (first run: no .venv or no database)
FIRST_RUN=0
if [[ ! -d "$PROJECT_DIR/.venv" ]] || [[ ! -f "$DB_PATH" ]]; then
  FIRST_RUN=1
fi

if [[ "$FIRST_RUN" -eq 1 ]]; then
  if [[ "$DB_EXISTS" -eq 1 ]]; then
    log "Existing database detected — setup will preserve it (not a clean install)."
  else
    log "No existing database — fresh installation."
  fi
  /usr/bin/osascript -e "tell application \"Terminal\" to do script \"bash \\\"$PROJECT_DIR/scripts/bootstrap.sh\\\"\"" 2>> "$LOG_FILE" || {
    log "bootstrap.sh failed with exit $?"
    exit 1
  }
else
  /usr/bin/osascript -e "tell application \"Terminal\" to do script \"bash \\\"$PROJECT_DIR/scripts/start_jordana.sh\\\"\"" 2>> "$LOG_FILE" || {
    log "start_jordana.sh failed with exit $?"
    exit 1
  }
fi
LAUNCHER

chmod +x "$MACOS_DIR/launcher"

# --- Create a simple placeholder icon (1x1 transparent PNG) ---
# A real icon can be added later as Resources/AppIcon.icns
# The placeholder PNG is safe to commit (no private data).
python3 -c "
import struct, zlib
def create_png(path):
    # 1x1 transparent PNG
    data = b'\\x89PNG\\r\\n\\x1a\\n'
    ihdr = struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0)
    ihdr_crc = zlib.crc32(b'IHDR' + ihdr)
    data += struct.pack('>I', 13) + b'IHDR' + ihdr + struct.pack('>I', ihdr_crc & 0xffffffff)
    raw = b'\\x00\\x00\\x00\\x00\\x00'
    compressed = zlib.compress(raw)
    idat_crc = zlib.crc32(b'IDAT' + compressed)
    data += struct.pack('>I', len(compressed)) + b'IDAT' + compressed + struct.pack('>I', idat_crc & 0xffffffff)
    iend_crc = zlib.crc32(b'IEND')
    data += struct.pack('>I', 0) + b'IEND' + struct.pack('>I', iend_crc & 0xffffffff)
    with open(path, 'wb') as f:
        f.write(data)
create_png('$RESOURCES_DIR/AppIcon.png')
"

# --- Ad-hoc code sign (required for open/LaunchServices) ---
# Without at least ad-hoc signing, macOS `open` may silently refuse to
# launch the bundle.  This does not embed a Developer ID; it only satisfies
# the local Gatekeeper requirement for unsigned bundles.
# Strip extended attributes first (FinderInfo, provenance, etc.) which
# would prevent codesign from sealing the bundle.
xattr -cr "$APP_DIR" 2>/dev/null || true
codesign --force --deep --sign - "$APP_DIR" 2>/dev/null || true

echo "Built: $APP_DIR"
echo "Double-click to launch."
