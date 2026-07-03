# Production Packaging V1

Production packaging separates one-time installation from normal daily launch.
The V1 artifact is versioned, pinned, offline-installable from its shipped
wheelhouse, and checksummed. It is not described as bit-for-bit reproducible:
the build records a timestamp and may download wheels when assembling the
wheelhouse.

## Current Validation Status

Brooke reports that the current one-click installer successfully completed an
install and launch on a test Mac. The native setup app, offline wheelhouse,
private runtime, Application Support data paths, Documents output paths, and
daily double-click launch are therefore implemented and manually proven in at
least one test installation.

The full clean-Mac acceptance evidence record is not yet complete. The release
filename, checksum, Python version, Gatekeeper behavior, restart result,
duplicate-launch result, reinstall result, and remaining failure scenarios must
still be recorded in `docs/TEST_MAC_ACCEPTANCE.md` before final production
handoff.

### Current Test Build — v0.1.0-test.7

This is a controlled pilot/test release, not a final production release.

- **Release label:** v0.1.0-test.7
- **DMG:** `JordanaBilling-v0.1.0-test.7-179da1fe14ac-macos-arm64.dmg`
- **Manifest commit:** `179da1fe14ac1fd56ed1e6b939b34fafe7299760`
- **application_version:** 0.1.0
- **source_tree_dirty:** false
- **builder Python:** 3.14.4
- **requires_python:** 3.14.x
- **architecture:** arm64
- **DMG checksum verification:** passed
- **DMG SHA-256:** `f4eeab417425aad731570b42185810c6712b588bba7f5fe83129d44b2d93bd85`
- **Private-file scan:** no `.env`, SQLite, or PDF files found
- **contains_private_data:** false
- **Wheelhouse includes:** `jordana_invoice-0.1.0`, `reportlab 4.5.1`, `pillow 12.2.0`, `charset-normalizer 3.4.7`
- **Local browser smoke:** canonical draft PDF preview and stored finalized PDF preview load inline in the Invoices workspace

test.7 was built from commit `179da1f` with Python 3.14.4. It is locally
built, checksum-verified, and privacy-scanned. Install and clean-Mac acceptance
for this exact artifact remain pending and must be recorded in
`docs/TEST_MAC_ACCEPTANCE.md`.

The prior installed-smoke baseline remains test.6 from commit `0dec58b`. That
DMG was installed successfully on the brooketest account. Existing private
configuration and SQLite database were preserved during upgrade. Live smoke
testing passed for the major Billing Relationship, filing-owner,
delivery-contact, invoice, and data-preservation workflows.

An initial test.6 artifact built from commit `6c3dbab` using Python 3.11
was rejected before installation and was not published. The correct
replacement was built from commit `0dec58b` using Python 3.14.4 in a
clean temporary clone outside the Documents directory.

The prior test.5 and test.6 builds remain historically accurate for the periods
in which they were the current builds. test.7 supersedes test.6 as the current
built test artifact, but test.6 remains the latest installed-smoke baseline
until test.7 installation evidence is recorded.

The full clean-Mac acceptance evidence record (restart, duplicate launch,
cross-user port ownership, unrelated port conflict, missing-config,
missing-database, uninstall preservation) remains incomplete and should
be recorded in `docs/TEST_MAC_ACCEPTANCE.md` before final production
handoff.

### Launcher Build Non-Reproducibility

Launcher builds are not byte-reproducible because the Mach-O UUID and ad-hoc
code-signature metadata change on each compilation. The tracked verified
launcher binary hash is
`05288036d84eec8d635afd507af523949f8abb1af33e66b49a262e5abb51f154`. The
official build script recompiles the launcher and produces
`55b76bfc5e10a11b8311916089d0ef54b918d806705371ee9d5c9e14b7f7c7b5`. The
difference is limited to Mach-O UUID and ad-hoc code-signature hash metadata.
Both launchers use `Identifier=com.jordana.billing.launcher`,
`Signature=adhoc`, `TeamIdentifier=not set`. The release intentionally contains
the newly rebuilt launcher. The repository launcher was restored to the tracked
verified binary after the release build. This is expected and does not indicate
corruption or obsolescence.

