# Current Implementation Status And Handoff

This document supersedes older uploaded handoffs and stale repository notes. Newer repository code, schema, migrations, tests, and explicit decisions remain authoritative.

## Verified Baseline

- **Application and release baseline reviewed:** `179da1fe14ac1fd56ed1e6b939b34fafe7299760`
- **Documentation state reviewed before this reconciliation:** `fd9031b5fb694ddc138a939f6b2c0c98b2c98b46`
- **Migration head:** `017_relationship_filing_owner_target`
- **Latest recorded full-suite baseline:** 2,729 tests passed, 0 failures, 68 skipped on Python 3.14.4
- **Current test release target:** `v0.1.0-test.10`
- **Current release artifact:** recorded in the GitHub release and `release_manifest.json`
- **Current package/application version:** `0.1.0.post10`
- **Release status:** approved for a controlled Jordana beta; not represented as final production software
- **Prior test release:** `v0.1.0-test.8` is superseded by test.10 for installation and update testing

## Architecture

Apple Calendar → iPhone Shortcut → Google Apps Script → Google Sheets raw staging/audit → Python sync/import → local SQLite → review UI → approved sessions → invoice preview/finalization → payment tracking.

Source responsibilities:

- Apple Calendar and Google Sheets preserve source evidence.
- SQLite is the operational application database.
- CSV reports, invoice PDFs, receipt PDFs, and other exports are derived outputs.
- Raw calendar evidence is never edited or deleted by the application.
- Approved rates, approved Bill To values, finalized invoices, and payment history are never silently recalculated.

## Current Product Status

The application is ready for a supervised real-world beta using Jordana's June invoices. The core workflow is implemented and has been exercised on a test Mac. The remaining work is primarily acceptance evidence, minor workflow polish, packaging maintenance, and later accounting enhancements.

This is not yet a final production declaration. Brooke should remain available during the first June billing cycle, maintain verified backups, and stop if any data-preservation or invoice-integrity issue appears.

## Implemented Scope

### Calendar And Review

- Authenticated Apps Script sync with initial-full and incremental cursor behavior
- Raw snapshot preservation and duplicate event-version collapse
- Conservative calendar shorthand parsing and source-calendar classification
- Review queue with independent saves for Participants, Bill To, and Session Draft
- Candidate-to-session promotion with idempotent repeated saves
- Approval single-submit protection
- Review overlay cleanup, stale-state removal, focus restoration, and success confirmation
- Invoice-staging warnings treated separately from successful approval
- Duplicate resolution using **Confirm Duplicate & Next**
- Billing relationship wizard with payer and covered-client separation
- Active duplicate prevention and deactivation/reactivation rather than destructive deletion
- Audited normalization and relationship persistence

### People, Billing Relationships, And Rates

- Permanent UUID primary keys
- Human-readable person and account codes as secondary identifiers
- Payer, covered clients, Participants, Bill To, filing owner, and delivery contact remain separate concepts
- Organization and person payers
- Full people-directory search for filing owners and invoice delivery contacts
- Inline creation with duplicate-person safeguards
- Effective-dated global, person, exact participant-combination, and billing-relationship rate rules
- Session-only and future-rule scopes
- Approved session rate snapshots remain frozen

### Invoices

- Monthly invoice staging by Bill To and billing month
- Draft invoice editing with optimistic revision locking
- Canonical PDF preview and final PDF produced by one shared renderer
- Two-step finalization with transaction-safe numbering
- Repeated finalize requests return the existing immutable invoice rather than renumbering or regenerating it
- Finalized invoice snapshots and PDFs remain immutable
- Filing-owner selection and client/month folder organization
- Filing owner supports organization, payer, covered client, or another explicitly selected active person
- Draft filing-owner overrides do not rewrite relationship defaults
- Prior unpaid balances and frozen account-summary snapshots
- Optional invoice-specific insurance coding entered deliberately by Jordana
- Void and reissue workflow
- Searchable invoice library
- Waived late-cancellation lines can correctly persist as $0.00 without permitting arbitrary zero-dollar invoice lines

### Payments

