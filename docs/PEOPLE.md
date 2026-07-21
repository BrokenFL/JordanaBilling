# Clients

Clients are actual human records, not calendar shorthand. The backend table is still `people` for compatibility. Parser candidates do not become permanent people/clients until Jordana or the reviewer confirms them.

Names are stored as:

- `first_name`
- `last_name`
- `preferred_name`
- `display_name`

The Clients list sorts by last name and shows client code, last name, first name, display name, billing relationships, bill-to links, last session, and active status.

Client records show contact information, billing relationships, relationship roles, sessions, bill-to links, aliases, administrative notes, merge status, active status, actual charged-rate history, and active future rate exceptions.

Client names may be corrected from the client record. The corrected name flows to
person-linked billing setup, sessions, future billing, and editable draft invoice
snapshots. Finalized and void invoice snapshots and PDFs remain historically frozen.
The Advanced section also provides an explicit duplicate merge: the user searches
for and confirms the client record to keep, mutable relationships and sessions are
repointed transactionally, the duplicate is marked merged, and finalized invoice
snapshots are not rewritten.

Session history uses approved session values. It must not reconstruct historical charges from current rate rules.

Active rate exceptions shown on a person record include person-specific exceptions and shared/joint exceptions involving that person. Joint exceptions should show the actual participant names, not a fabricated household name.

Do not store clinical notes, psychotherapy notes, narrative diagnoses, symptoms, medical histories, treatment plans, session-content notes, treatment summaries, or clinical interpretations. A structured diagnosis code may be stored only when Jordana intentionally enters or approves it for invoice-specific insurance billing or reimbursement documentation; it is entered per-invoice during finalization, not on person records, and must never be inferred from person names, calendar text, session descriptions, or other application data. Administrative notes should stay limited to billing and operational details such as preferred email, payer instructions, check payment, or unpaid balance context.

Finalized invoice participant names are immutable line snapshots. Correcting a current person name does not rewrite old invoices.
