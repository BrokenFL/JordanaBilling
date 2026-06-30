# Review UI Specification

This document describes the current implemented local UI. Older right-side-inspector and browser-prompt designs are obsolete.

## Route And Shell

The application runs locally at:

```text
http://127.0.0.1:8765/review
```

Start it with:

```bash
PYTHONPATH=app .venv/bin/python -m jordana_invoice \
  --db data/jordana_invoice.sqlite3 \
  serve-review
```

The desktop shell contains:

- fixed left navigation
- compact top status area
- full-width Review Queue
- focused review overlay for one calendar candidate/session
- separate workspaces for Sessions, Clients, Billing Relationships, Rate Card, Invoices, Payments, Calendar Import, Reports, and Settings

The UI reads and writes SQLite only through localhost API routes. It never edits CSV exports or raw Google Sheet rows.

## Review Queue

The Review Queue resolves one calendar event at a time.

The main table uses these columns:

- Status
- Date
- Time
- Clients
- Calendar
- Duration
- Rate
- Review

The status filter offers:

1. **Needs Review** — default; excludes approved and excluded records
2. **Approved**
3. **Excluded**

Calendar filters can show normal review calendars, all calendars, preferred work calendar, other calendars, personal/admin calendars, and hidden calendars. Hidden records remain recoverable.

Selecting a row or clicking **Review** opens the focused review overlay. Routine review no longer uses a persistent right-side inspector.

## Review Overlay

The overlay presents the current candidate/session in this order:

1. **Clients in this session**
2. **Bill To**
3. **Session Details**
4. **Additional Information**
5. **Approve Session**

Source calendar evidence remains available in a collapsed read-only section.

The overlay supports:

- focus trapping
- Escape to close, subject to unsaved-change protection
- Previous/next navigation where available
- focus restoration after closing
- double-submission protection
- sanitized inline errors
- no browser `alert()`, `prompt()`, or `confirm()` in the normal workflow

### Clients In This Session

Participants are permanent human records connected to this session.

The UI supports:

- searching existing people
- confirming parser-proposed names
- creating a person only after confirmed first and last names
- multiple participants with one charge
- removing all participants intentionally
- exact approved-alias reuse
- single-participant alias learning after **Save Client(s)**

The UI must not create one mashed-together person from a multi-person calendar title.

### Bill To

Bill To is the person or organization responsible for receiving and paying the invoice. Bill To may differ from the participants.

Routine review offers appropriate existing payer choices and a path to **Change payer or shared billing**. Advanced relationship setup belongs in the Billing Relationships workflow.

Routine review must not require or prominently expose:

- Client / Family Account
- account codes
- household labels
- relationship-role editing
- backend membership structures

### Session Details

The active UI offers exactly five billing session types:

1. Psychotherapy Session
2. Psychotherapy Session / House Call
3. Psychotherapy Session / Weekend
4. Psychotherapy Session / Evening
5. Custom

Office, Phone, and FaceTime are appointment methods and may appear as source evidence. They are not selectable billing session types.

The active duration choices are:

- 30 minutes
- 60 minutes
- 90 minutes
- 120 minutes
- Custom

Custom duration requires the actual minutes. Custom session type requires a custom description and may include an optional administrative code.

The rate field remains editable and can save to one of these scopes:

- this session only
- future sessions for one client
- future joint sessions for the exact participant combination

Approved historical rates are never rewritten by later rate-card changes.

### Derived Time Category

Time category is derived from the authoritative calendar date and start time. It is not a normal selectable review field.

Current categories are:

- `standard`
- `evening`
- `weekend`

Weekend overrides evening. House Call affects billing session type but does not create a separate editable time-category control.

The UI may display the derived category for clarity. Approval validates the stored derived value.

### Additional Information

The visible label is **Payment Handling**:

- **Invoice billing** — session is eligible for monthly invoice staging after approval
- **Paid at session** — approval requires the received amount, payment date, and supported payment method; approval records one payment and allocation and skips invoice staging

Appointment status is separate from payment handling.

Cancelled and no-show appointments require a separate billing-treatment decision:

- billable
- not billable
- waived
- unresolved

Cancelled/no-show records remain preserved even when not billed.

## Section-Level Save Behavior

The independent actions are:

- **Save Client(s)**
- **Save Bill To**
- **Save Session Draft**
- **Approve Session**

No section save approves a session.

After a successful section save, the backend refreshes dependent suggestions, checklist state, unresolved fields, payer defaults, and rate suggestions while preserving unrelated unsaved browser fields.

On save failure:

- overlay stays open
- button re-enables
- no stale saved state is shown
- sanitized error is displayed
- no partial browser state is treated as authoritative

## Approval Behavior

**Approve Session** validates at minimum:

- confirmed participants
- Bill To
- duration
- one of the five billing session types
- derived time category
- actual charged rate
- payment handling
- cancelled/no-show billing treatment when applicable
- paid-at-session payment details when applicable

