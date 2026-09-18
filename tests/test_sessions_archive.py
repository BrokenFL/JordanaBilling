import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.review_services import list_sessions_ledger, set_sessions_archive_state


def raw_row(key: str, title: str, start: str) -> dict[str, str]:
    return {
        "ingested_at": "2026-07-11T02:00:00Z",
        "snapshot_key": key,
        "run_id": f"run-{key}",
        "batch_name": "archive-test",
        "capture_window": "past_7_days",
        "captured_at": "2026-07-11T01:30:00Z",
        "source_device": "test",
        "timezone": "America/New_York",
        "calendar_event_id": f"event-{key}",
        "event_fingerprint": f"fp-{key}",
        "event_title": title,
        "start_at": start,
        "end_at": start[:11] + "11:00:00-04:00",
        "duration_minutes": "60",
        "calendar": "Jordana Work",
        "payload_version": "2",
        "raw_json": "{}",
    }


class SessionsArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "archive.sqlite3")
        init_db(self.conn)
        import_rows(self.conn, [
            raw_row("one", "Sample Client 6", "2026-07-10T10:00:00-04:00"),
            raw_row("two", "Check little plant", "2026-07-10T12:00:00-04:00"),
        ], "test")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_archive_hides_selected_rows_without_changing_status_or_raw_evidence(self):
        before = list_sessions_ledger(self.conn, date_range="all", archive_status="all", limit=100)["items"]
        target = before[0]
        candidate_before = dict(self.conn.execute("SELECT * FROM calendar_event_candidates WHERE id = ?", (target["candidate_id"],)).fetchone())
        session_before = self.conn.execute("SELECT * FROM sessions WHERE candidate_id = ?", (target["candidate_id"],)).fetchone()
        raw_count = self.conn.execute("SELECT COUNT(*) FROM raw_calendar_snapshots").fetchone()[0]

        result = set_sessions_archive_state(self.conn, [target["candidate_id"]], archived=True)

        self.assertEqual(result, {"updated": 1})
        active_ids = {row["candidate_id"] for row in list_sessions_ledger(self.conn, date_range="all", limit=100)["items"]}
        archived_ids = {row["candidate_id"] for row in list_sessions_ledger(self.conn, date_range="all", archive_status="archived", limit=100)["items"]}
        self.assertNotIn(target["candidate_id"], active_ids)
        self.assertIn(target["candidate_id"], archived_ids)
        candidate_after = dict(self.conn.execute("SELECT * FROM calendar_event_candidates WHERE id = ?", (target["candidate_id"],)).fetchone())
        self.assertEqual(candidate_after["review_status"], candidate_before["review_status"])
        self.assertEqual(candidate_after["classification"], candidate_before["classification"])
        if session_before:
            session_after = self.conn.execute("SELECT * FROM sessions WHERE candidate_id = ?", (target["candidate_id"],)).fetchone()
            self.assertEqual(session_after["review_status"], session_before["review_status"])
            self.assertEqual(session_after["approved_rate_cents"], session_before["approved_rate_cents"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM raw_calendar_snapshots").fetchone()[0], raw_count)

    def test_restore_returns_row_to_current_sessions_view(self):
        candidate_id = self.conn.execute("SELECT id FROM calendar_event_candidates LIMIT 1").fetchone()[0]
        set_sessions_archive_state(self.conn, [candidate_id], archived=True)
        set_sessions_archive_state(self.conn, [candidate_id], archived=False)

        current_ids = {row["candidate_id"] for row in list_sessions_ledger(self.conn, date_range="all", limit=100)["items"]}
        self.assertIn(candidate_id, current_ids)
        row = self.conn.execute("SELECT sessions_archived_at FROM calendar_event_candidates WHERE id = ?", (candidate_id,)).fetchone()
        self.assertIsNone(row["sessions_archived_at"])

    def test_archive_allows_approved_row_but_does_not_modify_approval(self):
        row = self.conn.execute("SELECT id, candidate_id FROM sessions LIMIT 1").fetchone()
        self.conn.execute("UPDATE sessions SET review_status = 'approved', approved_rate_cents = 35000 WHERE id = ?", (row["id"],))
        self.conn.execute("UPDATE calendar_event_candidates SET review_status = 'approved' WHERE id = ?", (row["candidate_id"],))
        self.conn.commit()

        set_sessions_archive_state(self.conn, [row["candidate_id"]], archived=True)

        session = self.conn.execute("SELECT review_status, approved_rate_cents FROM sessions WHERE id = ?", (row["id"],)).fetchone()
        self.assertEqual(session["review_status"], "approved")
        self.assertEqual(session["approved_rate_cents"], 35000)

    def test_rejects_unknown_or_oversized_selection(self):
        with self.assertRaisesRegex(ValueError, "no longer exist"):
            set_sessions_archive_state(self.conn, ["missing"], archived=True)
        with self.assertRaisesRegex(ValueError, "no more than 250"):
            set_sessions_archive_state(self.conn, [f"id-{i}" for i in range(251)], archived=True)
