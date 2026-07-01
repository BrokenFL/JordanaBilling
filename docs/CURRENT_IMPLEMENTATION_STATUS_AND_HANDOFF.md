# Current Implementation Status And Handoff

This document supersedes all prior uploaded PDF handoffs and provides the
authoritative current state of the Jordana Billing system. Repository code,
schema, migrations, tests, and newer explicit decisions remain authoritative
when they are newer than this document.

**Last code verification commit:** `033d2634fa33688f686c66160ec0eff3e71bf8d7`
**Documentation reconciliation date:** 2026-07-01
**Current migration head:** `015_duplicate_repair_reversal_state`
**Recorded test baseline at the verified code commit:** 2,585 passing, 11 skipped, 0 failures (`2596` tests run)

## Current Architecture

The system is a local-first calendar evidence importer, billing review
workflow, invoice system, payment ledger, and receipt generator running on
macOS with SQLite and a Python HTTP server.

- **Capture:** Apple Shortcut → Google Apps Script → Google Sheets
- **Sync:** Python client pulls raw staged snapshots from Apps Script into local SQLite
- **Review:** Local web UI at `http://127.0.0.1:8765/review`
- **Database:** SQLite at `data/jordana_invoice.sqlite3`
- **Reports:** Local CSV exports after sync; installed releases write user-facing reports to `~/Documents/Jordana Billing/Session Lists/`
- **Invoices:** Local PDF generation via ReportLab; installed releases write new files under `~/Documents/Jordana Billing/Client Files/<Client Display Name>/<Month YYYY>/`
- **Payments:** Payment ledger, allocations, corrections, receipts, and financial summaries in SQLite with API and UI

### Key Modules

- `app/jordana_invoice/` — importer, parser, database schema, report builder, CLI
- `app/jordana_invoice/review_server.py` — local HTTP server with API routes
- `app/jordana_invoice/review_services.py` — review workflow, billing relationships, candidate promotion
- `app/jordana_invoice/invoice_services.py` — invoice lifecycle, monthly staging, line item editing
- `app/jordana_invoice/invoice_pdf.py` — canonical ReportLab PDF generation for draft and final output
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
- Normal capture labels prepared for `past_3_days` and `next_7_days`
- June 1-14, 2026 one-time backfill label support prepared locally
- CSV importer preserved for testing and emergency recovery
- Raw snapshot preservation (never edited in place)
- Duplicate collapse by `calendar_event_id` or `event_fingerprint`
- Source-calendar classification and review filtering
- `JORDANA_PREFERRED_WORK_CALENDAR` as classification signal (not ingestion filter)
- Sanitized Apps Script source is represented in `integrations/apps_script/Code.gs`; live deployment must preserve the existing Apps Script project and use Script Properties

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
- Transaction-safe invoice numbering and immutable finalization snapshots
- Two-step finalization: preview readiness validation followed by confirmation
- Line item editing for drafts: description and amount/rate with correction scope
- Optimistic locking via `expected_revision`
- Monthly invoice identity with `billing_month` and `supplement_sequence`
- Monthly staging service: consolidates duplicate payer drafts, groups by billing party + month
- One open monthly draft per canonical payer and month (person-linked payers)
- Organizations grouped by their actual organization billing-party record
- Stale draft line reconciliation (party or month changes)
- Future scheduled approved sessions remain unstaged until they become invoice-eligible; the approval response reports the skip reason, and successful calendar sync runs idempotent staging reconciliation
- Finalized and void invoices remain immutable
- No historical finalized invoice is silently repointed
- Void with reason; source sessions become eligible for reissue
- Invoice library: searchable, filterable, paginated
- **Prior Unpaid Balance & Account-Summary Presentation**:
  - Displays current charges, current balance, prior unpaid balances from prior finalized non-void invoices, and a final "TOTAL AMOUNT DUE" on HTML print previews and ReportLab PDFs
  - Payments Applied row is omitted when current-invoice payments are zero; shown with negative formatting only when greater than zero
  - Customer-facing previews and PDFs use "TOTAL AMOUNT DUE" without "(As Finalized)" or snapshot/version terminology; internal app detail views still distinguish frozen historical values from live status
  - Compact right-aligned summary block with single-line labels, reduced padding, and smaller-font prior-invoice note beneath the block
  - Single prior invoice: compact note "Includes prior invoice NNN dated … — $X remaining"; multiple: heading + one line per invoice
  - Frozen `account_summary_snapshot` JSON snapshot (version 1) is persisted in the database upon finalization
  - Deterministic same-date cutoff tie-breaking (using date, draft/finalized status, finalized_at timestamp, and alphabetical UUID)
  - Graceful fallback for legacy invoices with NULL snapshots
  - Void invoices are treated as having 0 current balance and are excluded from subsequent prior balance calculations
  - Account statements, delivery, credits, and reconciliation remain unimplemented. Payment receipts are implemented separately from invoice rendering

