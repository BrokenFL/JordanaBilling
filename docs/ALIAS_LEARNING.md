# Alias Learning

Aliases are learned only after Jordana approves a session or exclusion. Parser suggestions do not create permanent aliases.

## Client Session Approval

When a session is approved, the system saves a `calendar_aliases` row using:

- raw calendar title
- normalized alias text
- approved account
- approved primary person when available
- approved service mode
- `approved_by_user = 1`

Future matching records can use this approved alias as a high-confidence suggestion.

Smart prefill may connect participant, account, and default billing party from an approved alias. It still does not approve the session automatically.

## Multi-Person Titles

Titles such as `Bobsey and Fred 6` must not create one mashed-together person. Approval should connect:

- Fred Smith person
- Bobsey Smith person
- Fred Household account
- Fred Smith billing party
- one session with two participants

## Exclusions

Personal/admin/nonbillable aliases should also be learned only after review. Raw snapshots remain preserved.
