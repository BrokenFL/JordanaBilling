# Production Packaging V1

Production packaging separates one-time installation from normal daily launch.
The V1 artifact is versioned, pinned, offline-installable from its shipped
wheelhouse, and checksummed. It is not described as bit-for-bit reproducible:
the build records a timestamp and may download wheels when assembling the
wheelhouse.

## Strategy

V1 uses an offline pinned runtime install with a native macOS setup app:

- Brooke builds a versioned DMG release from this repo.
- The DMG root contains `Install Jordana Billing.app` and concise instructions.
- `Install Jordana Billing.app` contains an embedded `Contents/Resources/ReleasePayload` folder with `Jordana Billing.app`, installer scripts, a local wheelhouse, `requirements-production.lock`, `release_manifest.json`, docs, a sanitized config example, and checksums.
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
build/release/JordanaBilling-<version>-<commit>-macos-arm64.dmg
build/release/JordanaBilling-<version>-<commit>-macos-arm64.dmg.sha256
```

The artifact is inspected during build for forbidden private files such as `.env`, SQLite databases, PDFs, invoices, receipts, reports, and private data folders.

The release manifest records the exact git commit, build timestamp, builder
Python version, required Python major/minor family, payload checksums, and
whether the artifact contains private data.

## Private Configuration Setup

Never upload `.env` to GitHub and never send secrets in email, chat, logs, screenshots, or release assets.

The authoritative user path is the native setup app:

1. Open the DMG.
2. Double-click `Install Jordana Billing.app`.
3. Enter the Apps Script URL.
4. Enter the ingest API key in the hidden field.
5. Confirm whether to initialize a clean-start database.
6. Click Install.

The setup app asks for:

- `JORDANA_APPS_SCRIPT_URL`
- `JORDANA_INGEST_API_KEY`

The API key input is hidden. The setup app writes:

```text
~/Library/Application Support/Jordana Billing/config/.env
```

with permissions `600`. The config is not stored inside the `.app`, release DMG, GitHub, SQLite database, or browser storage. The installed launcher reads it at startup, validates the required keys, and exports them only to the local server process. The file persists across app restarts, Mac restarts, reinstalls, and updates. Removing the app bundle does not delete the config.

When private config already exists, the setup app disables the Apps Script URL
and ingest API-key fields and says the existing configuration will be preserved.
Reinstall remains possible without re-entering secrets.

The CLI helper `scripts/create_private_config.sh` remains available inside the payload for support use, but the GUI setup app is the user-facing workflow.

## One-Time Install

After opening the DMG, run `Install Jordana Billing.app`. It installs to
`~/Applications/Jordana Billing.app`, builds the private runtime from the
offline wheelhouse, preserves existing private config and database files, and
runs `scripts/verify_installation.sh`.

The installer preserves existing `config/.env` and `data/jordana_invoice.sqlite3`. When the database already exists, the setup app disables clean-start initialization and says the existing database will be preserved. It fails rather than creating a replacement database unless `--init-empty-db` is supplied and confirmed.

For the spare clean-Mac test, check the clean-start confirmation in the setup
app. Clean-start creates an empty database only after explicit confirmation.
It lets unresolved review evidence sync from Google Sheets but does not import
old invoices, payments, approved sessions, clients, or billing relationships.

## Daily Launch

Jordana double-clicks:

```text
~/Applications/Jordana Billing.app
```

Daily launch validates the installed runtime, private config, private database, port ownership, and health readiness. It may apply safe application migrations through the app startup contract, but it does not install packages, repair the runtime, access GitHub, access PyPI, or create a blank production database.

The daily app payload is embedded inside the setup app and is not exposed as a
separate DMG item.

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

## Port Conflicts

Before startup the launcher probes `http://127.0.0.1:8765/api/health`. If a
healthy Jordana Billing endpoint responds but the owning PID is not visible to
the current macOS user, the launcher does not start another server and does not
kill anything. It reports that Jordana Billing is already running under another
macOS user account. If a non-Jordana service or non-HTTP listener occupies the
port, launch stops with a sanitized port-conflict message.

## Safari Downloads

The authoritative release artifact is the DMG. Safari does not expand it into a
release folder and move the original artifact to Trash the way it can with ZIP
downloads. Verify the `.dmg.sha256` file against the downloaded `.dmg` from the
same folder.
