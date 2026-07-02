# Handoff To Jordana Mac

This is the continuation contract for installing, verifying, or continuing
development on Jordana's computer. Read `AGENTS.md` and
`docs/CURRENT_IMPLEMENTATION_STATUS_AND_HANDOFF.md` first.

- **Latest code commit reviewed:** `d99a42263cd48b0c454b1de7fdc5dd01db02ee5a`
- **Latest recorded full-suite verification commit:** `033d2634fa33688f686c66160ec0eff3e71bf8d7`
- **Recorded baseline:** 2,585 passing, 11 skipped, 0 failures (`2596` tests run)
- **Migration head:** `015_duplicate_repair_reversal_state`

## Source Of Truth

1. latest explicit approved decision
2. current repository, schema, migrations, tests, and documentation
3. `docs/CURRENT_IMPLEMENTATION_STATUS_AND_HANDOFF.md`
4. current private configuration and operational data
5. older historical notes

Do not revive obsolete schemas, terminology, editable time-category controls,
side-inspector behavior, or abandoned workflows.

## Before Any Change

```bash
pwd
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
git log -1 --oneline
```

- clean and synchronized: proceed
- dirty: stop and inspect every change
- behind: `git pull --ff-only`
- diverged: stop and investigate
- locally ahead from a completed prior round: inspect and push; never reset automatically

Read the applicable docs before editing, especially `AGENTS.md`, the current
status handoff, private-data transfer, fresh install, production packaging,
acceptance, write endpoint contracts, schema audit, review workflow, invoice
lifecycle, and invoice template.

## Production Handoff Path

1. Build a versioned DMG from a clean synchronized checkout.
2. Transfer the DMG and checksum separately from all private production data.
3. Verify the DMG checksum and private transfer checksums.
4. Place the verified operational SQLite database and private configuration in their documented Application Support locations.
5. Open the DMG and run `Install Jordana Billing.app`.
6. Launch `~/Applications/Jordana Billing.app` by double-clicking.
7. Complete the clean-Mac checklist and Jordana workflow smoke path.

The V1 release requires the Python major/minor version recorded in
`release_manifest.json` during one-time installation. Daily use does not require
Terminal, Git, GitHub, PyPI, pip, or a source checkout.

A production handoff must include the operational SQLite database. Google
Sheets preserves raw calendar evidence but cannot reconstruct reviewed people,
billing relationships, approved sessions, invoices, payments, receipts, or
audit history.

Never transfer private production files through GitHub.

## Installed Locations

Application:

```text
~/Applications/Jordana Billing.app
```

Private operational state:

```text
~/Library/Application Support/Jordana Billing/
  config/.env
  data/jordana_invoice.sqlite3
  backups/
  logs/
  runtime/
```

User-facing outputs:

```text
~/Documents/Jordana Billing/Session Lists/
~/Documents/Jordana Billing/Client Files/
```

The setup and launcher must preserve the operational database, create verified
backups before migrations or updates, and apply only additive migrations. A
missing production database is an error; daily launch must never create a blank
replacement.

## Current Installer Status

Brooke reports that the current one-click installer successfully completed an
install and launch on a test Mac. The native setup app, offline wheelhouse,
private runtime, installed launcher, Application Support storage, and Documents
output folders are implemented.

The full acceptance evidence record is still incomplete. Before final handoff,
record the release filename and commit, checksum, Mac and macOS version, Python
version, Gatekeeper behavior, restart, duplicate launch, port-conflict behavior,
reinstall preservation, and operational smoke path in
`docs/TEST_MAC_ACCEPTANCE.md`.

### Installer Rollback Caution

The installer safely stages a replacement app in a temporary path, but it
currently removes the existing app before final verification. Private data is
preserved, but a verification failure can leave the prior working app
unavailable. The next packaging round should retain the previous app until the
replacement verifies and restore it automatically on failure.

### Installer Version Caution

The installer currently installs `jordana-invoice==0.1.0` directly. This
matches the current project version, but future releases should read the
expected package version from `release_manifest.json`.

## Configuration

The native setup app writes private configuration to:

```text
~/Library/Application Support/Jordana Billing/config/.env
```

Required values:

- `JORDANA_APPS_SCRIPT_URL`
- `JORDANA_INGEST_API_KEY`

The key field is hidden and the file permissions must be `600`. Existing
configuration is preserved on reinstall. Never commit or paste real secrets,
Script Properties, spreadsheet IDs, or private paths into docs, screenshots,
logs, GitHub, or chat.

The release payload includes `scripts/install_release.sh`; the native setup app
is the user-facing entrypoint and delegates to the release installer workflow
inside the verified payload.

## Daily Launch Contract

The installed launcher:

- validates the installed runtime
- validates private configuration
- opens the SQLite database read-only for integrity checks before startup
- refuses to create a blank production database
- reuses a verified healthy existing Jordana server
- refuses to kill or reuse an unrelated process on port `8765`
- applies only safe pending migrations through the app startup contract
- starts the local review server and opens the browser after health readiness
- does not run pip, Git, PyPI, editable installs, or dependency repair

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

