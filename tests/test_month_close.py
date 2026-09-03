import sqlite3
import unittest
from datetime import date

from jordana_invoice.db import init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.month_close import get_month_close_report


def raw_row(snapshot_key, capture_window="next_2_days", payload_version="3"):
    return {
        "ingested_at": "2026-07-30T12:00:00Z",
        "snapshot_key": snapshot_key,
        "run_id": "run-july",
        "batch_name": "JORDANA_CALENDAR_TEST",
        "capture_window": capture_window,
        "captured_at": "2026-07-30T08:00:00-04:00",
        "source_device": "test-device",
        "timezone": "America/New_York",
        "calendar_event_id": "future-moved-event",
        "event_fingerprint": "future-moved-fingerprint",
        "event_title": "Fictional Client 5",
        "start_at": "2026-07-31T17:00:00-04:00",
        "end_at": "2026-07-31T18:00:00-04:00",
        "duration_minutes": "60",
        "location": "",
        "notes": "",
        "calendar": "Jordana Work",
        "payload_version": payload_version,
        "raw_json": "{}",
    }


class MonthCloseTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_additive_capture_run_migration_is_installed(self):
        table = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'calendar_capture_runs'"
        ).fetchone()
        self.assertIsNotNone(table)

    def complete_closing_sweep(self):
        self.conn.execute(
            """INSERT INTO calendar_capture_runs (
                 run_id, started_at, completed_at, past_found, past_received,
                 future_found, future_received, status, synced_at
               ) VALUES ('close-july', '2026-08-02T08:00:00-04:00',
                 '2026-08-02T08:01:00-04:00', 3, 3, 2, 2, 'complete',
                 '2026-08-02T12:01:00Z')"""
        )

    def test_future_only_snapshot_does_not_create_missing_session_warning(self):
        self.complete_closing_sweep()
        import_rows(self.conn, [raw_row("future-only")], "test")
        report = get_month_close_report(self.conn, "2026-07", today=date(2026, 8, 4))
        raw_check = next(item for item in report["checks"] if item["id"] == "raw_to_session")
        self.assertEqual(raw_check["status"], "passed")
        self.assertEqual(raw_check["count"], 0)

    def test_incomplete_capture_run_blocks_close(self):
        self.conn.execute(
            """INSERT INTO calendar_capture_runs (
                 run_id, started_at, completed_at, past_found, past_received,
                 future_found, future_received, status, synced_at
               ) VALUES ('partial-july', '2026-07-15T08:00:00-04:00',
                 '2026-07-15T08:01:00-04:00', 4, 3, 2, 2, 'partial',
                 '2026-07-15T12:01:00Z')"""
        )
        report = get_month_close_report(self.conn, "2026-07", today=date(2026, 8, 4))
        capture = next(item for item in report["checks"] if item["id"] == "capture_integrity")
        self.assertEqual(report["status"], "action_needed")
        self.assertEqual(capture["status"], "action_needed")
        self.assertEqual(capture["count"], 1)

    def test_legacy_future_only_candidate_does_not_create_review_warning(self):
        self.complete_closing_sweep()
        import_rows(self.conn, [raw_row("legacy-future", payload_version="2")], "test")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 1)
        report = get_month_close_report(self.conn, "2026-07", today=date(2026, 8, 4))
        review = next(item for item in report["checks"] if item["id"] == "review")
        self.assertEqual(review["status"], "passed")

    def test_clean_post_month_sweep_is_ready_and_unpaid_is_not_a_blocker(self):
        self.complete_closing_sweep()
        report = get_month_close_report(self.conn, "2026-07", today=date(2026, 8, 4))
        self.assertEqual(report["status"], "ready")
        payments = next(item for item in report["checks"] if item["id"] == "payments")
        self.assertEqual(payments["status"], "informational")

    def test_later_complete_run_clears_recovered_partial_warning(self):
        self.conn.execute(
            """INSERT INTO calendar_capture_runs (
                 run_id, started_at, completed_at, past_found, past_received,
                 future_found, future_received, status, synced_at
               ) VALUES ('partial', '2026-07-15T08:00:00-04:00',
                 '2026-07-15T08:01:00-04:00', 4, 3, 2, 2, 'partial',
                 '2026-07-15T12:01:00Z')"""
        )
        self.conn.execute(
            """INSERT INTO calendar_capture_runs (
                 run_id, started_at, completed_at, past_found, past_received,
                 future_found, future_received, status, synced_at
               ) VALUES ('recovery', '2026-07-16T08:00:00-04:00',
                 '2026-07-16T08:01:00-04:00', 4, 4, 2, 2, 'complete',
                 '2026-07-16T12:01:00Z')"""
        )
        self.complete_closing_sweep()
        report = get_month_close_report(self.conn, "2026-07", today=date(2026, 8, 4))
        capture = next(item for item in report["checks"] if item["id"] == "capture_integrity")
        self.assertEqual(capture["status"], "passed")


if __name__ == "__main__":
    unittest.main()
