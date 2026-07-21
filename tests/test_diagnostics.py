import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, migrate_database
from jordana_invoice.diagnostics import create_issue_report, record_event, record_exception
import jordana_invoice.diagnostics as diagnostics_module
from jordana_invoice.review_server import make_handler
from jordana_invoice.review_services import create_billing_party, create_person


class DiagnosticsReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "server.sqlite3"
        migrate_database(self.db_path)
        self.conn = connect(self.db_path)
        self.person = create_person(self.conn, {
            "first_name": "Avery",
            "last_name": "Stone",
            "display_name": "Avery Stone",
        })
        create_billing_party(self.conn, {
            "billing_name": "Avery Stone",
            "person_id": self.person["person_id"],
            "billing_email": "avery@example.test",
            "preferred_delivery_method": "email",
        })
        self.reports_dir = self.root / "diagnostics"

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_report_saves_sanitized_local_json(self):
        record_event(
            "review",
            "test_warning",
            severity="warning",
            path="/api/review/candidates/01234567-89ab-cdef-0123-456789abcdef/save",
            status=400,
            message="Avery Stone validation issue",
        )
        with patch.dict(os.environ, {"JORDANA_DIAGNOSTICS_DIR": str(self.reports_dir)}):
            result = create_issue_report(
                self.conn,
                area="review",
                description="Avery Stone called from 555-111-2222 about Z63.0",
                ui_state={
                    "current_screen": "Session Review",
                    "review_filters": {"status": "", "search_active": True},
                    "selected_candidate_present": True,
                    "raw_name": "Avery Stone",
                },
                frontend_events=[{
                    "timestamp": "2026-07-08T12:00:00Z",
                    "area": "review",
                    "event": "api_response",
                    "severity": "error",
                    "route": "/api/review/candidates/01234567-89ab-cdef-0123-456789abcdef",
                    "status": 400,
                    "message": "Avery Stone failed",
                }],
            )

        self.assertTrue(result["ok"])
        saved = self.reports_dir / result["filename"]
        self.assertTrue(saved.exists())
        text = saved.read_text(encoding="utf-8")
        self.assertNotIn("Avery", text)
        self.assertNotIn("Stone", text)
        self.assertNotIn("555-111-2222", text)
        self.assertNotIn("Z63.0", text)
        self.assertNotIn("raw_name", text)
        payload = json.loads(text)
        self.assertEqual(payload["selected_area"], "review")
        self.assertEqual(payload["build"]["application"], "Jordana Billing")
        self.assertRegex(payload["build"]["commit_hash"], r"^[0-9a-f]{40}$|source-checkout|unavailable")
        self.assertEqual(payload["schema"]["migration_head"], "021_cancellation_policy")
        self.assertIn("candidate_review_status_counts", payload["database_activity"])
        self.assertEqual(payload["system_health"]["database"]["quick_check"], "ok")
        self.assertIn("python", payload["system_health"]["runtime"])
        self.assertIn("calendar_sync", payload["system_health"])
        self.assertIn("/api/review/candidates/{id}", text)

    def test_exception_report_has_safe_failure_signature_without_message_or_path(self):
        try:
            raise RuntimeError("Avery Stone private failure")
        except RuntimeError as error:
            record_exception(error, method="POST", path="/api/review/candidates/01234567-89ab-cdef-0123-456789abcdef/save")
        with patch.dict(os.environ, {"JORDANA_DIAGNOSTICS_DIR": str(self.reports_dir)}):
            result = create_issue_report(self.conn, area="review")
        text = result["report_text"]
        self.assertIn('"exception_type": "RuntimeError"', text)
        self.assertIn('"failure_signature"', text)
        self.assertNotIn("private failure", text)
        self.assertNotIn(str(Path(__file__).parent), text)

    def test_sanitized_error_history_survives_memory_reset_without_private_message(self):
        with patch.dict(os.environ, {"JORDANA_DIAGNOSTICS_DIR": str(self.reports_dir)}):
            record_event(
                "review",
                "http_response",
                severity="error",
                method="POST",
                path="/api/review/candidates/01234567-89ab-cdef-0123-456789abcdef/save",
                status=500,
                message="Avery Stone private database detail",
            )
            persisted = self.reports_dir / "sanitized-runtime-errors.jsonl"
            self.assertTrue(persisted.exists())
            persisted_text = persisted.read_text(encoding="utf-8")
            self.assertNotIn("Avery", persisted_text)
            self.assertNotIn("private database detail", persisted_text)
            self.assertIn('"route": "/api/review/candidates/{id}/save"', persisted_text)
            self.assertEqual(persisted.stat().st_mode & 0o777, 0o600)
            diagnostics_module._RECENT_EVENTS.clear()
            result = create_issue_report(self.conn, area="review")

        self.assertTrue(any(event.get("status") == 500 for event in result["report"]["recent_errors"]))

    def test_report_issue_endpoint_requires_token_and_returns_report_text(self):
        handler_cls = make_handler(str(self.db_path))
        body = json.dumps({
            "area": "payments",
            "description": "Payment screen issue",
            "ui_state": {"current_screen": "Payments", "selected_payment_present": True},
            "frontend_events": [],
        }).encode("utf-8")
        handler = object.__new__(handler_cls)
        handler.path = "/api/diagnostics/report-issue"
        handler.headers = {
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
            handler_cls.write_token_header: handler_cls.write_token,
        }
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler._database_connection = self.conn
        captured = {}
        handler.send_json = lambda payload, status=200: captured.update({
            "payload": payload,
            "status": status,
        })
        handler.send_error = lambda code: captured.update({"payload": None, "status": code})
        handler.finish = lambda: None
        handler.log_message = lambda *a: None

        with patch.dict(os.environ, {"JORDANA_DIAGNOSTICS_DIR": str(self.reports_dir)}):
            handler.do_POST()

        self.assertEqual(captured["status"], 200)
        self.assertTrue(captured["payload"]["ok"])
        self.assertIn('"selected_area": "payments"', captured["payload"]["report_text"])
        self.assertTrue((self.reports_dir / captured["payload"]["filename"]).exists())


