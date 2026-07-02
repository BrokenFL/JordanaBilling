# Invoice Lifecycle

## Eligibility

A session must be approved, have participants and bill-to, preserve a nonnegative actual charged amount, not be future scheduled, not be excluded/personal/admin, not be marked "Paid at time of session", retain raw evidence, and not belong to another draft/finalized invoice. Cancelled/no-show records require explicit `billing_treatment=billable`.

## Draft

Drafts can add/remove eligible sessions, reorder lines, override delivery, and change dates. Totals use integer cents.

### Line Item Editing

Users can edit the description and line amount of draft invoices before finalization:
- **Description Editing**: Editing a line description updates only that draft invoice line's description snapshot and does not affect the backing session description.
- **Amount/Rate Editing**: When changing a line amount, two correction scopes are available:
  1. **Invoice line only** (default): Updates only the line item's amount on this draft invoice. The backing session rate remains unmodified.
  2. **Invoice line and approved session**: Updates the line item's amount on this draft invoice, and also propagates the update to the backing session's approved rate and rate snapshot. (Only available for lines linked to an approved session).
- **Validation**: Rejects empty descriptions, negative amounts, or values with more than two decimal places (fractional cents). A non-empty reason is required when the amount is modified.
- **Revision and Concurrency**: A successful edit increments the invoice revision number exactly once and recalculates totals. The update API requires `expected_revision` and rejects stale writes to prevent duplicate submission or overwrite conflicts.
- **Audit Logging**: All edits write to the general `audit_log`. Edits that modify the line amount also write detailed correction records to `invoice_line_item_corrections`, storing the old/new values, the correction scope, and the user's correction reason.
- **Immutability of Finalized/Voided Invoices**: Finalized and voided invoices cannot be edited. Attempts to modify them fail safely.

### Monthly Invoice Identity

Drafts may optionally carry a `billing_month` (`YYYY-MM`) that identifies the invoice as belonging to a specific calendar month. When `billing_month` is provided, the billing period start and end are derived automatically as the first and last day of that month. When only `billing_period_start` and `billing_period_end` are provided, `billing_month` is derived only if the period is exactly one complete calendar month; otherwise `billing_month` is `NULL` (legacy or nonmonthly).

For person-linked payers, at most one open draft may exist per canonical payer and billing month. The staging service consolidates duplicate drafts tied to legacy duplicate person-linked billing-party records into one canonical draft before staging. Finalized and void invoices do not block new drafts for the same month. `supplement_sequence` 0 marks the original monthly draft; 1+ is reserved for supplemental drafts. Organizations remain grouped by their actual organization billing-party record.

### Monthly Staging Service

A backend reconciliation service `stage_approved_sessions_to_monthly_drafts()` now exists in `invoice_services.py`. It is idempotent: repeated calls produce the same correct result.

The service first consolidates duplicate drafts tied to legacy duplicate person-linked billing-party records (Step 0), then groups eligible approved sessions by `billing_party_id` + calendar billing month and reconciles them into monthly draft invoices. For person-linked payers, this means one open monthly draft per canonical payer and month. For each (party, month) group it uses one `BEGIN IMMEDIATE` transaction:

- Finds the existing open monthly draft for the party and month, or creates one if none exists and there are eligible sessions to stage.
- If prior finalized or void invoices exist for the party and month, assigns `supplement_sequence = MAX(existing) + 1` for the new draft.
- Adds only sessions not already attached to a draft or finalized invoice.
- Reuses existing line snapshot creation logic.

Stale draft lines are reconciled before finalization:

- If a session's Bill To party no longer matches the invoice, the line is moved atomically to the correct target monthly draft.
- If a session date no longer belongs to the invoice's billing month, the line is moved atomically to the correct target monthly draft.
- If a session is no longer eligible, the line is removed and the session is left unstaged.
- Finalized and void invoice lines are never moved or modified.

