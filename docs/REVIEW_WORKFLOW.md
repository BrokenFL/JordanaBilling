# Review Workflow

Calendar and Google Sheet rows are source evidence. Review decisions live in SQLite. Normal review never edits or deletes raw calendar snapshots.

## Routine Review

The visible workflow is:

1. Participants
2. Bill To
3. Duration
4. Session type
5. derived time category
6. Rate
7. Payment Handling
8. Approve

Routine review does not expose backend Client / Family Account fields, account codes, household labels, or membership roles.

## Participants

Participants are permanent people connected to the session as attendees.

Parser-proposed names may appear before saving, but display alone does not create a person. **Save Client(s)** is the confirmation boundary.

- Exact unique active-person matches may be linked.
- A new person requires confirmed first and last names.
- Incomplete or ambiguous names remain reviewable text.
- Person codes are created only after complete names are confirmed.
- Saving an empty participant list intentionally clears attendance.
- Exactly one confirmed participant may create or update an approved calendar alias.
- Multi-person titles never create one combined person or alias.

## Bill To

Bill To is the person or organization responsible for receiving and paying the invoice. Bill To does not have to be a participant.

An existing deliberate session payer is preserved. Valid relationship defaults may be suggested for unapproved sessions, but ambiguous payer choices remain unresolved. Automatic matching never creates a billing party, and approved sessions are never silently reassigned.

### Organization Bill To Eligibility

When a billing relationship with an organization payer is saved, that organization becomes immediately eligible as Bill To for covered clients in the review UI. The candidate payload includes `bill_to_options` listing active billing-party records connected to confirmed participants via `client_accounts`. The UI renders these as `party:`-prefixed option values.

`refresh_candidate_suggestions` auto-assigns the relationship default account/billing-party to a session only when:
- the session has no account_id and no billing_party_id, and
- the session is not approved, and
- exactly one active relationship covers all confirmed participants.

Deliberate session-specific Bill To, approved session Bill To, and finalized invoice snapshots are never overwritten. Unrelated organizations are never offered.

### Invoice Delivery Contact

The billing relationship editor has two visibly separate sections:

1. **Save invoices under** — filing owner only (payer, organization, or covered clients). No arbitrary unrelated contacts.
2. **Billing delivery** — Send invoice to, with existing-person search, Add invoice contact form, preferred delivery method, and delivery email/phone/address fields.

The delivery contact is separate from Bill To, payer, Participants, covered clients, and filing owner.

- **Existing person search**: The "Find existing person" button searches the full active people directory via `/api/people?q=...`. Unrelated people are allowed here because this is delivery-contact selection, not payer or filing-owner selection. Selecting a person stores `delivery_contact_person_id` and does not alter payer identity, Bill To, filing owner, covered clients, or Participants.
- **New contact creation**: The "Add invoice contact" form includes first name, last name, display name, email, phone, and address fields (line 1, line 2, city, state, postal code). The existing `create_person` duplicate safeguard (case-insensitive display-name match) is reused. Save is transactional with the rest of the relationship update.
- **Organization payer**: The delivery contact person is linked via `delivery_contact_person_id` (canonical) and `person_id` (historical). The contact does not become a covered client, participant, payer, or Bill To.
- **Person payer**: The delivery contact person is linked via `delivery_contact_person_id`. The payer's `person_id` is preserved. The contact's delivery details (name, email, phone, address) are stored on the billing-party record.
- Delivery method, email, phone, and address persist on the billing party and are shown when the relationship is reopened.
- Future draft invoices inherit the delivery contact and delivery method from the billing party.
- Finalized invoices remain immutable; changing the relationship delivery contact affects future/unresolved drafts only.

## Session Details

The active duration choices are 30, 60, 90, 120, and Custom. Custom requires actual minutes.

The active billing session types are:

1. Psychotherapy Session
2. Psychotherapy Session / House Call
3. Psychotherapy Session / Weekend
4. Psychotherapy Session / Evening
5. Custom