During submission, the approve action is disabled.

On success:

- approval is committed
- stale selected state is cleared
- overlay closes
- the row is removed or refreshed
- focus is restored
- a confirmation banner appears
- no resubmittable stale form remains

For invoice billing, approval then attempts monthly invoice staging. Approval remains successful if staging warns or is temporarily unavailable; the staging warning is shown separately.

For paid-at-session, approval creates or validates the payment/allocation idempotently and reports invoice staging as not required.

On genuine approval failure:

- overlay stays open
- controls re-enable
- sanitized error is shown
- no successful state is falsely displayed

## Duplicate Resolution

The preferred action is **Confirm Duplicate & Next**.

On success:

- the action disables while pending
- duplicate resolution is persisted
- overlay closes
- stale selection is cleared
- the resolved item is removed or refreshed
- the next unresolved item opens when supported
- otherwise focus is restored
- a success banner appears

On failure:

- overlay stays open
- action re-enables
- sanitized error is displayed
- no partial UI state is treated as complete

## Billing Relationships

Billing Relationships is payer-centered.

Visible concepts are:

- Who receives the invoice?
- Who are they paying for?
- Invoice recipient
- Pays for
- Billing delivery
- Status

The guided wizard uses:

1. choose payer type and payer
2. explicitly select covered clients
3. review and save

Rules:

- the payer is not automatically covered
- session participants remain selectable but are not silently preselected
- changing payer type clears stale covered-client selections
- selected-client chips are the source of truth
- removing a chip makes that client searchable again
- exact active duplicates are blocked or reused explicitly
- saved relationships persist immediately to SQLite
- approved sessions are never silently rewritten
- deactivation preserves history; permanent deletion is not implemented

## Clients Workspace

The Clients workspace uses permanent `people` records and displays:

- names and person code
- active status
- billing setup
- payer relationships
- approved aliases
- recent sessions
- invoice history
- account summary
- active rate exceptions
- administrative billing notes

It must not store clinical notes, psychotherapy notes, narrative diagnoses, symptoms, medical histories, treatment plans, session-content notes, treatment summaries, or clinical interpretations. A structured diagnosis code may be stored only when required for administrative insurance billing (entered per-invoice during finalization). It must not rewrite finalized invoice snapshots after a current name change.

## Invoices Workspace

The Invoices workspace provides:

- searchable and filterable invoice library
- draft, finalized, and void statuses
- draft line editing with optimistic locking
- draft HTML print preview
- real in-memory draft PDF preview marked DRAFT
- readiness validation before finalization
- explicit two-step finalization
- immutable final PDF and snapshots
- void with reason and reissue under a new number
- prior unpaid balance and total amount due presentation
- filing-owner selection when required

Finalized and void invoices are never edited in place.

## Payments Workspace

The Payments workspace provides:

- Outstanding invoices
- Paid invoices
- All Payments ledger
- payment entry
- allocation history
- allocation reversal with reason
- apply available funds
- payment void with reason
- immutable manual payment receipts
- shared financial summaries

Invoice charges remain immutable. Settlement status is derived from the payment ledger.

## Calendar Import And Sessions

Calendar Import shows local sync status and:

- **Sync Calendar** — intelligent full-or-incremental choice based on durable sync state
- **Rebuild Calendar Data from Sheet** — advanced recovery-only full reread with confirmation and backup

The app never triggers the iPhone Shortcut.

The Sessions workspace is a read-only appointment/session ledger. Candidate-only unresolved rows may offer **Send to Review**. Excluded sessions may offer **Return to Review**. Approved or invoiced records are not silently reopened.

## Security And Rendering

All user-controlled values rendered through `innerHTML` must pass through `escapeHtml`, `escapeAttr`, or the escaped formatting helper.

Derived CSV downloads neutralize spreadsheet formula injection.

Write routes require the local write token and appropriate content type. Errors exposed to the browser must not include SQL, filesystem paths, stack traces, secrets, or private internal diagnostics.

## Shared Frontend API Module

The shared frontend request utility lives at `app/jordana_invoice/static/js/api.js`.

It is loaded as a classic script (IIFE) before `review.js` via `<script src="/static/js/api.js"></script>` in `review.html`. The server injects the bootstrap write-token script before `api.js`; the token is captured once at module load time.

### Exports

The module assigns `window.JordanaAPI` with:

- **`api(path, options)`** — async fetch helper. Sets `Content-Type: application/json` on all requests. Adds `X-Jordana-Write-Token` for POST/PUT/PATCH/DELETE. Parses response as JSON. Throws `Error(json.error || "Request failed")` when `!res.ok || json.ok === false`. Returns the parsed JSON object unchanged.
- **`sanitizeUiErrorMessage(message, fallback)`** — sanitizes error messages for UI display. Returns fallback for messages containing `/`, `traceback`, or `select `.

