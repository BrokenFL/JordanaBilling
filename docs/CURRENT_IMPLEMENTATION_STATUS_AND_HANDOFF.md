# Current Implementation Status And Handoff

This document supersedes older uploaded handoffs. Newer repository code, schema,
migrations, tests, and explicit decisions remain authoritative.

- **Latest code commit reviewed:** 6c3dbab — Allow people directory filing owners in billing relationships
- **Latest recorded full-suite verification commit:** 6c3dbab
- **Documentation reconciliation date:** 2026-07-02
- **Migration head:** `017_relationship_filing_owner_target`
- **Latest recorded full-suite baseline:** 2,721 passing, 11 skipped, 0 failures

## Architecture

Apple Calendar → iPhone Shortcut → Google Apps Script → Google Sheets raw
staging/audit → Python sync/import → local SQLite → review UI → approved
sessions → invoice preview/finalization → payment tracking.

- Google Sheets preserves source evidence.
- SQLite is the operational database.
- Reports, invoices, receipts, and PDFs are derived outputs.
- Raw calendar evidence and approved historical values are never silently rewritten.

## Current Implemented Scope

### Calendar and Review

- Authenticated Apps Script sync with full/incremental cursor behavior
- Raw snapshot preservation and duplicate event-version collapse
- Conservative parser and source-calendar classification
- Section-level saves for Participants, Bill To, and Session Draft
- Candidate-to-session promotion with idempotent repeated saves
- Approval single-submit protection, overlay cleanup, focus restoration, and success-with-warning behavior
- Billing relationship wizard, canonical payer records, duplicate prevention, deactivation/reactivation, and audited normalization
- Duplicate resolution using **Confirm Duplicate & Next**

### Rates

- Effective-dated global, person, exact participant-combination, and billing-relationship rules
- Session-only and future-rule scopes
- Approved session rates remain frozen

### Invoices

- Monthly draft staging by Bill To and billing month
- Draft line editing with optimistic revision locking
- Two-step finalization with transaction-safe numbering and immutable snapshots
- Filing-owner selection and client/month PDF folders
- Filing owner supports organization, payer person, covered client, and arbitrary active person targets with stable `kind` + `record_id` contract
- Draft invoice filing owner override via `filing_owner_kind` + `filing_owner_record_id`; legacy `person_id` remains backward compatible
- Draft override does not mutate relationship default; finalized snapshots remain immutable
- Prior unpaid balance and account-summary snapshots
- Optional invoice-specific insurance coding
- Void and reissue under a new number
- Searchable invoice library

### Canonical PDF Behavior

- Draft and finalized PDFs use one shared `_generate_invoice_pdf_bytes` renderer
- Draft preview is in-memory, clearly marked DRAFT, and side-effect free
- Review & Finalize embeds the canonical draft PDF preview before confirmation; the old duplicated HTML invoice card is not the approval visual
- Finalized PDFs are immutable and stored locally
- Draft and final endpoints use Safari-compatible inline PDF headers
- Commit `d99a42263cd48b0c454b1de7fdc5dd01db02ee5a` fixes the post-finalize workflow so the UI opens the canonical stored PDF rather than leaving the user on the older in-app HTML card
- Finalized invoice records expose a versioned `final_pdf_url`
- Final PDF responses use no-cache headers
- Repeated finalize submissions return the existing finalized invoice and PDF without regenerating or renumbering
- Release builds clear stale `build/lib`, `build/bdist.*`, and `build/temp.*` output before wheel creation

### Payments

- Payment ledger and allocations
- Paid-at-session approval workflow with idempotent payment creation/allocation
- Apply available funds, reversals, voids, and correction history
- Manual immutable payment receipts
- Outstanding, Paid, and All Payments views
- Shared financial summary calculations
- Read-only historical paid-at-session analyzer and CLI

### Packaging

- Native no-Terminal setup app
- Versioned DMG with checksum, embedded release payload, pinned offline wheelhouse, and release manifest
- Private runtime inside the installed app
- Private config and SQLite data under Application Support
- Reports, invoices, and receipts under Documents
- Daily launch does not run Git, pip, PyPI, dependency repair, or blank-database creation
- Port ownership and database integrity checks
- Brooke reports a successful one-click test installation and launch

## Current Audit Findings Requiring Narrow Follow-Up

