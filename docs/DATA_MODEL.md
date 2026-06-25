# Data Model

The database is local SQLite. Internal UUIDs are primary keys. Human-readable person and account codes are secondary identifiers.

Invoice development adds authoritative `business_profile`, `service_catalog`, `invoice_sequences`, `invoices`, and `invoice_line_items` tables. Finalized values are snapshots and are not reconstructed from current records. See `docs/INVOICE_MODEL.md`.

## Phase 2 Relationship Model

The app separates actual people, client accounts, account members, session participants, billing parties, calendar aliases, and rate rules.

This lets Simon attend a session while a parent receives the invoice. It also lets Fred and Bobsey attend together with one bill-to party and one charge without creating a visible household account during routine review.

## `sync_state`

One row per remote source. `google_calendar_snapshots` stores the Apps Script cursor, last attempt, last success, last error, and total local rows imported by sync.

The cursor is an `ingested_at` timestamp and advances only after a successful local transaction.

## `import_runs`

One row per CSV import or remote sync import. Tracks source path, import time, row count, completed run count, and status.

## `raw_calendar_snapshots`

Immutable preserved calendar evidence. Stores every imported raw row and normalized raw JSON.

Important fields:

- `run_id`
- `snapshot_key`
- `batch_name`
- `capture_window`
- `captured_at`
- `event_title`
- `start_at`
- `end_at`
- `duration_minutes`
- `notes`
- `raw_json`

## `calendar_event_candidates`

Collapsed event candidates built from raw snapshots. Stores parser output, classification, confidence, and review fields.

Phase 2 fields include confidence label, unresolved fields, review reasons, candidate people, service mode, rate group, evening/weekend category, and reconciliation status.

## `people`

Actual humans with permanent UUIDs. Person codes are optional human-readable helpers and are generated only after first and last names are confirmed. The code is not the primary key and is not silently changed when a name changes.

## `client_accounts`

Billing and relationship units such as an individual, household, family, couple, or organization. Account codes use the separate `ACCT-####` sequence and never imitate person codes.

Accounts are backend relationship support. Routine session review does not require Jordana to select a Client / Family Account.

## `account_members`

Join table connecting people to accounts with roles such as primary, spouse, child, parent, family member, payer, or other.

## `billing_parties`

The person or organization responsible for payment. The billing party is not assumed to be the session participant.

## `calendar_aliases`

Reusable reviewed aliases for client names, household aliases, or personal/admin exclusions.

## `rate_rules`

Effective-dated suggested-rate rules. Rules can be global, account-specific, person-specific, duration-specific, billing-session-type-specific, service-mode-specific, rate-group-specific, time-category-specific, appointment-status-specific, or an exact participant-combination exception. `appointment_status` supports `scheduled`, `cancelled`, and `no_show`; a `NULL` value is a wildcard.

## `rate_rule_participants`

Join table connecting a rate rule to the exact set of people required for a joint-session exception. Matching is order-independent; Fred + Bobsy equals Bobsy + Fred. The rule does not apply when only one member attends.

## `clients`

Canonical client records. These are not inferred automatically from shorthand in Phase 1.

## `client_aliases`

Calendar aliases that map shorthand or exclusion phrases to clients or non-client classifications. Aliases start unconfirmed unless reviewed.

## `client_rates`

Time-bound rates for clients. Rates are copied into finalized invoice/session history later so old invoices do not change when rates change.

## `sessions`

Proposed normalized billable sessions. In Phase 1 these remain `proposed` and `needs_review`.

Phase 2 sessions store optional account, billing party, date, parsed and approved duration, service mode, rate group, time category, suggested rate, approved/actual charged rate, suggested and approved rate provenance, payment status, and raw calendar title.

## `session_participants`

Join table for one or more participants in a single session. Multiple participants do not mean multiple charges.

## `review_queue`

Human decisions needed before billing facts become reliable.

## `review_items`

Structured review-state records for the future dashboard. Review decisions persist here, not in CSV.

## Local API Service

The review UI uses backend service functions and local API routes for candidate listing, retrieval, save, approval, inline person/account/billing-party creation, alias learning, and audit history. Frontend code must not write SQLite directly.

Section-level review APIs save participants, bill-to, person corrections, and session drafts independently. After each section save, `refresh_candidate_suggestions` recomputes payer, rate, checklist, unresolved fields, and review status.

## `audit_log`

Append-only record of parsing, proposed sessions, review decisions, and future invoice actions.

## Historical Integrity Rules

- Never edit raw snapshots.
- Never import the same non-empty `snapshot_key` twice.
- Preserve event versions and review decisions.
- Never rewrite historical finalized invoice values after rate changes.
- Never add clinical interpretation to this app.
## Calendar Status Additions

Authoritative raw calendar evidence remains in `raw_calendar_snapshots`. The existing `calendar` payload/header maps to `calendar_name`.

Additive tables/fields for this round:

- `calendar_preferences`: optional source-calendar disposition rules.
- `app_metadata`: database-level metadata such as explicit `demo_mode=true`.
- `calendar_event_candidates.appointment_status`: scheduled/completed/cancelled/no_show/unresolved.
- `sessions.appointment_status`: copied to the current session for review and future invoice selection.
- `billing_treatment`: separate human billing decision for cancelled/no-show appointments.
- `title_time_text`, `title_time_normalized`, `title_time_matches_calendar`: validation evidence; Calendar `start_at` remains authoritative.
- `calendar_disposition`, `calendar_is_preferred_work`, `hidden_from_review`: review filtering metadata.

Do not use `payment_status` for cancellation/no-show meaning. Do not delete raw snapshots when a calendar is personal/admin or hidden.

## Billing Session Type Additions

Additive fields for session normalization:

### `calendar_event_candidates` and `sessions`

