# Schema Audit

This audit reflects the current SQLite schema and migration registry on `main`. The executable source of truth is `SCHEMA`, `MIGRATIONS`, and the migration functions in `app/jordana_invoice/db.py`.

The schema is additive and local-first. The operational database is authoritative for reviewed people, relationships, sessions, approvals, invoices, payments, receipts, and audit history. Google Sheets remains source evidence for calendar capture; it is not a complete replacement for SQLite.

## Current Migration Head

The current migration head is:

```text
017_relationship_filing_owner_target
```

The registered migrations are:

1. `001_base` — base schema, compatibility columns, and service-catalog seed
2. `002_monthly_invoice_identity` — monthly billing identity and supplement sequencing
3. `003_payment_ledger_foundation` — payment and allocation ledger foundation
4. `004_payment_provenance` — payment source and source-session provenance
5. `005_invoice_line_corrections_audit` — immutable invoice-line correction history
6. `006_invoice_zelle_and_delivery` — Zelle and delivery snapshot support
7. `007_payment_corrections` — payment voiding, allocation reversal reasons, and idempotency keys
8. `008_billing_relationship_keys` — normalized active payer and covered-client relationship keys
9. `009_invoice_filing_owner` — filing-owner defaults and invoice snapshots
10. `010_invoice_prior_balance_snapshots` — frozen account-summary and prior-balance data
11. `011_payment_receipts` — receipt numbering and immutable receipt snapshots
12. `012_insurance_coding` — optional business and invoice insurance-code snapshots
13. `013_sync_state_hardening` — durable sync outcome and cursor metadata
14. `014_candidate_identity_aliases` — exact candidate identity aliases and duplicate-reconciliation records
15. `015_duplicate_repair_reversal_state` — reversible duplicate-repair state snapshots
16. `016_late_cancellation_billing` — late-cancellation billing snapshots and scheduled-rate preservation
17. `017_relationship_filing_owner_target` — relationship filing-owner kind/record targets for people or billing organizations

Do not describe `001_base` as the current migration. It is the first migration in the active sequence.

## Migration Safety

`migrate_database(db_path)` is the single migration entry point.

For an existing database with pending migrations it:

1. acquires the database lock
2. creates a timestamped SQLite backup
3. verifies the backup with `PRAGMA integrity_check`
4. applies pending migrations transactionally
5. records each migration ID in `schema_migrations`
6. restores the pre-migration database and raises `MigrationError` if migration fails

When the database is already current, migration performs no backup and no schema writes.

Migrations must remain:

- additive where possible
- reversible or recoverable from a verified backup
- idempotent
- backward compatible
- covered by migration-safety tests
- free of silent approved-value rewrites

The operational database must never be deleted, reset, or recreated as a migration strategy.

## Authoritative Operational Tables

### Raw evidence and sync

- `raw_calendar_snapshots` — append-only captured calendar evidence
- `sync_state` — durable full or incremental sync cursor and last-run outcome
- `calendar_preferences` — local calendar disposition and filtering preferences
- `app_metadata` — database-level application metadata such as demo mode

Raw snapshot rows are never edited or deleted by normal review workflows.

### Candidate identity and review

- `calendar_event_candidates` — current interpretation of source calendar evidence
- `candidate_identity_aliases` — exact event-ID, fingerprint, and conservative structural identity aliases
- `candidate_duplicate_reconciliations` — audited duplicate-repair plans, apply state, and reversal state
- `review_queue` and `review_items` — review status and decision tracking
- `calendar_aliases` — approved shorthand, person associations, and classification aliases

Candidate identity resolution is conservative. Duplicate repair may not modify protected approved, invoiced, paid, audited, or raw-evidence records.

### People and participation

- `people` — permanent human records; internal UUID is authoritative
- `session_participants` — people connected to one session as participants
- `sessions` — reviewed and approved billing records

`person_code` is a secondary identifier created only after confirmed first and last names. It does not silently change.

Multiple participants normally represent one session and one charge.

