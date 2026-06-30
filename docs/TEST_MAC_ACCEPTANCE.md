# Clean Test-Mac Acceptance Checklist

Use this checklist on Brooke's spare clean Mac before installing anything on Jordana's Mac. Do not use real credentials in screenshots or notes.

## Prerequisites

- macOS 12 or later on Apple Silicon.
- The Python major/minor version listed in `release_manifest.json` installed once.
- Access to the private GitHub repository release page.
- The versioned release zip and matching `.sha256` file from the private pre-release.
- The private Apps Script URL and ingest API key available locally, not in GitHub, email, chat, screenshots, or logs.

## Steps

1. Sign into GitHub with an account authorized for the private `BrokenFL/JordanaBilling` repository.
2. Open the repository's Releases page.
3. Download the test release zip and matching `.sha256` file.
4. Verify checksum:

```bash
shasum -a 256 -c JordanaBilling-<version>-<commit>-macos-arm64.zip.sha256
```

5. Unzip the release.
6. Create the private config:

```bash
cd JordanaBilling-<version>-<commit>-macos-arm64
scripts/create_private_config.sh
```

The helper writes `~/Library/Application Support/Jordana Billing/config/.env` with permissions `600`. It hides the API key while typing and does not print the key.

7. Install with a disposable first-time test DB:

```bash
scripts/install_release.sh --init-empty-db
```

8. Confirm the app exists at `~/Applications/Jordana Billing.app`.
9. Confirm private data exists under `~/Library/Application Support/Jordana Billing/`.
10. Double-click the app and confirm the browser opens only after health readiness.
11. Turn Wi-Fi off and repeat a Wi-Fi-off launch. Startup should still work; calendar sync may show an internet-dependent sync error.
12. Restart the Mac and launch again.
13. Double-click twice and confirm the second launch reuses the existing healthy server rather than creating a duplicate.
14. Start an unrelated process on port `8765`, then launch. Expected result: Jordana Billing refuses to stop or reuse it.
15. Temporarily move `config/.env` aside and test the missing config error. Restore the file afterward.
16. Temporarily move `data/jordana_invoice.sqlite3` aside and test the missing DB error. Restore the file afterward.
17. Reinstall the same release and confirm existing config and DB are preserved.
18. Remove `~/Applications/Jordana Billing.app` only, then confirm private data remains in Application Support.

## Evidence To Record

- Release filename and checksum result.
- Confirmation that `.env` permissions are `600`.
- Installer success output.
- Whether Gatekeeper required right-click Open or Security & Privacy approval.
- Offline launch result.
- Restart launch result.
- Duplicate-launch result.
- Port-conflict, missing-config, and missing-DB error wording.
- Reinstall result confirming data preservation.

## Stop Conditions

Stop before Jordana's Mac if any step creates a blank DB unexpectedly, overwrites private config, starts without the expected DB, requires PyPI/GitHub during launch, kills an unrelated process, exposes secrets in output, or fails to launch after reboot.

## Rollback

Move `~/Applications/Jordana Billing.app` to Trash. Keep `~/Library/Application Support/Jordana Billing` intact unless Brooke explicitly chooses to remove private data.
