import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.review_server import make_handler


class ReviewServerSyncConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "server.sqlite3")
        self.handler_cls = make_handler(self.db_path)

    def tearDown(self):
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
        handler.send_error = lambda code: (_ for _ in ()).throw(AssertionError(f"unexpected error {code}"))
        captured = {}
        handler.send_json = lambda payload, status=200: captured.setdefault("payload", payload)
        handler.finish = lambda: None
        return handler, captured

    def test_sync_status_uses_active_review_server_connection(self):
        shared_conn = object()
        handler, captured = self._handler("/api/sync/status")
        handler.conn = lambda: shared_conn

        with patch("jordana_invoice.review_server.sync_status_for_connection", side_effect=lambda conn: {"conn_id": id(conn)}) as sync_status, patch(
            "jordana_invoice.review_server.public_sync_status",
            side_effect=lambda payload: payload,
        ):
            handler.do_GET()

        sync_status.assert_called_once_with(shared_conn)
        self.assertEqual(captured["payload"]["conn_id"], id(shared_conn))

    def test_sync_run_uses_active_review_server_connection(self):
        shared_conn = object()
        handler, captured = self._handler("/api/sync/run", body=json.dumps({}).encode("utf-8"))
        handler.conn = lambda: shared_conn

        class Result:
            rows_fetched = 4
            rows_imported = 2

        with patch("jordana_invoice.review_server.sync_with_connection", return_value=Result()) as sync_run, patch(
            "jordana_invoice.review_server.review_sync_config",
            return_value={"reports_dir": "Reports"},
        ), patch(
            "jordana_invoice.review_server.sync_status_for_connection",
            return_value={"last_success": "2026-06-23T00:00:00"},
        ), patch(
            "jordana_invoice.review_server.public_sync_status",
            side_effect=lambda payload: payload,
        ):
            handler.do_POST()

        self.assertIs(sync_run.call_args.args[0], shared_conn)
        self.assertEqual(captured["payload"]["rows_fetched"], 4)
        self.assertEqual(captured["payload"]["rows_imported"], 2)

class ReviewServerSanitizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "server.sqlite3")
        self.handler_cls = make_handler(self.db_path)

    def tearDown(self):
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
        handler.send_error = lambda code: (_ for _ in ()).throw(AssertionError(f"unexpected error {code}"))
        captured = {}
        def mock_send_json(payload, status=200):
            captured["payload"] = payload
            captured["status"] = status
        handler.send_json = mock_send_json
        handler.finish = lambda: None
        return handler, captured

    @patch("jordana_invoice.review_server.dashboard_status")
    def test_unexpected_get_exception_is_sanitized(self, mock_dashboard_status):
        mock_dashboard_status.side_effect = RuntimeError("database disk image is malformed")
        handler, captured = self._handler("/api/status")
        handler.conn = lambda: None
        handler.do_GET()
        
        self.assertEqual(captured.get("status"), 500)
        self.assertEqual(captured.get("payload"), {"ok": False, "error": "An unexpected error occurred."})

    @patch("jordana_invoice.review_server.dashboard_status")
    def test_safe_get_validation_error_is_preserved(self, mock_dashboard_status):
        mock_dashboard_status.side_effect = ValueError("Year out of range")
        handler, captured = self._handler("/api/status")
        handler.conn = lambda: None
        handler.do_GET()
        
        self.assertEqual(captured.get("status"), 400)
        self.assertEqual(captured.get("payload"), {"ok": False, "error": "Year out of range"})

    @patch("jordana_invoice.review_server.dashboard_status")
    def test_unknown_get_value_error_is_sanitized(self, mock_dashboard_status):
        mock_dashboard_status.side_effect = ValueError("Internal SQL detail: SELECT * FROM sessions /path/to/db")
        handler, captured = self._handler("/api/status")
        handler.conn = lambda: None
        handler.do_GET()
        
        self.assertEqual(captured.get("status"), 500)
        self.assertEqual(captured.get("payload"), {"ok": False, "error": "An unexpected error occurred."})

    @patch("jordana_invoice.review_server.get_organization_billing_record")
    def test_billing_party_not_found_returns_404(self, mock_get_record):
        from jordana_invoice.review_services import BillingPartyNotFoundError
        mock_get_record.side_effect = BillingPartyNotFoundError("Billing party not found.")
        handler, captured = self._handler("/api/billing-parties/123")
        handler.conn = lambda: None
        handler.do_GET()
        
        self.assertEqual(captured.get("status"), 404)
        self.assertEqual(captured.get("payload"), {"ok": False, "error": "Billing party not found."})

    @patch("jordana_invoice.review_server.get_organization_billing_record")
    def test_billing_party_type_error_returns_400(self, mock_get_record):
        from jordana_invoice.review_services import BillingPartyTypeError
        mock_get_record.side_effect = BillingPartyTypeError("Billing party is not an organization.")
        handler, captured = self._handler("/api/billing-parties/123")
        handler.conn = lambda: None
        handler.do_GET()
        
        self.assertEqual(captured.get("status"), 400)
        self.assertEqual(captured.get("payload"), {"ok": False, "error": "Billing party is not an organization."})

    @patch("jordana_invoice.review_server.create_person")
    def test_unexpected_post_exception_is_sanitized(self, mock_create_person):
        mock_create_person.side_effect = RuntimeError("disk I/O error")
        handler, captured = self._handler("/api/people", body=json.dumps({"name": "Test"}).encode("utf-8"))
        handler.conn = lambda: None
        handler.do_POST()
        
        self.assertEqual(captured.get("status"), 400)
        self.assertEqual(captured.get("payload"), {"ok": False, "error": "An unexpected error occurred."})

    @patch("jordana_invoice.review_server.create_person")
    def test_unknown_post_value_error_is_sanitized(self, mock_create_person):
        mock_create_person.side_effect = ValueError("Internal SQL detail: SELECT * FROM sessions /path/to/db")
        handler, captured = self._handler("/api/people", body=json.dumps({"name": "Test"}).encode("utf-8"))
        handler.conn = lambda: None
        handler.do_POST()
        
        self.assertEqual(captured.get("status"), 400)
        self.assertEqual(captured.get("payload"), {"ok": False, "error": "An unexpected error occurred."})

    @patch("jordana_invoice.review_server.create_person")
    def test_safe_post_validation_error_is_preserved(self, mock_create_person):
        mock_create_person.side_effect = ValueError("Display name is required.")
        handler, captured = self._handler("/api/people", body=json.dumps({"name": ""}).encode("utf-8"))
        handler.conn = lambda: None
        handler.do_POST()
        
        self.assertEqual(captured.get("status"), 400)
        self.assertEqual(captured.get("payload"), {"ok": False, "error": "Display name is required."})

    @patch("jordana_invoice.review_server.deactivate_account")
    def test_post_account_not_found_returns_404(self, mock_deactivate):
        mock_deactivate.side_effect = ValueError("Account not found.")
        handler, captured = self._handler("/api/accounts/123/deactivate", body=json.dumps({}).encode("utf-8"))
        handler.conn = lambda: None
        handler.do_POST()
        
        self.assertEqual(captured.get("status"), 404)
        self.assertEqual(captured.get("payload"), {"ok": False, "error": "Account not found."})

    @patch("jordana_invoice.review_server.create_person")
    def test_database_busy_error_returns_503(self, mock_create_person):
        from jordana_invoice.db import DatabaseBusyError
        mock_create_person.side_effect = DatabaseBusyError("Database is currently locked.")
        handler, captured = self._handler("/api/people", body=json.dumps({"name": "Test"}).encode("utf-8"))
        handler.conn = lambda: None
        handler.do_POST()
        
        self.assertEqual(captured.get("status"), 503)
        self.assertEqual(captured.get("payload"), {"ok": False, "error": "Database is busy, please try again."})


class ReviewServerJsonRequestParsingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "server.sqlite3")
        self.handler_cls = make_handler(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def _handler(self, path, body=b"{}", content_type="application/json"):
        handler = object.__new__(self.handler_cls)
        handler.path = path
        headers = {"Content-Length": str(len(body))}
        if content_type is not None:
            headers["Content-Type"] = content_type
        handler.headers = headers
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.send_error = lambda code: (_ for _ in ()).throw(AssertionError(f"unexpected error {code}"))
        captured = {}

        def mock_send_json(payload, status=200):
            captured["payload"] = payload
            captured["status"] = status

        handler.send_json = mock_send_json
        handler.finish = lambda: None
        return handler, captured

    @patch("jordana_invoice.review_server.create_person")
    def test_valid_json_content_type_is_accepted(self, mock_create_person):
        mock_create_person.return_value = {"ok": True, "person_id": "p1"}
        handler, captured = self._handler(
            "/api/people",
            body=json.dumps({"display_name": "Test"}).encode("utf-8"),
        )
        handler.conn = lambda: None
        handler.do_POST()

        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"], {"ok": True, "person_id": "p1"})
        self.assertEqual(mock_create_person.call_args.args[1]["display_name"], "Test")

    @patch("jordana_invoice.review_server.create_person")
    def test_json_content_type_with_charset_is_accepted(self, mock_create_person):
        mock_create_person.return_value = {"ok": True, "person_id": "p1"}
        handler, captured = self._handler(
            "/api/people",
            body=json.dumps({"display_name": "Test"}).encode("utf-8"),
            content_type="application/json; charset=utf-8",
        )
        handler.conn = lambda: None
        handler.do_POST()

        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"], {"ok": True, "person_id": "p1"})

    @patch("jordana_invoice.review_server.create_person")
    def test_json_content_type_is_case_insensitive(self, mock_create_person):
        mock_create_person.return_value = {"ok": True, "person_id": "p1"}
        handler, captured = self._handler(
            "/api/people",
            body=json.dumps({"display_name": "Test"}).encode("utf-8"),
            content_type="Application/Json",
        )
        handler.conn = lambda: None
        handler.do_POST()

        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"], {"ok": True, "person_id": "p1"})

    @patch("jordana_invoice.review_server.create_person")
    def test_missing_content_type_returns_415(self, mock_create_person):
        handler, captured = self._handler(
            "/api/people",
            body=json.dumps({"display_name": "Test"}).encode("utf-8"),
            content_type=None,
        )
        handler.conn = lambda: None
        handler.do_POST()

        self.assertEqual(captured["status"], 415)
        self.assertEqual(captured["payload"], {"ok": False, "error": "Content-Type must be application/json."})
        mock_create_person.assert_not_called()

    @patch("jordana_invoice.review_server.create_person")
    def test_unsupported_content_type_returns_415(self, mock_create_person):
        handler, captured = self._handler(
            "/api/people",
            body=json.dumps({"display_name": "Test"}).encode("utf-8"),
            content_type="text/plain",
        )
        handler.conn = lambda: None
        handler.do_POST()

        self.assertEqual(captured["status"], 415)
        self.assertEqual(captured["payload"], {"ok": False, "error": "Content-Type must be application/json."})
        mock_create_person.assert_not_called()

    @patch("jordana_invoice.review_server.create_person")
    def test_lookalike_json_media_type_returns_415(self, mock_create_person):
        handler, captured = self._handler(
            "/api/people",
            body=json.dumps({"display_name": "Test"}).encode("utf-8"),
            content_type="application/json-patch+json",
        )
        handler.conn = lambda: None
        handler.do_POST()

        self.assertEqual(captured["status"], 415)
        self.assertEqual(captured["payload"], {"ok": False, "error": "Content-Type must be application/json."})
        mock_create_person.assert_not_called()

    @patch("jordana_invoice.review_server.create_person")
    def test_malformed_json_returns_400(self, mock_create_person):
        handler, captured = self._handler(
            "/api/people",
            body=b"{not valid json",
            content_type="application/json",
        )
        handler.conn = lambda: None
        handler.do_POST()

        self.assertEqual(captured["status"], 400)
        self.assertEqual(captured["payload"], {"ok": False, "error": "Malformed JSON in request body."})
        mock_create_person.assert_not_called()



if __name__ == "__main__":
    unittest.main()
