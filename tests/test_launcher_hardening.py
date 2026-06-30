"""Focused tests for Mac launcher and installer hardening."""

from __future__ import annotations

import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
APP_BUNDLE = PROJECT_DIR / "Jordana Billing.app"
PACKAGING_DIR = PROJECT_DIR / "packaging" / "macos"


class TestAppBundleStructure(unittest.TestCase):
    """Verify the committed .app bundle has a valid structure."""

    def test_bundle_directory_exists(self) -> None:
        self.assertTrue(APP_BUNDLE.is_dir(), "Jordana Billing.app directory missing")

    def test_contents_directory_exists(self) -> None:
        contents = APP_BUNDLE / "Contents"
        self.assertTrue(contents.is_dir(), "Contents/ directory missing")

    def test_macos_directory_exists(self) -> None:
        macos = APP_BUNDLE / "Contents" / "MacOS"
        self.assertTrue(macos.is_dir(), "Contents/MacOS/ directory missing")

    def test_resources_directory_exists(self) -> None:
        resources = APP_BUNDLE / "Contents" / "Resources"
        self.assertTrue(resources.is_dir(), "Contents/Resources/ directory missing")

    def test_info_plist_exists(self) -> None:
        plist_path = APP_BUNDLE / "Contents" / "Info.plist"
        self.assertTrue(plist_path.is_file(), "Info.plist missing")

    def test_launcher_executable_exists(self) -> None:
        launcher = APP_BUNDLE / "Contents" / "MacOS" / "launcher"
        self.assertTrue(launcher.is_file(), "launcher executable missing")

    def test_icon_exists(self) -> None:
        icon = APP_BUNDLE / "Contents" / "Resources" / "AppIcon.icns"
        self.assertTrue(icon.is_file(), "AppIcon.icns missing")


