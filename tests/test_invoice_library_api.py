import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, migrate_database
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import (
    create_invoice_draft,
    finalize_invoice,
    save_business_profile,
)
from jordana_invoice.review_server import make_handler
from jordana_invoice.review_services import approve_candidate, create_billing_party, create_person
from jordana_invoice.util import stable_hash


def raw_row(key, title, start):
    return {
        "ingested_at": "2026-08-20T12:00:00Z", "snapshot_key": key, "run_id": f"run-{key}",
        "batch_name": "test", "capture_window": "past_7_days", "captured_at": "2026-08-20T12:00:00Z",
        "source_device": "test", "timezone": "America/New_York", "calendar_event_id": f"event-{key}",
        "event_fingerprint": f"fp-{key}", "event_title": title, "start_at": start,
        "end_at": start[:11] + "11:00:00-04:00", "duration_minutes": "60", "calendar": "Jordana Work",
        "payload_version": "2", "raw_json": "{}",
    }


class InvoicePrintPreviewApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "test.sqlite3"
        self.previous_invoice_root = os.environ.get("JORDANA_INVOICES_DIR")
        os.environ["JORDANA_INVOICES_DIR"] = str(self.root / "Invoices")
        migrate_database(self.db_path)
        self.conn = connect(self.db_path)
        self.person = create_person(self.conn, {"first_name": "Pat", "last_name": "Client", "display_name": "Pat Client"})
        self.party = create_billing_party(self.conn, {
            "billing_name": "Pat Client", "person_id": self.person["person_id"],
            "billing_email": "pat@example.test", "billing_address_line_1": "1 Test St",
            "billing_city": "Test", "billing_state": "FL", "billing_postal_code": "00000",
            "preferred_delivery_method": "email",
        })
        save_business_profile(self.conn, {
            "business_name": "Test Practice", "provider_display_name": "Test Provider",
            "address_line_1": "100 Test Ave", "city": "Test", "state": "FL", "postal_code": "00000",
            "phone": "555-0100", "email": "billing@test", "payee_name": "Test Payee",
            "payment_address_line_1": "100 Test Ave", "payment_city": "Test", "payment_state": "FL",
            "payment_postal_code": "00000", "zelle_recipient": "demo-zelle@example.test",
        })
        self.handler_cls = make_handler(str(self.db_path))

    def tearDown(self):
        self.conn.close()
        if self.previous_invoice_root is None:
            os.environ.pop("JORDANA_INVOICES_DIR", None)
        else:
            os.environ["JORDANA_INVOICES_DIR"] = self.previous_invoice_root
        self.temp.cleanup()

    def _handler(self, path, body=b""):
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
        handler.send_response = lambda code: captured.setdefault("response_code", code)
        handler.send_header = lambda key, value: captured.setdefault("headers", {}).update({key: value})
        handler.end_headers = lambda: None
        handler._apply_security_headers = lambda nonce=None: None
        handler._security_headers_applied = True
        handler.finish = lambda: None
        return handler, captured

    def _approved_session(self, key, start_at="2026-05-15T10:00:00-04:00", amount="150.00"):
        import_rows(self.conn, [raw_row(key, "Pat Client | 60 | Office", start_at)], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Pat Client"}],
            "billing_party_id": self.party["billing_party_id"], "approved_duration_minutes": 60,
            "service_mode": "office", "time_category": "standard", "approved_rate": amount,
            "payment_status": "unpaid", "billing_treatment": "billable",
        })
        return self.conn.execute("SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)).fetchone()

    def _draft(self, sessions):
        return create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"], "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31", "invoice_date": "2026-05-31",
            "session_ids": [row["id"] for row in sessions],
        })

    def test_print_preview_returns_html_for_draft(self):
        s = self._approved_session("pp1")
        draft = self._draft([s])
        handler, captured = self._handler(f"/api/invoices/{draft['invoice']['invoice_id']}/print-preview")
        handler.conn = lambda: self.conn
        handler.do_GET()

        self.assertEqual(captured.get("response_code"), 200)
        body = handler.wfile.getvalue()
        self.assertIn(b"<html", body)
        self.assertIn(b"DRAFT", body)
        self.assertIn(b"draft-watermark", body)
        self.assertIn(b"Pat Client", body)

    def test_print_preview_rejects_finalized_invoice(self):
        s = self._approved_session("pp2")
        draft = self._draft([s])
        pdf_root = self.root / "Invoices"
        with patch("jordana_invoice.invoice_services.generate_invoice_pdf", side_effect=lambda inv, lines, path, **kw: (Path(path).parent.mkdir(parents=True, exist_ok=True), Path(path).write_bytes(b"%PDF-1.4 fake content"), "a" * 64)[-1]):
            finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=pdf_root)
        handler, captured = self._handler(f"/api/invoices/{draft['invoice']['invoice_id']}/print-preview")
        handler.conn = lambda: self.conn
        handler.do_GET()

        self.assertEqual(captured.get("status"), 400)
        self.assertIn("error", captured.get("payload", {}))

    def test_final_pdf_returns_pdf_bytes_for_finalized(self):
        s = self._approved_session("pdf1")
        draft = self._draft([s])
        pdf_root = self.root / "Invoices"
        with patch("jordana_invoice.invoice_services.generate_invoice_pdf", side_effect=lambda inv, lines, path, **kw: (Path(path).parent.mkdir(parents=True, exist_ok=True), Path(path).write_bytes(b"%PDF-1.4 fake content"), "a" * 64)[-1]):
            finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=pdf_root)
        handler, captured = self._handler(f"/api/invoices/{draft['invoice']['invoice_id']}/final-pdf")
        handler.conn = lambda: self.conn
        handler.do_GET()

        self.assertEqual(captured.get("response_code"), 200)
        body = handler.wfile.getvalue()
        self.assertTrue(body.startswith(b"%PDF"))

    def test_final_pdf_uses_no_cache_headers(self):
        s = self._approved_session("pdf-cache")
        draft = self._draft([s])
        pdf_root = self.root / "Invoices"
        with patch("jordana_invoice.invoice_services.generate_invoice_pdf", side_effect=lambda inv, lines, path, **kw: (Path(path).parent.mkdir(parents=True, exist_ok=True), Path(path).write_bytes(b"%PDF-1.4 fake content"), "a" * 64)[-1]):
            finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=pdf_root)
        handler, captured = self._handler(f"/api/invoices/{draft['invoice']['invoice_id']}/final-pdf?v=test")
        handler.conn = lambda: self.conn
        handler.do_GET()

        headers = captured.get("headers", {})
        self.assertEqual(headers.get("Cache-Control"), "no-store, no-cache, must-revalidate, max-age=0")
        self.assertEqual(headers.get("Pragma"), "no-cache")
        self.assertEqual(headers.get("Expires"), "0")

    def test_finalize_endpoint_returns_versioned_final_pdf_url_and_serves_same_file(self):
        s = self._approved_session("final-api")
        draft = self._draft([s])
        invoice_id = draft["invoice"]["invoice_id"]
        revision = draft["invoice"]["revision"]
        body = json.dumps({"confirmed": True, "expected_revision": revision}).encode("utf-8")
        handler, captured = self._handler(f"/api/invoices/{invoice_id}/finalize", body)
        handler.conn = lambda: self.conn
        handler.do_POST()

        payload = captured.get("payload", {})
        invoice = payload["invoice"]
        final_path = Path(invoice["pdf_path"])
        self.assertEqual(invoice["status"], "finalized")
        self.assertTrue(final_path.is_file())
        self.assertIn("/final-pdf?v=", invoice["final_pdf_url"])
        self.assertIn(invoice["pdf_sha256"], invoice["final_pdf_url"])

        pdf_handler, pdf_captured = self._handler(invoice["final_pdf_url"])
        pdf_handler.conn = lambda: self.conn
        pdf_handler.do_GET()

        self.assertEqual(pdf_captured.get("response_code"), 200)
        self.assertEqual(pdf_handler.wfile.getvalue(), final_path.read_bytes())
        self.assertIn(b"Times", final_path.read_bytes())
        self.assertNotIn(b"/BaseFont /Helvetica", final_path.read_bytes())

    def test_repeated_finalize_returns_existing_pdf_without_rewrite(self):
        s = self._approved_session("final-idempotent")
        draft = self._draft([s])
        invoice_id = draft["invoice"]["invoice_id"]
        first_body = json.dumps({"confirmed": True, "expected_revision": draft["invoice"]["revision"]}).encode("utf-8")
        first_handler, first_captured = self._handler(f"/api/invoices/{invoice_id}/finalize", first_body)
        first_handler.conn = lambda: self.conn
        first_handler.do_POST()
        first_invoice = first_captured["payload"]["invoice"]
        path = Path(first_invoice["pdf_path"])
        before_bytes = path.read_bytes()
        before_mtime_ns = path.stat().st_mtime_ns

        second_body = json.dumps({"confirmed": True, "expected_revision": first_invoice["revision"]}).encode("utf-8")
        second_handler, second_captured = self._handler(f"/api/invoices/{invoice_id}/finalize", second_body)
        second_handler.conn = lambda: self.conn
        second_handler.do_POST()

        self.assertEqual(second_captured.get("status", 200), 200)
        self.assertEqual(second_captured["payload"]["invoice"]["pdf_path"], str(path))
        self.assertEqual(path.read_bytes(), before_bytes)
        self.assertEqual(path.stat().st_mtime_ns, before_mtime_ns)

    def test_final_pdf_rejects_draft_invoice(self):
        s = self._approved_session("pdf2")
        draft = self._draft([s])
        handler, captured = self._handler(f"/api/invoices/{draft['invoice']['invoice_id']}/final-pdf")
        handler.conn = lambda: self.conn
        handler.do_GET()

        self.assertEqual(captured.get("status"), 400)
        self.assertIn("error", captured.get("payload", {}))

    def test_final_pdf_returns_404_for_missing_file(self):
        s = self._approved_session("pdf3")
        draft = self._draft([s])
        pdf_root = self.root / "Invoices"
        with patch("jordana_invoice.invoice_services.generate_invoice_pdf", side_effect=lambda inv, lines, path, **kw: (Path(path).parent.mkdir(parents=True, exist_ok=True), Path(path).write_bytes(b"%PDF-1.4 fake content"), "a" * 64)[-1]):
            finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=pdf_root)
        # Delete the PDF file to simulate missing file
        row = self.conn.execute("SELECT pdf_path FROM invoices WHERE invoice_id = ?", (draft["invoice"]["invoice_id"],)).fetchone()
        if row and row["pdf_path"]:
            Path(row["pdf_path"]).unlink(missing_ok=True)
        handler, captured = self._handler(f"/api/invoices/{draft['invoice']['invoice_id']}/final-pdf")
        handler.conn = lambda: self.conn
        handler.do_GET()

        self.assertEqual(captured.get("status"), 404)

    def test_invoice_list_api_returns_paginated_dict(self):
        s = self._approved_session("list1")
        self._draft([s])
        handler, captured = self._handler("/api/invoices")
        handler.conn = lambda: self.conn
        handler.do_GET()

        payload = captured.get("payload", {})
        self.assertIn("items", payload)
        self.assertIn("total", payload)
        self.assertEqual(payload["total"], 1)
        item = payload["items"][0]
        self.assertIn("paid_cents", item)
        self.assertIn("balance_cents", item)
        self.assertIn("payment_status", item)


