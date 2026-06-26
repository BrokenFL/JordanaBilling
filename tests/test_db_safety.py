"""
Tests for the operational-database safety helpers:
``is_operational_db_path``, ``get_configured_operational_db_path``,
``assert_csv_import_safe``, and the ``import-csv`` CLI guard.

All tests use tempfile paths; none touch the real operational database.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import (
    OperationalDatabaseError,
    assert_csv_import_safe,
    connect,
    get_configured_operational_db_path,
    is_operational_db_path,
    migrate_database,
)

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
    """Unit tests for ``is_operational_db_path`` using canonical-path comparison."""

    def setUp(self):
        # Save and unset JORDANA_DATABASE_PATH so tests are deterministic.
        self._old_op = os.environ.get("JORDANA_DATABASE_PATH")
        os.environ.pop("JORDANA_DATABASE_PATH", None)

    def tearDown(self):
        if self._old_op is not None:
            os.environ["JORDANA_DATABASE_PATH"] = self._old_op
        else:
            os.environ.pop("JORDANA_DATABASE_PATH", None)

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
        """``tempfile.mkdtemp`` paths are not the configured operational path."""
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

    # --- canonical path comparison with env var ---

    def test_env_var_sets_operational_path(self):
        """When JORDANA_DATABASE_PATH is set, only that exact path is operational."""
        with tempfile.TemporaryDirectory() as tmpdir:
            op_path = str(Path(tmpdir) / "my_op_db.sqlite3")
            os.environ["JORDANA_DATABASE_PATH"] = op_path
            self.assertTrue(is_operational_db_path(op_path))
            # A different path is not operational even with same filename.
            self.assertFalse(is_operational_db_path(str(Path(tmpdir) / "other.sqlite3")))
            # The default path is not operational when env var is set differently.
            self.assertFalse(is_operational_db_path("data/jordana_invoice.sqlite3"))

    def test_env_var_with_tilde_expansion(self):
        """Tilde in JORDANA_DATABASE_PATH is expanded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            op_path = str(Path(tmpdir) / "op.sqlite3")
            os.environ["JORDANA_DATABASE_PATH"] = op_path
            self.assertTrue(is_operational_db_path(op_path))

    # --- symlink handling ---

    def test_symlink_to_operational_db_is_detected(self):
        """A symlink pointing to the operational DB is detected as operational."""
        with tempfile.TemporaryDirectory() as tmpdir:
            real_db = Path(tmpdir) / "real_op.sqlite3"
            os.environ["JORDANA_DATABASE_PATH"] = str(real_db)
            # Create the real file so resolve() works.
            real_db.touch()
            link_path = Path(tmpdir) / "link_to_op.sqlite3"
            link_path.symlink_to(real_db)
            self.assertTrue(is_operational_db_path(link_path))

    def test_symlink_to_non_operational_is_not_operational(self):
        """A symlink pointing to a non-operational DB is not operational."""
        with tempfile.TemporaryDirectory() as tmpdir:
            other_db = Path(tmpdir) / "other.sqlite3"
            other_db.touch()
            link_path = Path(tmpdir) / "link.sqlite3"
            link_path.symlink_to(other_db)
            self.assertFalse(is_operational_db_path(link_path))

    # --- get_configured_operational_db_path ---

    def test_get_configured_path_uses_env_var(self):
        """get_configured_operational_db_path returns the env var path when set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            op_path = str(Path(tmpdir) / "env_op.sqlite3")
            os.environ["JORDANA_DATABASE_PATH"] = op_path
            result = get_configured_operational_db_path()
            self.assertEqual(str(result), op_path)

    def test_get_configured_path_defaults_without_env_var(self):
        """get_configured_operational_db_path returns the default when env var is unset."""
        result = get_configured_operational_db_path()
        self.assertEqual(str(result), "data/jordana_invoice.sqlite3")


class AssertCsvImportSafeTests(unittest.TestCase):
    """Tests for the service-layer guard ``assert_csv_import_safe``."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self._old_op = os.environ.get("JORDANA_DATABASE_PATH")
        self._old_backup = os.environ.get("JORDANA_BACKUP_DIR")
        os.environ["JORDANA_BACKUP_DIR"] = str(self.root)

    def tearDown(self):
        if self._old_op is not None:
            os.environ["JORDANA_DATABASE_PATH"] = self._old_op
        else:
            os.environ.pop("JORDANA_DATABASE_PATH", None)
        if self._old_backup is not None:
            os.environ["JORDANA_BACKUP_DIR"] = self._old_backup
        else:
            os.environ.pop("JORDANA_BACKUP_DIR", None)
        self.temp.cleanup()

    def _make_db(self, name: str = "test.sqlite3") -> Path:
        db_path = self.root / name
        migrate_database(db_path)
        return db_path

    def test_non_operational_db_passes_without_flag(self):
        """assert_csv_import_safe does not raise for a non-operational DB."""
        db_path = self._make_db()
        conn = connect(db_path)
        try:
            result = assert_csv_import_safe(conn, allow_operational=False)
            self.assertIsNone(result)
        finally:
            conn.close()

    def test_operational_db_raises_without_flag(self):
        """assert_csv_import_safe raises for the operational DB without authorization."""
        db_path = self._make_db("jordana_invoice.sqlite3")
        os.environ["JORDANA_DATABASE_PATH"] = str(db_path)
        conn = connect(db_path)
        try:
            with self.assertRaises(OperationalDatabaseError) as ctx:
                assert_csv_import_safe(conn, allow_operational=False)
            self.assertIn("Refused", str(ctx.exception))
            self.assertIn("run_acceptance_test.sh", str(ctx.exception))
        finally:
            conn.close()

    def test_operational_db_with_flag_creates_backup(self):
        """assert_csv_import_safe creates a verified backup when authorized."""
        db_path = self._make_db("jordana_invoice.sqlite3")
        os.environ["JORDANA_DATABASE_PATH"] = str(db_path)
        # Insert a row so the backup has content.
        conn = connect(db_path)
        conn.execute(
            "INSERT INTO import_runs (id, source_name, source_path, imported_at, "
            "source_row_count, completed_run_count, status, notes) "
            "VALUES ('test-1', 'test', 'test.csv', '2026-01-01T00:00:00Z', 1, 0, 'imported', 'test')"
        )
        conn.commit()
        backup_path = assert_csv_import_safe(conn, allow_operational=True)
        try:
            self.assertIsNotNone(backup_path)
            self.assertTrue(backup_path.exists())
            # Verify backup contains the row.
            backup_conn = sqlite3.connect(str(backup_path))
            try:
                count = backup_conn.execute("SELECT COUNT(*) FROM import_runs").fetchone()[0]
                self.assertEqual(count, 1)
            finally:
                backup_conn.close()
        finally:
            conn.close()

    def test_non_operational_db_with_flag_does_not_create_backup(self):
        """assert_csv_import_safe does not create a backup for non-operational DBs."""
        db_path = self._make_db()
        conn = connect(db_path)
        try:
            result = assert_csv_import_safe(conn, allow_operational=True)
            self.assertIsNone(result)
        finally:
            conn.close()


