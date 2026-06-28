# Invoice Model

`sessions` remains authoritative for approved occurrence facts and actual charged amount. `billing_parties` provides current payer/contact defaults. `business_profile` stores one active local invoice identity. `service_catalog` stores reusable current labels. `invoices` and `invoice_line_items` store lifecycle plus frozen snapshots.

All IDs are UUIDs and all money is integer cents. Drafts have no permanent number. Finalized and void invoices are immutable. Current person, payer, profile, service, or rate changes never rewrite finalized snapshots. The `invoices.revision` column increments on every draft mutation and enables optimistic locking during two-step finalization.

`invoice_sequences` stores the last number used per year. The configurable default is `YYYY-NNNN`; numbers are assigned inside finalization and never reused.

`billing_parties.preferred_delivery_method` plus the current billing email/address determine the customer-facing Bill To destination for draft previews. Finalized invoices freeze the chosen delivery method together with `bill_to_email_snapshot` and `bill_to_address_snapshot`, so later billing-party edits do not rewrite historical invoices.

`business_profile.zelle_recipient` stores the required Zelle destination for new invoices. Finalization freezes it into `invoices.zelle_recipient_snapshot`. Draft previews may show current settings, but finalized invoices always render the frozen snapshot.

`sessions.service_mode` remains historical text while `service_catalog_id` is additive. Legacy client/rate tables remain untouched.

## Monthly Invoice Identity

`invoices.billing_month` is a nullable `TEXT` column storing a canonical `YYYY-MM` key. When non-null, it identifies the invoice as belonging to a specific calendar month for monthly staging. When `NULL`, the invoice is legacy or nonmonthly and is excluded from automatic monthly staging.

`invoices.supplement_sequence` is a non-negative `INTEGER` (default 0). Value 0 marks the original monthly series entry. Values 1+ are reserved for supplemental drafts created after the original invoice for that month was finalized or voided. The staging service assigns supplemental sequences automatically when creating new drafts for a month that has prior finalized or void invoices.

A partial unique index `idx_invoices_draft_party_month` enforces that at most one open (`draft`) invoice may exist per `bill_to_party_id` + `billing_month`. Finalized and void invoices do not block new drafts for the same month.

## Monthly Staging

A backend reconciliation service `stage_approved_sessions_to_monthly_drafts()` reconciles eligible approved sessions into monthly draft invoices grouped by `billing_party_id` + calendar billing month. It is idempotent and uses one `BEGIN IMMEDIATE` transaction per (party, month) group. It reuses existing eligibility rules, line snapshot creation, and the monthly identity columns. Stale draft lines whose session party or date month changed are moved atomically. Finalized and void invoices remain immutable. The service is not yet connected to approval, API, or UI. Paid-at-session sessions remain excluded temporarily.

## Invoice Line Descriptions

Invoice line descriptions use the **billing session type labels**:

- Psychotherapy Session
- Psychotherapy Session / House Call
- Psychotherapy Session / Weekend
- Psychotherapy Session / Evening
- Custom (uses saved custom description)

Office, Phone, FaceTime, Unknown, or legacy service names are **never** generated for new invoice lines. These are appointment methods, not billing session types.

## Invoice Line Item Snapshots

Each invoice line item stores frozen snapshots at finalization:

- `billing_session_type_snapshot` — The billing session type at finalization
- `custom_service_description_snapshot` — Custom description if applicable
- `custom_service_code_snapshot` — Custom code if applicable

Finalized invoice snapshots remain immutable. Current session or catalog changes never rewrite finalized line items.

## Payments Workspace

The sidebar entry formerly labelled "Unpaid" is now **Payments**, a tabbed workspace with three views:

- **Outstanding** — finalized invoices with a positive remaining balance; supports recording payments
- **Paid** — finalized invoices with zero balance, showing paid date and payment method
- **All Payments** — chronological ledger of every payment with bill-to name, applied amount, and status

### Shared Payment Calculations

`payment_services.py` provides four shared functions used by both the API and the review services:

- `list_paid_invoices(conn)` — finalized non-void invoices with zero balance, includes `paid_date` and `payment_method`
- `list_all_payments(conn)` — ledger of all payments with bill-to name, invoice references, and applied amounts
- `get_payment_detail_view(conn, payment_id)` — detailed payment info with allocation breakdown, invoice references, correction history, and void/reversal reasons
- `get_payment_correction_history(conn, payment_id)` — audit-log-derived list of allocation reversals, payment voids, and fund applications
- `apply_available_funds(conn, payment_id, *, invoice_id, amount_cents, idempotency_key)` — apply unapplied payment funds to a finalized invoice
- `client_account_summary(conn, person_id)` — total billed, total paid, current balance, and account status for a person

### API Endpoints

| Endpoint | Description |
|---|---|
| `GET /api/payments/outstanding-invoices` | List finalized invoices with positive balance |
| `GET /api/payments/paid-invoices` | List finalized invoices with zero balance |
| `GET /api/payments` | List all payments chronologically |
| `GET /api/payments/{payment_id}` | Payment detail with allocations, correction history, and void info |
| `POST /api/invoices/{invoice_id}/payments` | Record a payment against an invoice |
| `POST /api/payments/allocations/{allocation_id}/reverse` | Reverse an active allocation with a required reason |
| `POST /api/payments/{payment_id}/apply-funds` | Apply available unapplied funds to a finalized invoice |
| `POST /api/payments/{payment_id}/void` | Void a posted payment with a required reason |
| `GET /api/people/{person_id}/account-summary` | Account summary for a person |

### Client Page Integration

Client record pages display account summary cards (Total Finalized Invoices, Total Payments Applied, Current Balance, Account Status). The invoice table includes Payment Status and Paid columns. The session table uses "Payment Handling" with friendly labels: "Invoice billing" and "Paid at session".
