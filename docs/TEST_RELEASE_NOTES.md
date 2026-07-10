# Jordana Billing v0.1.0-test.21 Release Notes

## Release Status

This private release is approved for supervised Jordana beta testing. It remains
a controlled pilot/test release and is not represented as final production
software.

Use the exact `v0.1.0-test.21` artifact published on GitHub. The release
manifest inside the DMG records the source commit, build ID, exact wheel path,
and checksum facts.

```text
JordanaBilling-v0.1.0-test.21-<commit>-macos-arm64.dmg
```

Release facts:

- **Release label:** v0.1.0-test.21
- **Python package/application version:** 0.1.0.post21
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
- **Wheelhouse:** exact `jordana_invoice-0.1.0.post21` app wheel plus pinned production dependencies
- **Focused tests, packaging checks, privacy checks, and Git safety checks:** required before publication

## Bug Fixes In test.21

1. **Review-after-session safeguard preserved** — Future sessions remain hidden from the actionable Review Queue and cannot be approved until their scheduled end time passes.
2. **Ended-only removal reconciliation** — Calendar absence can suppress only an unapproved appointment whose end time has passed; future and same-day appointments remain available for a later capture.
3. **Overlapping-batch presence protection** — When the current run's past and future windows overlap, an event present in either covering batch remains active. It is removed only when absent from every current covering batch.

## Bug Fixes Inherited from test.20

1. **Blank-boundary calendar reconciliation** — Calendar snapshot reconciliation now derives the documented inclusive date range from `captured_at` plus a canonical `past_3_days`, `past_7_days`, `next_7_days`, or `next_2_days` label when the Shortcut leaves explicit boundaries blank. Unknown labels remain non-covering.
2. **Upgrade/no-new-rows reconciliation** — An incremental sync with no new raw rows now rechecks pending candidates against preserved evidence, so a newly installed release can suppress already-imported removed occurrences without modifying raw snapshots.

## Bug Fixes Inherited from test.19

1. **Report Issue diagnostics** — The Review app can create a sanitized local diagnostics bundle for a reported error. It captures only operational build/schema/request context and deliberately excludes client names, clinical content, raw calendar data, invoices, receipts, logs, and the live database.
2. **Calendar snapshot reconciliation** — A pending calendar candidate is excluded when the newest complete, successful raw snapshot batch that explicitly covers its appointment date omits it. Raw snapshot evidence remains untouched; incomplete, failed, malformed, non-covering, and approved records are protected.

## Bug Fixes Inherited from test.18

1. **Approved-session Edit Session recovery** — `Edit Session` from an unfinalized draft invoice can return an approved session to Review and remove its draft invoice line. If a prior interrupted edit already left the session in Review while the draft line remained, the same action now cleans up that stale draft line instead of erroring.
2. **Draft invoice snapshot refresh** — Reapproving an edited session now refreshes existing draft invoice lines from the current approved session values. Draft invoices no longer keep stale amount, duration, participant, service, appointment-treatment, or description snapshots when the source session is corrected before finalization.
3. **Fred Colin June 8 regression coverage** — The release was checked against the supplied Jordana database backup using a temporary copy. The June 8 Fred Colin draft-invoice edit path returns to Review and restages with refreshed draft values.

## Bug Fixes Inherited from test.17

1. **Brett Barakett / Peter Grossman billing cleanup** — Changing Bill To now detaches stale archived Billing Relationship account links when the selected billing party no longer matches that relationship. Archived account links no longer block invoice staging when the session has an explicit valid Bill To.
2. **Erroneous Billing Relationship deletion** — Mistaken archived relationships can be deleted when they have no protected account-specific billing history. Stale unfinalized session links and relationship-specific aliases are cleaned up safely; finalized invoices, payments, receipts, and true protected history still force archive.
3. **Invoice lists show service period only** — Invoice, client, organization, and payment list surfaces now show the service period, such as `June 2026`, instead of invoice number and invoice date. The invoice number/date remain stored internally and on the actual invoice document.
4. **Draft invoice Edit Session workflow** — Draft invoice line editing now routes linked sessions back to Review through `Edit Session`, removing the draft line and recalculating totals instead of using the old limited line editor.
5. **Draft invoice deletion** — True draft invoices can be deleted from the draft editor. This removes draft line items without deleting the underlying sessions. Finalized invoices remain void-only.
6. **Invoice PDF footer removal** — Repeated page footers and page-number footer labels were removed from invoice PDFs.
7. **Recipient block cleanup** — Customer-facing invoice and receipt recipient blocks no longer display `Via Email` or `Via Mail`; they show only the recipient name plus applicable address and email details.
8. **Billing Relationship ordering and display** — Billing Relationships sort by payer last name and first name, and inactive archived account members no longer leak into active payer rows.

## Bug Fixes Inherited from test.15

1. **Review self-pay switch** — Review can switch a single-client session to Self pay and detach the stale session-level Billing Relationship/account link.
2. **Billing Relationship switcher** — Change payer or shared billing opens the relationship wizard from Review.
3. **Structured person selection** — Billing Relationship searches show explicit Select/Add/Remove actions.
4. **Covered-client edit refresh** — Covered-client changes refresh the originating Review candidate before returning.
5. **Last-name-first list labels** — Invoice, payment, client, and Billing Relationship list views show person names as Last, First while Review stays date-driven.

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
