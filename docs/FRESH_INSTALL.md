# Fresh Install Guide

This guide covers installing Jordana Billing on a new macOS computer from a clean clone.

## Choose The Correct Installation Type

There are two different installation paths. Do not confuse them.

### Production handoff or replacement Mac

A production handoff must preserve the operational SQLite database. Google Sheets contains raw calendar evidence, but it does not contain the complete reviewed operational state. Rebuilding only from the Sheet would omit or lose:

- confirmed people and aliases
- billing relationships and Bill To decisions
- approved session values and rate snapshots
- invoice drafts and finalized invoice history
- payments, allocations, receipts, and correction history
- audit history and local application settings

Follow `docs/PRIVATE_DATA_TRANSFER.md`. Transfer the live database, `.env`, private branding, generated invoices/receipts, and other private local files through an approved secure method. Never transfer production data through GitHub.

### New development, demo, or intentionally empty installation

A blank database may be created only for a new isolated development/demo installation or when an explicitly approved recovery plan calls for rebuilding raw evidence. A blank database can sync calendar snapshots from Google Sheets, but that is not a substitute for transferring an existing production database.

Use `scripts/create_demo_database.sh` for fictional demo data. Never import demo data into the operational database.

## Prerequisites

- macOS 12 or later, Apple Silicon or Intel
- Python 3.11 or newer
- Internet access for dependency installation and Google Sheets sync
- Access to the private GitHub repository
- For production handoff: the verified private transfer package described in `docs/PRIVATE_DATA_TRANSFER.md`

## 1. Clone The Repository

Clone using GitHub Desktop or the command line:

```bash
git clone <repo-url> "Jordana Billing"
```

The repository includes `Jordana Billing.app`, a double-clickable launcher.

## 2. Place Private Production Files

For a production handoff, stop before launching and place the verified private files in their documented locations.

Minimum production state normally includes:

```text
.env
data/jordana_invoice.sqlite3
data/private/
Invoices/
Receipts/
Reports/
```

Not every installation will already have every generated directory, but the live database must be transferred when preserving an existing production system.

Before accepting the database:

1. Verify the transfer manifest and SHA-256 checksums.
2. Confirm the database filename and destination path.
3. Confirm the source backup passed `PRAGMA integrity_check`.
4. Keep the transfer package outside the Git repository.
5. Retain the source backup until the new Mac is fully verified.

See `docs/PRIVATE_DATA_TRANSFER.md` for the authoritative secure-transfer procedure.

## 3. Configure `.env`

If `.env` was transferred securely, verify it without pasting its secrets into chat, documentation, source code, or Git.

For a new empty installation, copy the template:

```bash
cp .env.example .env
```

The required sync values include:

- `JORDANA_APPS_SCRIPT_URL`
- `JORDANA_INGEST_API_KEY`
- `JORDANA_DATABASE_PATH`
- `JORDANA_REPORTS_DIR`
- optional `JORDANA_BACKUP_DIR`

The launcher can create `.env` from `.env.example`, resolve `__PROJECT_DIR__` placeholders, open the file in TextEdit, and identify missing required values. It validates `.env` as data and does not execute it as shell code.

## 4. Double-Click The Launcher

Double-click **Jordana Billing.app** in Finder.

The app is ad-hoc code-signed and delegates setup to Terminal because macOS privacy controls may restrict direct access to files under `~/Documents`. The Terminal window is expected and may be closed after the browser opens successfully.

### First launch with a transferred production database

The launcher must:

1. Locate Python 3.11 or newer.
2. Create the project-local `.venv`.
3. Install pinned dependencies.
4. Validate `.env`.
5. Detect and preserve the existing operational database.
6. Create and verify a private SQLite backup before any pending migration.
7. Apply only pending additive migrations.
8. Start the local review server at `127.0.0.1:8765`.
9. Run intelligent calendar sync using the existing durable cursor.
10. Open the review UI in the default browser.

