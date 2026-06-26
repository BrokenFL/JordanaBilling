"""
Tests for the operational-database safety helper ``is_operational_db_path``
and the ``import-csv`` CLI guard.

All tests use tempfile paths; none touch the real operational database.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import is_operational_db_path

PROJECT_DIR = Path(__file__).resolve().parents[1]
APP_DIR = PROJECT_DIR / "app"
SAMPLE_CSV = PROJECT_DIR / "data" / "samples" / "june_calendar_snapshots.csv"


def _run_cli(*args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    e = (env or os.environ).copy()
    e["PYTHONPATH"] = str(APP_DIR)
    return subprocess.run(
        [sys.executable, "-m", "jordana_invoice", *args],
        capture_output=True,
        text=True,
        env=e,
        cwd=str(PROJECT_DIR),
    )


class IsOperationalDbPathTests(unittest.TestCase):
    """Unit tests for ``is_operational_db_path``."""

    # --- should be True ---

    def test_relative_default_path_is_operational(self):
        """The default relative path used by the CLI is flagged as operational."""
        self.assertTrue(is_operational_db_path("data/jordana_invoice.sqlite3"))

    def test_dotslash_relative_path_is_operational(self):
        """Leading ./ does not fool the checker."""
        self.assertTrue(is_operational_db_path("./data/jordana_invoice.sqlite3"))

    def test_absolute_path_in_project_data_is_operational(self):
        """An absolute path under the project data/ directory is flagged."""
        abs_path = str(PROJECT_DIR / "data" / "jordana_invoice.sqlite3")
        self.assertTrue(is_operational_db_path(abs_path))

    # --- should be False: temp-directory paths ---

    def test_temp_dir_path_with_matching_filename_is_not_operational(self):
        """A temp-directory path sharing the operational filename must return False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "jordana_invoice.sqlite3"
            self.assertFalse(is_operational_db_path(p))

    def test_mkdtemp_path_with_matching_filename_is_not_operational(self):
        """``tempfile.mkdtemp`` paths contain 'tmp' and are always excluded."""
        tmpdir = tempfile.mkdtemp(prefix="jordana_test_")
        try:
            p = Path(tmpdir) / "jordana_invoice.sqlite3"
            self.assertFalse(is_operational_db_path(p))
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    # --- should be False: different filenames ---

    def test_different_db_filename_is_not_operational(self):
        """A different database filename is never considered operational."""
        self.assertFalse(is_operational_db_path("data/test_billing.sqlite3"))

    def test_acceptance_temp_db_filename_is_not_operational(self):
        """The acceptance_test.sqlite3 name used by run_acceptance_test.sh is safe."""
        self.assertFalse(is_operational_db_path("/tmp/xyz/acceptance_test.sqlite3"))

    def test_demo_db_is_not_operational(self):
        """The demo database path is not operational."""
        self.assertFalse(is_operational_db_path("data/demo/jordana_demo.sqlite3"))

    # --- edge cases ---

    def test_empty_string_is_not_operational(self):
        self.assertFalse(is_operational_db_path(""))

    def test_path_object_works_as_well_as_string(self):
        """Accepts a Path object in addition to a string."""
        self.assertTrue(is_operational_db_path(Path("data") / "jordana_invoice.sqlite3"))


class CliImportCsvGuardTests(unittest.TestCase):
    """Tests for the CLI import-csv safety guard."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.temp_db = str(self.root / "acceptance_test.sqlite3")
        # Point backups to the temp dir so no real backup dirs are created.
        self.old_backup_dir = os.environ.get("JORDANA_BACKUP_DIR")
        os.environ["JORDANA_BACKUP_DIR"] = str(self.root)

    def tearDown(self):
        if self.old_backup_dir is not None:
            os.environ["JORDANA_BACKUP_DIR"] = self.old_backup_dir
        else:
            os.environ.pop("JORDANA_BACKUP_DIR", None)
        self.temp.cleanup()

    def test_import_csv_against_operational_db_is_refused(self):
        """import-csv without --allow-operational-db refuses the operational path."""
        result = _run_cli(
            "--db", "data/jordana_invoice.sqlite3",
            "import-csv", str(SAMPLE_CSV),
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("REFUSED", result.stderr)
        self.assertIn("operational", result.stderr.lower())

    def test_refused_message_mentions_safe_alternative(self):
        """The refusal message names the safe acceptance test script."""
        result = _run_cli(
            "--db", "data/jordana_invoice.sqlite3",
            "import-csv", str(SAMPLE_CSV),
        )
        self.assertIn("run_acceptance_test.sh", result.stderr)

    def test_refused_message_mentions_bypass_flag(self):
        """The refusal message tells the user about --allow-operational-db."""
        result = _run_cli(
            "--db", "data/jordana_invoice.sqlite3",
            "import-csv", str(SAMPLE_CSV),
        )
        self.assertIn("--allow-operational-db", result.stderr)

    def test_import_csv_against_temp_db_succeeds_without_flag(self):
        """import-csv against a temp-directory path is allowed without the bypass flag."""
        result = _run_cli(
            "--db", self.temp_db,
            "import-csv", str(SAMPLE_CSV),
        )
        # returncode should be 0 and no REFUSED output
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("REFUSED", result.stderr)

    def test_import_csv_with_allow_flag_bypasses_operational_guard(self):
        """
        import-csv --allow-operational-db bypasses the guard.

        We point it at the temp DB (which isn't actually operational) to avoid
        modifying the live database during tests; the key assertion is that the
        guard code path does not raise a refusal.
        """
        # The temp path is not operational so the guard never fires even without
        # the flag; this test just ensures the flag is accepted and doesn't
        # cause an argparse error.
        result = _run_cli(
            "--db", self.temp_db,
            "import-csv", str(SAMPLE_CSV),
            "--allow-operational-db",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("REFUSED", result.stderr)

    def test_import_csv_refusal_does_not_modify_operational_db(self):
        """
        When the guard fires, the operational database is not opened or modified.

        We check the modification time of the real operational DB (if it exists)
        before and after the refused invocation to confirm it is untouched.
        """
        op_db = PROJECT_DIR / "data" / "jordana_invoice.sqlite3"
        mtime_before = op_db.stat().st_mtime if op_db.exists() else None

        result = _run_cli(
            "--db", "data/jordana_invoice.sqlite3",
            "import-csv", str(SAMPLE_CSV),
        )
        self.assertEqual(result.returncode, 1)

        if op_db.exists() and mtime_before is not None:
            self.assertEqual(
                op_db.stat().st_mtime,
                mtime_before,
                "Operational database was modified despite safety refusal.",
            )


if __name__ == "__main__":
    unittest.main()
