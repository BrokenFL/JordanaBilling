# Monthly Calendar Review

Jordana can compare the month's iPhone calendar with her approved session log
in **Reports → Monthly Calendar Comparison**. Her saved approvals and actual
charged rates are authoritative. The calendar export supplies independent
evidence for her manual review.

## Set up once

1. In Jordana's installed Mac app, choose **Check for Updates → Update and
   restart** once Test.42 is offered. Save unfinished edits first. The updater
   creates a verified backup and preserves the existing database and configuration.
   For manual installation, quit the app and run **Install Jordana Billing.app**
   from the verified DMG with the existing installation selected.
2. Transfer **Jordana Monthly Calendar Export.shortcut** to Jordana's iPhone
   using Files or AirDrop. Open it and choose **Add Shortcut**. Inspect its
   actions before the first run.
3. Give this Shortcut access to all the calendars Jordana wants checked. Every
   calendar available to Shortcuts is included. Confirm the relevant work and
   personal calendars are present in the iPhone Calendar app.
4. Optionally add the Shortcut to the Home Screen for an easy month-end button.

The new Shortcut is separate from the existing daily Calendar Sync Shortcut.
It needs Calendar and Files access and uses the share sheet to transfer the
saved file. It contains no sync URL, API key, or credentials.

## At the end of each month

1. Run **Jordana Monthly Calendar Export** on the iPhone. Choose any date in
   the month to review; it defaults to the previous month. On September 30,
   explicitly choose a September date. On October 1, September is the default.
2. Save the CSV in Files. Its name includes the selected month and export time,
   for example `Jordana_Calendar_2026-09_20261001-090000.csv`.
3. Share the saved file to the Mac. Note the event count in the final message.
4. In Jordana Billing, open **Reports → Monthly Calendar Comparison**, select
   the same month, choose the CSV, and click **Compare Month**.
5. Check the timed-event count against the Shortcut's count. Review the rows
   shown under **Needs manual review**. Use **All appointments** to see the full
   calendar beside the approved log.
6. Save **Calendar CSV**, **Approved Session Log**, and **Comparison CSV** if
   Jordana wants copies for her monthly review.

The export includes every timed event that starts in the selected Eastern Time
month, across all available calendars. All-day events are excluded. Calendar
titles, source calendars, start/end times, and durations are preserved. Calendar
notes and locations are not exported. Month boundaries follow Eastern Time,
including daylight saving changes, even while the phone is travelling.

## Read the comparison

| Result | What Jordana should check |
| --- | --- |
| Matched | Calendar source, title, start/end, and duration agree with the approved record. |
| Values differ | The appointment matches, but the calendar and approved duration or end time differ. Her intentionally approved duration may be correct. |
| Possible match | A unique same-day title or start time suggests a match, but the title or time changed. Confirm it manually. |
| Multiple possible matches | More than one record could match. Every source row remains visible; no pairing was chosen automatically. |
| Possible duplicate | Repeated calendar records need an individual check. |
| Calendar only | There is no approved-session match. This may be personal/admin, cancelled, a duplicate, or still awaiting review. |
| Approved session only | The approved session is absent from this export. Check calendar deletions, later edits, calendar access, and the selected month. |

The default review-row count is a count of displayed rows, not a count of
missing appointments. An ambiguous group can contain separate calendar and
approved-session rows.

The comparison does not approve sessions, change charges, mark payments,
generate invoices, or import appointments. Resolve any needed changes through
the normal Review or invoice workflow, then compare the month again. Approved
billable cancellation/no-show records retain their recorded outcome; a calendar
entry by itself does not confirm attendance.

The file shows the calendar as it exists at export time. It cannot recover an
event previously deleted from the phone. Preserve the original monthly CSV and
compare later edits with the saved approvals. A matching calendar is one part
of review; Month Close retains its invoice, payment, and capture checks.

## First-run acceptance on Jordana's devices

The generated Shortcut is validated and signed locally. Its first actual run
on Jordana's iPhone remains a device acceptance step. Confirm a known timed
appointment from each relevant calendar appears, an all-day entry is absent,
the first and last dates are correct, the saved filename ends in `.csv`, and
the event count agrees in the Mac comparison. Choose September 2026 for the
first historical comparison; export the calendar as it currently exists.

## Maintainer contract

The credential-free generator is `scripts/build_monthly_calendar_shortcut.py`.
Generated XML, signed Shortcuts, calendars, and report downloads stay under
ignored `output/monthly-review/` or private user-selected storage.

The CSV has these exact columns, in order:

```text
Month,Exported At,Calendar,Title,Start,End,Duration Minutes,All Day
```

Each record carries the selected `YYYY-MM` month and a common offset-aware
export timestamp. Start/end timestamps include timezone offsets. Double quotes
in calendar names and titles are doubled; every record field is quoted so
commas, Unicode, and line breaks survive. A header-only CSV is a valid empty
export. Month selection supports 2000–2099. The local endpoint limits uploads to
800,000 UTF-8 CSV bytes and 5,000 records, within the existing 1 MiB JSON limit.

The app parses the upload in memory and compares it within a read transaction.
It does not save the uploaded file or write to SQLite. Derived report downloads
apply the existing spreadsheet formula protection. The source CSV download
preserves the original uploaded text.

Session Log, Client Sessions, and Client Summary use saved approval status over
an older or reparsed calendar classification. Pending unresolved sessions and
deliberately excluded/nonbillable sessions retain their existing report rules.
No schema migration is required for these changes.
