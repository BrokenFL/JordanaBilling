import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.review_server import make_handler
from jordana_invoice.review_services import dashboard_status, list_review_candidates


def raw_row(snapshot_key, title="Bobsey and Fred 6", start="2026-06-17T18:00:00-04:00"):
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
        "start_at": start,
        "end_at": "2026-06-17T19:00:00-04:00",
        "duration_minutes": "60",
        "calendar": "Jordana Calendar",
        "payload_version": "2",
        "raw_json": "{}",
    }


TABLES = [
    "raw_calendar_snapshots",
    "calendar_event_candidates",
    "sessions",
    "session_participants",
    "review_items",
    "people",
    "person_aliases",
    "client_accounts",
    "billing_parties",
    "rate_rules",
    "audit_log",
    "sync_state",
    "app_metadata",
]


def snapshot_db(conn: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
    result = {}
    for table in TABLES:
        try:
            result[table] = conn.execute(f"SELECT * FROM {table}").fetchall()
        except sqlite3.OperationalError:
            result[table] = []
    return result


def assert_db_unchanged(testcase, before, after):
    for table in TABLES:
        testcase.assertEqual(
            len(before[table]),
            len(after[table]),
            f"Row count changed for table {table}",
        )
        for i, (b, a) in enumerate(zip(before[table], after[table])):
            testcase.assertEqual(
                dict(b),
                dict(a),
                f"Row {i} of table {table} changed",
            )


class GetNoMutationHTTPTests(unittest.TestCase):
    """Exercise the actual HTTP GET endpoints and prove the DB is unchanged."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "review.sqlite3")
        self.conn = connect(self.db_path)
        init_db(self.conn)
        import_rows(self.conn, [raw_row("snap-1")], "test")
        self.handler_cls = make_handler(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _do_get(self, path):
        handler = object.__new__(self.handler_cls)
        handler.path = path
        handler.headers = {"Content-Length": "0"}
        handler.rfile = io.BytesIO(b"")
        handler.wfile = io.BytesIO()
        handler.send_error = lambda code: (_ for _ in ()).throw(
            AssertionError(f"unexpected error {code}")
        )
        captured = {}
        handler.send_json = lambda payload, status=200: captured.setdefault("payload", payload)
        handler.finish = lambda: None
        handler.do_GET()
        return captured.get("payload")

    def test_get_api_status_does_not_mutate_db(self):
        before = snapshot_db(self.conn)
        payload = self._do_get("/api/status")
        self.assertIsInstance(payload, dict)
        self.assertIn("needs_review", payload)
        self.assertIn("ready_to_approve", payload)
        self.assertIn("personal_admin", payload)
        after = snapshot_db(self.conn)
        assert_db_unchanged(self, before, after)

    def test_get_api_review_candidates_does_not_mutate_db(self):
        before = snapshot_db(self.conn)
        payload = self._do_get("/api/review/candidates")
        self.assertIsInstance(payload, dict)
        self.assertIn("items", payload)
        after = snapshot_db(self.conn)
        assert_db_unchanged(self, before, after)

    def test_get_api_status_does_not_mutate_with_unbackfilled_data(self):
        self.conn.execute(
            "UPDATE calendar_event_candidates SET confidence_label = NULL, "
            "unresolved_fields = NULL, service_mode = NULL WHERE id = "
            "(SELECT candidate_id FROM sessions LIMIT 1)"
        )
        self.conn.commit()
        before = snapshot_db(self.conn)
        self._do_get("/api/status")
        after = snapshot_db(self.conn)
        assert_db_unchanged(self, before, after)

    def test_get_api_review_candidates_does_not_mutate_with_unbackfilled_data(self):
        self.conn.execute(
            "UPDATE calendar_event_candidates SET confidence_label = NULL, "
            "unresolved_fields = NULL, service_mode = NULL WHERE id = "
            "(SELECT candidate_id FROM sessions LIMIT 1)"
        )
        self.conn.execute(
            "UPDATE sessions SET account_id = NULL, billing_party_id = NULL "
            "WHERE id = (SELECT id FROM sessions LIMIT 1)"
        )
        self.conn.commit()
        before = snapshot_db(self.conn)
        self._do_get("/api/review/candidates")
        after = snapshot_db(self.conn)
        assert_db_unchanged(self, before, after)


class GetNoMutationServiceTests(unittest.TestCase):
    """Service-level tests confirming the underlying functions are read-only."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "review.sqlite3")
        init_db(self.conn)
        import_rows(self.conn, [raw_row("snap-1")], "test")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_dashboard_status_does_not_mutate_db(self):
        before = snapshot_db(self.conn)
        result = dashboard_status(self.conn)
        self.assertIsInstance(result, dict)
        self.assertIn("needs_review", result)
        after = snapshot_db(self.conn)
        assert_db_unchanged(self, before, after)

    def test_list_review_candidates_does_not_mutate_db(self):
        before = snapshot_db(self.conn)
        result = list_review_candidates(self.conn)
        self.assertIsInstance(result, dict)
        self.assertIn("items", result)
        after = snapshot_db(self.conn)
        assert_db_unchanged(self, before, after)


if __name__ == "__main__":
    unittest.main()
