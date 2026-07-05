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

### Current Test Build — v0.1.0-test.11

This is a controlled pilot/test release, not a final production release.

- **Release label:** v0.1.0-test.11
- **Python package/application version:** 0.1.0.post11
- **DMG:** recorded in the GitHub release and the artifact `release_manifest.json`
- **Manifest commit:** recorded in the GitHub release and the artifact `release_manifest.json`
- **source_tree_dirty:** false
- **builder Python:** 3.14.4
- **requires_python:** 3.14.x
- **architecture:** arm64
- **DMG checksum verification:** required before publication
- **hdiutil verify:** required before publication
- **Private-file scan:** no `.env`, SQLite, or PDF files found
- **contains_private_data:** false
- **Wheelhouse includes:** exact `jordana_invoice-0.1.0.post11` wheel plus pinned production dependencies
- **Local browser smoke:** required before publication
- **Unit tests:** required before publication
- **Temporary-DB acceptance test:** required before publication (operational database untouched)
- **Privacy and Git safety checks:** required before publication

test.11 adds the weekday Review column, weekend/evening rate matching via manually
selected session type, Edit Session (no reason required), billing relationship
delete/archive, self-pay Edit, dedicated Billing Relationships route, canonical
relationship deep-linking, active-tab preservation, write-token messaging, SSL
blank-env handling, and the prepared `scripts/sign_and_notarize_release.sh`
signing script.

### Bug Fixes In test.11

1. **Weekday column** — Review queue shows short weekday abbreviation.
2. **Weekend/evening rate matching** — Manually selected weekend/evening session type propagates `time_category` to rate suggestion.
3. **Edit Session** — Eligible approved sessions return to Review without a reason prompt; draft line removed and total recalculated atomically.
4. **Billing Relationship delete/archive** — Unused relationships deleted; history-protected relationships archived.
5. **Self-pay Edit** — Self-pay rows show Edit; opens canonical account editor.
6. **Dedicated Billing Relationships route** — `#billing-relationships` keeps the nav active.
7. **Canonical relationship access** — All entry points resolve to the same `account_id`.
8. **Review relationship deep-link** — Opens canonical account editor directly from Review.
9. **Active-tab preservation** — Editor close/save returns to originating tab.
10. **Write-token messaging** — Returns `Write access expired. Refresh Jordana Billing and try again.`
11. **SSL blank-env handling** — Blank `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE` treated as unset.
12. **Signing preparation** — `scripts/sign_and_notarize_release.sh` added.

### Bug Fixes Inherited from test.10

1. **Composite cursor ordering fix** — sync cursor comparison now correctly handles rows with equal `ingested_at` values by using `snapshot_key` as a tiebreaker.
2. **Flaky test fix** — `test_07_health_endpoint` now includes a kill fallback on timeout.

### Bug Fixes Inherited from test.9

1. **In-app Quit** — the sidebar includes a visible Quit action. The token-protected endpoint stops sync work, shuts down the local server, and is idempotent for repeated requests.
2. **Installer stale-runtime hardening** — installation reads the exact wheel path and package version from `release_manifest.json`, uses `pip --force-reinstall` from the shipped wheelhouse, verifies payload and installed files against manifest checksums, imports the installed package build info, launches the installed app, and confirms `/api/build-info` reports the expected build ID.
3. **Rollback-safe update verification** — the installer coordinates with an already-running Jordana Billing process before replacement and restores the prior app/runtime automatically if app-bundle, package-identity, or running-server verification fails.
4. **June reconciliation proof** — the in-app Reconciliation flow is verified on a sanitized temporary database: June dry-run buckets render, apply creates a verified backup, missing rows become pending review sessions, pending edited rows refresh to the newest source version, non-client rows are excluded from billing, approved sessions remain frozen, and unresolved/excluded rows stay out of client reports and invoice staging.
5. **Report filtering** — Client Sessions, Client Summary, and Session Log exports exclude unresolved review rows and excluded/non-client sessions. The All Appointments ledger remains the audit export for unresolved and excluded evidence.

### Prior Test Builds

`v0.1.0-test.10` was built from commit `424cda3` with Python 3.14.4. test.11 supersedes test.10 for installation and update testing.

`v0.1.0-test.8` was built from commit `d97d6ba` with Python 3.14.4. Its DMG
payload was correct, but a supervised installation exposed that an older
private runtime could remain installed because the prior installer requested
the shared package version `0.1.0`. test.9 and test.10 superseded test.8.

`v0.1.0-test.7` was built from commit `179da1f` with Python 3.14.4 but was
never published. It is superseded by test.11 as the current built and
distributable controlled-beta release.

The prior installed-smoke baseline remains test.6 from commit `0dec58b`. That
DMG was installed successfully on the brooketest account. Existing private
configuration and SQLite database were preserved during upgrade. Live smoke
testing passed for the major Billing Relationship, filing-owner,
delivery-contact, invoice, and data-preservation workflows.

