# Section-Level Saves

The Review Queue resolves one calendar event. Each section can be saved independently, and no section save approves a session automatically.

## Save Person

Used for first name, last name, preferred name, display name, person code, contact fields, and active status. Corrections update the same `person_id`, preserve the old display name as an alias, write audit history, and refresh suggestions.

## Save Relationship

Used for participants, primary participant, account selection, membership roles, and default billing party. After saving, the backend refreshes account, billing party, confidence, suggested rate, checklist, unresolved fields, and review status.

## Save Billing Details

Used for billing party, billing name, email, address, phone, default payer, and administrative billing notes.

## Save Session Draft

Used for duration, service mode, time category, suggested rate, approved rate, payment status, and billable status. It saves without approval.

## Approval

Approve Session validates all required fields, saves reusable aliases and relationships, snapshots approved rates, writes audit history, and advances the review state. Approval is separate from every section save.
