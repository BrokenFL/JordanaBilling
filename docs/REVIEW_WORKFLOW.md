# Review Workflow

Calendar data is evidence. Review decisions are stored in SQLite and are intended to become the backing logic for the future dashboard.

## Review Statuses

- `needs_classification`
- `needs_person_match`
- `needs_account`
- `needs_participants`
- `needs_billing_party`
- `needs_duration`
- `needs_service_mode`
- `needs_rate`
- `ready_for_approval`
- `approved`
- `excluded`

Each candidate can have multiple unresolved fields. Those fields are stored as structured JSON in `calendar_event_candidates`, `review_queue`, and `review_items`.

### Review Queue Default and Status Filter

The Review Queue dropdown offers exactly three options:

1. **Needs Review** (default) — every status except `approved` and `excluded`
2. **Approved** — only `approved` sessions
3. **Excluded** — only `excluded` sessions

The default filter excludes both approved and excluded sessions from the API query, so approved sessions never appear in the Needs Review list. The backend total and item count are authoritative; no client-side filtering is needed.

### Approved Authority Score

When a session's `review_status` is `approved`, the review-authority score is forced to 100, reflecting full human confirmation. This overrides the title/calendar time-mismatch cap of 75. The original parser confidence field on `calendar_event_candidates` is never modified by approval or auto-linking.

### Exact-Name Auto-Link (apply_smart_prefill)

When `apply_smart_prefill` runs (during list/detail/dashboard calls), it checks `session_participants` rows with `person_id IS NULL` on unapproved sessions. For each:

- The participant name is normalized using the existing case-insensitive, whitespace-collapsing helpers.
- The system searches active permanent people records for an exact normalized identity match.
- If exactly one match exists, the participant row's `person_id` is set. No person is created.
- If zero or multiple matches exist, the participant remains unresolved for manual review.
- An `automatic_exact_name_match` audit entry is recorded.

Partial, fuzzy, and joint-session names do not auto-link. Ambiguous matches remain for manual confirmation.

### Payer Auto-Assignment Priority

After a participant is auto-linked, the billing party is assigned only when the session has no existing payer:

1. **Existing session payer** — preserved, never overwritten
2. **Account default payer** — if the session has an `account_id` with a `default_billing_party_id`
3. **One unique active person billing party** — if the linked person has exactly one active billing party
4. **Unresolved** — if the person has zero or multiple active billing parties, Bill to remains unresolved

An `automatic_billing_party_assigned` audit entry is recorded for any automatically assigned payer. No billing party is ever created by auto-link.

## Routine Confirmation Model

Routine review uses Jordana's normal mental model:

1. Clients in this session
2. Bill to
3. Session Type (exactly 5 choices)
4. Duration (exactly 5 choices)
5. Suggested/editable rate
6. Approve

Payment handling is set in the **Additional Information** section. The label is now **Payment Handling** with two options:

- **Invoice billing** (default) — session remains eligible for invoicing
- **Paid at session** — session is excluded from invoicing

### Session Type Choices

The system offers exactly **5 Session Type choices**:

1. **Psychotherapy Session** — Standard weekday daytime session
2. **Psychotherapy Session / House Call** — Explicit house call or location-based
3. **Psychotherapy Session / Weekend** — Saturday or Sunday
4. **Psychotherapy Session / Evening** — Weekday starting at 8:00 PM or later
5. **Custom** — Manual override with custom description

**No other session type may ever appear in active UI controls.** Office, Phone, FaceTime are appointment methods (internal evidence), not billing session types.

### Duration Choices

The system offers exactly **5 Duration choices**:

1. **30 minutes**
2. **60 minutes**
3. **90 minutes**
4. **120 minutes**
5. **Custom** — Requires actual minutes input

When no duration is parsed from the calendar title, the system suggests 60 minutes.

### Session Type Priority

Automatic derivation uses this priority:

1. Custom (manual only)
2. House Call (explicit text or nonblank location)
3. Weekend (Saturday or Sunday)
4. Evening (weekday >= 8:00 PM)
5. Standard Psychotherapy Session

House Call overrides Weekend and Evening. Weekend overrides Evening.

Clients in this session are permanent human client records connected to one session. The bill-to party is the person or organization responsible for receiving and paying the invoice, and does not have to be an attending client. A separate billing relationship is not required for a simple self-paying client.