class TestLauncherExecutablePermission(unittest.TestCase):
    """Verify the launcher is executable."""

    def test_launcher_is_executable(self) -> None:
        launcher = APP_BUNDLE / "Contents" / "MacOS" / "launcher"
        mode = launcher.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR, "launcher not user-executable")
        self.assertTrue(mode & stat.S_IXGRP, "launcher not group-executable")
        self.assertTrue(mode & stat.S_IXOTH, "launcher not other-executable")

    @unittest.skipUnless(sys.platform == "darwin", "file is only available on macOS")
    def test_launcher_is_native_arm64_executable(self) -> None:
        launcher = APP_BUNDLE / "Contents" / "MacOS" / "launcher"
        result = subprocess.run(
            ["file", str(launcher)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Mach-O", result.stdout)
        self.assertIn("arm64", result.stdout)
        self.assertNotIn("x86_64 executable", result.stdout)


class TestInfoPlist(unittest.TestCase):
    """Verify Info.plist is valid and has required keys."""

    def setUp(self) -> None:
        self.plist_path = APP_BUNDLE / "Contents" / "Info.plist"
        with open(self.plist_path, "rb") as f:
            self.plist = plistlib.load(f)

    def test_plist_is_parseable(self) -> None:
        self.assertIsInstance(self.plist, dict)

    def test_cf_bundle_executable(self) -> None:
        self.assertEqual(self.plist.get("CFBundleExecutable"), "launcher")

    def test_cf_bundle_identifier(self) -> None:
        self.assertEqual(self.plist.get("CFBundleIdentifier"), "com.jordana.billing.launcher")

    def test_cf_bundle_name(self) -> None:
        self.assertEqual(self.plist.get("CFBundleName"), "Jordana Billing")

    def test_cf_bundle_package_type(self) -> None:
        self.assertEqual(self.plist.get("CFBundlePackageType"), "APPL")

    def test_minimum_system_version(self) -> None:
        self.assertIn("LSMinimumSystemVersion", self.plist)

    def test_documents_folder_usage_description(self) -> None:
        self.assertIn("NSDocumentsFolderUsageDescription", self.plist)

    def test_icon_file_reference(self) -> None:
        self.assertEqual(self.plist.get("CFBundleIconFile"), "AppIcon")

    @unittest.skipUnless(sys.platform == "darwin", "plutil is only available on macOS")
    def test_plutil_lint(self) -> None:
        result = subprocess.run(
            ["plutil", "-lint", str(self.plist_path)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, f"plutil lint failed: {result.stderr}")


class TestPythonSelection(unittest.TestCase):
    """Test the find_python logic with mocked paths and versions."""

    def _make_find_python_script(self, candidates: list[str]) -> str:
        """Extract the find_python function from bootstrap.sh for testing."""
        common = (PROJECT_DIR / "scripts" / "launcher_common.sh").read_text()
        start = common.index("find_python() {")
        end = common.index("\n}", common.index("return 1", start)) + 2
        func_body = common[start:end]
        return f"""#!/usr/bin/env bash
{func_body}
find_python
"""

    def test_rejects_python_below_311(self) -> None:
        """A Python 3.10 candidate is rejected; 3.12 is accepted."""
        tmpdir = tempfile.mkdtemp(prefix="test_python_")
        try:
            fake_python_310 = Path(tmpdir) / "python3_310"
            fake_python_312 = Path(tmpdir) / "python3_312"
            fake_python_310.write_text("#!/usr/bin/env bash\nexit 1\n")
            fake_python_312.write_text("#!/usr/bin/env bash\nexit 0\n")
            fake_python_310.chmod(0o755)
            fake_python_312.chmod(0o755)

            script = f"""#!/usr/bin/env bash
find_python() {{
  local candidates=(
    "{fake_python_310}"
    "{fake_python_312}"
  )
  for candidate in "${{candidates[@]}}"; do
    if [[ -x "$candidate" ]]; then
      if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        echo "$candidate"
        return 0
      fi
    fi
  done
  return 1
}}
find_python
"""
            result = subprocess.run(
                ["bash", "-c", script],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), str(fake_python_312))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_returns_nonzero_when_no_python_found(self) -> None:
        """find_python returns 1 when no candidate is executable."""
        script = """#!/usr/bin/env bash
find_python() {
  local candidates=(
    "/nonexistent/path/python3"
    "/another/nonexistent/path/python3"
  )
  for candidate in "${candidates[@]}"; do
    if [[ -x "$candidate" ]]; then
      if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        echo "$candidate"
        return 0
      fi
    fi
  done
  return 1
}
find_python
"""
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.strip(), "")

    def test_bootstrap_has_find_python(self) -> None:
        """Shared launcher logic must contain the find_python function."""
        common = (PROJECT_DIR / "scripts" / "launcher_common.sh").read_text()
        self.assertIn("find_python()", common)
        self.assertIn("/opt/homebrew/bin/python3", common)
        self.assertIn("/usr/local/bin/python3", common)
        self.assertIn("/usr/bin/python3", common)

    def test_bootstrap_uses_python_bin_for_venv(self) -> None:
        """bootstrap flow must use the discovered Python for venv creation."""
        common = (PROJECT_DIR / "scripts" / "launcher_common.sh").read_text()
        self.assertIn('"$python_bin" -m venv', common)


class TestDatabaseDetection(unittest.TestCase):
    """Test existing database detection in launcher and bootstrap."""

    def test_bootstrap_does_not_create_blank_database_silently(self) -> None:
        bootstrap = (PROJECT_DIR / "scripts" / "bootstrap.sh").read_text()
        common = (PROJECT_DIR / "scripts" / "launcher_common.sh").read_text()
        self.assertNotIn("No existing database found — creating new database", bootstrap)
        self.assertNotIn("init-db\" \"$DB_PATH\"", common)
        self.assertIn("Configured Database Not Found", common)

    def test_database_validator_requires_existing_database(self) -> None:
        validator = (PROJECT_DIR / "scripts" / "validate_launcher_environment.py").read_text()
        self.assertIn("MISSING_DATABASE", validator)
        self.assertIn("mode=ro", validator)
        self.assertIn("PRAGMA integrity_check", validator)
        self.assertIn("os.environ.pop(key, None)", validator)


class TestInstallerAuthority(unittest.TestCase):
    """Verify production install and daily launch are separate."""

    def test_setup_jordana_mac_is_retired_stub(self) -> None:
        setup_script = (PROJECT_DIR / "scripts" / "setup_jordana_mac.sh").read_text()
        self.assertIn("has been retired", setup_script)
        self.assertIn("scripts/bootstrap.sh", setup_script)
        self.assertNotIn("python3 -m venv", setup_script)
        self.assertNotIn("cp data/jordana_invoice.sqlite3", setup_script)
        self.assertNotIn("init-db", setup_script)

    def test_readme_no_longer_lists_setup_as_setup_utility(self) -> None:
        readme = (PROJECT_DIR / "README.md").read_text()
        self.assertNotIn("scripts/setup_jordana_mac.sh`, `scripts/verify_install.sh`", readme)

    def test_handoff_documents_production_installer(self) -> None:
        handoff = (PROJECT_DIR / "docs" / "HANDOFF_TO_JORDANA_MAC.md").read_text()
        self.assertIn("scripts/install_release.sh", handoff)
        self.assertIn("Application Support/Jordana Billing", handoff)


class TestPortOwnershipSafety(unittest.TestCase):
    """Verify launcher scripts do not kill unrelated processes."""

    def test_no_broad_process_kill_patterns_in_launcher_scripts(self) -> None:
        checked = [
            PROJECT_DIR / "scripts" / "bootstrap.sh",
            PROJECT_DIR / "scripts" / "start_jordana.sh",
            PROJECT_DIR / "scripts" / "stop_jordana.sh",
            PROJECT_DIR / "scripts" / "launcher_common.sh",
            PROJECT_DIR / "scripts" / "launch_installed_app.sh",
        ]
        forbidden = [
            "pkill -f",
            "killall python",
            "killall Python",
            "lsof -ti \":${PORT}\"",
            "xargs kill",
            "kill $PID_ON_PORT",
        ]
        for path in checked:
            content = path.read_text()
            for pattern in forbidden:
                self.assertNotIn(pattern, content, f"{pattern!r} found in {path}")

    def test_pid_ownership_uses_metadata_and_command_verification(self) -> None:
        common = (PROJECT_DIR / "scripts" / "launcher_common.sh").read_text()
        self.assertIn("pid_metadata_matches", common)
        self.assertIn("pid_looks_like_jordana", common)
        self.assertIn("project_dir=$PROJECT_DIR", common)
        self.assertIn("serve-review", common)

    def test_unrelated_port_owner_fails_without_kill(self) -> None:
        common = (PROJECT_DIR / "scripts" / "launcher_common.sh").read_text()
        self.assertIn("Port 8765 Is In Use", common)
        self.assertIn("did not stop or reuse that process", common)
        self.assertNotIn("kill $port_pid", common)


class TestIconBuild(unittest.TestCase):
    """Verify icon source, generated outputs, and app integration."""

    def test_approved_icon_source_present(self) -> None:
        source = PACKAGING_DIR / "AppIcon-source.png"
        self.assertTrue(source.is_file())
        self.assertGreater(source.stat().st_size, 100_000)

    def test_icon_builder_creates_standard_sizes(self) -> None:
        script = (PROJECT_DIR / "scripts" / "build_app_icon.sh").read_text()
        for name in [
            "icon_16x16.png",
            "icon_16x16@2x.png",
            "icon_32x32.png",
            "icon_32x32@2x.png",
            "icon_128x128.png",
            "icon_128x128@2x.png",
            "icon_256x256.png",
            "icon_256x256@2x.png",
            "icon_512x512.png",
            "icon_512x512@2x.png",
        ]:
            self.assertIn(name, script)
        self.assertIn("iconutil -c icns", script)

    def test_generated_icns_exists(self) -> None:
        self.assertTrue((PACKAGING_DIR / "AppIcon.icns").is_file())

    def test_bundle_contains_generated_icns(self) -> None:
        self.assertTrue((APP_BUNDLE / "Contents" / "Resources" / "AppIcon.icns").is_file())

    def test_bundle_contains_installed_launcher_resource(self) -> None:
        self.assertTrue((APP_BUNDLE / "Contents" / "Resources" / "launch_installed_app.sh").is_file())

    def test_bootstrap_preserves_existing_database(self) -> None:
        """Launcher validation preserves existing database by opening read-only."""
        validator = (PROJECT_DIR / "scripts" / "validate_launcher_environment.py").read_text()
        self.assertIn("mode=ro", validator)
        self.assertNotIn("sqlite3.connect(str(db_path))", validator)

    def test_bootstrap_creates_new_database_when_missing(self) -> None:
        """bootstrap.sh does not create a replacement database when missing."""
        common = (PROJECT_DIR / "scripts" / "launcher_common.sh").read_text()
        self.assertIn("Configured Database Not Found", common)
        self.assertNotIn("creating new database", common)

    def test_launcher_logs_to_file(self) -> None:
        """Native launcher delegates to the installed shell helper."""
        source = (PACKAGING_DIR / "NativeLauncher.swift").read_text()
        self.assertIn("launch_installed_app", source)
        self.assertIn("Jordana Billing Has Not Been Installed Yet", source)

    def test_existing_database_not_deleted(self) -> None:
        """Neither launcher nor bootstrap deletes an existing database."""
        launcher = (PACKAGING_DIR / "NativeLauncher.swift").read_text()
        bootstrap = (PROJECT_DIR / "scripts" / "bootstrap.sh").read_text()
        for dangerous in ['rm -f "$DB_PATH"', 'rm "$DB_PATH"', "rm -f $DB_PATH", "unlink"]:
            self.assertNotIn(dangerous, launcher, f"launcher contains: {dangerous}")
            self.assertNotIn(dangerous, bootstrap, f"bootstrap contains: {dangerous}")


class TestBashSyntaxCheck(unittest.TestCase):
    """Run bash -n on all modified shell scripts."""

    SCRIPTS = [
        "scripts/bootstrap.sh",
        "scripts/start_jordana.sh",
        "scripts/build_launcher.sh",
        "scripts/build_app_icon.sh",
        "scripts/build_release.sh",
        "scripts/build_setup_wizard.sh",
        "scripts/install_release.sh",
        "scripts/launch_installed_app.sh",
        "scripts/launcher_common.sh",
        "scripts/setup_jordana_mac.sh",
        "scripts/stop_jordana.sh",
        "scripts/update_release.sh",
        "scripts/verify_installation.sh",
    ]

    def test_installed_launch_helper_syntax(self) -> None:
        launcher = APP_BUNDLE / "Contents" / "Resources" / "launch_installed_app.sh"
        result = subprocess.run(
            ["bash", "-n", str(launcher)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, f"launcher syntax error: {result.stderr}")

    def test_all_scripts_syntax(self) -> None:
        for script in self.SCRIPTS:
            path = PROJECT_DIR / script
            self.assertTrue(path.exists(), f"Missing script: {script}")
            result = subprocess.run(
                ["bash", "-n", str(path)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                result.returncode, 0,
                f"{script} syntax error: {result.stderr}",
            )


class TestBuildLauncherSigns(unittest.TestCase):
    """Verify build_launcher.sh includes ad-hoc signing."""

    def test_build_launcher_has_codesign(self) -> None:
        build_script = (PROJECT_DIR / "scripts" / "build_launcher.sh").read_text()
        self.assertIn("codesign", build_script)
        self.assertIn("--sign -", build_script)

    def test_build_launcher_strips_xattr(self) -> None:
        build_script = (PROJECT_DIR / "scripts" / "build_launcher.sh").read_text()
        self.assertIn("xattr -cr", build_script)

    def test_build_launcher_delegates_to_installed_launcher(self) -> None:
        """build_launcher.sh template delegates daily launch to the installed launcher."""
        build_script = (PROJECT_DIR / "scripts" / "build_launcher.sh").read_text()
        self.assertIn("NativeLauncher.swift", build_script)
        self.assertIn("launch_installed_app.sh", build_script)
        self.assertNotIn('exec "$PROJECT_DIR/scripts/bootstrap.sh"', build_script)
        self.assertNotIn("DB_EXISTS", build_script)

    def test_build_launcher_does_not_use_terminal(self) -> None:
        """build_launcher.sh template does not require Terminal."""
        build_script = (PROJECT_DIR / "scripts" / "build_launcher.sh").read_text()
        self.assertNotIn("tell application", build_script)
        self.assertNotIn("Terminal", build_script)
        self.assertNotIn("do script", build_script)

    def test_launcher_does_not_use_terminal(self) -> None:
        """Committed launcher does not require Terminal."""
        launcher = (PACKAGING_DIR / "NativeLauncher.swift").read_text()
        self.assertNotIn("tell application", launcher)
        self.assertNotIn("Terminal", launcher)
        self.assertNotIn("do script", launcher)

    @unittest.skipUnless(sys.platform == "darwin", "codesign is only available on macOS")
    def test_app_is_adhoc_signed(self) -> None:
        """Committed .app bundle should be ad-hoc signed."""
        result = subprocess.run(
            ["codesign", "-dvv", str(APP_BUNDLE)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, "App is not code-signed")
        self.assertIn("Signature=adhoc", result.stderr)


if __name__ == "__main__":
    unittest.main()