### PDF Generation

- ReportLab US Letter portrait template
- Draft PDF preview: real PDF, generated in memory, marked DRAFT, no invoice number
- Draft PDF is side-effect free: no disk write, no status/revision/pdf_path/checksum/audit change
- Missing readiness information may block finalization but not draft preview
- Draft and finalized PDFs delegate to one canonical `_generate_invoice_pdf_bytes` renderer
- Draft and finalized output share typography, spacing, header, table, totals, payment section, insurance block, and late-cancellation rendering
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
- **Paid-at-Session Apply Workflow**:
  - First-time approval of a session marked `paid_at_session` automatically records a posted payment and allocates it to the session
  - Strict in-transaction validation enforces that the amount matches approved rate exactly, payment date is valid, and payment method is selected
  - Full idempotency check in the write transaction prevents duplicate payments/allocations and repairs missing allocations when safe
  - Paid-at-session approvals bypass monthly invoice staging, returning a staging status of `not_required` to the UI
  - Report generation is executed post-commit; filesystem failures return a warning without rolling back the transaction
- Idempotency keys for correction deduplication
- `billing_party_id` is the authoritative payment owner
- Unapplied money computed dynamically (not stored as a column)
- Finalized invoice charges remain immutable; payments are a separate audited ledger
- Payment settlement may change after invoice finalization
- Paid/balance amounts derived dynamically from the ledger
- Tabbed Payments workspace: Outstanding, Paid, All Payments
- Payment detail overlay with allocations, correction history, apply-funds, and void forms
- Payment receipt PDF creation from posted payments: one receipt per payment, manual only, immutable JSON snapshot, stored PDF served from disk
- Client page account summary cards
- Shared financial summaries for draft value, monthly finalized, monthly receipts, and outstanding balance
- Read-only dry-run backfill analyzer and CLI for paid-at-session sessions

### Invoice Filing Owner

- `File invoice under` is separate from Participants, Bill To, billing relationships/accounts, and payment ownership
- Additive schema: `client_accounts.default_filing_owner_person_id`; `invoices.filing_owner_person_id`, `filing_owner_person_code_snapshot`, and `filing_owner_display_name_snapshot`
- Draft preview/finalization resolves filing ownership from Bill To client, eligible covered clients, and relationship defaults. Ambiguous multi-client drafts can preview but cannot finalize until Jordana selects an eligible client
- New finalized PDFs use `Client Files/<Client Display Name>/<Month YYYY>/Invoice_<number>.pdf` in installed releases; person code is appended only for same-display-name collisions
- Existing finalized invoices keep their current `pdf_path`, checksum, and immutable snapshots; no guessing backfill is performed
- Local document actions are record-derived only

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
6. Staging result is additive to the approval response; a clean future-session skip is success with zero staged sessions
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
- Normalize appears only for true duplicate person-linked billing-party conflicts

## Invoice Lifecycle And PDF Preview

