# v0.1.0-test.8 Jordana Mac Installation Incident

Date: 2026-07-03

This document records the supervised installation of `v0.1.0-test.8` on Jordana's Mac, the two runtime problems encountered, the successful recovery, and the packaging changes required before the next release.

## Release Identified

- Release: `v0.1.0-test.8`
- DMG: `JordanaBilling-v0.1.0-test.8-d97d6babc227-macos-arm64.dmg`
- Manifest commit: `d97d6babc2278bd1e19fbc36319d65acce24fbb4`
- SHA-256: `8cf5176bd5aba1aef79c798f4fe01955d358f988237c33efeaaa782842cb266b`
- GitHub release type: private prerelease
- Download checksum verification: passed
- `hdiutil verify`: passed before publication

The operational SQLite database and private configuration remained outside the app bundle under Application Support. A verified SQLite backup was created before the update. No database, configuration, invoice, payment, or generated-document data was intentionally overwritten during troubleshooting.

## Final Operational Result

After recovery:

- Jordana Billing launched from `~/Applications/Jordana Billing.app`.
- Calendar Sync worked.
- The installed package and the local browser server exposed the test.8 code markers.
- The four test.8 fixes were present:
  1. corrected Needs Classification ledger filtering;
  2. future appointments excluded from actionable review/dashboard counts;
  3. Needs Classification / Send to Review available in the Sessions advanced review-status filter;
  4. review overlay scroll reset when opening or switching candidates.
- Existing private configuration and SQLite operational data were preserved.

Jordana does not need to perform an additional repair for this installation. Brooke should retain the verified pre-test.8 backup and the exact published DMG until the June billing cycle and restart smoke checks are complete.

## Incident 1: Calendar Sync Certificate Failure

### Symptom

The installed app reported:

```text
Network failure during sync: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate>
```

### Confirmed Evidence

- The private app Python runtime could successfully open `https://www.google.com` and `https://script.google.com`.
- A direct POST to the exact configured Apps Script endpoint succeeded through the normal `script.google.com` to `script.googleusercontent.com` redirect chain.
- The running app process contained empty `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` environment values.
- Setting both values in the preserved private `.env` to the valid Python 3.14 certificate bundle resolved Calendar Sync after restart.

The repair used the existing trusted CA bundle and did not disable TLS certificate verification.

### Future Packaging Requirement

Before the next release:

1. Do not export blank certificate-related environment variables into the app process.
2. Treat an empty value as unset, not as an explicit certificate override.
3. Prefer a deterministic trusted-CA strategy, such as an explicitly bundled `certifi` dependency or a validated system-Python CA path.
4. Add an installed-app verification that performs a real HTTPS request through the same `urllib` path used by Calendar Sync, including the Apps Script redirect host, without exposing the URL path or API key.
5. Keep TLS verification enabled. Never solve this by using an unverified SSL context.

The current manual `.env` certificate path is tied to the installed Python 3.14 framework. It should be revisited if the required Python family changes.

## Incident 2: Installed Runtime Did Not Contain test.8 Code

### Symptom

The setup app completed and the new app bundle launched, but the expected test.8 behavior did not appear.

### Confirmed Evidence

The following markers were absent from both the installed package and the static files served on port `8765`:

- `Needs classification / Send to Review`
- `function resetReviewOverlayScroll`
- `def actionable_review_time_filter`
- `filters.review_status == "needs_classification"`

The downloaded DMG was then mounted read-only and inspected directly. Its release manifest correctly named commit `d97d6babc2278bd1e19fbc36319d65acce24fbb4`, and the embedded `jordana_invoice-0.1.0` wheel contained all four expected test.8 markers.

Therefore:

- the published DMG was correct;
- the embedded wheel was correct;
- the installed runtime was stale after the GUI setup/update path;
- this was not a Safari cache problem.

The exact setup-app failure mechanism was not conclusively identified during the supervised recovery. Do not state that the wheel, manifest, or release artifact was stale.

### Recovery Performed

With Jordana Billing stopped, the exact wheel from the verified DMG was installed into the app's private virtual environment using a forced local reinstall. No network package source was used. The app bundle was re-signed ad hoc, relaunched, and verified by checking both installed source files and the static files served by the running local server.

This repair changed application runtime code only. It did not modify the SQLite database or private configuration.

### Future Installer Requirements

Before the next release, the native setup/update path should be hardened as follows:

1. Stop or explicitly coordinate with an existing Jordana Billing server before app-bundle replacement.
2. Install from the exact wheel path recorded in the release payload, rather than relying only on the shared package version string `0.1.0`.
3. Use deterministic replacement semantics for repeated test releases that share the same application/package version, including `--force-reinstall` where appropriate.
4. Record the exact wheel digest in the release manifest.
5. After installation, verify the installed package against the expected release, not merely that `import jordana_invoice` succeeds.
6. Add a release/build identifier or source-commit marker that can be read from the installed package and exposed through a sanitized health/version endpoint.
7. Compare installed critical-file hashes or a packaged build manifest against the release payload before deleting `Jordana Billing.app.previous`.
8. If the installed runtime does not match the release payload, fail installation and restore `.previous` automatically.
9. Add an acceptance test for upgrading from a running prior release to a new release label while the Python package version remains unchanged.
10. Verify the four release-specific markers through the installed server after every beta update.

## Required Regression Test

Create an automated or scripted acceptance scenario with these conditions:

1. Install release A with package version `0.1.0`.
2. Launch it and leave its local server running.
3. Build release B with the same package version but a different release label and an unmistakable sanitized code marker.
4. Run the native GUI update path.
5. Confirm the prior server is stopped or safely coordinated.
6. Confirm private `.env`, SQLite data, invoices, receipts, and reports are preserved.
7. Confirm the installed runtime and served static files contain release B's marker.
8. Confirm the installed build/commit identity matches release B's manifest.
9. Simulate a mismatch and verify automatic rollback to release A.

This regression must run against a temporary or sanitized database, never the operational SQLite database.

## Current Classification

- test.8 release artifact: verified and usable for the current controlled installation.
- Jordana's current repaired installation: operational.
- Database/config preservation: passed during this supervised update.
- GUI same-version update reliability: unresolved packaging defect and a blocker for claiming final production-grade unattended updates.
- Full clean-Mac and restart acceptance evidence: still incomplete.

## Privacy

This incident record contains no credentials, Apps Script URL path, API key, live database, client records, invoices, receipts, clinical information, private screenshots, or private logs.
