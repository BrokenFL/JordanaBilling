# Software updates

The app checks an explicit promotion feed on launch and at most once daily.
A manual **Check for updates** button can refresh it sooner. Only a newer
version explicitly enabled in `updates/jordana.json` is offered. Publishing a
GitHub release alone does not offer it to Jordana. Test.40 is the first planned
promotion through this feed after its published DMG is independently verified.

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
