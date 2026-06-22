# Data Model

The database is local SQLite. Internal UUIDs are primary keys. Human-readable person and account codes are secondary identifiers.

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

Effective-dated suggested-rate rules. Rules can be global, account-specific, person-specific, duration-specific, service-mode-specific, rate-group-specific, time-category-specific, or an exact participant-combination exception.

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
