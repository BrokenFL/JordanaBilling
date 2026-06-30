#!/usr/bin/env bash
#
# Build the macOS AppIcon.icns from the approved source image.
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE_ICON="$PROJECT_DIR/packaging/macos/AppIcon-source.png"
ICONSET_DIR="$PROJECT_DIR/packaging/macos/AppIcon.iconset"
ICNS_PATH="$PROJECT_DIR/packaging/macos/AppIcon.icns"

if [[ ! -f "$SOURCE_ICON" ]]; then
  echo "Missing approved icon source: $SOURCE_ICON" >&2
  exit 1
fi
if ! command -v sips >/dev/null 2>&1; then
  echo "sips is required to build the macOS iconset." >&2
  exit 1
fi
if ! command -v iconutil >/dev/null 2>&1; then
  echo "iconutil is required to build AppIcon.icns." >&2
  exit 1
fi

rm -rf "$ICONSET_DIR"
mkdir -p "$ICONSET_DIR"

sips -z 16 16 "$SOURCE_ICON" --out "$ICONSET_DIR/icon_16x16.png" >/dev/null
sips -z 32 32 "$SOURCE_ICON" --out "$ICONSET_DIR/icon_16x16@2x.png" >/dev/null
sips -z 32 32 "$SOURCE_ICON" --out "$ICONSET_DIR/icon_32x32.png" >/dev/null
sips -z 64 64 "$SOURCE_ICON" --out "$ICONSET_DIR/icon_32x32@2x.png" >/dev/null
sips -z 128 128 "$SOURCE_ICON" --out "$ICONSET_DIR/icon_128x128.png" >/dev/null
sips -z 256 256 "$SOURCE_ICON" --out "$ICONSET_DIR/icon_128x128@2x.png" >/dev/null
sips -z 256 256 "$SOURCE_ICON" --out "$ICONSET_DIR/icon_256x256.png" >/dev/null
sips -z 512 512 "$SOURCE_ICON" --out "$ICONSET_DIR/icon_256x256@2x.png" >/dev/null
sips -z 512 512 "$SOURCE_ICON" --out "$ICONSET_DIR/icon_512x512.png" >/dev/null
sips -z 1024 1024 "$SOURCE_ICON" --out "$ICONSET_DIR/icon_512x512@2x.png" >/dev/null

iconutil -c icns "$ICONSET_DIR" -o "$ICNS_PATH"
echo "Built $ICNS_PATH"
