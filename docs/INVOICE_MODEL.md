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