## Strategy

V1 uses an offline pinned runtime install with a native macOS setup app:

- Brooke builds a versioned DMG release from this repo.
- The DMG root contains `Install Jordana Billing.app` and concise instructions.
- `Install Jordana Billing.app` contains an embedded `Contents/Resources/ReleasePayload` folder with `Jordana Billing.app`, installer scripts, a local wheelhouse, `requirements-production.lock`, `release_manifest.json`, docs, a sanitized config example, and checksums.
- The installer creates a private virtual environment inside the installed app bundle and installs only from the shipped wheelhouse.
- Normal double-click launch uses that installed runtime and never runs pip, Git, dependency repair, or package installation.

The app is not notarized and does not bundle Python itself. The clean Mac must
have the Python major/minor version recorded in `release_manifest.json`
installed once before installation because the wheelhouse may include
Python-specific macOS wheels. Calendar sync can still require internet during
application use; app startup and daily launch do not require PyPI, GitHub, or
Wi-Fi.

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
```

User-facing generated files:

```text
~/Documents/Jordana Billing/
  Session Lists/
    Jordana_All_Appointments.csv
    Jordana_Session_Log_<YEAR>.csv
    Jordana_Client_Sessions_<YEAR>.csv
    Jordana_Client_Summary_<YEAR>.csv
  Client Files/<Client Display Name>/<Month YYYY>/
    Invoice_<number>.pdf
    Receipt_<number>.pdf
