# Current Implementation Status And Handoff

This document supersedes older uploaded handoffs. Newer repository code, schema,
migrations, tests, and explicit decisions remain authoritative.

- **Latest code commit reviewed:** `d99a42263cd48b0c454b1de7fdc5dd01db02ee5a`
- **Latest recorded full-suite verification commit:** `033d2634fa33688f686c66160ec0eff3e71bf8d7`
- **Documentation reconciliation date:** 2026-07-02
- **Migration head:** `017_relationship_filing_owner_target`
- **Latest recorded full-suite baseline:** 2,612 passing, 11 skipped, 0 failures (`2623` tests run)

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

Latest recorded full suite:

```text
Ran 2623 tests in 180.357s
OK (skipped=11)
```

Exact counts: 2,612 passing, 11 skipped, 0 failures.

This baseline includes focused tests for Bill To delivery resolution, stale-draft
refresh, insurance/coding block layout spacing, and render-model delivery fallback.

```bash
PYTHONPATH=app .venv/bin/python -m unittest discover -s tests
scripts/run_acceptance_test.sh
scripts/git_safety_check.sh
scripts/privacy_check.sh
```


## Installer Acceptance Status

The one-click installer has been manually demonstrated successfully on a test
Mac. The full acceptance record is still incomplete. Before production handoff,
record the release filename and commit, checksum, Mac and macOS version, Python
version, Gatekeeper behavior, restart, duplicate launch, port-conflict tests,
reinstall preservation, and the operational smoke path in
`docs/TEST_MAC_ACCEPTANCE.md`.

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

1. Fix finalization transaction ownership and add rollback coverage.
2. Make installer replacement rollback-safe and manifest-version driven.
3. Finish and record the clean-Mac acceptance checklist.
4. Run Jordana's complete smoke path: launch, sync, review, approve, preview,
   finalize, open canonical PDF, record payment, restart, and reopen records.
5. Confirm rate exceptions and Bill To defaults with Jordana.
6. Treat historical backfill, dashboard, credits, reconciliation, and
   month-close as later enhancements rather than blockers to the core handoff.

### Completed in this round

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