1. **Finalization transaction ownership**
   `finalize_invoice()` starts an immediate transaction and calls
   `synchronize_draft_delivery_method(commit=False)`, which no longer commits
   internally. The sync now operates within the finalization transaction.
   A full regression test proving a failed finalization leaves the draft
   unchanged remains a follow-up item.

2. **Installer app-bundle rollback**
   The installer stages the replacement safely but removes the existing app
   before final verification. Private data remains safe, but a verification
   failure can leave the prior working app unavailable. Preserve the old app
   until the replacement passes verification and restore it on failure.

3. **Installer version source**
   The installer currently installs `jordana-invoice==0.1.0` directly. This
   matches the current project version but can drift on a future version bump.
   Read the expected version from `release_manifest.json`.

4. **Remote CI**
   The reviewed commits have no GitHub status checks. Local tests remain the
   source of truth; sanitized CI would reduce the risk of an untested push.

## Test Status

Latest recorded full suite at commit 6c3dbab:

```text
2,721 tests, 0 failures, 11 skipped
```

This baseline includes focused tests for Bill To delivery resolution, stale-draft
refresh, insurance/coding block layout spacing, render-model delivery fallback,
and people-directory filing-owner selection with inline person creation.

```bash
PYTHONPATH=app .venv/bin/python -m unittest discover -s tests
scripts/run_acceptance_test.sh
scripts/git_safety_check.sh
scripts/privacy_check.sh
```


## Current Release Build

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

### Launcher Build Notes

The tracked verified launcher binary hash is
`05288036d84eec8d635afd507af523949f8abb1af33e66b49a262e5abb51f154`. The
official build script recompiles the launcher and produces
`55b76bfc5e10a11b8311916089d0ef54b918d806705371ee9d5c9e14b7f7c7b5`. The
difference is limited to Mach-O UUID and ad-hoc code-signature hash metadata.
Both launchers use `Identifier=com.jordana.billing.launcher`,
`Signature=adhoc`, `TeamIdentifier=not set`. The release intentionally contains
the newly rebuilt launcher. The repository launcher was restored to the tracked
verified binary after the release build.

Launcher builds are not byte-reproducible because the Mach-O UUID and ad-hoc
code-signature metadata change on each compilation. This is expected and does
not indicate corruption or obsolescence.

### Fresh Test Database Work

On the source checkout, a fresh test database was prepared:

- app stopped, no DB process, port 8765 free
- SQLite backup created outside repo; `integrity_check` returned `ok`
- old DB and WAL/SHM moved outside repo
- fresh DB initialized and migrated; `integrity_check` returned `ok`
- bootstrap repaired the editable package installation
- source app launched healthy
- calendar sync may now populate the fresh test database
- Rebuild Calendar from Data Sheet is not a database wipe

### Pending Acceptance

- test.6 has been built and copied to `/Users/Shared`
- brooketest upgrade/data-preservation installation has **not** yet been run
- full clean-account acceptance has **not** yet been run
- GitHub Release has **not** yet been published
- Do not claim test.6 is accepted, production-ready, or published

## Installer Acceptance Status

The one-click installer has been manually demonstrated successfully on a test
Mac. The full acceptance record is still incomplete. Before production handoff,
record the release filename and commit, checksum, Mac and macOS version, Python
version, Gatekeeper behavior, restart, duplicate launch, port-conflict tests,
reinstall preservation, and the operational smoke path in
`docs/TEST_MAC_ACCEPTANCE.md`.

### Pending Acceptance Checklist for v0.1.0-test.6

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

## Privacy Rules

Never commit live databases, raw calendar exports, private spreadsheets,
invoices, receipts, credentials, `.env`, logs containing names, screenshots,
backups, or real diagnosis codes. Do not store clinical notes, psychotherapy
notes, symptoms, treatment plans, session-content notes, or clinical
interpretations. Structured insurance diagnosis codes are permitted only when
Jordana explicitly enters or approves the minimum necessary billing value for a
specific invoice; they must never be inferred or committed.

## Known Product Limitations

- No invoice email/mail delivery and tracking
- No legacy paid-at-session apply mode
- No credits, refunds, write-offs, automated multi-invoice allocation, formal reconciliation, or month-close workflow
- No polished production dashboard
- No notarized installer
- Matching Python major/minor runtime required for V1 installation
- Full clean-Mac acceptance evidence not yet recorded
- No permanent billing-relationship deletion by design
- No formal client-versus-non-client schema distinction
- No automatic payer classification

## Immediate Next Steps

