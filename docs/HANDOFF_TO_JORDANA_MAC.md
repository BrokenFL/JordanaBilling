# Handoff To Jordana Mac

This file is the continuation contract for a future Codex session on Jordana's computer.

## Goal

Continue the local-first invoice system without relying on prior chat history.

## Setup

1. Copy or clone this project folder onto Jordana's Mac.
2. Open Terminal in the project folder.
3. Run:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
jordana-invoice --db data/jordana_invoice.sqlite3 init-db
```

## Configure Automated Sync

1. Copy `.env.example` to `.env`.
2. Fill in the Apps Script web app URL, ingest API key, database path, and reports directory.
3. Do not commit or paste the real `.env` into docs or chat.

Run a manual sync:

```bash
PYTHONPATH=app python -m jordana_invoice sync
PYTHONPATH=app python -m jordana_invoice sync-status
```

Run a test without writing:

```bash
PYTHONPATH=app python -m jordana_invoice sync --dry-run
```

## Install Hourly Mac Sync

From the project folder, run:

```bash
scripts/install_sync_launch_agent.sh
```

The installer creates required folders, validates `.venv` and `.env`, installs `~/Library/LaunchAgents/com.jordana.billing.sync.plist`, loads the job, and runs one immediate sync.

The job runs at login and once every hour. Logs are written to:

- `logs/sync.stdout.log`
- `logs/sync.stderr.log`

To remove it:

```bash
scripts/uninstall_sync_launch_agent.sh
```

## Recover With CSV If Remote Sync Fails

1. Open the Google Sheet receiving Shortcut uploads.
2. Export `Raw_Event_Snapshots` as CSV.
3. Save it locally, for example:

```text
data/imports/Raw_Event_Snapshots_June.csv
```

4. Run:

```bash
jordana-invoice --db data/jordana_invoice.sqlite3 import-csv data/imports/Raw_Event_Snapshots_June.csv --report data/june_acceptance_report.md
```

This does not replace automated sync. It is for testing or emergency recovery.

## Rotate The API Key

1. Change the accepted key in Apps Script.
2. Deploy a new Apps Script web app version.
3. Update `JORDANA_INGEST_API_KEY` in `.env` on the Mac.
4. Run `PYTHONPATH=app python -m jordana_invoice sync --dry-run`.
5. If dry run succeeds, run `PYTHONPATH=app python -m jordana_invoice sync`.

## What To Review First

Open the generated report and focus on:

- `client_session` rows needing full-name confirmation
- Client rate gaps
- `unresolved` rows
- Personal/admin rows that should become exclusion aliases
- Time discrepancies
- Unknown service modes
- Missing account or billing-party relationships
- Missing suggested or approved rates
- Multi-person titles such as Fred and Bobsey

## Do Not Do Yet

- Do not generate PDFs.
- Do not mark sessions invoice-ready without Jordana review.
- Do not add clinical notes.
- Do not infer rates from memory or calendar notes.

## Next Development Steps

1. Add a small review UI or CLI for accepting/rejecting candidates.
2. Add confirmed clients and aliases.
3. Add rate management.
4. Re-run June imports and review until clean.
5. Build invoice generation only after normalized sessions are trusted.

## Phase 2 Backend Pieces Now Present

- People, account, account-member, billing-party, alias, rate-rule, session-participant, and review-item tables
- Service-mode and time-category parsing
- Effective-dated suggested-rate rules
- Developer commands for rate seeding, rate policy, and review decisions
- Phase 2 CSV exports

The next Codex session should build the review dashboard on top of these services, not PDF invoicing.

## Start Review UI

```bash
PYTHONPATH=app python -m jordana_invoice --db data/jordana_invoice.sqlite3 serve-review
```

Open `http://127.0.0.1:8765/review`.

The approved UI mockup is saved at `docs/review-ui-approved-mockup.png`.

## Git Safety

Before pushing or handing off through GitHub, run:

```bash
scripts/git_safety_check.sh
```

Live databases, reports, logs, screenshots with client names, shortcut backups, `.env`, and credentials should remain ignored locally.
