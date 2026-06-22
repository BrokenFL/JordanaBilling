# Business Profile

Production identity is private local data. Create ignored `data/private/business-profile.json`, then run:

```bash
PYTHONPATH=app python3 -m jordana_invoice --db data/jordana_invoice.sqlite3 set-business-profile data/private/business-profile.json
```

Place private branding at `data/private/branding/jordana-logo.svg` and set `logo_path` accordingly. SVG wrappers containing embedded artwork and PNG files preserve aspect ratio. Missing/unreadable logos use the business fallback. When `logo_contains_business_details` is true, address/phone are not duplicated; `show_email_below_logo` may still display email.

Only one active profile is supported. Changes are audited and finalization freezes the values used. Committed tests/screenshots use placeholders such as `Demo Practice`, `100 Example Avenue`, `555-0100`, `billing@example.test`, and `Demo Payee`.
