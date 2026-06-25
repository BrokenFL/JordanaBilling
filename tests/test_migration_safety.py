import os
import shutil
import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import (
    CURRENT_SCHEMA_VERSION,
    MIGRATION_002_MONTHLY_INVOICE_IDENTITY,
    MigrationError,
    connect,
    init_db,
    migrate_database,
)
from jordana_invoice.review_services import dashboard_status
from jordana_invoice.invoice_services import get_business_profile

import jordana_invoice.db as db_module


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

    def tearDown(self):
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

        self.assertEqual(len(rows_before), len(rows_after))
        for row in rows_after:
            self.assertIn(row["migration_id"], (CURRENT_SCHEMA_VERSION, MIGRATION_002_MONTHLY_INVOICE_IDENTITY))

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
        row = conn.execute(
            "SELECT migration_id FROM schema_migrations WHERE migration_id = ?",
            (CURRENT_SCHEMA_VERSION,),
        ).fetchone()
        self.assertIsNotNone(row)
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

        original_data = db_path.read_bytes()
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

        self.assertEqual(db_path.read_bytes(), original_data)

        conn = connect(db_path)
        rows = conn.execute("SELECT * FROM import_runs").fetchall()
        self.assertEqual(len(rows), 0)
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
        rows = conn.execute(
            "SELECT migration_id FROM schema_migrations WHERE migration_id = ?",
            (CURRENT_SCHEMA_VERSION,),
        ).fetchall()
        self.assertEqual(len(rows), 1)
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


if __name__ == "__main__":
    unittest.main()
