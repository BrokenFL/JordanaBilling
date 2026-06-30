#!/usr/bin/env bash
#
# Authoritative installer and launcher for Jordana Billing.
#
# Safe to re-run. It creates or repairs the project-local .venv, installs the
# package, validates private configuration, verifies the configured SQLite
# database exists and is readable, applies pending additive migrations, starts
# only this app's local server, waits for health, and opens the browser.
#
# It never creates, overwrites, deletes, or raw-copies the operational database.
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_FILE="$PROJECT_DIR/logs/bootstrap.log"
mkdir -p "$PROJECT_DIR/logs" "$PROJECT_DIR/data" "$PROJECT_DIR/Reports"

# shellcheck source=launcher_common.sh
. "$PROJECT_DIR/scripts/launcher_common.sh"

ensure_runtime_paths
cd "$PROJECT_DIR"
LOG_FILE="$PROJECT_DIR/logs/bootstrap.log"

log_message "Starting authoritative Jordana Billing bootstrap."
prepare_bootstrap_environment
ensure_env_file
resolve_project_placeholders
validate_private_configuration
handle_existing_server_or_port
apply_pending_migrations
handle_existing_server_or_port
start_review_server
wait_for_health_or_fail
open_review_url
log_message "Bootstrap complete."
