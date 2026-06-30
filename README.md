# Jordana Invoice System

Local-first calendar evidence importer, billing review workflow, and invoice prototype.

Phase 1 does **not** create final invoices. It imports Apple Calendar snapshot rows, preserves the raw evidence, collapses duplicate event versions, proposes classifications, parses Jordana's shorthand, and opens review items for anything uncertain.

Phase 1.1 removes the normal manual CSV step. The Apple Shortcut still writes to Google Sheets through Google Apps Script, but the Mac can now pull raw staged snapshots from the Apps Script endpoint into local SQLite.

Phase 2 strengthens the normalization layer. The current prototype adds invoice drafts, immutable finalization snapshots, numbering, void/reissue, history, and local PDFs on top of approved sessions. It does not implement invoice delivery. The payment ledger is now implemented with payment creation, allocation across invoice lines, reversal/void corrections, apply-available-funds, invoice payment history, and a tabbed Payments workspace. Credits, multi-invoice payments, reconciliation, and month-close workflows remain unfinished.

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

On the clean Mac, download the private GitHub pre-release DMG and matching
checksum, verify the checksum, open the DMG, and double-click
`Install Jordana Billing.app`. Never upload `.env` or real secrets to GitHub.

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
JORDANA_DATABASE_PATH=/Users/brookesnader/Documents/Jordana Billing/data/jordana_invoice.sqlite3
JORDANA_REPORTS_DIR=/Users/brookesnader/Documents/Jordana Billing/Reports
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

- `Reports/Jordana_Client_Sessions_2026.csv`
- `Reports/Jordana_Client_Summary_2026.csv`
- `Reports/Jordana_All_Appointments.csv`

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

The `Sessions` sidebar screen is a read-only ledger built from the same appointment query used for `Reports/Jordana_All_Appointments.csv`, including unresolved and non-session calendar records.

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
- Phase 2 includes local PDF invoice generation and immutable finalization snapshots plus an implemented payment ledger with corrections.
- No polished production dashboard is built yet.
- Do not store clinical notes, psychotherapy notes, narrative diagnoses, symptoms, medical histories, treatment plans, session-content notes, treatment summaries, or clinical interpretations beyond the raw calendar evidence already imported. A structured diagnosis code may be stored only when required for administrative insurance billing or reimbursement documentation; it must not be silently inferred from calendar text or session descriptions.
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
- `scripts/build_release.sh` - builds the versioned offline production DMG artifact.
- `scripts/build_setup_wizard.sh` - builds the native no-Terminal setup app for the DMG.
- `scripts/install_release.sh` - one-time production installer copied into release artifacts.
- `scripts/create_private_config.sh` - support-only interactive private config helper.
- `scripts/launch_installed_app.sh` - daily installed-app launcher; no package installation or Git/PyPI access.
- `scripts/bootstrap.sh` - development-checkout bootstrap and source launcher.
- `scripts/setup_jordana_mac.sh` - retired non-destructive stub.
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

The latest session export is:

```text
/Users/brookesnader/Documents/Jordana Billing/Reports/Jordana_Client_Sessions_2026.csv
```
