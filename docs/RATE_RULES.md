# Rate Rules

Rates are stored in SQLite as effective-dated `rate_rules`. Suggested rates are not approved billing values.

## Matching Inputs

The normalizer considers participant combinations, person, account, duration, billing session type, time category, and effective date.

### Billing Session Types (for rate matching)

Rate rules store `billing_session_type` to match the normalized session type. Rules with a specific billing session type only apply to sessions with that same type. Rules with a `NULL` billing session type apply to any billing session type.

- `psychotherapy` — Standard session
- `psychotherapy_house_call` — House call
- `psychotherapy_weekend` — Weekend session
- `psychotherapy_evening` — Evening session (weekday >= 8 PM)
- `custom` — Custom session type

### Appointment Methods (internal evidence)

Office, Phone, and FaceTime are **appointment methods**, not billing session types. They are treated identically for rate matching purposes. The rate engine does not distinguish among these three methods. The `service_mode` and `rate_group` columns remain in `rate_rules` for backward compatibility but are not the primary matching dimension for the new billing session types.

Legacy service modes (`phone`, `facetime`, `office`) and rate groups (`remote`, `office`) are preserved for historical rate rules but are not exposed as new selectable billing types.

### Time Categories

- `standard`
- `evening`
- `weekend`
- `weekend_evening`

Rate matching is exact on both dimensions. A rule with `time_category = 'standard'` matches only standard sessions; a rule with `time_category = 'evening'` matches only evening sessions; and so on. There is no fallback from one time category to another.

`billing_session_type` matching is also exact. A `psychotherapy_evening`, `psychotherapy_weekend`, or `psychotherapy_house_call` session will not fall back to a base `psychotherapy` rule. Each modified session type requires its own rate rule. A rule with `billing_session_type = NULL` remains a wildcard that matches any session type.

## Precedence

1. Approved session override (the session's manually saved or approved `approved_rate_cents`)
2. Exact participant-combination exception
3. Person-specific matching rule
4. Billing-relationship matching rule
5. Global/default matching rule
6. No match means rate review is required

Approved session rates are copied to the session. Later rate-card changes must not rewrite historical approved rates, finalized invoice snapshots, or payment history.

## Effective Date Behavior

- A rule is only effective for sessions on or after `effective_from`.
- A session dated before the rule's effective date does not receive the rule.
- Sessions dated on or after the effective date receive the rule unless a higher-priority exception applies.
- Adding a new default rule immediately refreshes suggestions for existing unapproved sessions; approved and excluded sessions are never rewritten.

## Manual Rate Changes

When Jordana changes the suggested rate during review, the UI asks `Apply this rate to:`.

- This session only stores the edited amount as the session's approved rate, marks the source as `manual_override`, and does not create a future rule.
- Future sessions for this client stores the edited amount for the current session and creates or updates an effective-dated person-specific exception.
- Future joint sessions for these clients stores the edited amount for the current session and creates or updates an effective-dated exception for the exact participant set.

Joint matching is order-independent and exact. Fred Colin + Bobsy Colin matches Bobsy Colin + Fred Colin, but does not match Fred alone.

## Weekend Evening

Weekend-evening sessions are ambiguous until policy is configured. The default policy is `manual_review`.

Supported policy values: `use_weekend`, `use_evening`, `use_combined_rate`, `use_highest_rate`, `manual_review`.

## Developer Commands

```bash
PYTHONPATH=app python -m jordana_invoice --db data/jordana_invoice.sqlite3 seed-rate-rule --amount 150 --effective-from 2026-01-01 --duration-minutes 60 --billing-session-type psychotherapy
PYTHONPATH=app python -m jordana_invoice --db data/jordana_invoice.sqlite3 set-rate-policy weekend_evening_policy manual_review
```

## Review UI Rate Card

The local Rate Card supports amount, session length, session type, time category, applies-to scope, and effective date. Applies-to can be Everyone, One Client, Clients Together, or Billing Relationship. Specific scopes require an explicit resolved UUID-backed selection in the UI; the app does not silently pick the first search result or fall back to Everyone.

Custom rate rules can store `custom_service_description` and optional `custom_service_code`. Custom matching uses code first when present, otherwise a normalized description match. Custom rows should display the entered description and actual minutes everywhere instead of a generic `Custom` label.

Replace ends the old active rule on the day before the new `effective_from` date and creates a new same-scope rule. End sets `effective_through` without deleting history. Both actions immediately recalculate unapproved session suggestions and refresh the local reports. If no active rule still matches, stale rule-derived suggestions are cleared and the session returns to `needs_rate`.

Client records reuse this same `rate_rules` system for person-specific overrides. A client override is just a `person_id`-scoped active rule with the same matching dimensions and precedence as any other person-specific exception. If no client-specific rule matches, the standard Rate Card still applies.

Creating a rule with the same scope, dimensions, and effective date as an existing active rule is blocked to prevent duplicate active rules. Validation errors and API failures are surfaced in the UI instead of failing silently.

The Rate Card form is responsive: all controls and the **Add Rate Rule** button remain visible and clickable at normal laptop/browser widths.

Historical approved session rates are preserved in `sessions.rate_cents_snapshot` at approval time and must not be rewritten by later rate-card edits. `approved_rate_source` and `approved_rate_rule_id` preserve the source used for the actual charged amount. Finalized invoice line amounts are frozen snapshots and are never reconstructed from current rate rules.
## Calendar Status Interaction

Rates are not read from calendar titles. Structured titles may include participants, optional title time, duration, session type, and optional `Cancelled` or `No Show` status only.

Cancelled/no-show appointment status does not itself create a payment status. If the reviewed billing treatment is `billable`, the approved/actual charged rate is preserved on the session exactly like any other approved session. If the treatment is `not_billable` or `waived`, the event remains preserved without becoming an ordinary billable completed session.

Invoice amounts copy `approved_rate_cents`/`rate_cents_snapshot`; they are never reconstructed from current rate rules. Finalized line amounts remain unchanged after future rate changes.
