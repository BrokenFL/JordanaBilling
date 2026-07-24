"""Focused tests for Production Packaging V1."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent


class ProductionPackagingContractTest(unittest.TestCase):
    def test_dependency_lock_pins_exact_versions(self) -> None:
        lock = (PROJECT_DIR / "requirements-production.lock").read_text(encoding="utf-8")
        self.assertIn("reportlab==", lock)
        self.assertNotIn(">=", lock)
        self.assertNotIn("<", lock)

    def test_daily_launch_does_not_install_or_use_git(self) -> None:
        launcher = (PROJECT_DIR / "scripts" / "launch_installed_app.sh").read_text(encoding="utf-8")
        forbidden = [
            "pip install",
            "install --upgrade",
            " -m venv",
            "git clone",
            "git pull",
            "github.com",
            "wget ",
        ]
        for text in forbidden:
            self.assertNotIn(text, launcher)
        self.assertIn("Application Support/Jordana Billing", launcher)
        self.assertIn("init-db", launcher, "launch may apply application migrations to an existing DB")

    def test_installer_uses_offline_non_editable_wheel_install(self) -> None:
        installer = (PROJECT_DIR / "scripts" / "install_release.sh").read_text(encoding="utf-8")
        self.assertIn("--no-index", installer)
        self.assertIn("--find-links", installer)
        self.assertIn("--force-reinstall", installer)
        self.assertIn('"$APP_WHEEL"', installer)
        self.assertIn("__installer_manifest__", installer)
        self.assertIn("release requires Python", installer)
        self.assertIn("create_private_config.sh", installer)
        self.assertNotIn("pip install -e", installer)
        self.assertNotIn("pip install --upgrade pip", installer)
        self.assertIn("--init-empty-db", installer)
        self.assertIn("Preserved existing database", installer)
        self.assertIn("Preserved existing private configuration", installer)

    def test_release_builder_records_manifest_and_checksums(self) -> None:
        builder = (PROJECT_DIR / "scripts" / "build_release.sh").read_text(encoding="utf-8")
        self.assertIn("release_manifest.json", builder)
        self.assertIn("SHA256SUMS", builder)
        self.assertIn("wheelhouse", builder)
        self.assertIn("requirements-production.lock", builder)
        self.assertIn("build_setup_wizard.sh", builder)
        self.assertIn("Install Jordana Billing.app", builder)
        self.assertIn("ReleasePayload", builder)
        self.assertIn("git archive HEAD", builder)
        self.assertIn("BUILD_ID", builder)
        self.assertIn("sign_setup_app", builder)
        self.assertIn('SETUP_APP="$BUILD_ROOT/Install Jordana Billing.app"', builder)
        self.assertIn('PAYLOAD_DIR="$SETUP_APP/Contents/Resources/ReleasePayload"', builder)
        self.assertIn("hdiutil create", builder)
        self.assertIn("COPYFILE_DISABLE=1 hdiutil create", builder)
        self.assertIn('shasum -a 256 "$(basename "$DMG_PATH")"', builder)
        self.assertIn("docs/TEST_MAC_ACCEPTANCE.md", builder)
        self.assertIn("config/example.env", builder)
        self.assertIn("Private artifact path found", builder)
        self.assertIn('rm -rf "$PROJECT_DIR/build/lib"', builder)

    def test_release_builder_supports_optional_release_label_and_build_identity(self) -> None:
        builder = (PROJECT_DIR / "scripts" / "build_release.sh").read_text(encoding="utf-8")
        self.assertIn('RELEASE_LABEL="${JORDANA_RELEASE_LABEL:-}"', builder)
        self.assertIn("--release-label", builder)
        self.assertIn('ARTIFACT_VERSION="${RELEASE_LABEL:-$VERSION}"', builder)
        self.assertIn('RELEASE_NAME="JordanaBilling-${ARTIFACT_VERSION}-${COMMIT}-macos-arm64"', builder)
        self.assertIn('"application_version": version', builder)
        self.assertIn('"build_id": build_id', builder)
        self.assertIn('"package": {', builder)
        self.assertIn('"wheel": app_wheel_rel', builder)
        self.assertIn('manifest["release_label"] = release_label', builder)
        self.assertIn('"source_tree_dirty": source_tree_dirty', builder)
        self.assertIn("BUNDLE_SHORT_VERSION", builder)
        self.assertIn("BUNDLE_BUILD_VERSION", builder)
        self.assertIn("JORDANA_BUNDLE_SHORT_VERSION", builder)
        self.assertIn("JORDANA_BUNDLE_BUILD_VERSION", builder)
        self.assertIn('LAUNCHER_APP="$BUILD_ROOT/Jordana Billing.app"', builder)
        self.assertIn("JORDANA_LAUNCHER_APP_DIR", builder)

    def test_native_bundle_builders_accept_release_version_metadata(self) -> None:
        launcher = (PROJECT_DIR / "scripts" / "build_launcher.sh").read_text(encoding="utf-8")
        setup = (PROJECT_DIR / "scripts" / "build_setup_wizard.sh").read_text(encoding="utf-8")
        self.assertIn("JORDANA_LAUNCHER_APP_DIR", launcher)
        for text in (launcher, setup):
            self.assertIn("JORDANA_BUNDLE_SHORT_VERSION", text)
            self.assertIn("JORDANA_BUNDLE_BUILD_VERSION", text)
            self.assertIn("<string>${BUNDLE_SHORT_VERSION}</string>", text)
            self.assertIn("<string>${BUNDLE_BUILD_VERSION}</string>", text)

    def test_release_builder_rejects_unsafe_release_labels(self) -> None:
        script = PROJECT_DIR / "scripts" / "build_release.sh"
        for label in ("../bad", "bad/name", "bad name", "-bad"):
            result = subprocess.run(
                ["bash", str(script), "--release-label", label],
                capture_output=True,
                text=True,
                cwd=str(PROJECT_DIR),
            )
            self.assertEqual(result.returncode, 2, label)
            self.assertIn("Unsafe release label", result.stderr)

    def test_release_builder_rejects_missing_release_label_argument(self) -> None:
        script = PROJECT_DIR / "scripts" / "build_release.sh"
        result = subprocess.run(
            ["bash", str(script), "--release-label"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_DIR),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Missing value for --release-label", result.stderr)

    def test_release_uses_unique_package_version_for_next_beta(self) -> None:
        installer = (PROJECT_DIR / "scripts" / "install_release.sh").read_text(encoding="utf-8")
        pyproject = (PROJECT_DIR / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('version = "0.1.0.post28"', pyproject)
        self.assertNotIn("jordana-invoice==0.1.0", installer)
        self.assertIn("PACKAGE_VERSION", installer)

    def test_installer_verifies_manifest_and_running_build_before_success(self) -> None:
        installer = (PROJECT_DIR / "scripts" / "install_release.sh").read_text(encoding="utf-8")
        self.assertIn("verify_release_payload_checksums", installer)
        self.assertIn("verify_installed_app_manifest", installer)
        self.assertIn("verify_installed_package_identity", installer)
        self.assertIn("verify_running_server_build_id", installer)
        self.assertIn("/api/build-info", installer)
        self.assertIn("EXPECTED_BUILD_ID", installer)
        self.assertIn('bash "$RELEASE_DIR/scripts/verify_installation.sh"', installer)
        self.assertIn('bash "$APP_DEST/Contents/Resources/launch_installed_app.sh"', installer)
        self.assertNotIn('codesign --force --deep --sign - "$APP_DEST"', installer)
        self.assertLess(
            installer.index('VENV_PYTHON="$APP_DEST/Contents/Resources/runtime/venv/bin/python"'),
            installer.index("verify_running_server_build_id ||"),
        )
        self.assertLess(installer.index("verify_running_server_build_id"), installer.index('echo "Jordana Billing release installed successfully."'))

    def test_installer_coordinates_with_running_app_without_unknown_kill(self) -> None:
        installer = (PROJECT_DIR / "scripts" / "install_release.sh").read_text(encoding="utf-8")
        self.assertIn("stop_existing_app_for_install", installer)
        self.assertIn("pid_looks_like_jordana", installer)
        self.assertIn("No process was stopped", installer)
        self.assertIn("Port $PORT is in use by another application", installer)

    def test_release_builder_keeps_launcher_and_rollback_payload_contracts(self) -> None:
        builder = (PROJECT_DIR / "scripts" / "build_release.sh").read_text(encoding="utf-8")
        self.assertIn('"$PROJECT_DIR/scripts/build_launcher.sh" --force', builder)
        self.assertIn('cp "$PROJECT_DIR/scripts/install_release.sh"', builder)
        self.assertIn("clean_and_sign_app", builder)
        self.assertLess(builder.index('clean_and_sign_app "$RELEASE_DIR/Jordana Billing.app"'), builder.index('"checksums": checksums'))
        self.assertLess(builder.index('"checksums": checksums'), builder.index('sign_setup_app "$SETUP_APP"'))
        self.assertIn('codesign --force --sign - --timestamp=none "$app_path"', builder)
        self.assertNotIn('codesign --force --deep --sign - --timestamp=none "$SETUP_APP"', builder)

    def test_installed_launcher_resource_is_committed_without_private_data(self) -> None:
        resource = PROJECT_DIR / "Jordana Billing.app" / "Contents" / "Resources" / "launch_installed_app.sh"
        self.assertTrue(resource.is_file())
        text = resource.read_text(encoding="utf-8")
        self.assertIn("Installed Runtime Missing", text)
        self.assertNotIn("jb_", text)
        self.assertNotIn("BEGIN PRIVATE KEY", text)

    def test_test_mac_acceptance_document_exists(self) -> None:
        doc = PROJECT_DIR / "docs" / "TEST_MAC_ACCEPTANCE.md"
        self.assertTrue(doc.is_file())
        text = doc.read_text(encoding="utf-8")
        for phrase in [
            "Verify checksum",
            "Install Jordana Billing",
            "Rosetta prompt",
            "missing config",
            "missing DB",
            "Reinstall",
            "private data remains",
        ]:
            self.assertIn(phrase, text)

    def test_documentation_gives_one_authoritative_config_workflow(self) -> None:
        doc = (PROJECT_DIR / "docs" / "PRODUCTION_PACKAGING.md").read_text(encoding="utf-8")
        self.assertIn("Private Configuration Setup", doc)
        self.assertIn("~/Library/Application Support/Jordana Billing/config/.env", doc)
        self.assertIn("Install Jordana Billing.app", doc)
        self.assertIn("The API key input is hidden", doc)
        self.assertIn("not stored inside the `.app`, release DMG, GitHub, SQLite database, or browser storage", doc)


class NativeSetupWizardContractTest(unittest.TestCase):
    def test_setup_wizard_uses_native_secure_field(self) -> None:
        source = (PROJECT_DIR / "packaging" / "macos" / "SetupWizard.swift").read_text(encoding="utf-8")
        self.assertIn("NSSecureTextField", source)
        self.assertIn("validURL", source)
        self.assertIn("Bundle.main.resourceURL", source)
        self.assertIn("Initialize a clean production database?", source)
        self.assertIn("Existing private config found and will be preserved.", source)
        self.assertIn("Existing database found and will be preserved.", source)

    def test_setup_wizard_resolves_embedded_payload(self) -> None:
        source = (PROJECT_DIR / "packaging" / "macos" / "SetupWizard.swift").read_text(encoding="utf-8")
        payload_block = source[
            source.index("private var payloadRoot: URL") :
            source.index("private var supportRoot: URL")
        ]
        self.assertIn("Bundle.main.resourceURL", payload_block)
        self.assertIn('appendingPathComponent("ReleasePayload")', payload_block)
        self.assertNotIn("deletingLastPathComponent()", payload_block)

    def test_setup_wizard_finds_and_passes_compatible_python(self) -> None:
        source = (PROJECT_DIR / "packaging" / "macos" / "SetupWizard.swift").read_text(encoding="utf-8")
        self.assertIn("findCompatiblePython(required:", source)
        self.assertIn("requiredPythonPrefix", source)
        self.assertIn("/usr/local/bin/python3", source)
        self.assertIn("/opt/homebrew/bin/python3", source)
        self.assertIn("/Library/Frameworks/Python.framework/Versions/", source)
        self.assertIn('environment["JORDANA_INSTALL_PYTHON"] = python.path', source)
        self.assertIn("process.environment = environment", source)

    def test_setup_wizard_has_normal_quit_behavior(self) -> None:
        source = (PROJECT_DIR / "packaging" / "macos" / "SetupWizard.swift").read_text(encoding="utf-8")
        self.assertIn("NSWindowDelegate", source)
        self.assertIn("applicationShouldTerminateAfterLastWindowClosed", source)
        self.assertIn("windowShouldClose", source)
        self.assertIn("buildMenu()", source)
        self.assertIn("Quit Install Jordana Billing", source)

    def test_setup_wizard_fresh_install_keeps_required_inputs_enabled(self) -> None:
        source = (PROJECT_DIR / "packaging" / "macos" / "SetupWizard.swift").read_text(encoding="utf-8")
        config_detection = source.index("if fm.fileExists(atPath: configPath.path)")
        db_detection = source.index("if fm.fileExists(atPath: dbPath.path)")
        self.assertNotIn("urlField.isEnabled = false", source[:config_detection])
        self.assertNotIn("keyField.isEnabled = false", source[:config_detection])
        self.assertNotIn("cleanStart.isEnabled = false", source[:db_detection])
        self.assertIn("if !hasConfig", source)
        self.assertIn("guard validURL(appsURL)", source)
        self.assertIn("guard !apiKey.isEmpty", source)
        self.assertIn("if !hasDb && cleanStart.state != .on", source)

    def test_setup_wizard_reinstall_preserves_existing_config_and_database(self) -> None:
        source = (PROJECT_DIR / "packaging" / "macos" / "SetupWizard.swift").read_text(encoding="utf-8")
        config_block = source[
            source.index("if fm.fileExists(atPath: configPath.path)") :
            source.index("if fm.fileExists(atPath: dbPath.path)")
        ]
        db_block = source[
            source.index("if fm.fileExists(atPath: dbPath.path)") :
            source.index("status.stringValue = messages.joined")
        ]
        self.assertIn("Existing private config found and will be preserved.", config_block)
        self.assertIn("urlField.isEnabled = false", config_block)
        self.assertIn("keyField.isEnabled = false", config_block)
        self.assertIn("Existing database found and will be preserved.", db_block)
        self.assertIn("cleanStart.isEnabled = false", db_block)
        self.assertIn("if !hasConfig", source)
        self.assertIn("if !hasDb", source)

    def test_setup_wizard_does_not_pass_secrets_as_arguments(self) -> None:
        source = (PROJECT_DIR / "packaging" / "macos" / "SetupWizard.swift").read_text(encoding="utf-8")
        self.assertIn("JORDANA_INGEST_API_KEY=", source)
        self.assertIn("process.arguments = initEmptyDb ? [script.path, \"--init-empty-db\", \"--yes\"] : [script.path]", source)
        self.assertNotIn("--api-key", source)
        self.assertNotIn("apiKey]", source)

    def test_setup_wizard_cancel_does_not_write_before_install(self) -> None:
        source = (PROJECT_DIR / "packaging" / "macos" / "SetupWizard.swift").read_text(encoding="utf-8")
        self.assertIn("@objc private func install()", source)
        self.assertIn("if alert.runModal() != .alertFirstButtonReturn", source)
        self.assertLess(source.index("if alert.runModal() != .alertFirstButtonReturn"), source.index("try self.writeConfig"))

    def test_setup_wizard_builder_creates_native_app(self) -> None:
        builder = (PROJECT_DIR / "scripts" / "build_setup_wizard.sh").read_text(encoding="utf-8")
        self.assertIn("swiftc -target arm64-apple-macos12", builder)
        self.assertIn("CFBundleExecutable", builder)
        self.assertIn("InstallJordanaBilling", builder)
        self.assertIn("codesign", builder)

    def test_release_dmg_payload_hides_daily_app_from_root(self) -> None:
        builder = (PROJECT_DIR / "scripts" / "build_release.sh").read_text(encoding="utf-8")
        self.assertIn('ditto --norsrc "$SETUP_APP" "$DMG_ROOT/Install Jordana Billing.app"', builder)
        self.assertIn('mv "$RELEASE_DIR" "$PAYLOAD_DIR"', builder)
        self.assertIn('sign_and_notarize_release.sh', builder)
        self.assertIn("clean_and_sign_app", builder)
        self.assertIn("codesign --verify --deep --strict", builder)
        self.assertIn("The release payload is embedded inside the installer app.", builder)
        self.assertNotIn('PAYLOAD_DIR="$DMG_ROOT/ReleasePayload"', builder)

    def test_signing_notarization_script_requires_local_credentials(self) -> None:
        script = PROJECT_DIR / "scripts" / "sign_and_notarize_release.sh"
        source = script.read_text(encoding="utf-8")
        self.assertIn("JORDANA_CODESIGN_IDENTITY", source)
        self.assertIn("JORDANA_NOTARYTOOL_PROFILE", source)
        self.assertIn("xcrun notarytool submit", source)
        self.assertIn("xcrun stapler staple", source)
        self.assertIn("codesign --verify --deep --strict --verbose=2", source)
        self.assertIn("spctl --assess", source)
        self.assertIn("hdiutil verify", source)
        self.assertNotIn("APPLE_ID", source)
        self.assertNotIn("APP_SPECIFIC_PASSWORD", source)
        result = subprocess.run(
            ["bash", str(script), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("JORDANA_NOTARYTOOL_PROFILE", result.stdout)

    def test_checksum_generation_rejects_absolute_paths(self) -> None:
        builder = (PROJECT_DIR / "scripts" / "build_release.sh").read_text(encoding="utf-8")
        self.assertIn("basename \"$DMG_PATH\"", builder)
        self.assertIn("Malformed checksum path", builder)
        self.assertNotIn('shasum -a 256 "$DMG_PATH" >', builder)

    def test_installed_launcher_probes_health_before_lsof_only_decision(self) -> None:
        launcher = (PROJECT_DIR / "scripts" / "launch_installed_app.sh").read_text(encoding="utf-8")
        self.assertIn("http_service_status", launcher)
        self.assertIn("port_accepts_tcp", launcher)
        self.assertIn("Jordana Billing is already running under another macOS user account", launcher)
        self.assertLess(launcher.index("status=\"$(http_service_status"), launcher.index("port_pid=\"$(pid_on_port)\""))


class InstallerRollbackSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="jordana_installer_rollback_")
        self.root = Path(self.temp.name)
        self.release = self.root / "release"
        self.app_dest = self.root / "Applications" / "Jordana Billing.app"
        self.support = self.root / "Support"
        self.docs = self.root / "Documents"
        self.bin = self.root / "bin"
        self._build_fake_release()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_executable(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    def _build_fake_release(self) -> None:
        payload_app = self.release / "Jordana Billing.app"
        (payload_app / "Contents" / "Resources").mkdir(parents=True)
        (payload_app / "Contents" / "marker.txt").write_text("new", encoding="utf-8")
        (self.release / "wheelhouse").mkdir(parents=True)
        (self.release / "wheelhouse" / "jordana_invoice-0.1.0.post10-py3-none-any.whl").write_text("wheel", encoding="utf-8")
        (self.release / "release_manifest.json").write_text(
            json.dumps(
                {
                    "runtime": {"requires_python": "3.14.x"},
                    "package": {
                        "name": "jordana-invoice",
                        "version": "0.1.0.post10",
                        "wheel": "wheelhouse/jordana_invoice-0.1.0.post10-py3-none-any.whl",
                    },
                    "build_id": "v0.1.0-test.10-testbuild",
                    "git_commit": "test",
                    "checksums": {},
                }
            ),
            encoding="utf-8",
        )
        scripts = self.release / "scripts"
        scripts.mkdir()
        shutil.copy(PROJECT_DIR / "scripts" / "install_release.sh", scripts / "install_release.sh")
        (scripts / "install_release.sh").chmod(0o755)
        self._write_verify_script(0)
        self._write_fake_tools()
        self._write_private_files()

    def _write_fake_tools(self) -> None:
        self._write_executable(
            self.bin / "uname",
            """#!/usr/bin/env bash
