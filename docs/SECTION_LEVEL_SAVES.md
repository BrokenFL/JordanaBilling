# Section-Level Saves

The Review Queue resolves one calendar event. Each section can be saved independently, and no section save approves a session automatically.

## Save Participants

Used for one or more people who attended the session. Saving participants persists immediately, writes audit history, and refreshes bill-to defaults, rate suggestions, unresolved fields, checklist, and review status.

Creating or correcting a person from the Participants workflow saves directly to SQLite. Person codes are generated only after first and last name are confirmed.

## Save Relationship

Used for optional advanced account selection, membership roles, default payer, and shared billing. After saving, the backend refreshes account, billing party, confidence, suggested rate, checklist, unresolved fields, and review status.

## Save Bill To

Used for billing party, billing name, email, address, phone, default payer, and administrative billing notes. Saving a bill-to contact does not add that contact as a session participant.

## Save Session Draft

Used for duration, session type, time category, suggested/editable rate, rate-change scope, payment status, and billable status. It saves without approval.

## Approval

Approve Session validates participants, bill-to, duration, session type, time category, approved/actual charged rate, and payment status. It saves reusable aliases and relationships, snapshots approved rates, writes audit history, and advances the review state. Approval is separate from every section save.
