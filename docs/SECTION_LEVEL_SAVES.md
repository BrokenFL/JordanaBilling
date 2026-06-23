# Section-Level Saves

The Review Queue resolves one calendar event. Each section can be saved independently, and no section save approves a session automatically.

## Save Client(s)

Used for one or more clients who attended the session. Saving clients persists attendance immediately, writes audit history, and refreshes bill-to defaults, rate suggestions, unresolved fields, checklist, and review status.

Parser-derived names can appear here as proposed clients without writing to SQLite. Save Client(s) is the confirmation boundary: exact existing-person matches are linked, confirmed complete first-and-last names may create a permanent person/client, and incomplete or ambiguous names remain uncoded session participant text. Person codes are generated only after first and last name are confirmed.

Saving an empty client list clears session attendance and does not recreate the parser proposal.

## Save Billing Relationship

Used for optional advanced account selection, membership roles, default payer, and shared billing. After saving, the backend refreshes account, billing party, confidence, suggested rate, checklist, unresolved fields, and review status.

## Save Bill To

Used for selecting the payer for the current session. The normal Review UI offers confirmed session clients as payer choices by permanent `person_id`; advanced parent, spouse, organization, household, shared default, or special-rate setup belongs in Billing Relationships. Saving Bill To does not approve the session.

## Save Session Draft

Used for duration, session type, time category, suggested/editable rate, rate-change scope, payment status, and billable status. It saves without approval.

## Approval

Approve Session validates clients in the session, bill-to, duration, session type, time category, approved/actual charged rate, and payment status. It saves reusable aliases and relationships, snapshots approved rates, writes audit history, and advances the review state. Approval is separate from every section save.
## Calendar/Status Fields

Section-level saves remain independent:

- Save Client(s)
- Save Bill To
- Save Session Draft
- Approve Session

Saving session draft may update `billing_treatment` for cancelled/no-show appointments. That does not approve the session and does not overwrite raw calendar evidence.

Client or bill-to saves continue to refresh dependent suggestions while preserving unrelated unsaved session fields in the UI.
