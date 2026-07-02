# Clean Test-Mac Acceptance Checklist

Use this checklist on Brooke's spare clean Mac before installing anything on Jordana's Mac. Do not use real credentials in screenshots or notes.

## Current Status — 2026-07-01

Brooke reports that the current one-click installer successfully completed an install and launch on a test Mac. This confirms that the basic native setup, offline runtime installation, and daily app launch work in at least one real installation.

The checklist is not yet recorded as fully complete. Do not assume restart, duplicate launch, cross-user port ownership, unrelated port conflict, missing-config, missing-database, reinstall preservation, or uninstall preservation passed unless they are explicitly recorded below.

Before final production handoff, record the release filename and commit, checksum result, test Mac and macOS version, Python version, Gatekeeper behavior, completed steps, deferred steps, and operational smoke-test result.

Installer rollback behavior is implemented in the release installer but still
needs clean-Mac evidence. During reinstall, the previous app bundle is kept as
`Jordana Billing.app.previous` until the replacement verifies. If verification
fails, the installer should restore `.previous`; if no previous app existed, it
should remove the failed app. Private configuration and SQLite data remain
outside the app and must be preserved.

## Prerequisites

- macOS 12 or later on Apple Silicon.
- The Python major/minor version listed in `release_manifest.json` installed once.
- Access to the private GitHub repository release page.
- The versioned release DMG and matching `.sha256` file from the private pre-release.
- For repeated test builds with the same application version, use the explicit
  release label in the filename, for example
  `JordanaBilling-v0.1.0-test.5-<commit>-macos-arm64.dmg`.
- The private Apps Script URL and ingest API key available locally, not in GitHub, email, chat, screenshots, or logs.

## Steps

1. Sign into GitHub with an account authorized for the private `BrokenFL/JordanaBilling` repository.
2. Open the repository's Releases page.
3. Download the test release DMG and matching `.sha256` file.
4. Verify checksum. Expected result: the command prints `OK` and the checksum file names only the DMG filename, not Brooke's local build path.

```bash
shasum -a 256 -c JordanaBilling-<release-label-or-version>-<commit>-macos-arm64.dmg.sha256
```

5. Open the DMG. Expected result: the top level shows `Install Jordana Billing.app` and README only; the daily app and `ReleasePayload` folder are not exposed as separate DMG items.
6. Double-click `Install Jordana Billing.app`. If Gatekeeper blocks it, use right-click Open or Security & Privacy approval. Do not bypass Gatekeeper silently.
7. Confirm no Rosetta prompt appears. Stop if macOS asks to install Rosetta.
8. Enter the Apps Script URL. On a reinstall where `~/Library/Application Support/Jordana Billing/config/.env` already exists, this field is disabled and the setup app says the existing private config will be preserved.
9. Enter the ingest API key. Expected result: the key field is hidden and the key is not displayed afterward. On a reinstall with existing private config, this field is disabled so secrets do not need to be re-entered.
10. Check the clean-start database confirmation. Expected explanation: unresolved review evidence will sync; historical invoices, payments, clients, approved sessions, and billing relationships will not be imported. On a reinstall where `~/Library/Application Support/Jordana Billing/data/jordana_invoice.sqlite3` already exists, clean-start initialization is disabled and the setup app says the existing database will be preserved.
11. Click Install.
12. Expected result: setup installs `~/Applications/Jordana Billing.app`, writes `~/Library/Application Support/Jordana Billing/config/.env` with permissions `600`, creates `~/Documents/Jordana Billing/Session Lists` and `~/Documents/Jordana Billing/Client Files`, creates the clean database only after confirmation, runs verification, and reports success.
13. Click Open Jordana Billing. Expected result: the browser opens only after health readiness.
14. Confirm unresolved review items load after sync.
15. Confirm there are no old invoices, payments, clients, approved sessions, or billing relationships in an intentionally clean-start test.
16. Restart the Mac and launch `~/Applications/Jordana Billing.app` again.
17. Double-click twice and confirm the second launch reuses the existing healthy server rather than creating a duplicate.
18. From another macOS user account, leave Jordana Billing running on port `8765`, then try launching from this account. Expected result: a clear message says Jordana Billing is already running under another macOS user account. It must not kill the other process.
19. Start an unrelated process on port `8765`, then launch. Expected result: Jordana Billing refuses to stop or reuse it.
20. Temporarily move `config/.env` aside and test the missing config error. Restore the file afterward.
21. Temporarily move `data/jordana_invoice.sqlite3` aside and test the missing DB error. Restore the file afterward.
22. Reinstall the same release and confirm existing config and DB are preserved. Expected result: Apps Script URL and ingest API-key fields are disabled, clean-start initialization is disabled, and installation remains possible without re-entering secrets.
23. Remove `~/Applications/Jordana Billing.app` only, then confirm private data remains in Application Support and user-facing generated folders remain in Documents.

## Operational Smoke Path

After the installation mechanics pass, verify the actual user workflow with approved test data:

1. Launch the installed app by double-clicking.
2. Run Calendar Sync and confirm it completes without duplicate snapshots or sessions.
3. Open one review candidate and save Participants, Bill To, and Session Draft.
4. Approve the session and confirm the overlay closes, the item refreshes, and no duplicate approval is possible.
5. Open the resulting draft invoice.
6. Open the draft PDF preview and verify it matches the intended final layout except for DRAFT versus invoice number.
7. Finalize a disposable test invoice after confirming filing owner, delivery method, and readiness.
8. Open the finalized PDF and verify the file exists in the expected client/month folder.
9. Confirm payment and balance behavior using the approved test workflow.
10. Restart the Mac and confirm the same records remain visible.

## Evidence To Record

- Release filename and checksum result.
- Git commit recorded in the release manifest.
- Test Mac model, macOS version, and installer Python version.
- Confirmation that `.env` permissions are `600`.
- Confirmation that the Documents Session Lists and Client Files folders exist and are writable.
- Setup app success message.
- Whether Gatekeeper required right-click Open or Security & Privacy approval.
- Confirmation that no Rosetta prompt appeared.
- Offline launch result.
- Restart launch result.
- Duplicate-launch result.
- Cross-user, port-conflict, missing-config, and missing-DB error wording.
- Reinstall result confirming data preservation.
- Operational smoke-path result.
- Any untested step and the reason it was deferred.

## Acceptance Record

```text
Date:
Release DMG:
Release commit:
Checksum verified:
Test Mac / macOS:
Installer Python:
Gatekeeper result:
Passed steps:
Deferred steps:
Operational smoke path:
Known limitations accepted:
Tester:
```

## Stop Conditions

Stop before Jordana's Mac if any step creates a blank DB unexpectedly, overwrites private config, starts without the expected DB, requires PyPI/GitHub during launch, asks to install Rosetta, kills an unrelated process, exposes secrets in output, fails to launch after reboot, or loses the only available working app during an update.

## Rollback

Move `~/Applications/Jordana Billing.app` to Trash. Keep `~/Library/Application Support/Jordana Billing` intact unless Brooke explicitly chooses to remove private data.

If automatic app-bundle restore fails, preserve any
`Jordana Billing.app.previous` or failed app bundle left in `~/Applications`
and use the prior verified release DMG and checksum for manual recovery.
