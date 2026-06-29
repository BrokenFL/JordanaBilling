# Calendar Entry Standard

## Intake Rule

The iPhone Shortcut continues to import non-all-day events from all calendars.

`Jordana Work` is the preferred future work calendar. It is a strong classification signal, not an ingestion restriction. Events from other calendars remain preserved as raw evidence and can still become sessions after review.

The normal capture windows are `past_3_days` and `next_7_days`. Deprecated `past_7_days` and `next_2_days` rows remain readable during transition.

## Preferred Titles

Without title time:

```text
Full Name | Minutes | Session Type
```

With title time:

```text
Full Name | Time | Minutes | Session Type
```

Cancelled:

```text
Full Name | Time | Minutes | Session Type | Cancelled
```

No-show:

```text
Full Name | Time | Minutes | Session Type | No Show
```

Supported preferred session types are `Phone`, `FaceTime`, `Office`, and `House Call`. Parsing is case-insensitive. Unknown types remain reviewable.

## Title Time

Calendar `start_at` is authoritative. Optional title time is validation evidence only.

Supported title-time examples include `6`, `6:00`, `6 PM`, `6:00 PM`, `830`, and `8:30 PM`.

When title time and Calendar start time disagree, the app keeps the Calendar time, adds a review warning, and does not silently correct either source. No tolerance window is currently approved; hour and minute must match exactly.

## Statuses

Canonical appointment statuses are:

- `scheduled`
- `completed`
- `cancelled`
- `no_show`
- `unresolved`

`Cancelled` and `No Show` in a title set appointment status. Billing treatment is a separate review decision: `billable`, `not_billable`, `waived`, or `unresolved`.

## Calendar Rules

- Participants belong in the title.
- Bill-to does not belong in the title.
- Rate does not belong in the title.
- Payment status does not belong in the title.
- Account/client codes do not belong in the title.
- Cancelled and no-show events remain on the calendar.
- Use administrative notes only.
- Do not add clinical notes.
- All calendars remain captured.
- Irrelevant calendars are filtered after ingestion.
- Hidden records remain recoverable through calendar filters/search.

## Demo Workflow

Create sanitized demo data only:

```bash
scripts/create_demo_database.sh
```

Launch the demo review UI:

```bash
PYTHONPATH=app .venv/bin/python -m jordana_invoice --db data/demo/jordana_demo.sqlite3 serve-review
```

The demo database is explicitly marked with `app_metadata.demo_mode=true`, and the UI shows `DEMO DATA - NOT FOR REAL BILLING`.
