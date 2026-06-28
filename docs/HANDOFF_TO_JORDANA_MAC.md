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

The setup script creates `.venv`, required folders, initializes or migrates SQLite without overwriting an existing live database, creates a WAL-safe timestamped backup (using the SQLite backup API) when a database already exists, verifies the backup, and runs verification (current full suite is 1490 tests passing, 11 skipped, 0 failures). Schema migrations run only during explicit startup/init/migrate flows — normal API/web requests never run migrations or seed data.

## Configure Automated Sync

1. Copy `.env.example` to `.env`.
2. Fill in the Apps Script web app URL, ingest API key, database path, and reports directory.
3. Optionally configure `JORDANA_BACKUP_DIR` to override the default backup location (`~/.jordana_invoice/backups`).
4. Do not commit or paste the real `.env` into docs or chat.

Run a manual sync:

```bash
PYTHONPATH=app .venv/bin/python -m jordana_invoice sync
PYTHONPATH=app .venv/bin/python -m jordana_invoice sync-status
```

Run a test without writing:

```bash
PYTHONPATH=app .venv/bin/python -m jordana_invoice sync --dry-run
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
4. Run `PYTHONPATH=app .venv/bin/python -m jordana_invoice sync --dry-run`.
5. If dry run succeeds, run `PYTHONPATH=app .venv/bin/python -m jordana_invoice sync`.

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

The local prototype now supports invoice drafts, finalization, PDF history, and void/reissue. Configure private identity/branding with `docs/BUSINESS_PROFILE.md` before any private trial. New generated PDFs default to ignored `Invoices/<PERSON_CODE> - <Display Name>/<year>/`; this application does not send them.

- Do not mark sessions invoice-ready without Jordana review.
- Do not add clinical notes.
- Do not infer rates from memory or calendar notes.
- Do not create visible household accounts just because multiple people attended one session.

## Billing Relationships — Complete (Rounds 1–3)

Billing Relationships Rounds 1 through 3 are complete and merged into `main` as of commit `2d13942`.

**What is implemented:**

- Guided billing relationship creation wizard (3-step: invoice recipient → pays for → review and save)
- Invoice recipient and Pays for are separate concepts in the editor and directory
- People and organizations can be created in-wizard during relationship setup
- Relationship editing: change invoice recipient, add/remove covered clients, update billing delivery
- Deactivate and reactivate billing relationships (no permanent deletion)
- Session Review integration: launch wizard from a review candidate, preselect participants, attach relationship to session
- Exact active duplicate prevention during creation and editing
- In-page confirmation dialogs throughout (no browser `alert()`, `prompt()`, or `confirm()`)
- Unsaved changes detection in the editor with return-link confirmation
- XSS-safe rendering of user-provided values in return links

**What is not implemented:**

- Permanent deletion of billing relationships (by design — deactivation only)
- Formal client-versus-non-client schema distinction (all active people appear in search)
- Automatic payer classification
- Full right-panel redesign

**No schema migration was introduced.** All features use existing tables and columns.

**The production database remains local and private.** No real client data is committed to Git.

See `docs/CLIENTS_AND_ACCOUNTS.md` for full documentation of all rounds.

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

The next product round should add invoice delivery workflow and dashboard integration. The payment ledger, allocations, corrections (reversal/void/apply-funds), invoice payment history, and Payments workspace are now implemented. Credits, multi-invoice payments, formal reconciliation, and month-close workflows remain unfinished.

## Start Review UI

```bash
PYTHONPATH=app .venv/bin/python -m jordana_invoice --db data/jordana_invoice.sqlite3 serve-review
```

Open `http://127.0.0.1:8765/review`.

The approved UI mockup is saved at `docs/review-ui-approved-mockup.png`.

## Git Safety

Before pushing or handing off through GitHub, run:

```bash
scripts/git_safety_check.sh
scripts/privacy_check.sh
```

Live databases, reports, logs, screenshots with client names, local backups (stored outside the repository in `~/.jordana_invoice/backups` by default), `.env`, and credentials should remain ignored locally.
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
PYTHONPATH=app .venv/bin/python -m jordana_invoice --db data/demo/jordana_demo.sqlite3 serve-review
```

Never import demo rows into a live database.

## Recent Commits

The following commits completed the backup relocation, test repair, and review/appointment status rounds:

- `b0377d5` — WAL-safe SQLite backups
- `db69a3e` — external backup directory
- `434bc70` — backup path expansion and test isolation
- `6f57d8a` — demo invoice creation-order fix
- `c728226` — appointment status rate dimension and cancelled/no-show service labels
- `d57df55` — reparse unapproved candidates and `for <reference>` title evidence
- `5571ce5` — candidate-only send-to-review promotion route
- `a5b179d` — Sessions page Send to Review button for unclassified appointments
