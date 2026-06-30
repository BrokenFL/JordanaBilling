# Handoff To Jordana Mac

This is the continuation contract for installing, verifying, or continuing development on Jordana's computer. Read `AGENTS.md` and `docs/CURRENT_IMPLEMENTATION_STATUS_AND_HANDOFF.md` before making changes.

## Goal

Continue the local-first invoice system without relying on chat history while preserving all private operational data and reviewed billing history.

## Source Of Truth

Use this order:

1. latest explicit approved decision
2. current repository, schema, migrations, tests, and documentation
3. `docs/CURRENT_IMPLEMENTATION_STATUS_AND_HANDOFF.md`
4. current private local configuration and transferred operational data
5. older historical notes

Do not revive obsolete schemas, terminology, side-inspector UI behavior, editable time-category controls, or abandoned workflows.

## Before Any Change

From the actual local checkout, inspect:

```bash
pwd
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
git log -1 --oneline
```

Interpret the state before editing:

- clean and synchronized: proceed
- dirty: stop and inspect every change
- behind: use `git pull --ff-only`
- diverged: stop and investigate
- locally ahead only because a completed prior round was committed but not pushed: inspect and push it; do not reset or discard it automatically

Also read:

- `AGENTS.md`
- `docs/CURRENT_IMPLEMENTATION_STATUS_AND_HANDOFF.md`
- `docs/PRIVATE_DATA_TRANSFER.md`
- `docs/FRESH_INSTALL.md`
- the documents relevant to the requested change

## Production Handoff Setup

1. Build a versioned release artifact from the development Mac.
2. Transfer the release zip and private production data separately using `docs/PRIVATE_DATA_TRANSFER.md`.
3. Verify the release checksum and the private transfer manifest/checksums.
4. Unzip the release on Jordana's Mac.
5. Open Terminal in the unzipped release folder.
6. Run the private config helper:

```bash
scripts/create_private_config.sh
```

7. Run the one-time installer:

```bash
scripts/install_release.sh --database /secure/path/jordana_invoice.sqlite3
```

`scripts/install_release.sh` is the production installer for release artifacts.
The older `scripts/setup_jordana_mac.sh` path is retired. `scripts/bootstrap.sh`
is now a development-checkout bootstrap and source launcher, not the daily
production launch path.

A production handoff must include the operational SQLite database. Google Sheets contains raw calendar evidence but cannot reconstruct reviewed people, billing relationships, approved sessions, invoices, payments, receipts, or audit history.

Never transfer private production files through GitHub.

Typical private local state now lives under
`~/Library/Application Support/Jordana Billing/` and includes:

```text
config/.env
data/jordana_invoice.sqlite3
backups/
logs/
Reports/
```

The setup and launcher flows must preserve an existing database, create a verified private backup before pending migrations, and apply only additive migrations. They must never delete, recreate, or silently replace the operational database.

The double-click app uses `Contents/Resources/launch_installed_app.sh`. It
validates private config, verifies that the Application Support SQLite database
exists and can be opened read-only, reuses a verified already-running Jordana
Billing server, and refuses to kill or reuse an unrelated process on port `8765`.
It does not run pip, Git, PyPI, editable installs, dependency repair, or blank DB
creation during normal launch.

## Configuration

For a production handoff, create or verify `.env` locally without exposing its values:

```bash
scripts/create_private_config.sh
```

The helper stores the private config at `~/Library/Application Support/Jordana Billing/config/.env`, hides API-key input, and sets file permissions to `600`.

Required production release configuration includes:

- `JORDANA_APPS_SCRIPT_URL`
- `JORDANA_INGEST_API_KEY`

Do not commit or paste the real `.env`, credentials, Script Properties, spreadsheet IDs, or private paths into documentation, screenshots, logs, GitHub, or chat.

## Current Database State

The current migration head is:

```text
015_duplicate_repair_reversal_state
```

Migrations `001` through `015` are registered. `app/jordana_invoice/db.py` is the executable migration source of truth.

Migration safety includes:

- database locking
- WAL-safe SQLite backup
- backup integrity verification
- transactional migration
- restore on failure
- no request-path migrations
- no deletion or reset of the operational database

See `docs/SCHEMA_AUDIT.md` for the current migration list and table responsibilities.

See `docs/WRITE_ENDPOINT_CONTRACTS.md` for a complete inventory of every backend write HTTP endpoint and its current request/response contract. Characterization tests are in `tests/test_write_endpoint_contracts.py`.

Round 4A.2 added explicit request-parsing and validation helpers for the four highest-risk review write endpoints (approve, save/section saves, mark/duplicate resolution, restore). The helpers are in `app/jordana_invoice/request_validation.py` and use frozen dataclasses with explicit parser functions. All existing endpoint paths, payload keys, response shapes, status codes, and business rules are preserved. Focused tests are in `tests/test_request_validation.py` (102 tests). See the Round 4A.2 section in `docs/WRITE_ENDPOINT_CONTRACTS.md` for details.

