#!/usr/bin/env bash
#
# Launch Jordana Billing after bootstrap has installed the project.
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_FILE="$PROJECT_DIR/logs/start.log"
mkdir -p "$PROJECT_DIR/logs"

# shellcheck source=launcher_common.sh
. "$PROJECT_DIR/scripts/launcher_common.sh"

LOG_FILE="$PROJECT_DIR/logs/start.log"
log_message "Starting Jordana Billing from existing installation."
launch_jordana_billing
