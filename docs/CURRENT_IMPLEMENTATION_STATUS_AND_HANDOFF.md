# Current Implementation Status And Handoff

This document supersedes older uploaded handoffs and stale repository notes. Newer repository code, schema, migrations, tests, and explicit decisions remain authoritative.

## Verified Baseline

- **Application and release baseline reviewed:** `179da1fe14ac1fd56ed1e6b939b34fafe7299760`
- **Documentation state reviewed before this reconciliation:** `fd9031b5fb694ddc138a939f6b2c0c98b2c98b46`
- **Migration head:** `018_delivery_contact_person`
- **Latest recorded full-suite baseline:** 2,795 tests passed, 0 failures, 68 skipped on Python 3.14.4
- **Current test release target:** `v0.1.0-test.20`
- **Current release artifact:** recorded in the GitHub release and `release_manifest.json`
- **Current package/application version:** `0.1.0.post20`
- **Release status:** approved for a controlled Jordana beta; not represented as final production software
- **Prior test release:** `v0.1.0-test.19` is superseded by test.20 for installation and update testing

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
- Canonical invoice render model drives the in-app HTML preview, exact draft/finalization PDF previews, and final PDF
- Two-step finalization with transaction-safe numbering
- Repeated finalize requests return the existing immutable invoice rather than renumbering or regenerating it
- Finalized invoice snapshots and PDFs remain immutable
- Filing-owner selection and client/month folder organization
- Filing owner supports organization, payer, covered client, or another explicitly selected active person
- Draft filing-owner overrides do not rewrite relationship defaults
- Draft Bill To, File invoice under, delivery scope, and line/session correction controls are inline in the invoice workspace
- Draft packet PDF printing is available for selected draft invoices and is side-effect free
- Finalization readiness errors can route to missing billing email/address fixes and return to the invoice
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
- Outstanding, Paid, and All Payments views with shared Invoice Period filtering and first-name sorting
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

The current controlled-beta release target is (test.20 supersedes test.19):

```text
JordanaBilling-v0.1.0-test.20-<commit>-macos-arm64.dmg
```

Release facts are recorded in the GitHub release, `.sha256` asset, and artifact
`release_manifest.json` after publication.

- Release label: `v0.1.0-test.20`
- Python package/application version: `0.1.0.post20`
- Build ID: embedded in the wheel and exposed by `/api/build-info`
- Source tree dirty: false
- Builder Python: 3.14.4
- Required Python family: 3.14.x
- Architecture: arm64
- DMG checksum verification: required before publication
- `hdiutil verify`: required before publication
- Private-file scan: no `.env`, SQLite, or PDF files found in release payload
- `contains_private_data`: false
- Wheelhouse includes exact `jordana_invoice-0.1.0.post20` app wheel and explicit `Pillow` runtime support required by ReportLab PDF rendering
- Local browser smoke testing: required before publication
- Focused tests pass for Quit, installer/update behavior, build identity, report filtering, June reconciliation, weekday column, weekend/evening rate matching, Edit Session, billing relationship deletion/archive, self-pay edit, SSL handling, and write-token messaging

### Bug Fixes In test.20

1. **Blank-boundary calendar reconciliation** — The importer derives definite inclusive coverage from a canonical capture label plus `captured_at` when the current Shortcut leaves `window_start` and `window_end` blank. Unknown labels remain non-covering.
2. **Upgrade/no-new-rows reconciliation** — A sync that imports no new raw rows still rechecks pending candidates against preserved evidence, so installing this release can safely suppress already-imported removed occurrences.

### Bug Fixes In test.19

1. **Sanitized Report Issue diagnostics** — Review can create a local support bundle with operational build/schema/request context while excluding client names, clinical content, raw calendar data, invoices, receipts, logs, and the live database.
2. **Calendar snapshot reconciliation** — Pending candidates are excluded when the newest complete successful raw snapshot batch that explicitly covers their appointment date omits them. Raw evidence and approved sessions remain unchanged.

