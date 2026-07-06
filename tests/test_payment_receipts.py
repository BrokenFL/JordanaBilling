import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, migrate_database
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import (
    create_invoice_draft,
    finalize_invoice,
    preview_finalization,
    save_business_profile,
)
from jordana_invoice.payment_services import (
    allocate_payment_to_session,
    create_payment,
    record_invoice_payment,
    reverse_allocation,
    void_payment,
)
from jordana_invoice.receipt_services import (
    create_payment_receipt,
    preview_payment_receipt,
    trusted_receipt_document_action,
)
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


class PaymentReceiptTests(unittest.TestCase):
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

    def _approved_session(self, key, amount="150.00", payment_status="unpaid"):
        import_rows(self.conn, [raw_row(key, "Pat Client | 60 | Office", "2026-05-10T10:00:00-04:00")], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        payload = {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Pat Client"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": amount,
            "payment_status": payment_status,
            "billing_treatment": "billable",
        }
        if payment_status == "paid_at_session":
            payload.update({"amount_received": amount, "payment_date": "2026-05-10", "payment_method": "zelle", "reference_number": "Z-1"})
        detail = approve_candidate(self.conn, candidate_id, payload)
        return self.conn.execute("SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)).fetchone()

    def _finalize_invoice(self, session_id):
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31",
            "session_ids": [session_id],
        })
        with patch("jordana_invoice.invoice_services.generate_invoice_pdf") as fake_pdf:
            fake_pdf.side_effect = lambda inv, lines, path, **kw: (Path(path).parent.mkdir(parents=True, exist_ok=True), Path(path).write_bytes(b"%PDF invoice"), "i" * 64)[-1]
            preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
            return finalize_invoice(
                self.conn,
                draft["invoice"]["invoice_id"],
                expected_revision=preview["preview_revision"],
                pdf_root=self.root / "Invoices",
            )

    def _line_id(self, session_id):
        return self.conn.execute(
            "SELECT invoice_line_item_id FROM invoice_line_items WHERE source_session_id = ?",
            (session_id,),
        ).fetchone()["invoice_line_item_id"]

    def _create_receipt(self, payment_id):
        with patch("jordana_invoice.receipt_services.generate_receipt_pdf") as fake_pdf:
            fake_pdf.side_effect = lambda snapshot, path: (Path(path).parent.mkdir(parents=True, exist_ok=True), Path(path).write_bytes(b"%PDF receipt"), "r" * 64)[-1]
            return create_payment_receipt(self.conn, payment_id, pdf_root=self.root / "Receipts")

    def test_preview_creates_no_database_or_filesystem_mutation(self):
        session = self._approved_session("prev-no-mut")
        invoice = self._finalize_invoice(session["id"])["invoice"]["invoice_id"]
        payment = record_invoice_payment(self.conn, invoice_id=invoice, payment_date="2026-05-15", amount_cents=15000, payment_method="zelle")["payment"]
        before_receipts = self.conn.execute("SELECT COUNT(*) FROM payment_receipts").fetchone()[0]
        before_sequence = self.conn.execute("SELECT COUNT(*) FROM receipt_sequences").fetchone()[0]
        preview = preview_payment_receipt(self.conn, payment["payment_id"])
        self.assertEqual(preview["mode"], "preview")
        self.assertEqual(preview["snapshot"]["document_title"], "DRAFT PAYMENT RECEIPT")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM payment_receipts").fetchone()[0], before_receipts)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM receipt_sequences").fetchone()[0], before_sequence)
        self.assertFalse((self.root / "Receipts").exists())

    def test_posted_invoice_payment_creates_receipt(self):
        session = self._approved_session("inv-rec")
        invoice = self._finalize_invoice(session["id"])["invoice"]["invoice_id"]
        payment = record_invoice_payment(self.conn, invoice_id=invoice, payment_date="2026-05-15", amount_cents=15000, payment_method="check")["payment"]
        result = self._create_receipt(payment["payment_id"])
        self.assertTrue(result["created"])
        self.assertEqual(result["receipt"]["receipt_number"], "R-2026-0001")
        self.assertIn("Invoice", result["snapshot"]["allocations"][0]["reference_display"])

    def test_paid_at_session_payment_creates_receipt_without_invoice(self):
        session = self._approved_session("pas-rec", payment_status="paid_at_session")
        payment = self.conn.execute("SELECT * FROM payments WHERE source_session_id = ?", (session["id"],)).fetchone()
        result = self._create_receipt(payment["payment_id"])
        self.assertTrue(result["created"])
        self.assertEqual(result["snapshot"]["allocations"][0]["reference_display"], "Session")
        self.assertEqual(result["snapshot"]["filing_owner"]["selected"]["person_id"], self.person["person_id"])

    def test_partial_payment_shows_remaining_balance(self):
        session = self._approved_session("partial", amount="200.00")
        invoice = self._finalize_invoice(session["id"])["invoice"]["invoice_id"]
        payment = record_invoice_payment(self.conn, invoice_id=invoice, payment_date="2026-05-15", amount_cents=10000, payment_method="ach")["payment"]
        result = self._create_receipt(payment["payment_id"])
        self.assertEqual(result["snapshot"]["allocations"][0]["remaining_balance_cents"], 10000)
        self.assertFalse(result["snapshot"]["paid_in_full"])

    def test_full_payment_shows_paid_in_full(self):
        session = self._approved_session("full")
        invoice = self._finalize_invoice(session["id"])["invoice"]["invoice_id"]
        payment = record_invoice_payment(self.conn, invoice_id=invoice, payment_date="2026-05-15", amount_cents=15000, payment_method="ach")["payment"]
        result = self._create_receipt(payment["payment_id"])
        self.assertTrue(result["snapshot"]["paid_in_full"])

    def test_unapplied_amount_is_displayed(self):
        session = self._approved_session("unapplied")
        payment = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=20000, received_at="2026-05-15", method="cash")
        allocate_payment_to_session(self.conn, payment_id=payment["payment_id"], session_id=session["id"], amount_cents=15000)
        result = self._create_receipt(payment["payment_id"])
        self.assertEqual(result["snapshot"]["unapplied_cents"], 5000)
        self.assertEqual(result["snapshot"]["unapplied_display"], "$50.00")

    def test_receipt_snapshot_remains_unchanged_after_allocation_changes(self):
        session = self._approved_session("snapshot")
        payment = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-15")
        allocation = allocate_payment_to_session(self.conn, payment_id=payment["payment_id"], session_id=session["id"], amount_cents=15000)
        result = self._create_receipt(payment["payment_id"])
        before = result["receipt"]["snapshot_json"]
        reverse_allocation(self.conn, allocation["allocation_id"], reason="later correction")
        after = self.conn.execute("SELECT snapshot_json FROM payment_receipts WHERE payment_id = ?", (payment["payment_id"],)).fetchone()[0]
        self.assertEqual(json.loads(before), json.loads(after))

    def test_duplicate_creation_returns_existing_receipt(self):
        session = self._approved_session("dupe")
        invoice = self._finalize_invoice(session["id"])["invoice"]["invoice_id"]
        payment = record_invoice_payment(self.conn, invoice_id=invoice, payment_date="2026-05-15", amount_cents=15000, payment_method="zelle")["payment"]
        first = self._create_receipt(payment["payment_id"])
        second = self._create_receipt(payment["payment_id"])
        self.assertFalse(second["created"])
        self.assertEqual(second["receipt"]["receipt_id"], first["receipt"]["receipt_id"])
        self.assertEqual(self.conn.execute("SELECT last_value FROM receipt_sequences WHERE sequence_year = 2026").fetchone()[0], 1)

    def test_void_payment_cannot_create_receipt(self):
        session = self._approved_session("void")
        payment = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-15")
        allocation = allocate_payment_to_session(self.conn, payment_id=payment["payment_id"], session_id=session["id"], amount_cents=15000)
        reverse_allocation(self.conn, allocation["allocation_id"], reason="reverse")
        void_payment(self.conn, payment["payment_id"], reason="void")
        with self.assertRaisesRegex(ValueError, "posted"):
            self._create_receipt(payment["payment_id"])

    def test_filing_owner_and_path_are_correct(self):
        session = self._approved_session("path")
        invoice = self._finalize_invoice(session["id"])["invoice"]["invoice_id"]
        payment = record_invoice_payment(self.conn, invoice_id=invoice, payment_date="2026-05-15", amount_cents=15000, payment_method="zelle")["payment"]
        result = self._create_receipt(payment["payment_id"])
        path = Path(result["receipt"]["pdf_path"])
        self.assertEqual(path.parts[-4:], ("Receipts", "Pat Client", "May 2026", "Receipt_R-2026-0001.pdf"))
        self.assertEqual(result["receipt"]["filing_owner_person_id"], self.person["person_id"])

    def test_receipt_uses_configured_documents_client_files_root(self):
        session = self._approved_session("docs-receipt")
        invoice = self._finalize_invoice(session["id"])["invoice"]["invoice_id"]
        payment = record_invoice_payment(self.conn, invoice_id=invoice, payment_date="2026-05-15", amount_cents=15000, payment_method="zelle")["payment"]
        client_files = self.root / "Documents" / "Jordana Billing" / "Client Files"
        with patch("jordana_invoice.receipt_services.generate_receipt_pdf") as fake_pdf:
            fake_pdf.side_effect = lambda snapshot, path: (Path(path).parent.mkdir(parents=True, exist_ok=True), Path(path).write_bytes(b"%PDF receipt"), "2" * 64)[-1]
            with patch.dict("os.environ", {"JORDANA_RECEIPTS_DIR": str(client_files)}):
                result = create_payment_receipt(self.conn, payment["payment_id"])

        self.assertEqual(
            Path(result["receipt"]["pdf_path"]),
            client_files / "Pat Client" / "May 2026" / "Receipt_R-2026-0001.pdf",
        )

    def test_receipt_document_actions_reject_paths_outside_configured_root(self):
        session = self._approved_session("outside-receipt")
        payment = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-15")
        allocate_payment_to_session(self.conn, payment_id=payment["payment_id"], session_id=session["id"], amount_cents=15000)
        outside_pdf = self.root / "Other Files" / "Pat Client" / "Receipt_R-2026-0001.pdf"
        outside_pdf.parent.mkdir(parents=True, exist_ok=True)
        outside_pdf.write_bytes(b"pdf")
        self.conn.execute(
            """INSERT INTO payment_receipts (
              receipt_id, payment_id, receipt_number, status, payment_received_at,
              amount_cents, filing_owner_person_id, filing_owner_person_code_snapshot,
              filing_owner_display_name_snapshot, snapshot_json, pdf_path, pdf_sha256,
              created_at, updated_at
            ) VALUES ('outside-receipt-id', ?, 'R-2026-0001', 'finalized', '2026-05-15',
              15000, ?, ?, 'Pat Client', '{}', ?, ?, '2026-05-15T00:00:00', '2026-05-15T00:00:00')""",
            (payment["payment_id"], self.person["person_id"], self.person["person_code"], str(outside_pdf), "c" * 64),
        )
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "outside the configured receipt folder"):
            trusted_receipt_document_action(
                self.conn,
                "outside-receipt-id",
                "open_pdf",
                pdf_root=self.root / "Documents" / "Jordana Billing" / "Client Files",
            )

    def test_existing_invoice_pdf_row_remains_unchanged(self):
        session = self._approved_session("invoice-unchanged")
        invoice = self._finalize_invoice(session["id"])["invoice"]["invoice_id"]
        before = dict(self.conn.execute("SELECT pdf_path, pdf_sha256, invoice_number, status FROM invoices WHERE invoice_id = ?", (invoice,)).fetchone())
        payment = record_invoice_payment(self.conn, invoice_id=invoice, payment_date="2026-05-15", amount_cents=15000, payment_method="zelle")["payment"]
        self._create_receipt(payment["payment_id"])
        after = dict(self.conn.execute("SELECT pdf_path, pdf_sha256, invoice_number, status FROM invoices WHERE invoice_id = ?", (invoice,)).fetchone())
        self.assertEqual(before, after)

    def test_receipt_pdf_uses_invoice_layout_with_paid_on_date(self):
        try:
            from pypdf import PdfReader
        except ImportError:
            self.skipTest("pypdf is not installed in the active test interpreter")
        session = self._approved_session("receipt-layout")
        invoice = self._finalize_invoice(session["id"])["invoice"]["invoice_id"]
        payment = record_invoice_payment(self.conn, invoice_id=invoice, payment_date="2026-05-15", amount_cents=15000, payment_method="zelle")["payment"]

        result = create_payment_receipt(self.conn, payment["payment_id"], pdf_root=self.root / "Receipts")

        text = "\n".join(page.extract_text() or "" for page in PdfReader(result["receipt"]["pdf_path"]).pages)
        self.assertIn("RECEIPT", text)
        self.assertIn("Paid on May 15, 2026", text)
        self.assertIn("R-2026-0001", text)
        self.assertIn("AMOUNT PAID", text)
        self.assertNotIn("PAYMENT RECEIPT", text)
        self.assertNotIn("Please make checks payable to", text)


if __name__ == "__main__":
    unittest.main()
