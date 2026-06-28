# Current Implementation Status And Handoff

This document supersedes all prior uploaded PDF handoffs and provides the
authoritative current state of the Jordana Billing system as of the
`main` branch commit listed below.

**Authoritative main commit hash:** `d73ce94`

## Current Architecture

The system is a local-first calendar evidence importer, billing review
workflow, and invoice prototype running on macOS with SQLite and a Python
HTTP server.

- **Capture:** Apple Shortcut → Google Apps Script → Google Sheets
- **Sync:** Python client pulls completed runs from Apps Script into local SQLite
- **Review:** Local web UI at `http://127.0.0.1:8765/review`
- **Database:** SQLite at `data/jordana_invoice.sqlite3`
- **Reports:** Local CSV exports after sync
- **Invoices:** Local PDF generation via ReportLab; new files stored in ignored `Invoices/<Client Display Name>/<Month YYYY>/`
- **Payments:** Payment ledger in SQLite with API and UI

### Key Modules

- `app/jordana_invoice/` — importer, parser, database schema, report builder, CLI
- `app/jordana_invoice/review_server.py` — local HTTP server with API routes
- `app/jordana_invoice/review_services.py` — review workflow, billing relationships, candidate promotion
- `app/jordana_invoice/invoice_services.py` — invoice lifecycle, monthly staging, line item editing
- `app/jordana_invoice/invoice_pdf.py` — ReportLab PDF generation (draft and final)
- `app/jordana_invoice/invoice_rendering.py` — shared render model and HTML print preview
- `app/jordana_invoice/payment_services.py` — payment ledger, allocations, corrections
- `app/jordana_invoice/financial_summary.py` — shared financial summary calculations
- `app/jordana_invoice/static/` — local review UI (HTML/CSS/JS)

## Authoritative Terminology

| Term | Meaning |
|------|---------|
| **Participant** | A person who attended a session (stored in `session_participants`) |
| **Payer** | The person or organization responsible for receiving and paying the invoice |
| **Bill To** | The billing party selected for a specific session or invoice |
| **Billing Party** | A `billing_parties` record (person-linked or organization) storing billing contact and delivery info |
| **Canonical Active Billing Party** | One active person-linked billing-party record per payer for normal future use |
| **Billing Relationship** | A `client_accounts` record linking a payer to covered clients |
| **Account** | Backend grouping structure (`client_accounts`); not required for self-pay |
| **Candidate** | A `calendar_event_candidates` row awaiting review |
| **Session** | A `sessions` row promoted from a candidate via section-level save |
| **Review Status** | The workflow state of a candidate/session (separate from appointment status) |
| **Billing Treatment** | How cancelled/no-show appointments are handled for billing (`billable`, `not_billable`, `waived`, `unresolved`) |
| **Payment Handling** | Whether a session is invoiced or paid at session (`invoice_billing`, `paid_at_session`) |

## Implemented Features

### Calendar Import And Sync

- Apple Shortcut captures calendar snapshots to Google Sheets
- Authenticated sync from Apps Script endpoint to local SQLite
- CSV importer preserved for testing and emergency recovery
- Raw snapshot preservation (never edited in place)
- Duplicate collapse by `calendar_event_id` or `event_fingerprint`
- Source-calendar classification and review filtering
- `JORDANA_PREFERRED_WORK_CALENDAR` as classification signal (not ingestion filter)

### Review Workflow

- Section-level saves: Clients, Bill To, Session Details, Approval
- Candidate-only records promoted to exactly one `sessions` row on first save
- Later saves and approval reuse the same `sessions.candidate_id` link
- Multi-participant and single-participant candidates follow the same save/approval contract
- Repeated saves do not create duplicate sessions
- Exact-name auto-linking for unapproved sessions
- Payer auto-assignment priority: existing session payer → account default → unique active billing party
- Approval triggers automatic monthly invoice staging
- Approval remains successful even if invoice staging later warns
- Overlay closes on success, stays open on failure with sanitized errors
- Double-submission prevention via button disabling