The focused review overlay uses independent **Save Client(s)**, **Save Bill To**,
**Save Session Draft**, and **Approve Session** actions. Section saves do not
approve a session. Successful approval prevents duplicate submission, closes
and unmounts the overlay, clears stale state, refreshes the queue, restores
focus, and treats invoice-staging warnings as warnings rather than approval
failures.

Duplicate resolution uses **Confirm Duplicate & Next** with the same completed
action behavior.

## Billing Relationships And Rates

Visible concepts are Who receives the invoice, Who are they paying for, Bill
To, and Participants. The payer is not automatically covered. Selected-client
chips are the source of truth. Saving persists immediately to SQLite. Approved
sessions are never silently rewritten.

Rate priority:

1. session-specific approved override
2. exact participant-combination exception
3. person exception
4. billing-relationship exception
5. global/default rate

Approved session rates remain frozen.

## Invoice Behavior

Implemented:

- monthly staging by Bill To and billing month
- draft correction audit and optimistic locking
- two-step finalization
- one canonical ReportLab renderer for draft and final PDFs
- immutable finalized snapshots and PDFs
- filing-owner selection and client/month folders
- prior-balance and account-summary snapshots
- optional invoice-specific insurance coding
- void and reissue
- searchable invoice library

Commit `d99a42263cd48b0c454b1de7fdc5dd01db02ee5a`
fixes the post-finalize user flow. Successful finalization returns a versioned
`final_pdf_url` and opens the canonical stored PDF with no-cache headers. The
in-app HTML invoice card is not the authoritative proof of final layout.
Repeated finalize submissions return the existing immutable invoice and PDF
without regenerating or renumbering.

Review & Finalize now embeds the canonical draft PDF preview from
`POST /api/invoices/{id}/draft-pdf` before Jordana confirms finalization. The
confirmation screen no longer uses the duplicated HTML invoice card as the
approval visual. The readiness endpoint is side-effect free and does not save
draft edits, assign numbers, write PDF metadata, or change invoice status.

The application does not yet send invoices by email or mail.

### Finalization Transaction Caution

`finalize_invoice()` starts an immediate transaction and calls
`synchronize_draft_delivery_method()`, which can commit internally when it fills
a stale delivery method. Make transaction ownership explicit and add rollback
coverage before describing every finalization failure path as fully atomic.

## Payment Behavior

Implemented:

- payment ledger and allocations
- paid-at-session approval workflow
- apply available funds
- allocation reversal and payment voiding
- correction history
- manual immutable receipts
- Outstanding, Paid, and All Payments views

The legacy historical paid-at-session analyzer remains dry-run only.

## Verification

Latest recorded full suite:

```text
Commit: 033d2634fa33688f686c66160ec0eff3e71bf8d7
Ran 2596 tests in 180.798s
OK (skipped=11)
```

The newer code commit `d99a42263cd48b0c454b1de7fdc5dd01db02ee5a`
adds focused finalization and PDF tests but does not record a newer full-suite
total. Rerun the suite before final release.

```bash
PYTHONPATH=app .venv/bin/python -m unittest discover -s tests
scripts/run_acceptance_test.sh
scripts/git_safety_check.sh
scripts/privacy_check.sh
```

Acceptance testing must use the provided temporary-database script. Never run
ad hoc acceptance imports against the operational database.

## Known Limitations

- no invoice delivery and tracking
- no historical paid-at-session apply mode
- no credits, refunds, or write-offs
- no automated multi-invoice allocation
- no formal reconciliation or month-close workflow
- no polished production dashboard
- no notarized installer
- matching Python runtime required for V1 installation
- full clean-Mac evidence record incomplete
- installer replacement not yet rollback-safe
- finalization transaction ownership needs the narrow fix above
- no formal client-versus-non-client distinction
- no automatic payer classification
- no permanent billing-relationship deletion by design

## Privacy And Completion

Never commit live SQLite databases, calendar exports, private spreadsheets,
invoices, receipts, credentials, `.env`, private branding, logs with names,
screenshots, backups, or real diagnosis codes.

Every completed code round must report:

- goal or root cause
- files changed
- focused and full test results
- documentation changed
- privacy and Git-safety results
- commit hash
- branch synchronization and worktree state
- known limitations
- local-only private files requiring secure transfer

## Immediate Handoff Order

1. Fix finalization transaction ownership.
2. Make installer replacement rollback-safe and manifest-version driven.
3. Rerun the full suite on the latest code head.
4. Complete and record clean-Mac acceptance.
5. Build the final release from a clean synchronized checkout.
6. Transfer private production data separately and verify it.
7. Run the complete operational smoke path: launch, sync, review, approve,
   preview, finalize, open canonical PDF, record payment, restart, and reopen.
