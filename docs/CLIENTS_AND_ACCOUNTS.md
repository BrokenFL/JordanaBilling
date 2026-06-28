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
- **Billing Summary:** Five compact cards — Sessions, Approved Uninvoiced Sessions, Invoices, Total Invoiced, and Finalized Invoice Total. The total reflects non-void finalized invoice totals only. A muted note explains: "Finalized invoice totals reflect non-void finalized invoices only. Payment tracking is not yet implemented."
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
- Finalized Invoice Total reflects non-void finalized invoices only; invoice payments are not yet tracked.
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
- **Billing Relationships reuse one active Bill To record per payer:** When a billing relationship is created or edited for a person payer, the app reuses one canonical active billing-party record for that payer instead of silently creating a new competing Bill To record.
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
- **Legacy duplicates stay reviewable:** If multiple active billing-party records or duplicate active relationships already exist, they are surfaced for review rather than merged or deleted automatically. Cleanup uses the explicit audited normalization endpoint (`POST /api/billing-relationships/normalize-payer`) which selects one canonical record, copies missing fields, deactivates redundant records, repoints safe mutable references, and leaves finalized invoices and payment history unchanged. The Billing Relationships UI shows a "Normalize" button on payer rows with detected conflicts.

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
4. **Finalized Invoice Total** — sum of totals for non-void finalized invoices; invoice payments are not yet tracked

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
- If no equivalent exists: a new account is created with the account name equal to the client's display name exactly (e.g., "Avery Stone", not "Avery Stone Billing Relationship") and type `individual`, and the selected client is added as the primary account member
- The account editor opens
- Return-to-review context is preserved when creation began from Session Review

#### Duplicate Relationship Prevention

An active billing relationship is unique by:

- the payer identity;
- the normalized covered-client UUID set;
- active status.

Covered-client order does not matter. Labels and account names do not make otherwise equivalent active relationships distinct.

Valid examples:

- Rebecca pays for Rebecca.
- Rebecca pays for Rebecca and Barbara.
- Rebecca pays only for Barbara.

Invalid duplicates:

- two active self-pay relationships for Rebecca;
- two active Rebecca → Rebecca and Barbara relationships;
- the same payer plus the same covered-client set under slightly different account names;
- the same payer plus the same covered-client set split across competing active Bill To records.

When an equivalent active relationship exists, the backend returns a 409 response with the existing account's identifier. The modal shows an inline message: "A billing relationship already exists for this client." with an "Open existing relationship" button. Clicking it opens the existing record and preserves return-to-review context. No duplicate account is created.

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

### Round 2B: Transactional Setup Backend

A new backend endpoint and service function provide transactional billing relationship setup from existing records.

#### Endpoint

`POST /api/billing-relationships/setup`

#### Supported Payer Kinds

- **client** — an existing active person who is also a session participant
- **person** — an existing active person who is not necessarily a session participant
- **organization** — an existing active organization billing party

#### Payload

```json
{
  "payer_kind": "client" | "person" | "organization",
  "payer_person_id": "<UUID>",
  "organization_billing_party_id": "<UUID>",
  "covered_client_ids": ["<UUID>", ...],
  "use_for_future_sessions": true
}
```

- `payer_person_id` is required for `client` and `person` payer kinds.
- `organization_billing_party_id` is required for `organization` payer kind.
- `covered_client_ids` must contain at least one existing active person ID.
- Duplicate covered-client IDs are rejected.
- This round does not accept raw person or organization creation fields.

#### What the Backend Does

For all payer kinds:

1. Creates or reuses a billing party (person billing party for client/person, existing org billing party for organization).
2. Creates a `client_accounts` billing relationship (account).
3. Stores covered clients in `account_members`.
4. Sets the payer billing party as `client_accounts.default_billing_party_id`.
5. Writes sanitized audit entries (no emails, phone numbers, addresses, or private notes).
6. Commits the entire operation atomically — any failure rolls back all changes.

Organization payers are NOT represented as only a standalone billing party. They get a full `client_accounts` relationship with `account_members` linking the covered clients, just like person payers.

#### Exact Duplicate Rule

An active relationship is an exact duplicate only when:

1. Its default billing party represents the same payer (same `person_id` for person payers, same `billing_party_id` for organization payers).
2. Its active account-member person-ID set exactly equals the requested covered-client set (order-independent, no subset or superset matching).
3. The account is active.

When a duplicate exists, the backend returns the existing relationship with `created: false, duplicate: true`. Repeated identical calls are idempotent.

Still allowed: same payer with different client set, different payer with same client set, a client in unrelated shared relationships, inactive historical relationships.

#### Derived Account Type