- Payment ledger and allocations
- Paid-at-session approval workflow with idempotent payment creation/allocation
- Available-funds application
- Allocation reversal and payment voiding
- Correction history
- Manual immutable receipt generation
- Outstanding, Paid, and All Payments views
- Shared invoice/payment financial-summary calculations
- Read-only historical paid-at-session analyzer and CLI

### Packaging And Installation

- Native no-Terminal setup app
- Versioned DMG and matching checksum
- Embedded offline wheelhouse and release manifest
- Private runtime inside the installed application
- Private config and SQLite data under Application Support
- User-facing reports, invoices, and receipts under Documents
- Daily launch does not use Git, pip, PyPI, editable installs, or dependency repair
- Missing production database is treated as an error rather than silently creating a blank replacement
- Port ownership and database-integrity checks
- Existing app bundle preserved as `.previous` until a replacement verifies
- Automatic rollback of the app bundle when final installation verification fails
- Private Application Support data remains outside app-bundle replacement and rollback

## Release Target

The current controlled-beta release target is:

```text
JordanaBilling-v0.1.0-test.10-<commit>-macos-arm64.dmg
```

Release facts are recorded in the GitHub release, `.sha256` asset, and artifact
`release_manifest.json` after publication.

- Release label: `v0.1.0-test.10`
- Python package/application version: `0.1.0.post10`
- Build ID: embedded in the wheel and exposed by `/api/build-info`
- Source tree dirty: false
- Builder Python: 3.14.4
- Required Python family: 3.14.x
- Architecture: arm64
- DMG checksum verification: required before publication
- `hdiutil verify`: required before publication
- Private-file scan: no `.env`, SQLite, or PDF files found in release payload
- `contains_private_data`: false
- Wheelhouse includes exact `jordana_invoice-0.1.0.post10` app wheel and explicit `Pillow` runtime support required by ReportLab PDF rendering
- Local browser smoke testing passed for June Reconciliation and in-app Quit on a sanitized temporary database
- Focused tests pass for Quit, installer/update behavior, build identity, report filtering, and June reconciliation

### Bug Fixes In test.10

1. **Composite cursor ordering fix** — sync cursor comparison now correctly handles rows with equal `ingested_at` values by using `snapshot_key` as a tiebreaker.
2. **Flaky test fix** — `test_07_health_endpoint` now includes a kill fallback on timeout.

### Bug Fixes Inherited from test.9

1. **In-app Quit** — visible sidebar Quit safely stops sync work and the local server.
2. **Installer stale-runtime hardening** — installation reads and force-reinstalls the exact app wheel from the manifest, verifies payload and installed file checksums, imports installed build info, launches the app, and confirms the running server build ID.
3. **Rollback-safe updates** — an already-running Jordana Billing server is coordinated before replacement, unrelated port owners are refused, and the prior app/runtime is restored if verification fails.
4. **June reconciliation workflow** — dry-run/apply is verified for missing sessions, extra sessions, possible duplicates, edited event versions, excluded/non-client billing issues, and approved-session warnings. Missing recovered rows appear in Review and Sessions while unresolved/excluded rows stay out of client reports and invoice staging.
5. **Report filtering** — client-facing session exports exclude unresolved and excluded rows; All Appointments remains the complete audit ledger.

### Prior Test Releases

`v0.1.0-test.8` was built from commit `d97d6babc2278bd1e19fbc36319d65acce24fbb4`. Its DMG payload was correct, but supervised installation showed that stale installed runtime code could remain when multiple releases shared package version `0.1.0`. test.10 supersedes test.8 for installation and update testing.

`v0.1.0-test.7` was built from commit `179da1fe14ac1fd56ed1e6b939b34fafe7299760` but was never published. It is superseded by test.10 as the current controlled-beta release target.

The prior installed-smoke baseline remains `v0.1.0-test.6` from commit `0dec58b6bf5ab35e2d48600b57fec83a477e304d`, which preserved existing private configuration and SQLite data during the brooketest upgrade installation and passed the major Billing Relationship, filing-owner, delivery-contact, invoice, and data-preservation workflows.

An earlier test.6 artifact built from commit `6c3dbab` using Python 3.11 was rejected and was not published or distributed. It must not be used.

## Controlled Beta Decision

The test.10 artifact may be installed on Jordana's Mac for a controlled June-invoice beta when all of the following are true:

