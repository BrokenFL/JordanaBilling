# Jordana Billing v0.1.0-test.7 Release Notes

## Release Status

This private release is approved for a supervised Jordana beta using June invoices. It remains a controlled pilot/test release and is not represented as final production software.

Use the exact `v0.1.0-test.7` artifact attached to the private GitHub prerelease. The release manifest inside the DMG records the source commit and checksum facts.

```text
JordanaBilling-v0.1.0-test.7-179da1fe14ac-macos-arm64.dmg
```

Release facts:

- **Release label:** v0.1.0-test.7
- **Manifest commit:** `179da1fe14ac1fd56ed1e6b939b34fafe7299760`
- **Application version:** 0.1.0
- **Source tree dirty:** false
- **Builder Python:** 3.14.4
- **Required Python family:** 3.14.x
- **Architecture:** arm64
- **DMG checksum verification:** passed locally; verify the matching `.sha256` asset again after download
- **DMG SHA-256:** `f4eeab417425aad731570b42185810c6712b588bba7f5fe83129d44b2d93bd85`
- **Private-file scan:** no `.env`, SQLite, or PDF files found
- **Contains private data:** false
- **Wheelhouse:** `jordana_invoice-0.1.0`, ReportLab, Pillow, charset-normalizer

The prior verified controlled-beta release was `v0.1.0-test.6` from commit `0dec58b`. An earlier test.6 artifact built from commit `6c3dbab` using Python 3.11 was rejected and was not published or distributed. Do not use it.

## Acceptance Completed

The test.7 artifact is locally built, checksum-verified, privacy-scanned, and
ready for controlled installation testing. It has not yet been installed on the
brooketest account in this acceptance record.

Confirmed:

1. Release label and manifest matched the intended build.
2. Local checksum verification passed.
3. Release payload private-file scan passed.
4. Full unit suite passed.
5. Acceptance import test passed on a temporary database without touching the operational database.
6. Privacy and Git safety checks passed.
7. Canonical draft PDF preview loads inline in the Invoices workspace.
8. Stored finalized PDF preview loads inline in the Invoices workspace.

Prior test.6 installed-smoke results remain the latest recorded brooketest
installation evidence: existing private configuration and SQLite data were
preserved, filing-owner workflows passed, future draft inheritance passed,
finalized invoice immutability passed, and major Billing Relationship,
delivery-contact, invoice, and data-preservation smoke workflows passed.

## Controlled Beta Conditions

Install on Jordana's Mac only when:

- Brooke has retained a verified source database backup.
- The private `.env` and operational SQLite database are transferred separately and securely.
- Transfer checksums and SQLite integrity pass.
- Python 3.14.x is available for the one-time installation.
- Brooke is present for installation and the first complete smoke path.
- Jordana reviews every session and invoice before approval or finalization.
- The prior working installation and source backup remain available until the June billing cycle is completed successfully.

## Feature Highlights

### Review

- Independent saves for Participants, Bill To, and Session Draft
- Approval single-submit protection
- Clean overlay completion and stale-state removal
- Duplicate resolution using **Confirm Duplicate & Next**
- Conservative handling of unresolved calendar evidence

### Billing Relationships

The editor separates:

1. **Bill To / payer** — responsible for payment
2. **Covered clients** — people whose sessions the payer covers
3. **Participants** — people who attended
4. **Save invoices under** — filing owner and folder organization
5. **Send invoice to** — delivery contact

Filing owners and delivery contacts can be selected from the active people directory or created inline. Neither action silently changes payer, Bill To, covered-client, or Participant roles.

### Invoices

- Monthly staging by Bill To and billing month
- Canonical draft PDF preview embedded directly in the Invoices workspace
- Stored finalized/void PDF preview embedded directly in the Invoices workspace
- Shared PDF renderer for draft and final output
- Separate `Open PDF` / `Open PDF in new tab` actions for standalone browser PDF viewing
- Transaction-safe numbering and immutable finalization
- Repeated finalization returns the existing canonical PDF
- Client/month filing folders
- Organization, payer, covered-client, and explicitly selected person filing owners
- Prior-balance and account-summary snapshots
- Optional invoice-specific insurance coding
- Void and reissue
- Waived late-cancellation $0.00 line support

### Payments

- Payment ledger and allocations
- Paid-at-session workflow
- Available-funds application
- Reversals, voids, corrections, receipts, and balance views

### Installation

- Native setup app
- Offline wheelhouse
- Explicit Pillow runtime dependency for ReportLab PDF rendering
- Installer and installed-app verification import `PIL` so PDF dependency problems fail during installation
- Private runtime
- Application Support storage for config and SQLite
- Documents storage for reports and client files
- Daily launch without Git, pip, PyPI, or Terminal
- Rollback-safe app-bundle replacement

## Known Beta Friction

### Client Refresh After Confirmation

An unresolved client may initially show fallback values such as Standard 60. After the client identity is confirmed and saved, refresh or reopen the session before approval so the final duration, time category, and related defaults are visible.

### Delivery Is Manual

The application creates and files invoice PDFs but does not send or track them by email or mail.

## Known Limitations

Not included in this release:

- credits, refunds, or write-offs
- formal reconciliation or month close
- automated multi-invoice payment allocation
- production historical paid-at-session backfill
- polished management dashboard
- notarized installer
- bundled Python runtime

## Remaining Production Evidence

The following acceptance scenarios still require a fully recorded clean-Mac result before final production declaration:

- restart launch
- duplicate launch
- cross-user port ownership
- unrelated process on port 8765
- missing-config failure
- missing-database failure
- reinstall preservation
- uninstall preservation
- installer rollback behavior

Follow `docs/HANDOFF_TO_JORDANA_MAC.md` and `docs/TEST_MAC_ACCEPTANCE.md`.

## Privacy

No private configuration, operational database, credentials, invoices, receipts, reports, logs, real client data, or real diagnosis codes are included in the release artifact.

Private production data must never move through GitHub or a public release asset.