The service returns a structured summary with counts of drafts created/reused, sessions staged/already staged/moved/removed as ineligible, sessions skipped with reasons, drafts consolidated, and errors by party/month. It does not expose private names in returned diagnostic identifiers.

Future scheduled sessions are approved but not invoice-eligible until the appointment is no longer future-dated. They appear in `sessions_skipped` with the reason `"Future scheduled session is not invoice eligible"` and do not create a draft or invoice line. After a later successful calendar sync updates the session so it is eligible, the sync path runs the same idempotent staging reconciliation and adds the session to the appropriate monthly draft without duplicating existing drafts or lines.

### Staging API Endpoint

An administrative HTTP endpoint is available for manual or scripted staging:

```
POST /api/invoices/stage
```

**Request body** (optional JSON):

```json
{"session_ids": ["session-id-1", "session-id-2"]}
```

- Omitted `session_ids` reconciles all eligible approved sessions.
- An empty list returns a successful zero-change result.
- Each value must be a non-empty string.
- No Bill To names, client names, month overrides, invoice IDs, or rate overrides are accepted.

**Response**: the staging service's structured summary (drafts_created, drafts_reused, sessions_staged, sessions_already_staged, sessions_moved, sessions_removed_ineligible, sessions_skipped, errors). Internal UUIDs may appear for administrative diagnostics; private names and calendar titles are never exposed.

**Error behavior**:
- Malformed JSON or invalid request: HTTP 400
- Database busy/locked: HTTP 503
- Unexpected server failure: HTTP 500
- Per-party-month errors remain in the structured `errors` array and are not silently discarded.

The endpoint is **administrative/manual only**. No UI controls exist yet. Paid-at-session sessions remain excluded temporarily.

### Approval Integration

Session approval now triggers monthly invoice staging automatically. When a candidate is approved via `POST /api/review/candidates/{id}/approve`:

1. `approve_candidate()` runs exactly as before and commits the approval transaction.
2. After the approval commit, the server calls `stage_approved_sessions_to_monthly_drafts()` with `session_ids=[approved_session_id]`.
3. The staging result is attached to the approval response as an additive `invoice_staging` field:

```json
{
  "session": { ... },
  "participants": [ ... ],
  "invoice_staging": {
    "status": "success | warning | unavailable | error",
    "summary": { ... }
  }
}
```

**Transaction separation**: Approval commits before staging begins. Staging uses its own per-party-month `BEGIN IMMEDIATE` transactions. A staging failure never reverses, rolls back, or misreports the successful approval.

**Status values**:
- `success` — staging completed with no party-month errors; full summary included. This can still mean zero sessions were staged, for example when the approved session is future scheduled and appears in `sessions_skipped`.
- `warning` — staging completed but the summary contains errors; full summary included.
- `unavailable` — staging could not run because the database was busy; `summary` is `null`.
- `error` — unexpected staging exception; `summary` is `null`. No exception text, SQL, paths, or private data is exposed.

**HTTP behavior**: Approval validation failure returns HTTP 400 (staging is not called). Database busy during approval returns HTTP 503 (staging is not called). Approval success always returns HTTP 200 regardless of staging outcome.

**Idempotency**: Repeated staging for the same approved session creates no duplicate draft and no duplicate invoice line. Repeated approval calls still produce existing approval-side audit, usage, review-item, alias-update, and report side effects; those are not altered in this round.

**Frontend**: The review UI (`review.js`) processes the additive `invoice_staging` field returned by candidate approval:
- On successful staging (`status == "success"`), the success banner states `"Session approved and added to monthly draft."`
- On a successful approval where the staging summary reports zero staged sessions because the session is future scheduled, the success banner states `"Session approved. This future session will become invoice-eligible after the appointment date."`
- On staging warning (`status == "warning"`), database busy (`status == "unavailable"`), or unexpected error (`status == "error"`), approval remains successful, and a persistent amber warning banner is displayed at the top of the workbench via `showReviewWarning(message)`.
- If the Invoices view is visible, the UI automatically invalidates/refreshes the active invoices list via `loadInvoices()` and reopens the active invoice via `openInvoice(...)` to reflect the newly staged session without requiring a manual reload.