Parser-derived names may appear in Clients in this session as proposed clients before anything has been saved. Showing a proposed client does not create a permanent person, approve the session, or change raw calendar evidence. When Jordana clicks Save Client(s), the matcher checks exact normalized case-and-whitespace active client names first, then exact normalized approved calendar aliases. Only exactly one active client auto-links; ambiguous or missing matches remain proposed for manual choice. A new permanent person/client is created only when the confirmed client name has a usable first and last name; incomplete or ambiguous names remain reviewable session participant text until completed. When exactly one participant is confirmed, Save Client(s) also upserts an approved calendar alias so future events with the same shorthand auto-resolve via smart prefill; multi-person titles and aliases already approved for another active person are skipped. See `docs/ALIAS_LEARNING.md` for details.

## Relationship Review

Titles with multiple names or relationship phrases stay reviewable.

Examples:

- `Bobsey and Fred 6`
- `Fred + Bobsey | 60 | Office`
- `Caitlin Schneider 530 for Sage`

The system must not create a new permanent flat client or visible household account from a combined title. Review should decide whether each name is a participant, bill-to party, parent, child, spouse, family member, unrelated note, or unknown.

### Titles ending in `for <reference>`

Titles ending in `for <reference>` (e.g., `Caitlin Schneider 530 for Sage`) preserve the reference as unresolved evidence. The parser does not infer a participant, bill-to, or relationship from the reference name. The reference appears in candidate evidence for manual review only.

## Decisions

The temporary developer command records a review event:

```bash
PYTHONPATH=app .venv/bin/python -m jordana_invoice --db data/jordana_invoice.sqlite3 record-review --candidate-id CANDIDATE_ID --status needs_rate --reason "Waiting for Jordana rate confirmation"
```

Future UI work should call the same service layer instead of writing CSV edits.

## Local Review UI

The first functional UI is available at `/review` through:

```bash
PYTHONPATH=app .venv/bin/python -m jordana_invoice --db data/jordana_invoice.sqlite3 serve-review
```

It supports save without approval, approval validation, proposed client editing before confirmation, billing relationship maintenance, and structured audit records.

The Clients record view is the durable place to review client details, bill-to links, recent sessions, person-specific rate overrides, and approved calendar aliases. The inline session participant editor remains intentionally simple.

The client record now opens as a full-width workspace at `#people/{person_id}` with billing summary cards, billing setup, billing relationships, invoice history, sessions, and rate preferences. See `docs/CLIENTS_AND_ACCOUNTS.md` for details on the client workspace sections.

## Section-Level Saves

The routine inspector is now guided in this order, presented in a focused review overlay that opens when a row is selected or the Review button is clicked:

1. Clients in this session
2. Bill to
3. Session Details
4. Approve Session

The review list is full-width with columns: Status, Date, Time, Clients, Calendar, Duration, Rate, and a Review button. The side inspector has been replaced by the overlay, which supports keyboard navigation (Escape to close, Previous/Save and next/Approve), focus trap, and collapsed Source details.

### Overlay Behavior on Approval

When Jordana clicks **Approve Session**, the overlay closes automatically on success. The candidate is removed from the review list, focus returns to the next review button (or the search box), and a success banner appears briefly. The approve button is disabled during the API request to prevent double-submission. If approval fails, the button is re-enabled and a sanitized error message is shown. If the session is successfully staged, the success banner indicates this. If staging generates warnings, database-busy, or unexpected errors, approval remains successful, and a persistent warning banner is displayed at the top of the workbench page via `showReviewWarning(message)`.

### Overlay Behavior on Duplicate Resolution

When Jordana clicks **Confirm Duplicate & Next**, the overlay closes automatically on success. The candidate is marked as `duplicate` and removed from the review list. The next unresolved candidate opens in the overlay, or if none remain, focus returns to the first review button (or the search box). A "Duplicate resolved" success banner appears briefly. The button is disabled during the API request to prevent double-submission. If the request fails, the overlay stays open, the button is re-enabled, and a sanitized error message is shown. No partial state mutation occurs on failure.

### Overlay Behavior on Billing Relationship Navigation

When Jordana clicks **Change payer or shared billing** or opens a billing relationship record from the overlay, the review overlay closes before navigation. If there are unsaved changes, Jordana is prompted to confirm closing. The return context (candidate ID, session ID, account ID, billing party ID) is preserved so Jordana can return to the same review candidate after editing the billing relationship.

### Billing Relationship Updates and Session Propagation

When a billing relationship's payer or covered clients are updated through the account record editor, the new default billing party is propagated to all non-approved sessions belonging to that account. Approved sessions are never modified — their billing party is frozen at approval time. When Jordana returns to the review candidate after saving a billing relationship, the candidate is refreshed server-side so the new billing party and rate suggestions are immediately available.

