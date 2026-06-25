# Schema Audit

This audit reflects the local SQLite schema after the simplified review and rate-memory round.

## Current Authoritative Tables

- `people`: permanent human client records. Internal `person_id` UUID is authoritative. `person_code` is a secondary human-readable code generated only after first and last name are confirmed.
- `calendar_aliases`: reviewed calendar shorthand and classification aliases. Used for smart prefill and personal/admin exclusions.
- `sessions`: permanent normalized session records. Approved sessions preserve `approved_rate_cents`, `rate_cents_snapshot`, `approved_rate_source`, and `approved_rate_rule_id` so later rate changes do not rewrite history.
- `session_participants`: people who attended one session. Multiple participants still represent one session and one charge.
- `billing_parties`: bill-to contacts for invoice recipient/payer. A billing party may be a person or organization and does not have to be a participant.
- `rate_rules`: effective-dated suggested-rate rules for defaults, person exceptions, account rules, service/time/duration rules, and joint-session exceptions.
- `rate_rule_participants`: normalized exact participant set for joint-session rate exceptions. Matching is order-independent and exact.
- `client_accounts` and `account_members`: backend relationship support for couples, families, shared billing, default payer, special rates, and future invoice grouping. These are not required in routine session review.
- `raw_calendar_snapshots`: immutable calendar evidence.
- `calendar_event_candidates`, `review_queue`, and `review_items`: parser output and review-state tracking.
- `audit_log`: append-only audit events for review, identity, billing, and rate-memory changes.

## Legacy Or Compatibility Tables

- `clients`: legacy canonical client table from Phase 1. Current review and CRM flows do not treat it as authoritative.
- `client_aliases`: legacy alias table tied to `clients`. Current alias learning uses `calendar_aliases`.
- `client_rates`: legacy client-rate table tied to `clients`. Current suggested rates use `rate_rules`; approved session history uses `sessions`.

These legacy tables remain intact for compatibility. They should not be deleted until there is a proven migration, backup, and test coverage showing no read or write paths remain.

## Focused Table Findings

### `people`

- Exists: yes.
- Sanitized fixture row count: 0 after sample import.
- Key columns: `person_id`, `display_name`, `first_name`, `last_name`, `preferred_name`, `person_code`, billing contact fields, administrative notes, merge fields, active status.
- Indexes: primary key, unique `person_code`, `idx_people_display_name`.
- Foreign-key references from: `account_members`, `billing_parties`, `calendar_aliases`, `rate_rules`, `session_participants`, `rate_rule_participants`.
- Read paths: People view, participant search, person record, rate matching, smart alias prefill, schema audit tests.
- Write paths: create/update person APIs, participant creation, merge workflow.
- Status: authoritative.

### `clients`

- Exists: yes.
- Sanitized fixture row count: 0.
- Key columns: `id`, `display_name`, `client_code`, `status`.
- Indexes: primary key, unique `client_code`.
- Foreign-key references from: `client_aliases`, `client_rates`, `sessions.client_id`.
- Read/write paths: compatibility only; current review workflow does not depend on it.
- Status: legacy compatibility.

### `calendar_aliases`

- Exists: yes.
- Sanitized fixture row count: 0 after sample import.
- Key columns: `alias_id`, `raw_alias`, `normalized_alias`, `account_id`, `person_id`, `classification`, `service_mode`, confidence and approval fields.
- Indexes: primary key, unique `normalized_alias`, `idx_calendar_aliases_normalized_alias`.
- Read paths: smart prefill, person/account records, similar-person search.
- Write paths: approval alias learning, personal/admin marking, person-name correction, merge.
- Status: authoritative alias table.

### `client_aliases`

- Exists: yes.
- Sanitized fixture row count: 0.
- Key columns: `id`, `client_id`, `alias`, `alias_type`, `classification_hint`, `review_status`, `notes`.
- Indexes: primary key, unique `alias`.
- Read/write paths: compatibility only.
- Status: legacy compatibility.

### `rate_rules`

- Exists: yes.
- Sanitized fixture row count: 0 after sample import.
- Key columns: `rate_rule_id`, `client_account_id`, `person_id`, `billing_session_type`, `custom_service_description`, `custom_service_code`, duration, service, rate group, time category, `appointment_status`, amount, effective dates, priority, active.
- Indexes: primary key, `idx_rate_rules_match`, and `idx_rate_rules_custom_match`.
- Foreign-key references from: `sessions.rate_rule_id`, `sessions.approved_rate_rule_id`, `rate_rule_participants`.
- Read paths: rate suggestion engine, Rate Card, person/account CRM records.
- Write paths: Rate Card, CLI seeding, manual rate-scope memory.
- Status: authoritative suggested/future rate table.

