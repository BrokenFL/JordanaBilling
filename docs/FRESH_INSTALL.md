# Fresh Install Guide

This guide covers setting up Jordana Billing on a fresh macOS machine from a clean clone.

## Prerequisites

- macOS 12 or later (Apple Silicon or Intel)
- Python 3.11 or newer (check with `python3 --version`)
- Git
- Internet access for pip and Google Sheets sync

## Steps

### 1. Clone the repository

```bash
git clone <repo-url> "Jordana Billing"
cd "Jordana Billing"
```

### 2. Add private credentials

Copy the example env file and fill in real values:

```bash
cp .env.example .env
```

Edit `.env` and replace `__PROJECT_DIR__` with the actual absolute path to the project folder. Fill in:

- `JORDANA_APPS_SCRIPT_URL` — the Google Apps Script `/exec` web app URL
- `JORDANA_INGEST_API_KEY` — the ingest API key accepted by Apps Script
- `JORDANA_DATABASE_PATH` — absolute path to the SQLite database (default: `<project>/data/jordana_invoice.sqlite3`)
- `JORDANA_REPORTS_DIR` — absolute path for CSV report output (default: `<project>/Reports`)

Never commit `.env` or share it.

### 3. Build the launcher

```bash
bash scripts/build_launcher.sh
```

This creates `Jordana Billing.app` at the project root.

### 4. Double-click the launcher

Double-click **Jordana Billing.app** in Finder.

**First launch** performs:

1. Creates a project-local virtual environment (`.venv`)
2. Installs pinned dependencies
3. Validates `.env` for required variables
4. Creates a blank SQLite database if missing
5. Applies existing migrations safely
6. Runs a full Google Sheets sync (only if database is empty)
7. Starts the local review server on `127.0.0.1:8765`
8. Waits for a successful health check
9. Opens the review UI in the default browser

**Later launches** start quickly:

- Applies only pending safe migrations
- Starts the server
- Opens the browser
- Does not repeat a full import unnecessarily
- Preserves all local data

### 5. Verify

Open a browser to `http://127.0.0.1:8765/review` if it does not open automatically.

Run the health check:

```bash
scripts/health_check.sh
```

## Command Reference

| Command | Purpose |
|---------|---------|
| `scripts/bootstrap.sh` | First-run setup and launch |
| `scripts/start_jordana.sh` | Subsequent launch |
| `scripts/stop_jordana.sh` | Stop the review server |
| `scripts/health_check.sh` | Check if server is healthy |
| `scripts/full_sync.sh` | Full Google Sheets sync |
| `scripts/backup_db.sh` | Backup the SQLite database |
| `scripts/reset_test_db.sh` | Test-only database reset (requires confirmation) |
| `scripts/build_launcher.sh` | Rebuild the .app bundle |
| `scripts/verify_install.sh` | Verify installation integrity |
| `scripts/git_safety_check.sh` | Check for staged private files |
| `scripts/privacy_check.sh` | Check for tracked private files |

## Private Files Required Separately

These files must be provided manually and are never committed to Git:

- `.env` — API keys and local paths
- `credentials*.json` — Google credential files (if applicable)
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

### "Configuration Missing" dialog

The `.env` file was not found or required variables are empty. Re-check step 2.

### "Startup Failed" dialog

Check `logs/bootstrap.log` or `logs/start.log` for details. Common causes:

- Network failure during Google Sheets sync
- Port 8765 already in use (stop with `scripts/stop_jordana.sh`)
- Database migration error (check `data/backups/` for automatic backups)

### Server does not open in browser

Open manually: `http://127.0.0.1:8765/review`

## Apple Silicon Notes

The launcher works natively on Apple Silicon. Python 3.11+ from python.org or Homebrew provides arm64 binaries. No Rosetta required.