if [[ "${1:-}" == "-s" ]]; then echo Darwin; elif [[ "${1:-}" == "-m" ]]; then echo arm64; else /usr/bin/uname "$@"; fi
""",
        )
        self._write_executable(
            self.bin / "ditto",
            """#!/usr/bin/env bash
if [[ "${1:-}" == "--norsrc" ]]; then shift; fi
cp -R "$1" "$2"
""",
        )
        for name in ("xattr", "codesign"):
            self._write_executable(self.bin / name, "#!/usr/bin/env bash\nexit 0\n")
        self._write_executable(
            self.bin / "python3",
            """#!/usr/bin/env bash
if [[ "${1:-}" == "-" && "${3:-}" == "__installer_manifest__" ]]; then
  printf '0.1.0.post10\twheelhouse/jordana_invoice-0.1.0.post10-py3-none-any.whl\tv0.1.0-test.10-testbuild\ttest\tv0.1.0-test.10\n'
  exit 0
fi
if [[ "${1:-}" == "-" ]]; then exit 0; fi
if [[ "${1:-}" == "-m" && "${2:-}" == "venv" ]]; then
  mkdir -p "$3/bin"
  cp "$0" "$3/bin/python"
  exit 0
fi
if [[ "${1:-}" == "-m" && "${2:-}" == "pip" ]]; then exit 0; fi
if [[ "${1:-}" == "-c" ]]; then exit 0; fi
if [[ "${1:-}" == "-m" && "${2:-}" == "jordana_invoice" ]]; then
  while [[ $# -gt 0 ]]; do
    if [[ "$1" == "--db" ]]; then shift; touch "$1"; exit 0; fi
    shift
  done
fi
exit 0
""",
        )

    def _write_private_files(self) -> None:
        (self.support / "config").mkdir(parents=True)
        (self.support / "data").mkdir(parents=True)
        (self.support / "config" / ".env").write_text(
            "JORDANA_APPS_SCRIPT_URL=https://example.invalid/app\nJORDANA_INGEST_API_KEY=secret-test-key\n",
            encoding="utf-8",
        )
        (self.support / "data" / "jordana_invoice.sqlite3").write_text("db", encoding="utf-8")

    def _write_verify_script(self, exit_code: int, output: str = "") -> None:
        self._write_executable(
            self.release / "scripts" / "verify_installation.sh",
            f"#!/usr/bin/env bash\nprintf '%s' {output!r}\nexit {exit_code}\n",
        )

    def _write_existing_app(self, marker: str = "old") -> None:
        (self.app_dest / "Contents").mkdir(parents=True)
        (self.app_dest / "Contents" / "marker.txt").write_text(marker, encoding="utf-8")

    def _run_installer(self) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.bin}:{env['PATH']}",
                "JORDANA_INSTALL_PYTHON": str(self.bin / "python3"),
                "JORDANA_INSTALL_APP_DEST": str(self.app_dest),
                "JORDANA_APP_SUPPORT_DIR": str(self.support),
                "JORDANA_DOCUMENTS_ROOT": str(self.docs),
            }
        )
        return subprocess.run(
            ["bash", str(self.release / "scripts" / "install_release.sh"), "--skip-launch-verify"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(self.release),
        )

    def _private_contents(self) -> tuple[str, str]:
        return (
            (self.support / "config" / ".env").read_text(encoding="utf-8"),
            (self.support / "data" / "jordana_invoice.sqlite3").read_text(encoding="utf-8"),
        )

    def test_fresh_install_with_no_previous_app(self) -> None:
        before = self._private_contents()
        result = self._run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.app_dest / "Contents" / "marker.txt").read_text(encoding="utf-8"), "new")
        self.assertFalse(Path(f"{self.app_dest}.previous").exists())
        self.assertFalse(Path(f"{self.app_dest}.installing").exists())
        self.assertEqual(self._private_contents(), before)

    def test_successful_upgrade_removes_backup(self) -> None:
        self._write_existing_app("old")
        result = self._run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.app_dest / "Contents" / "marker.txt").read_text(encoding="utf-8"), "new")
        self.assertFalse(Path(f"{self.app_dest}.previous").exists())

    def test_failed_verification_restores_previous_app(self) -> None:
        self._write_existing_app("old")
        self._write_verify_script(42, "secret-test-key https://example.invalid/app")
        before = self._private_contents()
        result = self._run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.app_dest / "Contents" / "marker.txt").read_text(encoding="utf-8"), "old")
        self.assertFalse(Path(f"{self.app_dest}.previous").exists())
        self.assertFalse(Path(f"{self.app_dest}.installing").exists())
        self.assertEqual(self._private_contents(), before)
        self.assertNotIn("secret-test-key", result.stderr + result.stdout)
        self.assertNotIn("https://example.invalid/app", result.stderr + result.stdout)

    def test_failed_verification_without_previous_app_removes_failed_app(self) -> None:
        self._write_verify_script(42)
        result = self._run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.app_dest.exists())
        self.assertFalse(Path(f"{self.app_dest}.installing").exists())

    def test_stale_installing_path_is_cleaned_before_install(self) -> None:
        stale = Path(f"{self.app_dest}.installing")
        (stale / "Contents").mkdir(parents=True)
        (stale / "Contents" / "marker.txt").write_text("stale", encoding="utf-8")
        result = self._run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(stale.exists())
        self.assertEqual((self.app_dest / "Contents" / "marker.txt").read_text(encoding="utf-8"), "new")

    def test_stale_backup_is_removed_when_current_app_exists(self) -> None:
        self._write_existing_app("current")
        previous = Path(f"{self.app_dest}.previous")
        (previous / "Contents").mkdir(parents=True)
        (previous / "Contents" / "marker.txt").write_text("stale", encoding="utf-8")
        result = self._run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(previous.exists())
        self.assertEqual((self.app_dest / "Contents" / "marker.txt").read_text(encoding="utf-8"), "new")

    def test_interrupted_install_backup_recovers_and_upgrades(self) -> None:
        previous = Path(f"{self.app_dest}.previous")
        (previous / "Contents").mkdir(parents=True)
        (previous / "Contents" / "marker.txt").write_text("old", encoding="utf-8")
        result = self._run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(previous.exists())
        self.assertEqual((self.app_dest / "Contents" / "marker.txt").read_text(encoding="utf-8"), "new")

    def test_restore_failure_message_preserves_manual_recovery_contract(self) -> None:
        installer = (PROJECT_DIR / "scripts" / "install_release.sh").read_text(encoding="utf-8")
        rollback_block = installer[
            installer.index("rollback_replacement()") : installer.index("replace_app_bundle()")
        ]
        self.assertIn("Automatic restore failed", rollback_block)
        self.assertIn("Jordana Billing.app.previous", rollback_block)
        self.assertNotIn('rm -rf "$PREVIOUS_APP"', rollback_block)


class PrivateConfigHelperTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="jordana_config_helper_")
        self.config_path = Path(self.temp.name) / "config" / ".env"
        self.script = PROJECT_DIR / "scripts" / "create_private_config.sh"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _run_helper(self, stdin: str, *, path: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["JORDANA_CONFIG_OUTPUT"] = str(path or self.config_path)
        return subprocess.run(
            ["bash", str(self.script)],
            input=stdin,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(PROJECT_DIR),
        )

    def test_helper_writes_required_keys_with_600_permissions(self) -> None:
        result = self._run_helper("https://example.invalid/apps-script\nsecret-test-key\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.config_path.is_file())
        self.assertEqual(oct(self.config_path.stat().st_mode & 0o777), "0o600")
        lines = self.config_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            lines,
            [
                "JORDANA_APPS_SCRIPT_URL=https://example.invalid/apps-script",
                "JORDANA_INGEST_API_KEY=secret-test-key",
            ],
        )

    def test_helper_does_not_print_api_key(self) -> None:
        result = self._run_helper("https://example.invalid/apps-script\nsecret-test-key\n")
        combined = result.stdout + result.stderr
        self.assertNotIn("secret-test-key", combined)
        self.assertIn("Private config created at:", combined)

    def test_helper_does_not_accept_api_key_argument(self) -> None:
        result = subprocess.run(
            ["bash", str(self.script), "--api-key", "secret-test-key"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_DIR),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.config_path.exists())

    def test_empty_url_is_rejected_without_partial_file(self) -> None:
        result = self._run_helper("\nsecret-test-key\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Apps Script URL cannot be empty", result.stderr)
        self.assertFalse(self.config_path.exists())

    def test_empty_api_key_is_rejected_without_partial_file(self) -> None:
        result = self._run_helper("https://example.invalid/apps-script\n\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Ingest API key cannot be empty", result.stderr)
        self.assertFalse(self.config_path.exists())

    def test_invalid_url_is_rejected_without_partial_file(self) -> None:
        result = self._run_helper("not-a-url\nsecret-test-key\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("valid https URL", result.stderr)
        self.assertFalse(self.config_path.exists())

    def test_existing_config_is_not_overwritten_silently(self) -> None:
        self.config_path.parent.mkdir(parents=True)
        self.config_path.write_text("JORDANA_APPS_SCRIPT_URL=https://old.invalid\nJORDANA_INGEST_API_KEY=old\n", encoding="utf-8")
        self.config_path.chmod(0o600)
        result = self._run_helper("\n")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Existing config was preserved", result.stdout)
        self.assertIn("old", self.config_path.read_text(encoding="utf-8"))

    def test_existing_config_can_be_overwritten_explicitly(self) -> None:
        self.config_path.parent.mkdir(parents=True)
        self.config_path.write_text("old\n", encoding="utf-8")
        self.config_path.chmod(0o600)
        result = self._run_helper("OVERWRITE\nhttps://example.invalid/apps-script\nnew-key\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("new-key", self.config_path.read_text(encoding="utf-8"))

    def test_cancelled_input_leaves_no_partial_config(self) -> None:
        result = self._run_helper("")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.config_path.exists())

    def test_helper_uses_standard_config_path_by_default(self) -> None:
        helper = self.script.read_text(encoding="utf-8")
        self.assertIn("$HOME/Library/Application Support/Jordana Billing", helper)
        self.assertIn("config/.env", helper)


class ReleaseArtifactBuildSmokeTest(unittest.TestCase):
    def test_manifest_shape_example(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jordana_manifest_") as tmp:
            manifest = Path(tmp) / "release_manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "application": "Jordana Billing",
                        "version": "0.1.0",
                        "git_commit": "example",
                        "supported_architecture": "arm64",
                        "checksums": {"Jordana Billing.app/Contents/Info.plist": "abc"},
                    }
                ),
                encoding="utf-8",
            )
            data = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(data["application"], "Jordana Billing")
            self.assertEqual(data["supported_architecture"], "arm64")
            self.assertIn("checksums", data)

    def test_new_shell_scripts_parse(self) -> None:
        for script in [
            "scripts/build_release.sh",
            "scripts/create_private_config.sh",
            "scripts/install_release.sh",
            "scripts/launch_installed_app.sh",
            "scripts/sign_and_notarize_release.sh",
            "scripts/update_release.sh",
            "scripts/verify_installation.sh",
        ]:
            result = subprocess.run(
                ["bash", "-n", str(PROJECT_DIR / script)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, f"{script}: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
