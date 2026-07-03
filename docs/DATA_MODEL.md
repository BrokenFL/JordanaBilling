# Data Model

The database is local SQLite. Internal UUIDs are primary keys. Human-readable person and account codes are secondary identifiers.

Invoice development adds authoritative `business_profile`, `service_catalog`, `invoice_sequences`, `invoices`, and `invoice_line_items` tables. Finalized values are snapshots and are not reconstructed from current records. See `docs/INVOICE_MODEL.md`.

## Phase 2 Relationship Model

The app separates actual people, client accounts, account members, session participants, billing parties, calendar aliases, and rate rules.

This lets Simon attend a session while a parent receives the invoice. It also lets Fred and Bobsey attend together with one bill-to party and one charge without creating a visible household account during routine review.

## `sync_state`

One row per remote source. `google_calendar_snapshots` stores the Apps Script cursor, last attempt, last success, last error, and total local rows imported by sync.

Legacy cursors may be an `ingested_at` timestamp string. New successful syncs
store a backward-compatible composite cursor containing `ingested_at` plus
`snapshot_key`; this prevents rows with the same ingest timestamp from being
skipped across pagination boundaries. The cursor advances only after a
successful local transaction and report write.

Additional status fields store the last sync mode, rows fetched, new raw
snapshots imported, duplicate snapshots skipped, and review items changed.
These fields power the Calendar Import status summary and do not expose API
keys, database paths, run IDs, or cursor internals.

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

## `candidate_identity_aliases`

Additive identity-resolution table for calendar candidates. It stores exact
aliases that point to the preserved candidate ID without rewriting historical
`candidate_key` values:

- `calendar_event_id`
- `event_fingerprint`
- exact structural identity hash from normalized title, start, end, duration,
  and calendar

The importer consults this table before creating a new candidate. Structural
reuse is allowed only when exact event ID/fingerprint aliases do not resolve
uniquely and exact structural identity resolves to one unique existing
candidate. Fully identified rows with both a new event ID and a new fingerprint
do not collapse solely by structure. Rows with one changed stable identifier do
not structurally reuse an approved existing session; identifier-missing rows may
reuse a protected canonical session when the structural match is unique. If
event ID and fingerprint point to different candidates, the row is kept
reviewable as an identity ambiguity.

## `candidate_duplicate_reconciliations`

Additive reconciliation ledger for future duplicate repair application. It
records the canonical candidate/session, duplicate candidate/session, status,
reason, original/applied state snapshots for reversible fields, and timestamps.
It does not store names or calendar titles. Dry-run analysis does not write to
this table; apply mode may write here only for newly created unapproved
duplicates. Applied rows are excluded from future duplicate discovery. Reversal
sets `status='reversed'` and `reversed_at` only after proving current values
still match the repair-applied state.

## `people`

Actual humans with permanent UUIDs. Person codes are optional human-readable helpers and are generated only after first and last names are confirmed. The code is not the primary key and is not silently changed when a name changes.

## `client_accounts`

Billing and relationship units such as an individual, household, family, couple, or organization. Account codes use the separate `ACCT-####` sequence and never imitate person codes.

Accounts are backend relationship support. Routine session review does not require Jordana to select a Client / Family Account.

## `account_members`

Join table connecting people to accounts with roles such as primary, spouse, child, parent, family member, payer, or other.

## `billing_parties`

The person or organization responsible for payment. The billing party is not assumed to be the session participant. `delivery_contact_person_id` stores a separate invoice delivery contact (the person who should receive the invoice), distinct from `person_id` (the payer for person-linked parties). For organization payers, `person_id` historically stored the delivery contact; `delivery_contact_person_id` is the canonical field going forward. `preferred_delivery_method` (email, mail, both, unresolved) is inherited by future draft invoices but never overwrites finalized invoice snapshots.

## `client_accounts`

Billing relationships/groupings for covered clients and the default Bill To party. `default_filing_owner_kind` plus `default_filing_owner_record_id` store the relationship-level `Save invoices under` target, which may be the billing organization, payer person, a covered client connected to the relationship, or an explicitly selected active person from the people directory. `default_filing_owner_person_id` remains for compatibility with existing person-based filing defaults. Changing this setting does not rewrite approved sessions, finalized invoices, payments, or historical PDFs.

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
- Never move, rename, or rewrite finalized invoice PDFs because a filing owner, person display name, or billing relationship changes later.
- Never add clinical interpretation to this app.

## Invoice Filing Owner Additions