Later steps stay locked until the earlier step is established. When the backend can already confirm a step from saved clients, saved payer setup, or an exact matching rate rule, the UI collapses that step into a compact confirmed summary until Jordana chooses Change.

Confirm Client(s), Save Bill To, and Save Session remain independent. None of them approves a session. After section saves, the backend refresh service recomputes payer, rate, unresolved fields, checklist state, review status, and the separate review-authority score used for guided review.

Candidate-only calendar records may appear in the review list when a title needs
manual relationship review before it is safe to treat it as a client session.
Some candidates remain candidate-only until manual review — they have no
`sessions` row until the first section-level save.

The first section-level save for one of those rows promotes the candidate into
exactly one reviewable `sessions` row inside the same backend save flow, then
saves the confirmed clients, Bill To, or session details against that row. The
`session_for_candidate` function checks for an existing session by
`candidate_id`; if none exists, `_ensure_review_session_for_candidate` creates
exactly one. Later section saves and approval reuse the same
`sessions.candidate_id` link and must not create a second session.

Multi-participant and single-participant candidates follow the same
save/approval contract — the candidate-to-session promotion logic is identical
regardless of participant count.

The UI does not show stale "saved" state after a failed save. On save failure,
the overlay stays open, the save button is re-enabled, and a sanitized error
message is shown. On approval failure, the overlay stays open with a sanitized
error. On success, the overlay closes, stale state is cleared, the item is
refreshed or removed from the review list, and double submission is prevented
by disabling the approve button during the API request.

Removing all clients and saving clears the session participants. The parser proposal is not reinserted after that explicit save.

When a relationship save refreshes suggestions, the browser preserves unsaved session draft fields so Jordana can resolve identity first without losing rate or payment edits.

Shared billing and relationships are still available, but ordinary review no longer exposes inline relationship-role editing. That deeper work stays in the Billing Relationships workflow and record view.

Active billing relationships are unique by payer identity plus normalized covered-client UUID set. Covered-client order does not matter, and duplicate cleanup remains an explicit audited follow-up task rather than an automatic rewrite.

Calendar evidence remains read-only under View Calendar Evidence.

## Duplicate Candidate Repair

Calendar candidate identity repair is intentionally conservative. The importer
first evaluates exact event ID, then exact event fingerprint, then exact
structural identity if neither stable identifier resolves uniquely. Structural
identity uses normalized title, start, end, duration, and calendar. No fuzzy
title matching is used for automatic reconciliation. Fully identified rows with
both a new event ID and a new fingerprint are not merged by structure alone.
Rows with one changed stable identifier do not structurally reuse approved
sessions; rows missing stable identifiers can reuse a protected canonical
session when the structural match is unique.

If event ID and fingerprint resolve to different candidates, or structural
identity is ambiguous, the candidate remains in review with an
identity-resolution warning. Existing candidate IDs and candidate keys are not
rewritten.

For existing duplicates, the developer CLI can produce a sanitized dry-run plan:

```bash
PYTHONPATH=app .venv/bin/python -m jordana_invoice --db data/jordana_invoice.sqlite3 duplicate-repair --dry-run
```

Canonical selection prioritizes invoiced records, then approved records, then
the earliest legitimate candidate/session. Apply mode is guarded and may affect
only newly created unapproved duplicates. Approved, invoiced, paid, and raw
snapshot records must not be altered by repair.

Apply and reversal are both idempotent. Applied reconciliations are excluded
from later duplicate discovery, so repeated dry runs and repeated apply calls do
not churn timestamps, audit rows, or state. Reversal requires
`--confirm-reversal REVERSE_DUPLICATE_REPAIR`; it restores only values that were
changed by duplicate repair and only when no later edit has changed those same
fields. Unsafe reversals are refused and left for manual review. Operational
apply/reversal creates and verifies a private SQLite backup immediately before
repair writes.

## Reparse Unapproved Candidates

Historical unapproved candidates can be reparsed through `POST /api/review/reparse-candidates`. This re-runs the parser on all candidates whose review status is not `approved` or `excluded`, updates parsed fields, and writes audit entries. Raw snapshots are never modified. Approved and excluded candidates are skipped.

## Candidate Promotion to Review

Candidate-only records (calendar candidates with no session yet) can be manually promoted into the review queue through `POST /api/review/candidates/{id}/send-to-review`. Promotion re-parses the preserved raw snapshot, forces classification to `client_session`, and creates one reviewable session.

Promotion is duplicate-protected: if a session already exists for the candidate, the API rejects the request. Approved and excluded candidates are also rejected. The action is audited and preserves raw evidence.

## Sessions Page Actions

