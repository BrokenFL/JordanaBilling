# Software updates

The app checks an explicit promotion feed on launch and at most once daily.
A manual **Check for updates** button can refresh it sooner. Only a newer
version explicitly enabled in `updates/jordana.json` is offered. Publishing a
GitHub release alone does not offer it to Jordana. Test.40 was the first
promotion through this feed. Each newer release, including Test.42, requires
its own verified offer.

The default feed is the `main` branch's `updates/jordana.json` on
`raw.githubusercontent.com/BrokenFL/JordanaBilling`. Checks send no calendar,
participant, invoice, database, or credential data. Network failures leave the
billing app usable. **Later** hides that version until a manual check or a newer
offer. Notifications appear while the app is open; no background notification
agent is installed.

**Update and restart** requires a deliberate click and a reminder to save
unfinished edits. The server validates the local write token, refreshes the
maintainer offer, rejects a changed version and concurrent update, and creates a
verified private database backup before launching a detached worker. It only
updates the normal installed Apple Silicon app's configured database path.

The worker is copied outside the installed app and runs with the base Python
interpreter so replacing the app/runtime cannot terminate it. It downloads the
exact promoted GitHub DMG, checks its pinned SHA-256, verifies disk-image and
bundle signature integrity, checks embedded manifest identity and file hashes,
and invokes the existing installer. That installer coordinates shutdown,
verifies the installed build, handles runtime rollback on failure, and relaunches
the app. Update progress/failure is retained in Application Support's `updates`
directory. Financial data is never included in the downloaded artifact. A
checksum and ad-hoc bundle signature are integrity checks; release authenticity
currently relies on HTTPS and control of the fixed GitHub repository/feed.

The detached worker and installer run with the stable update-job directory as
their explicit working directory. The installed launcher changes to Application
Support before starting Python. A server must never inherit a removable app
bundle or mounted-image directory: its first ReportLab import calls `os.getcwd()`
and fails if that inherited directory was deleted after startup. Verify PDF
generation after old-bundle cleanup and installer ejection, not only while the
release payload remains present.

## Maintainer promotion

1. Build, test, publish and independently verify a release using the existing
   packaging procedure. Do not promote an unverified artifact.
2. Prepare an offer from the verified downloaded DMG and its checksum:

   ```bash
   python3 scripts/prepare_update_offer.py /path/to/verified-release.dmg \
     --commit FULL_SOURCE_COMMIT --notes-file /path/to/short-release-notes.md
   ```

3. Review `updates/jordana.json`, run privacy/Git safety checks, then explicitly
   publish that file to `main` when Jordana should receive the notice. Preparing
   the file does not publish it. Setting `enabled` to false withdraws an offer
   before installation starts; it does not interrupt an already running update.
4. Test.39 and newer installations already contain the updater. Jordana can use
   **Check for Updates**, then **Update and restart**, after a verified offer is
   promoted. The discovery, worker, routing and failure paths have automated
   tests; installed-Mac confirmation remains separate evidence.

## Current verified offer

Test.45 (`0.1.0.post45`) is offered from clean source
`03ba27284dfcc0743a1722d60d3f168b594cfb5a`. Its publicly downloaded DMG has
SHA-256 `d3b2e921b1468a6fbff7efac2e65953abf6016157235250788193335b711a8f0`.
The public image passed the updater's checksum, signature, and payload checks.
An isolated Test.44-to-Test.45 upgrade preserved fictional billing history and
configuration. Session approval, invoice PDFs, and payment posting passed on the
installed build. On Jordana's Mac, choose **Check for Updates → Update and
restart**. Installation on her Mac remains a separate confirmation.

## Previous verified offer

Test.44 (`0.1.0.post44`) was promoted from clean source
`76f4a7c61d7b33452713d3b2de84206bd9362641`. Its publicly downloaded DMG has
SHA-256 `e77c1ddab617afd65645dd0b2bb89697518fc53e30d4a0d7edd6d6d8d4d447a0`.
Source CI and public artifact identity/signature/privacy verification passed.
An actual isolated Test.43-to-44 installer upgrade from the old app's working
directory preserved fictional billing history and generated draft/final PDFs
after the previous app was removed. Eleven focused launcher/updater/packaging
checks also passed. A currently affected user should Quit and reopen the
installed app, then choose Check for Updates and Update and restart.
Recipient-device confirmation remains pending.

## Previous verified offers

Test.43 (`0.1.0.post43`) is promoted from clean source
`732f50cd077df088bced3e531616ef398bc8f793`. Its publicly downloaded DMG has
SHA-256 `2aebbd03c1da82fdf4db564372825770a5b6c20655e8addc1c4aa8c6861add9a`.
Checksum, signature, disk-image, embedded manifest/hashes, runtime identity,
privacy checks, and packaged-wheel recovery tests passed. The changed-feature
checks cover all-status Sessions navigation, late-cancellation recovery,
explicit excluded-record restoration, rollback, and protected approved records.
Jordana can choose **Check for Updates → Update and restart**, then open Review
or Sync Calendar and confirm a recovered cancellation's client, Bill To, and
fee before approval. Installation on her Mac remains a device acceptance step.

## Previous verified offer

Test.42 (`0.1.0.post42`) is promoted from clean source
`bed7ad690525d6d5a008efe0da21516b22b1387e`. The published DMG has SHA-256
`34a79ab4d5cd99048a81f31ec56de6f4a91507d6c84149e3ac79ad2f3c2861d7`.
Source CI and independent downloaded-artifact verification passed.

The isolated Test.41-to-Test.42 pre-promotion check used fictional records and
a local offer override for discovery only. The production worker downloaded
the public asset, verified it, installed it, and restarted the app. A protected
verified backup, raw calendar evidence, approved rates, finalized invoice, payment
allocation and configuration were preserved. The updated report included both
previously omitted approvals and the comparison matched both calendar rows.
This does not establish installation on Jordana's actual Mac or iPhone.
