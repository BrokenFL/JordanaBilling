import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from jordana_invoice.db import DatabaseBusyError, connect, migrate_database
from jordana_invoice.importer import import_rows
from jordana_invoice.review_server import make_handler
from jordana_invoice.review_services import create_billing_party, create_person
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


def approval_payload(person_id, party_id):
    return {
        "participants": [{"person_id": person_id, "display_name": "Avery Stone"}],
        "billing_party_id": party_id,
        "approved_duration_minutes": 60,
        "service_mode": "office",
        "time_category": "standard",
        "approved_rate": "150.00",
        "payment_status": "unpaid",
        "billing_treatment": "billable",
    }


class ApprovalStagingTests(unittest.TestCase):
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
            "payment_postal_code": "00000", "zelle_recipient": "demo-zelle@example.test", "invoice_total_label": "TOTAL DUE", "invoice_number_format": "YYYY-NNNN",
        })
        self.handler_cls = make_handler(self.db_path)
        self.candidate_id = self._import_candidate("s1", "Avery Stone | 60 | Office", "2026-05-10T10:00:00-04:00")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _import_candidate(self, key, title, start):
        import_rows(self.conn, [raw_row(key, title, start)], "test")
        return self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]

    def _handler(self, path, body=b"{}"):
        handler = object.__new__(self.handler_cls)
        handler.path = path
        handler.headers = {
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
            self.handler_cls.write_token_header: self.handler_cls.write_token,
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

    def _approve_via_http(self, candidate_id=None, body=None):
        cid = candidate_id or self.candidate_id
        if body is None:
            body = json.dumps(approval_payload(self.person["person_id"], self.party["billing_party_id"])).encode("utf-8")
        handler, captured = self._handler(f"/api/review/candidates/{cid}/approve", body=body)
        handler.do_POST()
        return captured

    # 1. Successful approval calls staging with only the approved session ID
    def test_approval_calls_staging_with_approved_session_id(self):
        with patch("jordana_invoice.review_server.stage_approved_sessions_to_monthly_drafts") as mock_stage:
            mock_stage.return_value = {
                "drafts_created": 1, "drafts_reused": 0, "sessions_staged": 1,
                "sessions_already_staged": 0, "sessions_moved": 0,
                "sessions_removed_ineligible": 0, "sessions_skipped": [], "errors": [],
            }
            captured = self._approve_via_http()
        mock_stage.assert_called_once()
        call = mock_stage.call_args
        passed_session_ids = call.kwargs.get("session_ids")
        self.assertEqual(passed_session_ids, [captured["payload"]["session"]["id"]])

    # 2. Successful staging adds invoice_staging.status = "success"
    def test_successful_staging_status_success(self):
        captured = self._approve_via_http()
        self.assertEqual(captured["status"], 200)
        self.assertIn("invoice_staging", captured["payload"])
        self.assertEqual(captured["payload"]["invoice_staging"]["status"], "success")
        self.assertIn("summary", captured["payload"]["invoice_staging"])
        self.assertEqual(captured["payload"]["invoice_staging"]["summary"]["sessions_staged"], 1)

    # 3. Staging summary errors produce status = "warning" and HTTP 200
    def test_staging_errors_produce_warning(self):
        with patch("jordana_invoice.review_server.stage_approved_sessions_to_monthly_drafts") as mock_stage:
            mock_stage.return_value = {
                "drafts_created": 0, "drafts_reused": 0, "sessions_staged": 0,
                "sessions_already_staged": 0, "sessions_moved": 0,
                "sessions_removed_ineligible": 0, "sessions_skipped": [],
                "errors": [{"billing_party_id": "test", "billing_month": "2026-05", "error": "Session is already included in this draft."}],
            }
            captured = self._approve_via_http()
        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"]["invoice_staging"]["status"], "warning")
        self.assertEqual(captured["payload"]["invoice_staging"]["summary"]["errors"][0]["error"], "Session is already included in this draft.")

    def test_unexpected_inner_staging_error_is_sanitized(self):
        with patch("jordana_invoice.review_server.stage_approved_sessions_to_monthly_drafts") as mock_stage:
            mock_stage.return_value = {
                "drafts_created": 0, "drafts_reused": 0, "sessions_staged": 0,
                "sessions_already_staged": 0, "sessions_moved": 0,
                "sessions_removed_ineligible": 0, "sessions_skipped": [],
                "errors": [{"billing_party_id": "test", "billing_month": "2026-05", "error": "OperationalError: table sessions has no column name /path/to/db"}],
            }
            captured = self._approve_via_http()
        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"]["invoice_staging"]["status"], "warning")
        self.assertEqual(captured["payload"]["invoice_staging"]["summary"]["errors"][0]["error"], "An unexpected error occurred during invoice staging.")


    # 4. Database-busy staging produces status = "unavailable" and HTTP 200
    def test_database_busy_staging_unavailable(self):
        with patch("jordana_invoice.review_server.stage_approved_sessions_to_monthly_drafts",
                    side_effect=DatabaseBusyError("Database is locked")):
            captured = self._approve_via_http()
        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"]["invoice_staging"]["status"], "unavailable")
        self.assertIsNone(captured["payload"]["invoice_staging"]["summary"])

    # 5. Unexpected staging exception produces status = "error" and HTTP 200
    def test_unexpected_staging_exception_error(self):
        with patch("jordana_invoice.review_server.stage_approved_sessions_to_monthly_drafts",
                    side_effect=RuntimeError("Unexpected internal error with /path/to/db.sqlite3")):
            captured = self._approve_via_http()
        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"]["invoice_staging"]["status"], "error")
        self.assertIsNone(captured["payload"]["invoice_staging"]["summary"])

    # 6. Approval validation failure does not call staging
    def test_approval_failure_does_not_call_staging(self):
        with patch("jordana_invoice.review_server.stage_approved_sessions_to_monthly_drafts") as mock_stage:
            body = json.dumps({"participants": [], "billing_party_id": "", "approved_duration_minutes": 0}).encode("utf-8")
            captured = self._approve_via_http(body=body)
        self.assertEqual(captured["status"], 400)
        mock_stage.assert_not_called()

    # 7. Database-busy approval does not call staging
    def test_database_busy_approval_does_not_call_staging(self):
        with patch("jordana_invoice.review_server.approve_candidate",
                    side_effect=DatabaseBusyError("Database is locked")):
            with patch("jordana_invoice.review_server.stage_approved_sessions_to_monthly_drafts") as mock_stage:
                captured = self._approve_via_http()
        self.assertEqual(captured["status"], 503)
        mock_stage.assert_not_called()

    # 8. Existing candidate-detail keys remain at the top level
    def test_existing_candidate_keys_preserved(self):
        captured = self._approve_via_http()
        payload = captured["payload"]
        for key in ("session", "participants", "account", "billing_party", "checklist", "audit"):
            self.assertIn(key, payload, f"Missing existing top-level key: {key}")

    # 9. Frontend-required keys (session, participants) remain unchanged
    def test_session_and_participants_unchanged(self):
        captured = self._approve_via_http()
        payload = captured["payload"]
        self.assertEqual(payload["session"]["review_status"], "approved")
        self.assertIsInstance(payload["participants"], list)
        self.assertEqual(len(payload["participants"]), 1)
        self.assertEqual(payload["participants"][0]["person_id"], self.person["person_id"])

    # 10. Repeating the integrated approval request does not duplicate drafts or lines
    def test_repeated_approval_no_duplicate_drafts_or_lines(self):
        captured1 = self._approve_via_http()
        self.assertEqual(captured1["payload"]["invoice_staging"]["status"], "success")
        self.assertEqual(captured1["payload"]["invoice_staging"]["summary"]["drafts_created"], 1)
        self.assertEqual(captured1["payload"]["invoice_staging"]["summary"]["sessions_staged"], 1)
        captured2 = self._approve_via_http()
        self.assertEqual(captured2["status"], 200)
        self.assertEqual(captured2["payload"]["invoice_staging"]["status"], "success")
        self.assertEqual(captured2["payload"]["invoice_staging"]["summary"]["drafts_created"], 0)
        self.assertEqual(captured2["payload"]["invoice_staging"]["summary"]["sessions_staged"], 0)
        self.assertEqual(captured2["payload"]["invoice_staging"]["summary"]["sessions_already_staged"], 1)
        drafts = self.conn.execute(
            "SELECT * FROM invoices WHERE status = 'draft' AND billing_month IS NOT NULL"
        ).fetchall()
        self.assertEqual(len(drafts), 1)
        lines = self.conn.execute(
            "SELECT COUNT(*) FROM invoice_line_items WHERE invoice_id = ?", (drafts[0]["invoice_id"],)
        ).fetchone()[0]
        self.assertEqual(lines, 1)

    # 11. No private names, titles, exception text, database paths, or SQL in failure responses
    def test_no_private_data_in_failure_responses(self):
        with patch("jordana_invoice.review_server.stage_approved_sessions_to_monthly_drafts",
                    side_effect=RuntimeError("Internal error: SELECT * FROM sessions WHERE name='Avery Stone' /path/to/db.sqlite3")):
            captured = self._approve_via_http()
        staging_field = captured["payload"]["invoice_staging"]
        staging_str = json.dumps(staging_field)
        self.assertNotIn("Avery Stone", staging_str)
        self.assertNotIn("SELECT", staging_str)
        self.assertNotIn("/path/to", staging_str)
        self.assertNotIn("Internal error", staging_str)
        self.assertEqual(staging_field["status"], "error")
        self.assertIsNone(staging_field["summary"])

        with patch("jordana_invoice.review_server.stage_approved_sessions_to_monthly_drafts",
                    side_effect=DatabaseBusyError("Database locked at /secret/path/db.sqlite3")):
            captured2 = self._approve_via_http()
        staging_str2 = json.dumps(captured2["payload"]["invoice_staging"])
        self.assertNotIn("/secret", staging_str2)
        self.assertNotIn("Avery Stone", staging_str2)


if __name__ == "__main__":
    unittest.main()