### Bug Fixes In test.18

1. **Approved-session Edit Session recovery** — `Edit Session` from an unfinalized draft invoice can return an approved session to Review and remove its draft line. If an interrupted prior edit already left the session in Review while the draft line remained, the same action cleans up the stale draft line instead of erroring.
2. **Draft invoice snapshot refresh** — Reapproving an edited session refreshes existing draft invoice lines from the current approved session values, preventing stale amount, duration, participant, service, appointment-treatment, or description values from remaining on draft invoices.
3. **Fred Colin June 8 regression check** — the supplied Jordana backup was checked with a temporary copy to verify this path.

### Bug Fixes In test.17

1. **Brett Barakett / Peter Grossman billing cleanup** — Changing Bill To detaches stale archived Billing Relationship account links when the selected billing party no longer matches that relationship. Archived account links no longer block invoice staging when the session has an explicit valid Bill To.
2. **Erroneous Billing Relationship deletion** — Mistaken archived relationships can be deleted when they have no protected account-specific billing history. Stale unfinalized session links and relationship-specific aliases are cleaned up safely; finalized invoices, payments, receipts, and true protected history still force archive.
3. **Service-period invoice lists** — Invoice, client, organization, and payment list surfaces show the service period rather than invoice number/date. Invoice number/date remain stored internally and on the invoice document.
4. **Draft invoice Review workflow** — Draft invoice line editing routes linked sessions back to Review through `Edit Session`; the old limited line editor is no longer used.
5. **Draft invoice deletion** — True draft invoices can be deleted from the draft editor, removing draft line items without deleting underlying sessions.
6. **Customer-facing invoice cleanup** — Invoice PDF footers/page-number footer labels are removed, and recipient blocks no longer show `Via Email` or `Via Mail`.
7. **Billing Relationship ordering and display** — Billing Relationships sort by payer last name and first name, and inactive archived account members no longer leak into active payer rows.

### Bug Fixes In test.15

1. **Review self-pay switch** — The Review Bill To section includes a direct Self pay action for single-client sessions. It saves the client as Bill To and detaches the stale session-level Billing Relationship/account link, so an archived or deleted shared relationship no longer keeps shadowing the review item.
2. **Billing Relationship switcher** — Change payer or shared billing opens the relationship wizard from Review instead of trapping the user in the old relationship record.
3. **Structured person selection** — Billing Relationship payer, recipient, delivery-contact, and covered-client searches show explicit Select/Add/Remove buttons so typed matches are easier to choose reliably.
4. **Covered-client edit refresh** — Adding or removing a covered client refreshes the originating Review candidate before returning, preventing removed names from lingering in the Review tab.
5. **Last-name-first list labels** — Invoice, payment, client, and Billing Relationship list views show person names as Last, First while the Review queue remains date-driven.

### Bug Fixes In test.14

1. **Static asset cache-busting** — CSS and JS assets are now served with mtime-based version query strings and `no-store` Cache-Control headers, preventing stale cached assets after updates.
2. **Inactive payer record conflict fix** — The billing relationship directory no longer reports a false payer-record-conflict warning when an inactive billing party exists alongside an active one for the same person.
3. **SELECT change-event handling** — Dropdown selects in the Review inspector and Rate Card now listen for `change` events in addition to `input` events via a shared `bindInputAndChange` helper, ensuring selections like billing type, duration, payment method, and attendance outcome are detected reliably.
4. **Inline invoice workspace at laptop widths** — The invoice workspace renders inline within the invoices view at laptop widths instead of as a modal sheet, with smooth scroll-to-top on reveal and no backdrop overlay.
5. **Paid-at-session Receipt button** — The paid-at-session summary in the Review inspector now includes a Receipt button that opens the payment detail view for the associated payment.

### Bug Fixes In test.13

