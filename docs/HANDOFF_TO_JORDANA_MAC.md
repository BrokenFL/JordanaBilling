# Handoff To Jordana Mac

This file is the continuation contract for a future Codex session on Jordana's computer.

## Goal

Continue the local-first invoice system without relying on prior chat history.

## Setup

1. Clone the private GitHub repository onto Jordana's Mac.
2. Transfer private data separately using `docs/PRIVATE_DATA_TRANSFER.md`.
3. Open Terminal in the project folder.
4. Run:

```bash
scripts/setup_jordana_mac.sh
```

The setup script creates `.venv`, required folders, initializes or migrates SQLite without overwriting an existing live database, creates a first backup when a database already exists, and runs verification.

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
- Participants versus Bill to decisions
- Any changed rate that should become session-only, a future-person exception, or a future joint-session exception

## Do Not Do Yet

The local prototype now supports invoice drafts, finalization, PDF history, and void/reissue. Configure private identity/branding with `docs/BUSINESS_PROFILE.md` before any private trial. Generated PDFs default to ignored `Invoices/<year>/`; this application does not send them.

- Do not mark sessions invoice-ready without Jordana review.
- Do not add clinical notes.
- Do not infer rates from memory or calendar notes.
- Do not create visible household accounts just because multiple people attended one session.

## Next Development Steps

1. Finish the one-click launcher and synchronization experience.
2. Re-run June imports and review until clean.
3. Confirm rate exceptions and bill-to defaults with Jordana.
4. Keep invoice eligibility tied to reviewed normalized sessions.

## Phase 2 Backend Pieces Now Present

- People, account, account-member, billing-party, alias, rate-rule, session-participant, and review-item tables
- Service-mode and time-category parsing
- Effective-dated suggested-rate rules
- Participant-combination rate exceptions through `rate_rule_participants`
- Simplified Participants and Bill-to review workflow
- Developer commands for rate seeding, rate policy, and review decisions
- Phase 2 CSV exports

The next product round should add payment tracking, invoice delivery workflow, and dashboard integration without weakening snapshot immutability.

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
scripts/privacy_check.sh
```

Live databases, reports, logs, screenshots with client names, shortcut backups, `.env`, and credentials should remain ignored locally.
## Calendar Intake Note

Do not change the live Shortcut or Apps Script for this calendar-classification round. The Shortcut should continue to send all non-all-day calendars through the existing Google Sheet headers, including the existing `calendar` field.

`Jordana Work` may be configured later as:

```bash
JORDANA_PREFERRED_WORK_CALENDAR=Jordana Work
```

That value is a review/classification preference only. It is not a Shortcut filter and it does not reject events from other calendars.

For demo review on a Mac, create the isolated sanitized database:

```bash
scripts/create_demo_database.sh
PYTHONPATH=app python3 -m jordana_invoice --db data/demo/jordana_demo.sqlite3 serve-review
```

Never import demo rows into a live database.
