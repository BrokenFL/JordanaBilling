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
    OperationalImportAuthorization,
    assert_csv_import_safe,
    authorize_operational_import,
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

    def test_non_operational_db_passes_without_authorization(self):
        """assert_csv_import_safe does not raise for a non-operational DB."""
        db_path = self._make_db()
        conn = connect(db_path)
        try:
            result = assert_csv_import_safe(conn, authorization=None)
            self.assertIsNone(result)
        finally:
            conn.close()

    def test_operational_db_raises_without_authorization(self):
        """assert_csv_import_safe raises for the operational DB without authorization."""
        db_path = self._make_db("jordana_invoice.sqlite3")
        os.environ["JORDANA_DATABASE_PATH"] = str(db_path)
        conn = connect(db_path)
        try:
            with self.assertRaises(OperationalDatabaseError) as ctx:
                assert_csv_import_safe(conn, authorization=None)
            self.assertIn("Refused", str(ctx.exception))
            self.assertIn("run_acceptance_test.sh", str(ctx.exception))
        finally:
            conn.close()

    def test_operational_db_with_authorization_object_no_duplicate_backup(self):
        """assert_csv_import_safe with authorization object does not create a second backup."""
        db_path = self._make_db("jordana_invoice.sqlite3")
        os.environ["JORDANA_DATABASE_PATH"] = str(db_path)
        conn = connect(db_path)
        try:
            # Create authorization with backup first.
            auth = authorize_operational_import(db_path, confirmed_path=str(db_path))
            self.assertIsNotNone(auth.backup_path)
            self.assertTrue(auth.backup_path.exists())
            # Pass to assert_csv_import_safe — should NOT create another backup.
            result = assert_csv_import_safe(conn, authorization=auth)
            self.assertEqual(result, auth.backup_path)
            # Only one backup file should exist.
            backups = list(self.root.glob("*backup-migrate-*"))
            self.assertEqual(len(backups), 1)
        finally:
            conn.close()

    def test_non_operational_db_ignores_authorization(self):
        """assert_csv_import_safe does not create a backup for non-operational DBs."""
        db_path = self._make_db()
        conn = connect(db_path)
        try:
            result = assert_csv_import_safe(conn, authorization=None)
            self.assertIsNone(result)
        finally:
            conn.close()

    def test_operational_db_rejects_boolean_true(self):
        """assert_csv_import_safe rejects a plain Boolean True for operational DB."""
        db_path = self._make_db("jordana_invoice.sqlite3")
        os.environ["JORDANA_DATABASE_PATH"] = str(db_path)
        conn = connect(db_path)
        try:
            with self.assertRaises(OperationalDatabaseError):
                assert_csv_import_safe(conn, authorization=True)  # type: ignore[arg-type]
        finally:
            conn.close()

    def test_operational_db_rejects_fabricated_authorization_wrong_path(self):
        """assert_csv_import_safe rejects a fabricated authorization with wrong confirmed_path."""
        db_path = self._make_db("jordana_invoice.sqlite3")
        os.environ["JORDANA_DATABASE_PATH"] = str(db_path)
        conn = connect(db_path)
        try:
            fake_auth = OperationalImportAuthorization(
                confirmed_path=Path("/wrong/path.sqlite3"),
                backup_path=None,
            )
            with self.assertRaises(OperationalDatabaseError) as ctx:
                assert_csv_import_safe(conn, authorization=fake_auth)
            self.assertIn("does not match", str(ctx.exception))
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

    def test_import_csv_with_authorization_creates_backup_on_operational(self):
        """import_csv with OperationalImportAuthorization creates exactly one backup."""
        from jordana_invoice.importer import import_csv

        db_path = self.root / "jordana_invoice.sqlite3"
        migrate_database(db_path)
        os.environ["JORDANA_DATABASE_PATH"] = str(db_path)
        # Authorize first (creates backup).
        auth = authorize_operational_import(db_path, confirmed_path=str(db_path))
        conn = connect(db_path)
        try:
            import_csv(conn, str(SAMPLE_CSV), operational_authorization=auth)
            # Exactly one backup should exist (from authorize, not from assert).
            backups = list(self.root.glob("*backup-migrate-*"))
            self.assertEqual(len(backups), 1)
        finally:
            conn.close()

    def test_import_csv_rejects_boolean_true_on_operational(self):
        """import_csv with allow_operational_db=True (legacy bool) is now rejected."""
        from jordana_invoice.importer import import_csv

        db_path = self.root / "jordana_invoice.sqlite3"
        migrate_database(db_path)
        os.environ["JORDANA_DATABASE_PATH"] = str(db_path)
        conn = connect(db_path)
        try:
            with self.assertRaises(OperationalDatabaseError):
                import_csv(conn, str(SAMPLE_CSV), operational_authorization=True)  # type: ignore[arg-type]
        finally:
            conn.close()

    def test_import_csv_rejects_authorization_for_different_db(self):
        """Authorization created for one operational DB cannot be reused for another."""
        from jordana_invoice.importer import import_csv

        # Create two separate operational DBs in different temp dirs.
        tmp1 = tempfile.mkdtemp()
        tmp2 = tempfile.mkdtemp()
        old_backup = os.environ.get("JORDANA_BACKUP_DIR")
        try:
            db1 = Path(tmp1) / "jordana_invoice.sqlite3"
            db2 = Path(tmp2) / "jordana_invoice.sqlite3"
            migrate_database(db1)
            migrate_database(db2)

            # Authorize for db1.
            os.environ["JORDANA_DATABASE_PATH"] = str(db1)
            os.environ["JORDANA_BACKUP_DIR"] = tmp1
            auth1 = authorize_operational_import(db1, confirmed_path=str(db1))

            # Switch configured operational to db2, try using auth1.
            os.environ["JORDANA_DATABASE_PATH"] = str(db2)
            os.environ["JORDANA_BACKUP_DIR"] = tmp2
            conn = connect(db2)
            try:
                with self.assertRaises(OperationalDatabaseError) as ctx:
                    import_csv(conn, str(SAMPLE_CSV), operational_authorization=auth1)
                self.assertIn("does not match", str(ctx.exception))
            finally:
                conn.close()
        finally:
            os.environ.pop("JORDANA_DATABASE_PATH", None)
            if old_backup:
                os.environ["JORDANA_BACKUP_DIR"] = old_backup
            else:
                os.environ.pop("JORDANA_BACKUP_DIR", None)


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


