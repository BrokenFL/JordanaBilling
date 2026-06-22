# Sanitized Demo Data

The repository includes a fictional-only fixture:

```text
data/samples/sanitized_demo_calendar_snapshots.csv
```

It covers structured titles with and without title time, joint sessions, cancelled appointments, no-shows, title-time mismatch, personal/admin items on `Jordana Work`, valid client-style items on other calendars, hidden-calendar recovery, future scheduled sessions, past completed sessions, unknown session types, legacy shorthand, and event versions after cancellation, title correction, and calendar movement.

Create the isolated database:

```bash
scripts/create_demo_database.sh
```

The script:

- writes only to `data/demo/*.sqlite3`
- refuses the default/live database path
- deletes and recreates only the demo database
- imports the sanitized fixture
- seeds demo calendar preferences
- writes `app_metadata.demo_mode=true`
- runs `PRAGMA integrity_check`
- prints the review UI launch command

Launch:

```bash
PYTHONPATH=app python3 -m jordana_invoice --db data/demo/jordana_demo.sqlite3 serve-review
```

The review UI displays `DEMO DATA - NOT FOR REAL BILLING` only when `app_metadata.demo_mode=true`.