### `client_rates`

- Exists: yes.
- Sanitized fixture row count: 0.
- Key columns: `id`, `client_id`, `rate_cents`, currency, effective dates.
- Indexes: primary key.
- Read/write paths: compatibility only.
- Status: legacy compatibility.

## Known Overlaps

- `people` vs `clients`: `people` is the current permanent human model. `clients` is legacy and should not receive new routine review writes.
- `calendar_aliases` vs `client_aliases`: `calendar_aliases` is current and can point to people, accounts, or non-client classifications. `client_aliases` is legacy.
- `rate_rules` vs `client_rates`: `rate_rules` is current and supports person, account, default, service/time/duration, and joint exceptions. `client_rates` is legacy.
- `client_accounts` vs bill-to: accounts are backend relationship groups; `billing_parties` is the current bill-to authority for a session.

## Recommended Future Migration

1. Keep all legacy tables intact through invoice development.
2. Add read-path telemetry or tests proving no current code uses `clients`, `client_aliases`, or `client_rates` for review, rate, or invoice decisions.
3. Create a reversible migration that copies any legacy rows into `people`, `calendar_aliases`, and `rate_rules` where appropriate.
4. Back up the live database and run `PRAGMA integrity_check`.
5. Run the full tests plus privacy and git safety checks.
6. Only then consider retiring legacy tables.

## Removal Risks

- Historical live data may still exist in legacy tables on a transferred Mac.
- Future invoice work may need to read `sessions.client_id` for compatibility with early imports.
- Removing alias/rate tables without migration could lose reviewed shorthand or rate evidence.

## Prerequisites Before Removal

- Confirm live row counts on Jordana's Mac after private data transfer.
- Export a backup.
- Prove no application read/write path depends on the legacy table.
- Provide downgrade or restore instructions.
- Update documentation and tests in the same change.
## Calendar Classification Additions

This round added only additive SQLite structures. No legacy client/rate tables were deleted or destructively migrated.

New authoritative support tables:

- `calendar_preferences`: optional calendar disposition rules. Authoritative for local review filtering only.
- `app_metadata`: database-level metadata such as explicit demo mode.

New candidate/session fields:

- `appointment_status`: scheduled/completed/cancelled/no_show/unresolved.
- `billing_treatment`: human decision for cancelled/no-show billing; separate from `payment_status`.
- `title_time_text`, `title_time_normalized`, `title_time_matches_calendar`: title-time validation evidence.
- `calendar_disposition`, `calendar_is_preferred_work`, `hidden_from_review`: source-calendar review/filter metadata.

Authoritative direction remains:

- `raw_calendar_snapshots` preserves every captured version and original `calendar_name`.
- `calendar_event_candidates` stores the current interpretation for an event identity.
- `sessions` stores reviewable and approved session facts, including historical charged amounts.

Removal prerequisites are unchanged: no destructive cleanup of legacy `clients`, `client_aliases`, or `client_rates` should happen until live data absence, compatibility removal, backup, reversible migration, tests, and documentation prove it safe.

## Invoice Additions

- `business_profile`: one active local invoice identity.
- `service_catalog`: normalized current service labels and usage metadata.
- `invoice_sequences`: per-year numbering state.
- `invoices`: draft/finalized/void lifecycle and frozen bill-to/business snapshots. Includes `billing_month` (nullable `YYYY-MM` for monthly staging) and `supplement_sequence` (non-negative integer; 0 = original, 1+ = supplemental).
- `invoice_line_items`: source-session links plus frozen line display values.
- `billing_parties.preferred_delivery_method`: current delivery default.
- `sessions.service_catalog_id`: additive catalog link; `service_mode` remains the historical text value.

No legacy client/rate table was removed or repurposed.

## Explicit Migration System

Database schema migrations and seed data now run only during explicit startup/init/migrate flows, never during normal API/web requests.

### How it works