### People And Billing Relationships

- People, billing parties, accounts, account members, aliases, rate rules
- One canonical active person-linked billing-party record per payer
- Billing Relationships directory folds same-payer shared accounts into one payer-centered row
- Backend account/group records remain for compatibility and advanced detail
- Folding does not delete or rewrite accounts, members, sessions, approved Bill To values, invoices, payments, or audit history
- Normalize button appears only for true duplicate person-linked billing-party conflicts
- Audited normalization: selects canonical, copies missing fields, deactivates redundant, repoints safe mutable references, leaves finalized invoices and payments unchanged
- Guided billing relationship creation wizard (3-step)
- In-wizard person and organization creation with duplicate detection
- Relationship editing: invoice recipient, covered clients, billing delivery
- Deactivate/reactivate billing relationships (no permanent deletion)
- Session Review integration: attach billing relationship to a review candidate
- Exact active duplicate prevention during creation and editing
- Read-only duplicate analysis for legacy conflicts

### Invoice Lifecycle

- Draft, finalized, and void invoice lifecycle
- Transaction-safe numbering and immutable finalization snapshots
- Two-step finalization: preview (readiness validation) → confirm (atomic transaction)
- Line item editing for drafts: description and amount/rate with correction scope
- Optimistic locking via `expected_revision`
- Monthly invoice identity with `billing_month` and `supplement_sequence`
- Monthly staging service: consolidates duplicate payer drafts, groups by billing party + month
- One open monthly draft per canonical payer and month (person-linked payers)
- Organizations grouped by their actual organization billing-party record
- Stale draft line reconciliation (party or month changes)
- Finalized and void invoices remain immutable
- No historical finalized invoice is silently repointed
- Void with reason; source sessions become eligible for reissue
- Invoice library: searchable, filterable, paginated
- **Prior Unpaid Balance & Account-Summary Presentation**:
  - Displays current charges, current balance, prior unpaid balances from prior finalized non-void invoices, and a final "TOTAL AMOUNT DUE" on HTML print previews and ReportLab PDFs.
  - Payments Applied row is omitted when current-invoice payments are zero; shown with negative formatting only when greater than zero.
  - Customer-facing previews and PDFs use "TOTAL AMOUNT DUE" without "(As Finalized)" or snapshot/version terminology; internal app detail views still distinguish frozen historical values from live status.
  - Compact right-aligned summary block with single-line labels, reduced padding, and smaller-font prior-invoice note beneath the block.
  - Single prior invoice: compact note "Includes prior invoice NNN dated … — $X remaining"; multiple: heading + one line per invoice.
  - Frozen `account_summary_snapshot` JSON snapshot (version 1) is persisted in the database upon finalization.
  - Deterministic same-date cutoff tie-breaking (using date, draft/finalized status, finalized_at timestamp, and alphabetical UUID).
  - Graceful fallback for legacy invoices with NULL snapshots (UI shows live payment status and hides the as-finalized snapshot section).
  - Void invoices are treated as having 0 current balance and are excluded from subsequent prior balance calculations.
  - Receipts, account statements, delivery, credits, and reconciliation remain unimplemented.

### PDF Generation

- ReportLab US Letter portrait template
- Draft PDF preview: real PDF, generated in memory, marked DRAFT, no invoice number
- Draft PDF is side-effect free: no disk write, no status/revision/pdf_path/checksum/audit change
- Missing readiness information may block finalization but not draft preview
- Draft and final PDF endpoints use dedicated inline PDF response headers compatible with Safari
- PDF responses use `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`
- PDF responses do not apply `X-Frame-Options: DENY` or CSP headers (allows inline browser preview)
- HTML/JSON CSP and frame protections remain unchanged
- Multi-page invoices repeat headers, keep rows intact, show totals on last page only
- Existing finalized PDFs are immutable; layout refinements apply only to new PDFs

### Payment Ledger

