# Clean Test-Mac Acceptance Checklist

Use this checklist on Brooke's spare clean Mac before installing anything on Jordana's Mac. Do not use real credentials in screenshots or notes.

## Current Status — 2026-07-02

Brooke reports that the current one-click installer successfully completed an install and launch on a test Mac. This confirms that the basic native setup, offline runtime installation, and daily app launch work in at least one real installation.

The checklist is not yet recorded as fully complete. Do not assume restart, duplicate launch, cross-user port ownership, unrelated port conflict, missing-config, missing-database, reinstall preservation, or uninstall preservation passed unless they are explicitly recorded below.

Before final production handoff, record the release filename and commit, checksum result, test Mac and macOS version, Python version, Gatekeeper behavior, completed steps, deferred steps, and operational smoke-test result.

Installer rollback behavior is implemented in the release installer but still
needs clean-Mac evidence. During reinstall, the previous app bundle is kept as
`Jordana Billing.app.previous` until the replacement verifies. If verification
fails, the installer should restore `.previous`; if no previous app existed, it
should remove the failed app. Private configuration and SQLite data remain
outside the app and must be preserved.

### Current Test Build — v0.1.0-test.17

This is a controlled pilot/test release, not a final production release.

- **Release label:** v0.1.0-test.17
- **Python package/application version:** 0.1.0.post17
- **DMG:** recorded in the GitHub release and `release_manifest.json`
- **Manifest commit:** recorded in `release_manifest.json`
- **Build ID:** recorded in `release_manifest.json` and exposed by `/api/build-info`
- **source_tree_dirty:** false
- **builder Python:** 3.14.4
- **requires_python:** 3.14.x
- **architecture:** arm64
- **DMG checksum verification:** required before publication
- **DMG SHA-256:** recorded in the published `.sha256` asset
- **hdiutil verify:** required before publication
- **Private-file scan:** no `.env`, SQLite, or PDF files found
- **contains_private_data:** false
- **Local browser smoke:** required for Reconciliation and Quit before publication
- **Unit tests:** required before publication
- **Temporary-DB acceptance test:** required before publication (operational database untouched)
- **Privacy and Git safety checks:** required before publication

test.17 supersedes test.15 for installation and update testing because it uses
a unique package version and verifies the exact installed runtime plus running
server build ID before reporting success.

### Bug Fixes In test.17

1. **Brett/Peter billing cleanup** — stale archived Billing Relationship account links are detached when Bill To is corrected.
2. **Erroneous relationship deletion** — mistaken archived relationships can be deleted when they have no protected account-specific billing history.
3. **Service-period invoice lists** — invoice and payment list surfaces show service period instead of invoice number/date.
4. **Edit Session from drafts** — draft invoice lines route linked sessions back to Review instead of using the old limited line editor.
5. **Draft deletion and invoice cleanup** — true draft invoices can be deleted, PDF footers are removed, and recipient blocks no longer show `Via Email` or `Via Mail`.
6. **Billing Relationship ordering** — relationship rows sort by payer last name and first name.

### Bug Fixes In test.15

1. **Review self-pay switch** — Review can switch a single-client session to Self pay and detach the stale session-level Billing Relationship/account link.
2. **Billing Relationship switcher** — Change payer or shared billing opens the relationship wizard from Review.
3. **Structured person selection** — Billing Relationship searches show explicit Select/Add/Remove actions.
4. **Covered-client edit refresh** — Covered-client changes refresh the originating Review candidate before returning.
5. **Last-name-first list labels** — Invoice, payment, client, and Billing Relationship list views show person names as Last, First while Review stays date-driven.

### Bug Fixes In test.14

1. **Static asset cache-busting** — CSS/JS versioned with mtime query strings and `no-store` headers.
2. **Inactive payer conflict fix** — `has_payer_record_conflict` counts only active billing parties.
3. **SELECT change-event handling** — `bindInputAndChange` adds `change` listeners to SELECT elements.
4. **Inline invoice workspace** — Invoice workspace renders inline at laptop widths with smooth scroll.
5. **Paid-at-session Receipt button** — Review inspector shows Receipt button for paid-at-session payments.

### Prior Test Builds

`v0.1.0-test.15` is superseded by test.17 for installation and update testing.