The Sessions page shows a **Send to Review** button only for candidate-only rows with `review_status == needs_classification`. After successful promotion, the row refreshes and the candidate appears in the Review Queue.

The existing **Return to Review** button remains for excluded sessions, restoring them to `needs_classification` for re-review.

Calendar evidence remains read-only under View Calendar Evidence.

## Frontend HTML Escaping

The review UI (`review.js`) renders dynamic content via `innerHTML` with template literals. All user-controlled values are escaped before interpolation:

- **`escapeHtml(value)`** — converts `&`, `<`, `>`, `"`, `'` to HTML entities. Used for text content and attribute values.
- **`escapeAttr(value)`** — alias for `escapeHtml`; used in `data-*` attributes and `value` attributes for clarity.
- **`fmt(value)`** — the shared formatting helper; calls `escapeHtml` on truthy values, returns `"-"` for falsy.

No raw user-controlled field (client names, calendar titles, account names, billing party names, IDs, etc.) is interpolated into `innerHTML` without passing through `escapeHtml`, `escapeAttr`, or `fmt`. Static-analysis tests in `tests/test_html_escaping.py` guard against regressions.

## Calendar, Status, and Billing Treatment

Routine review remains a confirmation form: Clients in this session, Bill to, Duration, Session type, Suggested/editable rate, Payment status, and Approve. Time category is derived automatically from the session date and start time; it is no longer a selectable field in the review UI.

The review screen now also shows source calendar, calendar disposition, appointment-status badge, Calendar start time, parsed title time, original title, and title-time mismatch warnings.

Cancelled and no-show appointments stay preserved and reviewable. They require a separate billing-treatment decision:

- `billable`
- `not_billable`
- `waived`
- `unresolved`

This decision is not payment status. A no-charge cancelled appointment should be preserved instead of excluded/deleted.

Calendar filters are:

- normal review calendars
- all calendars
- preferred work calendar
- other calendars
- personal/admin calendars
- hidden calendars

Hidden records are recoverable through the intentional hidden-calendar filter.

## Invoice Eligibility Boundary

Approval automatically stages the session into the corresponding monthly draft invoice, creating the draft if it does not yet exist. The invoice builder revalidates approval, clients in the session, bill-to, actual charged amount, appointment status, billing treatment, billable classification, raw evidence, and duplicate attachment. Cancelled/no-show sessions require explicit `billable` treatment.

## Invoice Library

The Invoices view (`#invoices`) provides a searchable, filterable, paginated library of all invoices. The table columns include: Number, Invoice Date, Service Period, Bill To, Participants, Status, Payment, Total, Paid, Balance, and Actions.

**Controls**:
- **Search** — free-text search on invoice number or Bill To name (debounced 300ms)
- **Status filter** — All / Drafts / Finalized / Void
- **Payment status filter** — All / Unpaid / Partially Paid / Paid / Void
- **Bill To filter** — dropdown of all billing parties
- **Date filter** — All / This month / Last month / This quarter / This year / Custom range
- **Pagination** — prev/next buttons with result count

**Draft invoices** show a "Draft PDF" button that opens a side-effect-free inline PDF preview with a DRAFT watermark in a new tab, and a "Print Preview" button that opens a side-effect-free HTML page with a DRAFT watermark.

**Finalized invoices** show "Open PDF" and "Print PDF" buttons that serve the stored final PDF via `/api/invoices/{id}/final-pdf`. The file path is never exposed to the client.

See `docs/INVOICE_LIFECYCLE.md` for full API documentation.

## Payment Corrections

The **All Payments** tab in the Payments workspace now supports correction actions via a payment detail overlay (replacing the former `alert()` display).

**Opening the overlay**: Click a payment row in the All Payments tab. The overlay shows:

- Payment fields (date, method, reference, received from, amount, applied, unapplied, status)
- Void reason and voided-at timestamp (if voided)
- Allocations table with per-row reverse buttons (for active allocations)
- Correction history table (allocation reversals, payment voids, fund applications)
- Apply Available Funds form (when the payment is posted and has unapplied funds)
- Void Payment form (when the payment is posted and all allocations are reversed)

**Reversing an allocation**: Click "Reverse" on an active allocation row. A prompt asks for an administrative reason. The reversal is stored with the reason and timestamp. The overlay refreshes to show the updated state.

**Applying available funds**: Enter an invoice UUID and amount in cents. The funds are allocated across invoice line items deterministically (oldest service date first). The payment and invoice must share the same Bill To party.

**Voiding a payment**: Enter an administrative reason. All allocations must be reversed first. The void reason and timestamp are stored.

All correction actions support an optional `idempotency_key` to prevent duplicate processing of the same request.
