# CSV Exports

SQLite is the system of record. CSV files are review and handoff exports.

## Export types

There are four CSV export types. Each can be generated on demand via the localhost review server or written to disk by `write_reports()` during sync.

| Type | Display name | Year required | Disk filename |
|---|---|---|---|
| `sessions` | Client Sessions | Yes | `Jordana_Client_Sessions_{year}.csv` |
| `summary` | Client Summary | Yes | `Jordana_Client_Summary_{year}.csv` |
| `simple` | Session Log | Yes | `Jordana_Session_Log_{year}.csv` |
| `appointments` | All Appointments | Yes | `Jordana_All_Appointments.csv` |

## Client Sessions Export

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
- `rate_source`
- `payment_status`
- `appointment_status`
- `review_status`
- `review_reasons`
- `invoice_number`

## Client Summary Export

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
- `paid_at_session_amount`
- `unpaid_amount`
- `last_session_date`

## Session Log Export

Current file:

```text
Reports/Jordana_Session_Log_2026.csv
```

Columns:

- `Date`
- `Time`
- `Client / Participants`
- `Session Length`
- `Session Type`
- `Time Category`
- `Rate`
- `Payment Status`
- `Review Needed`

## All Appointments Export

Current file:

```text
Reports/Jordana_All_Appointments.csv
```

The legacy disk export contains all years cumulatively. The on-demand API accepts a `year` parameter and filters rows to that year.

Columns:

- `Date`
- `Time`
- `Calendar Title`
- `Client / Participants`
- `Session Length`
- `Session Type`
- `Rate`
- `Payment Status`
- `Review Status`
- `Appointment Status`
- `Classification`
- `Calendar`

## CSV Injection Neutralisation

All derived CSV exports apply the `csv_safe()` helper from `util.py` to every cell value before writing. This prevents CSV formula injection in spreadsheet applications.

Values whose first non-whitespace character is `=`, `+`, `-`, or `@` are prefixed with a single apostrophe (`'`) so the spreadsheet treats them as text rather than a formula. Numeric, date, and normal text values pass through unchanged.

This applies to both disk exports (via `write_rows()`) and on-demand API downloads (via `stream_csv()`).

## Disk exports

Exports are written atomically: temporary file first, validation second, replacement last. Approved historical amounts come from `sessions.approved_rate_cents` or `sessions.rate_cents_snapshot`, not from recomputing current rate rules.

The `Reports/` directory is gitignored and must never be committed.

## On-demand download API

The localhost review server provides two endpoints for report access without writing to disk:

### `GET /api/reports`

Returns report metadata, available years, and a default year:

```json
{
  "reports": [
    {"type": "sessions", "display_name": "Client Sessions", "description": "...", "year_required": true},
    {"type": "summary", "display_name": "Client Summary", "description": "...", "year_required": true},
    {"type": "simple", "display_name": "Session Log", "description": "...", "year_required": true},
    {"type": "appointments", "display_name": "All Appointments", "description": "...", "year_required": true}
  ],
  "years": [2026, 2025],
  "default_year": 2026
}
```

`default_year` is the current Eastern year when present in the data, otherwise the newest available year, otherwise the current Eastern year.

### `GET /api/reports/download?type={type}&year={year}`

Returns the generated CSV as an attachment with:

- `Content-Type: text/csv; charset=utf-8`
- `Content-Disposition: attachment; filename="Jordana_..."`

The filename follows the existing naming convention. The API does not serve files from `Reports/` or accept user-provided filenames. On-demand generation does not write to disk.
