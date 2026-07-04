# Jordana Invoice System

Local-first calendar evidence importer, billing review workflow, invoice system, and payment ledger for Jordana's single-user macOS workflow.

The current implementation imports Apple Calendar snapshot rows through Google Sheets, preserves the raw evidence, collapses duplicate event versions, proposes classifications, parses Jordana's shorthand, and keeps ambiguous records reviewable. Approved sessions can be staged into draft invoices, finalized with immutable snapshots and local PDFs, voided and reissued, and tracked through payments, allocations, corrections, receipts, prior balances, filing ownership, and optional invoice-specific insurance coding.

This is not a general multi-user billing platform. It is implemented and tested locally, with clean-Mac acceptance and final production handoff still tracked separately. Invoice delivery by email/mail, credits/refunds/write-offs, automated multi-invoice allocation, payment reconciliation, month-close, and a polished dashboard remain known limitations.

## Current Scope

- Authenticated Google Apps Script sync
- Normal Shortcut capture window of 3 days back and 7 days ahead
- One-time June 1-14, 2026 backfill capture label support
- Google Sheets CSV importer for testing and emergency recovery
- Raw snapshot preservation
- Completed-run validation from capture windows
- Duplicate collapse into event candidates
- Conservative shorthand parser
- Structured calendar titles with optional title time and Cancelled/No Show status
- Source-calendar classification and review filtering
- Event classification
- People/account/billing-party data model
- Session participant modeling
- Simplified Participants and Bill-to review workflow
- Service mode, rate group, evening, and weekend categorization
- Effective-dated rate rules
- Person-specific and exact participant-combination rate exceptions
- Historical approved-rate snapshots
- Structured review-state engine
- Section-level saves for people, relationships, billing, and session drafts
- Human-readable person codes and account codes
- Billing Relationships and Clients CRM views
- Guided billing relationship creation wizard ( Rounds 1–3, merged)
- Relationship editing: invoice recipient, covered clients, billing delivery
- Deactivate and reactivate billing relationships (no permanent deletion)
- Session Review integration: attach billing relationship to a review candidate
- Exact active duplicate prevention for billing relationships, using payer identity plus normalized covered-client set
- Shared Bill To delivery data reused across a payer's valid active relationships
- Read-only duplicate analysis for legacy relationship and payer-record conflicts
- Initial client, alias, rate, session, review, and audit tables
- Local CSV reports after successful sync
- Acceptance report for June-style data
- Isolated sanitized demo database for review testing
- Private local business profile and logo reference
- Normalized service catalog
- Draft, finalized, and void invoice lifecycle
- Transaction-safe numbering and immutable invoice snapshots
- Local multi-page PDF generation
- Shared invoice/payment financial summaries for draft value, monthly finalized invoices, monthly payment receipts, and outstanding balance

## Quick Start

For production or clean-Mac testing, build and install a versioned offline DMG release artifact:

```bash
scripts/build_release.sh
```

The release DMG contains `Install Jordana Billing.app` with the release payload
embedded inside the setup app. The setup app collects the Apps Script URL and
ingest API key in a native macOS window, writes private configuration under
`~/Library/Application Support/Jordana Billing/`, asks before initializing a
clean-start database, installs `Jordana Billing.app` to `~/Applications`, and
runs install verification. Daily double-click launch validates and starts the
installed app only; it does not run pip, repair `.venv`, install editable
packages, use Git, access PyPI, or create a blank production database. See
`docs/PRODUCTION_PACKAGING.md` and `docs/TEST_MAC_ACCEPTANCE.md`.

The V1 release installer requires the Python major/minor version recorded in
the release's `release_manifest.json`; Brooke may need to install that matching
Python once before handing the computer to Jordana. Normal daily app use does
not require Terminal, Git, GitHub, PyPI, pip, or a source checkout.

On the clean Mac, download the private release DMG and matching checksum, verify
the checksum, open the DMG, and double-click `Install Jordana Billing.app`.
Never upload `.env`, databases, real diagnosis codes, or real secrets to
GitHub.

For local development checkouts only, `scripts/bootstrap.sh` remains available
to create or repair the repo-local `.venv` and launch from source.

