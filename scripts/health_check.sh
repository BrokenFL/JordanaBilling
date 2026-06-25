#!/usr/bin/env bash
#
# Health check for the Jordana Billing review server.
# Exits 0 if healthy, 1 if not responding.
#
set -euo pipefail

PORT="${JORDANA_PORT:-8765}"
HEALTH_URL="http://127.0.0.1:${PORT}/api/health"
MAX_WAIT="${1:-5}"

for i in $(seq 1 "$MAX_WAIT"); do
  if curl -sf "$HEALTH_URL" >/dev/null 2>&1; then
    echo "healthy"
    exit 0
  fi
  sleep 1
done

echo "unhealthy"
exit 1