Round 4B.1 extracted the shared frontend API utility. The `api()` request helper and `sanitizeUiErrorMessage()` error sanitizer were moved from `review.js` into a new shared module at `app/jordana_invoice/static/js/api.js`. The module is loaded as a classic IIFE script before `review.js` and assigns `window.JordanaAPI` with `api` and `sanitizeUiErrorMessage`. All endpoint paths, HTTP methods, payload keys, headers, write-token behavior, response parsing, error messages, warning handling, and call-site behavior are preserved. Two direct `fetch()` calls intentionally remain in `review.js`: the draft PDF blob download and the billing-relationship setup (which throws raw JSON for duplicate inspection). No backend contracts, UI behavior, or workflow structure were changed. Focused tests are in `tests/test_api_util.py`.

Round 4B.2 extracted a shared frontend overlay lifecycle manager and migrated the four highest-risk overlay workflows. The overlay manager lives at `app/jordana_invoice/static/js/overlay_manager.js` and is loaded as a classic IIFE script before `review.js`. It assigns `window.JordanaOverlay.create(config)` which returns an overlay controller with `open`, `close`, `beginPending`, `endPending`, `isPending`, `isOpen`, `getReturnFocus`, and `setReturnFocus` methods. The manager coordinates focus capture/restoration, ARIA state synchronization, body scroll lock (reference-counted for nested overlays), keydown handler binding (once per controller, not duplicated on repeated opens), and pending-state button disabling. Four workflows were migrated: review approval (`approvalState`), duplicate confirmation (`duplicateState`), restore candidate (`restoreState`), and billing relationship wizard (`billingWizardState`). Each workflow owns its own state object with `submitting` and `candidateId` fields. No backend contracts, endpoint paths, payload keys, response envelopes, business rules, visual design, or workflow terminology were changed. No backend files, launcher files, or schema were modified. Focused tests are in `tests/test_overlay_manager.py` (111 tests).

Round 4A.2.1 fixed the restore false-failure behavior. Previously, `restore_candidate` committed the restore, then called `refresh_candidate_suggestions` which could raise an unsafe exception. The HTTP handler sanitized this to a 400 response even though the restore had already succeeded. The fix isolates the refresh as a secondary operation: if it raises, the committed restore is preserved and the response includes an additive `warning` field (`"Candidate was restored, but suggestions could not be refreshed."`) on the normal 200 success response. This follows the same success-with-warning convention used by the approve endpoint when invoice staging warns. No endpoint paths, payload keys, schemas, or business rules were changed. Regression tests are in `tests/test_request_validation.py` and `tests/test_routine_queue_filter.py`.

## Verification Baseline

The last documented full-suite baseline is:

```text
2,072 passing
11 skipped
0 failures
```

That number is a historical baseline, not a substitute for running the current suite. Later commits may add tests. Before completing any new code round, run the current focused tests and full suite locally and report the exact results.

Acceptance testing must use:

```bash
scripts/run_acceptance_test.sh
```

Do not manually recreate that workflow with ad hoc commands against the operational database.

## Calendar Synchronization

The app uses one intelligent synchronization path:

- no successful cursor: full read of staged Google Sheet evidence
- successful cursor: incremental sync
- startup: sync begins after launch
- while open: incremental sync repeats every 15 minutes
- manual Calendar Import action: **Sync Calendar**
- recovery-only full reread: **Rebuild Calendar Data from Sheet**, with confirmation and a private backup

The cursor is composite: `ingested_at` plus `snapshot_key`.

The app does not trigger the iPhone Shortcut. The Shortcut must separately stage Apple Calendar snapshots into Google Sheets.

All non-all-day events from all calendars are captured. `Jordana Work` is a classification preference, not an ingestion filter.

Normal capture-window labels are:

- `past_3_days`
- `next_7_days`

Deprecated labels remain readable for compatibility. The June 1–14, 2026 backfill label remains supported for its one-time purpose.

Manual commands:

```bash
PYTHONPATH=app .venv/bin/python -m jordana_invoice sync
PYTHONPATH=app .venv/bin/python -m jordana_invoice sync-status
```

Dry run:

```bash
PYTHONPATH=app .venv/bin/python -m jordana_invoice sync --dry-run
```

## Review Workflow

Routine review focuses on:

- Participants
- Bill To
- Duration
- Session type
- derived time category
- Rate
- Payment Handling
- Approve

Time category is derived from the authoritative calendar date and start time. It is not a normal editable control.

The five active billing session types are:

1. Psychotherapy Session
2. Psychotherapy Session / House Call
3. Psychotherapy Session / Weekend
4. Psychotherapy Session / Evening
5. Custom