- Monthly staging groups by `billing_party_id` + calendar billing month
- For person-linked payers: one open monthly draft per canonical payer and month
- Staging consolidates duplicate drafts tied to legacy duplicate person-linked billing-party records
- Finalized and void invoices remain immutable; no historical finalized invoice is silently repointed
- Organizations remain grouped by their actual organization billing-party record
- Draft PDF preview is an actual PDF, generated in memory and clearly marked DRAFT
- Draft PDF has no invoice number, no disk write, and no invoice-state mutation
- Draft and finalized PDFs use the same canonical ReportLab renderer
- Both draft and final PDF endpoints use inline PDF headers compatible with Safari

## Payment Ledger Status

**Implemented:**
- Payment creation, allocation across invoice lines, reversal/void with reasons
- Apply available funds to finalized invoices
- Invoice payment history
- Payment detail overlay with correction history
- Manual payment receipt preview/create/open/show-in-Finder actions for posted payments
- Tabbed Payments workspace
- Client page account summary
- Shared financial summary calculations
- Idempotency keys for corrections
- Read-only dry-run backfill analyzer and CLI

**Not implemented:**
- Paid-at-session backfill apply mode for legacy pre-workflow sessions
- Credits, multi-invoice payments, formal reconciliation, month-close workflows
- Invoice delivery by email or mail

## Recent Live-Validated Fixes

