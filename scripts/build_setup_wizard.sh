#!/usr/bin/env bash
#
# Build the native no-Terminal setup wizard for release artifacts.
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="Install Jordana Billing"
APP_DIR="${1:-$PROJECT_DIR/build/Install Jordana Billing.app}"
CONTENTS_DIR="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"
SOURCE="$PROJECT_DIR/packaging/macos/SetupWizard.swift"
ICNS_PATH="$PROJECT_DIR/packaging/macos/AppIcon.icns"

"$PROJECT_DIR/scripts/build_app_icon.sh" >/dev/null

rm -rf "$APP_DIR"
mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"

cat > "$CONTENTS_DIR/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>Install Jordana Billing</string>
  <key>CFBundleDisplayName</key>
  <string>Install Jordana Billing</string>
  <key>CFBundleIdentifier</key>
  <string>com.jordana.billing.installer</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1.0</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleExecutable</key>
  <string>InstallJordanaBilling</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
PLIST

swiftc -target arm64-apple-macos12 "$SOURCE" -o "$MACOS_DIR/InstallJordanaBilling"
cp "$ICNS_PATH" "$RESOURCES_DIR/AppIcon.icns"
xattr -cr "$APP_DIR" 2>/dev/null || true
xattr -dr com.apple.FinderInfo "$APP_DIR" 2>/dev/null || true
xattr -dr com.apple.fileprovider.fpfs#P "$APP_DIR" 2>/dev/null || true
rm -rf "$APP_DIR/Contents/_CodeSignature"
dot_clean -m "$APP_DIR" 2>/dev/null || true
xattr -c "$APP_DIR" 2>/dev/null || true
codesign --force --deep --sign - --timestamp=none "$APP_DIR" >/dev/null 2>&1 || true
echo "Built: $APP_DIR"
