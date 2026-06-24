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
- `needs_payment_status`
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
6. Payment status
7. Approve

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

Parser-derived names may appear in Clients in this session as proposed clients before anything has been saved. Showing a proposed client does not create a permanent person, approve the session, or change raw calendar evidence. When Jordana clicks Save Client(s), the matcher checks exact normalized case-and-whitespace active client names first, then exact normalized approved calendar aliases. Only exactly one active client auto-links; ambiguous or missing matches remain proposed for manual choice. A new permanent person/client is created only when the confirmed client name has a usable first and last name; incomplete or ambiguous names remain reviewable session participant text until completed.

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
PYTHONPATH=app python -m jordana_invoice --db data/jordana_invoice.sqlite3 record-review --candidate-id CANDIDATE_ID --status needs_rate --reason "Waiting for Jordana rate confirmation"
```

Future UI work should call the same service layer instead of writing CSV edits.

## Local Review UI

The first functional UI is available at `/review` through:

```bash
PYTHONPATH=app python -m jordana_invoice --db data/jordana_invoice.sqlite3 serve-review
```

It supports save without approval, approval validation, proposed client editing before confirmation, billing relationship maintenance, and structured audit records.

The Clients record view is the durable place to review client details, bill-to links, recent sessions, person-specific rate overrides, and approved calendar aliases. The inline session participant editor remains intentionally simple.

## Section-Level Saves

The routine inspector is now guided in this order:

1. Clients in this session
2. Bill to
3. Session Details
4. Final Approve Session

Later steps stay locked until the earlier step is established. When the backend can already confirm a step from saved clients, saved payer setup, or an exact matching rate rule, the UI collapses that step into a compact confirmed summary until Jordana chooses Change.

Confirm Client(s), Save Bill To, and Save Session Draft remain independent. None of them approves a session. After section saves, the backend refresh service recomputes payer, rate, unresolved fields, checklist state, review status, and the separate review-authority score used for guided review.

Removing all clients and saving clears the session participants. The parser proposal is not reinserted after that explicit save.

When a relationship save refreshes suggestions, the browser preserves unsaved session draft fields so Jordana can resolve identity first without losing rate or payment edits.

Shared billing and relationships are still available, but ordinary review no longer exposes inline relationship-role editing. That deeper work stays in the Billing Relationships workflow and record view.

Calendar evidence remains read-only under View Calendar Evidence.

## Reparse Unapproved Candidates

Historical unapproved candidates can be reparsed through `POST /api/review/reparse-candidates`. This re-runs the parser on all candidates whose review status is not `approved` or `excluded`, updates parsed fields, and writes audit entries. Raw snapshots are never modified. Approved and excluded candidates are skipped.

## Candidate Promotion to Review

Candidate-only records (calendar candidates with no session yet) can be manually promoted into the review queue through `POST /api/review/candidates/{id}/send-to-review`. Promotion re-parses the preserved raw snapshot, forces classification to `client_session`, and creates one reviewable session.

Promotion is duplicate-protected: if a session already exists for the candidate, the API rejects the request. Approved and excluded candidates are also rejected. The action is audited and preserves raw evidence.

## Sessions Page Actions

The Sessions page shows a **Send to Review** button only for candidate-only rows with `review_status == needs_classification`. After successful promotion, the row refreshes and the candidate appears in the Review Queue.

The existing **Return to Review** button remains for excluded sessions, restoring them to `needs_classification` for re-review.

Calendar evidence remains read-only under View Calendar Evidence.
## Calendar, Status, and Billing Treatment

Routine review remains a confirmation form: Clients in this session, Bill to, Duration, Session type, Time category, Suggested/editable rate, Payment status, and Approve.

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

Approval does not create an invoice. The invoice builder revalidates approval, clients in the session, bill-to, actual charged amount, appointment status, billing treatment, billable classification, raw evidence, and duplicate attachment. Cancelled/no-show sessions require explicit `billable` treatment.
