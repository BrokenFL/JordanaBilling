"""Focused tests for Production Packaging V1."""

from __future__ import annotations

import json
import os
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
        self.assertIn("jordana-invoice==0.1.0", installer)
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
        self.assertIn("hdiutil create", builder)
        self.assertIn('shasum -a 256 "$(basename "$DMG_PATH")"', builder)
        self.assertIn("docs/TEST_MAC_ACCEPTANCE.md", builder)
        self.assertIn("config/example.env", builder)
        self.assertIn("Private artifact path found", builder)

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
        self.assertIn("Initialize a clean production database?", source)
        self.assertIn("Existing private config found and will be preserved.", source)
        self.assertIn("Existing database found and will be preserved.", source)

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
        self.assertIn('ditto --norsrc "$BUILD_ROOT/Install Jordana Billing.app" "$DMG_ROOT/Install Jordana Billing.app"', builder)
        self.assertIn('mv "$RELEASE_DIR" "$PAYLOAD_DIR"', builder)
        self.assertIn("Do not open the app inside ReleasePayload directly", builder)

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
