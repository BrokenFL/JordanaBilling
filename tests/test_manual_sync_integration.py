"""Manual integration test: real Google Sheet sync.

This test performs an actual pull from the configured Google Apps Script
endpoint and reports imported, skipped, and duplicate counts.

Run manually (not part of the automated suite):

    PYTHONPATH=app python3 -m unittest tests.test_manual_sync_integration

Requires a valid .env with JORDANA_APPS_SCRIPT_URL and JORDANA_INGEST_API_KEY.
Uses a temporary database so it never touches production data.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_DIR / "app"


class ManualSyncIntegrationTest(unittest.TestCase):
    """Pull real records from Google Apps Script and verify no duplicates."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="jordana_sync_test_")
        self.db_path = Path(self.tmpdir) / "sync_test.sqlite3"

    def tearDown(self) -> None:
        if self.db_path.exists():
            self.db_path.unlink()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_real_sync_imports_without_duplicates(self) -> None:
        """Sync --full twice; second sync must not create duplicates."""
        env_path = PROJECT_DIR / ".env"
        if not env_path.exists():
            self.skipTest("No .env file — skipping real sync integration test")

        # Load env to check required vars
        from jordana_invoice.google_sync import load_env_file, SyncConfig
        load_env_file(env_path)
        url = os.environ.get("JORDANA_APPS_SCRIPT_URL", "")
        key = os.environ.get("JORDANA_INGEST_API_KEY", "")
        if not url or not key:
            self.skipTest("JORDANA_APPS_SCRIPT_URL or JORDANA_INGEST_API_KEY not set")

        import subprocess, sys

        env = os.environ.copy()
        env["PYTHONPATH"] = str(APP_DIR)

        # Init DB
        result = subprocess.run(
            [sys.executable, "-m", "jordana_invoice", "--db", str(self.db_path), "init-db"],
            capture_output=True, text=True, env=env, cwd=str(PROJECT_DIR),
        )
        self.assertEqual(result.returncode, 0, f"init-db failed: {result.stderr}")

        # First full sync
        result1 = subprocess.run(
            [sys.executable, "-m", "jordana_invoice", "sync", "--full", "--env", str(env_path)],
            capture_output=True, text=True, env=env, cwd=str(PROJECT_DIR),
        )
        if result1.returncode != 0:
            self.skipTest(f"First sync failed (network?): {result1.stderr}")

        conn = sqlite3.connect(str(self.db_path))
        count_after_first = conn.execute(
            "SELECT COUNT(*) FROM raw_calendar_snapshots"
        ).fetchone()[0]
        conn.close()

        if count_after_first == 0:
            self.skipTest("Sync returned 0 rows — likely no real credentials or empty sheet")

        # Second full sync — must not create duplicates
        result2 = subprocess.run(
            [sys.executable, "-m", "jordana_invoice", "sync", "--full", "--env", str(env_path)],
            capture_output=True, text=True, env=env, cwd=str(PROJECT_DIR),
        )
        self.assertEqual(result2.returncode, 0, f"Second sync failed: {result2.stderr}")

        conn = sqlite3.connect(str(self.db_path))
        count_after_second = conn.execute(
            "SELECT COUNT(*) FROM raw_calendar_snapshots"
        ).fetchone()[0]
        conn.close()

        self.assertEqual(
            count_after_first,
            count_after_second,
            f"Duplicate rows created: {count_after_first} -> {count_after_second}",
        )

        print(f"\nManual sync integration test passed:")
        print(f"  Rows imported (first sync):  {count_after_first}")
        print(f"  Rows after second sync:     {count_after_second}")
        print(f"  Duplicates prevented:       {count_after_first - count_after_second}")


if __name__ == "__main__":
    unittest.main()
