#!/usr/bin/env bash
set -euo pipefail

blocked_patterns=(
  '.env'
  '*.sqlite3'
  '*.sqlite3-*'
  'Reports/*'
  'logs/*'
  'output/*'
  'shortcut-backups/*'
  'docs/review-ui-approved-mockup.png'
  '*.pdf'
  '*credentials*'
)

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  staged="$(git diff --cached --name-only)"
  for pattern in "${blocked_patterns[@]}"; do
    while IFS= read -r file; do
      [[ -z "$file" ]] && continue
      [[ "$file" == "config/example.env" ]] && continue
      case "$file" in
        $pattern)
          echo "Blocked staged file: $file" >&2
          exit 1
          ;;
      esac
    done <<< "$staged"
  done
fi

if rg --hidden -n 'jb_[0-9a-fA-F]{8,}' . -g '!.git/**' -g '!.env' -g '!.venv/**' -g '!build/**' -g '!data/private/**' -g '!output/**' >/tmp/jordana_git_secret_scan.txt 2>/dev/null; then
  cat /tmp/jordana_git_secret_scan.txt >&2
  echo "Blocked: possible API key outside .env." >&2
  exit 1
elif ! command -v rg >/dev/null 2>&1; then
  # Fallback to grep if rg is not installed
  if grep -rn 'jb_[0-9a-fA-F]\{8,\}' . --exclude-dir=.git --exclude=.env --exclude-dir=.venv --exclude-dir=build --exclude-dir=data --exclude-dir=output --exclude-dir=shortcut-backups >/tmp/jordana_git_secret_scan.txt 2>/dev/null; then
    cat /tmp/jordana_git_secret_scan.txt >&2
    echo "Blocked: possible API key outside .env." >&2
    exit 1
  fi
fi

echo "Git safety check passed."
