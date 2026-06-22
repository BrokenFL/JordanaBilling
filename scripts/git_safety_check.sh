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
      case "$file" in
        $pattern)
          echo "Blocked staged file: $file" >&2
          exit 1
          ;;
      esac
    done <<< "$staged"
  done
fi

if rg --hidden -n 'jb_[0-9a-fA-F]{8,}' . -g '!.env' -g '!.venv/**' -g '!build/**' >/tmp/jordana_git_secret_scan.txt; then
  cat /tmp/jordana_git_secret_scan.txt >&2
  echo "Blocked: possible API key outside .env." >&2
  exit 1
fi

echo "Git safety check passed."
