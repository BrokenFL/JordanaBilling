# Section-Level Saves

The Review Queue resolves one calendar event at a time. Each section can be saved independently. No section-level save approves a session.

Candidate-only calendar records may not have a `sessions` row yet. The first valid section save promotes the candidate into exactly one reviewable session. Later saves and approval reuse that same `sessions.candidate_id` link.

## Save Client(s)

**Save Client(s)** confirms who attended the session.

It:

- persists one or more participants immediately
- links exact existing people when the match is unique
- may create a permanent person only after confirmed first and last names
- leaves incomplete or ambiguous names as reviewable participant text
- generates person codes only for confirmed complete names
- writes audit history
- refreshes Bill To defaults, rate suggestions, unresolved fields, checklist state, and review status

Parser-proposed names may appear before saving, but they are not permanent people merely because they are displayed.

Saving an empty participant list intentionally clears attendance. The parser proposal must not be reinserted after that explicit save.

When exactly one participant is confirmed, the save may learn an approved calendar alias for future smart prefill. Multi-person or ambiguous titles do not create one combined alias or person.

## Save Bill To

**Save Bill To** selects the billing party for this session.

Bill To is the person or organization responsible for receiving and paying the invoice. They do not have to be a participant.

The normal review UI may offer confirmed session participants and valid existing payer defaults. Advanced parent, spouse, organization, or shared-billing setup belongs in Billing Relationships.

Saving Bill To:

- persists the session-specific payer immediately
- writes audit history
- refreshes dependent rate and readiness suggestions
- does not approve the session
- does not create an account unless the explicit Billing Relationships workflow does so
- does not overwrite approved sessions

A deliberate session-specific Bill To is preserved unless an explicit user action or approved rule changes it before approval.

## Save Billing Relationship

**Save Billing Relationship** is an advanced workflow for payer and covered-client structure.

It may persist:

- invoice recipient
- covered clients
- billing delivery details
- default payer
- optional filing-owner default
- active or inactive relationship state

Rules:

- the payer is not automatically included among covered clients
- changing payer type clears stale covered-client selections
- selected-client chips are the source of truth
- removing a selected client makes that person searchable again
- exact active duplicates are blocked or reused explicitly
- saving is transactional
- approved sessions and finalized invoices are never silently rewritten

After a successful relationship save, reopening or refreshing review exposes the saved relationship. Jordana still confirms Bill To for the individual session.

## Save Session Draft

**Save Session Draft** saves session facts without approval.

Editable fields include:

- approved duration choice and custom minutes when needed
- one of the five billing session types
- custom service description or code when applicable
- editable actual-rate proposal
- rate-change scope
- Payment Handling
- billing treatment for cancelled or no-show appointments
- paid-at-session details when that handling is selected

Time category is not a normal editable field. It is derived from the authoritative calendar date and start time as `standard`, `evening`, or `weekend`; weekend overrides evening.

The five active billing session types are:

1. Psychotherapy Session
2. Psychotherapy Session / House Call
3. Psychotherapy Session / Weekend
4. Psychotherapy Session / Evening
5. Custom

Office, Phone, and FaceTime remain source appointment methods, not selectable billing session types.

Saving a rate change must distinguish:

- this session only
- future sessions for one participant
- future joint sessions for the exact participant combination

Approved historical rates are never silently rewritten.

## Approval

**Approve Session** is separate from every section save.

Approval validates at minimum:

- confirmed participants
- Bill To
- duration
- one of the five billing session types
- derived time category
- approved or actual charged rate
- Payment Handling
- cancelled or no-show billing treatment when applicable
- paid-at-session payment details when applicable

Approval snapshots the charged rate and approved values, writes audit history, and advances review state.

For invoice billing, successful approval attempts monthly invoice staging. A staging warning does not roll back approval and is shown separately.

For paid-at-session, successful approval idempotently creates or validates one posted payment and allocation, and invoice staging is not required.

After successful approval, the overlay closes, stale state clears, the item refreshes or is removed, focus is restored, and no resubmittable form remains. On genuine failure, the overlay stays open, controls re-enable, and a sanitized error is shown.

## Save Failure Behavior

On any genuine section-save failure:

- keep the overlay open
- re-enable the action
- show a sanitized error
- do not display stale browser state as saved
- do not treat a partial write as complete
- preserve raw calendar evidence