- Client paying for self only → `individual`
- All other person or client payer relationships → `family`
- Organization payer → `family` (organization is not used as an account_type value to avoid schema migration; the billing party type distinguishes the payer)

#### Account Naming

- Client paying for self only → client display name
- Person payer → payer display name (with "— pays for ..." suffix when covering other clients)
- Organization payer → organization name (with "— pays for ..." suffix when covering multiple clients)
- Never appends "Billing Relationship", "Account", or "Household"
- Naming is not used as the duplicate key

#### Transaction Safety

Helper functions (`create_account`, `create_billing_party`, `add_account_member`, `billing_party_for_person`) accept a backward-compatible `commit: bool = True` keyword argument. When `commit=False`, they skip their own `conn.commit()`, allowing the caller to compose them in a single atomic transaction. (Note: `init_db()` is now a no-op in request-path code; schema migrations run explicitly at startup via `migrate_database()`.)

#### API Response

```json
{
  "account_id": "<UUID>",
  "billing_party_id": "<UUID>",
  "account_name": "<name>",
  "account_type": "<type>",
  "covered_client_ids": ["<UUID>", ...],
  "created": true,
  "duplicate": false
}
```

#### Not Implemented in Round 2B

- Frontend wizard (3-step guided UI) — implemented in Round 2C
- Creating new people or organizations from the setup endpoint
- Session Review attachment (linking the relationship to a review candidate)
- Calendar alias creation for future-session matching

### Round 2C: Guided Billing Relationship Wizard

A three-step wizard replaces the basic Create Billing Relationship modal. The wizard uses existing records only — no new people or organizations are created.

#### Wizard Steps

**Step 1 — Who receives the invoice?**

Three selectable choices: A client, Another person, An organization. Each choice reveals a search input:

- A client / Another person → searches `/api/people` (existing active people)
- An organization → searches `/api/organization-billing-parties` (active billing parties with `billing_party_type = "organization"` only; person-linked billing parties are excluded)

All active people appear in client/person search because the current schema does not formally distinguish clients from non-clients. This is a known limitation.

**Step 2 — Who are they paying for?**

Searchable multi-select of existing active people via `/api/people`. Selected clients appear as removable chips. At least one selection is required. Selected clients are omitted from search results (not shown as actionable rows); removing a chip restores that client to search.

Defaults:
- No automatic preselection for any payer type (client, person, or organization)
- Session Review participants are not auto-preselected; they remain selectable via search
- Changing payer type clears all covered-client selections to prevent stale state
- Navigating Back and Forward preserves existing selections

**Step 3 — Review and save**

Shows invoice recipient, Pays for list, and a future-sessions checkbox. Save calls `POST /api/billing-relationships/setup` with the Round 2B payload.

#### Save Behavior

- Save button disables and shows "Saving…" during submission
- New relationship (`created: true`) → wizard closes, new relationship record opens, return context preserved
- Exact duplicate (`duplicate: true` or `created: false`) → inline message "This billing relationship already exists." with "Open existing relationship" button; wizard stays open
- API error → inline error text; wizard stays open
- No `alert()` or `prompt()` used in the wizard workflow

#### Navigation and Cancel

- Back preserves all valid selections
- Cancel with no changes closes immediately
- Cancel after selections shows an in-page confirmation (not a browser `confirm()` dialog)
- Escape follows the same safe-cancel behavior
- Return focus to the initiating control after cancellation
- Only one wizard overlay may be open at a time

#### Accessibility

- Proper `<label>` elements, semantic buttons and radio controls
- Keyboard operable with focus trap
- Visible focus states
- User-controlled values escaped via `escapeHtml()` before rendering
- No browser `prompt()` or `alert()` in the wizard

#### New API Endpoint

`GET /api/organization-billing-parties?q=<query>` — returns active organization billing parties only (billing_party_type = "organization", active = 1). Person-linked billing parties are excluded.

#### Not Implemented in Round 2C

- Creating new clients, people, or organizations from the wizard — implemented in Round 2D1 (clients and people) and Round 2D2 (organizations)
- Session Review relationship attachment
- Automatic return to Session Review after save
- Saved relationship editor redesign
- Delete or deactivate controls

### Round 2D1: In-Wizard Person Creation (Clients and Other People)

The wizard now supports creating new person records directly inside Steps 1 and 2. No new schema fields or migrations are required — all person records use the existing `people` table via `POST /api/people`.

#### Step 1: Create New Client or Another Person

When "A client" or "Another person" is selected as the payer type, a "Create new client" (or "Create another person") button appears below the search results. Clicking it opens an in-wizard child form.

**Form fields:**
- First name — required
- Last name — required
- Preferred name — optional
- Billing email — optional
- Billing phone — optional
- Administrative notes — optional (billing/administrative content only)

