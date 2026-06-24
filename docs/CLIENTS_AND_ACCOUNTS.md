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
- **Organization rows:** Open displays a dedicated read-only organization detail panel (`#organizationRecord`) within the Billing Relationships page. The panel is independent from the account panel (`#accountRecord`). No hash route is used. See [Organization Detail Panel](#organization-detail-panel) below.

## Inactive and Empty States

Inactive billing parties and accounts remain visible and are clearly labeled "Inactive." They are not hidden by default.

When the directory is empty, the table shows "No billing relationships yet."

## Organization Detail Panel

The organization detail panel is a read-only side panel that appears within the Billing Relationships page when an organization row's Open button is clicked. It is independent from the account detail panel — opening one clears or hides the other.

The panel displays:

- **Header:** Organization name (falling back to billing name), billing name as secondary when different, Active/Inactive status, and a Close button. Internal UUIDs are not displayed prominently.
- **Billing Details:** Read-only organization name, billing name, email, phone, address, preferred delivery method, and administrative notes. Missing values show a neutral fallback (em dash). No Edit, Save, Delete, Deactivate, or Reactivate controls appear.
- **Billing Summary:** Five compact cards — Sessions, Approved Uninvoiced Sessions, Invoices, Total Invoiced, and Outstanding Balance*. The balance uses the backend's current no-payments convention (non-void invoice totals). A muted note explains: "Payment tracking is not yet implemented. This currently reflects non-void invoice totals."
- **Covered Clients:** Table with client name, person code, session count, latest session date, and Open (navigates to `#people/{person_id}`). Empty state: "No clients have sessions billed to this organization yet." No account membership is inferred from sessions.
- **Sessions:** Table with date, participants, session type, duration, time category, stored approved rate, review status, invoice, and Open in Review. Sessions are newest first. Rates are displayed as stored — no recalculation. Draft invoices with an `invoice_id` but no `invoice_number` show "Draft invoice." Open in Review navigates to the existing review workbench via `candidate_id`.
- **Invoice History:** Table with invoice number, billing period, issue date, status, total, balance, and Open. Void invoices show zero balance. Open uses the existing invoice view. No Finalize, Mark Paid, Delete, or payment controls appear.
- **Related Shared Billing Groups:** Linked genuine accounts (where `default_billing_party_id` matches) are shown with account name, code, type, status, members, and Open (existing account panel behavior). Empty state: "No linked shared billing groups." Organization membership is not inferred from sessions.
- **Administrative History:** Read-only audit log showing timestamp, action, and sanitized details. No editing controls.

### Organization Panel API

The panel fetches data from `GET /api/billing-parties/{billing_party_id}`. This endpoint returns 200 for valid organizations, 404 for missing billing parties, 400 for person-linked billing parties (which should use the client endpoint), and 500 for unexpected errors. The endpoint performs no writes.

### Organization Panel Constraints

- Organizations remain separate from people and accounts.
- No person or account is automatically created.
- Invoices may be opened read-only via the existing invoice view.
- No payment or finalization controls are available.
- Outstanding Balance currently uses the no-payments convention.
- No schema migration was required.

### Organization Editing

Existing organization billing records are editable from the organization detail panel. The Edit button opens an inline form prefilled with current values.

**Editable fields:**

- Organization name (required)
- Billing name (required)
- Billing email
- Billing phone
- Address line 1
- Address line 2
- City
- State
- Postal code
- Preferred delivery method (Email, Mail, Email and mail, Unresolved)
- Administrative billing notes

**Not exposed in the form:**

- Billing-party type selector
- Person selector
- Account controls
- Primary/default controls

The form always preserves `billing_party_type: "organization"` and never sends `person_id`.

**Field clearing:** Blank optional fields are submitted as empty strings and stored as NULL. The organization name and billing name are required and cannot be blank.

**Cancel** closes the form without saving. **Duplicate submissions** are blocked while a save is in progress. Visible success and error messages appear in the panel.

**After save**, the panel re-fetches the organization detail endpoint and the Billing Relationships directory row refreshes.

**Deactivate/Reactivate:** Organizations can be deactivated and reactivated but never deleted. Deactivation shows a confirmation explaining that historical sessions and invoices will remain unchanged. Deactivate sends `{"active": false}`; reactivate sends `{"active": true}`. Both actions refresh the panel and directory status.

**What editing does not change:**

- Historical sessions
- Approved rates
- Invoices (bill-to IDs, totals, snapshots)
- Finalized invoice snapshots
- Linked accounts and their members
- Session billing-party IDs
- No person, account, or membership is created

**Audit:** All edit, deactivate, and reactivate actions are recorded in the audit log. Audit details contain changed field names only — no email, phone, address, or notes values are exposed.

Organization creation remains deferred.

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

## Round 1: Browser Prompt Removal

Browser `prompt()` calls for creating billing relationships and adding clients have been replaced with in-page modal dialogs. The following changes were made:

### Create Billing Relationship

Both creation paths — the main "Create Billing Relationship" button and the "Create Billing Relationship" button in the return-to-review workflow — now open an in-page modal instead of a browser prompt.

The modal contains:
- Heading: "Create Billing Relationship"
- Instruction text: "Select an existing client to begin. A more detailed payer setup will be completed in the next workflow step."
- A search input labeled "Search existing clients"
- Explicit selectable result rows (no fuzzy first-result auto-selection)
- Selected-client confirmation display
- Create and Cancel buttons
- Inline validation and error messages

When a client is selected and Create is pressed:
- The request goes to `/api/accounts/from-client` which checks for an existing equivalent relationship before creating
- If no equivalent exists: a new account is created with a safe default name (`{DisplayName} Billing Relationship`) and type `individual`, and the selected client is added as the primary account member
- The account editor opens
- Return-to-review context is preserved when creation began from Session Review

#### Duplicate Relationship Prevention

If the selected client is already the primary or sole member of an active individual billing relationship, the backend returns a 409 response with the existing account's identifier. The modal shows an inline message: "A billing relationship already exists for this client." with an "Open existing relationship" button. Clicking it opens the existing record and preserves return-to-review context. No duplicate account is created.

The backend enforces this through `find_equivalent_account` and `create_account_or_return_existing` in `review_services.py`. Repeated Create clicks for the same client produce only one account.

### Add Client

The former "Add Member" browser prompt has been replaced with an in-page client selector modal. The button label changed from "Add Member" to "Add Client".

The modal:
- Searches existing clients through the `/api/people` API
- Shows explicit selectable result rows
- Existing members are visually marked "Already included" and are not clickable
- The Add button remains disabled until a non-duplicate client is selected
- Shows the selected client before saving
- If a duplicate request reaches the backend, displays "This client is already included in this billing relationship." inline (no `alert()`)
- The modal remains open after a duplicate validation error so the user can try again
- Never silently chooses the first fuzzy match
- Refreshes the relationship record after a successful add

### Backend Change

`add_account_member` now checks for existing membership before inserting. If the person is already a member of the account, it raises `ValueError("This client is already included in this billing relationship.")` instead of silently succeeding via `INSERT OR IGNORE`. The API endpoint returns this as a 400 error with a clear message.

`ensure_account_member` (used by `save_interpretation`) remains idempotent and unchanged.

### Accessibility

The modals are keyboard usable with proper `<label>` elements, focus trap, Escape to cancel, and focus return to the initiating button on cancel. User-controlled text is escaped via `escapeHtml()` before insertion into HTML.

### Out of Scope (Round 2)

The following remain planned for Round 2 and were not started:
- Full guided "Who pays?" wizard
- Creating new clients from the modal
- Creating new organizations from the modal
- Automatic payer classification
- Full right-panel redesign
