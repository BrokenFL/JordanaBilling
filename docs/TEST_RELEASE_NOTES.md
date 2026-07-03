# Jordana Billing v0.1.0 Test Release

This is a private clean-Mac test release for Brooke's spare Mac. It is a pre-release and is not approved for Jordana's production Mac.

## Current Test Build — v0.1.0-test.6

This is a controlled pilot/test release, not a final production release.

- **Release label:** v0.1.0-test.6
- **DMG:** `JordanaBilling-v0.1.0-test.6-0dec58b6bf5a-macos-arm64.dmg`
- **Manifest commit:** `0dec58b6bf5ab35e2d48600b57fec83a477e304d`
- **application_version:** 0.1.0
- **source_tree_dirty:** false
- **builder Python:** 3.14.4
- **requires_python:** 3.14.x
- **architecture:** arm64
- **DMG checksum verification:** passed
- **Private-file scan:** no `.env`, SQLite, or PDF files found
- **contains_private_data:** false
- **Wheelhouse includes:** `jordana_invoice-0.1.0`, `reportlab 4.5.1`, `pillow 12.2.0`, `charset-normalizer 3.4.7`
- **Stale build artifacts removed** after wheel creation
- **DMG and checksum copied to `/Users/Shared`** and verified there

### Release History Correction

An initial test.6 artifact was built from commit `6c3dbab` using Python 3.11
and was rejected before installation. It was not published. A correct
replacement was built from commit `0dec58b` using Python 3.14.4 in a clean
temporary clone outside the Documents directory. The replacement passed
checksum and manifest verification and installed successfully on the
test Mac.

### Status

- test.6 was built from commit `0dec58b` with Python 3.14.4
- brooketest upgrade/data-preservation installation completed successfully
- Existing private configuration and SQLite database were preserved during upgrade
- Live smoke testing passed for the major Billing Relationship, filing-owner,
  delivery-contact, invoice, and data-preservation workflows
- This is a controlled pilot/test release — not final production
- GitHub Release publication follows the final documentation-embedded rebuild

### Billing Relationship Separation

The Billing Relationship editor now separates four distinct concepts:

1. **Save invoices under** (filing owner) — can choose connected
   organization/payer/covered clients, search the active people directory,
   or create a new filing person
2. **Send invoice to** (delivery contact) — can search the active people
   directory or create a new delivery contact
3. **Bill To** — the payer organization or person responsible for payment
4. **Participants / covered clients** — the session attendees and covered
   people

Filing owner and delivery contact remain separate from payer, Bill To,
Participants, and covered clients.

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

### Launcher Build Notes

Launcher builds are not byte-reproducible because the Mach-O UUID and ad-hoc
code-signature metadata change on each compilation. The release intentionally
contains the newly rebuilt launcher. The repository launcher was restored to
the tracked verified binary after the release build. This is expected and does
not indicate corruption or obsolescence.

### Feature Highlights in test.6

- "Save invoices under" supports any active existing person from the full
  people directory, inline creation of a new filing person, and storage as
  `default_filing_owner_kind = "person"` + `default_filing_owner_record_id = person_id`
- Complete separation from payer, Bill To, Participants, covered clients, and
  Send invoice to / delivery contact
- Organization-first default with payer fallback
- Future-draft inheritance
- Finalized-invoice immutability preserved

### Acceptance Checklist Results

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
11. **Publish only the exact verified DMG after brooketest passes** — pending final rebuild

## Previous Test Build — v0.1.0-test.5

The test.5 build was the prior test release. Historical references to test.5
remain accurate for the period in which test.5 was the current build. test.6
supersedes test.5 as the current test build.

## General Test Release Information

- Required Mac: Apple Silicon.
- Required Python runtime: see `release_manifest.json` for the exact required major/minor family for this artifact.
- After download, installation uses the shipped wheelhouse and can run offline.
- Normal daily launch does not require Git, GitHub, PyPI, pip, or a source checkout.
- No private configuration, database, credentials, invoices, receipts, reports, logs, or client data are included.
- The native setup app writes private configuration on first install and preserves it on reinstall.
- The spare-Mac test should use an explicitly initialized disposable database.
- Gatekeeper may require right-click Open or Security & Privacy approval.
- Verify the `.dmg` checksum before installing.
- Follow `docs/TEST_MAC_ACCEPTANCE.md`.

Do not install this test release on Jordana's production Mac yet.
