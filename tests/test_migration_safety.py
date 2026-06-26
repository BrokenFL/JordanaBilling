import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import (
    CURRENT_SCHEMA_VERSION,
    MIGRATION_002_MONTHLY_INVOICE_IDENTITY,
    MIGRATION_003_PAYMENT_LEDGER_FOUNDATION,
    MIGRATION_004_PAYMENT_PROVENANCE,
    MigrationError,
    connect,
    init_db,
    migrate_database,
)
from jordana_invoice.review_services import dashboard_status
from jordana_invoice.invoice_services import get_business_profile

import jordana_invoice.db as db_module


PROJECT_DIR = Path(__file__).resolve().parents[1]


def _make_old_db(db_path: Path) -> None:
    """Create a database with tables but no schema_migrations entry."""
    conn = connect(db_path)
    conn.executescript(
        """
        CREATE TABLE import_runs (
          id TEXT PRIMARY KEY, source_name TEXT NOT NULL, source_path TEXT,
          imported_at TEXT NOT NULL, source_row_count INTEGER NOT NULL DEFAULT 0,
          completed_run_count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, notes TEXT
        );
        CREATE TABLE raw_calendar_snapshots (
          id TEXT PRIMARY KEY, import_run_id TEXT NOT NULL, source_row_number INTEGER NOT NULL,
          source_hash TEXT NOT NULL, snapshot_key TEXT, run_id TEXT, batch_name TEXT,
          capture_window TEXT, captured_at TEXT, ingested_at TEXT, source_device TEXT,
          timezone TEXT, calendar_event_id TEXT, event_fingerprint TEXT, event_title TEXT,
          start_at TEXT, end_at TEXT, duration_minutes INTEGER, location TEXT, notes TEXT,
          calendar_name TEXT, payload_version INTEGER, raw_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE schema_migrations (
          migration_id TEXT PRIMARY KEY, applied_at TEXT NOT NULL
        );
        CREATE TABLE app_metadata (
          metadata_key TEXT PRIMARY KEY, metadata_value TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE service_catalog (
          service_catalog_id TEXT PRIMARY KEY, canonical_name TEXT NOT NULL,
          normalized_name TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
          description TEXT, catalog_type TEXT NOT NULL DEFAULT 'appointment_method',
          legacy_appointment_method INTEGER NOT NULL DEFAULT 0,
          active INTEGER NOT NULL DEFAULT 1, usage_count INTEGER NOT NULL DEFAULT 0,
          first_used_at TEXT, last_used_at TEXT,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


class MigrationSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_backup_dir = os.environ.get("JORDANA_BACKUP_DIR")
        os.environ["JORDANA_BACKUP_DIR"] = str(self.root)

    def tearDown(self):
        if self.old_backup_dir is not None:
            os.environ["JORDANA_BACKUP_DIR"] = self.old_backup_dir
        else:
            os.environ.pop("JORDANA_BACKUP_DIR", None)
        self.temp.cleanup()

    # --- normal request connections do not alter schema or seed data ---

    def test_request_path_does_not_alter_schema_migrations(self):
        db_path = self.root / "test.sqlite3"
        migrate_database(db_path)

        conn = connect(db_path)
        rows_before = conn.execute("SELECT * FROM schema_migrations").fetchall()
        dashboard_status(conn)
        rows_after = conn.execute("SELECT * FROM schema_migrations").fetchall()
        conn.close()

        current_migration_ids = {migration_id for migration_id, _ in db_module.MIGRATIONS}
        self.assertEqual(len(rows_before), len(rows_after))
        for row in rows_after:
            self.assertIn(row["migration_id"], current_migration_ids)

    def test_request_path_does_not_seed_service_catalog(self):
        db_path = self.root / "test.sqlite3"
        migrate_database(db_path)

        conn = connect(db_path)
        conn.execute("DELETE FROM service_catalog")
        conn.commit()
        count_before = conn.execute("SELECT COUNT(*) AS c FROM service_catalog").fetchone()["c"]
        self.assertEqual(count_before, 0)
        get_business_profile(conn)
        count_after = conn.execute("SELECT COUNT(*) AS c FROM service_catalog").fetchone()["c"]
        conn.close()

        self.assertEqual(count_before, count_after)

    # --- startup migrates an old test database once ---

    def test_startup_migrates_old_database(self):
        db_path = self.root / "test.sqlite3"
        _make_old_db(db_path)
        result = migrate_database(db_path)
        self.assertTrue(result["migrated"])
        conn = connect(db_path)
        for migration_id in [
            CURRENT_SCHEMA_VERSION,
            MIGRATION_002_MONTHLY_INVOICE_IDENTITY,
            MIGRATION_003_PAYMENT_LEDGER_FOUNDATION,
            MIGRATION_004_PAYMENT_PROVENANCE,
        ]:
            row = conn.execute(
                "SELECT migration_id FROM schema_migrations WHERE migration_id = ?",
                (migration_id,),
            ).fetchone()
            self.assertIsNotNone(row, f"Migration {migration_id} was not applied")
        conn.close()

    # --- a backup is created before migration ---

    def test_backup_created_before_migration(self):
        db_path = self.root / "test.sqlite3"
        _make_old_db(db_path)
        result = migrate_database(db_path)
        self.assertTrue(result["migrated"])
        self.assertIsNotNone(result["backup_path"])
        backup_path = Path(result["backup_path"])
        self.assertTrue(backup_path.exists())
        self.assertGreater(backup_path.stat().st_size, 0)
        self.assertIn("backup-migrate-", backup_path.name)

    # --- current databases are not backed up or rewritten unnecessarily ---

    def test_current_database_not_migrated_or_backed_up(self):
        db_path = self.root / "test.sqlite3"
        migrate_database(db_path)

        backups_before = list(self.root.glob("*backup-migrate-*"))
        result = migrate_database(db_path)
        backups_after = list(self.root.glob("*backup-migrate-*"))

        self.assertFalse(result["migrated"])
        self.assertIsNone(result["backup_path"])
        self.assertEqual(len(backups_before), len(backups_after))

    # --- failed migration rolls back and leaves the original usable ---

    def test_failed_migration_rolls_back(self):
        db_path = self.root / "test.sqlite3"
        _make_old_db(db_path)
        original_migrations = db_module.MIGRATIONS[:]

        def failing_migration(conn):
            raise RuntimeError("Simulated migration failure")

        db_module.MIGRATIONS = [(CURRENT_SCHEMA_VERSION, failing_migration)]
        try:
            with self.assertRaises(MigrationError) as ctx:
                migrate_database(db_path)
            self.assertIn("Simulated migration failure", str(ctx.exception))
        finally:
            db_module.MIGRATIONS = original_migrations

        conn = connect(db_path)
        rows = conn.execute("SELECT * FROM import_runs").fetchall()
        self.assertEqual(len(rows), 0)
        migration_rows = conn.execute("SELECT * FROM schema_migrations").fetchall()
        self.assertEqual(len(migration_rows), 0)
        conn.close()

    # --- repeated migration is safe ---

    def test_repeated_migration_is_safe(self):
        db_path = self.root / "test.sqlite3"
        r1 = migrate_database(db_path)
        self.assertTrue(r1["migrated"])
        r2 = migrate_database(db_path)
        self.assertFalse(r2["migrated"])
        r3 = migrate_database(db_path)
        self.assertFalse(r3["migrated"])

        conn = connect(db_path)
        for migration_id in [
            CURRENT_SCHEMA_VERSION,
            MIGRATION_002_MONTHLY_INVOICE_IDENTITY,
            MIGRATION_003_PAYMENT_LEDGER_FOUNDATION,
            MIGRATION_004_PAYMENT_PROVENANCE,
        ]:
            rows = conn.execute(
                "SELECT migration_id FROM schema_migrations WHERE migration_id = ?",
                (migration_id,),
            ).fetchall()
            self.assertEqual(len(rows), 1, f"Migration {migration_id} does not have exactly 1 record")
        conn.close()

    # --- app refuses to start when migration fails ---

    def test_app_refuses_to_start_on_migration_failure(self):
        db_path = self.root / "test.sqlite3"
        _make_old_db(db_path)

        original_migrations = db_module.MIGRATIONS[:]

        def failing_migration(conn):
            raise RuntimeError("Startup failure simulation")

        db_module.MIGRATIONS = [(CURRENT_SCHEMA_VERSION, failing_migration)]
        try:
            with self.assertRaises(MigrationError):
                migrate_database(db_path)
        finally:
            db_module.MIGRATIONS = original_migrations

        self.assertTrue(db_path.exists())
        conn = connect(db_path)
        conn.execute("SELECT * FROM import_runs").fetchall()
        conn.close()

    def test_backup_created_from_wal_contains_committed_rows(self):
        db_path = self.root / "wal-source.sqlite3"
        _make_old_db(db_path)

        writer = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            writer.execute("PRAGMA journal_mode = WAL")
            writer.execute("PRAGMA wal_autocheckpoint = 0")
            writer.execute(
                """
                INSERT INTO import_runs (
                    id, source_name, source_path, imported_at,
                    source_row_count, completed_run_count, status, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "run-1",
                    "test",
                    "fixture.csv",
                    "2026-06-26T00:00:00Z",
                    1,
                    1,
                    "complete",
                    "committed in wal",
                ),
            )
            writer.commit()

            wal_path = Path(f"{db_path}-wal")
            self.assertTrue(wal_path.exists(), "Expected WAL file before backup")

            backup_path = Path(db_module._create_backup(db_path))
            backup_conn = sqlite3.connect(str(backup_path))
            try:
                count = backup_conn.execute("SELECT COUNT(*) FROM import_runs").fetchone()[0]
            finally:
                backup_conn.close()
        finally:
            writer.close()

        self.assertEqual(count, 1)

    def test_failed_migration_restore_preserves_committed_wal_rows(self):
        db_path = self.root / "wal-restore.sqlite3"
        _make_old_db(db_path)

        writer = sqlite3.connect(str(db_path), timeout=5.0)
        writer.execute("PRAGMA journal_mode = WAL")
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute(
            """
            INSERT INTO import_runs (
                id, source_name, source_path, imported_at,
                source_row_count, completed_run_count, status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-restore",
                "test",
                "fixture.csv",
                "2026-06-26T00:00:00Z",
                1,
                1,
                "complete",
                "restore me",
            ),
        )
        writer.commit()

        original_migrations = db_module.MIGRATIONS[:]

        def failing_migration(conn):
            raise RuntimeError("Restore simulation failure")

        db_module.MIGRATIONS = [(CURRENT_SCHEMA_VERSION, failing_migration)]
        try:
            with self.assertRaises(MigrationError):
                migrate_database(db_path)
        finally:
            db_module.MIGRATIONS = original_migrations
            writer.close()

        restored = sqlite3.connect(str(db_path))
        try:
            row = restored.execute(
                "SELECT notes FROM import_runs WHERE id = ?",
                ("run-restore",),
            ).fetchone()
        finally:
            restored.close()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], "restore me")


class BackupScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_backup_dir = os.environ.get("JORDANA_BACKUP_DIR")
        os.environ["JORDANA_BACKUP_DIR"] = str(self.root)

    def tearDown(self):
        if self.old_backup_dir is not None:
            os.environ["JORDANA_BACKUP_DIR"] = self.old_backup_dir
        else:
            os.environ.pop("JORDANA_BACKUP_DIR", None)
        self.temp.cleanup()

    def test_backup_script_captures_committed_wal_rows(self):
        db_path = self.root / "script.sqlite3"
        writer = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            writer.execute("PRAGMA journal_mode = WAL")
            writer.execute("PRAGMA wal_autocheckpoint = 0")
            writer.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY, note TEXT NOT NULL)")
            writer.execute("INSERT INTO evidence (note) VALUES (?)", ("wal row",))
            writer.commit()

            wal_path = Path(f"{db_path}-wal")
            self.assertTrue(wal_path.exists(), "Expected WAL file before backup script")

            result = subprocess.run(
                ["bash", str(PROJECT_DIR / "scripts" / "backup_db.sh"), str(db_path)],
                cwd=str(PROJECT_DIR),
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            writer.close()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Integrity: ok", result.stdout)

        backup_line = next(
            line for line in result.stdout.splitlines() if line.startswith("Backup created: ")
        )
        backup_path = Path(backup_line.split(": ", 1)[1])
        self.assertTrue(backup_path.exists())

        backup_conn = sqlite3.connect(str(backup_path))
        try:
            row = backup_conn.execute("SELECT note FROM evidence").fetchone()
        finally:
            backup_conn.close()

        self.assertEqual(row[0], "wal row")

    def test_get_backup_dir_default_and_tilde_expansion(self):
        # Save current JORDANA_BACKUP_DIR and remove it to test default path
        old_val = os.environ.get("JORDANA_BACKUP_DIR")
        if "JORDANA_BACKUP_DIR" in os.environ:
            del os.environ["JORDANA_BACKUP_DIR"]
        try:
            expected = Path.home() / ".jordana_invoice" / "backups"
            self.assertEqual(db_module.get_backup_dir(), expected)
        finally:
            if old_val is not None:
                os.environ["JORDANA_BACKUP_DIR"] = old_val

    def test_get_backup_dir_override_and_tilde_expansion(self):
        old_val = os.environ.get("JORDANA_BACKUP_DIR")
        # Test override with tilde
        os.environ["JORDANA_BACKUP_DIR"] = "~/custom_backup_test_dir"
        try:
            expected = Path.home() / "custom_backup_test_dir"
            self.assertEqual(db_module.get_backup_dir(), expected)
        finally:
            if old_val is not None:
                os.environ["JORDANA_BACKUP_DIR"] = old_val

    def test_create_backup_creates_directory_automatically(self):
        custom_dir = self.root / "sub" / "folder" / "backups"
        self.assertFalse(custom_dir.exists())

        old_val = os.environ.get("JORDANA_BACKUP_DIR")
        os.environ["JORDANA_BACKUP_DIR"] = str(custom_dir)
        try:
            db_path = self.root / "test_auto.sqlite3"
            _make_old_db(db_path)
            
            backup_path = db_module._create_backup(db_path)
            self.assertTrue(custom_dir.exists())
            self.assertTrue(backup_path.exists())
            self.assertEqual(backup_path.parent, custom_dir)
        finally:
            if old_val is not None:
                os.environ["JORDANA_BACKUP_DIR"] = old_val

    def test_backup_script_respects_env_override_and_tilde(self):
        db_path = self.root / "script_override.sqlite3"
        writer = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            writer.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY)")
            writer.commit()
        finally:
            writer.close()

        custom_backup_dir_path = Path.home() / "jordana_test_script_backups"
        if custom_backup_dir_path.exists():
            import shutil
            shutil.rmtree(custom_backup_dir_path, ignore_errors=True)

        env = os.environ.copy()
        env["JORDANA_BACKUP_DIR"] = "~/jordana_test_script_backups"

        try:
            result = subprocess.run(
                ["bash", str(PROJECT_DIR / "scripts" / "backup_db.sh"), str(db_path)],
                cwd=str(PROJECT_DIR),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(custom_backup_dir_path.exists())
            
            backups = list(custom_backup_dir_path.glob("script_override.backup-*.sqlite3"))
            self.assertEqual(len(backups), 1)
        finally:
            if custom_backup_dir_path.exists():
                import shutil
                shutil.rmtree(custom_backup_dir_path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
