#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

fail=0

blocked_tracked='(\.env$|\.sqlite3($|-)|credential.*\.json$|Reports/|logs/|output/|\.pdf$|Jordana_Private_Transfer/)'
if git ls-files | grep -E "$blocked_tracked" >/tmp/jordana_privacy_tracked.txt; then
  echo "Blocked tracked private files:" >&2
  cat /tmp/jordana_privacy_tracked.txt >&2
  fail=1
fi

blocked_staged='(\.env$|\.sqlite3($|-)|credential.*\.json$|Reports/|logs/|output/|\.pdf$|Jordana_Private_Transfer/)'
if git diff --cached --name-only | grep -E "$blocked_staged" >/tmp/jordana_privacy_staged.txt; then
  echo "Blocked staged private files:" >&2
  cat /tmp/jordana_privacy_staged.txt >&2
  fail=1
fi

if rg --hidden -n 'jb_[0-9a-fA-F]{8,}|AKIA[0-9A-Z]{16}|-----BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY-----' . \
  -g '!.git/**' -g '!.env' -g '!.venv/**' -g '!build/**' -g '!logs/**' -g '!Reports/**' \
  -g '!data/private/**' -g '!output/**' >/tmp/jordana_privacy_secrets.txt 2>/dev/null; then
  echo "Possible secret outside approved private files:" >&2
  cat /tmp/jordana_privacy_secrets.txt >&2
  fail=1
elif ! command -v rg >/dev/null 2>&1; then
  # Fallback to grep if rg is not installed
  if grep -rn 'jb_[0-9a-fA-F]\{8,\}\|AKIA[0-9A-Z]\{16\}\|-----BEGIN \(RSA \|OPENSSH \|EC \)\?PRIVATE KEY-----' . \
    --exclude-dir=.git --exclude=.env --exclude-dir=.venv --exclude-dir=build \
    --exclude-dir=logs --exclude-dir=Reports --exclude-dir=data --exclude-dir=output --exclude-dir=shortcut-backups \
    >/tmp/jordana_privacy_secrets.txt 2>/dev/null; then
    echo "Possible secret outside approved private files:" >&2
    cat /tmp/jordana_privacy_secrets.txt >&2
    fail=1
  fi
fi

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi

echo "Privacy check passed."