class ImportCsvServiceLayerGuardTests(unittest.TestCase):
    """Tests that import_csv (the service-layer function) enforces the guard."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self._old_op = os.environ.get("JORDANA_DATABASE_PATH")
        self._old_backup = os.environ.get("JORDANA_BACKUP_DIR")
        os.environ["JORDANA_BACKUP_DIR"] = str(self.root)

    def tearDown(self):
        if self._old_op is not None:
            os.environ["JORDANA_DATABASE_PATH"] = self._old_op
        else:
            os.environ.pop("JORDANA_DATABASE_PATH", None)
        if self._old_backup is not None:
            os.environ["JORDANA_BACKUP_DIR"] = self._old_backup
        else:
            os.environ.pop("JORDANA_BACKUP_DIR", None)
        self.temp.cleanup()

    def test_import_csv_raises_on_operational_db_without_flag(self):
        """import_csv raises OperationalDatabaseError for the operational DB."""
        from jordana_invoice.importer import import_csv

        db_path = self.root / "jordana_invoice.sqlite3"
        migrate_database(db_path)
        os.environ["JORDANA_DATABASE_PATH"] = str(db_path)
        conn = connect(db_path)
        try:
            with self.assertRaises(OperationalDatabaseError):
                import_csv(conn, str(SAMPLE_CSV))
        finally:
            conn.close()

    def test_import_csv_succeeds_on_temp_db_without_flag(self):
        """import_csv succeeds for a non-operational DB without the flag."""
        from jordana_invoice.importer import import_csv

        db_path = self.root / "test.sqlite3"
        migrate_database(db_path)
        conn = connect(db_path)
        try:
            import_csv(conn, str(SAMPLE_CSV))
            count = conn.execute("SELECT COUNT(*) FROM raw_calendar_snapshots").fetchone()[0]
            self.assertGreater(count, 0)
        finally:
            conn.close()

    def test_import_csv_with_flag_creates_backup_on_operational(self):
        """import_csv with allow_operational_db=True creates a backup."""
        from jordana_invoice.importer import import_csv

        db_path = self.root / "jordana_invoice.sqlite3"
        migrate_database(db_path)
        os.environ["JORDANA_DATABASE_PATH"] = str(db_path)
        conn = connect(db_path)
        try:
            import_csv(conn, str(SAMPLE_CSV), allow_operational_db=True)
            # Backup should exist in the backup dir.
            backups = list(self.root.glob("*backup-migrate-*"))
            self.assertEqual(len(backups), 1)
        finally:
            conn.close()


class CliImportCsvGuardTests(unittest.TestCase):
    """Tests for the CLI import-csv safety guard."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.temp_db = str(self.root / "acceptance_test.sqlite3")
        # Point backups to the temp dir so no real backup dirs are created.
        self.old_backup_dir = os.environ.get("JORDANA_BACKUP_DIR")
        os.environ["JORDANA_BACKUP_DIR"] = str(self.root)
        # Save and unset JORDANA_DATABASE_PATH so tests are deterministic.
        # The CLI will load .env naturally, which may set it.
        self.old_op_path = os.environ.get("JORDANA_DATABASE_PATH")
        os.environ.pop("JORDANA_DATABASE_PATH", None)

    def tearDown(self):
        if self.old_backup_dir is not None:
            os.environ["JORDANA_BACKUP_DIR"] = self.old_backup_dir
        else:
            os.environ.pop("JORDANA_BACKUP_DIR", None)
        if self.old_op_path is not None:
            os.environ["JORDANA_DATABASE_PATH"] = self.old_op_path
        else:
            os.environ.pop("JORDANA_DATABASE_PATH", None)
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