**Not asked from the user:**
- Display name (derived from first + last name)
- Person code (generated server-side only after first and last names exist)
- Account type, relationship name, clinical information

**Display name derivation:** `"${first_name} ${last_name}".trim()` — follows existing repository conventions.

**Person-code generation:** Uses the existing `generate_person_code()` function. Format: `PREFIX-NNN` (e.g., `JS-001`). Generated only when both first and last names are provided.

**Buttons:**
- Back to search — returns to the search view without losing parent wizard state
- Cancel — follows the wizard's existing safe-cancel behavior
- Create Client / Create Person — submits the form

#### Step 2: Create New Client

Step 2 also includes a "Create new client" button below the search results. After creation:
- The new client is added to the Pays for list
- The payer selection is preserved
- All previously selected covered clients are preserved
- Duplicate covered-client selections are prevented

#### Duplicate-Person Handling

Before final creation, the backend checks for an active person with the same normalized display name (`lower(display_name) = lower(?)`).

When a possible duplicate exists:
- No new person is silently created
- The child form stays open
- An inline warning shows: "A person with this name already exists."
- The existing person's display name and person code are shown
- Two options are offered:
  - **Use existing person** — selects the existing record as the invoice recipient (or adds to Pays for in Step 2)
  - **Go back and edit** — returns to the form fields to change the name

The backend response now includes `created: true/false` and `existing: true/false` flags for unambiguous duplicate detection. This is backward compatible — existing callers that don't check these flags still work.

#### Successful Creation Behavior

After successful creation or explicit selection of an existing duplicate:
- Returns to wizard Step 1 (or Step 2)
- Selects the created/existing person as the invoice recipient
- Preserves the chosen payer type
- Preserves any previously selected Pays for clients
- Preserves return context
- Enables Continue

For "A client":
- The newly created client is NOT automatically added under Pays for; the user must explicitly select covered clients in Step 2

For "Another person":
- The newly created person is NOT automatically added under Pays for

#### Form Behavior

- No browser `prompt()`, `alert()`, or `confirm()` used
- Inline field validation (first and last name required)
- User-controlled values escaped via `escapeHtml()`
- Submission disabled while saving (shows "Creating…")
- Double submission prevented via `creating` flag
- Form stays open after API failure with inline error
- First invalid field receives focus
- Back returns without losing parent wizard state

#### Backend Changes

`create_person()` now returns `created: true` and `existing: false` for new records, and `created: false` and `existing: true` for duplicates. This is additive and backward compatible.

#### Known Limitation: Client vs. Person Distinction

The current schema does not formally distinguish clients from other people. All active people appear in both client and person search. The wizard's "A client" vs. "Another person" distinction is a UI-level payer-type choice, not a permanent classification. This is documented as a known limitation.

#### Not Implemented in Round 2D1

- Creating new organizations from the wizard — implemented in Round 2D2
- Organization contact creation
- Session Review attachment
- Automatic return to Session Review after save
- Saved relationship editor redesign
- Delete or deactivate controls

### Round 2D2: In-Wizard Organization Creation

The wizard now supports creating new organization billing parties directly inside Step 1. No new schema fields or migrations are required — all organization records use the existing `billing_parties` table via `POST /api/billing-parties`.

#### Step 1: Create New Organization

When "An organization" is selected as the payer type, a "Create new organization" button appears below the search results. Clicking it opens an in-wizard child form.

**Required field:**
- Organization name

**Optional fields:**
- Billing contact name (maps to `billing_name`; defaults to organization name if left blank)
- Billing email
- Billing phone
- Address line 1
- Address line 2
- City
- State
- Postal code
- Preferred delivery method (select: Unresolved, Email, Mail, Both — uses existing supported values)
- Administrative notes (billing/administrative content only)

**Not included:**
- Clinical fields
- Organization contact person records (organizations are billing parties, not people)
- New tables or columns

**Buttons:**
- Back to search — returns to organization search without losing parent wizard state
- Cancel — follows the wizard's existing safe-cancel behavior
- Create Organization — submits the form

#### Duplicate-Organization Handling

Before creation, the backend checks for an active organization billing party with the same normalized organization name (`lower(organization_name) = lower(?)`).

When a possible duplicate exists:
- No new billing party is silently created
- The child form stays open
- An inline warning shows: "An organization with this name already exists."
- The existing organization's name and available billing contact details (email, phone) are shown
- Two options are offered:
  - **Use existing organization** — selects the existing record as the invoice recipient
  - **Go back and edit** — returns to the form fields to change the name

The backend response now includes `created: true/false` and `existing: true/false` flags for unambiguous duplicate detection. This is backward compatible — existing callers that don't check these flags still work.

#### Successful Creation Behavior

