# Business Profile

Production identity is private local data. Create ignored `data/private/business-profile.json`, then run:

```bash
PYTHONPATH=app python3 -m jordana_invoice --db data/jordana_invoice.sqlite3 set-business-profile data/private/business-profile.json
```

Place private branding at `data/private/branding/jordana-logo.svg` and set `logo_path` accordingly. SVG wrappers containing embedded artwork and PNG files preserve aspect ratio. Missing/unreadable logos use the business fallback. When `logo_contains_business_details` is true, address/phone are not duplicated; `show_email_below_logo` may still display email.

If `logo_path` is blank, invoices may fall back to the bundled approved default logo at `app/jordana_invoice/static/assets/jordana-logo.png`. That default does not overwrite or replace a deliberately configured custom local logo path.

The review UI now exposes this as `Settings -> Invoice Settings`, backed by the existing `GET /api/business-profile` and `POST /api/business-profile` endpoints. Only one active profile is supported. Changes are audited and finalized invoices retain the frozen snapshot values captured at finalization time, even after the active profile changes. Committed tests/screenshots use placeholders such as `Demo Practice`, `100 Example Avenue`, `555-0100`, `billing@example.test`, and `Demo Payee`.
