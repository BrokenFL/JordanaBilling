# Invoice Lifecycle

## Eligibility

A session must be approved, have participants and bill-to, preserve a nonnegative actual charged amount, not be future scheduled, not be excluded/personal/admin, retain raw evidence, and not belong to another draft/finalized invoice. Cancelled/no-show records require explicit `billing_treatment=billable`.

## Draft

Drafts can add/remove eligible sessions, reorder lines, edit invoice-only descriptions, override delivery, and change dates. Totals use integer cents. Source sessions are not edited.

## Finalized

Explicit confirmation starts a transaction that revalidates every source session, assigns the number, freezes bill-to/business/line snapshots, calculates totals, writes the PDF atomically, stores SHA-256, and audits finalization. Failure rolls back and removes partial output.

## Void And Reissue

Void requires a reason and preserves the number, snapshots, PDF, and checksum. Source sessions become eligible for a new invoice with a new number. Payments and delivery are deferred.
