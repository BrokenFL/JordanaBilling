#!/usr/bin/env bash
#
# Build the "Jordana Billing.app" macOS launcher bundle.
# The app is a thin wrapper that runs bootstrap.sh on first launch
# and start_jordana.sh on subsequent launches.
#
# The .app bundle lives at the project root and is gitignored.
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

APP_NAME="Jordana Billing"
APP_DIR="$PROJECT_DIR/${APP_NAME}.app"
CONTENTS_DIR="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"

# Remove old bundle if present
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

# Determine if this is a first run (no .venv or no database)
FIRST_RUN=0
if [[ ! -d "$PROJECT_DIR/.venv" ]] || [[ ! -f "$PROJECT_DIR/data/jordana_invoice.sqlite3" ]]; then
  FIRST_RUN=1
fi

if [[ "$FIRST_RUN" -eq 1 ]]; then
  bash "$PROJECT_DIR/scripts/bootstrap.sh"
else
  bash "$PROJECT_DIR/scripts/start_jordana.sh"
fi
LAUNCHER

chmod +x "$MACOS_DIR/launcher"

# --- Create a simple placeholder icon (1x1 transparent PNG) ---
# A real icon can be added later as Resources/AppIcon.icns
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

echo "Built: $APP_DIR"
echo "Double-click to launch."
