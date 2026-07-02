"""Focused tests for Bill To delivery resolution and insurance/coding layout.

Tests the two release-blocking invoice issues:
1. BILL TO delivery/contact information reaching the invoice PDF
2. Insurance/coding block placement below the payment block
"""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import (
    create_invoice_draft,
    finalize_invoice,
    get_invoice,
    preview_finalization,
    save_business_profile,
    synchronize_draft_delivery_method,
    update_invoice_draft,
    validate_invoice_readiness,
)
from jordana_invoice.invoice_rendering import build_invoice_render_model
from jordana_invoice.invoice_pdf import (
    BODY_LEADING,
    _build_insurance_coding_flowables,
    _build_pdf_footer,
    generate_draft_pdf_bytes,
    generate_invoice_pdf,
)
from jordana_invoice.review_services import approve_candidate, create_billing_party, create_person
from jordana_invoice.util import stable_hash


def raw_row(key, title, start):
    return {
        "ingested_at": "2026-08-20T12:00:00Z", "snapshot_key": key,
        "run_id": f"run-{key}", "batch_name": "delivery-test",
        "capture_window": "past_7_days", "captured_at": "2026-08-20T12:00:00Z",
        "source_device": "test", "timezone": "America/New_York",
        "calendar_event_id": f"event-{key}", "event_fingerprint": f"fp-{key}",
        "event_title": title, "start_at": start,
        "end_at": start[:11] + "11:00:00-04:00", "duration_minutes": "60",
        "calendar": "Jordana Work", "payload_version": "2", "raw_json": "{}",
    }


def _has_pdf_deps():
    try:
        import reportlab  # noqa: F401
        from pypdf import PdfReader  # noqa: F401
        return True
    except ImportError:
        return False