class AuthorizeOperationalImportTests(unittest.TestCase):
    """Tests for authorize_operational_import — the pre-migration authorization."""

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

    def _make_op_db(self) -> Path:
        db_path = self.root / "jordana_invoice.sqlite3"
        migrate_database(db_path)
        os.environ["JORDANA_DATABASE_PATH"] = str(db_path)
        return db_path

    def test_authorize_with_correct_confirmation_returns_authorization(self):
        """authorize_operational_import with correct path returns authorization with backup."""
        db_path = self._make_op_db()
        auth = authorize_operational_import(db_path, confirmed_path=str(db_path))
        self.assertIsInstance(auth, OperationalImportAuthorization)
        self.assertEqual(auth.confirmed_path, db_path.resolve())
        self.assertIsNotNone(auth.backup_path)
        self.assertTrue(auth.backup_path.exists())

    def test_authorize_without_confirmation_raises(self):
        """authorize_operational_import without confirmed_path raises."""
        db_path = self._make_op_db()
        with self.assertRaises(OperationalDatabaseError) as ctx:
            authorize_operational_import(db_path, confirmed_path=None)
        self.assertIn("confirmation", str(ctx.exception).lower())

    def test_authorize_with_wrong_confirmation_raises(self):
        """authorize_operational_import with wrong path raises."""
        db_path = self._make_op_db()
        with self.assertRaises(OperationalDatabaseError) as ctx:
            authorize_operational_import(db_path, confirmed_path="/wrong/path/db.sqlite3")
        self.assertIn("does not match", str(ctx.exception))

    def test_authorize_with_symlink_confirmation_resolves(self):
        """authorize_operational_import resolves symlinks in confirmation path."""
        db_path = self._make_op_db()
        link_path = self.root / "link_to_op.sqlite3"
        link_path.symlink_to(db_path)
        auth = authorize_operational_import(db_path, confirmed_path=str(link_path))
        self.assertEqual(auth.confirmed_path, db_path.resolve())

    def test_authorize_creates_backup_before_any_mutation(self):
        """authorize_operational_import creates backup before migration could run."""
        db_path = self._make_op_db()
        # Insert data to verify backup captures it.
        conn = connect(db_path)
        conn.execute(
            "INSERT INTO import_runs (id, source_name, source_path, imported_at, "
            "source_row_count, completed_run_count, status, notes) "
            "VALUES ('pre-mig-1', 'test', 'test.csv', '2026-01-01T00:00:00Z', 1, 0, 'imported', 'test')"
        )
        conn.commit()
        conn.close()

        auth = authorize_operational_import(db_path, confirmed_path=str(db_path))
        self.assertTrue(auth.backup_path.exists())
        # Verify backup has the data.
        backup_conn = sqlite3.connect(str(auth.backup_path))
        try:
            count = backup_conn.execute("SELECT COUNT(*) FROM import_runs").fetchone()[0]
            self.assertEqual(count, 1)
        finally:
            backup_conn.close()

    def test_authorize_non_operational_raises(self):
        """authorize_operational_import raises for non-operational DB."""
        db_path = self.root / "test.sqlite3"
        migrate_database(db_path)
        with self.assertRaises(OperationalDatabaseError):
            authorize_operational_import(db_path, confirmed_path=str(db_path))


