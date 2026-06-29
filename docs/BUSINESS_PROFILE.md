# Business Profile

Production identity is private local data. Create ignored `data/private/business-profile.json`, then run:

```bash
PYTHONPATH=app .venv/bin/python -m jordana_invoice --db data/jordana_invoice.sqlite3 set-business-profile data/private/business-profile.json
```

Place private branding at `data/private/branding/jordana-logo.svg` and set `logo_path` accordingly. SVG wrappers containing embedded artwork and PNG files preserve aspect ratio. Missing/unreadable logos use the business fallback. When `logo_contains_business_details` is true, address/phone are not duplicated; `show_email_below_logo` may still display email.

If `logo_path` is blank, invoices may fall back to the bundled approved default logo at `app/jordana_invoice/static/assets/jordana-logo.png`. That default does not overwrite or replace a deliberately configured custom local logo path.

The review UI now exposes this as `Settings -> Invoice Settings`, backed by the existing `GET /api/business-profile` and `POST /api/business-profile` endpoints. Only one active profile is supported. Changes are audited and finalized invoices retain the frozen snapshot values captured at finalization time, even after the active profile changes. Committed tests/screenshots use placeholders such as `Demo Practice`, `100 Example Avenue`, `555-0100`, `billing@example.test`, and `Demo Payee`.

Invoice Settings now also includes `zelle_recipient` ("Zelle email or mobile number"). It is required for invoice readiness and is frozen into finalized invoices as `zelle_recipient_snapshot`. Use sanitized placeholder values such as `demo-zelle@example.test` or `15551234567` in tests and documentation; do not commit a real private Zelle identifier.

Invoice Settings also includes optional insurance coding identifiers: `insurance_ein`, `insurance_npi`, and `insurance_sw`. These are editable only in Invoice Settings and are read-only during invoice finalization. When a user checks "Add Insurance Coding" during finalization and provides a diagnosis code, all four values (diagnosis code + EIN/NPI/SW from settings) are frozen into the finalized invoice snapshot. Later settings changes do not affect existing finalized invoices. The diagnosis code is invoice-specific private billing data and is never stored on draft invoices, people, sessions, or reusable defaults. Use fictional placeholders such as EIN `00-0000000`, NPI `0000000000`, SW `SW-TEST`, and diagnosis code `Z00.0` in tests and documentation; never commit real identifiers.
