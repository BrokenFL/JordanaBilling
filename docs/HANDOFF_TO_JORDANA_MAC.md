# Handoff To Jordana Mac

This is the operational guide for installing Jordana Billing on Jordana's Mac and beginning the controlled June-invoice beta.

Read first:

1. `AGENTS.md`
2. `docs/CURRENT_IMPLEMENTATION_STATUS_AND_HANDOFF.md`
3. `docs/PRIVATE_DATA_TRANSFER.md`
4. `docs/FRESH_INSTALL.md`
5. `docs/TEST_MAC_ACCEPTANCE.md`

## Current Handoff Decision

The `v0.1.0-test.9` release may be used for a supervised Jordana beta after
Brooke verifies the published GitHub artifact, checksum, installer runtime
identity, and upgrade-over-old-build test. It is not represented as final
production software.

Use this exact artifact:

```text
JordanaBilling-v0.1.0-test.9-<commit>-macos-arm64.dmg
```

Release facts to verify before installing:

- Manifest commit: matches the GitHub release and `release_manifest.json`
- Python package/application version: 0.1.0.post9
- Build ID: matches `release_manifest.json` and the installed `/api/build-info`
- Builder Python: 3.14.4
- Required Python family: 3.14.x
- Architecture: Apple Silicon arm64
- Checksum verification: passed
- Private-file scan: passed
- Installer exact-wheel/runtime verification: passed
- Upgrade over an older installed build: passed
- Existing config/database preservation during upgrade: passed

Do not use the rejected Python 3.11 test.6 artifact from commit `6c3dbab`, the
superseded test.7 artifact, or the test.8 artifact for installation/update
testing.

## Before Leaving Brooke's Mac

Stop the application and preserve the current operational state.

Required safeguards:

1. Create a verified SQLite backup outside the repository.
2. Run `PRAGMA integrity_check` against the backup.
3. Record the current migration IDs through `017_relationship_filing_owner_target`.
4. Record row counts for critical operational tables.
5. Calculate SHA-256 checksums for every private file being transferred.
6. Keep Brooke's original database and backup unchanged until Jordana completes the June billing cycle.

The source database must not be deleted, reset, overwritten, or treated as disposable.

## Files That Must Transfer Separately

GitHub and the DMG contain sanitized application code only. Transfer private operational data directly or through an encrypted method.

Minimum production/beta state:

```text
config/.env
data/jordana_invoice.sqlite3
backups/
```

Preserve when available:

```text
Session Lists/
Client Files/
private branding
existing invoices and receipts
TRANSFER_MANIFEST.txt
```

Recommended transfer methods:

- direct AirDrop between trusted Macs
- encrypted external drive
- encrypted archive through an approved secure channel

Do not use GitHub, ordinary email, chat, screenshots, issue comments, or unencrypted cloud folders for private data.

## Destination Locations

Application:

```text
~/Applications/Jordana Billing.app
```

Private operational state:

```text
~/Library/Application Support/Jordana Billing/
  config/.env
  data/jordana_invoice.sqlite3
  backups/
  logs/
  runtime/
```

User-facing outputs:

```text
~/Documents/Jordana Billing/
  Session Lists/
  Client Files/
```

## Installation Order

1. Confirm Jordana's Mac is Apple Silicon.
2. Install the Python 3.14 family required by this release if it is not already available.
3. Transfer the DMG and matching `.sha256` file.
4. Verify the checksum.
5. Transfer the private package separately.
6. Verify private-file checksums and SQLite integrity.
7. Open the DMG.
8. Double-click `Install Jordana Billing.app`.
9. Allow the setup app to delegate to `scripts/install_release.sh` inside the
   embedded release payload while preserving the transferred config and
   database.
10. Do not choose clean-start database initialization when the transferred operational database exists.
11. Complete installation and open the installed app.

Checksum pattern:

```bash
shasum -a 256 -c JordanaBilling-v0.1.0-test.9-<commit>-macos-arm64.dmg.sha256
```

Expected result: the command reports `OK`.

## Gatekeeper

The application is ad-hoc signed and not notarized. macOS may require:

1. Right-click the installer app.
2. Choose **Open**.
3. Confirm the Security & Privacy prompt when required.

Do not silently disable Gatekeeper. Stop if macOS asks to install Rosetta; this release is intended to run natively on Apple Silicon.

## Installer Safety Contract

The installer must:

