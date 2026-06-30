# Production Packaging V1

Production packaging separates one-time installation from normal daily launch.

## Strategy

V1 uses an offline pinned runtime install:

- Brooke builds a versioned zip release from this repo.
- The release contains `Jordana Billing.app`, installer scripts, a local wheelhouse, `requirements-production.lock`, `release_manifest.json`, docs, a sanitized config example, and checksums.
- The installer creates a private virtual environment inside the installed app bundle and installs only from the shipped wheelhouse.
- Normal double-click launch uses that installed runtime and never runs pip, Git, dependency repair, or package installation.

The app is not notarized and does not bundle Python itself. The clean Mac must have the Python major/minor version recorded in `release_manifest.json` installed once before installation, because the wheelhouse may include Python-specific macOS wheels. Calendar sync can still require internet during application use; app startup and daily launch do not require PyPI, GitHub, or Wi-Fi.

## Locations

Application code:

```text
~/Applications/Jordana Billing.app
```

Private operational data:

```text
~/Library/Application Support/Jordana Billing/
  config/.env
  data/jordana_invoice.sqlite3
  backups/
  logs/
  runtime/
  Reports/
```

The SQLite database, private config, backups, reports, generated invoices, receipts, and logs must not live inside the app bundle or the Git repository.

## Build A Release

From a clean development checkout:

```bash
scripts/build_release.sh
```

The build writes:

```text
build/release/JordanaBilling-<version>-<commit>-macos-arm64.zip
build/release/JordanaBilling-<version>-<commit>-macos-arm64.zip.sha256
```

The artifact is inspected during build for forbidden private files such as `.env`, SQLite databases, PDFs, invoices, receipts, reports, and private data folders.

## Private Configuration Setup

Never upload `.env` to GitHub and never send secrets in email, chat, logs, screenshots, or release assets.

On the spare Mac, create the private config from inside the unzipped release:

```bash
scripts/create_private_config.sh
```

The helper asks for:

- `JORDANA_APPS_SCRIPT_URL`
- `JORDANA_INGEST_API_KEY`

The API key input is hidden. The helper writes:

```text
~/Library/Application Support/Jordana Billing/config/.env
```

with permissions `600`. The config is not stored inside the `.app`, release ZIP, GitHub, SQLite database, or browser storage. The installed launcher reads it at startup, validates the required keys, and exports them only to the local server process. The file persists across app restarts, Mac restarts, reinstalls, and updates. Removing the app bundle does not delete the config.

To rotate the key, rerun `scripts/create_private_config.sh` and type `OVERWRITE` when prompted, or edit the Application Support config locally. Delete any temporary source file after confirming the Application Support config exists.

## One-Time Install

After unzipping the release on the target Mac:

```bash
cd JordanaBilling-<version>-<commit>-macos-arm64
scripts/install_release.sh --database /secure/path/jordana_invoice.sqlite3
```

For a disposable clean-Mac test only, Brooke may initialize an empty database explicitly:

```bash
scripts/install_release.sh --init-empty-db
```

The installer preserves existing `config/.env` and `data/jordana_invoice.sqlite3`. It fails rather than creating a replacement database unless `--init-empty-db` is supplied and confirmed.

For the spare clean-Mac test, after running `scripts/create_private_config.sh`, use:

```bash
scripts/install_release.sh --init-empty-db
```

## Daily Launch

Jordana double-clicks:

```text
~/Applications/Jordana Billing.app
```

Daily launch validates the installed runtime, private config, private database, port ownership, and health readiness. It may apply safe application migrations through the app startup contract, but it does not install packages, repair the runtime, access GitHub, access PyPI, or create a blank production database.

## Update

Use the release's deliberate update entrypoint:

```bash
scripts/update_release.sh
```

It creates and verifies a private SQLite backup before delegating to the installer. Application code can be replaced without deleting private data.

## Uninstall

To remove application code only, move `~/Applications/Jordana Billing.app` to Trash.

Do not delete `~/Library/Application Support/Jordana Billing` unless Brooke explicitly intends to remove private configuration, the operational database, backups, logs, reports, invoices, receipts, and runtime metadata.

## Gatekeeper

The app is ad-hoc signed, not notarized. A clean Mac may report the app as from an unidentified developer and require right-click Open or Security & Privacy approval. Do not bypass Gatekeeper silently.
