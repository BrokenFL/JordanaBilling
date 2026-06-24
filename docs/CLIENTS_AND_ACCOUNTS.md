# Billing Relationships

Billing Relationships is the CRM-style home for backend relationship and shared-billing structures: households, families, individual accounts, billing parties, rates, aliases, administrative notes, and session history.

Routine session review should not force Jordana to choose a backend account or family record. Use Clients in this session and Bill to first. Open Billing Relationships only when a family/couple/default-payer/shared-rate relationship needs maintenance.

The list shows account code, name, type, primary person, members, billing party, default rate, outstanding balance, last session, and active status.

Billing relationship records show:

- Header details and active status
- Members and relationship roles
- Default billing party and contact information
- Account-specific rates
- Calendar aliases
- Session history
- Audit history through the backend service

Quick client and payer choices can be made in the Review Queue inspector. Deeper billing setup belongs here.

Do not automatically create a permanent shared account merely because two names appear in one calendar title.

Invoice grouping uses the confirmed billing party, not a required visible household account. Bill-to delivery preference is `email`, `mail`, `both`, or `unresolved`; a draft may override it and finalization freezes the selected method and destination.

## Client Workspace

Clicking a client in the Clients list opens a full-width client workspace at `#people/{person_id}`. The workspace replaces the former narrow sidebar layout and is the primary place to review a single client's permanent record, billing setup, relationships, invoices, sessions, and rate preferences.

### Permanent Client Data vs Session Participation

Permanent client data (name, contact, code, status, aliases, rate overrides) is stored in `people` and related tables. Session participation is stored in `session_participants` and is per-session. The client workspace shows both, but they remain separate: removing a client from a session does not delete the person, and deleting a person is not driven by session participation.

### Billing Setup

Billing setup is stored through billing-party records linked to the person. The client workspace shows all billing parties where this person is the payer, including billing name, email, phone, full address, delivery method, and active/inactive status. For self-pay clients, the card displays "Bills sent to this client." When no billing parties exist, the section shows "No billing setup saved." This section is read-only in the current phase.

### Payer Relationship Wording

The Billing Relationships section uses plain-language statements derived from `payers_for_client` and `people_billed_for`:

- **Self-pay:** `{Client} pays for herself` (e.g., "Robin Rivers pays for herself")
- **Third-party payer:** `{Client} is billed to {Payer}` (e.g., "Taylor Reed is billed to Avery Stone")
- **Payer's record:** `{Payer} pays for {Participant}` (e.g., "Avery Stone pays for Taylor Reed")

Each statement includes session count and most recent session date when available. Duplicate statements are suppressed. Account membership information is shown in a secondary subsection labeled "Related billing group information" and is not the primary client concept.

### Client Billing Summary

Four compact summary cards appear near the top of the client workspace:

1. **Active Billing Records** — count of active billing parties where this person is the payer
2. **Approved Uninvoiced Sessions** — count of approved, billable, non-future sessions billed to this person that are not already attached to a draft or finalized invoice
3. **Total Invoiced** — sum of all non-void invoice totals for billing parties belonging to this person
4. **Outstanding Balance** — sum of balances for non-void invoices; currently equals total invoiced because payment tracking is not implemented

### Client Invoice History

The invoices table shows all invoices addressed to billing parties belonging to this person. Columns: Invoice Number, Billing Period, Issue Date, Bill To, Status, Total, Balance, and Open. Void invoices show zero balance. The Open action navigates to the existing invoice view. Invoice history is read-only from the client page — no payment, finalization, or void controls appear here.

No schema migration was required for any of these features. All data is derived from existing tables via read-only queries.
