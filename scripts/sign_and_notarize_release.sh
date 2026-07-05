#!/usr/bin/env bash
#
# Sign and notarize a prepared Jordana Billing macOS release artifact.
#
# This script intentionally requires local Apple Developer credentials. It does
# not create, store, or export certificates, keys, Apple IDs, passwords, or
# notarytool profiles.
#
set -euo pipefail

RELEASE_DIR=""
DMG_PATH=""
IDENTITY="${JORDANA_CODESIGN_IDENTITY:-}"
NOTARY_PROFILE="${JORDANA_NOTARYTOOL_PROFILE:-}"

usage() {
  cat <<'EOF'
Usage: scripts/sign_and_notarize_release.sh --release-dir PATH --dmg PATH

Required environment:
  JORDANA_CODESIGN_IDENTITY     Developer ID Application identity name or hash.
  JORDANA_NOTARYTOOL_PROFILE    Existing xcrun notarytool keychain profile.

The script signs nested executable code inside prepared app bundles, signs the
app bundles with hardened runtime, signs the DMG, submits it to Apple notarytool,
staples the ticket, and verifies codesign, spctl, stapler, and hdiutil results.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --release-dir) RELEASE_DIR="$2"; shift 2 ;;
    --dmg) DMG_PATH="$2"; shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

fail() {
  echo "ERROR: $1" >&2
  exit 1
}

[[ "$(uname -s)" == "Darwin" ]] || fail "Developer ID signing and notarization require macOS."
[[ -n "$RELEASE_DIR" && -d "$RELEASE_DIR" ]] || fail "--release-dir must point to a prepared release directory."
[[ -n "$DMG_PATH" && -f "$DMG_PATH" ]] || fail "--dmg must point to the release DMG."
[[ -n "$IDENTITY" ]] || fail "JORDANA_CODESIGN_IDENTITY is required. Do not use ad-hoc signing for notarization."
[[ -n "$NOTARY_PROFILE" ]] || fail "JORDANA_NOTARYTOOL_PROFILE is required. Create it locally with xcrun notarytool store-credentials."
command -v codesign >/dev/null 2>&1 || fail "codesign is unavailable."
command -v xcrun >/dev/null 2>&1 || fail "xcrun is unavailable."
command -v spctl >/dev/null 2>&1 || fail "spctl is unavailable."
command -v stapler >/dev/null 2>&1 || fail "stapler is unavailable."
command -v hdiutil >/dev/null 2>&1 || fail "hdiutil is unavailable."

sign_file_if_needed() {
  local path="$1"
  [[ -f "$path" ]] || return 0
  if file "$path" | grep -Eq 'Mach-O|shell script|Python script'; then
    codesign --force --options runtime --timestamp --sign "$IDENTITY" "$path"
  fi
}

sign_app_bundle() {
  local app="$1"
  find "$app/Contents" -type f -perm -111 -print0 | while IFS= read -r -d '' file_path; do
    sign_file_if_needed "$file_path"
  done
  find "$app/Contents" \( -name '*.dylib' -o -name '*.so' \) -type f -print0 | while IFS= read -r -d '' file_path; do
    sign_file_if_needed "$file_path"
  done
  codesign --force --options runtime --timestamp --sign "$IDENTITY" "$app"
  codesign --verify --deep --strict --verbose=2 "$app"
  spctl --assess --type execute --verbose=4 "$app"
}

find "$RELEASE_DIR" -name '*.app' -type d -print0 | while IFS= read -r -d '' app; do
  sign_app_bundle "$app"
done

codesign --force --timestamp --sign "$IDENTITY" "$DMG_PATH"
codesign --verify --deep --strict --verbose=2 "$DMG_PATH"
spctl --assess --type open --context context:primary-signature --verbose=4 "$DMG_PATH"
xcrun notarytool submit "$DMG_PATH" --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple "$DMG_PATH"
xcrun stapler validate "$DMG_PATH"
hdiutil verify "$DMG_PATH"

echo "Signing and notarization verification passed."
