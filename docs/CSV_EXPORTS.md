# CSV Exports

SQLite is the system of record. CSV files are review and handoff exports.

## Candidate And Yearly Session Export

Current file:

```text
Reports/Jordana_Client_Sessions_2026.csv
```

Columns:

- `session_date`
- `start_time`
- `raw_calendar_title`
- `classification`
- `confidence`
- `candidate_person_names`
- `candidate_account_code`
- `candidate_account_name`
- `participant_names`
- `billing_party_name`
- `duration_minutes`
- `calendar_duration_minutes`
- `service_mode`
- `rate_group`
- `time_category`
- `is_evening`
- `is_weekend`
- `suggested_rate`
- `approved_rate`
- `payment_status`
- `review_status`
- `review_reasons`
- `invoice_number`

## Account Summary Export

Current file:

```text
Reports/Jordana_Client_Summary_2026.csv
```

Columns:

- `account_code`
- `account_name`
- `participant_names`
- `session_count`
- `billed_amount`
- `paid_amount`
- `outstanding_amount`
- `last_session_date`

Exports are written atomically: temporary file first, validation second, replacement last.