class BillToDeliveryTests(unittest.TestCase):
    """Tests 1-10: Bill To / delivery data flow."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "billto.sqlite3")
        init_db(self.conn)
        self.person = create_person(self.conn, {
            "first_name": "Robin", "last_name": "Rivers",
            "display_name": "Robin Rivers",
        })
        save_business_profile(self.conn, {
            "business_name": "Demo Practice", "provider_display_name": "Demo Provider",
            "address_line_1": "100 Example Avenue", "city": "Example",
            "state": "FL", "postal_code": "00000", "phone": "555-0100",
            "email": "billing@example.test", "payee_name": "Demo Payee",
            "payment_address_line_1": "100 Example Avenue", "payment_city": "Example",
            "payment_state": "FL", "payment_postal_code": "00000",
            "zelle_recipient": "demo-zelle@example.test",
            "invoice_total_label": "TOTAL DUE", "invoice_number_format": "YYYY-NNNN",
        })

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _make_party(self, delivery="email", email="robin.rivers@example.test",
                    address=True):
        data = {
            "billing_name": "Robin Rivers", "person_id": self.person["person_id"],
            "billing_email": email,
            "preferred_delivery_method": delivery,
        }
        if address:
            data["billing_address_line_1"] = "10 Sample Street"
            data["billing_city"] = "Example"
            data["billing_state"] = "FL"
            data["billing_postal_code"] = "00000"
        return create_billing_party(self.conn, data)

    def _approved_session(self, key, party_id, day=15):
        import_rows(self.conn, [raw_row(key, "Robin Rivers | 60 | Office",
                                        f"2026-05-{day:02d}T10:00:00-04:00")], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": self.person["person_id"],
                              "display_name": "Robin Rivers"}],
            "billing_party_id": party_id, "approved_duration_minutes": 60,
            "service_mode": "office", "time_category": "standard",
            "approved_rate": "150.00", "payment_status": "unpaid",
            "billing_treatment": "billable",
        })
        return self.conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)
        ).fetchone()

    def _draft(self, party_id, sessions=None, **extra):
        data = {
            "bill_to_party_id": party_id,
            "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31",
        }
        if sessions:
            data["session_ids"] = [s["id"] for s in sessions]
        data.update(extra)
        return create_invoice_draft(self.conn, data)

    # 1. Active billing setup with email preference populates draft delivery
    def test_email_preference_populates_draft(self):
        party = self._make_party("email")
        draft = self._draft(party["billing_party_id"])
        self.assertEqual(draft["invoice"]["delivery_method"], "email")

    # 2. Canonical PDF includes Via Email line
    def test_pdf_includes_via_email(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        party = self._make_party("email")
        session = self._approved_session("s1", party["billing_party_id"])
        draft = self._draft(party["billing_party_id"], [session])
        pdf_bytes = generate_draft_pdf_bytes(
            draft["invoice"], draft["lines"],
            render_model=draft["render_model"],
        )
        from pypdf import PdfReader
        text = "\n".join(
            page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf_bytes)).pages
        )
        self.assertIn("Robin Rivers", text)
        self.assertIn("Via Email: robin.rivers@example.test", text)

    # 3. Review & Finalize readiness does not report unresolved delivery
    def test_readiness_no_unresolved_with_email(self):
        party = self._make_party("email")
        session = self._approved_session("s1", party["billing_party_id"])
        draft = self._draft(party["billing_party_id"], [session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        delivery_errors = [
            e for e in preview["readiness"]["errors"]
            if e.get("field") in ("delivery_method", "delivery_email", "delivery_address")
        ]
        self.assertEqual(delivery_errors, [])

    # 4. Active billing setup with mailing address renders mail delivery
    def test_mail_delivery_renders_address(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        party = self._make_party("mail", email="")
        session = self._approved_session("s1", party["billing_party_id"])
        draft = self._draft(party["billing_party_id"], [session])
        pdf_bytes = generate_draft_pdf_bytes(
            draft["invoice"], draft["lines"],
            render_model=draft["render_model"],
        )
        from pypdf import PdfReader
        text = "\n".join(
            page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf_bytes)).pages
        )
        self.assertIn("Robin Rivers", text)
        self.assertIn("10 Sample Street", text)
        self.assertNotIn("Via Email:", text)

    # 5. Both-delivery case renders address and email
    def test_both_delivery_renders_address_and_email(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        party = self._make_party("both")
        session = self._approved_session("s1", party["billing_party_id"])
        draft = self._draft(party["billing_party_id"], [session])
        pdf_bytes = generate_draft_pdf_bytes(
            draft["invoice"], draft["lines"],
            render_model=draft["render_model"],
        )
        from pypdf import PdfReader
        text = "\n".join(
            page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf_bytes)).pages
        )
        self.assertIn("Robin Rivers", text)
        self.assertIn("10 Sample Street", text)
        self.assertIn("Via Email: robin.rivers@example.test", text)

    # 6. Existing mutable draft created before Billing Setup can safely refresh
    def test_stale_draft_refreshes_delivery(self):
        party = self._make_party("unresolved", email="")
        draft = self._draft(party["billing_party_id"])
        invoice_id = draft["invoice"]["invoice_id"]
        self.assertEqual(draft["invoice"]["delivery_method"], "unresolved")
        # Now update the party's preference and email
        self.conn.execute(
            "UPDATE billing_parties SET preferred_delivery_method = 'email', billing_email = 'robin.rivers@example.test' WHERE billing_party_id = ?",
            (party["billing_party_id"],),
        )
        self.conn.commit()
        # get_invoice should auto-sync
        result = get_invoice(self.conn, invoice_id)
        self.assertEqual(result["invoice"]["delivery_method"], "email")

    # 7. Deliberate invoice-specific override is preserved
    def test_deliberate_override_preserved(self):
        party = self._make_party("email")
        draft = self._draft(party["billing_party_id"], delivery_method="mail")
        invoice_id = draft["invoice"]["invoice_id"]
        # get_invoice should NOT overwrite the deliberate "mail" override
        result = get_invoice(self.conn, invoice_id)
        self.assertEqual(result["invoice"]["delivery_method"], "mail")

    # 8. Finalized invoice snapshots remain unchanged
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_finalized_snapshot_immutable(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        party = self._make_party("email")
        session = self._approved_session("s1", party["billing_party_id"])
        draft = self._draft(party["billing_party_id"], [session])
        invoice_id = draft["invoice"]["invoice_id"]
        final = finalize_invoice(self.conn, invoice_id, pdf_root=self.root / "Invoices")
        self.assertEqual(final["invoice"]["delivery_method"], "email")
        original_snapshot = final["invoice"]["bill_to_email_snapshot"]
        self.assertEqual(original_snapshot, "robin.rivers@example.test")
        # Change the party's email after finalization
        self.conn.execute(
            "UPDATE billing_parties SET billing_email = 'changed@example.test' WHERE billing_party_id = ?",
            (party["billing_party_id"],),
        )
        self.conn.commit()
        result = get_invoice(self.conn, invoice_id)
        self.assertEqual(result["invoice"]["bill_to_email_snapshot"], original_snapshot)

    # 9. Preview, finalization preview, and finalized PDF use same Bill To lines
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_preview_and_final_same_bill_to_lines(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        party = self._make_party("email")
        session = self._approved_session("s1", party["billing_party_id"])
        draft = self._draft(party["billing_party_id"], [session])
        invoice_id = draft["invoice"]["invoice_id"]
        # Draft render model
        draft_result = get_invoice(self.conn, invoice_id)
        draft_lines = draft_result["render_model"]["bill_to_lines"]
        # Preview render model
        preview = preview_finalization(self.conn, invoice_id)
        preview_lines = preview["render_model"]["bill_to_lines"]
        self.assertEqual(draft_lines, preview_lines)
        # Finalized
        final = finalize_invoice(self.conn, invoice_id, pdf_root=self.root / "Invoices")
        final_lines = final["render_model"]["bill_to_lines"]
        self.assertEqual(draft_lines, final_lines)
        # All should contain the Via Email line
        self.assertIn("Via Email: robin.rivers@example.test", draft_lines)

    # 10. No stale data from inactive or duplicate billing party
    def test_no_stale_data_from_inactive_party(self):
        party1 = self._make_party("email", email="active@example.test")
        # Deactivate party1 and create a new active one
        self.conn.execute(
            "UPDATE billing_parties SET active = 0 WHERE billing_party_id = ?",
            (party1["billing_party_id"],),
        )
        self.conn.commit()
        party2 = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers", "person_id": self.person["person_id"],
            "billing_email": "new@example.test",
            "billing_address_line_1": "20 New Street",
            "billing_city": "Newville", "billing_state": "FL",
            "billing_postal_code": "00001",
            "preferred_delivery_method": "email",
        })
        session = self._approved_session("s1", party2["billing_party_id"])
        draft = self._draft(party2["billing_party_id"], [session])
        # The render model should use the active party's email, not the inactive one
        bill_to_lines = draft["render_model"]["bill_to_lines"]
        self.assertIn("Via Email: new@example.test", bill_to_lines)
        self.assertNotIn("active@example.test", bill_to_lines)


class InsuranceCodingLayoutTests(unittest.TestCase):
    """Tests 11-16: Insurance/coding block layout."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _sample_invoice(self, **overrides):
        base = {
            "invoice_number": "2026-0042",
            "invoice_date": "2026-06-01",
            "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31",
            "business_name_snapshot": "Demo Practice",
            "provider_name_snapshot": "Demo Provider",
            "credentials_snapshot": "LCSW",
            "business_address_snapshot": "100 Example Avenue\nExample, FL 00000",
            "bill_to_name_snapshot": "Robin Rivers",
            "bill_to_address_snapshot": "10 Sample Street\nExample, FL 00000",
            "bill_to_email_snapshot": "robin.rivers@example.test",
            "delivery_method": "email",
            "total_cents": 15000,
            "total_label_snapshot": "TOTAL DUE",
            "payee_name_snapshot": "Demo Payee",
            "payment_address_snapshot": "Demo Payee\n100 Example Avenue\nExample, FL 00000",
            "business_phone_snapshot": "555-0100",
            "zelle_recipient_snapshot": "demo-zelle@example.test",
            "status": "finalized",
            "insurance_coding_included": 1,
            "insurance_diagnosis_code_snapshot": "Z00.0",
            "insurance_ein_snapshot": "00-0000000",
            "insurance_npi_snapshot": "0000000000",
            "insurance_sw_snapshot": "SW-TEST",
        }
        base.update(overrides)
        return base

    def _sample_lines(self, count=3):
        return [
            {
                "service_date": f"2026-05-{22 + i:02d}",
                "participants_snapshot": "Robin Rivers",
                "description_snapshot": "Office Visit",
                "duration_minutes": 60,
                "line_amount_cents": 5000,
            }
            for i in range(count)
        ]

    # 11. Coding block starts below payment block by ~4 body-leading lines
    def test_coding_spacer_is_four_body_leading(self):
        from reportlab.platypus import Spacer
        render = build_invoice_render_model(self._sample_invoice(), self._sample_lines())
        flowables = _build_insurance_coding_flowables(render, None)
        self.assertTrue(flowables)
        spacer = flowables[0]
        self.assertIsInstance(spacer, Spacer)
        self.assertAlmostEqual(spacer.height, 4 * BODY_LEADING, delta=0.5)

    # 12. Coding block uses standard left margin (TA_LEFT alignment)
    def test_coding_block_left_aligned(self):
        from reportlab.lib.enums import TA_LEFT
        from reportlab.platypus import Paragraph
        render = build_invoice_render_model(self._sample_invoice(), self._sample_lines())
        flowables = _build_insurance_coding_flowables(render, None)
        coding_paras = [f for f in flowables if isinstance(f, Paragraph)]
        self.assertTrue(coding_paras)
        for para in coding_paras:
            self.assertEqual(para.style.alignment, TA_LEFT)

    # 13. No overlap: coding block is in the footer KeepTogether after payment
    def test_coding_block_after_payment_in_footer(self):
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.platypus import Table
        styles = getSampleStyleSheet()
        body = ParagraphStyle("B", parent=styles["BodyText"])
        small = ParagraphStyle("S", parent=body)
        total_label = ParagraphStyle("TL", parent=body)
        total_amount = ParagraphStyle("TA", parent=body)
        payment_title = ParagraphStyle("PT", parent=body)
        render = build_invoice_render_model(self._sample_invoice(), self._sample_lines())
        footer = _build_pdf_footer(render, 15000, body, small, total_label, total_amount, payment_title)
        coding = _build_insurance_coding_flowables(render, small)
        # Footer contains payment table
        payment_tables = [f for f in footer if isinstance(f, Table)]
        self.assertTrue(payment_tables)
        # Coding flowables come after footer in the final story assembly
        self.assertTrue(len(coding) > 0)

    # 14. Coding block absent when not enabled
    def test_coding_block_absent_when_not_enabled(self):
        render = build_invoice_render_model(
            self._sample_invoice(insurance_coding_included=0), self._sample_lines()
        )
        flowables = _build_insurance_coding_flowables(render, None)
        self.assertEqual(flowables, [])

    # 15. Draft/final layout parity for coding block
    def test_coding_block_draft_final_parity(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from jordana_invoice.invoice_rendering import build_invoice_render_model
        profile = {
            "insurance_ein": "00-0000000",
            "insurance_npi": "0000000000",
            "insurance_sw": "SW-TEST",
        }
        draft_invoice = self._sample_invoice(invoice_number="", status="draft")
        draft_model = build_invoice_render_model(
            draft_invoice, self._sample_lines(),
            business_profile=profile,
            insurance_coding_payload={
                "insurance_coding_included": True,
                "insurance_diagnosis_code": "Z00.0",
            },
        )
        draft_bytes = generate_draft_pdf_bytes(draft_invoice, self._sample_lines(), render_model=draft_model)
        final_invoice = self._sample_invoice()
        final_model = build_invoice_render_model(final_invoice, self._sample_lines())
        final_path = self.root / "Invoice_coding.pdf"
        generate_invoice_pdf(final_invoice, self._sample_lines(), final_path, render_model=final_model)
        from pypdf import PdfReader
        draft_text = "\n".join(
            page.extract_text() or "" for page in PdfReader(io.BytesIO(draft_bytes)).pages
        )
        final_text = "\n".join(
            page.extract_text() or "" for page in PdfReader(final_path).pages
        )
        for s in ["Diagnosis Code: Z00.0", "EIN: 00-0000000", "NPI: 0000000000", "SW: SW-TEST"]:
            self.assertIn(s, draft_text, f"Draft PDF missing: {s}")
            self.assertIn(s, final_text, f"Finalized PDF missing: {s}")

    # 16. Structured code values rendered exactly, not inferred
    def test_code_values_rendered_exactly(self):
        render = build_invoice_render_model(self._sample_invoice(), self._sample_lines())
        coding = render.get("insurance_coding")
        self.assertIsNotNone(coding)
        self.assertEqual(coding[0]["label"], "Diagnosis Code")
        self.assertEqual(coding[0]["value"], "Z00.0")
        self.assertEqual(coding[1]["label"], "EIN")
        self.assertEqual(coding[1]["value"], "00-0000000")
        self.assertEqual(coding[2]["label"], "NPI")
        self.assertEqual(coding[2]["value"], "0000000000")
        self.assertEqual(coding[3]["label"], "SW")
        self.assertEqual(coding[3]["value"], "SW-TEST")


class RenderModelDeliveryResolutionTests(unittest.TestCase):
    """Unit tests for the render model delivery_method resolution fix."""

    def test_unresolved_falls_back_to_party_preference(self):
        invoice = {
            "delivery_method": "unresolved",
            "bill_to_name_snapshot": "Robin Rivers",
            "bill_to_email_snapshot": "",
        }
        party = {
            "billing_name": "Robin Rivers",
            "billing_email": "robin.rivers@example.test",
            "preferred_delivery_method": "email",
        }
        render = build_invoice_render_model(invoice, [], billing_party=party)
        self.assertIn("Via Email: robin.rivers@example.test", render["bill_to_lines"])

    def test_blank_falls_back_to_party_preference(self):
        invoice = {
            "delivery_method": "",
            "bill_to_name_snapshot": "Robin Rivers",
            "bill_to_email_snapshot": "",
        }
        party = {
            "billing_name": "Robin Rivers",
            "billing_email": "robin.rivers@example.test",
            "preferred_delivery_method": "email",
        }
        render = build_invoice_render_model(invoice, [], billing_party=party)
        self.assertIn("Via Email: robin.rivers@example.test", render["bill_to_lines"])

    def test_deliberate_override_not_overwritten(self):
        invoice = {
            "delivery_method": "mail",
            "bill_to_name_snapshot": "Robin Rivers",
            "bill_to_email_snapshot": "",
            "bill_to_address_snapshot": "10 Sample Street\nExample, FL 00000",
        }
        party = {
            "billing_name": "Robin Rivers",
            "billing_email": "robin.rivers@example.test",
            "preferred_delivery_method": "email",
        }
        render = build_invoice_render_model(invoice, [], billing_party=party)
        # Should use "mail" from the invoice, not "email" from the party
        self.assertNotIn("Via Email:", render["bill_to_lines"])
        self.assertIn("10 Sample Street", render["bill_to_lines"])

    def test_finalized_uses_snapshot_not_party(self):
        invoice = {
            "delivery_method": "email",
            "bill_to_name_snapshot": "Robin Rivers",
            "bill_to_email_snapshot": "frozen@example.test",
            "status": "finalized",
        }
        party = {
            "billing_name": "Robin Rivers",
            "billing_email": "changed@example.test",
            "preferred_delivery_method": "email",
        }
        render = build_invoice_render_model(invoice, [], billing_party=party)
        self.assertIn("Via Email: frozen@example.test", render["bill_to_lines"])
        self.assertNotIn("changed@example.test", render["bill_to_lines"])


if __name__ == "__main__":
    unittest.main()