`v0.1.0-test.14` was built from commit `e31e0e2` with Python 3.14.4. test.15
supersedes test.14 for installation and update testing.

`v0.1.0-test.13` was built from commit `5436468` with Python 3.14.4. test.14
supersedes test.13 for installation and update testing.

The prior installed-smoke baseline remains test.6 from commit `0dec58b`. That
DMG was installed successfully on the brooketest account. Existing private
configuration and SQLite database were preserved during upgrade. Live smoke
testing passed for the major Billing Relationship, filing-owner,
delivery-contact, invoice, and data-preservation workflows.

An initial test.6 artifact built from commit `6c3dbab` using Python 3.11
was rejected before installation and was not published. The correct
replacement was built from commit `0dec58b` using Python 3.14.4 in a
clean temporary clone outside the Documents directory.

The prior test.5, test.6, test.7, test.8, test.9, test.10, test.11, test.12,
test.13, and test.14 builds remain historically accurate for the periods in
which they were the current builds. test.17 supersedes test.15 for installation
and stale-runtime verification.

The full clean-Mac acceptance evidence record (restart, duplicate launch,
cross-user port ownership, unrelated port conflict, missing-config,
missing-database, uninstall preservation) remains incomplete and should
be recorded before final production handoff.

### v0.1.0-test.13 Acceptance Checklist Results

1. **Build exact test.13 DMG** — required before publication
2. **Verify checksum locally** — required before publication
3. **Verify release manifest, exact wheel, package version, commit, and build ID** — required before publication
4. **Verify private-file scan** — required before publication
5. **Verify Reconciliation and Quit in local browser on a temporary database** — passed locally before build
6. **Install test.9 over an older installed build and prove stale-runtime replacement** — required before publication
7. **Verify installed server reports expected build ID** — required before publication
8. **Run clean-account acceptance** — pending
9. **Publish/download/checksum the GitHub release asset before installation** — pending

### Prior v0.1.0-test.6 Acceptance Checklist Results

1. **Install test.6 over existing brooketest installation** — passed
2. **Verify private configuration and DB preservation** — passed
3. **Verify release label and manifest** — passed (v0.1.0-test.6, commit 0dec58b)
4. **Test arbitrary existing filing person** — passed
5. **Test inline-created filing person** — passed
6. **Verify persistence after close/reopen** — passed
7. **Verify no accidental payer/Bill To/Participant/covered-client/delivery-contact linkage** — passed
8. **Verify future draft inheritance** — passed
9. **Verify finalized invoice immutability** — passed
10. **Run clean-account acceptance** — deferred (full clean-Mac evidence record incomplete)
11. **Publish only the exact verified DMG after brooketest passes** — superseded by test.9 release publication path

### Unresolved-Client Refresh Behavior

During smoke testing, some unresolved/unknown-client sessions initially
appeared with safe fallback defaults (e.g., Standard 60) and did not yet
show the final time classification. This is expected workflow behavior,
not a parser defect:

1. An unknown/unresolved client appears with a safe fallback.
2. The user confirms and saves the client identity.
3. After a manual refresh, the system recognizes the client.
4. The correct session duration, time category, and related defaults are
   then applied.

A future UX improvement may automatically reparse/refresh the session
immediately after client confirmation so the user does not need a manual
refresh. This improvement is not yet implemented.

## Prerequisites

- macOS 12 or later on Apple Silicon.
- The Python major/minor version listed in `release_manifest.json` installed once.
- Access to the private GitHub repository release page.
- The versioned release DMG and matching `.sha256` file from the private pre-release.
- For repeated test builds with the same application version, use the explicit
  release label in the filename, for example
  `JordanaBilling-v0.1.0-test.9-<commit>-macos-arm64.dmg`.
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

### Signing And Notarization Evidence

If a Developer ID certificate and notarytool Keychain profile are available,
run the prepared signing/notarization script against the release payload and
DMG before distribution. Record the sanitized outcome of:

- `codesign --verify --deep --strict --verbose=2`
- `spctl --assess`
- `xcrun notarytool submit --wait`
- `xcrun stapler staple`
- `xcrun stapler validate`
- `hdiutil verify`

