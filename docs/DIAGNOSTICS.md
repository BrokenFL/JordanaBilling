# Diagnostics And Report Issue

The local review app includes a sidebar **Report Issue** action. It creates a
sanitized diagnostic JSON bundle for support without copying the live SQLite
database or generated customer documents.

## User Flow

1. Click **Report Issue** in the sidebar.
2. Choose the area: Review, Billing Relationships, Invoices, Payments, Calendar Sync, or Other.
3. Optionally enter a short description. Avoid names or private billing details; the backend also redacts known local people and billing-party names, emails, phone numbers, local paths, and diagnosis-code-shaped tokens.
4. Click **Create Report**.
5. Use **Copy Report** or **Export Report**.

The Reports workspace also offers **Download Diagnostics JSON** for an
on-demand support report without opening the issue-description dialog.

Reports are saved locally under:

```text
Reports/Diagnostics/
```

The folder is ignored by Git with the rest of `Reports/`.

## Bundle Contents

Each report includes:

- app version, build ID, release label, and commit field from build info
- schema migration head, applied migration count, and latest applied migration
- selected issue area and current screen
- current UI filters and selection-presence flags
- recent frontend API/UI events
- recent sanitized backend HTTP/sync events
- up to 200 rotating sanitized warning/error fingerprints retained across app restarts
- recent warnings and errors
- privacy-safe unexpected-error fingerprints containing exception type and only
  source filename, function, and line number (never values or full paths)
- selected-module database activity summaries using counts and aggregate totals only
- Python, operating-system, architecture, SQLite, database quick-check, foreign-key count,
  and sanitized calendar-sync health
- timestamps

## Privacy Boundary

Reports must not include:

- client names
- clinical information
- raw calendar titles
- invoice PDFs or receipt PDFs
- the live SQLite database or backups
- credentials, API keys, or `.env` values
- full local filesystem paths
- real diagnosis codes

The diagnostic service uses aggregate database queries and rolling in-memory
events rather than audit-log payload dumps. Warning/error history is also kept
in `sanitized-runtime-errors.jsonl` with permissions `600`; it contains only
timestamps, route templates, status codes, exception types, and safe source
signatures. Messages and business values are never persisted. A database backup
is deliberately not bundled; database export remains a separate, explicit
backup action.

## Endpoint

`POST /api/diagnostics/report-issue`

Request body:

```json
{
  "area": "review",
  "description": "Short optional description",
  "ui_state": {},
  "frontend_events": []
}
```

The endpoint is write-token protected because it writes a local report file.
The response includes the saved filename plus `report_text` for the Copy and
Export buttons.