Paid-at-session sessions remain excluded from staging temporarily. Paid-at-session backfill is analyzed by a read-only dry-run CLI (see [Payment Ledger Foundation](#payment-ledger-foundation) below), but no apply mode exists yet.

## Finalized

Finalization is a two-step process:

1. **Preview**: Reread the saved draft from SQLite, run `validate_invoice_readiness` to check all readiness rules, and return a preview with a `revision` number for optimistic locking and a `readiness` object with `ready` (bool) and `errors` (list of `{field, message}` dicts). This step is side-effect free: it does not save draft edits, sync delivery, assign a number, write a PDF path/checksum, create audit rows, or change revision/status. The UI embeds the canonical draft PDF preview from `POST /api/invoices/{id}/draft-pdf`, so the approval preview uses the same ReportLab renderer as finalization. The UI shows "Ready to finalize" or "Not ready to finalize" with specific fixes, and disables the finalize button while errors exist.
2. **Confirm**: Finalize only if the invoice revision matches the preview and `validate_invoice_readiness` passes. This prevents stale or double submissions.

### Readiness Validation

A single authoritative function `validate_invoice_readiness` is used in both preview and confirm. It checks:

- Bill-to party exists and is active
- At least one eligible invoice line
- All line amounts are positive
- Valid invoice date
- Active business profile
- Required bill-to contact details for the selected delivery method (email for email/both, mailing address for mail/both)
- Delivery method cannot remain unresolved
- Required business/payee/payment-address details used on the invoice
- Required `zelle_recipient` in Invoice Settings
- Valid, unique invoice number generation
- All included sessions remain invoice-eligible
- Resolved `File invoice under` client when filing ownership is ambiguous
- Preview revision is not stale (when `expected_revision` is provided)

Validation errors are structured as `{field, message}` for UI display. No validation logic is duplicated between frontend and backend.

Explicit confirmation starts a transaction that revalidates readiness, checks the revision matches, assigns the number, freezes bill-to/business/line/filing-owner snapshots, calculates totals, writes the PDF atomically, stores SHA-256, and audits finalization. Failure rolls back and removes partial output. The finalized snapshot and PDF match the embedded canonical preview except for approved final metadata such as the real invoice number replacing the draft marker.

### Optional Insurance Coding

During finalization, the user may optionally check "Add Insurance Coding" and enter a diagnosis code. When enabled:

- The diagnosis code is required and must be non-empty.
- EIN, NPI, and SW must exist in Invoice Settings; finalization stops with a clear message if any are missing.
- All four values (diagnosis code + EIN/NPI/SW from settings) are frozen into the finalized invoice snapshot columns: `insurance_coding_included`, `insurance_diagnosis_code_snapshot`, `insurance_ein_snapshot`, `insurance_npi_snapshot`, `insurance_sw_snapshot`.
- The diagnosis code is never persisted on draft invoices, people, sessions, or reusable defaults — it exists only in the finalization payload and the finalized snapshot.
- Preview uses the temporary finalization payload plus current settings; it does not mutate the database.
- Later settings changes do not affect existing finalized invoices.
- When unchecked, none of these fields block finalization and no insurance block appears on the PDF.

Diagnosis codes are local operational data. Real diagnosis codes must never appear in source control, fixtures, screenshots, logs, examples, demo data, documentation, or committed databases. Diagnosis codes may appear only in authorized insurance-related invoice output when Jordana intentionally supplies or approves them. Standard self-pay invoices should not include diagnosis codes. Diagnosis-code values must never be inferred from calendar text, participant names, session descriptions, or other application data. Approved invoice snapshots must remain historically stable; removing or changing a diagnosis code after finalization must use the existing correction, void, or reissue workflow rather than silently rewriting finalized records.

### File Invoice Under

`File invoice under` is a separate filing-owner concept from Participants, Bill To, billing relationship/account, and payment owner. Bill To remains the payer and `billing_party_id` remains the payment owner. Filing owner determines the local client folder for newly finalized PDFs.

Resolution rules:

- A self-paying client files under that client.
- When Bill To is an established client person, the invoice files under that paying client, even if another client received the service.
- When Bill To is an organization, the invoice files under a covered client from that billing relationship. One eligible covered client can be selected automatically; multiple eligible clients require Jordana to choose.
- When Bill To is a non-client individual, the invoice files under the service client when unambiguous; multi-client ambiguity requires Jordana to choose.
- Only eligible covered client people may be selected. Draft preview still works when unresolved, but finalization readiness fails with a filing-owner validation message.

Billing relationships may store `default_filing_owner_person_id`. It must reference a covered client. If relationship membership changes through the relationship editor and the default no longer belongs, the update must clear or replace the default; it must not silently choose among multiple remaining clients. Approved sessions are not rewritten by default changes.

Finalization freezes `filing_owner_person_id`, `filing_owner_person_code_snapshot`, `filing_owner_display_name_snapshot`, and `pdf_path`. Later person-name or relationship changes do not move or rename finalized invoices. Existing finalized invoices keep their existing path/checksum/snapshots and are not backfilled by guessing.

New finalized PDFs are stored under the configured invoice root. Installed
releases set that root to `~/Documents/Jordana Billing/Client Files`:

`Client Files/<Client Display Name>/<Month YYYY>/Invoice_<number>.pdf`

The month folder uses `billing_month` when present. If `billing_month` is absent, it falls back to `billing_period_start`. It never uses the wall-clock date or PDF creation date. Path parts are sanitized, the stable person code remains frozen internally, and organization names are not used as the folder when the invoice is filed under a client.

When two different filing-owner people would otherwise use the same sanitized display-name folder, the later conflicting folder is disambiguated with the permanent person code:

`Client Files/<Client Display Name> [<PERSON_CODE>]/<Month YYYY>/Invoice_<number>.pdf`

If an existing plain display-name folder is present but SQLite cannot prove that it belongs to the same filing-owner person, new finalization uses the code-disambiguated folder instead of guessing from the folder name.

Bill To rendering is delivery-aware:

- `email` => name, then `Via Email: ...`
- `mail` => name, then mailing address only
- `both` => name, mailing address, then `Via Email: ...`

The payment block remains one centered block and now always includes both the check instructions and a Zelle line. Draft previews show `Not configured` when Zelle is missing so readiness errors are clear; finalized invoices use the frozen `zelle_recipient_snapshot`.

## Void And Reissue

Void requires a reason and preserves the number, snapshots, PDF, and checksum. Source sessions become eligible for a new invoice with a new number. Payments and delivery are not automatically handled by void; existing payment records and allocations remain in the ledger and are not deleted.

## Client Page Invoice History

The client workspace displays a read-only invoice history table for all invoices addressed to billing parties belonging to that person and can identify invoices filed under that client. Void invoices show zero balance. No payment, finalization, or void controls appear on the client page — those actions remain on the dedicated invoice view. The client page now shows account summary cards (Total Finalized Invoices, Total Payments Applied, Current Balance, Account Status) powered by `client_account_summary`. The invoice table includes Payment Status and Paid columns. Session tables use "Payment Handling" with labels "Invoice billing" and "Paid at session".

## Payment Ledger Foundation

Migration `003_payment_ledger_foundation` adds two additive tables — `payments` and `payment_allocations` — as the schema foundation. Migration `004_payment_provenance` adds provenance columns to `payments`. Backend payment services are implemented in `payment_services.py`.

### Key Design Decisions

- **`billing_party_id` is the authoritative payment owner** on `payments`. This is the entity that owes the invoice.
- **`received_from_name`** on `payments` records the payer when payment is received from someone other than the Bill To party, without changing who owes the invoice.
- **`session_id` is the durable allocation target** on `payment_allocations`. It is NOT NULL and allows a payment to be recorded before the session has an invoice line.
- **`invoice_line_item_id` is nullable** on `payment_allocations`. It may be populated later when the session is staged into an invoice draft. No payment history is deleted or recreated during this transition.
- **Unapplied money** is the payment amount minus the sum of active allocation amounts. This is computed dynamically by `payment_services.py`, not stored as a column.
- **Finalized invoice charges remain immutable.** Payment records and allocations are a separate audited ledger and may be created, allocated, reversed, or voided after invoice finalization.
- **Payment settlement may change after invoice finalization.** No constraint or documentation claims that payments cannot be applied to finalized invoices.
- **Provenance is stored directly on the payment** via `source_type` and `source_session_id` columns (migration 004). `source_type = 'manual'` for user-created payments; `source_type = 'paid_at_session_backfill'` for future backfill payments. `source_session_id` is the idempotency anchor — a unique partial index prevents a second backfill payment for the same session across all payment statuses (posted or void). Uniqueness applies even after voiding or allocation reversal. Manual payments remain distinct and do not occupy the backfill provenance slot.

### Backend Services

`payment_services.py` provides:

- `create_payment` — Creates a posted payment. Validates Bill To party exists, positive cents, required received_at. Accepts internal-only `source_type` and `source_session_id` parameters for provenance. Validates that manual payments have no source session, backfill payments have a valid matching session, and unsupported source types are rejected.
- `allocate_payment_to_session` — Allocates to a session charge using `BEGIN IMMEDIATE`. Enforces Bill To matching, payment limit, session charge limit, and invoice line consistency.
- `link_session_allocations_to_invoice_line` — Links pre-staging allocations to a later invoice line. Idempotent. Does not recreate rows.
- `reverse_allocation` — Sets status to `reversed`, preserves the row. Requires a non-empty administrative reason. Supports optional idempotency key. Rejects double reversal.
- `void_payment` — Requires all allocations reversed first. Requires a non-empty administrative reason. Supports optional idempotency key. Sets status to `void`. Rejects double void.
- `apply_available_funds` — Applies unapplied payment funds to a finalized invoice. Creates new allocation rows (never edits reversed ones). Validates payment posted, invoice finalized, Bill To match, amount within available and balance. Supports optional idempotency key.
- Read helpers: `payment_allocated_amount`, `payment_unapplied_amount`, `session_paid_amount`, `invoice_line_paid_amount`, `get_payment_detail`.
- Round 1 invoice-payment helpers: `list_outstanding_invoices`, `list_invoice_payment_history`, and `record_invoice_payment`.
- `dry_run_paid_at_session_backfill` — Read-only analyzer that classifies `paid_at_session` sessions into eligibility categories and returns a sanitized aggregate report. Performs no writes. Classification order: already backfilled, not approved, missing Bill To, missing/invalid amount, missing/invalid date, existing manual allocation conflict, eligible. Amount priority: `rate_cents_snapshot` then `approved_rate_cents`. Date priority: `session_date` then `start_at`.

`receipt_services.py` provides manual payment receipt support:

- `preview_payment_receipt` builds a draft receipt snapshot from the current posted payment ledger without reserving a number, inserting a row, writing a file, or advancing the receipt sequence.
- `create_payment_receipt` creates one finalized receipt per posted payment. Repeated create requests return the existing receipt.
- Finalized receipts store one immutable `snapshot_json` and serve the stored PDF; they are not re-rendered from live payment or allocation state.
- Receipt PDFs are stored under the configured receipt root. Installed releases set that root to `~/Documents/Jordana Billing/Client Files`, using `Client Files/<Client Display Name>/<Month YYYY>/Receipt_<number>.pdf`.
- Invoice-linked payments inherit invoice filing ownership. Paid-at-session payments without invoices resolve an eligible session participant; ambiguous ownership blocks final creation.

### Dry-Run CLI

A local CLI command is available for running the dry-run analyzer against a specified database:

```
.venv/bin/python -m jordana_invoice.payment_backfill_cli --dry-run --db /path/to/database.sqlite
```

- An explicit `--db` path is mandatory. No default database is used.
- The connection is read-only (`file:...?mode=ro`). No WAL, SHM, or journal files are created.
- Migrations are not run. The database must already have migration `004_payment_provenance` applied.
- No `--apply` mode exists.
- Output is aggregate JSON only, followed by a read-only safety statement.
- Operators should first make and verify a private database backup before any later apply operation.
- Do not run this against the live operational database during this development round.
- Paid-at-session invoice eligibility remains unchanged.

All calculation helpers count only allocations where `payment.status = 'posted'` and `payment_allocation.status = 'active'`.

## Payment Tracking Round 1

The **Payments** workspace (formerly the "Unpaid" screen) now covers the normal payment path with a tabbed interface.

### Included

- Lists finalized, non-void invoices with a remaining balance greater than zero.
- Derives invoice `paid_cents` and `balance_cents` from the payment ledger instead of storing a second editable balance field.
- Labels invoice payment state as `unpaid`, `partially_paid`, or `paid` from those derived values.
- Records one manual payment against one invoice at a time using payment date, amount, method, reference number, received from, and administrative note.
- Re-reads invoice ownership, finalized status, current balance, and invoice lines inside the write transaction.
- Allocates each payment across line items in this deterministic order:
  1. oldest `service_date`
  2. then `sort_order`
  3. then `invoice_line_item_id`
- Applies each allocation only up to that line's unpaid amount.
- Uses one `BEGIN IMMEDIATE` transaction so payment creation plus all intended allocations succeed or fail together.
- Keeps a compact read-only payment history per invoice. Only posted payments with active allocations count toward current paid totals; reversed or voided records remain visible but inactive.

### Current limitations

- No overpayments or unapplied credits
- No multi-invoice payments
- No due dates, overdue labels, aging, reconciliation, automatic receipts, bulk receipts, or email confirmations
- No historical paid-at-session backfill
- No invoice PDF data or logic changes (PDF layout refinement applied in a separate presentation-only round; see INVOICE_TEMPLATE.md for current layout specs)

### Payments Workspace (Round 2)

The sidebar entry formerly labelled "Unpaid" is now **Payments**, a tabbed workspace with three views:

- **Outstanding** — finalized invoices with a positive remaining balance; supports recording payments (same as Round 1)
- **Paid** — finalized invoices with zero balance, showing paid date and payment method
- **All Payments** — chronological ledger of every payment with bill-to name, applied amount, and status

Shared calculation functions in `payment_services.py`:

- `list_paid_invoices(conn)` — finalized non-void invoices with zero balance
- `list_all_payments(conn)` — all payments with applied amounts and bill-to names
- `get_payment_detail_view(conn, payment_id)` — payment detail with allocations and invoice references
- `client_account_summary(conn, person_id)` — total billed, total paid, current balance, account status

API endpoints added:

- `GET /api/payments/paid-invoices`
- `GET /api/payments`
- `GET /api/payments/{payment_id}`
- `GET /api/people/{person_id}/account-summary`

### Payment Corrections (Round 3)

Migration `007_payment_corrections` adds `void_reason` to `payments`, `reversal_reason` to `payment_allocations`, and a new `idempotency_keys` table for deduplication of correction requests.

**Reversal** — `reverse_allocation` now requires a non-empty administrative reason (stored as `reversal_reason`). An optional `idempotency_key` prevents duplicate processing.

**Void** — `void_payment` now requires a non-empty administrative reason (stored as `void_reason`). An optional `idempotency_key` prevents duplicate processing.

**Apply Available Funds** — `apply_available_funds` applies unapplied funds from a posted payment to a finalized invoice. Creates new allocation rows (never edits reversed ones). Validates payment posted, invoice finalized, Bill To match, amount within available and balance. Supports optional idempotency key.

**Correction History** — `get_payment_correction_history` returns audit-log-derived entries for allocation reversals, payment voids, and fund applications. `get_payment_detail_view` now includes correction history, `void_reason`, `voided_at`, and per-allocation `reversal_reason` and `reversed_at`.

**UI** — The payment detail overlay replaces the former `alert()` display. It shows payment fields, allocation table with per-row reverse buttons, correction history table, an apply-funds form (when unapplied funds exist), and a void payment form (when posted).

API endpoints added:

- `POST /api/payments/allocations/{allocation_id}/reverse`
- `POST /api/payments/{payment_id}/apply-funds`
- `POST /api/payments/{payment_id}/void`

### What Is Not Implemented

- No apply mode exists — only the read-only dry-run analyzer and its CLI are available.
- No historical payment records have been created — provenance schema, service validation, and dry-run analysis exist but the backfill has not been run.
- No paid-at-session eligibility transition — paid-at-session sessions remain excluded from invoicing.
- No invoice totals changes (no `paid_cents`, `balance_cents`, or settlement-status columns on invoices).
- Payment tracking beyond Round 3 remains unfinished: credits, multi-invoice payments, reconciliation, and month-close workflows still belong to later rounds. The implemented payment ledger, allocations, invoice payment history, and applying available funds are all functional.

## Invoice Library

The Invoices view now includes a searchable, filterable, paginated invoice library.

### Enhanced List Endpoint

```
GET /api/invoices
```

Query parameters (all optional):

- `status` — `draft`, `finalized`, or `void`
- `search` — free-text search on invoice number or Bill To name
- `bill_to_party_id` — filter by billing party UUID
- `participant_person_id` — filter by participant person UUID (joins through `session_participants`)
- `payment_status` — `unpaid`, `partially_paid`, `paid`, or `void` (derived field, post-filtered)
- `invoice_date_from` / `invoice_date_to` — invoice date range (ISO date `YYYY-MM-DD`)
- `billing_month` — filter by `YYYY-MM` billing month
- `service_period_from` / `service_period_to` — billing period start/end range
- `sort_by` — `invoice_date` (default), `invoice_number`, `total_cents`, `created_at`, or `bill_to_name`
- `sort_dir` — `desc` (default) or `asc`
- `limit` — page size (default 50)
- `offset` — pagination offset (default 0)

**Response**: a paginated dict `{ items, total, limit, offset }`. Each item includes all invoice columns plus `current_bill_to_name`, `line_count`, `participants_display` (deduplicated), `paid_cents`, `balance_cents`, and `payment_status`.

### Print Preview (Draft Only, HTML)

```
GET /api/invoices/{invoice_id}/print-preview
```

Returns a self-contained HTML page with a **DRAFT** watermark and banner. Side-effect free: does not write to the database, generate PDFs, assign invoice numbers, or change any state. Only available for draft invoices; finalized or void invoices return HTTP 400.

### Draft PDF Preview

```
GET /api/invoices/{invoice_id}/draft-pdf
POST /api/invoices/{invoice_id}/draft-pdf
GET /api/invoices/{invoice_id}/finalization-preview-pdf
POST /api/invoices/{invoice_id}/finalization-preview-token
```

Returns a real PDF preview of a draft invoice using the same canonical ReportLab renderer (`_generate_invoice_pdf_bytes`) as final invoice generation. The PDF is clearly marked **DRAFT** and does not assign an invoice number. Side-effect free: does not write to the database, does not write `pdf_path` or `pdf_sha256`, does not change invoice status or revision, and does not create any audit event. Missing readiness errors (e.g. missing address or email) do not block the preview. Only available for draft invoices; finalized or void invoices return HTTP 400.

The Review & Finalize confirmation step embeds the same-origin `GET /api/invoices/{id}/finalization-preview-pdf` endpoint instead of a blob URL or separate HTML invoice design. Optional insurance/coding preview values are stored only in a short-lived in-memory preview token and are not written to SQLite or persisted until the user explicitly confirms finalization.

Both draft PDF and final PDF endpoints use dedicated inline PDF response headers (`Content-Type: application/pdf`, `Content-Disposition: inline`) compatible with Safari. PDF responses use `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer` but do not apply the `X-Frame-Options: DENY` or CSP headers used for HTML/JSON responses, allowing inline browser preview.

### Final PDF Serving

```
GET /api/invoices/{invoice_id}/final-pdf
```

Serves the stored PDF file for finalized or void invoices. Returns the raw PDF bytes with `Content-Type: application/pdf` and `Content-Disposition: inline`. Does not expose the file path to the client. Returns HTTP 400 for draft invoices, HTTP 404 if the invoice or PDF file is missing.

### Normalize Duplicate Payer Billing Parties

```
POST /api/billing-relationships/normalize-payer
```

Audited normalization of duplicate active person-linked billing parties for a given payer. Request body: `{ "person_id": "...", "canonical_billing_party_id": "..." (optional) }`. Selects or establishes one canonical active billing-party record, copies missing contact/delivery fields from redundant records (never overwriting non-empty canonical fields), deactivates redundant records, repoints safe mutable references (account defaults, draft-only invoice/session references), and leaves finalized invoices, snapshots, PDF paths, and payment ownership unchanged. Returns a structured summary of the merge operation.

## Prior Unpaid Balance & Account Summary Presentation

Invoices clearly show current-period charges alongside unpaid balances from earlier finalized invoices for the same payer responsibility.

### Calculations
For a given invoice:
- **Current Period Charges**: The sum of the line items of the current invoice.
- **Payments Applied**: The total payment amount currently allocated to the current invoice.
- **Current Invoice Balance**: `max(Charges - Payments, 0)`. Forced to `0` for void invoices.
- **Prior Unpaid Balance**: The sum of the remaining unpaid balances of prior finalized, non-void invoices for the same responsibility, net of their own payments.
- **TOTAL AMOUNT DUE**: `Current Invoice Balance + Prior Unpaid Balance`.

This prior balance is displayed as a summary block and does not create duplicate service lines.

### Same-Date Cutoff Ordering
Determining whether an invoice is "prior" relative to the current one uses a strict deterministic ordering:
1. **Invoice Date**: Candidate is prior if `candidate.invoice_date < current.invoice_date`.
2. **Tie-Breaker 1 (Finalized vs Draft)**: If dates match, a finalized invoice is prior to a draft invoice.
3. **Tie-Breaker 2 (Finalized Timestamps)**: If both are finalized and dates match, candidate is prior if `candidate.finalized_at < current.finalized_at`.
4. **Tie-Breaker 3 (UUID comparison)**: If finalized at the exact same millisecond, candidate is prior if `candidate.invoice_id < current.invoice_id` (alphabetically).

### Persistence & Immutability
- **Snapshot Finalization**: During finalization, the calculated summary is frozen in a versioned JSON snapshot (version 1) in the database (`account_summary_snapshot`). The PDF generated reflects this frozen snapshot and remains immutable.
- **Live Status Card**: The local UI details page displays the frozen "As-Finalized" summary side-by-side with the current live status (live paid amount and current remaining balance from the payment ledger).
- **Legacy Invoices**: Legacy invoices finalized before this implementation (where `account_summary_snapshot` is NULL) are handled gracefully. The system bypasses the frozen snapshot view and displays only the live ledger status, notifying the operator that the historical snapshot is unavailable.
- **Void Invoices**: Void invoices carry a current balance of zero and do not contribute to subsequent prior unpaid balance calculations.

### Unimplemented Features
The following features are **not implemented** in this round and remain out of scope:
- Automatic receipts, bulk receipts, and receipt correction workflows
- Paid Invoice documents
- Optional prior-invoice PDF packets
- Email or mail delivery and delivery tracking
- Paid-at-session backfill apply mode (dry-run only)
- Credits, write-offs, or refunds
- Automated multi-invoice payment allocation
- Reconciliation and month-close workflows