It must not delete, recreate, replace, or treat the transferred database as a clean installation.

### First launch without a database

For an explicitly empty installation, the launcher creates a new SQLite database, applies migrations, and performs an initial full read of staged Google Sheet evidence. This reconstructs raw calendar evidence and proposed review records only. It does not reconstruct prior human review, invoices, payments, or other production-only SQLite state.

### Later launches

Later launches:

- preserve all local data
- apply only pending safe migrations
- start the server and open the browser
- trigger intelligent sync after startup
- use incremental sync when a successful cursor exists
- repeat incremental sync while the app remains open
- never trigger the iPhone Shortcut

## 5. Verify The Installation

The review UI should open at:

```text
http://127.0.0.1:8765/review
```

Run the repository verification script when performing a production handoff:

```bash
scripts/verify_install.sh
```

For a transferred production database, also verify:

1. `PRAGMA integrity_check` returns `ok`.
2. Expected migration IDs are present through the current repository migration.
3. Manifest row counts match the source package.
4. Existing approved sessions, invoices, payments, and audit history are visible.
5. Stored finalized PDFs and receipts open from their existing records.
6. A manual calendar sync succeeds without duplicating snapshots or sessions.
7. A fresh private backup is created on the new Mac.

Do not run destructive reset scripts against the operational database.

## Calendar Synchronization

The app uses one intelligent sync path:

- no successful cursor: initial full read of staged Sheet evidence
- successful cursor present: incremental sync
- periodic in-app sync: incremental only
- Advanced **Rebuild Calendar Data from Sheet**: recovery-only full reread with confirmation and a private database backup

The app does not launch the iPhone Shortcut. The Shortcut remains responsible for placing new Apple Calendar snapshots into Google Sheets.

## Optional Command Reference

These scripts are available for terminal use:

| Command | Purpose |
|---|---|
| `scripts/bootstrap.sh` | First-run setup and launch |
| `scripts/start_jordana.sh` | Subsequent launch |
| `scripts/stop_jordana.sh` | Stop the review server |
| `scripts/health_check.sh` | Check server health |
| `scripts/full_sync.sh` | Recovery-oriented full Sheet sync |
| `scripts/backup_db.sh` | Create a private SQLite backup |
| `scripts/reset_test_db.sh` | Reset a test database only; requires confirmation |
| `scripts/build_launcher.sh --force` | Rebuild the `.app` bundle |
| `scripts/verify_install.sh` | Verify installation integrity |
| `scripts/git_safety_check.sh` | Check for staged private files |
| `scripts/privacy_check.sh` | Check for tracked private files |

## Private Files

The following remain local and must never be committed:

- `.env`
- `data/jordana_invoice.sqlite3`
- `data/private/`
- raw imports and Google credentials
- `Reports/`
- `logs/`
- `Invoices/`
- `Receipts/`
- SQLite backups
- Shortcut exports or secret-bearing Shortcut build specifications

## Troubleshooting

### Python 3.11 or newer is required

The launcher searches common Homebrew, system, and `PATH` locations. Install a current Python from python.org or Homebrew when necessary:

```bash
brew install python@3.12
```

### Configuration created or incomplete

Fill in the missing variables identified by the launcher, save `.env`, and launch the app again. Do not paste live values into GitHub, documentation, screenshots, or chat.

### Startup failed

Inspect the sanitized local logs:

```text
logs/bootstrap.log
logs/start.log
```

Common causes include:

- missing or invalid `.env` values
- unavailable network during sync
- port `8765` already in use
- a migration failure
- an unreadable or missing transferred file

A sync failure should not delete or reset the database. Migration failure should stop startup and preserve or restore the pre-migration database from the verified private backup.

### Server does not open in the browser

Open manually:

```text
http://127.0.0.1:8765/review
```

## Apple Silicon

The launcher runs natively on Apple Silicon when Python 3.11 or newer is installed. Rosetta is not required.
