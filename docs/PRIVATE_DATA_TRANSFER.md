# Private Data Transfer

GitHub transfers code only. Live databases, `.env`, reports, credentials, raw imports, and backups must move separately.

## Package Shape

Recommended local package:

```text
Jordana_Private_Transfer/
  data/jordana_invoice.sqlite3
  config/.env
  credentials/
  imports/
  Reports/
  TRANSFER_MANIFEST.txt
```

Do not place this package inside Git.

## Safe Transfer Methods

Use direct AirDrop, an encrypted external drive, or an encrypted archive over an appropriate secure channel. Do not use ordinary email for live data.

## Before Packaging

1. Stop the local app.
2. Create a SQLite backup.
3. Run `PRAGMA integrity_check`.
4. Record schema version or table list, including `rate_rule_participants` if present.
5. Record row counts.
6. Calculate SHA256 checksums.
7. Fill out `scripts/TRANSFER_MANIFEST_TEMPLATE.txt`.
8. Encrypt the package.

## On Jordana's Mac

1. Clone the private GitHub repository.
2. Run `scripts/setup_jordana_mac.sh`.
3. Place private files in the documented paths.
4. Run `scripts/verify_install.sh`.
5. Compare manifest row counts.
6. Run a manual sync or open the review UI.
7. Create a fresh backup.

After transfer, review `docs/SCHEMA_AUDIT.md` before attempting any legacy table cleanup.
