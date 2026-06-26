import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import DatabaseBusyError, connect, migrate_database
from jordana_invoice.importer import import_rows
from jordana_invoice.review_server import make_handler
from jordana_invoice.review_services import approve_candidate, create_billing_party, create_person
from jordana_invoice.invoice_services import save_business_profile
from jordana_invoice.util import stable_hash


def raw_row(key, title, start):
    return {
        "ingested_at": "2026-08-20T12:00:00Z", "snapshot_key": key, "run_id": f"run-{key}",
        "batch_name": "invoice-demo", "capture_window": "past_7_days", "captured_at": "2026-08-20T12:00:00Z",
        "source_device": "test", "timezone": "America/New_York", "calendar_event_id": f"event-{key}",
        "event_fingerprint": f"fp-{key}", "event_title": title, "start_at": start,
        "end_at": start[:11] + "11:00:00-04:00", "duration_minutes": "60", "calendar": "Jordana Work",
        "payload_version": "2", "raw_json": "{}",
    }


class StagingApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = str(self.root / "server.sqlite3")
        migrate_database(self.db_path)
        self.conn = connect(self.db_path)
        self.person = create_person(self.conn, {"first_name": "Avery", "last_name": "Stone", "display_name": "Avery Stone"})
        self.party = create_billing_party(self.conn, {
            "billing_name": "Avery Stone", "person_id": self.person["person_id"],
            "billing_email": "avery@example.test", "billing_address_line_1": "10 Sample Street",
            "billing_city": "Example", "billing_state": "FL", "billing_postal_code": "00000",
            "preferred_delivery_method": "both",
        })
        save_business_profile(self.conn, {
            "business_name": "Demo Practice", "provider_display_name": "Demo Provider",
            "address_line_1": "100 Example Avenue", "city": "Example", "state": "FL", "postal_code": "00000",
            "phone": "555-0100", "email": "billing@example.test", "payee_name": "Demo Payee",
            "payment_address_line_1": "100 Example Avenue", "payment_city": "Example", "payment_state": "FL",
            "payment_postal_code": "00000", "invoice_total_label": "TOTAL DUE", "invoice_number_format": "YYYY-NNNN",
        })
        self.handler_cls = make_handler(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _handler(self, path, body=b"{}"):
        handler = object.__new__(self.handler_cls)
        handler.path = path
        handler.headers = {
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
        }
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler._database_connection = self.conn
        captured = {}
        handler.send_json = lambda payload, status=200: captured.update({"payload": payload, "status": status})
        handler.send_error = lambda code: captured.update({"payload": None, "status": code})
        handler.finish = lambda: None
        handler.log_message = lambda *a: None
        return handler, captured

    def approved_session(self, key="one", day=15):
        import_rows(self.conn, [raw_row(key, "Avery Stone | 60 | Office", f"2026-05-{day:02d}T10:00:00-04:00")], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Avery Stone"}],
            "billing_party_id": self.party["billing_party_id"], "approved_duration_minutes": 60,
            "service_mode": "office", "time_category": "standard", "approved_rate": "150.00",
            "payment_status": "unpaid", "billing_treatment": "billable",
        })
        return detail["session"]["id"]

    # 1. POST with no body stages all eligible sessions
    def test_no_body_stages_all(self):
        sid = self.approved_session("s1", day=10)
        handler, captured = self._handler("/api/invoices/stage", body=b"{}")
        handler.do_POST()
        self.assertEqual(captured["status"], 200)
        payload = captured["payload"]
        self.assertEqual(payload["drafts_created"], 1)
        self.assertEqual(payload["sessions_staged"], 1)

    # 2. POST with selected session_ids stages only those sessions
    def test_selected_session_ids_stages_only_those(self):
        sid1 = self.approved_session("s1", day=10)
        sid2 = self.approved_session("s2", day=20)
        handler, captured = self._handler(
            "/api/invoices/stage",
            body=json.dumps({"session_ids": [sid1]}).encode("utf-8"),
        )
        handler.do_POST()
        self.assertEqual(captured["status"], 200)
        payload = captured["payload"]
        self.assertEqual(payload["sessions_staged"], 1)
        # Second session should not be staged
        drafts = self.conn.execute("SELECT * FROM invoices WHERE status = 'draft' AND billing_month IS NOT NULL").fetchall()
        self.assertEqual(len(drafts), 1)
        lines = self.conn.execute("SELECT COUNT(*) FROM invoice_line_items WHERE invoice_id = ?", (drafts[0]["invoice_id"],)).fetchone()[0]
        self.assertEqual(lines, 1)

    # 3. Empty session_ids returns zero changes
    def test_empty_session_ids_zero_changes(self):
        self.approved_session("s1", day=10)
        handler, captured = self._handler(
            "/api/invoices/stage",
            body=json.dumps({"session_ids": []}).encode("utf-8"),
        )
        handler.do_POST()
        self.assertEqual(captured["status"], 200)
        payload = captured["payload"]
        self.assertEqual(payload["drafts_created"], 0)
        self.assertEqual(payload["sessions_staged"], 0)

    # 4. Repeating the request creates no duplicate drafts or lines
    def test_repeat_no_duplicates(self):
        self.approved_session("s1", day=10)
        self.approved_session("s2", day=20)
        handler1, captured1 = self._handler("/api/invoices/stage", body=b"{}")
        handler1.do_POST()
        self.assertEqual(captured1["payload"]["drafts_created"], 1)
        self.assertEqual(captured1["payload"]["sessions_staged"], 2)
        handler2, captured2 = self._handler("/api/invoices/stage", body=b"{}")
        handler2.do_POST()
        self.assertEqual(captured2["payload"]["drafts_created"], 0)
        self.assertEqual(captured2["payload"]["drafts_reused"], 1)
        self.assertEqual(captured2["payload"]["sessions_staged"], 0)
        self.assertEqual(captured2["payload"]["sessions_already_staged"], 2)
        drafts = self.conn.execute("SELECT * FROM invoices WHERE status = 'draft' AND billing_month IS NOT NULL").fetchall()
        self.assertEqual(len(drafts), 1)

    # 5. Malformed JSON returns 400
    def test_malformed_json_returns_400(self):
        handler, captured = self._handler("/api/invoices/stage", body=b"{not valid json")
        handler.do_POST()
        self.assertEqual(captured["status"], 400)
        self.assertIn("error", captured["payload"])

    # 6. Non-list session_ids returns 400
    def test_non_list_session_ids_returns_400(self):
        handler, captured = self._handler(
            "/api/invoices/stage",
            body=json.dumps({"session_ids": "not-a-list"}).encode("utf-8"),
        )
        handler.do_POST()
        self.assertEqual(captured["status"], 400)
        self.assertIn("error", captured["payload"])

    # 7. Blank or non-string session IDs return 400
    def test_blank_session_id_returns_400(self):
        handler, captured = self._handler(
            "/api/invoices/stage",
            body=json.dumps({"session_ids": [""]}).encode("utf-8"),
        )
        handler.do_POST()
        self.assertEqual(captured["status"], 400)

    def test_non_string_session_id_returns_400(self):
        handler, captured = self._handler(
            "/api/invoices/stage",
            body=json.dumps({"session_ids": [123]}).encode("utf-8"),
        )
        handler.do_POST()
        self.assertEqual(captured["status"], 400)

    # 8. Unknown request fields are handled according to existing API convention (ignored)
    def test_unknown_fields_ignored(self):
        sid = self.approved_session("s1", day=10)
        handler, captured = self._handler(
            "/api/invoices/stage",
            body=json.dumps({"session_ids": [sid], "unknown_field": "ignored"}).encode("utf-8"),
        )
        handler.do_POST()
        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"]["sessions_staged"], 1)

    # 9. Structured summary is returned unchanged
    def test_structured_summary_returned(self):
        self.approved_session("s1", day=10)
        handler, captured = self._handler("/api/invoices/stage", body=b"{}")
        handler.do_POST()
        payload = captured["payload"]
        expected_keys = {
            "drafts_created", "drafts_reused", "sessions_staged",
            "sessions_already_staged", "sessions_moved",
            "sessions_removed_ineligible", "sessions_skipped", "errors",
        }
        self.assertTrue(expected_keys <= set(payload.keys()))

    # 10. Database busy error returns 503
    def test_database_busy_returns_503(self):
        self.approved_session("s1", day=10)
        handler, captured = self._handler("/api/invoices/stage", body=b"{}")
        with patch(
            "jordana_invoice.review_server.stage_approved_sessions_to_monthly_drafts",
            side_effect=DatabaseBusyError("Database is locked"),
        ):
            handler.do_POST()
        self.assertEqual(captured["status"], 503)
        self.assertIn("error", captured["payload"])

    # 11. No private names or calendar titles appear in the response
    def test_no_private_names_in_response(self):
        self.approved_session("s1", day=10)
        handler, captured = self._handler("/api/invoices/stage", body=b"{}")
        handler.do_POST()
        payload_str = json.dumps(captured["payload"])
        # Private names should not appear
        self.assertNotIn("Avery Stone", payload_str)
        self.assertNotIn("avery@example.test", payload_str)
        self.assertNotIn("Jordana Work", payload_str)
        # Session IDs (internal UUIDs) may appear in sessions_skipped but not private names

    # 12. GET on the same route is rejected or not found according to existing routing
    def test_get_on_stage_route_not_staging(self):
        handler, captured = self._handler("/api/invoices/stage")
        handler.do_GET()
        # GET /api/invoices/stage matches the existing /api/invoices/<id> pattern
        # and returns an error (500 for "Invoice was not found") — not a staging result
        self.assertNotEqual(captured["status"], 200)
        # Verify it's not a staging summary
        if captured["payload"]:
            self.assertNotIn("drafts_created", captured["payload"])


if __name__ == "__main__":
    unittest.main()
