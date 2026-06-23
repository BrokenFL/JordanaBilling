# Section-Level Saves

The Review Queue resolves one calendar event. Each section can be saved independently, and no section save approves a session automatically.

## Save Participants

Used for one or more people who attended the session. Saving participants persists immediately, writes audit history, and refreshes bill-to defaults, rate suggestions, unresolved fields, checklist, and review status.

Parser-derived names can appear here as proposed participants without writing to SQLite. Saving Participants is the confirmation boundary: exact existing-person matches are linked, confirmed complete first-and-last names may create a permanent person, and incomplete or ambiguous names remain uncoded session participant text. Person codes are generated only after first and last name are confirmed.

Saving an empty participant list clears participants and does not recreate the parser proposal.

## Save Relationship

Used for optional advanced account selection, membership roles, default payer, and shared billing. After saving, the backend refreshes account, billing party, confidence, suggested rate, checklist, unresolved fields, and review status.

## Save Bill To

Used for billing party, billing name, email, address, phone, default payer, and administrative billing notes. Saving a bill-to contact does not add that contact as a session participant.

## Save Session Draft

Used for duration, session type, time category, suggested/editable rate, rate-change scope, payment status, and billable status. It saves without approval.

## Approval

Approve Session validates participants, bill-to, duration, session type, time category, approved/actual charged rate, and payment status. It saves reusable aliases and relationships, snapshots approved rates, writes audit history, and advances the review state. Approval is separate from every section save.
## Calendar/Status Fields

Section-level saves remain independent:

- Save Participants
- Save Bill To
- Save Session Draft
- Approve Session

Saving session draft may update `billing_treatment` for cancelled/no-show appointments. That does not approve the session and does not overwrite raw calendar evidence.

Participant or bill-to saves continue to refresh dependent suggestions while preserving unrelated unsaved session fields in the UI.