- `payments` and `payment_allocations` tables (additive migrations)
- Payment provenance via `source_type` and `source_session_id` (migration 004)
- Payment corrections: reversal with reason, void with reason, apply available funds (migration 007)
- Idempotency keys for correction deduplication
- `billing_party_id` is the authoritative payment owner
- Unapplied money computed dynamically (not stored as a column)
- Finalized invoice charges remain immutable; payments are a separate audited ledger
- Payment settlement may change after invoice finalization
- Paid/balance amounts derived dynamically from the ledger (no `paid_cents`/`balance_cents` columns on invoices)
- Tabbed Payments workspace: Outstanding, Paid, All Payments
- Payment detail overlay with allocations, correction history, apply-funds, and void forms
- Client page account summary cards (Total Finalized, Total Payments Applied, Current Balance, Account Status)
- Shared financial summaries for draft value, monthly finalized, monthly receipts, and outstanding balance
- Read-only dry-run backfill analyzer and CLI for paid-at-session sessions

### Invoice Filing Owner

- `File invoice under` is separate from Participants, Bill To, billing relationships/accounts, and payment ownership.
- Additive schema: `client_accounts.default_filing_owner_person_id`; `invoices.filing_owner_person_id`, `filing_owner_person_code_snapshot`, and `filing_owner_display_name_snapshot`.
- Draft preview/finalization resolves filing ownership from Bill To client, eligible covered clients, and relationship defaults. Ambiguous multi-client drafts can preview but cannot finalize until Jordana selects an eligible client.
- New finalized PDFs use `Invoices/<Client Display Name>/<Month YYYY>/Invoice_<number>.pdf`; person code is appended to the client folder only for same-display-name collisions.
- Existing finalized invoices keep their current `pdf_path`, checksum, and immutable snapshots; no guessing backfill is performed.
- Local document actions are record-derived only: Open PDF uses the served final PDF endpoint, Show in Finder reveals the stored PDF, and Open client invoice folder opens the client-level folder for the current path shape while still accepting legacy stored paths.

### Rate Rules

- Effective-dated suggested rates
- Person-specific and exact participant-combination rate exceptions
- Rate-change scope: this session only, future sessions for one participant, future joint sessions
- Replace and End actions preserve rate-rule history
- Approved rates never rewritten by future rate rules

## Current Review Workflow

1. Calendar data syncs into SQLite as raw snapshots
2. Parser classifies and proposes client/session/duration/time
3. Review queue shows candidates needing attention
4. Section-level saves promote candidates to sessions:
   - Save Client(s) — confirms participants
   - Save Bill To — selects payer
   - Save Session Draft — duration, type, rate, payment handling
   - Approve Session — validates and commits
5. Approval triggers monthly invoice staging automatically
6. Staging result is additive to the approval response (success/warning/unavailable/error)
7. Approval remains successful even if staging warns

## Canonical Payer And Billing Relationships Behavior

- One canonical active person-linked billing-party record per payer for normal future use
- Legacy duplicates may remain historically visible until explicitly normalized
- Normalization is audited and never rewrites finalized invoices, payments, or approved historical values
- Inactive historical records may remain visible
- Multiple active competing payer profiles are not normal intended behavior
- Normal UI shows one payer-centered row when a shared backend account resolves to the same payer relationship
- Backend account/group records remain for compatibility and advanced detail
- Folding does not delete or rewrite accounts, memberships, sessions, approved Bill To values, invoices, payments, or audit history
- Normalize appears only for true duplicate person-linked billing-party conflicts, not merely because an internal shared account exists

## Invoice Lifecycle And PDF Preview

- Monthly staging groups by `billing_party_id` + calendar billing month
- For person-linked payers: one open monthly draft per canonical payer and month
- Staging consolidates duplicate drafts tied to legacy duplicate person-linked billing-party records
- Finalized and void invoices remain immutable; no historical finalized invoice is silently repointed
- Organizations remain grouped by their actual organization billing-party record
- Draft PDF preview is an actual PDF (not HTML), generated in memory, clearly marked DRAFT
- Draft PDF has no invoice number, no disk write, no mutation of invoice status/revision/pdf_path/checksum/audit
- Both draft and final PDF endpoints use inline PDF headers compatible with Safari

