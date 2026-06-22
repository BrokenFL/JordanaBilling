# Jordana Invoice System

Local-first calendar evidence importer and billing review workflow.

Phase 1 does **not** create final invoices. It imports Apple Calendar snapshot rows, preserves the raw evidence, collapses duplicate event versions, proposes classifications, parses Jordana's shorthand, and opens review items for anything uncertain.

Phase 1.1 removes the normal manual CSV step. The Apple Shortcut still writes to Google Sheets through Google Apps Script, but the Mac can now pull completed runs from the Apps Script endpoint into local SQLite.

Phase 2 strengthens the normalization layer. It separates people, client accounts, account members, session participants, billing parties, aliases, rate rules, sessions, review items, raw snapshots, and audit records. The local review UI now includes section-level saves plus first CRM-style People and Clients & Accounts views. It still does not generate invoices.

## Current Scope

- Authenticated Google Apps Script sync
- Google Sheets CSV importer for testing and emergency recovery
- Raw snapshot preservation
- Completed-run validation from capture windows
- Duplicate collapse into event candidates
- Conservative shorthand parser
- Event classification
- People/account/billing-party data model
- Session participant modeling
- Service mode, rate group, evening, and weekend categorization
- Effective-dated rate rules
- Structured review-state engine
- Section-level saves for people, relationships, billing, and session drafts
- Human-readable person codes and account codes
- Clients & Accounts and People CRM views
- Initial client, alias, rate, session, review, and audit tables
- Local CSV reports after successful sync
- Acceptance report for June-style data

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

The inspector has independent saves for person, relationship, billing details, and session draft. None of those saves approve a session automatically.

## Important Guardrails

- Calendar rows are evidence, not approved billing.
- Google Sheets is the cloud staging and audit layer; do not delete or alter source rows there.
- SQLite is the local application database.
- Ambiguous rows stay reviewable and reversible.
- No PDF invoices are generated in Phase 1.
- No PDF invoices are generated in Phase 2.
- No polished production dashboard is built yet.
- Do not store clinical notes beyond the raw calendar evidence already imported.
- Do not rewrite historical finalized invoice values when rates change later.
- Store secrets only in `.env`; never paste the real API key into source files or docs.
- Do not make a permanent new client account just because two names appear in one calendar title.
- Do not generate a person code for a provisional parser-only name.
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

## Downloadable Report Link

The latest session export is:

```text
/Users/brookesnader/Documents/Jordana Billing/Reports/Jordana_Client_Sessions_2026.csv
```
