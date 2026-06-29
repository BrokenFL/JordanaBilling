import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import connect, init_db
from jordana_invoice.google_sync import (
    EMPTY_CURSOR,
    SOURCE_NAME,
    SyncConfig,
    SyncError,
    get_cursor,
    parse_cursor,
    public_sync_status,
    should_run_initial_full_sync,
    sync_calendar_automatically,
    sync_with_connection,
    sync_status_for_connection,
)


def row(
    snapshot_key,
    run_id="run-complete",
    capture_window="next_2_days",
    ingested_at="2026-06-22T02:00:00.000Z",
    title="Bonnie 5",
    start_at="2026-06-23T17:00:00-04:00",
    event_fingerprint=None,
):
    return {
        "ingested_at": ingested_at,
        "snapshot_key": snapshot_key,
        "run_id": run_id,
        "batch_name": "JORDANA_CALENDAR_[2026-06-21_225234]",
        "capture_window": capture_window,
        "captured_at": "2026-06-21T22:52:34-04:00",
        "source_device": "jordana_iphone",
        "timezone": "America/New_York",
        "calendar_event_id": "",
        "event_fingerprint": event_fingerprint or f"fp-{snapshot_key}",
        "event_title": title,
        "start_at": start_at,
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
        self.calls = []

    def __call__(self, url, payload, timeout_seconds):
        self.calls.append(payload)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "jordana.sqlite3"
        self.reports_dir = self.root / "Reports"
        self.config = SyncConfig(
            apps_script_url="https://example.test/exec",
            ingest_api_key="test-key",
            database_path=str(self.db_path),
            reports_dir=str(self.reports_dir),
        )
        self.conn = connect(self.db_path)
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_empty_sync_response(self):
        result = sync_with_connection(
            self.conn,
            self.config,
            transport=FakeTransport(
                [
                    {
                        "ok": True,
                        "record_type": "sync_response",
                        "rows": [],
                        "next_cursor": EMPTY_CURSOR,
                        "has_more": False,
                    }
                ]
            ),
        )
        self.assertEqual(result.rows_imported, 0)
        self.assertEqual(count(self.conn, "raw_calendar_snapshots"), 0)

    def test_dry_run_does_not_write_sync_state_or_rows(self):
        result = sync_with_connection(
            self.conn,
            self.config,
            dry_run=True,
            transport=FakeTransport(
                [
                    {
                        "ok": True,
                        "record_type": "sync_response",
                        "rows": [row("snap-dry-run")],
                        "next_cursor": "2026-06-22T02:00:00.000Z",
                        "has_more": False,
                    }
                ]
            ),
        )
        self.assertEqual(result.rows_fetched, 1)
        self.assertEqual(result.rows_imported, 0)
        self.assertEqual(count(self.conn, "raw_calendar_snapshots"), 0)
        self.assertIsNone(
            self.conn.execute(
                "SELECT 1 FROM sync_state WHERE source_name = ?",
                (SOURCE_NAME,),
            ).fetchone()
        )

    def test_no_successful_cursor_triggers_full_sync(self):
        transport = FakeTransport(
            [
                {
                    "ok": True,
                    "record_type": "sync_response",
                    "rows": [row("snap-initial")],
                    "next_cursor": {
                        "ingested_at": "2026-06-22T02:00:00.000Z",
                        "snapshot_key": "snap-initial",
                    },
                    "has_more": False,
                }
            ]
        )
        result = sync_calendar_automatically(self.config, transport=transport)
        self.assertEqual(result.mode, "initial_full")
        self.assertEqual(transport.calls[0]["after_ingested_at"], EMPTY_CURSOR)
        self.assertEqual(transport.calls[0]["after_snapshot_key"], "")

    def test_valid_cursor_triggers_incremental_sync(self):
        self.conn.execute(
            """
            INSERT INTO sync_state (source_name, cursor_value, last_success_at)
            VALUES (?, ?, ?)
            """,
            (SOURCE_NAME, "2026-06-22T01:00:00.000Z", "2026-06-22T01:01:00.000Z"),
        )
        self.conn.commit()
        transport = FakeTransport(
            [
                {
                    "ok": True,
                    "record_type": "sync_response",
                    "rows": [],
                    "next_cursor": "2026-06-22T01:00:00.000Z",
                    "has_more": False,
                }
            ]
        )
        result = sync_calendar_automatically(self.config, transport=transport)
        self.assertEqual(result.mode, "incremental")
        self.assertEqual(transport.calls[0]["after_ingested_at"], "2026-06-22T01:00:00.000Z")

    def test_one_page_sync_normalizes_rows(self):
        result = sync_with_connection(
            self.conn,
            self.config,
            transport=FakeTransport(
                [
                    {
                        "ok": True,
                        "record_type": "sync_response",
                        "rows": [
                            row("snap-1", capture_window="next_2_days", event_fingerprint="fp-shared"),
                            row("snap-2", capture_window="past_7_days", event_fingerprint="fp-shared"),
                        ],
                        "next_cursor": "2026-06-22T02:01:00.000Z",
                        "has_more": False,
                    }
                ]
            ),
        )
        self.assertEqual(result.rows_imported, 2)
        self.assertEqual(count(self.conn, "calendar_event_candidates"), 1)
        self.assertEqual(count(self.conn, "sessions"), 1)
        self.assertEqual(count(self.conn, "review_queue"), 1)

    def test_multi_page_sync(self):
        transport = FakeTransport(
            [
                {
                    "ok": True,
                    "record_type": "sync_response",
                    "rows": [row("snap-1", ingested_at="2026-06-22T02:00:00.000Z")],
                    "next_cursor": "2026-06-22T02:00:00.000Z",
                    "has_more": True,
                },
                {
                    "ok": True,
                    "record_type": "sync_response",
                    "rows": [row("snap-2", ingested_at="2026-06-22T02:01:00.000Z")],
                    "next_cursor": "2026-06-22T02:01:00.000Z",
                    "has_more": False,
                },
            ]
        )
        result = sync_with_connection(self.conn, self.config, transport=transport)
        self.assertEqual(result.rows_imported, 2)
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(transport.calls[1]["after_ingested_at"], "2026-06-22T02:00:00.000Z")

    def test_duplicate_snapshot_keys_are_skipped(self):
        response = {
            "ok": True,
            "record_type": "sync_response",
            "rows": [row("snap-1")],
            "next_cursor": "2026-06-22T02:00:00.000Z",
            "has_more": False,
        }
        first = sync_with_connection(
            self.conn, self.config, full=True, transport=FakeTransport([response])
        )
        second = sync_with_connection(
            self.conn, self.config, full=True, transport=FakeTransport([response])
        )
        self.assertEqual(first.rows_imported, 1)
        self.assertEqual(second.rows_imported, 0)
        self.assertEqual(count(self.conn, "raw_calendar_snapshots"), 1)

    def test_invalid_api_response_fails(self):
        with self.assertRaises(SyncError):
            sync_with_connection(
                self.conn,
                self.config,
                transport=FakeTransport([{"ok": True, "rows": []}]),
            )

    def test_network_failure_records_error(self):
        with self.assertRaises(SyncError):
            sync_with_connection(
                self.conn,
                self.config,
                transport=FakeTransport([SyncError("network down")]),
            )
        status = self.conn.execute(
            "SELECT last_error FROM sync_state WHERE source_name = ?",
            (SOURCE_NAME,),
        ).fetchone()
        self.assertIn("network down", status["last_error"])

    def test_cursor_not_advancing_after_failure(self):
        self.conn.execute(
            """
            INSERT INTO sync_state (source_name, cursor_value)
            VALUES (?, ?)
            """,
            (SOURCE_NAME, "2026-06-22T01:00:00.000Z"),
        )
        self.conn.commit()
        with self.assertRaises(SyncError):
            sync_with_connection(
                self.conn,
                self.config,
                transport=FakeTransport([SyncError("boom")]),
            )
        self.assertEqual(get_cursor(self.conn), "2026-06-22T01:00:00.000Z")

    def test_failed_initial_sync_does_not_create_successful_cursor(self):
        with self.assertRaises(SyncError):
            sync_calendar_automatically(
                self.config,
                transport=FakeTransport([SyncError("boom")]),
            )
        self.assertTrue(should_run_initial_full_sync(self.conn))

    def test_successful_full_sync_stores_composite_cursor(self):
        sync_with_connection(
            self.conn,
            self.config,
            full=True,
            transport=FakeTransport(
                [
                    {
                        "ok": True,
                        "record_type": "sync_response",
                        "rows": [
                            row("snap-a", ingested_at="2026-06-22T02:00:00.000Z"),
                            row("snap-b", ingested_at="2026-06-22T02:00:00.000Z"),
                        ],
                        "next_cursor": {
                            "ingested_at": "2026-06-22T02:00:00.000Z",
                            "snapshot_key": "snap-b",
                        },
                        "has_more": False,
                    }
                ]
            ),
        )
        cursor = parse_cursor(self.conn.execute(
            "SELECT cursor_value FROM sync_state WHERE source_name = ?",
            (SOURCE_NAME,),
        ).fetchone()["cursor_value"])
        self.assertEqual(cursor.ingested_at, "2026-06-22T02:00:00.000Z")
        self.assertEqual(cursor.snapshot_key, "snap-b")

    def test_composite_cursor_paginates_equal_timestamps(self):
        transport = FakeTransport(
            [
                {
                    "ok": True,
                    "record_type": "sync_response",
                    "rows": [row("snap-a", ingested_at="2026-06-22T02:00:00.000Z")],
                    "next_cursor": {
                        "ingested_at": "2026-06-22T02:00:00.000Z",
                        "snapshot_key": "snap-a",
                    },
                    "has_more": True,
                },
                {
                    "ok": True,
                    "record_type": "sync_response",
                    "rows": [row("snap-b", ingested_at="2026-06-22T02:00:00.000Z")],
                    "next_cursor": {
                        "ingested_at": "2026-06-22T02:00:00.000Z",
                        "snapshot_key": "snap-b",
                    },
                    "has_more": False,
                },
            ]
        )
        sync_with_connection(self.conn, self.config, transport=transport)
        self.assertEqual(transport.calls[1]["after_ingested_at"], "2026-06-22T02:00:00.000Z")
        self.assertEqual(transport.calls[1]["after_snapshot_key"], "snap-a")
        self.assertEqual(count(self.conn, "raw_calendar_snapshots"), 2)

    def test_legacy_timestamp_only_cursor_remains_compatible(self):
        self.conn.execute(
            """
            INSERT INTO sync_state (source_name, cursor_value, last_success_at)
            VALUES (?, ?, ?)
            """,
            (SOURCE_NAME, "2026-06-22T01:00:00.000Z", "2026-06-22T01:01:00.000Z"),
        )
        self.conn.commit()
        self.assertFalse(should_run_initial_full_sync(self.conn))
        cursor = get_cursor(self.conn)
        self.assertEqual(cursor.ingested_at, "2026-06-22T01:00:00.000Z")
        self.assertEqual(cursor.snapshot_key, "")

    def test_only_completed_runs_response_is_processed(self):
        sync_with_connection(
            self.conn,
            self.config,
            transport=FakeTransport(
                [
                    {
                        "ok": True,
                        "record_type": "sync_response",
                        "rows": [
                            row("snap-1", capture_window="next_2_days", event_fingerprint="fp-shared"),
                            row("snap-2", capture_window="past_7_days", event_fingerprint="fp-shared"),
                        ],
                        "next_cursor": "2026-06-22T02:01:00.000Z",
                        "has_more": False,
                    }
                ]
            ),
        )
        completed = self.conn.execute(
            "SELECT completed_run_count FROM import_runs"
        ).fetchone()["completed_run_count"]
        self.assertEqual(completed, 1)

    def test_reports_written_atomically(self):
        sync_with_connection(
            self.conn,
            self.config,
            transport=FakeTransport(
                [
                    {
                        "ok": True,
                        "record_type": "sync_response",
                        "rows": [row("snap-1")],
                        "next_cursor": "2026-06-22T02:00:00.000Z",
                        "has_more": False,
                    }
                ]
            ),
        )
        self.assertTrue((self.reports_dir / "Jordana_Client_Sessions_2026.csv").exists())
        self.assertTrue((self.reports_dir / "Jordana_Client_Summary_2026.csv").exists())
        self.assertTrue((self.reports_dir / "Jordana_All_Appointments.csv").exists())
        self.assertFalse(list(self.reports_dir.glob("*.tmp")))

    def test_public_sync_status_exposes_only_safe_summary_fields(self):
        sync_with_connection(
            self.conn,
            self.config,
            transport=FakeTransport(
                [
                    {
                        "ok": True,
                        "record_type": "sync_response",
                        "rows": [row("snap-1")],
                        "next_cursor": "2026-06-23T01:06:00.000Z",
                        "has_more": False,
                    }
                ]
            ),
        )
        self.conn.execute(
            """
            UPDATE sync_state
            SET last_attempt_at = ?, last_success_at = ?, last_error = ?, rows_imported = ?
            WHERE source_name = ?
            """,
            (
                "2026-06-23T01:05:00.000Z",
                "2026-06-23T01:06:00.000Z",
                "network down",
                7,
                SOURCE_NAME,
            ),
        )
        self.conn.commit()

        status = public_sync_status(sync_status_for_connection(self.conn))

        self.assertEqual(
            status,
            {
                "current_status": "Attention Needed",
                "last_attempt": "2026-06-23T01:05:00.000Z",
                "last_success": "2026-06-23T01:06:00.000Z",
                "last_mode": "Incremental sync",
                "rows_fetched": 1,
                "new_raw_snapshots_imported": 1,
                "duplicate_snapshots_skipped": 0,
                "review_items_changed": 1,
                "total_rows_imported": 7,
                "raw_snapshot_count": 1,
                "open_review_count": 1,
                "last_error": "Calendar sync needs attention. Please retry from Calendar Import.",
            },
        )


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]


if __name__ == "__main__":
    unittest.main()