14. **Paid-at-session review persistence and approval** — Approved paid-at-session sessions reload with a payment-ledger summary showing the stored amount, date, method, allocation state, and optional reference/admin fields. Completed paid-at-session reviews approve normally even when the cancellation-billing field is hidden; the backend normalizes that completed-session treatment to billable. If the paid-at-session detail form has already been saved and collapsed, approval reuses the stored payment detail instead of sending blank fields. Approval continues to create or validate one provenance-linked payment/allocation idempotently and remains excluded from invoice staging.
15. **Chronological invoice line order** — Draft editor rows, in-app HTML previews, exact PDF previews, finalized PDFs, and canonical invoice serialization now order session lines by service date, source start time, and stable line UUID rather than import, approval, insertion, or row order.
16. **Model-backed HTML invoice preview restored** — Draft, finalization, and finalized/void invoice screens use a clean in-app HTML card built from the current canonical invoice render model. Exact PDF open/download/print actions remain available, and the stored PDF remains the official customer-facing artifact.
17. **Invoice and review layout polish** — The Review queue uses the required Status, Date, Day, Time, RAW CLIENT, Clients, Duration, Rate, Review order and shows the original raw calendar title in the RAW CLIENT column. The draft invoice editor separates Date and Participants. The invoice library exposes only Status and Service Period filters, dynamically lists current service periods, shows filtered Draft/Finalized counts and totals, and sorts by Bill To/client first name.
18. **Invoice header presentation** — Draft previews, finalization previews, and finalized PDFs show the invoice header as `INVOICE`, an unlabeled uppercase short invoice date, and an unlabeled invoice number or draft placeholder. Billing Period is not displayed in the invoice header.
19. **Payments period filtering** — The Payments screen now filters Outstanding, Paid, and All Payments by Invoice Period rather than invoice date. Outstanding and Paid invoice tables display Invoice Period, rows sort by Bill To/client first name, and paid-at-session posted payments appear in the Paid tab as session-payment rows without creating invoices or changing finalized invoice history.
20. **Reports browser smoke** — `/reports` and `/api/reports` were verified in a real browser during release prep after the local debug session; report metadata loads and cards render.
21. **Beta invoice polish** — Draft invoices can be batch-printed as a draft packet, edited inline for Bill To/File Under/delivery scope, and corrected back to linked approved sessions only with an explicit reason. Finalization repair actions return to the same invoice after missing billing contact details are saved.
22. **Verified backup workflow** — App-launch daily backup, manual backup, migration backup, operational sync/rebuild backup, and finalization/void backup paths now use the verified backup module with manifests, retention, optional private-config copy, and optional secondary copy.

### Bug Fixes In test.12

1. **Duplicate Billing Relationships display suppression** — The Billing Relationships directory no longer shows an implicit/session-derived self-pay row alongside an existing canonical active Billing Relationship for the same person. One visible active row is shown per actual Billing Relationship. Canonical account wins: when a canonical active account exists, the shadowed implicit row is suppressed while the Edit action and canonical `account_id` are preserved. No data merge, cleanup, or account creation is performed. The defect was display-only; no real duplicate active `client_accounts` rows existed.

### Bug Fixes In test.11

