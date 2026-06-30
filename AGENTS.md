# Codex Handoff Notes

This project is a local-first billing normalization app for Jordana.

## Required Behavior

- Preserve raw calendar evidence.
- Treat Google Sheets as the raw cloud staging and audit layer.
- Treat SQLite as the local application database.
- Keep ambiguous data reviewable and reversible.
- Do not generate invoices from unapproved or ineligible sessions.
- Do not add clinical notes, psychotherapy notes, narrative diagnoses, symptoms, medical histories, treatment plans, session-content notes, treatment summaries, or clinical interpretations. A structured diagnosis code may be stored only when required for administrative insurance billing or reimbursement documentation.
- Do not silently classify uncertain records.
- Use internal UUID primary keys.
- Keep secrets in `.env`; never put the real API key in source or docs.
- Preserve the CSV importer for testing and recovery.
- Do not create permanent people/accounts from multi-person titles without review.
- Suggested rates are not approved rates.
- Routine review should use Participants and Bill to; backend accounts are advanced relationship support.
- Every approved session must preserve the actual charged rate.
- Joint participant rate exceptions must use person UUIDs, not display names.
- The Shortcut imports all calendars; calendar source is a classification/filtering signal, not an ingestion filter.
- `Jordana Work` is the preferred future work calendar when configured with `JORDANA_PREFERRED_WORK_CALENDAR`.
- Appointment status is separate from review status, billable status, payment status, and future invoice status.
- Cancelled and no-show appointments remain preserved and require a separate billing-treatment decision.
- Calendar start time is authoritative; title time is validation evidence only.
- Use only sanitized fictional records for demo data.
- Invoice statuses are `draft`, `finalized`, and `void`; payment remains separate.
- Finalization freezes source values and assigns numbers transactionally.
- Never edit or delete a finalized invoice; void with a reason and reissue with a new number.
- Keep private business profiles, branding assets, and generated PDFs outside Git.
- Before any GitHub push, run `scripts/git_safety_check.sh`.
- Do not commit live databases, reports, logs, screenshots with client names, shortcut backups, `.env`, or credentials.

## Operational Database Safety

The operational SQLite database is at `data/jordana_invoice.sqlite3`.
Manual approval decisions and invoice-staging work stored there are **real data**,
even during demo development.  Destroying them requires manual re-entry.

**These actions are permanently prohibited without explicit human confirmation:**

- Deleting, overwriting, truncating, or recreating the operational database file by any means
- `import-csv` against the operational database without `--allow-operational-db`
- Any script or CLI invocation that deletes, truncates, recreates, or replaces the file
- Any migration that is not additive (drop-column, drop-table, truncate) on the live path

**For acceptance testing, always use:**

```bash
scripts/run_acceptance_test.sh
```

This script creates a temporary database, runs the import, writes the report to
`data/acceptance_report.md`, and deletes the temp DB on exit — the operational
database is never touched.

If you ever need to import into the live database (e.g. initial population on a
fresh install), pass `--allow-operational-db` explicitly and document why.
A verified SQLite backup is created automatically before any mutation.

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
15. `docs/CALENDAR_ENTRY_STANDARD.md`
16. `docs/DEMO_DATA.md`
17. `docs/INVOICE_MODEL.md`
18. `docs/INVOICE_LIFECYCLE.md`
19. `docs/INVOICE_TEMPLATE.md`
20. `docs/SERVICE_CATALOG.md`
21. `docs/BUSINESS_PROFILE.md`

## Verification

Run unit tests:

```bash
PYTHONPATH=app .venv/bin/python -m unittest discover -s tests
```

Run the import-csv acceptance test **without touching the operational database**:

```bash
scripts/run_acceptance_test.sh
```

Confirm the report at `data/acceptance_report.md` lists likely client sessions,
likely personal/admin events, review items, proposed client/time/duration,
and no generated invoices.

For remote sync work, verify:

```bash
PYTHONPATH=app .venv/bin/python -m jordana_invoice sync --dry-run
PYTHONPATH=app .venv/bin/python -m jordana_invoice sync-status
```

Before committing or pushing, run:

```bash
scripts/git_safety_check.sh
scripts/privacy_check.sh
```