## Payment Ledger Status

**Implemented:**
- Payment creation, allocation across invoice lines, reversal/void with reasons
- Apply available funds to finalized invoices
- Invoice payment history
- Payment detail overlay with correction history
- Tabbed Payments workspace (Outstanding, Paid, All Payments)
- Client page account summary
- Shared financial summary calculations
- Idempotency keys for corrections
- Read-only dry-run backfill analyzer and CLI

**Not implemented:**
- Paid-at-session backfill apply mode (dry-run only)
- Paid-at-session eligibility transition (sessions remain excluded from invoicing)
- Credits, multi-invoice payments, formal reconciliation, month-close workflows
- Invoice delivery (email/mail sending)

## Recent Live-Validated Fixes

- **Canonical payer (PR #4):** Unified payer billing profiles; one canonical active person-linked billing-party record per payer; draft PDF preview restored using same ReportLab render model as final PDFs
- **Folded payer rows and Safari PDF headers (PR #5):** Same-payer shared billing accounts folded into one payer-centered Billing Relationships row; browser-compatible inline PDF headers for Safari
- **Joint-session review linkage (PR #6):** Candidate-only review records promoted into exactly one sessions row during section save; multi-participant save and approval reusing the same candidate-to-session link; approval remaining successful even if invoice staging later warns
- **Draft preview account-summary wiring fix:** The `/api/invoices/<id>/print-preview` and `/api/invoices/<id>/draft-pdf` endpoints now pass the already-calculated `account_summary` (from `get_invoice()`) into `build_print_preview_html` and `build_invoice_render_model`, so draft previews correctly display prior unpaid balances and total amount due. Previously these endpoints omitted the `account_summary` argument, causing draft previews to miss the prior-balance section.

## Current Test Strategy

```bash
PYTHONPATH=app .venv/bin/python -m unittest discover -s tests
```

- Full suite: 1490+ tests passing, 11 skipped, 0 failures (as of last handoff)
- Acceptance test (uses temporary database, never touches operational DB):

```bash
scripts/run_acceptance_test.sh
```

- Privacy and Git safety checks before any push:

```bash
scripts/git_safety_check.sh
scripts/privacy_check.sh
```

## Privacy And Git Safety Rules

- Never commit `.env`, API keys, live databases, real CSV reports, Google credentials, invoice PDFs, logs, screenshots with client names, shortcut backups, or raw Google Sheet exports
- Use sanitized fictional records for demo data only
- Keep private business profiles, branding assets, and generated PDFs outside Git
- Before any GitHub push, run `scripts/git_safety_check.sh`
- Do not commit live databases, reports, logs, screenshots with client names, shortcut backups, `.env`, or credentials
- The operational SQLite database at `data/jordana_invoice.sqlite3` contains real data; never delete, overwrite, truncate, or recreate it
- For acceptance testing, always use `scripts/run_acceptance_test.sh` (creates a temporary database)

## Local Run Command

```bash
PYTHONPATH=app .venv/bin/python -m jordana_invoice \
  --db data/jordana_invoice.sqlite3 \
  serve-review
```

Open: `http://127.0.0.1:8765/review`

## Known Limitations

- No formal client-versus-non-client schema distinction (all active people appear in search)
- No automatic payer classification
- No invoice delivery (email/mail sending)
- No paid-at-session backfill apply mode (dry-run only)
- No credits, multi-invoice payments, formal reconciliation, or month-close workflows
- No polished production dashboard
- No permanent deletion of billing relationships (by design — deactivation only)
- No clinical notes beyond raw calendar evidence

## Recommended Next Steps

1. Finish the one-click launcher and synchronization experience
2. Re-run imports and review until clean
3. Confirm rate exceptions and bill-to defaults with Jordana
4. Implement invoice delivery workflow
5. Implement paid-at-session backfill apply mode
6. Build dashboard integration
7. Add credits, multi-invoice payments, reconciliation, and month-close workflows