An initial test.6 artifact built from commit `6c3dbab` using Python 3.11
was rejected before installation and was not published. The correct
replacement was built from commit `0dec58b` using Python 3.14.4 in a
clean temporary clone outside the Documents directory.

The prior test.5, test.6, test.7, test.8, test.9, and test.10 builds remain historically
accurate for the periods in which they were the current builds. test.11
supersedes test.10, test.9, and test.8 for installation and stale-runtime verification.

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

The app is not notarized unless the separate supervised Developer ID signing
step has been completed with local Apple credentials. The app does not bundle
Python itself. The clean Mac must have the Python major/minor version recorded
in `release_manifest.json` installed once before installation because the
wheelhouse may include Python-specific macOS wheels. Calendar sync can still
require internet during application use; app startup and daily launch do not
require PyPI, GitHub, or Wi-Fi.

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

Every controlled-beta release must use a unique Python package/application
version as well as a release label. Do not ship multiple beta installers with
the same package version.

```bash
scripts/build_release.sh --release-label v0.1.0-test.10
```

`JORDANA_RELEASE_LABEL=v0.1.0-test.10 scripts/build_release.sh` is equivalent.
Release labels must be simple path-safe values such as `v0.1.0-test.8` or
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
build/release/JordanaBilling-v0.1.0-test.10-<commit>-macos-arm64.dmg
build/release/JordanaBilling-v0.1.0-test.10-<commit>-macos-arm64.dmg.sha256
```

The artifact is inspected during build for forbidden private files such as
`.env`, SQLite databases, PDFs, invoices, receipts, reports, and private data
folders.

The release manifest records the exact git commit, application version,
optional release label, build ID, exact application wheel path, whether tracked
source files were dirty at build time, build timestamp, builder Python version,
required Python major/minor family, payload checksums, and whether the artifact
contains private data. The generated package embeds the same commit, build ID,
release label, and package version in `jordana_invoice.build_info`; the running
server exposes it through `/api/build-info` and `/api/health`.

## Developer ID Signing And Notarization

The default local build remains ad-hoc signed for development unless Apple
Developer credentials are available. Do not claim Gatekeeper acceptance,
notarization, or staple success unless the actual Apple commands pass.

Prepared supervised signing path:

```bash
export JORDANA_CODESIGN_IDENTITY="Developer ID Application: Example Name (TEAMID)"
export JORDANA_NOTARYTOOL_PROFILE="jordana-billing-notary"
scripts/sign_and_notarize_release.sh \
  --release-dir build/release/<release>/Install\ Jordana\ Billing.app/Contents/Resources/ReleasePayload \
  --dmg build/release/JordanaBilling-<release>-macos-arm64.dmg
```

The notary profile must be created locally in Keychain using Apple's supported
`xcrun notarytool store-credentials` workflow. Never commit Developer ID
certificates, private keys, Apple IDs, app-specific passwords, keychain exports,
or notarytool profiles.

The script signs nested executable code and app bundles with hardened runtime,
signs the DMG, submits with `xcrun notarytool submit --wait`, staples the
ticket, and then reruns `codesign`, `spctl`, `stapler validate`, and
`hdiutil verify`. Missing credentials fail clearly instead of falling back to a
fake or ad-hoc notarization result.

## SSL Certificate Environment

Calendar Sync uses the production `urllib` transport. Blank inherited
`SSL_CERT_FILE` or `REQUESTS_CA_BUNDLE` values are treated as unset before sync
requests. Nonblank explicit certificate paths remain preserved, and TLS
certificate verification stays enabled through Python's normal verified HTTPS
path. Sync errors continue to be sanitized so the Apps Script URL, API key, and
private payloads are not logged or displayed.

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
`~/Applications/Jordana Billing.app`, builds the private runtime from the exact
wheel recorded in the release manifest and shipped in the offline wheelhouse,
preserves existing private config and database files, and runs
`scripts/verify_installation.sh`.

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

### Runtime Identity Verification

The installer never relies on a shared base package version. It reads the
package version, exact app wheel path, Git commit, release label, and build ID
from `release_manifest.json`. It installs the exact wheel with
`--force-reinstall --no-index --find-links`, verifies every manifest checksum in
the release payload, verifies installed app-bundle files against the manifest,
imports the installed package's `current_build_info()`, launches the installed
app, and polls `/api/build-info` until the running server reports the expected
build ID. It reports success only after all of those checks pass.

Before replacing the app, the installer checks the stored runtime PID and the
configured port. It stops only a process that clearly looks like Jordana
Billing's local `serve-review` process; if another process owns the port, the
installer fails with a safe message instead of killing it. If verification
fails after replacement, the previous installed app/runtime is restored
automatically. `.env`, SQLite databases, reports, invoices, receipts, backups,
logs, and Documents output folders are outside the app bundle and are preserved.

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