- `billing_session_type`: One of `psychotherapy`, `psychotherapy_house_call`, `psychotherapy_weekend`, `psychotherapy_evening`, `custom`.
- `appointment_method`: One of `office`, `phone`, `facetime`, `unknown`. Internal evidence, not a billing type.
- `duration_choice`: One of `30`, `60`, `90`, `120`, `custom`. Standard billing increments.
- `custom_duration_minutes`: Actual minutes when `duration_choice=custom`.
- `house_call_suggested`: Boolean flag when location suggests House Call but explicit confirmation needed.
- `billing_type_source`: One of `auto`, `manual`, `location_inferred`.
- `location_text`: Preserved location field for House Call detection.

### `sessions` only

- `custom_service_description`: User-provided description for Custom billing type.
- `custom_service_code`: Optional admin code for future insurance integration.

### `service_catalog`

- `catalog_type`: One of `billing_session_type`, `appointment_method`.
- `legacy_appointment_method`: Boolean flag marking Office/Phone/FaceTime as legacy appointment methods.

### `invoice_line_items`

- `billing_session_type_snapshot`: Frozen billing type at finalization.
- `custom_service_description_snapshot`: Frozen custom description.
- `custom_service_code_snapshot`: Frozen custom code.

### `custom_service_mappings`

New table for person+duration custom code foundation:

- `mapping_id`: Primary key.
- `person_id`: References `people`.
- `duration_choice`: Standard or custom duration.
- `custom_description`: Reusable custom service description.
- `custom_code`: Optional admin code.
- `active`: Boolean for soft delete.

This table enables future insurance coding by storing per-person, per-duration custom service mappings.

### Schema Change Policy

All changes are additive. The existing `service_mode` column is preserved for backward compatibility. New `billing_session_type` and `appointment_method` columns separate billing concerns from appointment evidence.

## Payment Ledger Foundation (Migration 003)

### Overview

Migration `003_payment_ledger_foundation` adds two additive tables for recording money received and applying it to session charges. This is a schema-only foundation. No payment services, backfill, eligibility changes, invoice totals, UI, API routes, or PDF behavior are implemented yet.

### `payments`

Records money received from a Bill To party.

- `payment_id`: Permanent UUID primary key.
- `billing_party_id`: Authoritative payment owner. References `billing_parties`. This is the entity that owes the invoice.
- `amount_cents`: Positive integer cents. CHECK constraint enforces > 0.
- `received_at`: ISO timestamp when money was received.
- `method`: Payment method (cash, check, card, transfer, other). Default `'other'`.
- `reference_number`: Optional check number or transaction ID.
- `received_from_name`: Records the payer when payment is received from someone other than the Bill To party. Does not change who owes the invoice.
- `administrative_note`: Free-text administrative note.
- `status`: `'posted'` (active) or `'void'` (cancelled). CHECK constraint enforces valid values. No `refunded` status in this round — refunds are a separate future model.
- `voided_at`: Timestamp when voided.
- `created_at`, `updated_at`: Standard timestamps.

Indexes: `idx_payments_billing_party` (billing_party_id, received_at), `idx_payments_status` (status).

### `payment_allocations`

Applies a payment to a session charge. One allocation row links one payment to one session.

- `allocation_id`: Permanent UUID primary key.
- `payment_id`: References `payments`. No cascade delete — payment history must not disappear.
- `session_id`: Durable allocation target. NOT NULL. References `sessions`. This is the anchor that connects a payment to a charge before an invoice line exists.
- `invoice_line_item_id`: Nullable. References `invoice_line_items`. Populated when the session is later staged into an invoice draft. NULL means the payment is recorded but the session has not yet been staged.
- `amount_cents`: Positive integer cents. CHECK constraint enforces > 0.
- `status`: `'active'` or `'reversed'`. CHECK constraint enforces valid values. Reversing an allocation sets status to `reversed` and records `reversed_at`, preserving history rather than deleting.
- `reversed_at`: Timestamp when reversed.
- `created_at`, `updated_at`: Standard timestamps.

Indexes: `idx_allocations_payment` (payment_id), `idx_allocations_session` (session_id), `idx_allocations_invoice_line` (invoice_line_item_id WHERE NOT NULL), `idx_allocations_session_active` (session_id, status WHERE active).

No uniqueness constraints prevent multiple payments to one session, one payment across multiple sessions, or multiple partial allocations from the same payment to the same session.

### Finalized Invoice Charges vs Payment Ledger

Finalized invoice charges and finalized document snapshots are immutable. Payment records and allocations are a separate audited ledger and may be created, allocated, reversed, or voided after invoice finalization. Payments may be applied to finalized invoices.

### Unapplied Payment Amount

Unapplied money is the payment amount minus the sum of active allocation amounts for that payment. This is computed by the application, not stored as a column.

### Schema-Enforced vs Application-Enforced Invariants

The database schema directly enforces only:

- Valid foreign keys.
- Positive payment and allocation amounts (CHECK > 0).
- Allowed status values (CHECK constraints).
- Required identifiers and timestamps (NOT NULL).

The following are intentionally deferred to the payment-service round and are not schema-enforced:

- Allocations not exceeding a payment amount.
- Allocations not exceeding a session charge.
- Bill To party matching between payment and session.
- Consistency between session_id and invoice_line_item_id.
- Reversal and void transaction rules.
- Unapplied payment calculation.

### Not Yet Implemented

- No payment services (create, void, reverse, allocate).
- No paid-at-session backfill.
- No paid-at-session eligibility transition.
- No invoice totals changes (no `paid_cents`, `balance_cents`, or settlement-status columns on invoices).
- No UI, API routes, or PDF changes.
- Paid-at-session sessions remain excluded from invoicing.