Office, Phone, and FaceTime are source appointment methods, not selectable billing session types.

Time category is derived from the authoritative calendar date and start time as `standard`, `evening`, or `weekend`. It is not a normal editable field. Weekend overrides evening.

## Rate

A confirmed rate change must choose one scope:

- this session only
- future sessions for one person
- future joint sessions for the exact participant combination

Rate priority is:

1. session-specific approved override
2. exact participant-combination exception
3. person exception
4. billing-relationship exception
5. global or default rule

Approval permanently stores the charged rate. Later rate changes do not rewrite approved sessions.

## Payment Handling

The visible choices are:

- **Invoice billing** — after approval, the session is eligible for monthly draft invoice staging
- **Paid at session** — approval requires the received amount, payment date, and supported method; approval idempotently creates or validates one posted payment and allocation and skips invoice staging

Payment Handling is separate from appointment status and cancelled or no-show billing treatment.

The legacy paid-at-session backfill analyzer remains dry-run only. Historical backfill apply is not implemented.

## Cancelled And No-Show Sessions

Cancelled and no-show appointments remain preserved and reviewable. They require a separate billing-treatment decision:

- `billable`
- `not_billable`
- `waived`
- `unresolved`

Late cancellation supports an additional `bill_full_fee` and `custom_fee` treatment.

When billing treatment is `waived` or `not_billable` and the approved rate is `$0.00`, the zero rate is valid and persists through save, reload, approval, invoice staging, and finalization. The rate card suggestion may still show the standard fee informationally, but it never replaces the saved zero. Zero rates for ordinary billable sessions, full-fee cancellations, or custom-fee cancellations remain invalid.

Calendar start time is authoritative. Parsed title time remains evidence and may create a warning.

## Section-Level Saves

The focused overlay uses independent actions:

- **Save Client(s)**
- **Save Bill To**
- **Save Session Draft**
- **Approve Session**

No section save approves a session.

A candidate-only record may not have a session yet. The first valid section save promotes it into exactly one session. Later saves and approval reuse that same candidate-to-session link.

After a successful save, dependent payer, rate, unresolved-field, checklist, and readiness suggestions refresh while unrelated unsaved browser fields are preserved.

On failure, the overlay stays open, the action re-enables, a sanitized error is shown, and stale browser state is not presented as saved.

See `docs/SECTION_LEVEL_SAVES.md` for the complete contract.

## Approval

Approval validates participants, Bill To, duration, one of the five session types, derived time category, actual charged rate, Payment Handling, and any required cancelled/no-show or paid-at-session details.

### Future-Appointment Gating

Future appointments may be opened and reviewed. Jordana may confirm Participants, Bill To, duration, session type, time category, rate, and payment status. The Approve action remains disabled until the event end time has passed. The backend enforces the same rule even if the frontend is bypassed.

- The actual event end timestamp in the configured Eastern timezone is used, not only the calendar date.
- The UI shows a message such as: `This appointment is scheduled for July 1 at 3:00 PM. It can be approved after the session ends.`
- Once the end time passes, the item becomes approvable without recreating it.
- Already-approved future sessions from prior versions are preserved with their audit history.

During submission, the approval action is disabled.

For invoice billing:

1. approval commits first
2. monthly invoice staging is attempted
3. a staging warning never rolls back approval
4. the warning is shown separately

For paid at session:

1. approval commits the session and financial action transactionally
2. one payment and allocation are created or validated idempotently
3. invoice staging reports that it is not required

After success, stale state clears, the overlay closes, the item refreshes or is removed, focus is restored, and confirmation appears. On genuine failure, the overlay remains open and controls re-enable.

## Duplicate Resolution

The preferred action is **Confirm Duplicate & Next**.

Success persists the decision, closes the overlay, clears stale state, refreshes or removes the item, and opens the next unresolved item when supported. Failure keeps the overlay open and shows a sanitized error.

## Billing Relationships

Billing Relationships is payer-centered:

1. choose payer type and payer
2. explicitly select covered clients
3. review and save

