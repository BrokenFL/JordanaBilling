# Review UI Spec

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

- Participants search/create/reuse/correction/merge
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
- collapsed Advanced relationships and shared billing controls for account members, roles, default payer, shared-rate setup, and opening account records
- functional Rate Card section
- independent Save Participants, Save Bill To, and Save Session Draft buttons
- Clients & Accounts list and account record
- People list and person record
- return link from CRM record back to the originating review item

Routine review should not display Client / Family Account, Primary Client, Household Name, Account Name, Relationship Role, or Account Membership as required fields.

## Future UI Work

- richer account membership editor
- billing address inline subform
- explicit previous/next controls
- audit history drawer
- duplicate-link picker
- deeper rate-rule editing and deactivation controls

The next build should keep this dense list plus inspector architecture.