```

The SQLite database, private config, backups, logs, runtime state, generated
reports, invoices, and receipts must not live inside the app bundle or the Git
repository. Reinstall/update preserves the Documents output folders.

## Build A Release

From a clean development checkout:

```bash
scripts/build_release.sh
```

For repeated test installers that share the same application/package version,
pass a separate release label. This does not change the Python package version,
database schema, migrations, invoice numbering, or data compatibility:

```bash
scripts/build_release.sh --release-label v0.1.0-test.7
```

`JORDANA_RELEASE_LABEL=v0.1.0-test.7 scripts/build_release.sh` is equivalent.
Release labels must be simple path-safe values such as `v0.1.0-test.7` or
`v0.1.0-rc.1`; blank, slash-containing, traversal, or shell-unsafe labels are
rejected.

The build writes:

```text
build/release/JordanaBilling-<version>-<commit>-macos-arm64.dmg
build/release/JordanaBilling-<version>-<commit>-macos-arm64.dmg.sha256
```

When a release label is provided, the label replaces `<version>` in the
artifact filename while `application_version` remains the package version from
`pyproject.toml`:

```text
build/release/JordanaBilling-v0.1.0-test.7-<commit>-macos-arm64.dmg
build/release/JordanaBilling-v0.1.0-test.7-<commit>-macos-arm64.dmg.sha256
```

The artifact is inspected during build for forbidden private files such as
`.env`, SQLite databases, PDFs, invoices, receipts, reports, and private data
folders.

The release manifest records the exact git commit, application version,
optional release label, whether tracked source files were dirty at build time,
build timestamp, builder Python version, required Python major/minor family,
payload checksums, and whether the artifact contains private data.

## Private Configuration Setup

Never upload `.env` to GitHub and never send secrets in email, chat, logs,
screenshots, or release assets.

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

with permissions `600`. The config is not stored inside the `.app`, release DMG, GitHub, SQLite database, or browser storage. The installed launcher reads
it at startup, validates the required keys, and exports them only to the local
server process. The file persists across app restarts, Mac restarts,
reinstalls, and updates. Removing the app bundle does not delete the config.

When private config already exists, the setup app disables the Apps Script URL
and ingest API-key fields and says the existing configuration will be
preserved. Reinstall remains possible without re-entering secrets.

The CLI helper `scripts/create_private_config.sh` remains available inside the
payload for support use, but the GUI setup app is the user-facing workflow.

## One-Time Install

After opening the DMG, run `Install Jordana Billing.app`. It installs to
`~/Applications/Jordana Billing.app`, builds the private runtime from the
offline wheelhouse, preserves existing private config and database files, and
runs `scripts/verify_installation.sh`.

The installer preserves existing `config/.env` and
`data/jordana_invoice.sqlite3`. When the database already exists, the setup app
disables clean-start initialization and says the existing database will be
preserved. It fails rather than creating a replacement database unless
`--init-empty-db` is supplied and confirmed.

### Installer Temporary Cleanup

The installer stages the app bundle in a temporary
`Jordana Billing.app.installing` directory during the virtual-environment
build. On controlled aborts or handled failures before the final replacement,
the temporary directory is removed through an EXIT trap. Application Support,
the database, config, invoices, receipts, reports, and user data are never
touched by temporary cleanup.

### Rollback-Safe App Replacement

Before replacing an existing `Jordana Billing.app`, the installer moves it to
`Jordana Billing.app.previous` in the same parent directory. The staged
replacement remains at `Jordana Billing.app.installing` until its runtime is
ready, then moves into the final app path and runs final verification.

If verification succeeds, `.previous` and stale `.installing` artifacts are
removed. If verification fails, the failed replacement is removed or
quarantined and `.previous` is restored to the original app path. If no
previous app existed, the failed replacement is removed. If automatic restore
fails, the installer preserves `.previous` where possible and reports a
sanitized manual recovery message. Private Application Support data and
Documents output folders remain outside the app bundle and are not touched by
app-bundle rollback cleanup.

### Current Package-Version Coupling

The release manifest records the project version, but
`scripts/install_release.sh` currently installs `jordana-invoice==0.1.0`
directly. This matches the current `pyproject.toml` version. A future version
bump must update both locations until the installer is changed to read the
expected package version from `release_manifest.json`.

For the spare clean-Mac test, check the clean-start confirmation in the setup
app. Clean-start creates an empty database only after explicit confirmation. It
lets unresolved review evidence sync from Google Sheets but does not import old
invoices, payments, approved sessions, clients, or billing relationships.

## Daily Launch

Jordana double-clicks:

```text
~/Applications/Jordana Billing.app
```

Daily launch validates the installed runtime, private config, private database,
port ownership, and health readiness. It may apply safe application migrations
through the app startup contract, but it does not install packages, repair the
runtime, access GitHub, access PyPI, or create a blank production database.

The daily app payload is embedded inside the setup app and is not exposed as a
separate DMG item.

## Update

Use the release's deliberate update entrypoint:

```bash
scripts/update_release.sh
```

It creates and verifies a private SQLite backup before delegating to the
installer. That protects the database and private operational state. The
installer also keeps the previous app bundle at `Jordana Billing.app.previous`
until the replacement verifies, then removes it after success or restores it on
verification failure.

## Uninstall

To remove application code only, move
`~/Applications/Jordana Billing.app` to Trash.

Do not delete `~/Library/Application Support/Jordana Billing` unless Brooke
explicitly intends to remove private configuration, the operational database,
backups, logs, reports, invoices, receipts, and runtime metadata.

## Gatekeeper

The app is ad-hoc signed, not notarized. A clean Mac may report the app as from
an unidentified developer and require right-click Open or Security & Privacy
approval. Do not bypass Gatekeeper silently.

## Port Conflicts

Before startup the launcher probes `http://127.0.0.1:8765/api/health`. If a
healthy Jordana Billing endpoint responds but the owning PID is not visible to
the current macOS user, the launcher does not start another server and does not
kill anything. It reports that Jordana Billing is already running under another
macOS user account. If a non-Jordana service or non-HTTP listener occupies the
port, launch stops with a sanitized port-conflict message.

## Safari Downloads

The authoritative release artifact is the DMG. Safari does not expand it into
a release folder and move the original artifact to Trash the way it can with
ZIP downloads. Verify the `.dmg.sha256` file against the downloaded `.dmg` from
the same folder.
