# Review UI Spec

## Invoice Workspace

`/invoices` keeps the dense desktop shell and adds one status-filtered invoice list with a focused builder/preview pane. Eligibility and totals come from the backend. Draft descriptions and delivery may be edited; finalization requires explicit confirmation. Finalized records allow history and void-with-reason, not silent editing.

Approved mockup is stored locally at `docs/review-ui-approved-mockup.png` when available. It is intentionally ignored by Git because screenshots may contain client-style data.

## Implemented Route

```text
/review
```

Run locally:

```bash
PYTHONPATH=app python -m jordana_invoice --db data/jordana_invoice.sqlite3 serve-review
```

Then open:

```text
http://127.0.0.1:8765/review
```

## Implemented Layout

- Fixed dark left navigation
- Compact top status bar
- Dense review list with one session per row
- Main list uses most of the window
- Right-side editable inspector
- Selecting a row updates the inspector in place
- Routine review does not require modals

## Implemented Editing

The routine inspector supports:

- Clients in this session search/add/reuse/correction/merge
- Bill-to search/create/reuse
- duration edits
- service mode edits
- time category edits
- suggested/editable rate edits with a visible explanation
- rate-change scope: this session only, future sessions for one participant, or future joint sessions for the exact participant set
- payment status edits
- save without approval
- approval with validation
- personal/admin/nonbillable/duplicate marking
- collapsed Advanced relationships and shared billing controls for billing relationship members, roles, default payer, shared-rate setup, and opening billing relationship records
- functional Rate Card section
- independent Save Client(s), Save Bill To, and Save Session Draft buttons
- Billing Relationships list and relationship record
- Clients list and client record
- return link from CRM record back to the originating review item

Routine review should not display backend family-account setup, household names, relationship roles, or billing relationship membership as required fields.

## Future UI Work

- richer account membership editor
- billing address inline subform
- explicit previous/next controls
- audit history drawer
- duplicate-link picker
- deeper rate-rule editing and deactivation controls

The next build should keep this dense list plus inspector architecture.
## Calendar Review UI Additions

Keep the dense desktop review layout. Do not turn the screen into calendar administration.

Additions:

- demo banner only when SQLite `app_metadata.demo_mode=true`
- source-calendar filter
- source calendar and preferred-work indicator
- calendar-disposition indicator
- appointment-status badge
- title-time discrepancy warning with Calendar start preserved
- cancellation/no-show billing-treatment control

Backend billing relationship controls remain collapsed under Advanced relationships and shared billing.
