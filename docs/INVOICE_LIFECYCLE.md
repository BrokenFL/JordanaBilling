# Invoice Lifecycle

## Eligibility

A session must be approved, have participants and bill-to, preserve a nonnegative actual charged amount, not be future scheduled, not be excluded/personal/admin, not be marked "Paid at time of session", retain raw evidence, and not belong to another draft/finalized invoice. Cancelled/no-show records require explicit `billing_treatment=billable`.

## Draft

Drafts can add/remove eligible sessions, reorder lines, edit invoice-only descriptions, override delivery, and change dates. Totals use integer cents. Source sessions are not edited.

## Finalized

Finalization is a two-step process:

1. **Preview**: Save the complete draft, reread from SQLite, validate all sessions and business profile, and return a preview with a `revision` number for optimistic locking.
2. **Confirm**: Finalize only if the invoice revision matches the preview. This prevents stale or double submissions.

Explicit confirmation starts a transaction that revalidates every source session, checks the revision matches, assigns the number, freezes bill-to/business/line snapshots, calculates totals, writes the PDF atomically, stores SHA-256, and audits finalization. Failure rolls back and removes partial output. The finalized snapshot and PDF exactly match the preview.

## Void And Reissue

Void requires a reason and preserves the number, snapshots, PDF, and checksum. Source sessions become eligible for a new invoice with a new number. Payments and delivery are deferred.

## Client Page Invoice History

The client workspace displays a read-only invoice history table for all invoices addressed to billing parties belonging to that person. Void invoices show zero balance. No payment, finalization, or void controls appear on the client page — those actions remain on the dedicated invoice view. The **Finalized Invoice Total** reflects non-void finalized invoice totals only. Payment tracking is not yet implemented; session payment status (Unpaid / Paid at time of session) is separate from invoice payment tracking.