The focused review overlay uses independent actions:

- **Save Client(s)**
- **Save Bill To**
- **Save Session Draft**
- **Approve Session**

No section save approves a session.

Successful approval prevents double submission, clears stale state, closes the overlay, refreshes or removes the item, restores focus, and shows confirmation. Invoice-staging warnings do not roll back approval.

Duplicate resolution uses **Confirm Duplicate & Next** and follows the same completed-action behavior.

## Billing Relationships

The visible concepts are:

- Who receives the invoice?
- Who are they paying for?
- Bill To
- Participants

The payer is not automatically covered. Session participants remain selectable but are not silently preselected. Changing payer type clears stale covered-client selections. Selected-client chips are the source of truth.

Saving persists immediately to SQLite. Reopening or refreshing review exposes the saved relationship. Jordana still confirms Bill To for each session.

Permanent deletion is intentionally absent; use deactivate and reactivate. Approved sessions are never silently rewritten.

## Rates

Rate priority is:

1. session-specific approved override
2. exact participant-combination exception
3. person exception
4. billing-relationship exception
5. global or default rate

When changing a rate, distinguish:

- this session only
- future sessions for one person
- future joint sessions for the exact participant combination

Approved session rates are frozen.

## Invoices

Implemented invoice behavior includes:

- monthly draft staging by Bill To and billing month
- supplement sequencing
- draft line correction audit
- optimistic draft revision locking
- draft HTML and PDF preview
- two-step finalization
- immutable finalized snapshots and PDFs
- void and reissue under a new number
- prior-balance and account-summary snapshots
- filing-owner selection
- optional per-invoice insurance coding
- invoice library search, filters, and pagination

The application does not yet send invoices by email or mail.

## Payments

Implemented payment behavior includes:

- payment ledger
- allocations
- allocation reversals
- payment voiding
- apply available funds
- payment correction history
- manual immutable receipts
- Outstanding, Paid, and All Payments views

New paid-at-session approvals create or validate one posted payment and allocation transactionally and idempotently, then skip monthly invoice staging.

The legacy paid-at-session backfill analyzer is dry-run only. There is no historical backfill apply mode.

## Candidate Identity And Duplicate Repair

Candidate identity matching uses:

1. exact calendar event ID
2. exact fingerprint
3. conservative exact structural matching

Exact aliases are stored for future resolution.

Duplicate repair supports:

- dry-run planning
- explicit apply confirmation
- verified operational-database backup before writes
- idempotent apply
- explicit reversal confirmation
- reversal only when current state still matches the repair-applied state
- refusal after later edits make reversal unsafe
- protection of approved, invoiced, paid, audited, and raw-evidence records

Do not run duplicate repair against the operational database without reviewing the plan and confirming a current backup.

## Current Known Limitations

- no invoice email or mail delivery and tracking
- no legacy paid-at-session backfill apply mode
- no credits, refunds, or write-offs
- no automated multi-invoice payment allocation
- no formal reconciliation or month-close workflow
- no polished production dashboard
- no formal client-versus-non-client schema distinction
- no automatic payer classification
- no permanent billing-relationship deletion

## Demo And Recovery Safety

Create only an isolated fictional demo database:

```bash
scripts/create_demo_database.sh
PYTHONPATH=app .venv/bin/python -m jordana_invoice --db data/demo/jordana_demo.sqlite3 serve-review
```

Never import demo rows into the operational database.

CSV import is for testing or emergency raw-evidence recovery. It does not replace the transferred operational database or reconstruct prior reviewed billing state.

## Start The Review UI

```bash
PYTHONPATH=app .venv/bin/python -m jordana_invoice \
  --db data/jordana_invoice.sqlite3 \
  serve-review
```

Open:

```text
http://127.0.0.1:8765/review
```

## Privacy And Git Safety

Never commit:

- live SQLite databases
- calendar exports
- client spreadsheets
- invoices or receipts
- credentials, API keys, or `.env`
- private branding assets
- logs with names
- private screenshots
- database backups
- real diagnosis codes

At the end of every completed code round:

1. update documentation
2. run focused tests
3. run the full suite
4. distinguish new failures from pre-existing failures
5. run privacy and Git-safety checks
6. inspect the complete diff
7. confirm no private files are tracked or staged
8. commit and push only sanitized changes
9. verify `HEAD == origin/main`
10. verify a clean worktree

Required checks include:

```bash
scripts/git_safety_check.sh
scripts/privacy_check.sh
```

## Completion Report

Every completed implementation round must report:

- root cause or requested goal
- files changed
- tests and exact results
- documentation changed
- privacy and Git-safety results
- commit hash
- final branch synchronization and worktree state
- known limitations
- any local-only private files requiring secure transfer
