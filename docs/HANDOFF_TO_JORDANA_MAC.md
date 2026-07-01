# Handoff To Jordana Mac

This is the continuation contract for installing, verifying, or continuing
development on Jordana's computer. Read `AGENTS.md` and
`docs/CURRENT_IMPLEMENTATION_STATUS_AND_HANDOFF.md` before making changes.

**Last code verification commit:** `033d2634fa33688f686c66160ec0eff3e71bf8d7`
**Recorded verification date:** 2026-07-01
**Recorded full-suite baseline:** 2,585 passing, 11 skipped, 0 failures (`2596` tests run)

## Goal

Continue the local-first invoice system without relying on chat history while
preserving all private operational data and reviewed billing history.

## Source Of Truth

Use this order:

1. latest explicit approved decision
2. current repository, schema, migrations, tests, and documentation
3. `docs/CURRENT_IMPLEMENTATION_STATUS_AND_HANDOFF.md`
4. current private local configuration and transferred operational data
5. older historical notes

Do not revive obsolete schemas, terminology, side-inspector UI behavior,
editable time-category controls, or abandoned workflows.

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

1. Build a versioned DMG release artifact from the development Mac.
2. Transfer the release DMG and private production data separately using `docs/PRIVATE_DATA_TRANSFER.md`.
3. Verify the release checksum and the private transfer manifest/checksums.
4. Open the DMG on Jordana's Mac.
5. Double-click `Install Jordana Billing.app`.
6. Enter the private Apps Script URL and hidden ingest API key.
7. Install the app. For a true clean-start test, explicitly confirm clean-start database initialization.

The V1 release requires the Python major/minor version recorded in the release's
`release_manifest.json`. The installer must find that compatible version before
installation because the shipped wheelhouse can contain Python-specific wheels.
Brooke may need to install the matching Python version once before handing the
computer to Jordana. Normal daily app use does not require Terminal, Git,
GitHub, PyPI, pip, or a source checkout.

`scripts/install_release.sh` is the production installer for release artifacts.
The older `scripts/setup_jordana_mac.sh` path is retired. `scripts/bootstrap.sh`
is a development-checkout bootstrap and source launcher, not the daily
production launch path.

A production handoff must include the operational SQLite database. Google
Sheets contains raw calendar evidence but cannot reconstruct reviewed people,
billing relationships, approved sessions, invoices, payments, receipts, or
audit history.

Never transfer private production files through GitHub.

Typical private local state lives under
`~/Library/Application Support/Jordana Billing/` and includes:

```text
config/.env
data/jordana_invoice.sqlite3
backups/
logs/
runtime/
```

Installed user-facing generated files live under
`~/Documents/Jordana Billing/Session Lists/` and
`~/Documents/Jordana Billing/Client Files/`. They remain outside Git and
outside the app bundle.

The setup and launcher flows must preserve an existing database, create a
verified private backup before pending migrations, and apply only additive
migrations. They must never delete, recreate, or silently replace the
operational database.

The double-click app uses `Contents/Resources/launch_installed_app.sh`. It
validates private config, verifies that the Application Support SQLite database
exists and can be opened read-only, reuses a verified already-running Jordana
Billing server, and refuses to kill or reuse an unrelated process on port
`8765`. It does not run pip, Git, PyPI, editable installs, dependency repair, or
blank-database creation during normal launch.

## Current Installer Validation Status

Brooke reports that the current one-click installer successfully completed an
install and launch on a test Mac. This is a meaningful handoff milestone and
replaces the older statement that the one-click launcher was unfinished.

The successful test is not yet a complete recorded acceptance run. Before the
Jordana production handoff, record the release filename, checksum, Mac used,
Python version, Gatekeeper behavior, restart result, duplicate-launch result,
reinstall result, and any remaining checklist scenarios in
`docs/TEST_MAC_ACCEPTANCE.md`.