After successful creation or explicit selection of an existing duplicate:
- Returns to wizard Step 1
- Keeps payer type as "An organization"
- Selects the newly created/existing organization as the invoice recipient
- Preserves any previously selected Pays for clients
- Preserves return context
- Enables Continue

The organization is NOT automatically added under Pays for. Organizations are billing parties, not session participants.

#### Step 2 Behavior

After a new organization is created:
- Previously selected covered clients remain selected
- The organization does not appear as a client
- The user may add existing or newly created clients through the already implemented Step 2 flow

#### Billing-Party Representation

Organizations are stored as `billing_parties` rows with `billing_party_type = "organization"`. When the final billing relationship is saved via `POST /api/billing-relationships/setup`, the organization's `billing_party_id` is passed as `organization_billing_party_id` in the payload. The setup endpoint creates or reuses an account for the organization, just as it does for person payers.

#### Form Behavior

- No browser `prompt()`, `alert()`, or `confirm()` used
- Inline validation (organization name required, whitespace-only rejected)
- User-controlled values escaped via `escapeHtml()`
- Submission disabled while saving (shows "Creating…")
- Double submission prevented via `creating` flag
- Form stays open after API failure with inline error
- First invalid field receives focus
- Back returns without losing parent wizard state

#### Backend Changes

`create_billing_party()` now returns `created: true` and `existing: false` for new records, and `created: false` and `existing: true` for duplicates (organization type only, detected by normalized organization name). This is additive and backward compatible.

#### Not Implemented in Round 2D2

- Organization contact person records (organizations are billing parties, not people)
- Session Review attachment — implemented in Round 2E1
- Automatic return to Session Review after save — implemented in Round 2E1
- Saved relationship editor redesign
- Delete or deactivate controls

### Round 2E1: Session Review Integration

The billing relationship wizard now integrates with Session Review. When launched from a review candidate, the wizard suggests the current payer, creates or reuses the billing relationship, attaches it to the session, and returns to the same candidate — all without approving the session.

#### Launching from Session Review

When Jordana selects "Change payer or shared billing" from a Session Review candidate and opens the wizard, the return context is preserved. The context includes:

- Candidate ID
- Session ID
- Return view
- Current participants (with person IDs)
- Current account ID
- Current billing-party ID
- Current bill-to person ID

The existing `sessionStorage` and URL/hash behavior is preserved. No second return-context system is introduced.

#### Step 1: Payer Suggestion

When launched from Session Review, the wizard suggests the current effective invoice recipient:

**Priority:**
1. Current session billing party
2. Current account default billing party
3. Current bill-to person
4. No suggestion

**Mapping:**
- Person-linked billing party whose person is a current session participant → select "A client"
- Person-linked billing party whose person is not among participants → select "Another person"
- Organization billing party → select "An organization"

The suggestion is shown as selected, but Jordana can change it. No new payer record is created from a suggestion. If the referenced record is inactive or missing, no suggestion is shown.

#### Step 2: Covered Client Selection

When the return context contains confirmed session participants:

- No participants are auto-preselected under Pays for
- Session participants remain selectable via the search input
- Unresolved participant names are not silently converted into people
- Jordana must explicitly select which clients the payer covers
- Changing payer type clears all covered-client selections to prevent stale state
- Selected clients are omitted from search results; removing a chip restores the client to search

#### Saving and Attaching

The save flow is a two-step process:

1. **Setup**: `POST /api/billing-relationships/setup` — creates or reuses the billing relationship, returns `account_id`, `billing_party_id`, `created`, `duplicate`
2. **Attach**: `POST /api/review/candidates/{id}/save-relationship` — attaches the returned account and billing party to the current review candidate, preserving confirmed participants

The attachment payload includes:
- `participants` — confirmed session participants with person IDs
- `account_id` — from setup response
- `billing_party_id` — from setup response
- `default_billing_party_id` — from setup response
- `primary_person_id` — first primary participant

**Not changed by attachment:**
- Duration, service/session type, time category, rate
- Payment status
- Approval status
- Raw calendar evidence

#### Duplicate Relationship Reuse

If setup returns an existing exact relationship (`duplicate: true`):

- When launched from Session Review: a "Use this billing relationship" button appears alongside "Open existing relationship"
- "Use this billing relationship" attaches the existing account and billing party to the current candidate
- "Open existing relationship" opens the account record while preserving return context
- When launched from the main Billing Relationships page: only "Open existing relationship" is shown (Round 2C behavior preserved)

#### Partial-Failure Recovery

If relationship setup succeeds but attachment fails:

- The wizard stays open with a recovery message: "The billing relationship was saved, but it could not be attached to this session."
- Three recovery actions are offered:
  - **Try attaching again** — retries only the attachment step (does not call setup again)
  - **Open billing relationship** — opens the account record while preserving return context
  - **Return to review without attaching** — returns to the candidate without attaching
