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

The command also seeds sanitized business/bill-to profiles, standard and custom services, eligible/ineligible cancellation and no-show cases, drafts, finalized history, void/reissue, a nonparticipant parent payer, joint sessions, and a multi-page invoice. Sanitized PDFs are written to ignored `output/pdf/demo/<year>/`.

The demo seed includes a sanitized permanent client **Robin Rivers** with one active billing party. This allows verification of exact-name auto-linking: calendar rows titled "Robin Rivers 5 30", "Robin Rivers 530", and "Robin Rivers 5:30 phone" auto-link to the existing person and billing party on rebuild. The non-exact variant "Robin Rivers 530 scheduled 5 30" remains unresolved for manual review. Robin Rivers is fictional sanitized demo data only.
