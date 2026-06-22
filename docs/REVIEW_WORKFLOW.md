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

## Relationship Review

Titles with multiple names or relationship phrases stay reviewable.

Examples:

- `Bobsey and Fred 6`
- `Fred + Bobsey | 60 | Office`
- `Caitlin Schneider 530 for Sage`

The system must not create a new permanent flat client from a combined title. Review should decide whether the extra name is a participant, billing party, parent, child, spouse, family member, unrelated note, or unknown.

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

The inspector order is:

1. Calendar Evidence
2. People and Relationship
3. Session Details
4. Review Checklist
5. Session Actions

Save Person, Save Relationship, Save Billing Details, and Save Session Draft are independent. None of them approves a session. After section saves, the backend refresh service recomputes payer, rate, unresolved fields, checklist state, and review status.

When a relationship save refreshes suggestions, the browser preserves unsaved session draft fields so Jordana can resolve identity first without losing rate or payment edits.
