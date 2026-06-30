# Clean Test-Mac Acceptance Checklist

Use this checklist on Brooke's spare clean Mac before installing anything on Jordana's Mac. Do not use real credentials in screenshots or notes.

## Prerequisites

- macOS 12 or later on Apple Silicon.
- The Python major/minor version listed in `release_manifest.json` installed once.
- A versioned release zip and matching `.sha256` file from `scripts/build_release.sh`.
- A private test `.env` supplied through a secure channel.

## Steps

1. Transfer the release zip and `.sha256` file to the clean Mac.
2. Verify checksum:

```bash
shasum -a 256 -c JordanaBilling-<version>-<commit>-macos-arm64.zip.sha256
```

3. Unzip the release.
4. Install with a disposable first-time test DB:

```bash
cd JordanaBilling-<version>-<commit>-macos-arm64
scripts/install_release.sh --config /secure/path/.env --init-empty-db
```

5. Confirm the app exists at `~/Applications/Jordana Billing.app`.
6. Confirm private data exists under `~/Library/Application Support/Jordana Billing/`.
7. Double-click the app and confirm the browser opens only after health readiness.
8. Turn Wi-Fi off and repeat a Wi-Fi-off launch. Startup should still work; calendar sync may show an internet-dependent sync error.
9. Restart the Mac and launch again.
10. Double-click twice and confirm the second launch reuses the existing healthy server rather than creating a duplicate.
11. Start an unrelated process on port `8765`, then launch. Expected result: Jordana Billing refuses to stop or reuse it.
12. Temporarily move `config/.env` aside and test the missing config error. Restore the file afterward.
13. Temporarily move `data/jordana_invoice.sqlite3` aside and test the missing DB error. Restore the file afterward.
14. Reinstall the same release and confirm existing config and DB are preserved.
15. Remove `~/Applications/Jordana Billing.app` only, then confirm private data remains in Application Support.

## Evidence To Record

- Release filename and checksum result.
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