- `migrate_database(db_path)` in `db.py` is the single entry point for schema migrations.
- It checks the `schema_migrations` table for applied migration IDs.
- If all migrations are already applied, it does nothing (no backup, no writes).
- For existing databases needing migration, it creates a timestamped backup, verifies the backup with `PRAGMA integrity_check`, runs migrations transactionally, and records each migration ID.
- If a migration fails, it rolls back the transaction, restores the original database from backup, raises `MigrationError`, and the app refuses to start.
- `init_db(conn)` remains for backward compatibility but is a no-op when the schema is already current. It is no longer called from request-path code.
- The review server calls `migrate_database()` at startup in `serve()`. If migration fails, the server exits with status 1.
- CLI commands call `migrate_database()` before connecting when database access is needed.
- Sync operations (`sync_now`, `get_unresolved_count`) call `migrate_database()` before connecting.

### Migration list

Migrations are defined in `MIGRATIONS` in `db.py`. Each entry is a `(migration_id, function)` tuple. The current migration is `001_base`, which runs `migrate_existing_db`, `executescript(SCHEMA)`, `migrate_phase2_columns`, and `seed_service_catalog`.

Migration `002_monthly_invoice_identity` adds `billing_month TEXT` and `supplement_sequence INTEGER NOT NULL DEFAULT 0 CHECK (>= 0)` to `invoices`, backfills `billing_month` only for existing invoices whose billing period is exactly one complete calendar month, detects duplicate draft invoices sharing the same `bill_to_party_id` + derived `billing_month` (aborting with `MigrationError` if found), and creates the partial unique index `idx_invoices_draft_party_month`.

Migration `003_payment_ledger_foundation` adds two new tables — `payments` and `payment_allocations` — by executing the full SCHEMA script (all statements use `IF NOT EXISTS`, making it idempotent). No existing tables or columns are modified. See `docs/DATA_MODEL.md` for table details.

### Adding a new migration

1. Append a new `(migration_id, function)` tuple to `MIGRATIONS`.
2. The function should be additive, idempotent, and backward compatible.
3. Add tests in `tests/test_migration_safety.py`.

## SQLite Concurrency and Locking

All managed connections use centralized configuration in `db.py::connect()`:

- `PRAGMA foreign_keys = ON` — enforced on every connection.
- `PRAGMA journal_mode = WAL` — Write-Ahead Logging for concurrent readers during writes. Falls back gracefully if unsupported.
- `PRAGMA busy_timeout = 5000` — 5-second wait before reporting a lock error.
- `sqlite3.Row` row factory — consistent dict-like row access.
- `timeout=5.0` on `sqlite3.connect()` — connection-level busy timeout.

### File-based sync lock

`DatabaseLock` in `db.py` uses `fcntl.flock` to prevent overlapping syncs and migrations across processes and threads:

- Lock file: `<database_path>.lock`
- Default timeout: 30 seconds (`DEFAULT_LOCK_TIMEOUT_SECONDS`)
- Stale lock recovery: the OS releases `flock` when the holding process exits; no manual cleanup needed.
- On timeout, raises `LockError` with a clear message.

### Sync collision protection

- `sync_with_connection()` acquires `DatabaseLock` before the write transaction and releases it after.
- `migrate_database()` acquires `DatabaseLock` before any schema work.
- A second sync or migration that cannot acquire the lock within the timeout raises `SyncError` (for sync) or `MigrationError` (for migration) with a clear message — no indefinite blocking.

### Invoice finalization under contention

- `finalize_invoice()` and `void_invoice()` use `BEGIN IMMEDIATE` for atomic transactions.
- If another connection holds a write lock, `BEGIN IMMEDIATE` fails and raises `DatabaseBusyError` with a clear operational message.
- The review server returns HTTP 503 for `DatabaseBusyError` and HTTP 400 for other errors.

### Lock contention error handling

- `sqlite3.OperationalError` with "locked" in the message is caught in `sync_with_connection()` and re-raised as `DatabaseBusyError`.
- `DatabaseBusyError` is never swallowed — it propagates to the caller with a human-readable message.
- No database deletion or reset occurs on lock errors.

### Tests

Focused tests in `tests/test_concurrency_locking.py` verify:

- All managed connections have approved PRAGMA settings (foreign_keys, WAL, busy_timeout, row_factory).
- Concurrent reads succeed during a writer in WAL mode.
- Overlapping sync fails cleanly within bounded timeout.
- Stale sync lock recovery works after lock release.
- Lock timeout produces a clear error message.
- Failed sync rolls back completely (no partial imports, cursor does not advance).
- Atomic invoice finalization raises `DatabaseBusyError` under SQLite lock contention.
- Migrations and sync cannot run concurrently (both directions).
- Repeated normal startup is safe — no locks left behind.
