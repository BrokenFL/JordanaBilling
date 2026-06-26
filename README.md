# Jordana Invoice System

Local-first calendar evidence importer, billing review workflow, and invoice prototype.

Phase 1 does **not** create final invoices. It imports Apple Calendar snapshot rows, preserves the raw evidence, collapses duplicate event versions, proposes classifications, parses Jordana's shorthand, and opens review items for anything uncertain.

Phase 1.1 removes the normal manual CSV step. The Apple Shortcut still writes to Google Sheets through Google Apps Script, but the Mac can now pull completed runs from the Apps Script endpoint into local SQLite.

Phase 2 strengthens the normalization layer. The current prototype adds invoice drafts, immutable finalization snapshots, numbering, void/reissue, history, and local PDFs on top of approved sessions. It does not implement invoice delivery. While backend payment and allocation foundations exist, a complete payment UI, reconciliation, reversal/void workflows, and month-close workflow remain unfinished.

## Current Scope

- Authenticated Google Apps Script sync
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
- Exact active duplicate prevention for billing relationships
- Initial client, alias, rate, session, review, and audit tables
- Local CSV reports after successful sync
- Acceptance report for June-style data
- Isolated sanitized demo database for review testing
- Private local business profile and logo reference
- Normalized service catalog
- Draft, finalized, and void invoice lifecycle
- Transaction-safe numbering and immutable invoice snapshots
- Local multi-page PDF generation

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
jordana-invoice --db data/jordana_invoice.sqlite3 init-db
jordana-invoice --db data/jordana_invoice.sqlite3 import-csv data/samples/june_calendar_snapshots.csv --report data/acceptance_report.md
```

The report is written to `data/acceptance_report.md`.

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
PYTHONPATH=app python -m jordana_invoice sync
PYTHONPATH=app python -m jordana_invoice sync-status
```

Useful variants:

```bash
PYTHONPATH=app python -m jordana_invoice sync --dry-run
PYTHONPATH=app python -m jordana_invoice sync --full
```

`sync` fetches only rows after the saved cursor. `sync --full` asks for all available completed rows but still does not duplicate snapshots because `snapshot_key` is unique locally.

After a successful sync the app updates:

- `Reports/Jordana_Client_Sessions_2026.csv`
- `Reports/Jordana_Client_Summary_2026.csv`
- `Reports/Jordana_All_Appointments.csv`

The reports are written atomically so a failed write does not leave a partial CSV behind.

## Review UI

Run the local review workbench:

```bash
PYTHONPATH=app python -m jordana_invoice --db data/jordana_invoice.sqlite3 serve-review
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

The `Calendar Import` sidebar screen shows local sync status and a single `Sync Now` action. That button only pulls completed snapshot rows already staged by the iPhone Shortcut through Apps Script; it does not trigger the Shortcut and does not edit Apple Calendar.

The `Sessions` sidebar screen is a read-only ledger built from the same appointment query used for `Reports/Jordana_All_Appointments.csv`, including unresolved and non-session calendar records.

The `Rate Card` sidebar screen supports global rates plus one-client, clients-together, and billing-relationship exceptions. Replace and End actions preserve rate-rule history, immediately refresh unapproved session suggestions, and never rewrite approved sessions or finalized invoices.

The inspector has independent saves for Participants, Bill To, and Session Draft. None of those saves approve a session automatically. Account and relationship controls remain available in the collapsed Advanced relationships and shared billing section.

Calendar filters can show all calendars, the preferred work calendar, other calendars, personal/admin calendars, and hidden calendars. Hidden means hidden from the normal queue only; records remain searchable and recoverable.

## Calendar Entry Standard

The Shortcut still imports all non-all-day events from all calendars. `Jordana Work` is a preferred classification signal, not an ingestion restriction. Calendar start time remains authoritative, and title time is validation evidence only.

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
PYTHONPATH=app python3 -m jordana_invoice --db data/demo/jordana_demo.sqlite3 serve-review
```

The generated demo DB is ignored by Git and explicitly marked as demo mode, causing the UI to show `DEMO DATA - NOT FOR REAL BILLING`.

## Important Guardrails

- Calendar rows are evidence, not approved billing.
- Google Sheets is the cloud staging and audit layer; do not delete or alter source rows there.
- SQLite is the local application database.
- Ambiguous rows stay reviewable and reversible.
- Calendar import and review commands do not generate invoices automatically.
- Phase 2 includes local PDF invoice generation and immutable finalization snapshots (invoice delivery and payment tracking UI are deferred).
- No polished production dashboard is built yet.
- Do not store clinical notes beyond the raw calendar evidence already imported.
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
- `scripts/setup_jordana_mac.sh`, `scripts/verify_install.sh`, and `scripts/privacy_check.sh` - setup and safety utilities for Mac handoff.
- `data/samples/` - small June-style fixture for acceptance testing.
- `tests/` - parser tests for known shorthand examples.

## Acceptance Test

Run:

```bash
PYTHONPATH=app python -m unittest discover -s tests
rm -f data/jordana_invoice.sqlite3 data/acceptance_report.md
PYTHONPATH=app python -m jordana_invoice --db data/jordana_invoice.sqlite3 import-csv data/samples/june_calendar_snapshots.csv --report data/acceptance_report.md
```

Expected output:

1. Likely client sessions are listed with parsed client candidate, time, duration, and reasons.
2. Likely personal/admin/nonbillable events are listed separately.
3. Ambiguous rows are in the review section.
4. No invoices are generated.

## Schema Audit

See `docs/SCHEMA_AUDIT.md` for the current authoritative tables, legacy compatibility tables, known overlaps, and prerequisites before any destructive migration.

## Downloadable Report Link

The latest session export is:

```text
/Users/brookesnader/Documents/Jordana Billing/Reports/Jordana_Client_Sessions_2026.csv
```