1. **Weekday column in Review queue** — Review queue now shows a short weekday abbreviation (Mon–Sun) as a visual reminder for weekend sessions.
2. **Weekend/evening custom-rate matching** — Manually selecting a weekend or evening billing session type now correctly propagates the `time_category` to rate suggestion, matching person exceptions and participant-combination exceptions at the correct tier.
3. **Custom rate creation** — New custom rates save immediately to SQLite, appear in the rate list, persist after refresh, and participate in Review rate matching.
4. **Edit Session (no reason required)** — Eligible approved sessions show an Edit Session button. Clicking it moves the session back to Review with no prompt. Preserved: participants, Bill To, Billing Relationship, duration, session type, time category, approved rate, payment status. Draft invoice line is removed and the draft total recalculated atomically. A system audit entry is written. Blocked cases return a sanitized reason without partial changes.
5. **Billing Relationship delete/archive** — Unused relationships without billing history may be permanently deleted. Relationships with protected billing history are archived; historical invoices and approved records retain their references.
6. **Self-pay Edit** — Self-pay rows now show Edit. Clicking it opens the canonical account editor, initializing the relationship on first access through the existing duplicate-safe setup service.
7. **Billing Relationships dedicated route** — The sidebar Billing Relationships nav item now uses `#billing-relationships` and keeps its active state. It does not fall back to Clients.
8. **Canonical relationship access** — All entry points (Billing Relationships page, payer person, covered-client person, Review modal, self-pay row) resolve to the same canonical `account_id`.
9. **Review relationship deep-linking** — Open Billing Relationship Record from Review initializes the canonical relationship when none is already stored and opens the account editor directly. Close/save returns to the same Review session.
10. **Active-tab preservation** — Closing or saving editors returns to the originating tab (Clients, Billing Relationships, person record, or Review) rather than defaulting to Review.
11. **Write-token messaging** — Missing or expired write token returns `Write access expired. Refresh Jordana Billing and try again.` instead of a generic `Forbidden.`.
12. **SSL blank-env handling** — Blank `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` values are treated as unset before sync requests. Valid nonblank paths are preserved.
13. **Signing/notarization preparation** — `scripts/sign_and_notarize_release.sh` added for supervised Developer ID signing. Included in the release payload via `build_release.sh`.

### Bug Fixes Inherited from test.10

1. **Composite cursor ordering fix** — sync cursor comparison now correctly handles rows with equal `ingested_at` values by using `snapshot_key` as a tiebreaker.
2. **Flaky test fix** — `test_07_health_endpoint` now includes a kill fallback on timeout.

### Bug Fixes Inherited from test.9

1. **In-app Quit** — visible sidebar Quit safely stops sync work and the local server.
2. **Installer stale-runtime hardening** — installation reads and force-reinstalls the exact app wheel from the manifest, verifies payload and installed file checksums, imports installed build info, launches the app, and confirms the running server build ID.
3. **Rollback-safe updates** — an already-running Jordana Billing server is coordinated before replacement, unrelated port owners are refused, and the prior app/runtime is restored if verification fails.
4. **June reconciliation workflow** — dry-run/apply is verified for missing sessions, extra sessions, possible duplicates, edited event versions, excluded/non-client billing issues, and approved-session warnings. Missing recovered rows appear in Review and Sessions while unresolved/excluded rows stay out of client reports and invoice staging.
5. **Report filtering** — client-facing session exports exclude unresolved and excluded rows; All Appointments remains the complete audit ledger.

### Prior Test Releases

`v0.1.0-test.14` was built from commit `e31e0e2`. test.15 supersedes test.14 for installation and update testing.

`v0.1.0-test.13` was built from commit `5436468`. test.14 supersedes test.13 for installation and update testing.

`v0.1.0-test.11` was built from commit `7bcb8d3`. test.12 supersedes test.11 for installation and update testing.

`v0.1.0-test.10` was built from commit `424cda3`.

`v0.1.0-test.8` was built from commit `d97d6babc2278bd1e19fbc36319d65acce24fbb4`. Its DMG payload was correct, but supervised installation showed that stale installed runtime code could remain when multiple releases shared package version `0.1.0`. test.10 superseded test.8.

`v0.1.0-test.7` was built from commit `179da1fe14ac1fd56ed1e6b939b34fafe7299760` but was never published.

The prior installed-smoke baseline remains `v0.1.0-test.6` from commit `0dec58b6bf5ab35e2d48600b57fec83a477e304d`, which preserved existing private configuration and SQLite data during the brooketest upgrade installation and passed the major Billing Relationship, filing-owner, delivery-contact, invoice, and data-preservation workflows.

An earlier test.6 artifact built from commit `6c3dbab` using Python 3.11 was rejected and was not published or distributed. It must not be used.

## Controlled Beta Decision

The test.11 artifact may be installed on Jordana's Mac for a controlled June-invoice beta when all of the following are true:

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