- The successfully created relationship is not deleted
- Retry reuses the same account and billing-party IDs

#### Successful Attachment

After both setup and attachment succeed:

- The wizard closes
- Return context is cleared
- Jordana returns to the same Session Review candidate
- The candidate is refreshed (reloaded)
- A brief inline confirmation shows: "Billing relationship saved for this session."
- The session remains unapproved — Jordana must still explicitly review and approve

#### Approval Safety

- Setup does not call any approval endpoint
- Attachment does not set `review_status = approved`
- No invoice is generated
- No payment record is created
- Raw calendar evidence is unchanged

#### New People and Organizations

Round 2D1 and 2D2 child forms continue to work within the Session Review flow. After creating a new client, person, or organization:

- Return context is preserved
- Selected session participants are preserved
- Other wizard choices are preserved
- Setup and attachment use the resulting IDs

Newly created payers or clients are not attached to the session until the final wizard Save succeeds.

#### User-Visible States

- "Saving relationship…" — during setup
- "Attaching to session…" — during attachment
- "Billing relationship saved for this session." — on success (inline, not a browser alert)
- Recovery message with actions — on attachment failure

#### No Visible Account Field in Review

The routine Session Review screen does not gain a visible Client / Family Account field. The account relationship is stored internally but not surfaced as a new visible field in the inspector.

#### Not Implemented in Round 2E1

- Saved Billing Relationship editor redesign
- Delete or deactivate relationship controls
- Rate/history/alias layout changes
- Invoice generation
- Payment changes
- Calendar aliases
- Bulk cleanup
- Schema migrations
- Automatic session approval

### Round 2E2: Final Integration Hardening

Round 2E2 completed the Round 2 workflow with integration fixes and behavior-oriented tests.

#### Integration Defects Found and Fixed

1. **`selectPayerType` always cleared `payerPerson`** — both `type !== "client"` and `type !== "person"` conditions fired regardless of selection. Fixed: only clear `payerPerson` when switching to organization; only clear `payerOrg` when switching away from organization.

2. **`selectPayerType` erased preselected participants** — `coveredClients = []` for person/org erased Session Review preselections. Fixed: no longer clears coveredClients when changing payer type. Client payer adds to coveredClients via `unshift` instead of replacing.

3. **`selectPayer` replaced coveredClients for client payer** — erases preselected participants. Fixed: adds client to coveredClients via `unshift` instead of replacing.

4. **`handlePersonCreated` replaced coveredClients for client payer** — same issue. Fixed: adds via `unshift`.

5. **`wizardUseExisting` DOM ID collision** — person duplicate button and relationship duplicate button shared the same ID. Fixed: person duplicate button renamed to `wizardUseExistingPerson`.

6. **`showPayerSelected` always labeled person payers as "Selected client"** — should say "Selected person". Fixed: passes actual `payerType`/`formPayerType` instead of hardcoded `"client"`.

7. **Stale return context from sessionStorage** — navigating to Billing Relationships via nav picked up stale context from a previous Session Review session. Fixed: `showClients` clears return context when no hash params are present.

#### Behavior-Oriented Tests

Added `tests/test_billing_relationships_round2e2.py` with 34 behavior tests and 7 API integration tests that verify control flow rather than just string presence:

- Wizard creates exactly one overlay
- Re-rendering uses `innerHTML` (replacement, not append)
- Main-page Save does not call `save-relationship`
- Session Review Save calls setup then attachment
- Double-click Save is prevented by `saving` flag
- Duplicate Use action calls `attachToSession` only
- Back and Forward preserve payer and covered clients
- Changing payer kind clears only the correct payer selection
- Confirmed participants are preselected; unresolved names excluded
- Child forms preserve parent state
- Cancel makes no API calls
- Attachment retry does not call setup
- Return-without-attachment does not modify billing fields
- Success clears return context only after attachment
- Failure preserves return context
- Session remains unapproved
- No invoice or payment is created
- Stale return context is cleared on nav
- No duplicate DOM IDs within one rendered wizard state
- Person duplicate button has distinct ID from relationship duplicate
- `showPayerSelected` uses correct label for person
- `selectPayerType` does not erase coveredClients
- `selectPayer` adds to coveredClients instead of replacing
- `handlePersonCreated` adds to coveredClients instead of replacing
- `suggestPayerFromContext` handles inactive payer gracefully
- API error parsing checks `res.ok` and `json.ok`
- Escape follows safe-cancel flow
- All user-controlled values are escaped
- API tests verify no approval, no invoice, no payment, evidence preserved, duration/rate preserved, duplicate setup creates one account

#### Demo Data Coverage

