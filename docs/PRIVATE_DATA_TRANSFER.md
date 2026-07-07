# Private Data Transfer

GitHub transfers sanitized code only. The operational SQLite database, `.env`,
private branding, raw imports, reports, backups, generated invoices, generated
receipts, credentials, and logs must transfer separately through a verified
encrypted or direct secure method.

This document covers private operational data only. For the production
application install, use the versioned DMG and native `Install Jordana
Billing.app` described in `docs/HANDOFF_TO_JORDANA_MAC.md`,
`docs/FRESH_INSTALL.md`, and `docs/PRODUCTION_PACKAGING.md`.

## What Must Transfer

A production handoff or replacement Mac must preserve the operational SQLite
database. Google Sheets is the raw cloud staging and audit layer, but it cannot
reconstruct reviewed SQLite state such as:

- approved sessions and actual charged rates
- confirmed people, aliases, billing parties, and billing relationships
- invoice drafts, finalized invoices, voids, PDFs, snapshots, and audit history
- payments, allocations, corrections, receipts, and receipt PDFs
- local settings, filing-owner choices, prior-balance snapshots, and duplicate-repair state

A blank database is only for an intentionally empty development, demo, or spare
clean-Mac acceptance installation. It is not a production migration strategy.

## Package Shape

Recommended local package:

```text
Jordana_Private_Transfer/
  config/.env
  data/jordana_invoice.sqlite3
  backups/
  Session Lists/
  Client Files/
  credentials/
  imports/
  TRANSFER_MANIFEST.txt
```

Not every installation will have every optional directory, but an existing
production handoff must include `data/jordana_invoice.sqlite3` and the private
configuration needed by the installed app.

Private branding belongs outside Git, commonly under
`data/private/branding/` in a development checkout or inside the private
Application Support tree for production. Business profile JSON may remain under
ignored `data/private/` and be applied with `set-business-profile`.

## Safe Transfer Methods

Use one of:

- direct AirDrop between trusted Macs
- an encrypted external drive
- an encrypted archive transferred through an approved secure channel

Do not use GitHub, ordinary email, chat, screenshots, issue comments, release
assets, or unencrypted cloud folders for live private data.

## Before Packaging

1. Stop the local app.
2. Create a SQLite backup from the operational database, preferably through the
   app's manual backup control or `scripts/backup_db.sh`.
3. Verify the backup with `PRAGMA integrity_check` and retain the generated
   manifest showing `integrity_status: ok`.
4. Record the database path and current migration IDs through the migration head.
5. Record row counts for operational tables such as `people`, `sessions`,
   `billing_parties`, `client_accounts`, `invoices`, `invoice_line_items`,
   `payments`, `payment_allocations`, `payment_receipts`, `audit_log`,
   `raw_calendar_snapshots`, and `schema_migrations`.
6. Calculate SHA-256 checksums for every transferred file.
7. Fill out `scripts/TRANSFER_MANIFEST_TEMPLATE.txt`.
8. Encrypt or directly secure the package before moving it.

Real diagnosis codes are private operational data. They may exist only in the
authorized local database or insurance-related invoice output and must never be
copied into documentation, fixtures, screenshots, logs, examples, or Git.

## On Jordana's Mac

1. Install the versioned release DMG using `Install Jordana Billing.app`.
2. Transfer the private package separately from GitHub.
3. Place files in the installed private data tree, normally:

```text
~/Library/Application Support/Jordana Billing/
  config/.env
  data/jordana_invoice.sqlite3
  backups/
  logs/
  runtime/
~/Documents/Jordana Billing/
  Session Lists/
  Client Files/
```

4. Verify SHA-256 checksums against `TRANSFER_MANIFEST.txt`.
5. Verify `PRAGMA integrity_check` on the transferred database.
6. Confirm expected migration IDs and row counts match the source manifest.
7. Run the release verification script from the release payload.
8. Launch the app and confirm existing approved sessions, invoices, payments,
   receipts, relationships, and audit history are visible.
9. Create a fresh private backup on the new Mac and confirm the backup manifest
   reports `integrity_status: ok`.

Do not clone the repository or run `scripts/setup_jordana_mac.sh` as the
production handoff path. That retired script is a non-destructive stub, and a
source checkout is not required for Jordana's normal daily app use.

After transfer, review `docs/SCHEMA_AUDIT.md` before attempting any legacy table
cleanup or migration work.
