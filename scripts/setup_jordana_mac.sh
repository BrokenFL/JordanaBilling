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

Use the authoritative installer instead:

  "$PROJECT_DIR/scripts/bootstrap.sh"

This stub did not create, copy, migrate, delete, or launch anything.
EOF
exit 2
