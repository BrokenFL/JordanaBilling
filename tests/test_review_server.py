import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.review_server import MAX_REQUEST_BODY_BYTES, make_handler


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
            self.handler_cls.write_token_header: self.handler_cls.write_token,
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

    def test_sync_run_uses_active_review_server_database_path(self):
        handler, captured = self._handler("/api/sync/run", body=json.dumps({}).encode("utf-8"))

        class Result:
            rows_fetched = 4
            rows_imported = 2
            duplicate_rows_skipped = 1
            review_items_changed = 3
            mode = "incremental"

        with patch("jordana_invoice.review_server.sync_calendar_automatically", return_value=Result()) as sync_run, patch(
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

        self.assertEqual(sync_run.call_args.args[0], {"reports_dir": "Reports"})
        self.assertEqual(captured["payload"]["rows_fetched"], 4)
        self.assertEqual(captured["payload"]["rows_imported"], 2)
        self.assertEqual(captured["payload"]["duplicate_snapshots_skipped"], 1)
        self.assertEqual(captured["payload"]["review_items_changed"], 3)

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
            self.handler_cls.write_token_header: self.handler_cls.write_token,
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
        headers[self.handler_cls.write_token_header] = self.handler_cls.write_token
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


class ReviewServerWriteTokenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "server.sqlite3")
        self.handler_cls = make_handler(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def _handler(self, path, body=b"{}", method="POST", token="auto"):
        handler = object.__new__(self.handler_cls)
        handler.path = path
        handler.command = method
        headers = {
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
        }
        if token == "auto":
            headers[self.handler_cls.write_token_header] = self.handler_cls.write_token
        elif token is not None:
            headers[self.handler_cls.write_token_header] = token
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
    def test_valid_write_token_is_accepted(self, mock_create_person):
        mock_create_person.return_value = {"ok": True, "person_id": "p1"}
        handler, captured = self._handler(
            "/api/people",
            body=json.dumps({"display_name": "Test"}).encode("utf-8"),
        )
        handler.conn = lambda: None
        handler.do_POST()

        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"], {"ok": True, "person_id": "p1"})

    @patch("jordana_invoice.review_server.create_person")
    def test_missing_write_token_returns_403(self, mock_create_person):
        handler, captured = self._handler(
            "/api/people",
            body=json.dumps({"display_name": "Test"}).encode("utf-8"),
            token=None,
        )
        handler.conn = lambda: None
        handler.do_POST()

        self.assertEqual(captured["status"], 403)
        self.assertEqual(captured["payload"], {"ok": False, "error": "Forbidden."})
        mock_create_person.assert_not_called()

    @patch("jordana_invoice.review_server.create_person")
    def test_incorrect_write_token_returns_403(self, mock_create_person):
        handler, captured = self._handler(
            "/api/people",
            body=json.dumps({"display_name": "Test"}).encode("utf-8"),
            token="wrong-token",
        )
        handler.conn = lambda: None
        handler.do_POST()

        self.assertEqual(captured["status"], 403)
        self.assertEqual(captured["payload"], {"ok": False, "error": "Forbidden."})
        mock_create_person.assert_not_called()

    def test_get_requests_do_not_require_write_token(self):
        handler, captured = self._handler("/api/status", body=b"", method="GET", token=None)
        handler.conn = lambda: object()
        with patch("jordana_invoice.review_server.dashboard_status", return_value={"ok": True}):
            handler.do_GET()

        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"], {"ok": True})

    def test_write_token_regenerates_between_handler_launches(self):
        other_handler_cls = make_handler(self.db_path)
        self.assertNotEqual(self.handler_cls.write_token, other_handler_cls.write_token)

    @patch("jordana_invoice.review_server.create_person")
    def test_missing_write_token_blocks_before_service_call_for_patch(self, mock_create_person):
        handler, captured = self._handler(
            "/api/people",
            body=json.dumps({"display_name": "Test"}).encode("utf-8"),
            method="PATCH",
            token=None,
        )
        handler.conn = lambda: None
        handler.do_PATCH()

        self.assertEqual(captured["status"], 403)
        self.assertEqual(captured["payload"], {"ok": False, "error": "Forbidden."})
        mock_create_person.assert_not_called()

    @patch("jordana_invoice.review_server.create_person")
    def test_incorrect_write_token_error_does_not_echo_real_token(self, mock_create_person):
        handler, captured = self._handler(
            "/api/people",
            body=json.dumps({"display_name": "Test"}).encode("utf-8"),
            token="wrong-token",
        )
        handler.conn = lambda: None
        handler.do_POST()

        payload_text = json.dumps(captured["payload"])
        self.assertEqual(captured["status"], 403)
        self.assertNotIn(self.handler_cls.write_token, payload_text)
        mock_create_person.assert_not_called()


class ReviewServerReviewPageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "server.sqlite3")
        self.handler_cls = make_handler(self.db_path, write_token='token"</script>&value')

    def tearDown(self):
        self.temp.cleanup()

    def _handler(self, path="/review"):
        handler = object.__new__(self.handler_cls)
        handler.path = path
        handler.headers = {}
        handler.rfile = io.BytesIO()
        handler.wfile = io.BytesIO()
        handler.finish = lambda: None
        handler.send_error = lambda code: (_ for _ in ()).throw(AssertionError(f"unexpected error {code}"))
        captured = {"headers": {}}

        handler.send_response = lambda status: captured.setdefault("status", status)
        handler.send_header = lambda key, value: captured["headers"].__setitem__(key, value)
        handler.end_headers = lambda: None
        return handler, captured

    def test_review_page_has_no_store_cache_control(self):
        handler, captured = self._handler()
        handler.send_static("review.html")

        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["headers"].get("Cache-Control"), "no-store")

    def test_review_page_bootstrap_serializes_token_safely(self):
        handler, captured = self._handler()
        handler.send_static("review.html")

        html = handler.wfile.getvalue().decode("utf-8")
        self.assertIn('window.__JORDANA_BOOTSTRAP__={"writeToken": "token\\"<\\/script>&value"};', html)
        self.assertNotIn("<!--", html)

    def test_static_review_js_does_not_embed_runtime_token(self):
        api_js = Path("app/jordana_invoice/static/js/api.js").read_text(encoding="utf-8")
        review_js = Path("app/jordana_invoice/static/review.js").read_text(encoding="utf-8")
        self.assertIn("window.__JORDANA_BOOTSTRAP__", api_js)
        self.assertNotIn(self.handler_cls.write_token, api_js)
        self.assertNotIn(self.handler_cls.write_token, review_js)


class ReviewServerRequestBodyLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "server.sqlite3")
        self.handler_cls = make_handler(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def _handler(self, path, body=b"{}", content_type="application/json",
                 content_length="auto"):
        handler = object.__new__(self.handler_cls)
        handler.path = path
        headers = {}
        if content_length == "auto":
            headers["Content-Length"] = str(len(body))
        elif content_length != "omit":
            headers["Content-Length"] = content_length
        if content_type is not None:
            headers["Content-Type"] = content_type
        headers[self.handler_cls.write_token_header] = self.handler_cls.write_token
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

    @staticmethod
    def _body_of_size(size):
        padding = size - 12  # '{"data": "' (10) + '"}' (2)
        return json.dumps({"data": "a" * padding}).encode("utf-8")

    @patch("jordana_invoice.review_server.create_person")
    def test_body_below_limit_accepted(self, mock_create_person):
        mock_create_person.return_value = {"ok": True}
        body = self._body_of_size(MAX_REQUEST_BODY_BYTES - 100)
        handler, captured = self._handler("/api/people", body=body)
        handler.conn = lambda: None
        handler.do_POST()

        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"], {"ok": True})
        mock_create_person.assert_called_once()

    @patch("jordana_invoice.review_server.create_person")
    def test_body_at_limit_accepted(self, mock_create_person):
        mock_create_person.return_value = {"ok": True}
        body = self._body_of_size(MAX_REQUEST_BODY_BYTES)
        handler, captured = self._handler("/api/people", body=body)
        handler.conn = lambda: None
        handler.do_POST()

        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"], {"ok": True})
        mock_create_person.assert_called_once()

    @patch("jordana_invoice.review_server.create_person")
    def test_body_above_limit_rejected_413_without_reading_body(self, mock_create_person):
        body = self._body_of_size(MAX_REQUEST_BODY_BYTES + 100)
        handler, captured = self._handler("/api/people", body=body)
        handler.conn = lambda: None
        handler.do_POST()

        self.assertEqual(captured["status"], 413)
        self.assertEqual(captured["payload"], {"ok": False, "error": "Request body too large."})
        mock_create_person.assert_not_called()
        self.assertEqual(handler.rfile.tell(), 0)

    @patch("jordana_invoice.review_server.create_person")
    def test_missing_content_length_returns_411(self, mock_create_person):
        body = json.dumps({"data": "test"}).encode("utf-8")
        handler, captured = self._handler("/api/people", body=body, content_length="omit")
        handler.conn = lambda: None
        handler.do_POST()

        self.assertEqual(captured["status"], 411)
        self.assertEqual(captured["payload"], {"ok": False, "error": "Content-Length header is required."})
        mock_create_person.assert_not_called()
        self.assertEqual(handler.rfile.tell(), 0)

    @patch("jordana_invoice.review_server.create_person")
    def test_invalid_content_length_returns_400(self, mock_create_person):
        handler, captured = self._handler(
            "/api/people",
            body=json.dumps({"data": "test"}).encode("utf-8"),
            content_length="not-a-number",
        )
        handler.conn = lambda: None
        handler.do_POST()

        self.assertEqual(captured["status"], 400)
        self.assertEqual(captured["payload"], {"ok": False, "error": "Invalid Content-Length header."})
        mock_create_person.assert_not_called()

    @patch("jordana_invoice.review_server.create_person")
    def test_negative_content_length_returns_400(self, mock_create_person):
        handler, captured = self._handler(
            "/api/people",
            body=json.dumps({"data": "test"}).encode("utf-8"),
            content_length="-1",
        )
        handler.conn = lambda: None
        handler.do_POST()

        self.assertEqual(captured["status"], 400)
        self.assertEqual(captured["payload"], {"ok": False, "error": "Invalid Content-Length header."})
        mock_create_person.assert_not_called()

    @patch("jordana_invoice.review_server.create_person")
    def test_short_body_returns_400(self, mock_create_person):
        body = json.dumps({"data": "test"}).encode("utf-8")
        handler, captured = self._handler(
            "/api/people",
            body=body,
            content_length=str(len(body) + 50),
        )
        handler.conn = lambda: None
        handler.do_POST()

        self.assertEqual(captured["status"], 400)
        self.assertEqual(captured["payload"], {"ok": False, "error": "Malformed JSON in request body."})
        mock_create_person.assert_not_called()

    @patch("jordana_invoice.review_server.create_person")
    def test_error_messages_do_not_echo_request_data(self, mock_create_person):
        unique_marker = "UNIQUE_SENSITIVE_DATA_12345"
        body = json.dumps({"data": unique_marker}).encode("utf-8")

        handler, captured = self._handler(
            "/api/people",
            body=body,
            content_length=str(len(body) + MAX_REQUEST_BODY_BYTES),
        )
        handler.conn = lambda: None
        handler.do_POST()

        self.assertEqual(captured["status"], 413)
        self.assertNotIn(unique_marker, json.dumps(captured["payload"]))

        handler2, captured2 = self._handler(
            "/api/people",
            body=body,
            content_length="invalid",
        )
        handler2.conn = lambda: None
        handler2.do_POST()

        self.assertEqual(captured2["status"], 400)
        self.assertNotIn(unique_marker, json.dumps(captured2["payload"]))

class ReviewServerHeaderValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "server.sqlite3")
        self.handler_cls = make_handler(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def _handler(self, command, headers, body=b"{}"):
        handler = object.__new__(self.handler_cls)
        handler.command = command
        handler.path = "/api/status"
        handler.headers = headers
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        
        captured = {}
        def mock_send_json(payload, status=200):
            captured["payload"] = payload
            captured["status"] = status
        handler.send_json = mock_send_json
        handler.finish = lambda: None
        return handler, captured

    def test_valid_host_headers(self):
        valid_hosts = [
            "localhost",
            "localhost:8765",
            "127.0.0.1",
            "127.0.0.1:8765",
            "[::1]",
            "[::1]:8765",
            "LOCALHOST",
            "LocalHost:8765",
            "localhost:1",
            "localhost:65535",
        ]
        for host in valid_hosts:
            with self.subTest(host=host):
                handler, captured = self._handler("GET", {"Host": host})
                result = handler.validate_host_and_origin()
                self.assertTrue(result)
                self.assertNotIn("status", captured)

    def test_missing_or_empty_host_header(self):
        # Missing Host
        handler, captured = self._handler("GET", {})
        result = handler.validate_host_and_origin()
        self.assertFalse(result)
        self.assertEqual(captured.get("status"), 400)
        self.assertEqual(captured.get("payload"), {"ok": False, "error": "Host header is required."})

        # Empty Host
        handler, captured = self._handler("GET", {"Host": ""})
        result = handler.validate_host_and_origin()
        self.assertFalse(result)
        self.assertEqual(captured.get("status"), 400)
        self.assertEqual(captured.get("payload"), {"ok": False, "error": "Invalid Host header."})

    def test_malformed_host_headers(self):
        malformed_hosts = [
            "localhost:invalid",
            "localhost:8765:9000",
            "[::1]8765",
            "[::1]:",
            "localhost:",
            "[::1:8765",
            "localhost:65536",
            "localhost:0",
            "localhost:-8765",
            "[::1]:-8765",
            "[::1]:0",
            "localhost:8765 ",
            " localhost:8765",
            "local host",
            "user@localhost",
            "user:pass@localhost:8765",
            "localhost/path",
            "localhost?query",
            "localhost#fragment",
            "localhost\\path",
        ]
        for host in malformed_hosts:
            with self.subTest(host=host):
                handler, captured = self._handler("GET", {"Host": host})
                result = handler.validate_host_and_origin()
                self.assertFalse(result)
                self.assertEqual(captured.get("status"), 400)
                self.assertEqual(captured.get("payload"), {"ok": False, "error": "Invalid Host header."})

    def test_external_host_headers(self):
        external_hosts = [
            "example.com",
            "google.com",
            "192.168.1.1",
            "localhost.evil.com",
            "127.0.0.1.evil.com",
            "[::1].evil.com",
        ]
        for host in external_hosts:
            with self.subTest(host=host):
                handler, captured = self._handler("GET", {"Host": host})
                result = handler.validate_host_and_origin()
                self.assertFalse(result)
                self.assertEqual(captured.get("status"), 400)
                self.assertEqual(captured.get("payload"), {"ok": False, "error": "Invalid Host header."})

    def test_mutating_requests_with_valid_origins(self):
        valid_origins = [
            "http://localhost",
            "http://localhost:8765",
            "http://127.0.0.1",
            "http://127.0.0.1:8765",
            "http://[::1]",
            "http://[::1]:8765",
            "http://LOCALHOST",
            "http://LocalHost:8765",
            "http://localhost:1",
            "http://localhost:65535",
        ]
        for origin in valid_origins:
            with self.subTest(origin=origin):
                handler, captured = self._handler("POST", {"Host": "localhost", "Origin": origin})
                result = handler.validate_host_and_origin()
                self.assertTrue(result)
                self.assertNotIn("status", captured)

    def test_mutating_requests_with_missing_origin(self):
        # Absent Origin should be allowed
        handler, captured = self._handler("POST", {"Host": "localhost"})
        result = handler.validate_host_and_origin()
        self.assertTrue(result)
        self.assertNotIn("status", captured)

    def test_mutating_requests_with_invalid_or_malformed_origins(self):
        invalid_origins = [
            "https://localhost",  # wrong scheme
            "http://example.com",
            "http://localhost.evil.com",  # suffix attack
            "http://127.0.0.1.evil.com",
            "http://[::1].evil.com",
            "http://localhost:invalid",
            "http://localhost:65536",
            "http://localhost:0",
            "null",
            "http://localhost/",  # trailing slash
            "http://user@localhost",
            "http://user:pass@localhost:8765",
            "http://localhost/path",
            "http://localhost?query",
            "http://localhost#fragment",
            "http://localhost\\path",
            "http://localhost:8765 ",
            "http:// localhost:8765",
        ]
        for origin in invalid_origins:
            with self.subTest(origin=origin):
                handler, captured = self._handler("POST", {"Host": "localhost", "Origin": origin})
                result = handler.validate_host_and_origin()
                self.assertFalse(result)
                self.assertEqual(captured.get("status"), 403)
                self.assertEqual(captured.get("payload"), {"ok": False, "error": "Invalid Origin header."})

    def test_non_mutating_requests_ignore_origin(self):
        # Origin is non-local, but request is GET, so should be ignored/allowed
        handler, captured = self._handler("GET", {"Host": "localhost", "Origin": "http://evil.com"})
        result = handler.validate_host_and_origin()
        self.assertTrue(result)
        self.assertNotIn("status", captured)


class SecurityHeaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "server.sqlite3")
        self.handler_cls = make_handler(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def _handler(self):
        handler = object.__new__(self.handler_cls)
        handler.path = "/review"
        handler.headers = {}
        handler.rfile = io.BytesIO()
        handler.wfile = io.BytesIO()
        handler.finish = lambda: None
        handler.send_error = lambda code: (_ for _ in ()).throw(AssertionError(f"unexpected error {code}"))
        captured = {"headers": {}, "header_counts": {}}
        handler.send_response = lambda status: captured.setdefault("status", status)

        def _send_header(key, value):
            captured["headers"][key] = value
            captured["header_counts"][key] = captured["header_counts"].get(key, 0) + 1

        handler.send_header = _send_header
        handler.end_headers = lambda: None
        return handler, captured

    def _assert_common_security_headers(self, headers):
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertIn("Content-Security-Policy", headers)

    def _assert_headers_appear_once(self, header_counts):
        for key in ("X-Content-Type-Options", "Referrer-Policy",
                     "X-Frame-Options", "Content-Security-Policy"):
            self.assertEqual(header_counts.get(key, 0), 1,
                             f"{key} should appear exactly once, got {header_counts.get(key, 0)}")

    def test_review_page_has_security_headers(self):
        handler, captured = self._handler()
        handler.send_static("review.html")
        self._assert_common_security_headers(captured["headers"])
        self._assert_headers_appear_once(captured["header_counts"])
        self.assertEqual(captured["status"], 200)

    def test_review_page_csp_has_nonce_matching_script_tag(self):
        handler, captured = self._handler()
        handler.send_static("review.html")
        csp = captured["headers"].get("Content-Security-Policy", "")
        self.assertIn("nonce-", csp)
        self.assertNotIn("'unsafe-inline'", csp.split("style-src")[0])

        html = handler.wfile.getvalue().decode("utf-8")
        nonce_start = html.find('nonce="')
        self.assertNotEqual(nonce_start, -1, "bootstrap script must have nonce attribute")
        nonce_end = html.find('"', nonce_start + len('nonce="'))
        nonce = html[nonce_start + len('nonce="'):nonce_end]
        self.assertTrue(nonce, "nonce value must not be empty")
        self.assertIn(f"'nonce-{nonce}'", csp)

    def test_review_page_nonce_differs_per_response(self):
        handler1, captured1 = self._handler()
        handler1.send_static("review.html")
        csp1 = captured1["headers"].get("Content-Security-Policy", "")

        handler2, captured2 = self._handler()
        handler2.send_static("review.html")
        csp2 = captured2["headers"].get("Content-Security-Policy", "")

        nonce1 = csp1.split("'nonce-")[1].split("'")[0]
        nonce2 = csp2.split("'nonce-")[1].split("'")[0]
        self.assertNotEqual(nonce1, nonce2)

    def test_review_page_preserves_no_store_cache_control(self):
        handler, captured = self._handler()
        handler.send_static("review.html")
        self.assertEqual(captured["headers"].get("Cache-Control"), "no-store")

    def test_review_page_csp_preserves_style_src_unsafe_inline(self):
        handler, captured = self._handler()
        handler.send_static("review.html")
        csp = captured["headers"].get("Content-Security-Policy", "")
        self.assertIn("style-src 'self' 'unsafe-inline'", csp)

    def test_review_page_csp_allows_embedded_pdf_preview(self):
        handler, captured = self._handler()
        handler.send_static("review.html")
        csp = captured["headers"].get("Content-Security-Policy", "")
        self.assertIn("frame-src 'self' blob:", csp)
        self.assertIn("object-src 'self' blob:", csp)
        self.assertIn("frame-ancestors 'none'", csp)

    def test_static_js_has_security_headers_no_unsafe_inline(self):
        handler, captured = self._handler()
        handler.send_static("review.js")
        self._assert_common_security_headers(captured["headers"])
        self._assert_headers_appear_once(captured["header_counts"])
        csp = captured["headers"].get("Content-Security-Policy", "")
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("'unsafe-inline'", csp.split("style-src")[0])

    def test_static_css_has_security_headers(self):
        handler, captured = self._handler()
        handler.send_static("review.css")
        self._assert_common_security_headers(captured["headers"])
        self._assert_headers_appear_once(captured["header_counts"])

    def test_json_api_response_has_security_headers(self):
        handler, captured = self._handler()
        handler.send_json({"ok": True})
        self._assert_common_security_headers(captured["headers"])
        self._assert_headers_appear_once(captured["header_counts"])
        self.assertEqual(captured["headers"].get("Content-Type"), "application/json; charset=utf-8")
        csp = captured["headers"].get("Content-Security-Policy", "")
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("'unsafe-inline'", csp.split("style-src")[0])

    def test_json_error_response_has_security_headers(self):
        handler, captured = self._handler()
        handler.send_json({"ok": False, "error": "bad"}, status=400)
        self._assert_common_security_headers(captured["headers"])
        self._assert_headers_appear_once(captured["header_counts"])

    def test_csv_download_response_has_security_headers(self):
        handler, captured = self._handler()
        handler.send_csv("col1,col2\nval1,val2\n", "report_2025.csv")
        self._assert_common_security_headers(captured["headers"])
        self._assert_headers_appear_once(captured["header_counts"])
        self.assertEqual(captured["headers"].get("Content-Type"), "text/csv; charset=utf-8")
        self.assertIn("attachment", captured["headers"].get("Content-Disposition", ""))
        csp = captured["headers"].get("Content-Security-Policy", "")
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("'unsafe-inline'", csp.split("style-src")[0])

    def test_inline_pdf_response_uses_pdf_safe_headers(self):
        handler, captured = self._handler()
        body = b"%PDF-1.4\n%%EOF\n"
        handler.send_pdf(body, 'preview".pdf')

        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["headers"].get("Content-Type"), "application/pdf")
        self.assertEqual(captured["headers"].get("Content-Length"), str(len(body)))
        self.assertEqual(captured["headers"].get("Content-Disposition"), 'inline; filename="preview.pdf"')
        self.assertEqual(captured["headers"].get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(captured["headers"].get("Referrer-Policy"), "no-referrer")
        self.assertNotIn("Content-Security-Policy", captured["headers"])
        self.assertNotIn("X-Frame-Options", captured["headers"])
        self.assertEqual(handler.wfile.getvalue(), body)


if __name__ == "__main__":
    unittest.main()
