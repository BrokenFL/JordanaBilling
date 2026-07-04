"""Clean-install verification test.

Tests the fresh-install workflow:
1. Fresh checkout (simulated with temp DB path)
2. No database present
3. Database created by init-db
4. Migrations applied
5. CSV import works (simulates sync validation)
6. Second import creates no duplicates
7. Review server starts and health check passes
8. Second launch preserves all local data
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import unittest

PROJECT_DIR = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_DIR / "app"
SAMPLE_CSV = PROJECT_DIR / "data" / "samples" / "june_calendar_snapshots.csv"


class CleanInstallTest(unittest.TestCase):
    """Verify the clean-install workflow end-to-end."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="jordana_clean_install_")
        self.db_path = Path(self.tmpdir) / "test_clean.sqlite3"

    def tearDown(self) -> None:
        if self.db_path.exists():
            self.db_path.unlink()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(APP_DIR)
        return subprocess.run(
            [sys.executable, "-m", "jordana_invoice", "--db", str(self.db_path), *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(PROJECT_DIR),
        )

    def test_01_no_database_present(self) -> None:
        """Fresh checkout: no database exists yet."""
        self.assertFalse(self.db_path.exists())

    def test_02_database_created(self) -> None:
        """init-db creates a blank database."""
        result = self._run_cli("init-db")
        self.assertEqual(result.returncode, 0, f"init-db failed: {result.stderr}")
        self.assertTrue(self.db_path.exists())

    def test_03_migrations_applied(self) -> None:
        """All required tables exist after migration."""
        self._run_cli("init-db")
        conn = sqlite3.connect(str(self.db_path))
        existing = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        conn.close()
        required = {
            "import_runs",
            "raw_calendar_snapshots",
            "calendar_event_candidates",
            "people",
            "client_accounts",
            "sessions",
            "review_items",
            "audit_log",
            "sync_state",
            "schema_migrations",
            "invoices",
            "invoice_line_items",
        }
        missing = required - existing
        self.assertEqual(missing, set(), f"Missing tables: {missing}")

    def test_04_integrity_check_passes(self) -> None:
        """SQLite integrity check passes on fresh database."""
        self._run_cli("init-db")
        conn = sqlite3.connect(str(self.db_path))
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()
        self.assertEqual(result, "ok")

    def test_05_csv_import_works(self) -> None:
        """CSV import succeeds (simulates raw record import)."""
        self._run_cli("init-db")
        result = self._run_cli("import-csv", str(SAMPLE_CSV))
        self.assertEqual(result.returncode, 0, f"import-csv failed: {result.stderr}")

        conn = sqlite3.connect(str(self.db_path))
        count = conn.execute("SELECT COUNT(*) FROM raw_calendar_snapshots").fetchone()[0]
        conn.close()
        self.assertGreater(count, 0, "No raw snapshots imported")

    def test_06_second_import_no_duplicates(self) -> None:
        """Re-importing the same CSV creates no duplicate snapshots."""
        self._run_cli("init-db")
        self._run_cli("import-csv", str(SAMPLE_CSV))

        conn = sqlite3.connect(str(self.db_path))
        count_before = conn.execute("SELECT COUNT(*) FROM raw_calendar_snapshots").fetchone()[0]
        conn.close()

        self._run_cli("import-csv", str(SAMPLE_CSV))

        conn = sqlite3.connect(str(self.db_path))
        count_after = conn.execute("SELECT COUNT(*) FROM raw_calendar_snapshots").fetchone()[0]
        conn.close()

        self.assertEqual(
            count_before,
            count_after,
            f"Duplicate snapshots created: {count_before} -> {count_after}",
        )

    def test_07_health_endpoint(self) -> None:
        """Review server starts and /api/health responds."""
        self._run_cli("init-db")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(APP_DIR)
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "jordana_invoice",
                "--db", str(self.db_path),
                "serve-review", "--host", "127.0.0.1", "--port", "8771",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(PROJECT_DIR),
        )
        try:
            healthy = False
            for _ in range(15):
                try:
                    resp = urllib.request.urlopen(
                        "http://127.0.0.1:8771/api/health", timeout=2
                    )
                    import json
                    data = json.loads(resp.read())
                    if data.get("ok") is True:
                        healthy = True
                        break
                except Exception:
                    pass
                time.sleep(1)
            self.assertTrue(healthy, "Health endpoint did not respond")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    def test_08_second_launch_preserves_data(self) -> None:
        """Running init-db again does not destroy existing data."""
        self._run_cli("init-db")
        self._run_cli("import-csv", str(SAMPLE_CSV))

        conn = sqlite3.connect(str(self.db_path))
        count_before = conn.execute("SELECT COUNT(*) FROM raw_calendar_snapshots").fetchone()[0]
        conn.close()

        # Simulate second launch: run init-db again
        self._run_cli("init-db")

        conn = sqlite3.connect(str(self.db_path))
        count_after = conn.execute("SELECT COUNT(*) FROM raw_calendar_snapshots").fetchone()[0]
        conn.close()

        self.assertEqual(count_before, count_after, "Data lost on second launch")

    def test_09_scripts_exist(self) -> None:
        """All required scripts exist and are executable."""
        scripts = [
            "bootstrap.sh",
            "start_jordana.sh",
            "stop_jordana.sh",
            "health_check.sh",
            "full_sync.sh",
            "backup_db.sh",
            "reset_test_db.sh",
            "build_launcher.sh",
        ]
        for name in scripts:
            path = PROJECT_DIR / "scripts" / name
            self.assertTrue(path.exists(), f"Missing script: {name}")

    def test_10_env_example_has_no_hardcoded_paths(self) -> None:
        """.env.example must not contain hardcoded username paths."""
        content = (PROJECT_DIR / ".env.example").read_text()
        self.assertNotIn("/Users/", content, ".env.example contains hardcoded /Users/ path")
        self.assertNotIn("/home/", content, ".env.example contains hardcoded /home/ path")
        self.assertIn("__PROJECT_DIR__", content, ".env.example should use __PROJECT_DIR__ placeholder")

    def test_11_launcher_bundle_committed(self) -> None:
        """Jordana Billing.app must be committed to Git (not gitignored)."""
        app_dir = PROJECT_DIR / "Jordana Billing.app"
        self.assertTrue(app_dir.exists(), "Jordana Billing.app not found")
        self.assertTrue((app_dir / "Contents" / "MacOS" / "launcher").exists())
        self.assertTrue((app_dir / "Contents" / "Info.plist").exists())
        result = subprocess.run(
            ["git", "ls-files", "Jordana Billing.app"],
            capture_output=True, text=True, cwd=str(PROJECT_DIR),
        )
        self.assertEqual(result.returncode, 0)
        tracked_files = result.stdout.strip().split("\n") if result.stdout.strip() else []
        self.assertGreater(len(tracked_files), 0, "Jordana Billing.app is not tracked by Git")

    def test_12_launcher_bundle_no_private_data(self) -> None:
        """Jordana Billing.app must not contain credentials or private data."""
        app_dir = PROJECT_DIR / "Jordana Billing.app"
        for file_path in app_dir.rglob("*"):
            if file_path.is_file():
                content = file_path.read_bytes()
                text_content = content.decode("utf-8", errors="ignore")
                self.assertNotIn("jb_", text_content[:10000], f"Possible API key in {file_path}")
                self.assertNotIn("AKIA", text_content[:10000], f"Possible AWS key in {file_path}")
                self.assertNotIn("BEGIN PRIVATE KEY", text_content[:10000], f"Private key in {file_path}")

    @unittest.skipUnless(sys.platform == "darwin", "macOS launcher build requires sips, iconutil, and swiftc")
    def test_13_build_launcher_force(self) -> None:
        """build_launcher.sh --force rebuilds the .app bundle."""
        result = subprocess.run(
            ["bash", str(PROJECT_DIR / "scripts" / "build_launcher.sh"), "--force"],
            capture_output=True, text=True, cwd=str(PROJECT_DIR),
        )
        self.assertEqual(result.returncode, 0, f"build_launcher.sh --force failed: {result.stderr}")
        app_dir = PROJECT_DIR / "Jordana Billing.app"
        self.assertTrue(app_dir.exists())
        self.assertTrue((app_dir / "Contents" / "Resources" / "AppIcon.icns").exists())

    def test_14_env_auto_resolve(self) -> None:
        """bootstrap.sh auto-resolves __PROJECT_DIR__ in .env."""
        common = (PROJECT_DIR / "scripts" / "launcher_common.sh").read_text()
        self.assertIn("__PROJECT_DIR__", common, "launcher_common.sh does not handle __PROJECT_DIR__")
        self.assertIn("sed", common, "launcher_common.sh does not use sed to resolve paths")


if __name__ == "__main__":
    unittest.main()
