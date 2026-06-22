#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10 or newer is required.")
print(f"Python OK: {sys.version.split()[0]}")
PY

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi

. .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
if [[ -f requirements.txt ]]; then
  python -m pip install -r requirements.txt
fi

mkdir -p data Reports logs backups imports credentials

if [[ ! -f .env ]]; then
  if [[ -f .env.example ]]; then
    cp .env.example .env
    echo "Created .env from .env.example. Fill in local values before syncing."
  else
    touch .env
    echo "Created empty .env."
  fi
fi

if git ls-files --error-unmatch data/jordana_invoice.sqlite3 >/dev/null 2>&1; then
  echo "Refusing setup: live database is tracked by Git." >&2
  exit 1
fi

if [[ ! -f data/jordana_invoice.sqlite3 ]]; then
  PYTHONPATH=app python -m jordana_invoice --db data/jordana_invoice.sqlite3 init-db
else
  cp data/jordana_invoice.sqlite3 "backups/jordana_invoice.$(date -u +%Y%m%dT%H%M%SZ).sqlite3"
  PYTHONPATH=app python -m jordana_invoice --db data/jordana_invoice.sqlite3 init-db
fi

scripts/verify_install.sh

echo "Setup complete. Launch with:"
echo "PYTHONPATH=app .venv/bin/python -m jordana_invoice --db data/jordana_invoice.sqlite3 serve-review --host 127.0.0.1 --port 8765"