### Token Behavior

- Write token is captured once from `window.__JORDANA_BOOTSTRAP__?.writeToken` at module load time.
- Only POST/PUT/PATCH/DELETE methods receive the token.
- GET requests do not receive the write-token header.
- The token is never placed in URLs, query strings, logs, or error messages.

### Warning Behavior

The `api()` function does not inspect or intercept warning fields. Responses with `warning` or `invoice_staging` fields remain successful. Call sites handle warnings independently (e.g., restore success-with-warning, approval staging warning).

### Direct Fetch Exceptions

Two `fetch()` calls in `review.js` intentionally bypass the shared utility:

1. **Draft PDF preview** (`/api/invoices/{id}/draft-pdf`) — returns a binary blob, not JSON.
2. **Billing relationship setup** (`/api/billing-relationships/setup`) — throws the raw JSON object (not an `Error`) so the catch block can inspect `err.duplicate`, `err.account_id`, and `err.created` for duplicate-relationship handling.

### Tests

Focused tests are in `tests/test_api_util.py`.

## Shared Frontend Overlay Manager

The shared overlay lifecycle manager lives at `app/jordana_invoice/static/js/overlay_manager.js`.

It is loaded as a classic script (IIFE) before `review.js` via `<script src="/static/js/overlay_manager.js"></script>` in `review.html`, after `api.js` and before `review.js`.

### Exports

The module assigns `window.JordanaOverlay` with:

- **`create(config)`** — creates an overlay controller for a specific overlay element. Returns an object with `open`, `close`, `beginPending`, `endPending`, `isPending`, `isOpen`, `getReturnFocus`, and `setReturnFocus` methods.

### Lifecycle Responsibilities

The overlay manager coordinates:

- **Open**: captures the previously focused element, shows the overlay, sets `aria-hidden` to `false`, applies body scroll lock (if configured), binds the keydown handler once, and moves focus to the first focusable control.
- **Pending**: `beginPending(buttons)` prevents a second submission by disabling specified buttons. Returns `false` if already pending.
- **Success/Close**: hides the overlay, sets `aria-hidden` to `true`, removes body scroll lock, unbinds keydown, runs the workflow-provided cleanup callback, restores pending buttons, and safely restores focus to the previously focused element (only if it remains in the DOM and is not hidden or disabled).
- **Failure**: the workflow calls `endPending()` to re-enable controls and clear pending state. The overlay remains open.
- **Body lock**: uses a reference-counted counter so nested overlays do not incorrectly unlock the body. Only resets `body.style.overflow` when the count reaches zero.
- **Idempotent**: `open()` is a no-op if already open. `close()` is a no-op if already closed. Keydown handlers are bound once and not duplicated on repeated opens.

### Migrated Workflows

The following workflows use the overlay manager:

1. **Review approval overlay** — uses `reviewOverlayCtrl` with `bodyLock: false`. The `approvalState` object tracks `submitting` and `candidateId`. `beginPending(["approveBtn"])` disables the approve button during submission. On success, the overlay closes and candidate state clears. On failure, `endPending()` re-enables the button. Success-with-warning (invoice staging) still closes the overlay and shows the warning separately.
2. **Duplicate confirmation** — uses `reviewOverlayCtrl` with `duplicateState` tracking `submitting` and `candidateId`. `beginPending(["duplicateBtn"])` disables the confirm button. On success, the overlay closes, candidate state clears, and the next unresolved item opens when supported.
3. **Restore candidate** — uses `restoreState` tracking `submitting` and `candidateId`. Prevents duplicate submission and concurrent restore of different candidates. On success, the sessions list refreshes. Success-with-warning shows the warning separately. On failure, the button re-enables and a sanitized error is shown.
4. **Billing relationship wizard** — uses `billingWizardState` tracking `submitting`. The wizard modal (`closeBillingModal`) clears `billingWizardState.submitting` on close. The `doSave` function syncs the local `saving` flag with `billingWizardState.submitting`. On failure, the modal stays open, the save button re-enables, and selected chips are preserved.

### Workflows Not Yet Migrated

The following overlays are not yet migrated to the overlay manager and retain their existing lifecycle:

- Payment overlay (`#paymentOverlay`)
- Payment detail overlay (`#paymentDetailOverlay`)
- Line editor modal (`#lineEditorModalOverlay`)
- Invoice finalization preview (inline workspace, not an overlay)

These may be migrated in a future round if they share the same lifecycle requirements.

### Tests

Focused tests are in `tests/test_overlay_manager.py` (111 tests).

## Current Deferred UI Work

The following are not complete:

- polished production dashboard
- invoice email/mail delivery and tracking
- legacy paid-at-session backfill apply UI
- credits, refunds, reconciliation, and month-close workflows
- automatic payer classification
- formal client-versus-non-client schema distinction
- permanent billing-relationship deletion