1. Brooke has a verified backup of the source operational database.
2. The private `.env` and SQLite database are transferred separately through a direct or encrypted method.
3. Transfer checksums and `PRAGMA integrity_check` pass.
4. The Mac has the Python 3.14 family required by the release manifest.
5. Brooke is present for installation and the first operational smoke path.
6. Jordana reviews every session and invoice before approval or finalization.
7. The prior Mac and source backup remain intact until the new installation has completed the June billing cycle successfully.

The beta is not an authorization to delete the original operational database, source backup, or prior working installation.

## Beta Smoke Path

After installation:

1. Launch the installed app by double-clicking.
2. Confirm the existing database and private configuration were preserved.
3. Run Calendar Sync and confirm it does not duplicate snapshots or sessions.
4. Review one June session and save Participants, Bill To, and Session Draft.
5. Approve the session and confirm the overlay closes and the item cannot be submitted again.
6. Open the resulting draft invoice.
7. Review the canonical draft PDF.
8. Confirm filing owner, delivery method, line items, rates, and total.
9. Finalize one disposable or carefully verified invoice.
10. Open the stored canonical PDF from the expected client/month folder.
11. Record or apply a payment through the supported workflow.
12. Restart the Mac and confirm the same records remain visible.

## Known Beta Friction

### Unresolved Client Refresh

An unknown client may initially display safe fallback values such as Standard 60. After Jordana confirms and saves the client identity, she may need to refresh or reopen the session before the final duration, time category, and related defaults appear.

Do not approve the session until the refreshed values are visible and correct. Automatic reparse/refresh after client confirmation remains a future UX improvement.

### Delivery Is Not Automated

The application prepares and files invoices but does not send or track delivery by email or mail. Jordana must distribute finalized invoices outside the application.

## Remaining Narrow Follow-Up

### Before Final Production Declaration

1. Complete and record the full clean-Mac acceptance evidence: restart, duplicate launch, cross-user port ownership, unrelated port conflict, missing config, missing database, reinstall preservation, uninstall preservation, and rollback behavior.
2. Add the specific regression test proving a failed finalization rolls back a delivery-method auto-sync performed inside the same transaction.
3. Confirm the repository's sanitized GitHub Actions workflow is enabled and recording successful status checks on current pushes.
4. Implement automatic candidate reparse/refresh after client confirmation.
5. Reconcile any future documentation immediately after application or release changes.

### Later Enhancements, Not June-Beta Blockers

- Invoice email/mail sending and delivery tracking
- Credits, refunds, and write-offs
- Automated multi-invoice payment allocation
- Formal reconciliation and month-close workflow
- Historical paid-at-session apply mode
- Polished management dashboard
- Notarized installer
- Bundled Python runtime
- Formal client-versus-non-client schema distinction
- Automatic payer classification

## Privacy And Data Safety

Never commit or publish:

- live SQLite databases
- private `.env` files or credentials
- raw calendar exports or private spreadsheet exports
- invoices, receipts, reports, or payment records
- logs containing real names
- screenshots containing private information
- backups
- real diagnosis codes
- private branding or transfer packages

A production or beta handoff must preserve the operational SQLite database. Google Sheets can reconstruct raw calendar evidence, but it cannot reconstruct reviewed people, billing relationships, approved rates, invoices, payments, receipts, filing choices, or audit history.

## Verification Commands

From a clean development checkout:

```bash
PYTHONPATH=app .venv/bin/python -m unittest discover -s tests
scripts/run_acceptance_test.sh
scripts/git_safety_check.sh
scripts/privacy_check.sh
```

Acceptance imports must use the supplied temporary-database workflow and must never target the operational database casually.

## Start Of Future Development Round

Before changing code:

```bash
pwd
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
git log -1 --oneline
```

Then read `AGENTS.md`, this document, `docs/HANDOFF_TO_JORDANA_MAC.md`, `docs/PRIVATE_DATA_TRANSFER.md`, `docs/FRESH_INSTALL.md`, `docs/PRODUCTION_PACKAGING.md`, and `docs/TEST_MAC_ACCEPTANCE.md`.

Do not restart the architecture. Continue from the current implementation and choose the smallest safe change.
