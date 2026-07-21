import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, migrate_database
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import (
    create_invoice_draft,
    delete_invoice_draft,
    finalize_invoice,
    get_invoice,
    save_business_profile,
    start_invoice_correction,
    stage_approved_sessions_to_monthly_drafts,
    update_invoice_line_item,
)
from jordana_invoice.payment_services import allocate_payment_to_session, create_payment, reverse_allocation
from jordana_invoice.review_services import approve_candidate, create_billing_party, create_person
from jordana_invoice.util import stable_hash


def raw_row(key, start):
    return {
        "ingested_at": "2026-08-20T12:00:00Z",
        "snapshot_key": key,
        "run_id": f"run-{key}",
        "batch_name": "correction-test",
        "capture_window": "past_7_days",
        "captured_at": "2026-08-20T12:00:00Z",
        "source_device": "test",
        "timezone": "America/New_York",
        "calendar_event_id": f"event-{key}",
        "event_fingerprint": f"fp-{key}",
        "event_title": "Avery Stone | 60 | Office",
        "start_at": start,
        "end_at": start[:11] + "11:00:00-04:00",
        "duration_minutes": "60",
        "calendar": "Jordana Work",
        "payload_version": "2",
        "raw_json": "{}",
    }


class InvoiceCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        db_path = self.root / "invoice.sqlite3"
        migrate_database(db_path)
        self.conn = connect(db_path)
        self.person = create_person(self.conn, {
            "first_name": "Avery", "last_name": "Stone", "display_name": "Avery Stone",
        })
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
            "payment_address_line_1": "100 Example Avenue", "payment_city": "Example",
            "payment_state": "FL", "payment_postal_code": "00000",
            "zelle_recipient": "demo-zelle@example.test", "invoice_total_label": "TOTAL DUE",
            "invoice_number_format": "YYYY-NNNN",
        })

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def approved_session(self, key="one"):
        import_rows(self.conn, [raw_row(key, "2026-05-15T10:00:00-04:00")], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Avery Stone"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office", "time_category": "standard", "approved_rate": "150.00",
            "payment_status": "unpaid", "billing_treatment": "billable",
        })
        return self.conn.execute("SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)).fetchone()

    def draft(self, session):
        return create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31",
            "session_ids": [session["id"]],
        })

    def finalize_original(self, session):
        draft = self.draft(session)
        return finalize_invoice(
            self.conn,
            draft["invoice"]["invoice_id"],
            pdf_root=self.root / "Invoices",
        )

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_start_correction_clones_editable_draft_and_preserves_original(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self.approved_session()
        original = self.finalize_original(session)
        original_id = original["invoice"]["invoice_id"]
        original_number = original["invoice"]["invoice_number"]
        original_line = dict(original["lines"][0])

        correction = start_invoice_correction(self.conn, original_id, "Incorrect invoice information")

        self.assertEqual(correction["invoice"]["status"], "draft")
        self.assertEqual(correction["invoice"]["correction_of_invoice_id"], original_id)
        self.assertEqual(correction["invoice"]["correction_reason"], "Incorrect invoice information")
        self.assertEqual(correction["lines"][0]["source_session_id"], original_line["source_session_id"])
        self.assertEqual(correction["lines"][0]["line_amount_cents"], original_line["line_amount_cents"])

        unchanged = get_invoice(self.conn, original_id)
        self.assertEqual(unchanged["invoice"]["status"], "finalized")
        self.assertEqual(unchanged["invoice"]["invoice_number"], original_number)
        self.assertEqual(unchanged["lines"][0]["description_snapshot"], original_line["description_snapshot"])
        self.assertTrue(unchanged["invoice"]["correction_available"])
        self.assertEqual(unchanged["invoice"]["replacement_invoice"]["invoice_id"], correction["invoice"]["invoice_id"])

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_abandoning_correction_deletes_only_draft(self, fake_pdf):
        fake_pdf.return_value = "b" * 64
        session = self.approved_session("delete")
        original = self.finalize_original(session)
        correction = start_invoice_correction(self.conn, original["invoice"]["invoice_id"], "Wrong address")

        result = delete_invoice_draft(self.conn, correction["invoice"]["invoice_id"])

        self.assertEqual(result["action"], "deleted")
        self.assertEqual(
            self.conn.execute(
                "SELECT status FROM invoices WHERE invoice_id = ?",
                (original["invoice"]["invoice_id"],),
            ).fetchone()["status"],
            "finalized",
        )
        self.assertIsNone(
            self.conn.execute(
                "SELECT 1 FROM invoices WHERE invoice_id = ?",
                (correction["invoice"]["invoice_id"],),
            ).fetchone()
        )

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_finalizing_correction_voids_original_and_uses_new_number_without_rewriting_pdf(self, fake_pdf):
        fake_pdf.return_value = "c" * 64
        session = self.approved_session("replace")
        original = self.finalize_original(session)
        original_id = original["invoice"]["invoice_id"]
        original_pdf = Path(original["invoice"]["pdf_path"])
        original_pdf.parent.mkdir(parents=True, exist_ok=True)
        original_pdf.write_bytes(b"historical finalized invoice")
        original_checksum = original["invoice"]["pdf_sha256"]
        correction = start_invoice_correction(self.conn, original_id, "Corrected service description")
        line = correction["lines"][0]
        correction = update_invoice_line_item(
            self.conn,
            correction["invoice"]["invoice_id"],
            line_id=line["invoice_line_item_id"],
            description="Corrected office session",
            amount_cents=line["line_amount_cents"],
            amount_scope="invoice_line_only",
            reason="",
            expected_revision=correction["invoice"]["revision"],
        )

        replacement = finalize_invoice(
            self.conn,
            correction["invoice"]["invoice_id"],
            pdf_root=self.root / "Invoices",
        )
        parent = get_invoice(self.conn, original_id)

        self.assertEqual(replacement["invoice"]["status"], "finalized")
        self.assertNotEqual(replacement["invoice"]["invoice_number"], original["invoice"]["invoice_number"])
        self.assertEqual(replacement["invoice"]["correction_of_invoice_id"], original_id)
        self.assertEqual(replacement["lines"][0]["description_snapshot"], "Corrected office session")
        self.assertEqual(parent["invoice"]["status"], "void")
        self.assertIn(replacement["invoice"]["invoice_number"], parent["invoice"]["void_reason"])
        self.assertEqual(parent["invoice"]["pdf_path"], original["invoice"]["pdf_path"])
        self.assertEqual(parent["invoice"]["pdf_sha256"], original_checksum)
        self.assertEqual(original_pdf.read_bytes(), b"historical finalized invoice")
        actions = {
            row["action"]
            for row in self.conn.execute(
                "SELECT action FROM audit_log WHERE entity_type = 'invoice' AND entity_id IN (?, ?)",
                (original_id, correction["invoice"]["invoice_id"]),
            )
        }
        self.assertIn("replaced_by_invoice", actions)
        self.assertIn("correction_finalized", actions)

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_any_payment_allocation_history_blocks_correction(self, fake_pdf):
        fake_pdf.return_value = "d" * 64
        session = self.approved_session("paid")
        original = self.finalize_original(session)
        line_id = original["lines"][0]["invoice_line_item_id"]
        payment = create_payment(
            self.conn,
            billing_party_id=self.party["billing_party_id"],
            amount_cents=15000,
            received_at="2026-06-01",
            method="check",
        )
        allocation = allocate_payment_to_session(
            self.conn,
            payment_id=payment["payment_id"],
            session_id=session["id"],
            amount_cents=15000,
            invoice_line_item_id=line_id,
        )
        reverse_allocation(self.conn, allocation["allocation_id"], reason="Reversed for correction test")

        with self.assertRaisesRegex(ValueError, "payment history"):
            start_invoice_correction(self.conn, original["invoice"]["invoice_id"], "Incorrect amount")
        self.assertIsNone(
            self.conn.execute(
                "SELECT 1 FROM invoices WHERE correction_of_invoice_id = ?",
                (original["invoice"]["invoice_id"],),
            ).fetchone()
        )

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_finalize_rechecks_parent_payment_history(self, fake_pdf):
        fake_pdf.return_value = "e" * 64
        session = self.approved_session("race")
        original = self.finalize_original(session)
        correction = start_invoice_correction(self.conn, original["invoice"]["invoice_id"], "Corrected information")
        payment = create_payment(
            self.conn,
            billing_party_id=self.party["billing_party_id"],
            amount_cents=15000,
            received_at="2026-06-02",
            method="check",
        )
        allocate_payment_to_session(
            self.conn,
            payment_id=payment["payment_id"],
            session_id=session["id"],
            amount_cents=15000,
            invoice_line_item_id=original["lines"][0]["invoice_line_item_id"],
        )

        with self.assertRaisesRegex(ValueError, "payment history"):
            finalize_invoice(self.conn, correction["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
        self.assertEqual(
            self.conn.execute(
                "SELECT status FROM invoices WHERE invoice_id = ?",
                (original["invoice"]["invoice_id"],),
            ).fetchone()["status"],
            "finalized",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT status FROM invoices WHERE invoice_id = ?",
                (correction["invoice"]["invoice_id"],),
            ).fetchone()["status"],
            "draft",
        )

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_monthly_staging_does_not_touch_correction_draft(self, fake_pdf):
        fake_pdf.return_value = "f" * 64
        session = self.approved_session("staging")
        original = self.finalize_original(session)
        correction = start_invoice_correction(self.conn, original["invoice"]["invoice_id"], "Correction draft")

        result = stage_approved_sessions_to_monthly_drafts(self.conn)

        self.assertEqual(result["drafts_created"], 0)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM invoices WHERE status = 'draft' AND correction_of_invoice_id IS NULL"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM invoice_line_items WHERE invoice_id = ?",
                (correction["invoice"]["invoice_id"],),
            ).fetchone()[0],
            1,
        )


if __name__ == "__main__":
    unittest.main()
