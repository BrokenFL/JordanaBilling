import json
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, migrate_database
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import create_invoice_draft, finalize_invoice, preview_finalization, save_business_profile, void_invoice
from jordana_invoice.payment_services import create_payment, allocate_payment_to_session
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


class PaymentApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "test.sqlite3"
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

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _approved_session(self, key, start_at, amount="150.00"):
        import_rows(self.conn, [raw_row(key, "Pat Client | 60 | Office", start_at)], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Pat Client"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": amount,
            "payment_status": "unpaid",
            "billing_treatment": "billable",
        })
        return self.conn.execute("SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)).fetchone()

    def _finalize_invoice(self, session_ids):
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31",
            "session_ids": session_ids,
        })
        with patch("jordana_invoice.invoice_services.generate_invoice_pdf") as fake_pdf:
            fake_pdf.return_value = "x" * 64
            preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
            return finalize_invoice(
                self.conn,
                draft["invoice"]["invoice_id"],
                expected_revision=preview["preview_revision"],
                pdf_root=self.root / "Invoices",
            )

    @contextmanager
    def server(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(str(self.db_path)))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{httpd.server_address[1]}"
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()

    def fetch_write_token(self, base_url: str) -> str:
        with urllib.request.urlopen(f"{base_url}/review") as response:
            html = response.read().decode("utf-8")
        match = re.search(r'window\.__JORDANA_BOOTSTRAP__=\{"writeToken":\s*"([^"]+)"\};', html)
        self.assertIsNotNone(match)
        return match.group(1)

    def request_json(self, base_url: str, path: str, *, method="GET", payload=None):
        headers = {"Content-Type": "application/json"}
        data = None
        if method != "GET":
            headers["X-Jordana-Write-Token"] = self.fetch_write_token(base_url)
            data = json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(f"{base_url}{path}", data=data, headers=headers, method=method)
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_get_outstanding_invoices_and_history(self):
        unpaid = self._approved_session("api-u", "2026-05-10T10:00:00-04:00", amount="200.00")
        partial = self._approved_session("api-p", "2026-05-11T10:00:00-04:00", amount="300.00")
        paid = self._approved_session("api-paid", "2026-05-12T10:00:00-04:00", amount="250.00")
        voided = self._approved_session("api-void", "2026-05-13T10:00:00-04:00", amount="150.00")

        unpaid_invoice = self._finalize_invoice([unpaid["id"]])["invoice"]["invoice_id"]
        partial_invoice = self._finalize_invoice([partial["id"]])["invoice"]["invoice_id"]
        paid_invoice = self._finalize_invoice([paid["id"]])["invoice"]["invoice_id"]
        void_invoice_id = self._finalize_invoice([voided["id"]])["invoice"]["invoice_id"]
        void_invoice(self.conn, void_invoice_id, "void")

        partial_line = self.conn.execute(
            "SELECT invoice_line_item_id FROM invoice_line_items WHERE source_session_id = ?",
            (partial["id"],),
        ).fetchone()["invoice_line_item_id"]
        payment = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=10000, received_at="2026-05-20", method="check")
        allocate_payment_to_session(self.conn, payment_id=payment["payment_id"], session_id=partial["id"], amount_cents=10000, invoice_line_item_id=partial_line)

        paid_line = self.conn.execute(
            "SELECT invoice_line_item_id FROM invoice_line_items WHERE source_session_id = ?",
            (paid["id"],),
        ).fetchone()["invoice_line_item_id"]
        paid_payment = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=25000, received_at="2026-05-21", method="ach")
        allocate_payment_to_session(self.conn, payment_id=paid_payment["payment_id"], session_id=paid["id"], amount_cents=25000, invoice_line_item_id=paid_line)

        with self.server() as base_url:
          outstanding = self.request_json(base_url, "/api/payments/outstanding-invoices")
          history = self.request_json(base_url, f"/api/invoices/{partial_invoice}/payments")

        ids = {item["invoice_id"] for item in outstanding["items"]}
        self.assertIn(unpaid_invoice, ids)
        self.assertIn(partial_invoice, ids)
        self.assertNotIn(paid_invoice, ids)
        self.assertNotIn(void_invoice_id, ids)
        self.assertEqual(history["invoice"]["invoice_id"], partial_invoice)
        self.assertEqual(history["payments"][0]["amount_applied_cents"], 10000)
        self.assertEqual(history["payments"][0]["payment_status"], "posted")

    def test_post_invoice_payment_records_and_refreshes_balance(self):
        session = self._approved_session("api-post", "2026-05-10T10:00:00-04:00", amount="150.00")
        invoice = self._finalize_invoice([session["id"]])["invoice"]["invoice_id"]

        with self.server() as base_url:
            created = self.request_json(
                base_url,
                f"/api/invoices/{invoice}/payments",
                method="POST",
                payload={
                    "payment_date": "2026-05-25",
                    "amount_cents": 5000,
                    "payment_method": "zelle",
                    "reference_number": "Z-100",
                    "received_from_name": "Pat Client",
                    "administrative_note": "Front desk only",
                },
            )
            outstanding = self.request_json(base_url, "/api/payments/outstanding-invoices")

        self.assertEqual(created["invoice"]["paid_cents"], 5000)
        self.assertEqual(created["invoice"]["balance_cents"], 10000)
        row = next(item for item in outstanding["items"] if item["invoice_id"] == invoice)
        self.assertEqual(row["paid_cents"], 5000)
        self.assertEqual(row["balance_cents"], 10000)

    def test_post_invoice_payment_validation_is_sanitized(self):
        session = self._approved_session("api-validation", "2026-05-10T10:00:00-04:00", amount="150.00")
        invoice = self._finalize_invoice([session["id"]])["invoice"]["invoice_id"]

        with self.server() as base_url:
            request = urllib.request.Request(
                f"{base_url}/api/invoices/{invoice}/payments",
                data=json.dumps({
                    "payment_date": "",
                    "amount_cents": 20000,
                    "payment_method": "",
                }).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-Jordana-Write-Token": self.fetch_write_token(base_url),
                },
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(request)

        self.assertEqual(ctx.exception.code, 400)
        payload = json.loads(ctx.exception.read().decode("utf-8"))
        self.assertEqual(payload, {"ok": False, "error": "Payment date is required."})
        ctx.exception.close()


if __name__ == "__main__":
    unittest.main()