The sanitized demo CSV (`data/samples/sanitized_demo_calendar_snapshots.csv`) covers:

- **Individual self-pay client**: Bob Smith, Joe Carter, Robin Rivers, etc.
- **Two-client session**: "Avery Stone + Taylor 6"
- **Existing another-person payer**: "Casey North mom paying 4"
- **Existing organization payer**: Cedar Family Trust (seeded by demo script)
- **Candidate with no current payer**: most candidates start without a payer
- **Unresolved participant name**: "Robin Rivers 530 scheduled 5 30"
- **Cancelled and no-show**: "Alex Lane 2 canceled", "Parker Vale 930 no show"
- **Personal/admin events**: "Lunch with Karen", "Dentist", "Pick up dry cleaning"
- **Duplicate relationship reuse**: create the same relationship twice
- **Title time variants**: "Bob Smith 10", "Bob Smith 10 office", "Robin Rivers 5 30", "Robin Rivers 530", "Robin Rivers 5:30 phone"

No changes to the demo CSV or demo script were needed.

### Round 3A: Deactivate and Reactivate Billing Relationships

Round 3A adds safe lifecycle management for billing relationships. A relationship can be deactivated when no longer needed and reactivated later if circumstances change. This is **not** deletion — all historical records are preserved.

#### Deactivation

A **Deactivate Billing Relationship** button appears in the open account record for active relationships. Clicking it shows an in-page confirmation:

> **Deactivate this billing relationship?**
> It will no longer appear in active searches or be suggested for future sessions. Existing sessions, invoices, rates, payments, and history will remain unchanged.

Buttons: **Cancel** and **Deactivate**.

After confirmation:
- Sets `client_accounts.active = 0`
- Preserves the account UUID, code, members, billing parties, sessions, invoices, payments, rates, and audit history
- Writes an audit entry with action `deactivated`
- Refreshes the record to show **Inactive** status

#### Reactivation

For an inactive relationship, the deactivate button is replaced with **Reactivate Billing Relationship**. The in-page confirmation says:

> **Reactivate this billing relationship?**
> It will appear in active searches and be suggested for future sessions again.

After confirmation:
- Sets `client_accounts.active = 1`
- Preserves all historical values
- Writes an audit entry with action `reactivated`
- Refreshes the directory and record to show **Active** status

#### Directory Filtering

The Billing Relationships directory has a status filter with three values:

- **Active** (default) — shows only active relationships
- **Inactive** — shows only inactive relationships
- **All** — shows both

The status filter works alongside the existing type filter. Search respects the selected status filter. Opening an inactive record still works from any filter view.

#### Suggestions and Duplicate Detection

Inactive relationships are excluded from:
- Session Review billing party suggestions (`effective_billing_party_lookup` checks `ca.active = 1`)
- Exact duplicate matching during relationship creation (`find_duplicate_billing_relationship` checks `a.active = 1`)
- Equivalent account lookup (`find_equivalent_account` checks `a.active = 1`)
- Account name duplicate detection (`create_account` checks `active = 1`)

A new active relationship with the same payer and covered-client set can be created after deactivation. The inactive record is not automatically reactivated.

#### Historical Preservation

Deactivating a relationship does not alter:
- `sessions.account_id` or `sessions.billing_party_id`
- Invoice linkage (`invoices.bill_to_party_id`)
- Payment status (`sessions.payment_status`)
- Approved rates (`sessions.approved_rate_cents`, `rate_rules`)
- Account members (`account_members`)
- Billing parties (`billing_parties`)
- Raw calendar evidence (`raw_calendar_snapshots`)
- Audit history

#### Backend API

Two focused endpoints:

- `POST /api/accounts/{account_id}/deactivate` — sets active to 0
- `POST /api/accounts/{account_id}/reactivate` — sets active to 1

Both are idempotent: if the state is already at the requested value, no audit entry is written. Both return the resulting account dict with the `active` field. Missing accounts return 404.

Audit actions: `deactivated` and `reactivated`. Audit details contain only `account_name` — no sensitive billing information.

#### Accessibility and UI Behavior

- In-page confirmation dialog (no browser `confirm()` or `alert()`)
- Escape key cancels safely
- Focus returns to the initiating button on cancel
- Buttons disabled while request is active
- Spinner text: "Deactivating…" / "Reactivating…"
- Inline API error display
- Double-click protection via `inFlight` flag
- All user-controlled values escaped with `escapeHtml`

#### No Permanent Deletion

Permanent deletion is not implemented and will not be implemented in this round. Deactivation is the only lifecycle action. No cascade deletes, no membership removal, no billing party removal.

#### No Schema Migration

The `client_accounts.active` column already existed as `INTEGER NOT NULL DEFAULT 1`. No migration was needed.

