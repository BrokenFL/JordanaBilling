# Fresh Install Guide

This guide covers setting up Jordana Billing on a fresh macOS machine from a clean clone.

## Prerequisites

- macOS 12 or later (Apple Silicon or Intel)
- Python 3.11 or newer (check with `python3 --version`)
- Internet access for pip and Google Sheets sync

## Steps

### 1. Clone the repository

Clone using GitHub Desktop or the command line:

```bash
git clone <repo-url> "Jordana Billing"
```

The repository includes `Jordana Billing.app` — a double-clickable launcher.

### 2. Add private configuration

The only private file needed is `.env` in the project root. It contains:

- `JORDANA_APPS_SCRIPT_URL` — the Google Apps Script `/exec` web app URL
- `JORDANA_INGEST_API_KEY` — the ingest API key accepted by Apps Script

No Google credentials JSON or OAuth tokens are required. Sync uses a simple HTTP POST to the Apps Script endpoint with the API key.

**Option A: Let the launcher create it**

Double-click `Jordana Billing.app`. On first run, it creates `.env` from `.env.example`, resolves all local paths, opens the new file in TextEdit, and shows a dialog naming the two credential values to add. Save the file and double-click the app again.

**Option B: Create it manually**

```bash
cp .env.example .env
```

Edit `.env` and fill in the two credential values. The `__PROJECT_DIR__` placeholders are resolved automatically on first launch — no need to edit paths manually.

### 3. Double-click the launcher

Double-click **Jordana Billing.app** in Finder.

**First launch** performs:

1. Checks Python 3.11+ (shows dialog if missing)
2. Creates a project-local virtual environment (`.venv`)
3. Installs pinned dependencies
4. Creates `.env` from template if missing, resolves local paths, and opens it in TextEdit
5. Validates `.env` without executing it as shell code
6. Creates a blank SQLite database if missing
7. Applies existing migrations safely
8. Runs a full Google Sheets sync (only if database is empty)
9. Starts the local review server on `127.0.0.1:8765`
10. Waits for a successful health check
11. Opens the review UI in the default browser

**Later launches** start quickly:

- Applies only pending safe migrations
- Starts the server
- Opens the browser
- Does not repeat a full import unnecessarily
- Preserves all local data

### 4. Verify

The review UI opens automatically at `http://127.0.0.1:8765/review`.

## No Database Required for Transfer

A fresh installation starts with no database. The first launch creates it, applies migrations, and imports the configured Google Sheet. Never copy a database between machines — each Mac creates its own from the Google Sheet source.

## No Terminal Required

The entire setup is double-click driven. The only manual step is pasting two credential values into the `.env` file that the launcher opens automatically in TextEdit.

## Command Reference (optional)

These scripts are available for Terminal use but are not required:

| Command | Purpose |
|---------|---------|
| `scripts/bootstrap.sh` | First-run setup and launch |
| `scripts/start_jordana.sh` | Subsequent launch |
| `scripts/stop_jordana.sh` | Stop the review server |
| `scripts/health_check.sh` | Check if server is healthy |
| `scripts/full_sync.sh` | Full Google Sheets sync |
| `scripts/backup_db.sh` | Backup the SQLite database |
| `scripts/reset_test_db.sh` | Test-only database reset (requires confirmation) |
| `scripts/build_launcher.sh --force` | Rebuild the .app bundle |
| `scripts/verify_install.sh` | Verify installation integrity |
| `scripts/git_safety_check.sh` | Check for staged private files |
| `scripts/privacy_check.sh` | Check for tracked private files |

## Private Files Required Separately

These files must be provided manually and are never committed to Git:

- `.env` — API keys and local paths (auto-created from template on first launch)
- `data/jordana_invoice.sqlite3` — local database (created on first run)
- `Reports/` — generated CSV reports
- `logs/` — sanitized application logs
- `Invoices/` — generated PDF invoices

## Troubleshooting

### "Python 3.11 or newer is required"

Install Python from [python.org](https://www.python.org/downloads/) or via Homebrew:

```bash
brew install python@3.12
```

### "Configuration Created" dialog

The launcher created `.env` from the template and opened it in TextEdit. Fill in:

- `JORDANA_APPS_SCRIPT_URL` — your Google Apps Script web app URL
- `JORDANA_INGEST_API_KEY` — your Apps Script ingest API key

Save the file, then double-click `Jordana Billing.app` again.

### "Configuration Incomplete" dialog

A required variable in `.env` is empty. The dialog names the exact variable and the file path.

### "Startup Failed" dialog

Check `logs/bootstrap.log` or `logs/start.log` for details. Common causes:

- Network failure during Google Sheets sync (server still starts)
- Port 8765 already in use (stop with `scripts/stop_jordana.sh`)
- Database migration error (check `data/backups/` for automatic backups)

### Server does not open in browser

Open manually: `http://127.0.0.1:8765/review`

## Apple Silicon Notes

The launcher works natively on Apple Silicon. Python 3.11+ from python.org or Homebrew provides arm64 binaries. No Rosetta required.
