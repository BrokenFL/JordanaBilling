#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.jordana.billing.sync"
SOURCE_PLIST="$PROJECT_DIR/launchd/$LABEL.plist"
TARGET_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$PROJECT_DIR/logs" "$HOME/Library/LaunchAgents"

if [[ ! -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  echo "Missing virtual environment: $PROJECT_DIR/.venv/bin/python" >&2
  echo "Create it with: python3 -m venv .venv && source .venv/bin/activate && python -m pip install ." >&2
  exit 1
fi

if [[ ! -f "$PROJECT_DIR/.env" ]]; then
  echo "Missing .env: $PROJECT_DIR/.env" >&2
  echo "Create it from .env.example and add the Apps Script URL, API key, and database path." >&2
  exit 1
fi

sed "s#__PROJECT_DIR__#$PROJECT_DIR#g" "$SOURCE_PLIST" > "$TARGET_PLIST"

launchctl bootout "gui/$(id -u)" "$TARGET_PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$TARGET_PLIST"
launchctl enable "gui/$(id -u)/$LABEL"

"$PROJECT_DIR/.venv/bin/python" -m jordana_invoice sync --env "$PROJECT_DIR/.env"

echo "Installed and tested $LABEL"
echo "stdout: $PROJECT_DIR/logs/sync.stdout.log"
echo "stderr: $PROJECT_DIR/logs/sync.stderr.log"
