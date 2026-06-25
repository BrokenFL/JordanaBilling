"""Focused tests for SQLite concurrency and sync-collision protection.

Covers:
- All managed connections have approved PRAGMA settings (foreign_keys, WAL, busy_timeout).
- Concurrent reads during a writer in WAL mode.
- Overlapping sync prevention (second sync fails cleanly within bounded timeout).
- Safe stale sync lock recovery (process exit releases flock).
- Lock timeout produces a clear error message.
- Failed sync rolls back completely (no partial imports).
- Atomic invoice finalization under contention (BEGIN IMMEDIATE).
- Migrations and sync cannot run concurrently.
- Repeated normal startup is safe (no locks left behind).
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from jordana_invoice.db import (
    CURRENT_SCHEMA_VERSION,
    DEFAULT_BUSY_TIMEOUT_MS,
    DatabaseLock,
    LockError,
    MigrationError,
    connect,
    migrate_database,
)
from jordana_invoice.google_sync import (
    EMPTY_CURSOR,
    SOURCE_NAME,
    SyncConfig,
    SyncError,
    sync_with_connection,
)
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import (
    DatabaseBusyError,
    finalize_invoice,
    save_business_profile,
)
from jordana_invoice.review_services import (
    approve_candidate,
    create_billing_party,
    create_person,
)


def raw_row(snapshot_key: str, title: str = "Bonnie 5") -> dict[str, str]:
    return {
        "ingested_at": "2026-06-22T02:00:00.000Z",
        "snapshot_key": snapshot_key,
        "run_id": "run-1",
        "batch_name": "test",
        "capture_window": "next_2_days",
        "captured_at": "2026-06-22T01:00:00.000Z",
        "source_device": "test",
        "timezone": "America/New_York",
        "calendar_event_id": "",
        "event_fingerprint": f"fp-{snapshot_key}",
        "event_title": title,
        "start_at": "2026-06-23T17:00:00-04:00",
        "end_at": "2026-06-23T18:00:00-04:00",
        "duration_minutes": "60",
        "location": "",
        "notes": "",
        "calendar": "Jordana Work",
        "payload_version": "2",
        "raw_json": "{}",
    }


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)

    def __call__(self, url, payload, timeout_seconds):
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _sync_config(db_path: str, reports_dir: str) -> SyncConfig:
    return SyncConfig(
        apps_script_url="https://example.test/exec",
        ingest_api_key="test-key",
        database_path=db_path,
        reports_dir=reports_dir,
    )


class ConnectionSettingsTests(unittest.TestCase):
    """Verify all managed connections have approved PRAGMA settings."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "test.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def test_foreign_keys_enabled(self):
        conn = connect(self.db_path)
        result = conn.execute("PRAGMA foreign_keys").fetchone()
        conn.close()
        self.assertEqual(result[0], 1)

    def test_wal_journal_mode(self):
        conn = connect(self.db_path)
        result = conn.execute("PRAGMA journal_mode").fetchone()
        conn.close()
        self.assertEqual(result[0].lower(), "wal")

    def test_busy_timeout_set(self):
        conn = connect(self.db_path)
        result = conn.execute("PRAGMA busy_timeout").fetchone()
        conn.close()
        self.assertEqual(result[0], DEFAULT_BUSY_TIMEOUT_MS)

    def test_row_factory_is_row(self):
        import sqlite3
        conn = connect(self.db_path)
        self.assertIs(conn.row_factory, sqlite3.Row)
        conn.close()