Finalized invoices can freeze `filing_owner_kind`, `filing_owner_record_id`, `filing_owner_person_id`, `filing_owner_person_code_snapshot`, and `filing_owner_display_name_snapshot`. These identify the permanent connected record whose folder contains the invoice PDF and are separate from `bill_to_party_id`, participants, account membership, and payment ownership. New visible folders normally use the display-name snapshot; the frozen person code disambiguates same-name person collisions and remains internal. Existing finalized invoices may have these fields blank and must keep their stored `pdf_path` and checksum.
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
- `source_type`: `'manual'` (user-created) or `'paid_at_session_backfill'` (created by future backfill). CHECK constraint enforces valid values. Default `'manual'`. Provenance is stored directly on the payment, not in notes or reference numbers.
- `source_session_id`: The session that triggered a backfill payment. NULL for manual payments. References `sessions`. This is the idempotency anchor for paid-at-session backfill — a unique partial index prevents a second backfill payment for the same session across all payment statuses (posted or void).
- `voided_at`: Timestamp when voided.
- `created_at`, `updated_at`: Standard timestamps.

Indexes: `idx_payments_billing_party` (billing_party_id, received_at), `idx_payments_status` (status), `idx_payments_paid_at_session_source` (unique on source_session_id WHERE source_type = 'paid_at_session_backfill' AND source_session_id IS NOT NULL).

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

The following are enforced by the payment service layer (`payment_services.py`) and are not schema-enforced:

- Allocations not exceeding a payment amount.
- Allocations not exceeding a session charge.
- Bill To party matching between payment and session.
- Consistency between session_id and invoice_line_item_id.
- Reversal and void transaction rules.
- Unapplied payment calculation.

### Payment Services (Backend Only)

The module `payment_services.py` provides backend functions for the payment ledger. API routes, a tabbed Payments workspace UI, and invoice payment history are now implemented. Invoice totals themselves are not modified — paid/balance amounts are derived dynamically from the payment ledger.

**Available operations**:

- `create_payment` — Creates a posted payment for a Bill To party. Validates party exists, positive integer cents, and required received_at. Commits atomically.
- `allocate_payment_to_session` — Allocates part or all of a payment to a session charge. Uses `BEGIN IMMEDIATE`. Enforces all invariants (see below). Returns the stored allocation record.
- `link_session_allocations_to_invoice_line` — Links existing active session allocations (with NULL `invoice_line_item_id`) to a newly created invoice line. Validates line belongs to session and Bill To consistency. Idempotent. Does not recreate rows or change amounts.
- `reverse_allocation` — Changes an allocation from `active` to `reversed`. Sets `reversed_at`. Never deletes the row. Rejects double reversal.
- `void_payment` — Changes a payment from `posted` to `void`. Rejects if active allocations exist. Sets `voided_at`. Rejects double void.
- `get_payment_detail` — Returns payment with allocations and computed allocated/unapplied amounts.
- `payment_allocated_amount` — Sum of active allocations for a posted payment.
- `payment_unapplied_amount` — Payment amount minus active allocations (zero for void payments).
- `session_paid_amount` — Active allocated amount for a session.
- `invoice_line_paid_amount` — Active allocated amount for an invoice line.

**Transaction boundaries**: All write operations that validate totals (`allocate_payment_to_session`, `link_session_allocations_to_invoice_line`, `reverse_allocation`, `void_payment`) use `BEGIN IMMEDIATE` so validation and write are atomic. `create_payment` commits atomically. `DatabaseBusyError` is raised when the database is locked.

**Allocation invariants** (enforced in `allocate_payment_to_session`):

1. Payment exists and status is `posted`.
2. Session exists.
3. Payment Bill To party equals session `billing_party_id`.
4. Allocation amount is a positive integer.
5. Sum of active allocations for the payment plus new allocation does not exceed payment amount.
6. Sum of active allocations for the session plus new allocation does not exceed session charge (prefers `rate_cents_snapshot`, then `approved_rate_cents`).
7. If `invoice_line_item_id` is supplied: line exists, line's `source_session_id` equals `session_id`, and line's invoice Bill To party equals payment Bill To party.
8. Finalized invoice charges may receive payment allocations — allocation creation does not modify invoice lines, rates, totals, snapshots, or finalized PDFs.

**Reversal and void behavior**:

- Reversing an allocation sets `status = 'reversed'` and `reversed_at`. The row is preserved. Reversed allocations are excluded from active totals. Double reversal raises `ValueError`.
- Voiding a payment requires all allocations to be reversed first. Void sets `status = 'void'` and `voided_at`. Void payments contribute zero to paid totals. Double void raises `ValueError`.

**Audit behavior**: Audit entries are recorded for `payment_created`, `allocation_created`, `allocations_linked`, `allocation_reversed`, and `payment_voided`. Audit details contain only internal IDs and monetary amounts. No client names, payer names, calendar titles, reference numbers, administrative-note content, or private descriptions are included.

