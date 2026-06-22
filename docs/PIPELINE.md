# Pipeline

## 1. Capture Calendar Evidence

The Apple Shortcut sends calendar snapshots to Google Apps Script. Google Sheets stores those rows in `Raw_Event_Snapshots`, and `Run_Log` records when both capture windows have completed.

Google Sheets is the raw cloud staging and audit layer. The local app never deletes or modifies Sheet rows.

## 2. Sync Completed Rows

The Python sync client sends an authenticated POST request to the Apps Script web app:

```json
{
  "record_type": "sync_request",
  "after_ingested_at": "2026-06-21T22:52:34.000Z",
  "limit": 500
}
```

The API key is sent in the POST body from `.env` and is not stored in SQLite or local reports.

Apps Script returns only rows whose `run_id` exists in `Run_Log` with status `complete`. Rows are sorted by `ingested_at` and `snapshot_key`.

The local `sync_state` table stores the cursor for `source_name = google_calendar_snapshots`. The cursor advances only after the fetched rows, normalization, review queue updates, and CSV report writes complete successfully.

## 3. Emergency CSV Recovery

The CSV importer remains available for testing or recovery if remote sync fails:

```bash
PYTHONPATH=app python -m jordana_invoice --db data/jordana_invoice.sqlite3 import-csv path/to/Raw_Event_Snapshots.csv --report data/acceptance_report.md
```

CSV imports and remote sync both use the same parser and duplicate protection.

## 4. Preserve Raw Rows

Every imported row is written to `raw_calendar_snapshots`. The importer stores:

- Sheet metadata such as `run_id`, `batch_name`, `capture_window`, and `captured_at`
- Calendar facts such as title, start, end, duration, location, notes, and calendar name
- Full normalized raw JSON
- A source hash and `snapshot_key` to avoid double-importing rows

Raw rows are never edited in place.

## 5. Validate Completed Runs

Apps Script filters sync rows to completed runs using `Run_Log`. The local importer also counts completed runs by grouping rows by `run_id`. A run is treated as complete when it has both:

- `next_2_days`
- `past_7_days`

This is a local validation signal only. It does not approve billing.

## 6. Collapse Duplicate Evidence

Multiple raw rows can describe the same appointment. The importer collapses rows by:

- `calendar_event_id` when present
- Otherwise `event_fingerprint`, title, start, and end

The latest raw snapshot remains linked as the current candidate, while all raw rows remain preserved. Remote sync additionally skips any `snapshot_key` already present locally.

## 7. Parse, Classify, And Categorize

The parser produces a `calendar_event_candidates` row with:

- Classification
- Confidence score
- Confidence label
- Explanation
- Fields requiring review
- Proposed client candidate
- Candidate participant names
- Proposed start time and duration
- Service mode and rate group
- Evening/weekend time category
- Parser payload

## 8. Create Review Items

Any uncertain or human-dependent item goes to `review_queue`. Examples:

- Unknown full client name
- Missing rate
- Question marks
- Personal-looking exclusions that should become aliases
- Time shorthand that disagrees with calendar start time
- Unknown service mode
- Missing rate
- Missing billing party

Review decisions are stored in SQLite. CSV exports are not the review system of record.

## 9. Proposed Sessions

Likely client sessions are written to `sessions` as `proposed` and `needs_review`. They are not final invoice rows.

Sessions can point to a client account, billing party, one or more participant rows, suggested rate rule, and later an approved rate snapshot.

## 10. Local Reports

After a successful sync, the app writes:

- `Reports/Jordana_Client_Sessions_2026.csv`
- `Reports/Jordana_Client_Summary_2026.csv`

Report writes are atomic: each file is written to a temporary file, validated, then moved into place.

## 11. Relationship And Rate Normalization

Phase 2 adds backend support for people, accounts, account members, billing parties, aliases, session participants, rate rules, and review items.

The importer does not create permanent people or accounts from ambiguous titles. Multi-person titles remain reviewable until Jordana confirms the relationship.

Suggested rates come from effective-dated rules and remain separate from approved rates.

## 12. Future Phases

After June normalization has been reviewed:

- Confirm clients and aliases
- Add client rates
- Approve session rows
- Only then build invoice generation
