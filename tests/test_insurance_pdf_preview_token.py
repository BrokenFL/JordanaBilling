"""Focused tests for the insurance-coded draft PDF preview endpoint.

Verifies that:
1. The POST to /api/invoices/<id>/draft-pdf requires a valid write token.
2. With a valid token, insurance coding payload is accepted.
3. The response is binary PDF bytes, not JSON.
4. No database mutation occurs during preview (diagnosis code not persisted).
5. Server-side token validation remains enabled (missing token → 403).
"""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import (
    create_invoice_draft,
    preview_finalization,
    save_business_profile,
)
from jordana_invoice.review_server import make_handler
from jordana_invoice.review_services import approve_candidate, create_billing_party, create_person
from jordana_invoice.util import stable_hash

FICTIONAL_EIN = "00-0000000"
FICTIONAL_NPI = "0000000000"
FICTIONAL_SW = "SW-TEST"
FICTIONAL_DIAGNOSIS = "Z00.0"


def raw_row(key, title, start):
    return {
        "ingested_at": "2026-08-20T12:00:00Z", "snapshot_key": key, "run_id": f"run-{key}",
        "batch_name": "insurance-pdf-test", "capture_window": "past_7_days", "captured_at": "2026-08-20T12:00:00Z",
        "source_device": "test", "timezone": "America/New_York", "calendar_event_id": f"event-{key}",
        "event_fingerprint": f"fp-{key}", "event_title": title, "start_at": start,
        "end_at": start[:11] + "11:00:00-04:00", "duration_minutes": "60", "calendar": "Jordana Work",
        "payload_version": "2", "raw_json": "{}",
    }


class DraftPdfPreviewTokenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = str(self.root / "test.sqlite3")
        self.conn = connect(self.root / "test.sqlite3")
        init_db(self.conn)
        self.handler_cls = make_handler(self.db_path, write_token="test-write-token")

        self.person = create_person(self.conn, {"first_name": "Ins", "last_name": "Pdf", "display_name": "Ins Pdf"})
        self.party = create_billing_party(self.conn, {
            "billing_name": "Ins Pdf", "person_id": self.person["person_id"],
            "billing_email": "ins@example.test", "billing_address_line_1": "12 Pdf Ln",
            "billing_city": "Pdfville", "billing_state": "FL", "billing_postal_code": "00000",
            "preferred_delivery_method": "both",
        })
        save_business_profile(self.conn, {
            "business_name": "Test Practice", "provider_display_name": "Test Provider",
            "address_line_1": "200 Test Ave", "city": "Testville", "state": "FL", "postal_code": "00000",
            "phone": "555-0100", "email": "billing@test", "payee_name": "Test Payee",
            "payment_address_line_1": "200 Test Ave", "payment_city": "Testville", "payment_state": "FL",
            "payment_postal_code": "00000", "zelle_recipient": "test@example.test",
            "insurance_ein": FICTIONAL_EIN, "insurance_npi": FICTIONAL_NPI, "insurance_sw": FICTIONAL_SW,
        })

        # Create a draft invoice with one approved session
        import_rows(self.conn, [raw_row("pdf1", f"Ins Pdf | 60 | Office", "2026-05-15T10:00:00-04:00")], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash("calendar_event_id:event-pdf1"),),
        ).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Ins Pdf"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60, "service_mode": "office",
            "time_category": "standard", "approved_rate": "150.00",
            "payment_status": "unpaid", "billing_treatment": "billable",
        })
        session = self.conn.execute("SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)).fetchone()
        self.draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_period_start": "2026-05-01", "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31", "session_ids": [session["id"]],
        })
        self.invoice_id = self.draft["invoice"]["invoice_id"]

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _make_handler(self, path, body, token="auto"):
        handler = object.__new__(self.handler_cls)
        handler.path = path
        handler.command = "POST"
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
        captured = {}

        def mock_send_json(payload, status=200):
            captured["payload"] = payload
            captured["status"] = status

        handler.send_json = mock_send_json
        handler.send_error = lambda code: (_ for _ in ()).throw(AssertionError(f"unexpected error {code}"))
        handler.send_pdf = lambda body_bytes, filename: captured.setdefault("pdf", body_bytes)
        handler.send_response = lambda code: captured.setdefault("response_code", code)
        handler.send_header = lambda k, v: None
        handler.end_headers = lambda: None
        handler._apply_security_headers = lambda: None
        handler._apply_pdf_safe_headers = lambda: None
        handler.finish = lambda: None
        return handler, captured

    # 1. Missing write token returns 403 for draft-pdf POST
    def test_missing_write_token_returns_403(self):
        body = json.dumps({"insurance_coding_included": True, "insurance_diagnosis_code": FICTIONAL_DIAGNOSIS}).encode()
        handler, captured = self._make_handler(f"/api/invoices/{self.invoice_id}/draft-pdf", body, token=None)
        handler.conn = lambda: self.conn
        handler.do_POST()

        self.assertEqual(captured.get("status"), 403)
        self.assertNotIn("pdf", captured)

    # 2. Incorrect write token returns 403 for draft-pdf POST
    def test_incorrect_write_token_returns_403(self):
        body = json.dumps({"insurance_coding_included": True, "insurance_diagnosis_code": FICTIONAL_DIAGNOSIS}).encode()
        handler, captured = self._make_handler(f"/api/invoices/{self.invoice_id}/draft-pdf", body, token="wrong-token")
        handler.conn = lambda: self.conn
        handler.do_POST()

        self.assertEqual(captured.get("status"), 403)
        self.assertNotIn("pdf", captured)

    # 3. Valid token with insurance payload returns PDF bytes (not JSON)
    @patch("jordana_invoice.review_server.generate_draft_pdf_bytes")
    def test_valid_token_returns_pdf_bytes(self, mock_pdf):
        mock_pdf.return_value = b"%PDF-1.4 fake pdf bytes"
        body = json.dumps({"insurance_coding_included": True, "insurance_diagnosis_code": FICTIONAL_DIAGNOSIS}).encode()
        handler, captured = self._make_handler(f"/api/invoices/{self.invoice_id}/draft-pdf", body)
        handler.conn = lambda: self.conn
        handler.do_POST()

        self.assertIn("pdf", captured)
        self.assertTrue(captured["pdf"].startswith(b"%PDF"))
        # Verify the mock was called with insurance_coding_payload
        call_kwargs = mock_pdf.call_args
        render_model = call_kwargs.kwargs.get("render_model") or {}
        self.assertIsNotNone(render_model.get("insurance_coding"))

    # 4. No database mutation: insurance fields not persisted on draft after preview
    @patch("jordana_invoice.review_server.generate_draft_pdf_bytes")
    def test_no_db_mutation_after_preview(self, mock_pdf):
        mock_pdf.return_value = b"%PDF-1.4 fake pdf bytes"
        body = json.dumps({"insurance_coding_included": True, "insurance_diagnosis_code": FICTIONAL_DIAGNOSIS}).encode()
        handler, captured = self._make_handler(f"/api/invoices/{self.invoice_id}/draft-pdf", body)
        handler.conn = lambda: self.conn
        handler.do_POST()

        row = self.conn.execute("SELECT * FROM invoices WHERE invoice_id = ?", (self.invoice_id,)).fetchone()
        self.assertFalse(row["insurance_coding_included"])
        self.assertIsNone(row["insurance_diagnosis_code_snapshot"])
        self.assertIsNone(row["insurance_ein_snapshot"])
        self.assertIsNone(row["insurance_npi_snapshot"])
        self.assertIsNone(row["insurance_sw_snapshot"])

    # 5. Insurance coding payload is passed through to render model
    @patch("jordana_invoice.review_server.generate_draft_pdf_bytes")
    def test_insurance_payload_passed_to_render_model(self, mock_pdf):
        mock_pdf.return_value = b"%PDF-1.4 fake pdf bytes"
        body = json.dumps({"insurance_coding_included": True, "insurance_diagnosis_code": FICTIONAL_DIAGNOSIS}).encode()
        handler, captured = self._make_handler(f"/api/invoices/{self.invoice_id}/draft-pdf", body)
        handler.conn = lambda: self.conn
        handler.do_POST()

        mock_pdf.assert_called_once()
        render_model = mock_pdf.call_args.kwargs.get("render_model", {})
        coding = render_model.get("insurance_coding")
        self.assertIsNotNone(coding)
        self.assertEqual(len(coding), 4)
        self.assertEqual(coding[0]["value"], FICTIONAL_DIAGNOSIS)
        self.assertEqual(coding[1]["value"], FICTIONAL_EIN)
        self.assertEqual(coding[2]["value"], FICTIONAL_NPI)
        self.assertEqual(coding[3]["value"], FICTIONAL_SW)

    # 6. Unchecked insurance coding produces no insurance block in render model
    @patch("jordana_invoice.review_server.generate_draft_pdf_bytes")
    def test_unchecked_insurance_no_block(self, mock_pdf):
        mock_pdf.return_value = b"%PDF-1.4 fake pdf bytes"
        body = json.dumps({"insurance_coding_included": False}).encode()
        handler, captured = self._make_handler(f"/api/invoices/{self.invoice_id}/draft-pdf", body)
        handler.conn = lambda: self.conn
        handler.do_POST()

        mock_pdf.assert_called_once()
        render_model = mock_pdf.call_args.kwargs.get("render_model", {})
        self.assertIsNone(render_model.get("insurance_coding"))


if __name__ == "__main__":
    unittest.main()
