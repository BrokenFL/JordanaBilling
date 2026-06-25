# Alias Learning

Aliases are learned after Jordana confirms a participant or approves a session or exclusion. Parser suggestions do not create permanent aliases.

## Participant Confirmation (Save Client(s))

When Jordana saves a single confirmed participant via Save Client(s), the system immediately upserts a `calendar_aliases` row using:

- raw calendar title and stripped shorthand
- normalized alias text
- confirmed person UUID
- `approved_by_user = 1`

This enables future events with the same shorthand to auto-resolve via smart prefill without waiting for full session approval.

The following safety rules apply:

- Only a single confirmed participant triggers alias learning; multi-person titles are skipped.
- Aliases are not learned from titles containing ambiguous multi-person tokens (`+`, `&`, `and`, `,`, `/`, `\`, `;`, `for`).
- An alias already approved for another active person is never overwritten; the conflicting alias is silently skipped.
- Repeated saves are idempotent — the same alias row is updated, not duplicated.
- Raw calendar evidence is never modified.

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