**Finalized-invoice payment behavior**: Payments may be applied to finalized invoice lines. The allocation does not alter the invoice line, invoice totals, snapshots, or PDF. Payment settlement may change after invoice finalization.

### Dry-Run Backfill Analyzer (Read-Only)

`dry_run_paid_at_session_backfill(conn)` analyzes sessions marked `paid_at_session` and returns a sanitized aggregate report. The function performs **no writes** — no INSERT, UPDATE, DELETE, commit, audit, or migration. It classifies every `paid_at_session` session into exactly one category using this decision order:

1. **already_backfilled** — a payment with `source_type = 'paid_at_session_backfill'` exists for the session (regardless of payment or allocation status)
2. **not_approved** — `review_status != 'approved'`
3. **missing_billing_party** — `billing_party_id` is NULL
4. **missing_or_invalid_amount** — neither `rate_cents_snapshot` nor `approved_rate_cents` is a positive integer
5. **missing_or_invalid_date** — neither `session_date` nor `start_at` can be parsed
6. **existing_manual_allocation_conflict** — an active allocation from a manual payment exists for the session
7. **eligible** — passes all checks

Amount priority: `rate_cents_snapshot` (if positive) preferred over `approved_rate_cents`. Rate disagreements are counted separately. Date priority: `session_date` preferred over `start_at`. Manual allocation conflicts are not modified — the analyzer only reports them. The report contains only aggregate counts and totals — no session IDs, payment IDs, names, or private text.

### Dry-Run CLI

A local command-line interface is available in `app/jordana_invoice/payment_backfill_cli.py`:

```
.venv/bin/python -m jordana_invoice.payment_backfill_cli --dry-run --db /path/to/database.sqlite
```

- An explicit `--db` database path is mandatory. No default operational database is used.
- The database is opened in strict read-only mode (`file:...?mode=ro`). No WAL, SHM, or journal files are created.
- Migrations are not run. If the database lacks migration `004_payment_provenance`, the command fails with exit code 3 and a sanitized message.
- No `--apply` mode exists.
- Output is formatted JSON containing only the aggregate report, followed by a read-only safety statement.
- Exit codes: 0 = success, 2 = invalid arguments or path, 3 = schema/open failure, 1 = other failure.
- Operators should first make and verify a private database backup before any later apply operation.
- Do not run this against the live operational database during this development round.

### Still Not Implemented

- No apply mode exists — only the read-only dry-run analyzer and its CLI are available.
- No historical payment records have been created — provenance schema, service validation, and dry-run analysis exist but the backfill has not been run.
- No paid-at-session eligibility transition.
- No `paid_cents`, `balance_cents`, or settlement-status columns on invoices — paid and balance amounts are derived dynamically from the payment ledger.
- Paid-at-session sessions remain excluded from invoicing.
- Credits, multi-invoice payments, reconciliation, and month-close workflows remain unfinished.
- Account statements, delivery, credits, and reconciliation remain unimplemented. Manual one-payment receipts are implemented separately with immutable receipt snapshots.

## Prior Balance & Account Summary Schema

Finalized invoices store an immutable historical snapshot of the payer's prior unpaid balance and payments applied in the `account_summary_snapshot` column of the `invoices` table.

### `account_summary_snapshot` JSON Structure (Version 1)
```json
{
  "version": 1,
  "current_invoice_total_cents": 15000,
  "current_invoice_paid_cents": 0,
  "current_invoice_balance_cents": 15000,
  "prior_unpaid_balance_cents": 30000,
  "total_amount_due_cents": 45000,
  "prior_invoices": [
    {
      "invoice_id": "8a06e93e-2b5d-4f10-b98a-9f5b2f8a12bc",
      "invoice_number": "2026-0001",
      "invoice_date": "2026-05-15",
      "remaining_balance_cents": 30000
    }
  ]
}
```

- **version** (integer): The schema version of the snapshot. Only version `1` is supported. Unknown or malformed snapshots are treated as unavailable.
- **current_invoice_total_cents** (integer): Sum of all lines on the current invoice.
- **current_invoice_paid_cents** (integer): Payments currently allocated to this invoice's line items.
- **current_invoice_balance_cents** (integer): `max(current_invoice_total_cents - current_invoice_paid_cents, 0)`. Forced to `0` for void invoices.
- **prior_unpaid_balance_cents** (integer): Sum of the remaining unpaid balances of prior finalized, non-void invoices for the same payer responsibility.
- **total_amount_due_cents** (integer): `current_invoice_balance_cents + prior_unpaid_balance_cents`.
- **prior_invoices** (list): Detail list of each prior invoice included in the balance calculation.
