# Billing Relationships

Billing Relationships is a combined billing directory. It shows who receives invoices and who they pay for, including shared billing groups.

The directory includes four types of entries:

- **Self-pay:** A person-linked billing party where the payer is also the session participant. Self-pay clients do not require a client account and no synthetic account is created for them.
- **Pays for others (third-party):** A person-linked billing party covering one or more other people. One payer record appears regardless of how many sessions exist.
- **Organization:** An organization billing party that pays for sessions. Organization rows do not require an account.
- **Shared billing group (account):** A genuine `client_accounts` grouping (household, family, couple, etc.) with members and an optional default billing party. Accounts remain genuine grouping structures, not wrappers around every payer.

Billing parties do not require accounts. Accounts are not created for self-pay clients. Accounts remain genuine grouping/default structures.

The directory is read-only in this phase. No accounts, billing parties, people, sessions, or invoices are created or modified while reading the directory.

## Directory Table

The directory table shows the following columns:

- **Type** — Self-pay, Pays for others, Organization, or Shared billing group
- **Payer / Relationship** — Payer name or account name, with a plain-language subtext (e.g., "Pays for herself", "Pays for Taylor Reed and 2 others")
- **Covers** — Covered clients or account members
- **Sessions** — Distinct session count
- **Latest Session** — Most recent session date
- **Billing Delivery** — Preferred delivery method (email, mail, both, or unresolved)
- **Status** — Active or Inactive
- **Open** — Navigation button

A type filter (All, Self-pay, Pays for others, Organizations, Shared billing groups) and search box filter the directory client-side. No additional API requests are made when filtering or searching.

## Linked Payer and Account Rows

When a billing party is linked as an account's default payer, both records appear in the directory:

- The **payer row** shows muted text: "Linked to shared billing group: {account_name}"
- The **account row** shows muted text: "Default bill to: {billing_name}"

Both records are shown intentionally to preserve all relationship evidence and direct session billing activity. Records are not merged.

## Navigation

- **Person-linked payer rows:** Open navigates to `#people/{person_id}`, opening the full-screen client profile.
- **Account rows:** Open uses the existing account-detail sidebar/panel.
- **Organization rows without an account:** Read-only. The Open button is disabled and labeled "Details unavailable" until organization billing-party editing is implemented.

## Inactive and Empty States

Inactive billing parties and accounts remain visible and are clearly labeled "Inactive." They are not hidden by default.

When the directory is empty, the table shows "No billing relationships yet."

## Account Detail View

The existing account-detail sidebar remains intact for genuine account rows. It shows:

- Header details and active status
- Members and relationship roles
- Default billing party and contact information
- Account-specific rates
- Calendar aliases
- Session history
- Audit history through the backend service

## Existing /api/accounts Endpoint

The existing `/api/accounts` endpoint remains unchanged. It continues to return account records only. The billing directory uses a separate endpoint: `GET /api/billing-relationships`.

## Client Workspace

Clicking a client in the Clients list opens a full-width client workspace at `#people/{person_id}`. The workspace replaces the former narrow sidebar layout and is the primary place to review a single client's permanent record, billing setup, relationships, invoices, sessions, and rate preferences.

### Permanent Client Data vs Session Participation

Permanent client data (name, contact, code, status, aliases, rate overrides) is stored in `people` and related tables. Session participation is stored in `session_participants` and is per-session. The client workspace shows both, but they remain separate: removing a client from a session does not delete the person, and deleting a person is not driven by session participation.

### Billing Setup

Billing setup is stored through billing-party records linked to the person. The client workspace shows all billing parties where this person is the payer, including billing name, email, phone, full address, delivery method, and active/inactive status. For self-pay clients, the card displays "Bills sent to this client." When no billing parties exist, the section shows "No billing setup saved."

Billing Setup is editable from the full-screen client profile. The following behaviors apply:

- **Multiple records:** A client may have multiple Billing Setup records. Each record represents billing contact and invoice-delivery information for that permanent client.
- **No primary or default flag:** There is no primary, default, or preferred billing setup field. All records are equal.
- **Inactive records remain visible:** Inactive billing setups are shown with reduced opacity and an "Inactive" status pill. They are not hidden.
- **Add:** The "Add Billing Setup" button opens an inline form with the client's display name prefilled as the billing name, delivery method defaulting to "Unresolved", and all optional fields blank.
- **Edit:** Each card has an Edit button that opens the inline form pre-filled with current values.
- **Field clearing:** Optional fields (email, phone, address lines, city, state, postal code, administrative notes) can be cleared by leaving them blank. Blank values are sent as empty strings and stored as NULL. The billing name is required and cannot be blank.
- **Deactivation:** Deactivating a billing setup sets `active = 0`. A confirmation dialog explains that historical sessions and invoices will remain unchanged. Deactivation affects future selection only — the record is never deleted.
- **Reactivation:** Reactivating sets `active = 1` and restores the card to active status.
- **Client-profile creation always links to the current client:** When creating a billing setup from the client profile, `billing_party_type` is always `"person"` and `person_id` is always the current client's person ID. The form does not expose billing-party type, person, or organization selectors.
- **No account is automatically created:** Creating or editing a billing setup does not create a `client_accounts` record or an `account_members` record.
- **Historical preservation:** Existing sessions and finalized invoices retain their billing-party references and snapshots. Editing a billing setup does not rewrite historical session or invoice values.
- **Audit:** All create, update, deactivate, and reactivate actions are recorded in the audit log. Audit details contain changed field names only — no billing email, phone, address, or other field values are exposed.
- **Organization editing remains out of scope:** The client profile Billing Setup form does not expose organization fields. Organization billing-party editing is a separate future feature.

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

No schema migration was required for any of these features, including editable Billing Setup. All data is derived from existing tables and the existing billing-parties schema.