- **Canonical payer (PR #4):** Unified payer billing profiles and restored draft PDF preview
- **Folded payer rows and Safari PDF headers (PR #5):** Folded same-payer shared accounts and added browser-compatible inline PDF headers
- **Joint-session review linkage (PR #6):** Promoted candidate-only records into exactly one session and kept repeated saves idempotent
- **Draft preview account-summary wiring:** Draft previews now receive the calculated account summary and show prior balances correctly
- **Canonical shared PDF renderer:** `generate_invoice_pdf` and `generate_draft_pdf_bytes` now delegate to `_generate_invoice_pdf_bytes`; the former duplicate final renderer is no longer reachable
- **Installer and launcher hardening:** The release uses a native no-Terminal setup app, offline wheelhouse, private app runtime, Application Support data paths, Documents output paths, port ownership checks, and clean temporary-install cleanup
- **Manual one-click install proof:** Brooke reports that the current installer successfully completed a one-click install and launch on a test Mac. Formal acceptance evidence still must be recorded in `docs/TEST_MAC_ACCEPTANCE.md`; this statement does not imply that every checklist scenario has been completed

## Current Audit Findings Requiring Narrow Follow-Up

These are verified implementation/documentation findings, not redesign requests.

1. **Invoice finalization transaction ownership:** `finalize_invoice()` begins an immediate transaction and calls `synchronize_draft_delivery_method()`, which can call `conn.commit()` internally. If delivery method synchronization occurs, the helper can commit before the rest of finalization finishes. The next code round should make transaction ownership explicit and add a rollback regression test.
2. **Installer rollback after app replacement:** `install_release.sh` stages the new app safely, but removes the existing app before final verification. A verification failure after replacement can leave the prior working app unavailable even though private data remains safe. The next packaging round should preserve the old app until the new app passes verification and restore it on failure.
3. **Installer version source:** `install_release.sh` currently installs `jordana-invoice==0.1.0` directly. This matches the current project version but can drift on a future version bump. Installation should eventually read the expected version from the release manifest.
4. **Remote CI:** The verified commit has no GitHub status checks. Local tests remain authoritative today; adding sanitized CI would reduce the chance of an untested future push.

## Current Test Strategy

```bash
PYTHONPATH=app .venv/bin/python -m unittest discover -s tests
```

- Recorded full-suite baseline: `Ran 2596 tests in 180.798s`, `OK (skipped=11)` on 2026-07-01 at commit `033d2634fa33688f686c66160ec0eff3e71bf8d7`
- Exact counts: 2,585 passing, 11 skipped, 0 failures
- Skipped tests include manual/network-style integration coverage where private or external state is not available in ordinary local runs
- Acceptance test uses a temporary database and never touches the operational database:

```bash
scripts/run_acceptance_test.sh
```

- Privacy and Git safety checks before any push:

```bash
scripts/git_safety_check.sh
scripts/privacy_check.sh
```

This documentation reconciliation does not claim that the suite was rerun after documentation-only edits. Future code rounds must rerun focused tests and the full suite.

## Privacy And Git Safety Rules

- Never commit `.env`, API keys, live databases, real CSV reports, Google credentials, invoice PDFs, logs, screenshots with client names, shortcut backups, raw Google Sheet exports, real diagnosis codes, or real diagnosis-code examples
- Use sanitized fictional records for demo data only
- Keep private business profiles, branding assets, and generated PDFs outside Git
- Before any GitHub push, run `scripts/git_safety_check.sh`
- The operational SQLite database contains real data; never delete, overwrite, truncate, or recreate it
- For acceptance testing, always use `scripts/run_acceptance_test.sh`

## Policy Decision: Diagnosis-Code Storage (2026-06-30)

The former categorical prohibition on diagnosis-code storage has been superseded **only** for structured insurance diagnosis codes. The application may store a structured diagnosis code only when required for administrative insurance billing or reimbursement documentation. Diagnosis codes must be limited to the minimum necessary billing information. The application must not store clinical notes, psychotherapy notes, narrative diagnoses, symptoms, medical histories, treatment plans, session-content notes, treatment summaries, clinical interpretations, or other unnecessary protected health information.

Additional rules:

- Diagnosis codes are local operational data; real diagnosis codes must never appear in source control, fixtures, screenshots, logs, examples, demo data, documentation, or committed databases
- Diagnosis codes are invoice-specific and optional
- Diagnosis codes may appear only in authorized insurance-related invoice output when Jordana intentionally supplies or approves them
- Standard self-pay invoices should not include diagnosis codes
- Diagnosis-code values must never be inferred from calendar text, participant names, session descriptions, or other application data
- Approved invoice snapshots must remain historically stable
- Removing or changing a diagnosis code after finalization must use the existing correction, void, or reissue workflow rather than silently rewriting finalized records

This decision is consistent with migration 012 (`012_insurance_coding`) and with invoice finalization, which freezes the diagnosis code into the finalized snapshot only when insurance coding is explicitly enabled.

## Local Run Command

```bash
PYTHONPATH=app .venv/bin/python -m jordana_invoice \
  --db data/jordana_invoice.sqlite3 \
  serve-review
```

Open: `http://127.0.0.1:8765/review`

## Known Limitations

- No formal client-versus-non-client schema distinction
- No automatic payer classification
- No invoice delivery by email or mail
- No paid-at-session backfill apply mode for legacy pre-workflow sessions
- No credits, refunds, write-offs, automated multi-invoice payment allocation, formal reconciliation, or month-close workflows
- No polished production dashboard
- No notarized installer
- V1 production installation requires the Python major/minor runtime recorded in `release_manifest.json`
- A one-click test install has succeeded, but the complete acceptance checklist and evidence record are not yet documented as complete
- Installer replacement is not yet rollback-safe after the existing app has been removed
- Finalization transaction ownership needs the narrow delivery-method synchronization fix described above
- Installer package version is currently hardcoded to `0.1.0`
- No permanent deletion of billing relationships (by design — deactivation only)
- No clinical notes, psychotherapy notes, narrative diagnoses, symptoms, medical histories, treatment plans, or session-content notes beyond raw calendar evidence; structured insurance diagnosis codes are permitted only under the policy above

## Recommended Next Steps

1. Fix invoice finalization transaction ownership and add rollback coverage
2. Make installer replacement rollback-safe and derive the package version from the release manifest
3. Record the completed one-click test details and finish the remaining clean-Mac acceptance checklist scenarios
4. Run the current release against Jordana's intended workflow: sync, review, approval, invoice preview, finalization, PDF opening, payment, and restart
5. Confirm rate exceptions and Bill To defaults with Jordana
6. Add sanitized CI for tests and safety checks
7. Treat invoice delivery, historical paid-at-session backfill, dashboard, credits, reconciliation, and month-close as later enhancements rather than blockers to the core handoff