class DiagnosticsUiStaticTests(unittest.TestCase):
    def setUp(self):
        self.html = Path("app/jordana_invoice/static/review.html").read_text(encoding="utf-8")
        self.js = Path("app/jordana_invoice/static/review.js").read_text(encoding="utf-8")
        self.css = Path("app/jordana_invoice/static/review.css").read_text(encoding="utf-8")
        self.api_js = Path("app/jordana_invoice/static/js/api.js").read_text(encoding="utf-8")

    def test_sidebar_and_modal_exist(self):
        self.assertIn('id="reportIssueBtn"', self.html)
        self.assertIn('id="reportIssueOverlay"', self.html)
        self.assertIn('id="reportIssueArea"', self.html)
        self.assertIn('id="reportIssueDescription"', self.html)
        self.assertIn('id="copyIssueReportBtn"', self.html)
        self.assertIn('id="exportIssueReportBtn"', self.html)
        self.assertIn('id="downloadSupportDiagnosticsBtn"', self.html)
        self.assertIn('id="calendarFreshnessWarning"', self.html)

    def test_report_payload_uses_sanitized_state_and_events(self):
        self.assertIn('api("/api/diagnostics/report-issue"', self.js)
        self.assertIn("collectDiagnosticUiState()", self.js)
        self.assertIn("frontend_events: state.diagnostics.events.slice(-80)", self.js)
        self.assertIn("selected_candidate_present", self.js)
        self.assertNotIn("raw_calendar_title: state", self.js)
        self.assertIn("downloadDiagnosticReport(result)", self.js)
        self.assertIn("calendar_sync_warning", self.js)

    def test_api_records_diagnostic_events_without_console_logging(self):
        self.assertIn('window.dispatchEvent(new CustomEvent("jordana:api-diagnostic"', self.api_js)
        self.assertIn("diagnosticRouteTemplate(path)", self.api_js)
        self.assertNotIn("console.log", self.api_js)
        self.assertNotIn("console.error", self.api_js)
        self.assertNotIn("console.warn", self.api_js)

    def test_report_issue_css_exists(self):
        self.assertIn(".report-issue-modal", self.css)
        self.assertIn(".report-issue-actions", self.css)