Current packaging caution: the installer safely builds the replacement app in
a temporary `.installing` path, but then removes the existing app before final
verification. A verification failure after that swap can leave the previous
working app unavailable even though private configuration, SQLite data,
backups, reports, invoices, and receipts remain preserved. A narrow hardening
round should retain the previous app until the new app passes verification and
restore it on failure.

## Configuration

For a production handoff, create or verify `.env` through
`Install Jordana Billing.app` without exposing its values. The setup app stores
the private config at
`~/Library/Application Support/Jordana Billing/config/.env`, hides API-key
input, and sets file permissions to `600`. The CLI helper
`scripts/create_private_config.sh` remains available for Brooke/support use
inside the payload, but it is not the normal Jordana setup path.

Required production release configuration includes:

- `JORDANA_APPS_SCRIPT_URL`
- `JORDANA_INGEST_API_KEY`

Do not commit or paste the real `.env`, credentials, Script Properties,
spreadsheet IDs, or private paths into documentation, screenshots, logs,
GitHub, or chat.

## Current Database State

The current migration head is:

```text
015_duplicate_repair_reversal_state
```

Migrations `001` through `015` are registered.
`app/jordana_invoice/db.py` is the executable migration source of truth.

Migration safety includes:

- database locking
- WAL-safe SQLite backup
- backup integrity verification
- transactional migration
- restore on failure
- no request-path migrations
- no deletion or reset of the operational database

See `docs/SCHEMA_AUDIT.md` for the current migration list and table
responsibilities.

See `docs/WRITE_ENDPOINT_CONTRACTS.md` for a complete inventory of every
backend write HTTP endpoint and its current request/response contract.
Characterization tests are in `tests/test_write_endpoint_contracts.py`.

Round 4A.2 added explicit request-parsing and validation helpers for the four
highest-risk review write endpoints. The helpers are in
`app/jordana_invoice/request_validation.py` and preserve existing endpoint
paths, payload keys, response shapes, status codes, and business rules.

Round 4B.1 extracted the shared frontend API utility into
`app/jordana_invoice/static/js/api.js`. It preserves write-token behavior,
response parsing, warning handling, and sanitized UI errors.

Round 4B.1.1 fixed the write-token load-order regression by reading
`window.__JORDANA_BOOTSTRAP__?.writeToken` at write-request time.

Round 4B.2 extracted a shared frontend overlay lifecycle manager into
`app/jordana_invoice/static/js/overlay_manager.js`. Review approval, duplicate
confirmation, restore, and the billing relationship wizard use managed pending
state, focus restoration, ARIA synchronization, body scroll locking, and
single-submit behavior.

Round 4A.2.1 fixed restore false-failure behavior. A secondary suggestion
refresh failure now returns success with an additive sanitized warning after
the committed restore instead of reporting the completed restore as failed.

## Verification Baseline

The current recorded full-suite baseline for code commit
`033d2634fa33688f686c66160ec0eff3e71bf8d7`, verified 2026-07-01, is:

```text
Ran 2596 tests in 180.798s
OK (skipped=11)
```

Exact counts: 2,585 passing, 11 skipped, 0 failures.

This documentation-only reconciliation does not claim that the suite was rerun
after documentation edits. Before completing any new code or handoff round,
run the current focused tests and full suite locally and report the exact
passing, skipped, and failure counts with the commit hash and verification
date.

Acceptance testing must use:

```bash
scripts/run_acceptance_test.sh
```

Do not manually recreate that workflow with ad hoc commands against the
operational database.

## Calendar Synchronization

The app uses one intelligent synchronization path:

- no successful cursor: full read of staged Google Sheet evidence
- successful cursor: incremental sync
- startup: sync begins after launch
- while open: incremental sync repeats every 15 minutes
- manual Calendar Import action: **Sync Calendar**
- recovery-only full reread: **Rebuild Calendar Data from Sheet**, with confirmation and a private backup

The cursor is composite: `ingested_at` plus `snapshot_key`.

The app does not trigger the iPhone Shortcut. The Shortcut must separately
stage Apple Calendar snapshots into Google Sheets.

All non-all-day events from all calendars are captured. `Jordana Work` is a
classification preference, not an ingestion filter.