class InvoiceLibraryUiStaticTests(unittest.TestCase):
    def test_invoice_library_html_has_search_and_filter_controls(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()
        self.assertIn('id="invoiceSearch"', html)
        self.assertIn('id="invoicePaymentStatusFilter"', html)
        self.assertIn('id="invoiceBillToFilter"', html)
        self.assertIn('id="invoiceDateFilter"', html)
        self.assertIn('id="invoiceCustomDateRange"', html)
        self.assertIn('id="invoicePrevPage"', html)
        self.assertIn('id="invoiceNextPage"', html)
        self.assertIn('id="invoiceResultCount"', html)

    def test_invoice_library_html_has_enhanced_columns(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()
        self.assertIn("<th>Number</th><th>Invoice Date</th><th>Service Period</th><th>Bill To</th><th>File Under</th><th>Participants</th><th>Status</th><th>Payment</th><th>Total</th><th>Paid</th><th>Balance</th><th>Actions</th>", html)

    def test_invoice_library_js_has_print_preview_and_pdf_buttons(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        self.assertIn("printPreviewBtn", js)
        self.assertIn("/print-preview", js)
        self.assertIn("openPdfBtn", js)
        self.assertIn("printPdfBtn", js)
        self.assertIn("/final-pdf", js)
        self.assertIn("renderInvoiceLibrary", js)
        self.assertIn("loadInvoiceBillToFilter", js)
        self.assertIn("invoiceLibrary", js)
        self.assertIn("invoicePrevPage", js)
        self.assertIn("invoiceNextPage", js)
        self.assertIn("invoicePaymentStatusFilter", js)
        self.assertIn("invoiceBillToFilter", js)
        self.assertIn("invoiceDateFilter", js)
        self.assertIn("invoiceSearch", js)
        self.assertIn("finalInvoicePdfUrl", js)
        self.assertIn("openFinalInvoicePdf(final.invoice, finalPdfWindow)", js)
        self.assertIn("window.open(\"about:blank\", \"_blank\")", js)

    def test_invoice_library_js_has_payment_summary_in_preview(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        self.assertIn("invoice-payment-summary", js)
        self.assertIn("invoice-void-info", js)

    def test_css_has_invoice_library_styles(self):
        css = Path("app/jordana_invoice/static/review.css").read_text()
        self.assertIn(".invoice-void-info", css)
        self.assertIn(".invoice-payment-summary", css)
        self.assertIn(".invoice-custom-date-range", css)
        self.assertIn(".pager", css)


if __name__ == "__main__":
    unittest.main()
