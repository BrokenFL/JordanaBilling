#!/usr/bin/env bash
#
# Interactive helper for creating the private Jordana Billing config file.
#
set -euo pipefail

CONFIG_PATH="${JORDANA_CONFIG_OUTPUT:-$HOME/Library/Application Support/Jordana Billing/config/.env}"

usage() {
  cat <<'EOF'
Usage: scripts/create_private_config.sh [--output PATH]

Creates the private Jordana Billing .env file interactively.
Do not upload this file to GitHub or paste its contents into chat, email, logs, or screenshots.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output) CONFIG_PATH="$2"; shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

CONFIG_DIR="$(dirname "$CONFIG_PATH")"
TMP_FILE=""

cleanup() {
  if [[ -n "${TMP_FILE:-}" && -f "$TMP_FILE" ]]; then
    rm -f "$TMP_FILE"
  fi
}
trap cleanup EXIT INT TERM

fail() {
  echo "ERROR: $1" >&2
  exit 1
}

valid_url() {
  [[ "$1" =~ ^https://[^[:space:]]+\.[^[:space:]]+/?[^[:space:]]*$ ]]
}

echo "Jordana Billing private configuration setup"
echo
echo "These values are private. The helper will not print the API key."
echo "Destination: $CONFIG_PATH"
echo

if [[ -e "$CONFIG_PATH" ]]; then
  printf 'A config file already exists. Type OVERWRITE to replace it, or press Return to cancel: '
  read -r overwrite
  if [[ "$overwrite" != "OVERWRITE" ]]; then
    echo "Cancelled. Existing config was preserved."
    exit 0
  fi
fi

printf 'Apps Script URL: '
read -r apps_script_url
if [[ -z "$apps_script_url" ]]; then
  fail "Apps Script URL cannot be empty."
fi
if ! valid_url "$apps_script_url"; then
  fail "Apps Script URL must be a valid https URL."
fi

printf 'Ingest API key (input hidden): '
read -r -s ingest_api_key
printf '\n'
if [[ -z "$ingest_api_key" ]]; then
  fail "Ingest API key cannot be empty."
fi

mkdir -p "$CONFIG_DIR"
TMP_FILE="$(mktemp "$CONFIG_DIR/.env.tmp.XXXXXX")"
chmod 600 "$TMP_FILE"
cat > "$TMP_FILE" <<EOF
JORDANA_APPS_SCRIPT_URL=$apps_script_url
JORDANA_INGEST_API_KEY=$ingest_api_key
EOF
chmod 600 "$TMP_FILE"
mv "$TMP_FILE" "$CONFIG_PATH"
TMP_FILE=""
chmod 600 "$CONFIG_PATH"

echo "Private config created at: $CONFIG_PATH"
echo "Permissions set to 600. Do not upload this file to GitHub."
