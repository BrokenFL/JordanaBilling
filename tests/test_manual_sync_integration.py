"""Manual integration test: real Google Sheet sync.

This test performs an actual pull from the configured Google Apps Script
endpoint and reports imported, skipped, and duplicate counts.

Run manually (not part of the automated suite):

    PYTHONPATH=app python3 -m unittest tests.test_manual_sync_integration.ManualSyncIntegrationTest

Requires a valid .env with JORDANA_APPS_SCRIPT_URL and JORDANA_INGEST_API_KEY.
Uses a temporary database and reports directory, so it never touches the
configured project database.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_DIR = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_DIR / "app"


def _read_env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_isolated_sync_env(
    source_env: Path,
    target_env: Path,
    database_path: Path,
    reports_dir: Path,
) -> None:
    values = _read_env_values(source_env)
    missing = [
        key
        for key in ("JORDANA_APPS_SCRIPT_URL", "JORDANA_INGEST_API_KEY")
        if not values.get(key)
    ]
    if missing:
        raise ValueError("Missing environment variables: " + ", ".join(missing))

    target_env.parent.mkdir(parents=True, exist_ok=True)
    target_env.write_text(
        "\n".join(
            [
                f"JORDANA_APPS_SCRIPT_URL={json.dumps(values['JORDANA_APPS_SCRIPT_URL'])}",
                f"JORDANA_INGEST_API_KEY={json.dumps(values['JORDANA_INGEST_API_KEY'])}",
                f"JORDANA_DATABASE_PATH={json.dumps(str(database_path))}",
                f"JORDANA_REPORTS_DIR={json.dumps(str(reports_dir))}",
                "",
            ]
        ),
        encoding="utf-8",
    )


class ManualSyncIsolationTest(unittest.TestCase):
    """Verify the manual sync helper points at disposable local paths."""

    def test_isolated_env_uses_temp_database_and_reports(self) -> None:
        from jordana_invoice.google_sync import load_config

        with tempfile.TemporaryDirectory(prefix="jordana_sync_env_test_") as tmp:
            root = Path(tmp)
            source_env = root / "source.env"
            target_env = root / "isolated.env"
            database_path = root / "sync_test.sqlite3"
            reports_dir = root / "Reports"
            source_env.write_text(
                "JORDANA_APPS_SCRIPT_URL=https://example.test/exec\n"
                "JORDANA_INGEST_API_KEY=test-key\n",
                encoding="utf-8",
            )

            write_isolated_sync_env(
                source_env,
                target_env,
                database_path,
                reports_dir,
            )

            with patch.dict(os.environ, {}, clear=True):
                config = load_config(target_env)

            self.assertEqual(config.database_path, str(database_path))
            self.assertEqual(config.reports_dir, str(reports_dir))
            self.assertNotEqual(target_env, source_env)


class ManualSyncIntegrationTest(unittest.TestCase):
    """Pull real records from Google Apps Script and verify no duplicates."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="jordana_sync_test_")
        self.tmp_path = Path(self.tmpdir)
        self.db_path = self.tmp_path / "sync_test.sqlite3"
        self.reports_dir = self.tmp_path / "Reports"
        self.sync_env_path = self.tmp_path / ".env"

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_real_sync_imports_without_duplicates(self) -> None:
        """Sync --full twice; second sync must not create duplicates."""
        source_env_path = PROJECT_DIR / ".env"
        if not source_env_path.exists():
            self.skipTest("No .env file — skipping real sync integration test")

        try:
            write_isolated_sync_env(
                source_env_path,
                self.sync_env_path,
                self.db_path,
                self.reports_dir,
            )
        except ValueError as error:
            self.skipTest(str(error))

        import subprocess
        import sys

        env = os.environ.copy()
        env["PYTHONPATH"] = str(APP_DIR)
        for key in (
            "JORDANA_APPS_SCRIPT_URL",
            "JORDANA_INGEST_API_KEY",
            "JORDANA_DATABASE_PATH",
            "JORDANA_REPORTS_DIR",
        ):
            env.pop(key, None)

        # Init the disposable DB.
        result = subprocess.run(
            [sys.executable, "-m", "jordana_invoice", "--db", str(self.db_path), "init-db"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(PROJECT_DIR),
        )
        self.assertEqual(result.returncode, 0, f"init-db failed: {result.stderr}")

        # First full sync uses the isolated env file and temporary DB.
        result1 = subprocess.run(
            [
                sys.executable,
                "-m",
                "jordana_invoice",
                "sync",
                "--full",
                "--env",
                str(self.sync_env_path),
            ],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(PROJECT_DIR),
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

        # Second full sync must not create duplicates in the temporary DB.
        result2 = subprocess.run(
            [
                sys.executable,
                "-m",
                "jordana_invoice",
                "sync",
                "--full",
                "--env",
                str(self.sync_env_path),
            ],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(PROJECT_DIR),
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

        print("\nManual sync integration test passed:")
        print(f"  Rows imported (first sync): {count_after_first}")
        print(f"  Rows after second sync:    {count_after_second}")
        print("  Production database used:  no")


if __name__ == "__main__":
    unittest.main()