The payer is not automatically covered. Session participants remain selectable but are not silently preselected. Changing payer clears stale covered-client selections. Selected-client chips are the source of truth.

Saving persists transactionally to SQLite. Approved sessions are never silently rewritten. Deactivation preserves history; permanent deletion is not implemented.

### Responsive Panels

On screens at or below 1800px, the Billing Relationship editor, organization record, invoice editor, and payment workspace become fixed bounded sheets with opaque backgrounds, a shared dimmed backdrop (`#workspaceBackdrop`), body scroll locking, background inert/aria-hidden, focus restoration, internal scrolling, and Close buttons. The underlying page is not interactive while a sheet is open. At or below 760px, sheets expand to near-full-width with safe viewport margins. See [Billing Relationships](CLIENTS_AND_ACCOUNTS.md#responsive-panel-behavior) for details.

## Candidate Identity And Repair

Candidate identity resolution uses exact event ID, exact fingerprint, then conservative exact structural matching. Ambiguous identity remains in review. Raw snapshots and existing identity evidence are preserved.

### Calendar Event Revision Handling

The same appointment may be edited in Apple Calendar and arrive later with a changed title. The system uses the stable calendar event identifier to recognize revisions of the same event.

- Every raw snapshot is preserved for audit history.
- The newest valid snapshot becomes the current source evidence for the unresolved review candidate.
- Two revisions of the same event do not create two active operational sessions.
- Approved sessions are not silently overwritten.
- If an already-approved session's source event later changes, a visible source-change warning review item is created instead of rewriting approved values.
- Event absence from one capture window alone does not prove deletion/cancellation.
- The logic is additive, idempotent, and reversible.

### Ambiguous Title Review Routing

Ambiguous but recognizable calendar titles (e.g. `Leah Grossman 630 38`, `Sage Burkhead 4 zoom`, `Fred 60`) are routed to the Review Queue with safe participant guesses.

- The parser extracts a leading person name as a participant guess where confidence is high.
- Calendar event start/end timestamps remain authoritative; title time tokens are hints only.
- Unknown or conflicting trailing text is preserved as administrative review context (not clinical interpretation).
- Any unresolved, conflicting, or extra token routes the item into the Review Queue.
- No silent auto-approval occurs.
- No duplicate operational session is created.

Duplicate repair supports a sanitized dry-run, explicit apply, verified backup, idempotent application, and guarded reversal. Approved, invoiced, paid, audited, and raw-evidence records are protected. Reversal is refused after later edits make it unsafe.

## Review Queue And Sessions

The Review Queue offers Needs Review, Approved, and Excluded filters. Needs Review excludes approved and excluded records.

The Sessions workspace is a read-only ledger. Eligible candidate-only records may be sent to review, and excluded sessions may be returned to review. Approved or invoiced records are not silently reopened.

## Safety

All browser-visible errors are sanitized. User-controlled HTML is escaped. Write routes require the local write token and correct content type.

The UI must not expose SQL, stack traces, filesystem paths, credentials, API keys, or private internal diagnostics.

## Shared Overlay Manager

The review overlay, duplicate confirmation, restore candidate, and billing relationship wizard use a shared overlay lifecycle manager at `app/jordana_invoice/static/js/overlay_manager.js`. The manager coordinates focus capture/restoration, ARIA state, body scroll lock, keydown binding, and pending-state button disabling. Each workflow owns its own state object (`approvalState`, `duplicateState`, `restoreState`, `billingWizardState`) with `submitting` and `candidateId` fields. See `docs/UI_SPEC.md` for the full overlay manager specification.

## Related Documents

- `docs/UI_SPEC.md`
- `docs/SECTION_LEVEL_SAVES.md`
- `docs/CLIENTS_AND_ACCOUNTS.md`
- `docs/ALIAS_LEARNING.md`
- `docs/RATE_RULES.md`
- `docs/INVOICE_LIFECYCLE.md`
- `docs/SCHEMA_AUDIT.md`
