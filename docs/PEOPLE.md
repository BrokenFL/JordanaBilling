# People

People are actual humans, not calendar shorthand. Parser candidates do not become permanent people until Jordana or the reviewer confirms them.

Names are stored as:

- `first_name`
- `last_name`
- `preferred_name`
- `display_name`

The People list sorts by last name and shows person code, last name, first name, display name, accounts, billing relationships, last session, and active status.

Person records show contact information, accounts, relationship roles, sessions, billing relationships, aliases, administrative notes, merge status, active status, actual charged-rate history, and active future rate exceptions.

Session history uses approved session values. It must not reconstruct historical charges from current rate rules.

Active rate exceptions shown on a person record include person-specific exceptions and shared/joint exceptions involving that person. Joint exceptions should show the actual participant names, not a fabricated household name.

Do not store clinical notes. Administrative notes should stay limited to billing and operational details such as preferred email, payer instructions, check payment, or unpaid balance context.

Finalized invoice participant names are immutable line snapshots. Correcting a current person name does not rewrite old invoices.
