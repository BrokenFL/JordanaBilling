#!/usr/bin/env bash
#
# Build the "Jordana Billing.app" macOS launcher bundle.
#
# The app is a thin double-click wrapper around the installed production
# launcher in Contents/Resources. One-time setup is handled by the release
# installer; normal launch only validates and starts the installed app.
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

APP_NAME="Jordana Billing"
APP_DIR="$PROJECT_DIR/${APP_NAME}.app"
CONTENTS_DIR="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"
ICNS_PATH="$PROJECT_DIR/packaging/macos/AppIcon.icns"

if [[ -d "$APP_DIR" && "${1:-}" != "--force" ]]; then
  echo "Jordana Billing.app already exists. Use --force to rebuild."
  exit 0
fi

"$PROJECT_DIR/scripts/build_app_icon.sh"

rm -rf "$APP_DIR"
mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"

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
  <string>2</string>
  <key>CFBundleShortVersionString</key>
  <string>1.1</string>
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
  <string>Jordana Billing needs access to the Documents folder to read configuration, preserve the local database, and launch the local billing app.</string>
</dict>
</plist>
PLIST

cat > "$MACOS_DIR/launcher" <<'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RESOURCE_DIR="$BUNDLE_DIR/Resources"
APP_SUPPORT_DIR="${JORDANA_APP_SUPPORT_DIR:-$HOME/Library/Application Support/Jordana Billing}"
LOG_DIR="$APP_SUPPORT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/launcher.log"

{
  printf '[%s] Double-click launcher invoked.\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  exec "$RESOURCE_DIR/launch_installed_app.sh"
} >> "$LOG_FILE" 2>&1
LAUNCHER

chmod +x "$MACOS_DIR/launcher"
cp "$ICNS_PATH" "$RESOURCES_DIR/AppIcon.icns"
cp "$PROJECT_DIR/scripts/launch_installed_app.sh" "$RESOURCES_DIR/launch_installed_app.sh"
chmod +x "$RESOURCES_DIR/launch_installed_app.sh"

xattr -cr "$APP_DIR" 2>/dev/null || true
codesign --force --deep --sign - "$APP_DIR" 2>/dev/null || true

echo "Built: $APP_DIR"
echo "Double-click to launch."