#### Tests

`tests/test_billing_relationships_round3a.py` contains 64 tests covering:
- Backend deactivate/reactivate with idempotency and audit
- Directory filtering by active/inactive/all
- Suggestion and duplicate exclusion for inactive relationships
- Historical preservation (sessions, invoices, payment status, rates, members)
- Frontend JS behavior (in-page confirmation, escape, focus, double-click, spinner, no browser prompts)
- API integration via HTTP (200, 404, idempotent, no side effects)
- No permanent delete action, no schema migration, raw evidence unchanged

### Out of Scope (Remaining)

The following remain planned and were not started:

- Automatic payer classification
- Full right-panel redesign

### Round 3B: Simplify and Correct the Billing Relationship Editor

Round 3B fixes a confirmed browser bug and simplifies the saved billing relationship editor.

#### Bug Fix: New Client Creation No Longer Silently Changes Payer

**Root cause**: When a user selected "A client" and created a person, then switched to "Another person", the `payerPerson` variable was not cleared — the old client remained as the selected payer. Additionally, `handlePersonCreated` always set `payerPerson = person` regardless of whether the creation context matched the current payer type.

**Fix**:
- `selectPayerType` now clears `payerPerson` when switching between "client" and "person" types
- `handlePersonCreated` checks that `formPayerType` matches the current `payerType` before setting `payerPerson`; if they don't match, the created person is added to covered clients only
- Step 2 covered-client search results are now clickable to remove (previously non-interactive "Selected" labels)

#### Simplified Editor

The saved billing relationship editor (`openAccountRecord`) has been redesigned around four concepts:

1. **Invoice recipient** — read-only display of current payer name, type, email, phone, delivery method, and address. A "Change invoice recipient" button opens an inline search with payer type choices (A client, Another person, An organization).

2. **Pays for** — list of covered clients with × remove buttons. An "Add client" button opens an inline search. Already-selected clients show "Click to remove" and can be toggled.

3. **Billing delivery** — editable fields: billing name, email, phone, contact name, address lines, city, state, postal code, preferred delivery method, and administrative notes.

4. **Status** — Active/Inactive pill with deactivate/reactivate button (from Round 3A).

A single **Save changes** button calls `POST /api/accounts/{account_id}/update-billing-relationship` transactionally.

#### Backend: Transactional Update

`update_billing_relationship(conn, account_id, payload)`:
- Validates payer_kind, covered_client_ids, and payer person/org
- Checks for exact duplicate (excluding self)
- Transactionally updates: billing party, account members (add/remove/update), billing delivery fields, admin notes
- Writes audit entry with action `updated_billing_relationship`
- Preserves account UUID, account code, sessions, invoices, payments, rates
- Rejects editing inactive accounts
- Returns full account record via `get_account_record`

`remove_account_member(conn, account_id, person_id)`:
- Removes a covered client from a billing relationship
- Preserves the person record and all historical sessions
- Writes audit entry with action `removed`

#### Backend API

- `POST /api/accounts/{account_id}/update-billing-relationship` — transactional update
- `POST /api/accounts/{account_id}/remove-member` — remove a covered client
- `GET /api/billing-relationships/find-duplicate` — find duplicate for editor's "Open existing" prompt

#### Duplicate Handling During Edit

If the edited payer and covered-client set matches an existing active relationship, the backend returns a 400 error with "This billing relationship already exists." The editor shows an in-page prompt with "Open existing relationship" and "Cancel changes" buttons.

#### No Browser Prompts

The editor uses in-page error boxes and duplicate boxes — no `alert()`, `prompt()`, or `confirm()`.

### Duplicate Analysis

The Billing Relationships screen includes a read-only duplicate analyzer. It flags:

- exact active duplicate relationships;
- duplicate self-pay relationships;
- same payer with the same normalized covered-client set;
- active relationships for one payer that still point at multiple active Bill To records.

The analyzer does not delete, merge, deactivate, or rewrite records. Existing duplicates remain visible and require a later explicit audited resolution workflow.

#### No Schema Migration

No schema changes were needed. All existing tables and columns are used as-is.

#### Tests

`tests/test_billing_relationships_round3b.py` contains 68 tests covering:
- Backend `update_billing_relationship`: changes payer, adds/removes covered clients, preserves account ID, writes audit, rejects inactive accounts, rejects duplicates, allows same-relationship update, validates empty covered/invalid payer kind/missing payer/nonexistent account, updates billing delivery and admin notes, preserves sessions, handles organization payer
- Backend `remove_account_member`: removes member, preserves person, writes audit, raises for nonexistent
- Wizard bug fix: `selectPayerType` clears `payerPerson`, `handlePersonCreated` checks payer type match, Step 2 results are clickable to remove, no alert in handler
- Editor JS: has Invoice recipient/Pays for/Billing delivery/Status sections, Save changes button, no old field IDs, no alert/prompt/confirm in save flow, validation messages, recipient search with payer type choices, covered search allows remove
- Editor CSS: `.editor-section`, `.covered-client-row`, `.covered-client-remove`
- Round 3A still works: deactivate/reactivate buttons, status filter, lifecycle confirm
- No schema migration: no ALTER TABLE, SCHEMA unchanged