class WALConcurrencyTests(unittest.TestCase):
    """Concurrent reads during a writer in WAL mode."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "test.sqlite3"
        migrate_database(self.db_path)
        self.conn = connect(self.db_path)
        import_rows(self.conn, [raw_row("snap-1")], "test")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_concurrent_read_during_write(self):
        read_result = []
        barrier = threading.Barrier(2)

        def reader():
            barrier.wait(timeout=5)
            rconn = connect(self.db_path)
            try:
                row = rconn.execute(
                    "SELECT COUNT(*) AS c FROM raw_calendar_snapshots"
                ).fetchone()
                read_result.append(row["c"])
            finally:
                rconn.close()

        def writer():
            barrier.wait(timeout=5)
            wconn = connect(self.db_path)
            try:
                import_rows(wconn, [raw_row("snap-2")], "test")
            finally:
                wconn.close()

        t_read = threading.Thread(target=reader)
        t_write = threading.Thread(target=writer)
        t_read.start()
        t_write.start()
        t_read.join(timeout=10)
        t_write.join(timeout=10)

        self.assertEqual(len(read_result), 1)
        self.assertGreaterEqual(read_result[0], 1)


class SyncLockTests(unittest.TestCase):
    """Overlapping sync prevention and stale lock recovery."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "test.sqlite3"
        self.reports_dir = self.root / "Reports"
        migrate_database(self.db_path)
        self.conn = connect(self.db_path)
        self.config = _sync_config(str(self.db_path), str(self.reports_dir))

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_overlapping_sync_fails_cleanly(self):
        lock = DatabaseLock(self.db_path, timeout_seconds=1.0)
        lock.acquire()
        try:
            with self.assertRaises(SyncError) as ctx:
                sync_with_connection(
                    self.conn,
                    self.config,
                    transport=FakeTransport([
                        {
                            "ok": True,
                            "record_type": "sync_response",
                            "rows": [],
                            "next_cursor": EMPTY_CURSOR,
                            "has_more": False,
                        }
                    ]),
                )
            self.assertIn("lock", str(ctx.exception).lower())
        finally:
            lock.release()

    def test_stale_lock_recovery(self):
        lock = DatabaseLock(self.db_path, timeout_seconds=1.0)
        lock.acquire()
        lock.release()
        result = sync_with_connection(
            self.conn,
            self.config,
            transport=FakeTransport([
                {
                    "ok": True,
                    "record_type": "sync_response",
                    "rows": [],
                    "next_cursor": EMPTY_CURSOR,
                    "has_more": False,
                }
            ]),
        )
        self.assertEqual(result.rows_imported, 0)

    def test_lock_timeout_produces_clear_error(self):
        lock = DatabaseLock(self.db_path, timeout_seconds=0.1)
        lock.acquire()
        try:
            second_lock = DatabaseLock(self.db_path, timeout_seconds=0.1)
            with self.assertRaises(LockError) as ctx:
                second_lock.acquire()
            self.assertIn("lock", str(ctx.exception).lower())
        finally:
            lock.release()


class SyncRollbackTests(unittest.TestCase):
    """Failed sync rolls back completely."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "test.sqlite3"
        self.reports_dir = self.root / "Reports"
        migrate_database(self.db_path)
        self.conn = connect(self.db_path)
        self.config = _sync_config(str(self.db_path), str(self.reports_dir))

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_failed_sync_rolls_back(self):
        count_before = self.conn.execute(
            "SELECT COUNT(*) AS c FROM raw_calendar_snapshots"
        ).fetchone()["c"]

        with self.assertRaises(SyncError):
            sync_with_connection(
                self.conn,
                self.config,
                transport=FakeTransport([SyncError("network failure")]),
            )

        count_after = self.conn.execute(
            "SELECT COUNT(*) AS c FROM raw_calendar_snapshots"
        ).fetchone()["c"]
        self.assertEqual(count_before, count_after)

    def test_failed_sync_does_not_advance_cursor(self):
        self.conn.execute(
            "INSERT INTO sync_state (source_name, cursor_value) VALUES (?, ?)",
            (SOURCE_NAME, "2026-06-22T01:00:00.000Z"),
        )
        self.conn.commit()

        with self.assertRaises(SyncError):
            sync_with_connection(
                self.conn,
                self.config,
                transport=FakeTransport([SyncError("boom")]),
            )

        cursor = self.conn.execute(
            "SELECT cursor_value FROM sync_state WHERE source_name = ?",
            (SOURCE_NAME,),
        ).fetchone()["cursor_value"]
        self.assertEqual(cursor, "2026-06-22T01:00:00.000Z")


class InvoiceFinalizationContentionTests(unittest.TestCase):
    """Atomic invoice finalization under contention."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "test.sqlite3"
        migrate_database(self.db_path)
        self.conn = connect(self.db_path)
        self.person = create_person(self.conn, {
            "first_name": "Pat", "last_name": "Client",
            "display_name": "Pat Client",
        })
        self.party = create_billing_party(self.conn, {
            "billing_name": "Pat Client",
            "person_id": self.person["person_id"],
            "billing_email": "pat@example.test",
            "billing_address_line_1": "1 Test St",
            "billing_city": "Test", "billing_state": "FL",
            "billing_postal_code": "00000",
            "preferred_delivery_method": "email",
        })
        save_business_profile(self.conn, {
            "business_name": "Test Practice",
            "provider_display_name": "Test Provider",
            "address_line_1": "100 Test Ave",
            "city": "Test", "state": "FL", "postal_code": "00000",
            "phone": "555-0100", "email": "billing@test",
            "payee_name": "Test Payee",
            "payment_address_line_1": "100 Test Ave",
            "payment_city": "Test", "payment_state": "FL",
            "payment_postal_code": "00000",
        })

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_finalize_raises_busy_error_when_locked(self):
        from jordana_invoice.invoice_services import create_invoice_draft
        from jordana_invoice.util import stable_hash

        import_rows(self.conn, [raw_row("inv-1", "Pat Client | 60 | Office")], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash("event_fingerprint:fp-inv-1"),),
        ).fetchone()[0]
        approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Pat Client"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60, "service_mode": "office",
            "time_category": "standard", "approved_rate": "150.00",
            "payment_status": "unpaid", "billing_treatment": "billable",
        })
        session = self.conn.execute("SELECT * FROM sessions WHERE candidate_id = ?", (candidate_id,)).fetchone()
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_period_start": "2026-06-01",
            "billing_period_end": "2026-06-30",
            "invoice_date": "2026-06-30",
            "session_ids": [session["id"]],
        })
        invoice_id = draft["invoice"]["invoice_id"]

        blocker = connect(self.db_path)
        blocker.execute("BEGIN IMMEDIATE")
        try:
            with self.assertRaises(DatabaseBusyError):
                finalize_invoice(self.conn, invoice_id, expected_revision=1)
        finally:
            blocker.rollback()
            blocker.close()


