# Invoice Lifecycle

## Eligibility

A session must be approved, have participants and bill-to, preserve a nonnegative actual charged amount, not be future scheduled, not be excluded/personal/admin, not be marked "Paid at time of session", retain raw evidence, and not belong to another draft/finalized invoice. Cancelled/no-show records require explicit `billing_treatment=billable`.

## Draft

Drafts can add/remove eligible sessions, reorder lines, edit invoice-only descriptions, override delivery, and change dates. Totals use integer cents. Source sessions are not edited.

### Monthly Invoice Identity

Drafts may optionally carry a `billing_month` (`YYYY-MM`) that identifies the invoice as belonging to a specific calendar month. When `billing_month` is provided, the billing period start and end are derived automatically as the first and last day of that month. When only `billing_period_start` and `billing_period_end` are provided, `billing_month` is derived only if the period is exactly one complete calendar month; otherwise `billing_month` is `NULL` (legacy or nonmonthly).

At most one open draft may exist per Bill To party and billing month. Finalized and void invoices do not block new drafts for the same month. `supplement_sequence` 0 marks the original monthly draft; 1+ is reserved for supplemental drafts.

### Monthly Staging Service

A backend reconciliation service `stage_approved_sessions_to_monthly_drafts()` now exists in `invoice_services.py`. It is idempotent: repeated calls produce the same correct result.

The service groups eligible approved sessions by `billing_party_id` + calendar billing month and reconciles them into monthly draft invoices. For each (party, month) group it uses one `BEGIN IMMEDIATE` transaction:

- Finds the existing open monthly draft for the party and month, or creates one if none exists and there are eligible sessions to stage.
- If prior finalized or void invoices exist for the party and month, assigns `supplement_sequence = MAX(existing) + 1` for the new draft.
- Adds only sessions not already attached to a draft or finalized invoice.
- Reuses existing line snapshot creation logic.

Stale draft lines are reconciled before finalization:

- If a session's Bill To party no longer matches the invoice, the line is moved atomically to the correct target monthly draft.
- If a session date no longer belongs to the invoice's billing month, the line is moved atomically to the correct target monthly draft.
- If a session is no longer eligible, the line is removed and the session is left unstaged.
- Finalized and void invoice lines are never moved or modified.

The service returns a structured summary with counts of drafts created/reused, sessions staged/already staged/moved/removed as ineligible, sessions skipped with reasons, and errors by party/month. It does not expose private names in returned diagnostic identifiers.

The service is **not yet connected to**:
- session approval (no automatic trigger on approval);
- API routes (no HTTP endpoint);
- UI controls (no button or menu item).

Paid-at-session sessions remain excluded from staging temporarily. Payment behavior will be changed in a later dedicated round.

## Finalized

Finalization is a two-step process:

1. **Preview**: Save the complete draft, reread from SQLite, run `validate_invoice_readiness` to check all readiness rules, and return a preview with a `revision` number for optimistic locking and a `readiness` object with `ready` (bool) and `errors` (list of `{field, message}` dicts). The UI shows "Ready to finalize" or "Not ready to finalize" with specific fixes, and disables the finalize button while errors exist.
2. **Confirm**: Finalize only if the invoice revision matches the preview and `validate_invoice_readiness` passes. This prevents stale or double submissions.

### Readiness Validation

A single authoritative function `validate_invoice_readiness` is used in both preview and confirm. It checks:

- Bill-to party exists and is active
- At least one eligible invoice line
- All line amounts are positive
- Valid invoice date
- Active business profile
- Required bill-to contact details for the selected delivery method (email for email/both, mailing address for mail/both)
- Required business/payee/payment-address details used on the invoice
- Valid, unique invoice number generation
- All included sessions remain invoice-eligible
- Preview revision is not stale (when `expected_revision` is provided)

Validation errors are structured as `{field, message}` for UI display. No validation logic is duplicated between frontend and backend.

Explicit confirmation starts a transaction that revalidates readiness, checks the revision matches, assigns the number, freezes bill-to/business/line snapshots, calculates totals, writes the PDF atomically, stores SHA-256, and audits finalization. Failure rolls back and removes partial output. The finalized snapshot and PDF exactly match the preview.

## Void And Reissue

Void requires a reason and preserves the number, snapshots, PDF, and checksum. Source sessions become eligible for a new invoice with a new number. Payments and delivery are deferred.

## Client Page Invoice History

The client workspace displays a read-only invoice history table for all invoices addressed to billing parties belonging to that person. Void invoices show zero balance. No payment, finalization, or void controls appear on the client page — those actions remain on the dedicated invoice view. The **Finalized Invoice Total** reflects non-void finalized invoice totals only. Payment tracking is not yet implemented; session payment status (Unpaid / Paid at time of session) is separate from invoice payment tracking.
