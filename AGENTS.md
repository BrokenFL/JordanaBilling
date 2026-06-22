# Codex Handoff Notes

This project is a local-first billing normalization app for Jordana.

## Required Behavior

- Preserve raw calendar evidence.
- Treat Google Sheets as the raw cloud staging and audit layer.
- Treat SQLite as the local application database.
- Keep ambiguous data reviewable and reversible.
- Do not generate invoices in Phase 1 or Phase 2.
- Do not add clinical notes.
- Do not silently classify uncertain records.
- Use internal UUID primary keys.
- Keep secrets in `.env`; never put the real API key in source or docs.
- Preserve the CSV importer for testing and recovery.
- Do not create permanent people/accounts from multi-person titles without review.
- Suggested rates are not approved rates.
- Routine review should use Participants and Bill to; backend accounts are advanced relationship support.
- Every approved session must preserve the actual charged rate.
- Joint participant rate exceptions must use person UUIDs, not display names.
- Before any GitHub push, run `scripts/git_safety_check.sh`.
- Do not commit live databases, reports, logs, screenshots with client names, shortcut backups, `.env`, or credentials.

## Start Here

Read these files before making changes:

1. `README.md`
2. `docs/PIPELINE.md`
3. `docs/CALENDAR_SHORTHAND_RULES.md`
4. `docs/DATA_MODEL.md`
5. `docs/RATE_RULES.md`
6. `docs/REVIEW_WORKFLOW.md`
7. `docs/CSV_EXPORTS.md`
8. `docs/SECTION_LEVEL_SAVES.md`
9. `docs/CLIENT_CODES.md`
10. `docs/CLIENTS_AND_ACCOUNTS.md`
11. `docs/PEOPLE.md`
12. `docs/PRIVATE_DATA_TRANSFER.md`
13. `docs/HANDOFF_TO_JORDANA_MAC.md`
14. `docs/SCHEMA_AUDIT.md`

## Verification

Run:

```bash
PYTHONPATH=app python -m unittest discover -s tests
rm -f data/jordana_invoice.sqlite3 data/acceptance_report.md
PYTHONPATH=app python -m jordana_invoice --db data/jordana_invoice.sqlite3 import-csv data/samples/june_calendar_snapshots.csv --report data/acceptance_report.md
```

Confirm the report lists likely client sessions, likely personal/admin events, review items, proposed client/time/duration, and no generated invoices.

For remote sync work, verify:

```bash
PYTHONPATH=app python -m jordana_invoice sync --dry-run
PYTHONPATH=app python -m jordana_invoice sync-status
```

Before committing or pushing, run:

```bash
scripts/git_safety_check.sh
scripts/privacy_check.sh
```
