# Jordana Billing v0.1.0-test.13 Release Notes

## Release Status

This private release is approved for supervised Jordana beta testing. It remains
a controlled pilot/test release and is not represented as final production
software.

Use the exact `v0.1.0-test.13` artifact published on GitHub. The release
manifest inside the DMG records the source commit, build ID, exact wheel path,
and checksum facts.

```text
JordanaBilling-v0.1.0-test.13-<commit>-macos-arm64.dmg
```

Release facts:

- **Release label:** v0.1.0-test.13
- **Python package/application version:** 0.1.0.post13
- **Manifest commit:** recorded in `release_manifest.json`
- **Build ID:** recorded in `release_manifest.json` and exposed by `/api/build-info`
- **Source tree dirty:** false
- **Builder Python:** 3.14.x
- **Required Python family:** 3.14.x
- **Architecture:** arm64
- **DMG checksum verification:** required before publication; verify the matching `.sha256` asset again after download
- **DMG SHA-256:** recorded in the published `.sha256` asset
- **hdiutil verify:** required before publication
- **Private-file scan:** no `.env`, SQLite, PDF, report, invoice, receipt, or private data files
- **Contains private data:** false
- **Wheelhouse:** exact `jordana_invoice-0.1.0.post13` app wheel plus pinned production dependencies
- **Unit tests and focused browser smoke:** required before publication
- **Temporary-DB acceptance test:** required before publication when running the broader release checklist
- **Privacy and Git safety checks:** required before publication

## Bug Fixes In test.13

1. **Paid-at-session approval after saved details** — Approval now reuses the saved paid-at-session amount, date, method, reference, and administrative note when the Session Details section is collapsed. This fixes the unexpected approval error without creating duplicate payments or changing finalized invoice history.
2. **Invoices screen simplification** — The invoice library now exposes only Status and Service Period filters, lists service periods dynamically, shows filtered Draft and Finalized counts/totals, and sorts by Bill To/client first name.
3. **Draft invoice table layout** — Draft invoice rows now separate Date and Participants and show Date, Participants, Session Type, Duration, and Rate as distinct columns.
4. **Review raw calendar title** — Review queue uses the RAW CLIENT label and displays the original raw calendar event title, matching the Session evidence source without altering raw evidence.
5. **Invoice header presentation** — Draft previews, finalization previews, finalized invoice views, and PDFs show the header as `INVOICE`, an unlabeled invoice date, and an unlabeled invoice number. Billing Period is removed from the invoice header.
6. **Payments period filtering** — Outstanding, Paid, and All Payments now filter by invoice/service period, display Invoice Period instead of Invoice Date on invoice-payment tables, sort by Bill To/client first name, and include posted paid-at-session session payments in Paid.
7. **Reports smoke verification** — `/reports` and `/api/reports` were verified in a real browser during release prep after a user-reported loading concern.

## Bug Fixes Inherited from test.12

1. **Duplicate Billing Relationships display suppression** — One visible active row per actual Billing Relationship. Canonical active account wins; implicit/session-derived fallback rows are suppressed while Edit and canonical `account_id` are preserved. No live data merge is performed.

## Bug Fixes Inherited from test.11

1. **Weekday column** — Review queue shows short weekday abbreviation.
2. **Weekend/evening rate matching** — Manually selected weekend/evening session type propagates `time_category` to rate suggestion.
3. **Edit Session** — Eligible approved sessions return to Review without a reason prompt; draft line removed and total recalculated atomically.
4. **Billing Relationship delete/archive** — Unused relationships delete; history-protected relationships archive.
5. **Self-pay Edit and canonical relationship access** — Self-pay rows open the canonical account editor consistently.
6. **Write-token messaging and SSL blank-env handling** — User-facing auth expiry and blank certificate env handling are hardened.

## Installation Notes

- The DMG is not notarized. Gatekeeper may require right-click Open.
- Python 3.14.x must be available for the one-time installer because the release wheelhouse is built for that Python family.
- The installer preserves existing private config, SQLite data, reports, invoices, and receipts outside the app bundle.
- Do not transfer private production data through GitHub.

## Controlled Beta Conditions

Install on Jordana's Mac only when:

- Brooke has retained a verified source database backup.
- The private `.env` and operational SQLite database are transferred separately and securely.
- Transfer checksums and SQLite integrity pass.
- Brooke is present for installation and the first complete smoke path.
- Jordana reviews every session and invoice before approval or finalization.
- The prior working installation and source backup remain available until the billing cycle completes successfully.

## Known Limitations

Not included in this release:

- credits, refunds, or write-offs
- formal reconciliation or month close
- automated multi-invoice payment allocation
- production historical paid-at-session backfill
- polished management dashboard
- notarized installer
- bundled Python runtime

## Privacy

No private configuration, operational database, credentials, invoices, receipts,
reports, logs, real client data, or real diagnosis codes are included in the
release artifact.

Private production data must never move through GitHub or a release asset.
