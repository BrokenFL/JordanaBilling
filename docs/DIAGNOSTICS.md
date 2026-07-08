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
- recent warnings and errors
- selected-module database activity summaries using counts and aggregate totals only
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
events rather than audit-log payload dumps. It does not add a schema migration
and does not write to operational billing tables.

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
