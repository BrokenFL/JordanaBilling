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

## Routine Confirmation Model

Routine review uses Jordana's normal mental model:

1. Participants
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

Participants are people connected to one session. The bill-to party is the person or organization responsible for receiving and paying the invoice, and does not have to be a participant. A separate client/account field is not required for routine approval.

## Relationship Review

Titles with multiple names or relationship phrases stay reviewable.

Examples:

- `Bobsey and Fred 6`
- `Fred + Bobsey | 60 | Office`
- `Caitlin Schneider 530 for Sage`

The system must not create a new permanent flat client or visible household account from a combined title. Review should decide whether each name is a participant, bill-to party, parent, child, spouse, family member, unrelated note, or unknown.

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

It supports save without approval, approval validation, inline person/account/billing-party creation, and structured audit records.

## Section-Level Saves

The routine inspector order is:

1. Participants
2. Bill to
3. Session Details
4. Advanced relationships and shared billing
5. Review Checklist
6. Session Actions

Save Participants, Save Bill To, and Save Session Draft are independent. None of them approves a session. After section saves, the backend refresh service recomputes payer, rate, unresolved fields, checklist state, and review status.

When a relationship save refreshes suggestions, the browser preserves unsaved session draft fields so Jordana can resolve identity first without losing rate or payment edits.

Calendar evidence remains read-only under View Calendar Evidence.
## Calendar, Status, and Billing Treatment

Routine review remains a confirmation form: Participants, Bill to, Duration, Session type, Time category, Suggested/editable rate, Payment status, and Approve.

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

Approval does not create an invoice. The invoice builder revalidates approval, participants, bill-to, actual charged amount, appointment status, billing treatment, billable classification, raw evidence, and duplicate attachment. Cancelled/no-show sessions require explicit `billable` treatment.
