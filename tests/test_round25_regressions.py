import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import confirmed_rate_context, import_rows
from jordana_invoice.review_server import make_handler
from jordana_invoice.review_services import archive_person, create_person, search_people


def raw_row(snapshot_key: str, title: str = "Alex and Bailey 6") -> dict[str, str]:
    return {
        "ingested_at": "2026-07-10T02:00:00.000Z",
        "snapshot_key": snapshot_key,
        "run_id": f"run-{snapshot_key}",
        "batch_name": "round25-test",
        "capture_window": "past_7_days",
        "captured_at": "2026-07-10T01:30:00.000Z",
        "source_device": "test",
        "timezone": "America/New_York",
        "calendar_event_id": f"event-{snapshot_key}",
        "event_fingerprint": f"fp-{snapshot_key}",
        "event_title": title,
        "start_at": "2026-07-09T18:00:00-04:00",
        "end_at": "2026-07-09T19:00:00-04:00",
        "duration_minutes": "60",
        "calendar": "Jordana Work",
        "payload_version": "2",
        "raw_json": "{}",
    }


class Round25RegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "round25.sqlite3")
        self.conn = connect(self.db_path)
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_client_search_returns_stable_code_and_excludes_archived_clients(self):
        active = create_person(self.conn, {"display_name": "Alex Example", "person_code": "AEXA-001"})
        archived = create_person(self.conn, {"display_name": "Alex Example Duplicate", "person_code": "AEXA-002"})
        self.conn.execute("UPDATE people SET active = 0, active_status = 'archived' WHERE person_id = ?", (archived["person_id"],))
        self.conn.commit()

        results = search_people(self.conn, "Alex")

        self.assertEqual([row["person_id"] for row in results], [active["person_id"]])
        self.assertEqual(results[0]["person_code"], "AEXA-001")

    def test_archive_client_preserves_row_and_refuses_active_session_reference(self):
        unused = create_person(self.conn, {"display_name": "Unused Duplicate"})
        archived = archive_person(self.conn, unused["person_id"], "duplicate")
        self.assertEqual(archived["active"], 0)
        self.assertEqual(archived["active_status"], "archived")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM people WHERE person_id = ?", (unused["person_id"],)).fetchone()[0], 1)

        import_rows(self.conn, [raw_row("referenced", "Referenced Client 6")], "test")
        referenced = create_person(self.conn, {"display_name": "Referenced Client"})
        session_id = self.conn.execute("SELECT id FROM sessions LIMIT 1").fetchone()[0]
        participant_id = self.conn.execute("SELECT session_participant_id FROM session_participants WHERE session_id = ? LIMIT 1", (session_id,)).fetchone()[0]
        self.conn.execute("UPDATE session_participants SET person_id = ? WHERE session_participant_id = ?", (referenced["person_id"], participant_id))
        self.conn.commit()

        with self.assertRaisesRegex(ValueError, "Reassign or exclude"):
            archive_person(self.conn, referenced["person_id"], "duplicate")
        self.assertEqual(self.conn.execute("SELECT active FROM people WHERE person_id = ?", (referenced["person_id"],)).fetchone()[0], 1)

    def test_partially_resolved_joint_session_does_not_use_solo_rate_scope(self):
        import_rows(self.conn, [raw_row("joint")], "test")
        person = create_person(self.conn, {"display_name": "Alex Example"})
        session = self.conn.execute("SELECT * FROM sessions LIMIT 1").fetchone()
        participants = self.conn.execute(
            "SELECT session_participant_id FROM session_participants WHERE session_id = ? ORDER BY session_participant_id",
            (session["id"],),
        ).fetchall()
        self.assertGreaterEqual(len(participants), 2)
        self.conn.execute(
            "UPDATE session_participants SET person_id = ? WHERE session_participant_id = ?",
            (person["person_id"], participants[0]["session_participant_id"]),
        )
        self.conn.commit()

        context = confirmed_rate_context(self.conn, session)

        self.assertIsNone(context["person_id"])
        self.assertEqual(context["participant_person_ids"], [])

    def test_calendar_reconciliation_is_a_protected_post_endpoint(self):
        handler_cls = make_handler(self.db_path)
        body = b"{}"
        handler = object.__new__(handler_cls)
        handler.path = "/api/review/reconcile-calendar"
        handler.headers = {
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
            handler_cls.write_token_header: handler_cls.write_token,
        }
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler._database_connection = self.conn
        captured = {}
        handler.send_json = lambda payload, status=200: captured.update(payload=payload, status=status)
        handler.finish = lambda: None
        handler.log_message = lambda *args: None

        with patch("jordana_invoice.importer.suppress_pending_events_missing_from_newest_covering_snapshot", return_value=2):
            handler.do_POST()

        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"], {"reconciled": 2})
