# Calendar Shorthand Rules

Calendar data is treated as evidence. The parser proposes interpretations but never silently approves uncertain billing facts.

Title-derived, non-client-specific rules run before client identification.
Duration, weekend/evening category, session type, cancellation/no-show markers,
exclusion indicators, appointment method, and other title-based rules are
preserved even when no client has been matched yet. Client matching is a later
step and must not be required before these safe parsing facts are available for
review.

## Supported Patterns

The parser supports both legacy shorthand and the future pipe-delimited format.

Future format examples:

- `Bonnie Smith | 60 | Phone`
- `Leah Grossman | 30 | FaceTime`
- `Rebecca Colon | 90 | Office`
- `Fred Smith + Bobsey Smith | 60 | House`

### `Bonnie 5`

- Client candidate: `Bonnie`
- Title time shorthand: `5:00`
- Duration: calendar duration if present, otherwise 60 minutes
- Review required: full client name and rate

### `Leah Grossman 630 30`

- Client candidate: `Leah Grossman`
- Title time shorthand: `6:30`
- Duration: 30 minutes
- The explicit title duration overrides the calendar event duration

### `Rebecca colon 630 90`

- Client candidate: `Rebecca Colon`
- Title time shorthand: `6:30`
- Duration: 90 minutes
- `colon` is normalized to `Colon`

### `Raisin??`

- Classification: `unresolved`
- Review required: client and classification

### `Mani pedi 4`

- Classification: `personal`
- Review required once so the phrase can become an exclusion alias

## Duration Precedence

1. Explicit recognized duration at the end of the title
2. Calendar start/end or `duration_minutes`
3. Default 60 minutes

### Standard Duration Choices

Standard billing durations are **30, 60, 90, and 120 minutes**.

Non-standard durations (e.g., 45, 75) are classified as **Custom** and require review confirmation.

## Billing Session Types

Sessions are automatically classified into one of five billing session types based on priority:

1. **Custom** — Manual override only; not auto-derived
2. **Psychotherapy Session / House Call** — Explicit "House Call" text OR nonblank location field
3. **Psychotherapy Session / Weekend** — Saturday or Sunday
4. **Psychotherapy Session / Evening** — Weekday starting at 8:00 PM or later
5. **Psychotherapy Session** — Default for standard weekday daytime sessions

### Priority Rules

- House Call overrides Weekend and Evening
- Weekend overrides Evening (Saturday or Sunday at any time of day = Weekend, not Evening)
- Location-based House Call detection suggests confirmation but does not auto-approve

### Appointment Methods vs Billing Types

**Appointment methods** (Office, Phone, FaceTime) are internal evidence, not billing types.
All three are treated identically for billing rates.

**Billing session types** determine the invoice line description and any premium considerations.

### Late Evening Warning

Sessions starting at 10:00 PM or later trigger a review warning to verify the time is correct.

## Time Handling

Calendar `start_at` is authoritative.

Title time shorthand is only used to verify agreement. If the shorthand time does not match the calendar start time, the candidate is sent to review with `time_discrepancy`.

## Classifications

- `client_session`
- `administrative`
- `personal`
- `cancelled`
- `no_show`
- `nonbillable`
- `duplicate`
- `unresolved`

## Confidence and Review

Every candidate includes:

- `classification`
- `confidence`
- `confidence_label`
- `explanation`
- `fields_requiring_review`
- `unresolved_fields`
- `review_reasons`

Service mode aliases such as `Phone`, `Call`, `FaceTime`, `FT`, `Office`, `In Person`, `House`, `House Call`, and `Home Visit` normalize to `phone`, `facetime`, `office`, or `house_call`.

Ambiguous records must stay reversible. A future UI should show the raw event alongside the proposed interpretation.

Multi-person shorthand such as `Fred + Bobsy` is only a participant candidate. It must not automatically create a permanent shared account or bill-to record without review.

### `for <reference>` titles

Titles ending in `for <reference>` (e.g., `Caitlin Schneider 530 for Sage`) preserve the reference as unresolved evidence. The parser does not infer a participant, bill-to, or relationship from the reference name. The reference appears in candidate evidence for manual review only.
## Structured Title Compatibility

Structured pipe titles are parsed before legacy shorthand:

```text
Full Name | Minutes | Session Type
Full Name | Time | Minutes | Session Type
Full Name | Time | Minutes | Session Type | Cancelled
Full Name | Time | Minutes | Session Type | No Show
```

Legacy shorthand remains supported after structured parsing, including forms such as `Fred 830`, `Leah Grossman 630 30`, `Rebecca colon 630 90`, and `Bobsy and Fred 6`.

Calendar start time remains authoritative. Optional title time is used only to create a warning when it disagrees exactly by hour/minute.
