# Calendar Shorthand Rules

Calendar data is treated as evidence. The parser proposes interpretations but never silently approves uncertain billing facts.

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

Recognized explicit durations include common billing increments such as 30, 45, 60, and 90.

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