- preserve existing private config
- preserve the operational database
- stop or coordinate with an already-running Jordana Billing process before replacement
- stage the replacement app before changing the installed app
- install the exact wheel recorded in the release manifest
- force-replace the installed runtime even when an older release used the same base package version
- verify installed package build info and running server build ID
- move the prior app to `Jordana Billing.app.previous`
- verify the replacement before deleting the prior app
- restore the prior app automatically when replacement verification fails
- leave Application Support data and Documents outputs untouched during app-bundle rollback

A failed app update must not destroy the only working app or private operational data.

## First Launch Verification

Confirm all of the following before Jordana begins real review:

1. The app opens from `~/Applications/Jordana Billing.app`.
2. The browser opens only after the local server is healthy.
3. Existing people, relationships, sessions, invoices, payments, and audit history are visible when they were transferred.
4. The database was not replaced with a blank database.
5. Calendar Sync succeeds.
6. Sync does not duplicate snapshots or sessions.
7. The expected Documents folders exist and are writable.
8. A fresh private backup is created on Jordana's Mac.

## June Beta Smoke Path

Complete this path with Brooke present before Jordana processes the full month:

1. Open one unresolved June calendar item.
2. Confirm and save Participants.
3. Confirm and save Bill To.
4. Confirm duration, session type, time category, rate, and payment handling.
5. Save the Session Draft.
6. Approve the session.
7. Confirm the overlay closes and the same session cannot be submitted again.
8. Open the resulting draft invoice.
9. Review the canonical draft PDF.
10. Confirm line items, rates, total, filing owner, and delivery method.
11. Finalize one carefully verified invoice.
12. Open the stored PDF from the expected client/month folder.
13. Record or apply a supported payment.
14. Restart the Mac.
15. Confirm the same records remain visible and unchanged.

## Routine Jordana Workflow

Routine review focuses on:

- Participants
- Bill To
- Duration
- Session type
- derived time category
- Rate
- Payment Handling
- Approve

Section saves do not approve a session automatically. No invoice is finalized without Jordana's explicit review and confirmation.

Duplicate resolution uses **Confirm Duplicate & Next** and must complete by closing the overlay, clearing stale state, and advancing safely.

## Billing Relationship Concepts

Keep these concepts separate:

- **Who pays / Bill To:** person or organization responsible for payment
- **Who are they paying for:** covered clients
- **Participants:** people who attended the session
- **Save invoices under:** filing owner and client-folder organization
- **Send invoice to:** delivery contact

Changing one concept must not silently convert the person into another role.

## Unresolved-Client Refresh

An unknown client may initially show safe fallback values such as Standard 60. After the client identity is confirmed and saved, Jordana may need to refresh or reopen the session before the final duration, time category, and related defaults appear.

Do not approve until the refreshed values are visible and correct.

## What The Beta Does Not Automate

The application does not currently:

- send invoices by email or mail
- track invoice delivery
- process credits, refunds, or write-offs
- perform formal reconciliation or month close
- automatically allocate one payment across multiple invoices
- apply the historical paid-at-session analyzer to production data

These are later enhancements, not reasons to bypass the implemented review controls.

## Stop Conditions

Stop the beta and preserve all data if any of the following occurs:

- the installer creates or proposes a blank database unexpectedly
- the transferred operational database is missing or unreadable
- private config is overwritten
- a migration fails
- an invoice number is consumed after a failed finalization
- a finalized invoice changes after reopening
- duplicate sessions or duplicate invoice lines appear after sync
- the application kills or reuses an unrelated process on port 8765
- a secret appears in logs, screenshots, or output
- the prior working app cannot be restored after an update failure

Do not troubleshoot by deleting the database.

## Beta Support Rule

During the first June cycle:

- keep Brooke's source backup intact
- make a fresh backup before migrations, bulk corrections, imports, or relationship merges
- document unexpected behavior before retrying destructive or financial actions
- prefer void/reissue over editing finalized invoice history
- never use GitHub to move private data

## Completion Criteria

The controlled beta is successful when Jordana can:

1. launch without Terminal
2. sync Calendar evidence safely
3. review and approve June sessions
4. create and review draft invoices
5. finalize and open canonical PDFs
6. record supported payments
7. restart and reopen without losing state
8. complete the cycle without duplicate sessions, stale approvals, overwritten history, or private-data exposure

Final production declaration still requires the remaining clean-Mac acceptance evidence and the narrow follow-up items listed in `docs/CURRENT_IMPLEMENTATION_STATUS_AND_HANDOFF.md`.