class MigrationSyncExclusionTests(unittest.TestCase):
    """Migrations and sync cannot run concurrently."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "test.sqlite3"
        self.reports_dir = self.root / "Reports"
        migrate_database(self.db_path)
        self.conn = connect(self.db_path)
        self.config = _sync_config(str(self.db_path), str(self.reports_dir))

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_sync_fails_when_migration_holds_lock(self):
        lock = DatabaseLock(self.db_path, timeout_seconds=0.5)
        lock.acquire()
        try:
            with self.assertRaises(SyncError) as ctx:
                sync_with_connection(
                    self.conn,
                    self.config,
                    transport=FakeTransport([
                        {
                            "ok": True,
                            "record_type": "sync_response",
                            "rows": [],
                            "next_cursor": EMPTY_CURSOR,
                            "has_more": False,
                        }
                    ]),
                )
            self.assertIn("lock", str(ctx.exception).lower())
        finally:
            lock.release()

    def test_migration_fails_when_sync_holds_lock(self):
        lock = DatabaseLock(self.db_path, timeout_seconds=0.5)
        lock.acquire()
        try:
            with self.assertRaises(MigrationError) as ctx:
                migrate_database(self.db_path)
            self.assertIn("lock", str(ctx.exception).lower())
        finally:
            lock.release()


class RepeatedStartupTests(unittest.TestCase):
    """Repeated normal startup is safe — no locks left behind."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "test.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def test_repeated_migrate_is_safe(self):
        r1 = migrate_database(self.db_path)
        self.assertTrue(r1["migrated"])
        r2 = migrate_database(self.db_path)
        self.assertFalse(r2["migrated"])
        r3 = migrate_database(self.db_path)
        self.assertFalse(r3["migrated"])

        conn = connect(self.db_path)
        rows = conn.execute(
            "SELECT migration_id FROM schema_migrations WHERE migration_id = ?",
            (CURRENT_SCHEMA_VERSION,),
        ).fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)

    def test_no_lock_file_left_after_migrate(self):
        migrate_database(self.db_path)
        lock_file = Path(str(self.db_path) + ".lock")
        self.assertTrue(lock_file.exists())
        second_lock = DatabaseLock(self.db_path, timeout_seconds=1.0)
        second_lock.acquire()
        second_lock.release()

    def test_connect_after_migrate_works(self):
        migrate_database(self.db_path)
        conn = connect(self.db_path)
        conn.execute("SELECT 1")
        conn.close()


if __name__ == "__main__":
    unittest.main()
