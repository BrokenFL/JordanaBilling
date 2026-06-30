# Jordana Billing v0.1.0 Test Release

This is a private clean-Mac test release for Brooke's spare Mac. It is a pre-release and is not approved for Jordana's production Mac.

- Required Mac: Apple Silicon.
- Required Python runtime: see `release_manifest.json`; this test release is expected to require Python 3.14.x.
- After download, installation uses the shipped wheelhouse and can run offline.
- Normal daily launch does not require Git, GitHub, PyPI, pip, or a source checkout.
- No private configuration, database, credentials, invoices, receipts, reports, logs, or client data are included.
- Brooke must create private configuration separately with `scripts/create_private_config.sh`.
- The spare-Mac test should use an explicitly initialized disposable database.
- Gatekeeper may require right-click Open or Security & Privacy approval.
- Verify the `.zip` checksum before installing.
- Follow `docs/TEST_MAC_ACCEPTANCE.md`.

Do not install this test release on Jordana's production Mac yet.
