# Rate Rules

Rates are stored in SQLite as effective-dated `rate_rules`. Suggested rates are not approved billing values.

## Matching Inputs

The normalizer considers participant combinations, person, account, duration, service mode, rate group, time category, and effective date.

Service modes: `phone`, `facetime`, `office`, `house_call`, `unknown`.

Rate groups: `remote`, `office`, `house_call`.

Time categories: `standard`, `evening`, `weekend`, `weekend_evening`.

## Precedence

1. Approved session override
2. Exact participant-combination exception
3. Person-specific matching rule
4. Account-specific matching rule
5. Global/default matching rule
6. No match means rate review is required

Approved session rates are copied to the session. Later rate-card changes must not rewrite historical approved rates.

## Manual Rate Changes

When Jordana changes the suggested rate during review, the UI asks `Apply this rate to:`.

- This session only stores the edited amount as the session's approved rate, marks the source as `manual_override`, and does not create a future rule.
- Future sessions for this participant stores the edited amount for the current session and creates or updates an effective-dated person-specific exception.
- Future joint sessions for these participants stores the edited amount for the current session and creates or updates an effective-dated exception for the exact participant set.

Joint matching is order-independent and exact. Fred Colin + Bobsy Colin matches Bobsy Colin + Fred Colin, but does not match Fred alone.

## Weekend Evening

Weekend-evening sessions are ambiguous until policy is configured. The default policy is `manual_review`.

Supported policy values: `use_weekend`, `use_evening`, `use_combined_rate`, `use_highest_rate`, `manual_review`.

## Developer Commands

```bash
PYTHONPATH=app python -m jordana_invoice --db data/jordana_invoice.sqlite3 seed-rate-rule --amount 150 --effective-from 2026-01-01 --duration-minutes 60 --rate-group remote
PYTHONPATH=app python -m jordana_invoice --db data/jordana_invoice.sqlite3 set-rate-policy weekend_evening_policy manual_review
```

## Review UI Rate Card

The local Rate Card supports amount, session length, session type, time category, applies-to scope, and effective date. Applies-to can be everyone, a specific account, or a specific person. Joint exceptions created from review are stored as `rate_rules` plus `rate_rule_participants` rows.

Historical approved session rates are preserved in `sessions.rate_cents_snapshot` at approval time and must not be rewritten by later rate-card edits. `approved_rate_source` and `approved_rate_rule_id` preserve the source used for the actual charged amount.
