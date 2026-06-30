# Fresh Install Guide

This guide covers installing Jordana Billing on a new macOS computer from a versioned release artifact.

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

- macOS 12 or later on Apple Silicon
- The Python major/minor version listed in `release_manifest.json` installed once
- A versioned DMG release artifact from `scripts/build_release.sh`
- Internet access for Google Sheets sync during application use
- For production handoff: the verified private transfer package described in `docs/PRIVATE_DATA_TRANSFER.md`

## 1. Transfer The Release

Transfer the release DMG and checksum file to the target Mac, then verify:

```bash
shasum -a 256 -c JordanaBilling-<version>-<commit>-macos-arm64.dmg.sha256
```

Open the DMG. A production install does not require Terminal, Git, or a source clone.

## 2. Place Private Production Files

For a production handoff, stop before launching and place the verified private files in their documented locations.

Minimum production state normally includes:

```text
config/.env
data/jordana_invoice.sqlite3
backups/
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

## 3. Private Configuration Setup

Never upload `.env` to GitHub. Never send secrets in email, chat, logs, screenshots, or release assets.

On the target Mac, double-click `Install Jordana Billing.app` from the DMG.

It asks for:

- `JORDANA_APPS_SCRIPT_URL`
- `JORDANA_INGEST_API_KEY`

The API key input is hidden. The setup app writes
`~/Library/Application Support/Jordana Billing/config/.env` with permissions
`600`. The installer finds that standard path automatically and preserves it
across reinstall/update.

## 4. Install And Launch

Run the one-time setup app from the DMG. It installs
`~/Applications/Jordana Billing.app`, creates the private runtime from the
offline wheelhouse, and runs installation verification.

After installation, double-click `~/Applications/Jordana Billing.app` in Finder.
The app is ad-hoc code-signed, not notarized, so Gatekeeper may require
right-click Open or Security & Privacy approval.

### First launch with a transferred production database

The launcher must:

1. Validate the installed app runtime.
2. Validate private config in Application Support.
3. Detect and preserve the existing operational database.
4. Apply only pending additive migrations.
5. Start the local review server at `127.0.0.1:8765`.
6. Run intelligent calendar sync using the existing durable cursor.
7. Open the review UI in the default browser.

It must not delete, recreate, replace, or treat the transferred database as a clean installation.

### First launch without a database

For production, a missing configured SQLite database is an error. The launcher
will stop with a clear message instead of creating a replacement blank database.

For an explicitly empty clean-Mac test, choose the clean-start database option
inside the setup app. Rebuilding from Google Sheets reconstructs raw calendar
evidence and proposed review records only. It does not reconstruct prior human
review, invoices, payments, clients, billing relationships, or other
production-only SQLite state.

### Later launches

Later launches:

- preserve all local data
- apply only pending safe migrations
- start the server and open the browser
- trigger intelligent sync after startup
- use incremental sync when a successful cursor exists
- repeat incremental sync while the app remains open
- never trigger the iPhone Shortcut
- reuse an already-running app-owned healthy local server
- fail safely when another application owns port `8765`

## 5. Verify The Installation

The review UI should open at:

```text
http://127.0.0.1:8765/review
```

Run the release verification script when performing a production handoff:

```bash
scripts/verify_installation.sh
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
| `scripts/install_release.sh` | One-time release installer |
| `scripts/build_setup_wizard.sh` | Build the native setup app for release artifacts |
| `scripts/update_release.sh` | Deliberate update path with pre-update DB backup |
| `scripts/verify_installation.sh` | Verify installed app, runtime, config, DB, and static assets |
| `scripts/build_release.sh` | Development checkout command to create the release artifact |
| `scripts/bootstrap.sh` | Development checkout bootstrap and source launcher only |
| `scripts/git_safety_check.sh` | Check for staged private files before Git operations |
| `scripts/privacy_check.sh` | Check for tracked private files |

## Private Files

The following remain local and must never be committed:

- `~/Library/Application Support/Jordana Billing/config/.env`
- `~/Library/Application Support/Jordana Billing/data/jordana_invoice.sqlite3`
- `~/Library/Application Support/Jordana Billing/backups/`
- raw imports and Google credentials
- `~/Library/Application Support/Jordana Billing/Reports/`
- `~/Library/Application Support/Jordana Billing/logs/`
- `Invoices/`
- `Receipts/`
- SQLite backups
- Shortcut exports or secret-bearing Shortcut build specifications

## Troubleshooting

### Matching Python Runtime Is Required

The V1 installer requires the Python major/minor version recorded in `release_manifest.json` before it creates the private app runtime. Install that Python from python.org or Homebrew when necessary:

```bash
brew install python@3.12
```

### Configuration created or incomplete

Supply the private `.env` to `scripts/install_release.sh --config ...`. Do not paste live values into GitHub, documentation, screenshots, or chat.

### Startup failed

Inspect the sanitized local logs:

```text
logs/bootstrap.log
logs/start.log
~/Library/Application Support/Jordana Billing/logs/launch.log
```

Common causes include:

- missing or invalid `.env` values
- unavailable network during sync
- port `8765` already in use by another application
- a migration failure
- an unreadable or missing transferred file

A sync failure should not delete or reset the database. Migration failure should stop startup and preserve or restore the pre-migration database from the verified private backup.

### Port 8765 is in use

The launcher never kills an unrelated process on port `8765`. If a verified
Jordana Billing server is already healthy, it opens the browser and exits. If
another process owns the port, it stops and asks Brooke to close the other app
or investigate.

### Launcher icon

The approved icon source lives at:

```text
packaging/macos/AppIcon-source.png
```

The reproducible generated icon lives at:

```text
packaging/macos/AppIcon.icns
Jordana Billing.app/Contents/Resources/AppIcon.icns
```

### Server does not open in the browser

Open manually:

```text
http://127.0.0.1:8765/review
```

## Apple Silicon

The launcher runs natively on Apple Silicon when Python 3.11 or newer is installed. Rosetta is not required.
