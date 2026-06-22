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

The inspector supports:

- participant search/create/reuse/correction/merge
- account search/create/reuse
- billing-party search/create/reuse
- duration edits
- service mode edits
- time category edits
- suggested and approved rate edits
- payment status edits
- save without approval
- approval with validation
- personal/admin/nonbillable/duplicate marking
- relationship editor for account members, roles, primary participant, and default payer
- functional Rate Card section

## Future UI Work

- richer account membership editor
- billing address inline subform
- explicit previous/next controls
- audit history drawer
- duplicate-link picker
- rate-rule picker with rule explanations
- full Clients & Accounts, People, and Rate Card pages

The next build should keep this dense list plus inspector architecture.