1. Complete the v0.1.0-test.6 pending acceptance checklist above.
2. Fix finalization transaction ownership and add rollback coverage.
3. Make installer replacement rollback-safe and manifest-version driven.
4. Finish and record the clean-Mac acceptance checklist.
5. Run Jordana's complete smoke path: launch, sync, review, approve, preview,
   finalize, open canonical PDF, record payment, restart, and reopen records.
6. Confirm rate exceptions and Bill To defaults with Jordana.
7. Treat historical backfill, dashboard, credits, reconciliation, and
   month-close as later enhancements rather than blockers to the core handoff.

### Completed in this round

- "Save invoices under" now supports any active existing person from the full
  people directory, inline creation of a new filing person, and storage as
  `default_filing_owner_kind = "person"` + `default_filing_owner_record_id = person_id`.
  The feature has complete separation from payer, Bill To, Participants,
  covered clients, and Send invoice to / delivery contact. Organization-first
  default and payer fallback are implemented. Future-draft inheritance is
  implemented. Finalized-invoice immutability is preserved. Joint participant
  rate exceptions use person UUIDs, not display names.
- Bill To delivery/contact information now correctly reaches the invoice PDF.
  Root cause: `build_invoice_render_model` treated `"unresolved"` as a valid
  delivery method, preventing fallback to the billing party's
  `preferred_delivery_method`. Additionally, `synchronize_draft_delivery_method`
  only ran during finalization, not during `get_invoice`/preview/readiness checks.
  Fix: render model now treats `"unresolved"`/blank as falsy and falls back to
  the active billing party's preference; `get_invoice` auto-syncs stale delivery
  on drafts; `finalize_invoice` calls sync with `commit=False` inside its
  existing transaction.
- Insurance/coding block spacer changed from a fixed `0.14 * 72` (~10pt) to
  `4 * BODY_LEADING` (~46pt), placing it approximately four body-text lines
  below the final payment-information line as specified.
- Organization payer becomes available as Bill To after saving a billing
  relationship. Root cause: `get_review_candidate` did not expose eligible
  Bill To options from saved billing relationships, and
  `refresh_candidate_suggestions` did not auto-link sessions to the matching
  relationship default. Fix: `eligible_bill_to_options` queries active
  client-account/billing-party rows for confirmed participants and returns
  them as `bill_to_options` in the candidate payload; `billToClientOptions` in
  the UI renders `party:`-prefixed option values; `saveBillingSection` accepts
  `billing_party_id` directly. `refresh_candidate_suggestions` now calls
  `default_relationship_for_participants` to auto-assign account/billing-party
  only when the session has neither and is not approved.
- Invoice delivery contact can be created or selected from the billing
  relationship editor. The editor has two visibly separate sections: "Save
  invoices under" (filing owner only — payer, organization, covered
  clients, or an explicitly selected active person from the people directory)
  and "Billing delivery" (Send invoice to). The Save invoices under section
  includes a "Find existing person" search and an "Add filing person" form
  that reuses the existing `create_person` duplicate safeguard. The delivery section
  includes a "Find existing person" search that queries the full active
  people directory via `/api/people?q=...` (unrelated people are allowed
  here), and an "Add invoice contact" form with first name, last name,
  display name, email, phone, and address fields. The existing
  `create_person` duplicate safeguard is reused. The delivery contact is
  linked via `delivery_contact_person_id` (canonical for both organization
  and person payers) and does not become a covered client, participant,
  payer, or Bill To. For organization payers, `person_id` stores the
  delivery contact historically. Delivery method, email, phone, and
  address persist on the billing party. Finalized invoices remain
  immutable; changing the delivery contact affects future/unresolved
  drafts only. No schema migration was required.
- Waived late cancellation with $0.00 rate now persists end-to-end. Root cause:
  `centString` and `firstPresent` used truthiness checks that treated `0` as
  falsy, causing the saved zero to disappear on reload; `unresolved_from_values`
  and `review_readiness` rejected any zero rate regardless of treatment. Fix:
  `centString` uses explicit null/undefined/empty-string checks; `firstPresent`
  returns the first non-null/non-undefined/non-empty value; readiness and
  unresolved logic exempt `waived`/`not_billable` treatment with zero rate for
  any cancellation outcome. The JS auto-sets the rate to `$0.00` when
  `billingTreatment === "waived"` and `attendanceOutcome === "late_cancellation"`.
  Invoice staging and finalization preserve the $0.00 line.