Normal capture-window labels are:

- `past_3_days`
- `next_7_days`

Deprecated labels remain readable for compatibility. The June 1–14, 2026
backfill label remains supported for its one-time purpose.

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

Time category is derived from the authoritative calendar date and start time. It
is not a normal editable control.

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

Successful approval prevents double submission, clears stale state, closes the
overlay, refreshes or removes the item, restores focus, and shows confirmation.
Invoice-staging warnings do not roll back approval.

Duplicate resolution uses **Confirm Duplicate & Next** and follows the same
completed-action behavior.

## Billing Relationships

The visible concepts are:

- Who receives the invoice?
- Who are they paying for?
- Bill To
- Participants

The payer is not automatically covered. Session participants remain selectable
but are not silently preselected. Changing payer type clears stale
covered-client selections. Selected-client chips are the source of truth.

Saving persists immediately to SQLite. Reopening or refreshing review exposes
the saved relationship. Jordana still confirms Bill To for each session.

Permanent deletion is intentionally absent; use deactivate and reactivate.
Approved sessions are never silently rewritten.

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
- one canonical ReportLab renderer for draft and finalized PDFs
- immutable finalized snapshots and PDFs
- void and reissue under a new number
- prior-balance and account-summary snapshots
- filing-owner selection
- optional per-invoice insurance coding
- invoice library search, filters, and pagination

Structured diagnosis codes are optional and invoice-specific. They may be
stored only when Jordana intentionally enters or approves them for insurance
billing or reimbursement. They must never be inferred from calendar text,
participant names, session descriptions, or other application data, should not
appear on ordinary self-pay invoices, and finalized diagnosis-code snapshots
remain frozen. Corrections after finalization use the existing correction,
void, or reissue process.

The application does not yet send invoices by email or mail.

### Current Finalization Transaction Caution

`finalize_invoice()` begins an immediate SQLite transaction and then calls
`synchronize_draft_delivery_method()`. That helper can commit internally when
it fills a stale delivery method from the active billing setup. If that path is
used, part of the draft mutation can commit before the rest of finalization
completes. The next narrow code round should make transaction ownership
explicit and add a regression test proving a failed finalization leaves the
entire draft unchanged.

This finding does not mean finalized invoice snapshots or PDFs are currently
mutable. It is a transaction-boundary defect in a failure path and should be
fixed before describing finalization as fully atomic in every case.

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

New paid-at-session approvals create or validate one posted payment and
allocation transactionally and idempotently, then skip monthly invoice staging.

The legacy paid-at-session backfill analyzer is dry-run only. There is no
historical backfill apply mode.

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

Do not run duplicate repair against the operational database without reviewing
the plan and confirming a current backup.

## Current Known Limitations

- no invoice email or mail delivery and tracking
- no legacy paid-at-session backfill apply mode
- no credits, refunds, or write-offs
- no automated multi-invoice payment allocation
- no formal reconciliation or month-close workflow
- no polished production dashboard
- no notarized installer
- matching Python major/minor runtime required for V1 installation
- one-click install has succeeded, but the full clean-Mac acceptance evidence record is not yet complete
- installer replacement is not yet rollback-safe after the existing app is removed
- invoice finalization transaction ownership needs the narrow delivery-method synchronization fix described above
- installer currently installs `jordana-invoice==0.1.0` directly rather than deriving the package version from the release manifest
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

CSV import is for testing or emergency raw-evidence recovery. It does not
replace the transferred operational database or reconstruct prior reviewed
billing state.

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

## Immediate Handoff Order

1. Fix finalization transaction ownership.
2. Make installer replacement rollback-safe and derive its package version from the manifest.
3. Record the successful one-click install details and finish the remaining clean-Mac checklist scenarios.
4. Build the final release from a clean synchronized checkout.
5. Transfer private production data separately and verify checksums and database integrity.
6. Run Jordana's complete operational smoke path before handoff: launch, sync, review, approve, preview, finalize, open PDF, record payment, restart, and reopen records.

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
