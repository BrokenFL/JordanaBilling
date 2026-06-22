# Rate Rules

Rates are stored in SQLite as effective-dated `rate_rules`. Suggested rates are not approved billing values.

## Matching Inputs

The normalizer considers account, person, duration, service mode, rate group, time category, and effective date.

Service modes: `phone`, `facetime`, `office`, `house_call`, `unknown`.

Rate groups: `remote`, `office`, `house_call`.

Time categories: `standard`, `evening`, `weekend`, `weekend_evening`.

## Precedence

1. Approved session override
2. Person-specific matching rule
3. Account-specific matching rule
4. Global matching rule
5. No match means rate review is required

Approved session rates are copied to the session. Later rate-card changes must not rewrite historical approved rates.

## Weekend Evening

Weekend-evening sessions are ambiguous until policy is configured. The default policy is `manual_review`.

Supported policy values: `use_weekend`, `use_evening`, `use_combined_rate`, `use_highest_rate`, `manual_review`.

## Developer Commands

```bash
PYTHONPATH=app python -m jordana_invoice --db data/jordana_invoice.sqlite3 seed-rate-rule --amount 150 --effective-from 2026-01-01 --duration-minutes 60 --rate-group remote
PYTHONPATH=app python -m jordana_invoice --db data/jordana_invoice.sqlite3 set-rate-policy weekend_evening_policy manual_review
```