For development-only experiments with a disposable database:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
jordana-invoice --db data/jordana_invoice.sqlite3 init-db
```

To run an acceptance test without touching the operational database:

```bash
scripts/run_acceptance_test.sh
```

The acceptance report is written to `data/acceptance_report.md`.


## Automated Sync

Create a real `.env` from `.env.example` and fill in:

```bash
JORDANA_APPS_SCRIPT_URL=
JORDANA_INGEST_API_KEY=
JORDANA_DATABASE_PATH=__PROJECT_DIR__/data/jordana_invoice.sqlite3
JORDANA_REPORTS_DIR=__PROJECT_DIR__/Reports
JORDANA_INVOICES_DIR=Invoices
JORDANA_RECEIPTS_DIR=Receipts
JORDANA_PREFERRED_WORK_CALENDAR=Jordana Work
# Optional backup directory. Defaults to ~/.jordana_invoice/backups if unset.
JORDANA_BACKUP_DIR=
```

Then run:

```bash
PYTHONPATH=app .venv/bin/python -m jordana_invoice sync
PYTHONPATH=app .venv/bin/python -m jordana_invoice sync-status
```

`sync` chooses the correct mode automatically. If there is no successful
`google_calendar_snapshots` cursor, it performs the initial full Sheet sync. If
a successful cursor exists, it performs an incremental sync. The explicit full
variant is reserved for recovery:

```bash
PYTHONPATH=app .venv/bin/python -m jordana_invoice sync --dry-run
PYTHONPATH=app .venv/bin/python -m jordana_invoice sync --full
```

`sync --full` asks for all available raw staged rows but still does not duplicate
snapshots because `snapshot_key` is unique locally. Successful syncs store a
durable cursor only after fetched rows, normalization, review updates, and
report writes all succeed.

When the local review app launches, it opens normally and then starts the same
intelligent sync in the background. A first installation therefore performs a
full Sheet sync automatically. Later launches perform incremental sync. While
the app remains open, it repeats incremental sync every 15 minutes by default:

```bash
JORDANA_CALENDAR_SYNC_INTERVAL_MINUTES=15
```

The app never triggers the iPhone Shortcut. The Shortcut must still run
separately to place new Calendar data into Google Sheets.

Calendar integration maintenance:

```bash
scripts/validate_calendar_integration_config.py
scripts/generate_calendar_shortcut_specs.py
scripts/configure_apps_script.py
```

See `docs/CALENDAR_INTEGRATION.md` for the Apps Script Script Properties, Shortcut payload labels, June backfill scope, and remaining device-side deployment steps.

After a successful sync the app updates:

- `Reports/Jordana_Client_Sessions_<YEAR>.csv`
- `Reports/Jordana_Client_Summary_<YEAR>.csv`
- `Reports/Jordana_Session_Log_<YEAR>.csv`
- `Reports/Jordana_All_Appointments.csv`

Automatic sync uses the current year in `America/New_York` for the annual
session reports. The all-appointments report remains continuously refreshed
without a year in the filename.

Installed releases set user-facing output defaults to
`~/Documents/Jordana Billing/Session Lists` for CSV reports and
`~/Documents/Jordana Billing/Client Files` for finalized invoice and receipt
PDFs. Operational data remains under
`~/Library/Application Support/Jordana Billing`.

The reports are written atomically so a failed write does not leave a partial CSV behind.

## Review UI

Run the local review workbench:

```bash
PYTHONPATH=app .venv/bin/python -m jordana_invoice --db data/jordana_invoice.sqlite3 serve-review
```

Open:

```text
http://127.0.0.1:8765/review
```

The review UI reads and writes SQLite through local API routes. It does not edit CSV files.

The Review Queue resolves one calendar event at a time. Quick fixes stay in the inspector. Deeper people/account editing happens in the CRM views:

- `/review`
- `/clients`
- `/people`

The `Calendar Import` sidebar screen shows local sync status and a single
`Sync Calendar` action. That button pulls snapshot rows already staged by the
iPhone Shortcut through Apps Script, automatically choosing initial full sync or
incremental sync from durable cursor state. It does not trigger the Shortcut and
does not edit Apple Calendar. The Advanced `Rebuild Calendar Data from Sheet`
action is for recovery only; it creates a private SQLite backup and rereads all
staged Sheet evidence idempotently.

For raw-row recovery where the Sheet evidence is already preserved in SQLite but
some derived candidates or sessions are missing, use the in-app
`Reconciliation` screen. Choose a month, run `Dry Run`, review missing sessions,
extra sessions, possible duplicates, edited event versions, excluded/non-client
billing issues, and approved records requiring manual review, then use
`Apply Safe Recovery` only when the dry-run output is understood.

The same recovery path is also available from the CLI. Run dry-run first:

```bash
PYTHONPATH=app python3 -m jordana_invoice --db data/jordana_invoice.sqlite3 calendar-reconcile --dry-run --month 2026-06
```

Apply only after reviewing the dry-run summary:

```bash
PYTHONPATH=app python3 -m jordana_invoice --db data/jordana_invoice.sqlite3 calendar-reconcile --apply --month 2026-06 --confirm-apply APPLY_CALENDAR_RECONCILE
```

This replays existing `raw_calendar_snapshots` without inserting duplicate raw
evidence, creates a verified SQLite backup before applying, refreshes only
pending/unreviewed operational records from the newest event version, excludes
pending records whose newest evidence is non-client, and protects approved
sessions from silent rewrites.

For June 2026 recovery after installing the current test release:

1. Open `Jordana Billing`.
2. Click `Reconciliation`.
3. Select `June 2026`.
4. Click `Dry Run` and review all six buckets.
5. Click `Apply Safe Recovery` only after reviewing the dry-run output.
6. Confirm the page shows `Safe Recovery Summary` and a verified backup path.
7. Recovered missing sessions appear in `Review Queue` as pending review items and in `Sessions` when the date filter includes June, such as `All dates` or `Previous month`.
8. Resolve and approve recovered sessions before generating client reports or staging invoices for them.

Unresolved recovered rows and excluded/non-client rows stay out of Client
Sessions reports, Session Log, Client Summary, and invoice staging.

The `Sessions` sidebar screen is a read-only ledger built from the same appointment query used for `Reports/Jordana_All_Appointments.csv`, including unresolved and non-session calendar records.
Its review-status filter intentionally contains only `All` and `Needs Classification`.

The `Rate Card` sidebar screen supports global rates plus one-client, clients-together, and billing-relationship exceptions. Replace and End actions preserve rate-rule history, immediately refresh unapproved session suggestions, and never rewrite approved sessions or finalized invoices.

The `Payments` sidebar screen lists outstanding and paid finalized invoices, supports payment entry and corrections, and exposes a payment ledger. Its summary cards show finalized invoice value and posted payment receipts for a selected month plus the current outstanding balance. The `Invoices` screen shows the same shared finalized and outstanding totals alongside current draft value. Both screens use the same `/api/financial-summary` backend calculation so a future dashboard can reuse it without redefining accounting logic. Multi-invoice payment entry and formal reconciliation remain unfinished.

The inspector has independent saves for Participants, Bill To, and Session Draft. None of those saves approve a session automatically. Account and relationship controls remain available in the collapsed Advanced relationships and shared billing section.

Calendar filters can show all calendars, the preferred work calendar, other calendars, personal/admin calendars, and hidden calendars. Hidden means hidden from the normal queue only; records remain searchable and recoverable.

## Calendar Entry Standard

The Shortcut still imports all non-all-day events from all calendars. `Jordana Work` is a preferred classification signal, not an ingestion restriction. Normal capture uses `past_3_days` and `next_7_days`. Calendar start time remains authoritative, and title time is validation evidence only.

Preferred structured titles:

```text
Full Name | Minutes | Session Type
Full Name | Time | Minutes | Session Type
Full Name | Time | Minutes | Session Type | Cancelled
Full Name | Time | Minutes | Session Type | No Show
```

See `docs/CALENDAR_ENTRY_STANDARD.md` for the full standard.

## Sanitized Demo Data

Create and launch the isolated demo database:

```bash
scripts/create_demo_database.sh
PYTHONPATH=app .venv/bin/python -m jordana_invoice --db data/demo/jordana_demo.sqlite3 serve-review
```

The generated demo DB is ignored by Git and explicitly marked as demo mode, causing the UI to show `DEMO DATA - NOT FOR REAL BILLING`.

## Important Guardrails

- Calendar rows are evidence, not approved billing.
- Google Sheets is the cloud staging and audit layer; do not delete or alter source rows there.
- SQLite is the local application database.
- Ambiguous rows stay reviewable and reversible.
- Calendar import and review commands do not generate invoices automatically.
- Invoice staging/finalization, local PDF generation, immutable snapshots, payment ledger corrections, receipts, and optional invoice-specific insurance coding are implemented.
- No polished production dashboard is built yet.
- Do not store clinical notes, psychotherapy notes, narrative diagnoses, symptoms, medical histories, treatment plans, session-content notes, treatment summaries, or clinical interpretations beyond the raw calendar evidence already imported.
- Structured diagnosis codes may be stored only when Jordana intentionally enters or approves them for invoice-specific insurance billing or reimbursement documentation. They must never be inferred from calendar text, participant names, session descriptions, or other application data, should not appear on ordinary self-pay invoices, and real values must never be committed to GitHub, fixtures, screenshots, logs, demo data, examples, or documentation.
- Do not rewrite historical finalized invoice values when rates change later.
- Every approved session must store the actual charged amount; future rate rules must not rewrite it.
- Do not expose household/account labels as routine review fields when Participants and Bill to are enough.
- Store secrets only in `.env`; never paste the real API key into source files or docs.
- Do not make a permanent new client account just because two names appear in one calendar title.
- Do not generate a person code for a provisional parser-only name.
- Do not treat appointment status as payment status.
- Do not filter calendar data at Shortcut level; filter irrelevant records after ingestion.
- Do not transfer private data through GitHub.

## Project Layout

- `app/jordana_invoice/` - importer, parser, database schema, report builder, CLI.
- `app/jordana_invoice/static/` - local review UI.
- `launchd/` and `scripts/` - hourly macOS sync agent installer and remover.
- `docs/` - pipeline, shorthand, data model, and handoff notes.
- `scripts/build_release.sh` - builds the versioned production DMG artifact with pinned dependencies, an offline wheelhouse, release manifest, and checksummed DMG.
- `scripts/build_setup_wizard.sh` - builds the native no-Terminal setup app for the DMG.
- `scripts/install_release.sh` - one-time production installer copied into release artifacts.
- `scripts/create_private_config.sh` - support-only interactive private config helper.
- `scripts/launch_installed_app.sh` - daily installed-app launcher; no package installation or Git/PyPI access.
- `scripts/bootstrap.sh` - development-checkout bootstrap and source launcher.
- `scripts/setup_jordana_mac.sh` - retired non-destructive stub; not a production handoff path.
- `scripts/verify_install.sh` and `scripts/privacy_check.sh` - verification and safety utilities for Mac handoff.
- `packaging/macos/AppIcon-source.png` and `packaging/macos/AppIcon.icns` - approved launcher icon source and generated macOS icon.
- `data/samples/` - small June-style fixture for acceptance testing.
- `tests/` - parser tests for known shorthand examples.

## Acceptance Test

> **Never delete or overwrite the operational database before this step.**
> Use `scripts/run_acceptance_test.sh` which operates on a temporary database
> and never touches the live operational database.

Run:

```bash
PYTHONPATH=app .venv/bin/python -m unittest discover -s tests
scripts/run_acceptance_test.sh
```

Expected output:

1. Likely client sessions are listed with parsed client candidate, time, duration, and reasons.
2. Likely personal/admin/nonbillable events are listed separately.
3. Ambiguous rows are in the review section.
4. No invoices are generated.


## Schema Audit

See `docs/SCHEMA_AUDIT.md` for the current authoritative tables, legacy compatibility tables, known overlaps, and prerequisites before any destructive migration.

## Write Endpoint Contracts

See `docs/WRITE_ENDPOINT_CONTRACTS.md` for a complete inventory of every backend write HTTP endpoint, including method, path, handler, service calls, auth requirements, request fields, response shapes, error codes, affected database tables, idempotency, and existing test coverage. Characterization tests are in `tests/test_write_endpoint_contracts.py`.

## Downloadable Report Link

For a development checkout, the latest session export is:

```text
Reports/Jordana_Client_Sessions_<YEAR>.csv
```
