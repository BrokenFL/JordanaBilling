# Calendar Integration

This integration keeps the existing architecture:

Apple Calendar -> iPhone Shortcut -> Google Apps Script -> existing Google Spreadsheet -> `Raw_Event_Snapshots` and `Run_Log` -> Python sync/import -> local SQLite.

## Normal Capture Window

The normal Shortcut should capture all non-all-day events from all calendars:

- 3 days backward with `capture_window=past_3_days`
- 7 days forward with `capture_window=next_7_days`
- timezone `America/New_York`
- payload version `2`

Deprecated transition labels remain accepted by the local importer and the repo Apps Script source:

- `past_7_days`
- `next_2_days`
- `legacy`

## June Backfill

The one-time backfill Shortcut is:

```text
Jordana Calendar Backfill - June 1-14, 2026
```

It covers `2026-06-01T00:00:00-04:00` through `2026-06-14T23:59:59-04:00`, inclusive, with:

```text
capture_window=backfill_2026_06_01_through_2026_06_14
```

It must not recur automatically and must not be run against the live calendar until Jordana launches it intentionally.

## Apps Script

Sanitized source now lives at:

```text
integrations/apps_script/Code.gs
```

The source preserves the existing sheet tabs:

- `Raw_Event_Snapshots`
- `Run_Log`

It reads deployment-specific values from Script Properties:

- `INGEST_API_KEY`
- `JORDANA_SPREADSHEET_ID`

The API key must not be hardcoded in Apps Script source. The spreadsheet ID should point to the existing production spreadsheet; do not create a replacement spreadsheet.

## Local Configuration

The ignored root `.env` remains the administrative recovery record. It stores the active sync values and the pending rotation values needed to update the same integration later.

Useful safe commands:

```bash
scripts/validate_calendar_integration_config.py
scripts/generate_calendar_shortcut_specs.py
scripts/configure_apps_script.py
```

`validate_calendar_integration_config.py` prints only whether required values are present. `generate_calendar_shortcut_specs.py` writes live, secret-bearing Shortcut payload specs to ignored `data/private/shortcut-build/`. `configure_apps_script.py` reports the exact missing local admin values or manual deployment steps without printing secrets.

## Shortcut Status

The current macOS Shortcuts library has `Jordana Calendar Snapshot v2`, but Apple `shortcuts` on this Mac can list/run/view/sign only; it does not export or install a Shortcut from the command line. Live Shortcut specs are therefore prepared locally in ignored files, while final installation/update remains a device-side step unless a safer Shortcuts automation path is added.

Do not commit live Shortcut artifacts. The generated payload specs contain the endpoint and key and must stay under `data/private/shortcut-build/`.

## Sync And Reconciliation

The Python importer preserves every new raw snapshot unless the exact `snapshot_key` already exists locally. Operational candidates collapse by:

1. `calendar_event_id`
2. `event_fingerprint`
3. title/start/end/calendar fallback evidence

Repeated normal captures, repeated June backfills, and overlap between normal and backfill windows should not create duplicate operational sessions when stable event identity is present. Approved session values remain protected from silent overwrite; source raw snapshot links and raw calendar title evidence may refresh.

Future events may be imported as proposed, reviewable sessions. They must not be treated as approved, finalized, paid, or invoice-ready merely because they were captured. A future homepage Upcoming Sessions section should query unapproved/proposed sessions by `session_date`/`start_at` and avoid invoice readiness state.

## Raw Snapshot Replay Recovery

If `Raw_Event_Snapshots` or local `raw_calendar_snapshots` contains calendar
evidence that did not become a candidate/session, use the in-app
`Reconciliation` screen first. Select a month and run `Dry Run`; the app shows
missing sessions, extra sessions, possible duplicates, newer edited event
versions, excluded/non-client items still affecting billing, and approved
records that require manual review. `Apply Safe Recovery` remains disabled until
a dry-run has completed for the selected month.

The same local replay service is available from the CLI. It does not fetch new
Sheet rows and never duplicates raw evidence.

Dry-run first:

```bash
PYTHONPATH=app python3 -m jordana_invoice --db data/jordana_invoice.sqlite3 calendar-reconcile --dry-run --month 2026-06
```

Apply only after the summary is reviewed:

```bash
PYTHONPATH=app python3 -m jordana_invoice --db data/jordana_invoice.sqlite3 calendar-reconcile --apply --month 2026-06 --confirm-apply APPLY_CALENDAR_RECONCILE
```

Apply mode creates and verifies a SQLite backup before derived writes. The
replay groups existing raw snapshots by calendar event identity, chooses the
newest captured/ingested version for pending records, creates missing
candidates/sessions, and excludes pending sessions whose latest evidence is
personal/admin/non-client. Approved sessions are not silently rewritten; later
source changes create review warnings instead.

### June 2026 Recovery Instructions For Jordana

After installing the current test release:

1. Open `Jordana Billing`.
2. Click `Reconciliation` in the left sidebar.
3. In `Month`, choose `June 2026`.
4. Click `Dry Run`.
5. Review all six sections: `Missing Sessions`, `Extra Sessions`, `Possible Duplicates`, `Newer Edited Event Versions`, `Excluded or Non-Client Items Affecting Billing`, and `Approved Records Requiring Manual Review`.
6. If the dry-run output looks reasonable, click `Apply Safe Recovery`.
7. Confirm the page changes to `Safe Recovery Summary` and shows `Verified backup: ...`.
8. Open `Review Queue`. Missing recovered June client sessions appear there as pending review items, usually under Needs Review, with their parsed participant/title and a `Review` button.
9. Open `Sessions`, set the date filter to `All dates` or `Previous month`, and use the ledger to confirm recovered sessions are present as unresolved/pending rows.
10. Resolve and approve recovered sessions manually before expecting them to appear in Client Sessions reports or invoice staging.

Expected behavior:

- Missing raw June rows become pending Review and Sessions rows.
- Pending edited/replaced events use the newest captured source version.
- Excluded or non-client latest evidence is marked excluded and does not enter billing.
- Approved sessions are not silently changed; source changes create review warnings.
- Reports and invoice staging exclude unresolved or excluded rows until Jordana resolves and approves them.

## Rollback

If deployment fails:

1. Keep the existing Apps Script deployment and active `JORDANA_INGEST_API_KEY` unchanged.
2. Do not promote `JORDANA_PENDING_INGEST_API_KEY` to active sync until Script Properties match.
3. Keep raw Sheet rows append-only.
4. Leave the local SQLite cursor untouched unless a dry-run and backup plan is approved.
5. Rebuild Shortcut specs from `.env` after correcting local admin values.
