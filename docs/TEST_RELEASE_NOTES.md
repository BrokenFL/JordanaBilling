# Jordana Billing v0.1.0 Test Release

This is a private clean-Mac test release for Brooke's spare Mac. It is a pre-release and is not approved for Jordana's production Mac.

## Current Pending Acceptance Build — v0.1.0-test.6

- **Release label:** v0.1.0-test.6
- **DMG:** `JordanaBilling-v0.1.0-test.6-6c3dbab028ac-macos-arm64.dmg`
- **Manifest commit:** `6c3dbab028acb4b44184b720d5160927d6d3d6c6`
- **application_version:** 0.1.0
- **source_tree_dirty:** false
- **builder Python:** 3.11.11
- **architecture:** arm64
- **DMG checksum verification:** passed
- **Private-file scan:** no `.env`, SQLite, or PDF files found
- **Wheelhouse includes:** `jordana_invoice-0.1.0`, `reportlab 4.5.1`, `pillow 12.2.0`, `charset-normalizer 3.4.7`
- **Stale build artifacts removed** after wheel creation
- **DMG and checksum copied to `/Users/Shared`** and verified there

### Status

- test.6 has been built and copied to `/Users/Shared`
- brooketest upgrade/data-preservation installation has **not** yet been run
- full clean-account acceptance has **not** yet been run
- GitHub Release has **not** yet been published
- Do not claim test.6 is accepted, production-ready, or published

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

### Pending Acceptance Checklist

1. Install test.6 over existing brooketest installation
2. Verify private configuration and DB preservation
3. Verify release label and manifest
4. Test arbitrary existing filing person
5. Test inline-created filing person
6. Verify persistence after close/reopen
7. Verify no accidental payer/Bill To/Participant/covered-client/delivery-contact linkage
8. Verify future draft inheritance
9. Verify finalized invoice immutability
10. Run clean-account acceptance
11. Publish only the exact verified DMG after brooketest passes

## Previous Test Build — v0.1.0-test.5

The test.5 build was the prior test release. Historical references to test.5
remain accurate for the period in which test.5 was the current build. test.6
supersedes test.5 as the current pending acceptance build.

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
