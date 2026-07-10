import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows


def raw_row(
    snapshot_key: str,
    *,
    run_id: str,
    capture_window: str,
    captured_at: str,
    window_start: str,
    window_end: str,
    event_id: str,
    title: str,
    start_at: str,
) -> dict[str, str]:
    return {
        "ingested_at": captured_at,
        "snapshot_key": snapshot_key,
        "run_id": run_id,
        "batch_name": "snapshot-reconciliation-test",
        "capture_window": capture_window,
        "captured_at": captured_at,
        "window_start": window_start,
        "window_end": window_end,
        "source_device": "test",
        "timezone": "America/New_York",
        "calendar_event_id": event_id,
        "event_fingerprint": f"fingerprint-{event_id}",
        "event_title": title,
        "start_at": start_at,
        "end_at": start_at.replace("17:00:00", "18:00:00"),
        "duration_minutes": "60",
        "location": "",
        "notes": "",
        "calendar": "Jordana Work",
        "payload_version": "2",
        "raw_json": "{}",
    }


class CalendarSnapshotReconciliationTests(unittest.TestCase):
    old_start = "2026-07-09T17:00:00-04:00"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "calendar.sqlite3")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def import_complete_run(
        self,
        run_id,
        captured_at,
        *,
        future_rows,
        past_window,
        future_window,
        explicit_windows=True,
    ):
        rows = [
            raw_row(
                f"{run_id}-past-anchor",
                run_id=run_id,
                capture_window="past_3_days",
                captured_at=captured_at,
                window_start=past_window[0] if explicit_windows else "",
                window_end=past_window[1] if explicit_windows else "",
                event_id=f"{run_id}-past-anchor",
                title="Past Anchor | 60 | Phone",
                start_at=past_window[0].replace("T00:00:00", "T12:00:00"),
            )
        ]
        rows.extend(
            raw_row(
                snapshot_key,
                run_id=run_id,
                capture_window="next_7_days",
                captured_at=captured_at,
                window_start=future_window[0] if explicit_windows else "",
                window_end=future_window[1] if explicit_windows else "",
                event_id=event_id,
                title=title,
                start_at=start_at,
            )
            for snapshot_key, event_id, title, start_at in future_rows
        )
        import_rows(self.conn, rows, "test")

    def import_old_event(self):
        self.import_complete_run(
            "older",
            "2026-07-03T12:00:00.000Z",
            future_rows=[("older-janet", "janet-old", "Janet Hershaft | 60 | Phone", self.old_start)],
            past_window=("2026-06-30T00:00:00-04:00", "2026-07-03T23:59:59-04:00"),
            future_window=("2026-07-03T00:00:00-04:00", "2026-07-10T23:59:59-04:00"),
        )

    def candidate_and_session(self, title="Janet Hershaft | 60 | Phone"):
        return self.conn.execute(
            """
            SELECT c.id AS candidate_id, c.review_status AS candidate_status,
                   c.reconciliation_status, c.hidden_from_review,
                   s.id AS session_id, s.review_status AS session_status,
                   s.billable_status
            FROM calendar_event_candidates c
            LEFT JOIN sessions s ON s.candidate_id = c.id
            WHERE c.title = ?
            """,
            (title,),
        ).fetchone()

    def test_newest_covering_snapshot_with_event_keeps_pending_candidate_active(self):
        self.import_old_event()
        self.import_complete_run(
            "newer",
            "2026-07-08T12:00:00.000Z",
            future_rows=[("newer-janet", "janet-old", "Janet Hershaft | 60 | Phone", self.old_start)],
            past_window=("2026-07-05T00:00:00-04:00", "2026-07-08T23:59:59-04:00"),
            future_window=("2026-07-08T00:00:00-04:00", "2026-07-15T23:59:59-04:00"),
        )

        row = self.candidate_and_session()
        self.assertNotEqual(row["candidate_status"], "excluded")
        self.assertNotEqual(row["session_status"], "excluded")
        self.assertNotEqual(row["reconciliation_status"], "removed_from_newest_covering_snapshot")

    def test_newest_covering_snapshot_omission_suppresses_pending_candidate(self):
        self.import_old_event()
        self.import_complete_run(
            "newer",
            "2026-07-08T12:00:00.000Z",
            future_rows=[("newer-moved", "janet-new", "Janet Hershaft | 60 | Phone", "2026-07-10T17:00:00-04:00")],
            past_window=("2026-07-05T00:00:00-04:00", "2026-07-08T23:59:59-04:00"),
            future_window=("2026-07-08T00:00:00-04:00", "2026-07-15T23:59:59-04:00"),
        )

        row = self.candidate_and_session()
        self.assertEqual(row["candidate_status"], "excluded")
        self.assertEqual(row["session_status"], "excluded")
        self.assertEqual(row["billable_status"], "excluded")
        self.assertEqual(row["reconciliation_status"], "removed_from_newest_covering_snapshot")
        self.assertEqual(row["hidden_from_review"], 1)

    def test_blank_production_window_bounds_use_canonical_capture_window_coverage(self):
        self.import_complete_run(
            "older",
            "2026-07-03T12:00:00.000Z",
            future_rows=[("older-janet", "janet-old", "Janet Hershaft | 60 | Phone", self.old_start)],
            past_window=("2026-06-30T00:00:00-04:00", "2026-07-03T23:59:59-04:00"),
            future_window=("2026-07-03T00:00:00-04:00", "2026-07-10T23:59:59-04:00"),
            explicit_windows=False,
        )
        self.import_complete_run(
            "newer",
            "2026-07-08T12:00:00.000Z",
            future_rows=[("newer-moved", "janet-new", "Janet Hershaft | 60 | Phone", "2026-07-10T17:00:00-04:00")],
            past_window=("2026-07-05T00:00:00-04:00", "2026-07-08T23:59:59-04:00"),
            future_window=("2026-07-08T00:00:00-04:00", "2026-07-15T23:59:59-04:00"),
            explicit_windows=False,
        )

        row = self.candidate_and_session()
        self.assertEqual(row["candidate_status"], "excluded")
        self.assertEqual(row["session_status"], "excluded")

    def test_no_new_row_sync_runs_pending_snapshot_reconciliation(self):
        with patch("jordana_invoice.importer.suppress_pending_events_missing_from_newest_covering_snapshot") as reconcile:
            import_rows(self.conn, [], "empty-incremental-sync")

        reconcile.assert_called_once_with(self.conn)

    def test_non_covering_newer_snapshot_does_not_suppress_older_event(self):
        old_start = "2026-07-08T17:00:00-04:00"
        self.import_complete_run(
            "older",
            "2026-07-01T12:00:00.000Z",
            future_rows=[("older-janet", "janet-old", "Janet Hershaft | 60 | Phone", old_start)],
            past_window=("2026-06-28T00:00:00-04:00", "2026-07-01T23:59:59-04:00"),
            future_window=("2026-07-01T00:00:00-04:00", "2026-07-08T23:59:59-04:00"),
        )
        self.import_complete_run(
            "newer",
            "2026-07-12T12:00:00.000Z",
            future_rows=[("newer-other", "other", "Other Client | 60 | Phone", "2026-07-13T17:00:00-04:00")],
            past_window=("2026-07-09T00:00:00-04:00", "2026-07-12T23:59:59-04:00"),
            future_window=("2026-07-12T00:00:00-04:00", "2026-07-19T23:59:59-04:00"),
        )

        row = self.candidate_and_session()
        self.assertNotEqual(row["candidate_status"], "excluded")
        self.assertNotEqual(row["session_status"], "excluded")

    def test_failed_or_incomplete_newer_snapshot_does_not_suppress_older_event(self):
        self.import_old_event()
        future_only = raw_row(
            "newer-moved",
            run_id="newer",
            capture_window="next_7_days",
            captured_at="2026-07-08T12:00:00.000Z",
            window_start="2026-07-08T00:00:00-04:00",
            window_end="2026-07-15T23:59:59-04:00",
            event_id="janet-new",
            title="Janet Hershaft | 60 | Phone",
            start_at="2026-07-10T17:00:00-04:00",
        )
        import_rows(self.conn, [future_only], "test")
        self.assertNotEqual(self.candidate_and_session()["candidate_status"], "excluded")

        self.conn.execute(
            "UPDATE import_runs SET status = 'failed' WHERE id = (SELECT import_run_id FROM raw_calendar_snapshots WHERE snapshot_key = ?)",
            ("newer-moved",),
        )
        failed_past = raw_row(
            "newer-past-anchor",
            run_id="newer",
            capture_window="past_3_days",
            captured_at="2026-07-08T12:00:00.000Z",
            window_start="2026-07-05T00:00:00-04:00",
            window_end="2026-07-08T23:59:59-04:00",
            event_id="newer-past-anchor",
            title="Past Anchor | 60 | Phone",
            start_at="2026-07-07T12:00:00-04:00",
        )
        import_rows(self.conn, [failed_past], "test")

        row = self.candidate_and_session()
        self.assertNotEqual(row["candidate_status"], "excluded")
        self.assertNotEqual(row["session_status"], "excluded")

    def test_approved_session_remains_unchanged_when_absent_from_newer_snapshot(self):
        self.import_old_event()
        before = self.candidate_and_session()
        self.conn.execute(
            "UPDATE sessions SET review_status = 'approved', billable_status = 'approved' WHERE id = ?",
            (before["session_id"],),
        )
        self.conn.commit()

        self.import_complete_run(
            "newer",
            "2026-07-08T12:00:00.000Z",
            future_rows=[("newer-moved", "janet-new", "Janet Hershaft | 60 | Phone", "2026-07-10T17:00:00-04:00")],
            past_window=("2026-07-05T00:00:00-04:00", "2026-07-08T23:59:59-04:00"),
            future_window=("2026-07-08T00:00:00-04:00", "2026-07-15T23:59:59-04:00"),
        )

        row = self.candidate_and_session()
        self.assertEqual(row["session_id"], before["session_id"])
        self.assertEqual(row["session_status"], "approved")
        self.assertEqual(row["billable_status"], "approved")

    def test_reconciliation_never_updates_or_deletes_raw_snapshot_rows(self):
        self.import_old_event()
        self.conn.executescript(
            """
            CREATE TRIGGER reject_raw_update BEFORE UPDATE ON raw_calendar_snapshots
            BEGIN SELECT RAISE(FAIL, 'raw snapshot update'); END;
            CREATE TRIGGER reject_raw_delete BEFORE DELETE ON raw_calendar_snapshots
            BEGIN SELECT RAISE(FAIL, 'raw snapshot delete'); END;
            """
        )
        self.import_complete_run(
            "newer",
            "2026-07-08T12:00:00.000Z",
            future_rows=[("newer-moved", "janet-new", "Janet Hershaft | 60 | Phone", "2026-07-10T17:00:00-04:00")],
            past_window=("2026-07-05T00:00:00-04:00", "2026-07-08T23:59:59-04:00"),
            future_window=("2026-07-08T00:00:00-04:00", "2026-07-15T23:59:59-04:00"),
        )

        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM raw_calendar_snapshots").fetchone()[0], 4)
        self.assertEqual(self.candidate_and_session()["candidate_status"], "excluded")

    def test_moved_appointment_suppresses_old_occurrence_and_keeps_new_occurrence_available(self):
        self.import_old_event()
        self.import_complete_run(
            "newer",
            "2026-07-08T12:00:00.000Z",
            future_rows=[("newer-moved", "janet-new", "Janet Hershaft | 60 | Phone", "2026-07-10T17:00:00-04:00")],
            past_window=("2026-07-05T00:00:00-04:00", "2026-07-08T23:59:59-04:00"),
            future_window=("2026-07-08T00:00:00-04:00", "2026-07-15T23:59:59-04:00"),
        )

        rows = self.conn.execute(
            """
            SELECT c.start_at, c.review_status, s.review_status AS session_status
            FROM calendar_event_candidates c
            JOIN sessions s ON s.candidate_id = c.id
            WHERE c.title = 'Janet Hershaft | 60 | Phone'
            ORDER BY c.start_at
            """
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["review_status"], "excluded")
        self.assertEqual(rows[0]["session_status"], "excluded")
        self.assertNotEqual(rows[1]["review_status"], "excluded")
        self.assertNotEqual(rows[1]["session_status"], "excluded")


if __name__ == "__main__":
    unittest.main()