If credentials are unavailable, record that signing/notarization was prepared
but not completed. Do not describe Gatekeeper acceptance or notarization as
passed unless the actual commands succeeded.

### SSL Smoke Evidence

Calendar Sync should be exercised once with blank inherited `SSL_CERT_FILE` and
`REQUESTS_CA_BUNDLE` values to confirm they are ignored, and once with the
normal environment to confirm TLS verification remains enabled through the same
`urllib` path used by production sync. Do not record the Apps Script URL, API
key, or private payloads.

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
Date: 2026-07-02
Release DMG: JordanaBilling-v0.1.0-test.6-0dec58b6bf5a-macos-arm64.dmg
Release commit: 0dec58b6bf5ab35e2d48600b57fec83a477e304d
Checksum verified: passed
Test Mac / macOS: brooketest account, Apple Silicon
Installer Python: 3.14.4
Gatekeeper result: not recorded (full clean-Mac evidence pending)
Passed steps: 1-9 (install, config/DB preservation, release label/manifest,
  filing person tests, persistence, linkage separation, draft inheritance,
  finalized immutability)
Deferred steps: 10 (clean-account acceptance), 11 (publish final DMG)
Operational smoke path: passed (Billing Relationship, filing-owner,
  delivery-contact, invoice, data-preservation workflows)
Known limitations accepted: unresolved-client requires manual refresh after
  confirmation; full clean-Mac evidence record incomplete
Tester: Brooke
```

```text
Date: 2026-07-03
Release DMG: JordanaBilling-v0.1.0-test.7-179da1fe14ac-macos-arm64.dmg
Release commit: 179da1fe14ac1fd56ed1e6b939b34fafe7299760
Checksum verified: passed
SHA-256: f4eeab417425aad731570b42185810c6712b588bba7f5fe83129d44b2d93bd85
Test Mac / macOS: not yet installed
Installer Python: 3.14.4 builder; installed runtime not yet recorded
Gatekeeper result: not yet recorded
Passed steps: build, manifest check, checksum verification, private-file scan,
  local browser smoke for inline draft PDF and stored finalized PDF previews
Deferred steps: brooketest install, clean-account acceptance, restart,
  duplicate launch, reinstall preservation, uninstall preservation
Operational smoke path: local browser preview smoke passed; installed app smoke pending
Known limitations accepted: full clean-Mac evidence record incomplete
Tester: Brooke/Codex local release build
Note: test.7 was never published and is superseded by test.9
```

```text
Date: 2026-07-03
Release DMG: JordanaBilling-v0.1.0-test.8-d97d6babc227-macos-arm64.dmg
Release commit: d97d6babc2278bd1e19fbc36319d65acce24fbb4
Checksum verified: passed
SHA-256: 8cf5176bd5aba1aef79c798f4fe01955d358f988237c33efeaaa782842cb266b
hdiutil verify: passed
Test Mac / macOS: not yet installed
Installer Python: 3.14.4 builder; installed runtime not yet recorded
Gatekeeper result: not yet recorded
Passed steps: build, manifest check, checksum verification, hdiutil verify,
  private-file scan, unit tests (2,729 passed, 68 skipped), temporary-DB
  acceptance test, privacy and Git safety checks, git diff --check,
  local browser smoke for inline draft PDF and stored finalized PDF previews
Deferred steps: brooketest install, clean-account acceptance, restart,
  duplicate launch, reinstall preservation, uninstall preservation
Operational smoke path: local browser preview smoke passed; installed app smoke pending
Known limitations accepted: full clean-Mac evidence record incomplete
Tester: Brooke/Codex local release build
```

## Stop Conditions

Stop before Jordana's Mac if any step creates a blank DB unexpectedly, overwrites private config, starts without the expected DB, requires PyPI/GitHub during launch, asks to install Rosetta, kills an unrelated process, exposes secrets in output, fails to launch after reboot, or loses the only available working app during an update.

## Rollback

Move `~/Applications/Jordana Billing.app` to Trash. Keep `~/Library/Application Support/Jordana Billing` intact unless Brooke explicitly chooses to remove private data.

If automatic app-bundle restore fails, preserve any
`Jordana Billing.app.previous` or failed app bundle left in `~/Applications`
and use the prior verified release DMG and checksum for manual recovery.
