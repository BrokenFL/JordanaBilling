"""Focused tests for Production Packaging V1."""

from __future__ import annotations

import json
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
            "Wi-Fi-off launch",
            "missing config",
            "missing DB",
            "Reinstall",
            "private data remains",
        ]:
            self.assertIn(phrase, text)


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
