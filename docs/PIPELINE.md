# Pipeline

## 1. Capture Calendar Evidence

The Apple Shortcut sends calendar snapshots to Google Apps Script. Google Sheets stores those rows in `Raw_Event_Snapshots`, and `Run_Log` records when capture windows have completed.

Google Sheets is the raw cloud staging and audit layer. The local app never deletes or modifies Sheet rows. Normal capture now uses `past_3_days` and `next_7_days`; deprecated `past_7_days` and `next_2_days` rows remain readable during transition.

## 2. Sync Staged Rows

The Python sync client sends an authenticated POST request to the Apps Script web app:

```json
{
  "record_type": "sync_request",
  "after_ingested_at": "2026-06-21T22:52:34.000Z",
  "limit": 500
}
```

The API key is sent in the POST body from `.env` and is not stored in SQLite or local reports.

Apps Script returns raw rows from `Raw_Event_Snapshots`, sorted by `ingested_at` and `snapshot_key`. `Run_Log` remains an audit and validation signal, but it is not a server-side gate for first-run sync. This lets a new Mac rebuild the local SQLite database from the Sheet even when older Shortcut runs were captured before the current `Run_Log` contract existed.

The local `sync_state` table stores the cursor for `source_name = google_calendar_snapshots`. The cursor advances only after the fetched rows, normalization, review queue updates, and CSV report writes complete successfully.

The application uses one intelligent sync entry point. When no successful
`google_calendar_snapshots` cursor exists, sync is treated as the initial full
Sheet sync and requests all staged rows. Once a successful cursor exists, sync
requests only rows newer than that cursor. Failed initial syncs do not mark
initialization complete, and failed incremental syncs leave the prior cursor in
place.

New successful cursors store both `ingested_at` and `snapshot_key` so rows that
share one timestamp remain page-safe and deterministic. Legacy timestamp-only
cursors remain readable; the next successful sync upgrades the stored cursor
format.

The local review server starts normally, then triggers intelligent sync in the
background. While the app is open, it schedules incremental sync every 15
minutes by default (`JORDANA_CALENDAR_SYNC_INTERVAL_MINUTES`). Scheduled syncs
never run full sync, never trigger the iPhone Shortcut, and skip cleanly if
another sync is already running.

A file-based lock (`DatabaseLock` in `db.py`) prevents overlapping syncs and migrations from running concurrently. If another sync or migration holds the lock, the second operation fails cleanly within a bounded timeout (default 30 seconds) with a clear error message. See `docs/SCHEMA_AUDIT.md` for details.

## 3. Emergency CSV Recovery

The CSV importer remains available for testing or recovery if remote sync fails:

```bash
PYTHONPATH=app .venv/bin/python -m jordana_invoice --db data/jordana_invoice.sqlite3 import-csv path/to/Raw_Event_Snapshots.csv --report data/acceptance_report.md
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

The local importer counts completed runs by grouping rows by `run_id`. A normal recurring run is treated as complete when it has one supported past label and one supported future label:

- `past_3_days` or deprecated `past_7_days`
- `next_7_days` or deprecated `next_2_days`

The June 1-14, 2026 backfill uses `backfill_2026_06_01_through_2026_06_14` and is treated as a coherent one-time historical past batch.

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

Sessions can point to an optional client account, billing party, one or more participant rows, suggested rate rule, and later an approved actual-rate snapshot.

## 10. Local Reports

After a successful sync, the app writes:

- `Reports/Jordana_Client_Sessions_2026.csv`
- `Reports/Jordana_Client_Summary_2026.csv`

Report writes are atomic: each file is written to a temporary file, validated, then moved into place.

## 11. Relationship And Rate Normalization

Phase 2 adds backend support for people, accounts, account members, billing parties, aliases, session participants, rate rules, and review items.

The importer does not create permanent people or accounts from ambiguous titles. Multi-person titles remain reviewable until Jordana confirms the relationship.

Suggested rates come from effective-dated rules and remain separate from approved/actual charged rates. Manual review can save a rate for this session only, future sessions for one participant, or future joint sessions for the exact participant set.

## 12. Future Phases

After June normalization has been reviewed:

- Confirm clients and aliases
- Add client rates
- Approve session rows
- Only then build invoice generation
## Calendar Classification Update

The Shortcut remains all-calendar intake. The existing payload/header field `calendar` maps to SQLite `calendar_name`; do not add a duplicate calendar-name payload key.

`JORDANA_PREFERRED_WORK_CALENDAR=Jordana Work` is optional. When present, that calendar raises work-session confidence but does not auto-approve or guarantee billability. Other calendars are still imported and preserved.

Calendar preferences live in SQLite `calendar_preferences` and support `preferred_work`, `review_normally`, `usually_personal_admin`, and `hidden`.

Hidden means hidden from the normal review queue only. Raw snapshots, event versions, audit records, and manual recovery remain available.

Candidate collapse now prefers stable identity in this order:

1. `calendar_event_id`
2. `event_fingerprint`
3. fallback title/start/end/calendar evidence

Later title corrections, cancellations, time moves, or calendar moves update the current candidate/session while preserving every raw snapshot version.
