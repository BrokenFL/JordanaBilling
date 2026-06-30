#!/usr/bin/env bash
#
# Retired installer path.
#
# bootstrap.sh is the only supported installer. This stub intentionally performs
# no setup, migration, copy, deletion, or launch action.
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cat >&2 <<EOF
setup_jordana_mac.sh has been retired.

For production release artifacts, use the installer included in the release:

  scripts/install_release.sh --config /secure/path/.env --database /secure/path/jordana_invoice.sqlite3

For development checkouts only, use:

  "$PROJECT_DIR/scripts/bootstrap.sh"

This stub did not create, copy, migrate, delete, or launch anything.
EOF
exit 2