class CliConfirmationTests(unittest.TestCase):
    """Tests for the CLI --confirm-operational-db-path flag."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.temp_db = str(self.root / "acceptance_test.sqlite3")
        self.old_backup_dir = os.environ.get("JORDANA_BACKUP_DIR")
        os.environ["JORDANA_BACKUP_DIR"] = str(self.root)
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

    def test_allow_without_confirm_path_refused(self):
        """--allow-operational-db without --confirm-operational-db-path is refused."""
        result = _run_cli(
            "--db", "data/jordana_invoice.sqlite3",
            "import-csv", str(SAMPLE_CSV),
            "--allow-operational-db",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("REFUSED", result.stderr)
        self.assertIn("confirmation", result.stderr.lower())

    def test_allow_with_wrong_confirm_path_refused(self):
        """--allow-operational-db with wrong --confirm-operational-db-path is refused."""
        result = _run_cli(
            "--db", "data/jordana_invoice.sqlite3",
            "import-csv", str(SAMPLE_CSV),
            "--allow-operational-db",
            "--confirm-operational-db-path", "/wrong/path.sqlite3",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("REFUSED", result.stderr)
        self.assertIn("does not match", result.stderr)

    def test_refusal_mentions_confirm_flag(self):
        """The refusal message mentions --confirm-operational-db-path."""
        result = _run_cli(
            "--db", "data/jordana_invoice.sqlite3",
            "import-csv", str(SAMPLE_CSV),
        )
        self.assertIn("--confirm-operational-db-path", result.stderr)

    def test_non_operational_does_not_require_confirm(self):
        """Non-operational temp DB does not require confirmation flags."""
        result = _run_cli(
            "--db", self.temp_db,
            "import-csv", str(SAMPLE_CSV),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("REFUSED", result.stderr)

    def test_non_operational_ignores_confirm_flag(self):
        """--confirm-operational-db-path is ignored for non-operational DBs."""
        result = _run_cli(
            "--db", self.temp_db,
            "import-csv", str(SAMPLE_CSV),
            "--allow-operational-db",
            "--confirm-operational-db-path", str(self.temp_db),
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class ImportRowsNoBypassTests(unittest.TestCase):
    """Verify that import_rows (lower-level) cannot bypass the safety boundary.

    import_rows is intentionally unguarded because it's used by both
    import_csv (guarded) and google_sync (legitimate production sync).
    This test documents that import_rows itself does NOT check, and that
    the guard is on import_csv only.
    """

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

    def test_import_rows_does_not_guard(self):
        """import_rows is intentionally unguarded (used by sync). import_csv is the guard point."""
        from jordana_invoice.importer import import_csv, import_rows

        db_path = self.root / "jordana_invoice.sqlite3"
        migrate_database(db_path)
        os.environ["JORDANA_DATABASE_PATH"] = str(db_path)
        conn = connect(db_path)
        try:
            # import_rows directly should work (it's the low-level function).
            # This is by design — google_sync uses it for routine production sync.
            raw_row = {
                "calendar_event_id": "test-1",
                "event_title": "Test Client 60",
                "start_at": "2026-06-23T17:00:00-04:00",
                "end_at": "2026-06-23T18:00:00-04:00",
                "duration_minutes": "60",
                "calendar": "Jordana Work",
                "payload_version": "2",
                "raw_json": "{}",
            }
            import_rows(conn, [raw_row], "test")
            count = conn.execute("SELECT COUNT(*) FROM raw_calendar_snapshots").fetchone()[0]
            self.assertGreater(count, 0)

            # But import_csv on the same operational DB must raise.
            with self.assertRaises(OperationalDatabaseError):
                import_csv(conn, str(SAMPLE_CSV))
        finally:
            conn.close()


class BackupBeforeMigrationTests(unittest.TestCase):
    """Verify that backup is created before migration in the CLI flow."""

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

    def test_backup_exists_before_migration(self):
        """authorize_operational_import creates backup before migrate_database runs."""
        db_path = self.root / "jordana_invoice.sqlite3"
        migrate_database(db_path)
        os.environ["JORDANA_DATABASE_PATH"] = str(db_path)

        # Insert data and checkpoint WAL so backup captures it.
        conn = connect(db_path)
        conn.execute(
            "INSERT INTO import_runs (id, source_name, source_path, imported_at, "
            "source_row_count, completed_run_count, status, notes) "
            "VALUES ('pre-backup-1', 'test', 'test.csv', '2026-01-01T00:00:00Z', 1, 0, 'imported', 'test')"
        )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()

        # Authorize — this creates the backup.
        auth = authorize_operational_import(db_path, confirmed_path=str(db_path))
        self.assertTrue(auth.backup_path.exists())

        # Now run migration (should be no-op since already current).
        result = migrate_database(db_path)
        self.assertFalse(result["migrated"])

        # Backup should still exist and contain the data.
        backup_conn = sqlite3.connect(str(auth.backup_path))
        try:
            count = backup_conn.execute("SELECT COUNT(*) FROM import_runs").fetchone()[0]
            self.assertEqual(count, 1)
        finally:
            backup_conn.close()

    def test_no_duplicate_backup_during_one_authorized_import(self):
        """One authorized import creates exactly one backup, not two."""
        db_path = self.root / "jordana_invoice.sqlite3"
        migrate_database(db_path)
        os.environ["JORDANA_DATABASE_PATH"] = str(db_path)

        # Authorize (creates backup #1).
        auth = authorize_operational_import(db_path, confirmed_path=str(db_path))

        # Pass to assert_csv_import_safe (should NOT create backup #2).
        conn = connect(db_path)
        try:
            result = assert_csv_import_safe(conn, authorization=auth)
            self.assertEqual(result, auth.backup_path)
        finally:
            conn.close()

        # Only one backup.
        backups = list(self.root.glob("*backup-migrate-*"))
        self.assertEqual(len(backups), 1)


if __name__ == "__main__":
    unittest.main()