Approved sessions preserve the charged rate and approved billing values so later defaults do not rewrite history.

### Billing relationships

- `billing_parties` — person or organization receiving and paying an invoice
- `client_accounts` and `account_members` — backend shared-billing and default structures
- `billing_relationship_keys` — normalized active payer and covered-client identity keys

Accounts are backend structures and are not required routine review terminology. Bill To remains a session-level decision.

The payer is not automatically a covered client. Relationship changes never silently rewrite approved sessions.

### Rates and services

- `service_catalog` — active billing service labels and usage metadata
- `rate_rules` — effective-dated default and exception rules
- `rate_rule_participants` — exact order-independent participant sets for joint-session rules

Rate priority is:

1. session-specific approved override
2. exact participant-combination exception
3. person exception
4. billing-relationship exception
5. global or default rule

Approved session rates remain frozen.

### Invoices

- `business_profile` — current local invoice identity and optional insurance fields
- `invoice_sequences` — annual invoice numbering state
- `invoices` — draft, finalized, and void lifecycle plus frozen snapshots
- `invoice_line_items` — source-session links and frozen line values
- `invoice_line_item_corrections` — audited draft-line corrections

The invoice schema supports:

- monthly draft identity by Bill To and billing month
- supplement sequencing
- filing-owner snapshots
- prior-balance and account-summary snapshots
- optional insurance-code snapshots
- immutable finalized PDFs and invoice values
- void and reissue rather than editing a finalized invoice

Structured diagnosis-code storage is limited to optional invoice-specific
insurance billing or reimbursement. Diagnosis codes must be intentionally
entered or approved by Jordana, never inferred from calendar text, participant
names, session descriptions, or other application data. Finalized diagnosis-code
snapshots remain frozen; corrections use the existing correction, void, or
reissue process. Real diagnosis codes must never be committed to GitHub,
fixtures, screenshots, logs, demo data, examples, or documentation.

### Payments and receipts

- `payments` — posted and void payment records with provenance
- `payment_allocations` — money applied to invoices or charges
- `payment_receipts` — immutable finalized receipt snapshots and PDF paths
- `receipt_sequences` — annual receipt numbering state
- `idempotency_keys` — protection against repeated financial actions

New paid-at-session approvals create or validate one posted payment and allocation transactionally and idempotently. They bypass monthly invoice staging.

The legacy paid-at-session backfill analyzer remains dry-run only. No migration itself creates historical payments or allocations.

### Audit

- `audit_log` — append-only audit events for review, identity, billing, rate, invoice, payment, and correction actions

Audit history is preserved. Sanitized application errors must not expose SQL, filesystem paths, stack traces, credentials, or private client data.

## Legacy Compatibility Tables

The following remain for backward compatibility:

- `clients`
- `client_aliases`
- `client_rates`

Current routine workflows use:

- `people` instead of `clients`
- `calendar_aliases` instead of `client_aliases`
- `rate_rules` instead of `client_rates`

Legacy tables must not be deleted until a separately approved, backed-up, reversible migration proves that no live read or write path depends on them.

## Concurrency And Locking

Managed connections enable:

- `PRAGMA foreign_keys = ON`
- WAL mode where supported
- a five-second SQLite busy timeout
- `sqlite3.Row` row access

The database lock prevents overlapping migrations and sync operations. Financial finalization and other sensitive writes use explicit transactions. Lock contention raises a controlled error; it never triggers database deletion or reset.

## Required Checks Before A Schema Change

Before changing the schema:

1. inspect the current repository, migration registry, and tests
2. verify local Git state and compare it with `origin/main`
3. create and verify a private backup of the operational database
4. choose the smallest additive migration
5. preserve approved sessions, rates, Bill To values, invoices, payments, receipts, and audit history
6. add focused migration and regression tests
7. run the full test suite
8. run privacy and Git-safety checks
9. inspect the final diff and confirm no private files are tracked or staged
10. document the new migration and secure rollback or recovery path

Never test a migration by deleting or recreating `data/jordana_invoice.sqlite3`.