### Round 3C: Final Integration Hardening

Round 3C completes the billing relationship editor with verified defect fixes, terminology cleanup, and runtime test coverage.

#### Verified Defects Fixed

1. **XSS in return links** — `raw_calendar_title` and `session_date` in editor return links were rendered via `fmt()` without escaping. Fixed: all user-provided values in return links now use `escapeHtml()`.

2. **Dead code removal** — Removed unused functions from the old editor refactor: `openAddClientModal`, `renderModalSearchResults`, `payerDisplayOptions`, `recordBillingPartyDraft`, `relationshipNameSuggestion`. Removed unused `ACCOUNT_TYPE_LABELS` constant.

3. **Backend-only labels in UI** — Removed `account_code` from the editor meta display. Removed `account_code` and `account_type` columns from the organization panel's linked accounts table. Changed "Default bill to:" to "Invoice recipient:" in the billing directory.

4. **Organization name field** — The "Organization name" input in the editor's Billing delivery section is now shown only when the payer type is `organization`. Person and client payers do not see this field.

5. **Unsaved changes detection** — The editor now tracks unsaved changes via an `editorDirty` flag. Modifying any delivery field, removing a covered client, or changing the invoice recipient sets the flag. Clicking the return link with unsaved changes shows an in-page confirmation with "Keep editing" and "Return without saving" options.

6. **Browser `confirm()` removal** — Organization deactivation and billing party deactivation now use in-page confirmation boxes with Cancel and Deactivate buttons, replacing native `confirm()` dialogs.

7. **Organization name in `update_billing_relationship`** — The backend `update_billing_relationship` function now processes `organization_name` from `billing_delivery` payload, allowing organization name updates through the editor.

#### Terminology Changes

| Old | New | Location |
|-----|-----|----------|
| "Default bill to:" | "Invoice recipient:" | Billing directory account rows |
| "Add client" | "Add Client" | Editor button (capitalization) |
| `account_code` in meta | Removed | Editor header |
| `account_code` column | Removed | Org linked accounts table |
| `account_type` column | Removed | Org linked accounts table |
| "Contact name" field | "Organization name" (org payers only) | Editor billing delivery |

#### Tests

`tests/test_billing_relationships_round3c.py` contains 59 tests covering:

- **XSS**: return links use `escapeHtml` for calendar title and session date; `fmt()` not used for raw calendar title
- **Dead code**: `openAddClientModal`, `payerDisplayOptions`, `recordBillingPartyDraft`, `renderModalSearchResults`, `ACCOUNT_TYPE_LABELS`, `relationshipNameSuggestion` all removed
- **Terminology**: no `account_code` in editor, no `account_code`/`account_type` in org linked accounts table, directory uses "Invoice recipient:" not "Default bill to:", organization name field only for org payers, no "Contact name" label
- **Unsaved changes**: editor has `editorDirty` flag, `markEditorDirty` function, return link checks dirty, dirty confirm has "Keep editing" and "Return without saving", delivery inputs mark dirty, covered removal marks dirty
- **No `confirm()`**: org deactivation, billing party deactivation, wizard, and editor save all free of `confirm()` calls; org and billing party deactivation have in-page confirmation boxes
- **Backend runtime**: preserves historical rates, idempotent same covered, duplicate detection with org payer, remove member doesn't affect other accounts, update doesn't change account name, null billing delivery preserves existing, empty billing delivery sets null, deactivate→reactivate→update works, person payer also covered client, organization name in billing delivery
- **JS runtime**: save re-enables button on error, duplicate box has Open existing and Cancel, lifecycle confirm box exists, org/billing deact confirm has Cancel and Deactivate, editor dirty confirm has buttons
- **CSS**: `.wizard-confirm-actions` and `.lifecycle-confirm-box` classes exist
- **No schema migration**: no ALTER TABLE in review_services, schema unchanged
- **Round 3A still works**: deactivate/reactivate buttons, lifecycle confirm, status filter
- **Round 3B still works**: editor has Invoice recipient/Pays for sections, calls update endpoint, has covered/recipient search

#### No Schema Migration

No schema changes were needed. All fixes use existing tables and columns.

#### No New Features

Round 3C fixes verified defects only. No new major features, no UI redesign, no schema migration, no permanent deletion.
