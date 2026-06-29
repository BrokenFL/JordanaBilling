import tempfile
import unittest
from pathlib import Path

from jordana_invoice.capture_windows import (
    completed_run_windows,
    is_backfill_capture_window,
    is_future_capture_window,
    is_past_capture_window,
)
from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import count_completed_runs, import_rows


BACKFILL_WINDOW = "backfill_2026_06_01_through_2026_06_14"


def raw_row(
    snapshot_key: str,
    *,
    run_id: str = "run-normal",
    capture_window: str = "past_3_days",
    event_id: str = "apple-event-1",
    fingerprint: str = "fp-1",
    title: str = "Bonnie Smith | 60 | Phone",
    start_at: str = "2026-06-12T17:00:00-04:00",
    end_at: str = "2026-06-12T18:00:00-04:00",
    ingested_at: str = "2026-06-22T02:00:00.000Z",
) -> dict[str, str]:
    return {
        "ingested_at": ingested_at,
        "snapshot_key": snapshot_key,
        "run_id": run_id,
        "batch_name": "test-calendar-capture",
        "capture_window": capture_window,
        "captured_at": "2026-06-22T01:00:00.000Z",
        "window_start": "2026-06-01T00:00:00-04:00",
        "window_end": "2026-06-14T23:59:59-04:00",
        "source_device": "test",
        "timezone": "America/New_York",
        "calendar_event_id": event_id,
        "event_fingerprint": fingerprint,
        "event_title": title,
        "start_at": start_at,
        "end_at": end_at,
        "duration_minutes": "60",
        "location": "",
        "notes": "",
        "calendar": "Jordana Work",
        "payload_version": "2",
        "raw_json": "{}",
    }


class CaptureWindowTests(unittest.TestCase):
    def test_capture_window_classification_supports_new_old_and_backfill_labels(self):
        self.assertTrue(is_past_capture_window("past_3_days"))
        self.assertTrue(is_past_capture_window("past_7_days"))
        self.assertTrue(is_future_capture_window("next_7_days"))
        self.assertTrue(is_future_capture_window("next_2_days"))
        self.assertTrue(is_backfill_capture_window(BACKFILL_WINDOW))
        self.assertFalse(is_future_capture_window(BACKFILL_WINDOW))

    def test_completed_run_windows_accepts_new_normal_old_normal_and_backfill(self):
        self.assertTrue(completed_run_windows({"past_3_days", "next_7_days"}))
        self.assertTrue(completed_run_windows({"past_7_days", "next_2_days"}))
        self.assertTrue(completed_run_windows({BACKFILL_WINDOW}))
        self.assertFalse(completed_run_windows({"past_3_days"}))
        self.assertFalse(completed_run_windows({"next_7_days"}))
        self.assertFalse(completed_run_windows({"legacy"}))

    def test_count_completed_runs_uses_new_labels_and_backfill(self):
        rows = [
            raw_row("normal-past", run_id="run-a", capture_window="past_3_days"),
            raw_row("normal-future", run_id="run-a", capture_window="next_7_days"),
            raw_row("backfill", run_id="run-b", capture_window=BACKFILL_WINDOW),
            raw_row("partial", run_id="run-c", capture_window="past_3_days"),
        ]
        self.assertEqual(count_completed_runs(rows), 2)


class CalendarCaptureImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "calendar.sqlite3")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_normal_and_backfill_overlap_create_one_operational_session(self):
        import_rows(
            self.conn,
            [
                raw_row("snap-normal", run_id="normal", capture_window="past_3_days"),
                raw_row(
                    "snap-backfill",
                    run_id="backfill",
                    capture_window=BACKFILL_WINDOW,
                    ingested_at="2026-06-22T02:01:00.000Z",
                ),
            ],
            "test",
        )

        self.assertEqual(count(self.conn, "raw_calendar_snapshots"), 2)
        self.assertEqual(count(self.conn, "calendar_event_candidates"), 1)
        self.assertEqual(count(self.conn, "sessions"), 1)
        candidate = self.conn.execute("SELECT capture_windows FROM calendar_event_candidates").fetchone()
        self.assertIn("past_3_days", candidate["capture_windows"])
        self.assertIn(BACKFILL_WINDOW, candidate["capture_windows"])

    def test_repeated_backfill_preserves_raw_evidence_without_duplicate_session(self):
        import_rows(
            self.conn,
            [
                raw_row("backfill-1", run_id="backfill-a", capture_window=BACKFILL_WINDOW),
                raw_row(
                    "backfill-2",
                    run_id="backfill-b",
                    capture_window=BACKFILL_WINDOW,
                    ingested_at="2026-06-22T02:02:00.000Z",
                ),
            ],
            "test",
        )

        self.assertEqual(count(self.conn, "raw_calendar_snapshots"), 2)
        self.assertEqual(count(self.conn, "calendar_event_candidates"), 1)
        self.assertEqual(count(self.conn, "sessions"), 1)

    def test_event_id_reconciles_title_changes_but_approved_session_values_survive(self):
        import_rows(
            self.conn,
            [
                raw_row("before-approval", title="Bonnie Smith | 60 | Phone"),
            ],
            "test",
        )
        session = self.conn.execute("SELECT id FROM sessions").fetchone()
        self.conn.execute(
            """
            UPDATE sessions
            SET review_status = 'approved',
                duration_minutes = 90,
                service_mode = 'office',
                time_category = 'evening',
                suggested_rate_cents = 12345
            WHERE id = ?
            """,
            (session["id"],),
        )
        self.conn.commit()

        import_rows(
            self.conn,
            [
                raw_row(
                    "after-approval",
                    title="Bonnie Smith | 30 | FaceTime",
                    fingerprint="fp-changed-title",
                    ingested_at="2026-06-22T02:03:00.000Z",
                ),
            ],
            "test",
        )

        self.assertEqual(count(self.conn, "raw_calendar_snapshots"), 2)
        self.assertEqual(count(self.conn, "calendar_event_candidates"), 1)
        self.assertEqual(count(self.conn, "sessions"), 1)
        updated = self.conn.execute(
            "SELECT review_status, duration_minutes, service_mode, time_category, suggested_rate_cents, raw_calendar_title FROM sessions"
        ).fetchone()
        self.assertEqual(updated["review_status"], "approved")
        self.assertEqual(updated["duration_minutes"], 90)
        self.assertEqual(updated["service_mode"], "office")
        self.assertEqual(updated["time_category"], "evening")
        self.assertEqual(updated["suggested_rate_cents"], 12345)
        self.assertEqual(updated["raw_calendar_title"], "Bonnie Smith | 30 | FaceTime")

    def test_future_event_imports_as_reviewable_unapproved_session(self):
        import_rows(
            self.conn,
            [
                raw_row(
                    "future",
                    run_id="future-run",
                    capture_window="next_7_days",
                    start_at="2026-07-01T17:00:00-04:00",
                    end_at="2026-07-01T18:00:00-04:00",
                )
            ],
            "test",
        )

        session = self.conn.execute(
            "SELECT session_date, review_status, billable_status, payment_status FROM sessions"
        ).fetchone()
        self.assertEqual(session["session_date"], "2026-07-01")
        self.assertNotEqual(session["review_status"], "approved")
        self.assertEqual(session["billable_status"], "proposed")
        self.assertEqual(session["payment_status"], "unpaid")
        self.assertEqual(count(self.conn, "invoices"), 0)


def count(conn, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]


if __name__ == "__main__":
    unittest.main()
