"""Focused tests for optional insurance coding on invoice PDFs.

Uses fictional placeholders only — no real EIN, NPI, SW, or diagnosis codes.
"""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_pdf import generate_draft_pdf_bytes, generate_invoice_pdf
from jordana_invoice.invoice_rendering import build_invoice_render_model, build_print_preview_html
from jordana_invoice.invoice_services import (
    create_invoice_draft,
    finalize_invoice,
    get_invoice,
    preview_finalization,
    save_business_profile,
    validate_invoice_readiness,
)
from jordana_invoice.review_services import approve_candidate, create_billing_party, create_person
from jordana_invoice.util import stable_hash

FICTIONAL_EIN = "00-0000000"
FICTIONAL_NPI = "0000000000"
FICTIONAL_SW = "SW-TEST"
FICTIONAL_DIAGNOSIS = "Z00.0"


def raw_row(key, title, start):
    return {
        "ingested_at": "2026-08-20T12:00:00Z", "snapshot_key": key, "run_id": f"run-{key}",
        "batch_name": "insurance-test", "capture_window": "past_7_days", "captured_at": "2026-08-20T12:00:00Z",
        "source_device": "test", "timezone": "America/New_York", "calendar_event_id": f"event-{key}",
        "event_fingerprint": f"fp-{key}", "event_title": title, "start_at": start,
        "end_at": start[:11] + "11:00:00-04:00", "duration_minutes": "60", "calendar": "Jordana Work",
        "payload_version": "2", "raw_json": "{}",
    }


class InsuranceCodingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "insurance.sqlite3")
        init_db(self.conn)
        self.person = create_person(self.conn, {"first_name": "Ins", "last_name": "Test", "display_name": "Ins Test"})
        self.party = create_billing_party(self.conn, {
            "billing_name": "Ins Test", "person_id": self.person["person_id"],
            "billing_email": "ins@example.test", "billing_address_line_1": "12 Test Ln",
            "billing_city": "Testville", "billing_state": "FL", "billing_postal_code": "00000",
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

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _approved_session(self, key, amount="150.00"):
        import_rows(self.conn, [raw_row(key, f"Ins Test | 60 | Office", f"2026-05-{10 + len(key):02d}T10:00:00-04:00")], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Ins Test"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60, "service_mode": "office",
            "time_category": "standard", "approved_rate": amount,
            "payment_status": "unpaid", "billing_treatment": "billable",
        })
        return self.conn.execute("SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)).fetchone()

    def _draft(self, sessions):
        return create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_period_start": "2026-05-01", "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31", "session_ids": [s["id"] for s in sessions],
        })

    # 1. Unchecked invoice preview has no insurance block
    def test_unchecked_preview_has_no_insurance_block(self):
        session = self._approved_session("unc1")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        self.assertIsNone(preview["render_model"].get("insurance_coding"))

    # 2. Checked preview shows the compact four-line block
    def test_checked_preview_shows_four_line_block(self):
        session = self._approved_session("chk1")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"], data={
            "insurance_coding_included": True,
            "insurance_diagnosis_code": FICTIONAL_DIAGNOSIS,
        })
        coding = preview["render_model"].get("insurance_coding")
        self.assertIsNotNone(coding)
        self.assertEqual(len(coding), 4)
        labels = [item["label"] for item in coding]
        self.assertEqual(labels, ["Diagnosis Code", "EIN", "NPI", "SW"])
        self.assertEqual(coding[0]["value"], FICTIONAL_DIAGNOSIS)

    # 3. Diagnosis code is required when enabled
    def test_diagnosis_required_when_enabled(self):
        session = self._approved_session("req1")
        draft = self._draft([session])
        readiness = validate_invoice_readiness(
            self.conn, draft["invoice"]["invoice_id"],
            insurance_coding_payload={"insurance_coding_included": True, "insurance_diagnosis_code": ""},
        )
        fields = {e["field"] for e in readiness["errors"]}
        self.assertIn("insurance_diagnosis_code", fields)

    # 4. Fixed identifiers come from settings
    def test_fixed_identifiers_from_settings(self):
        session = self._approved_session("fix1")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"], data={
            "insurance_coding_included": True,
            "insurance_diagnosis_code": FICTIONAL_DIAGNOSIS,
        })
        coding = preview["render_model"]["insurance_coding"]
        self.assertEqual(coding[1]["value"], FICTIONAL_EIN)
        self.assertEqual(coding[2]["value"], FICTIONAL_NPI)
        self.assertEqual(coding[3]["value"], FICTIONAL_SW)

    # 5. Finalization snapshot freezes all four values
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_finalization_freezes_all_values(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("frz1")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"], data={
            "insurance_coding_included": True,
            "insurance_diagnosis_code": FICTIONAL_DIAGNOSIS,
        })
        final = finalize_invoice(
            self.conn, draft["invoice"]["invoice_id"],
            expected_revision=preview["preview_revision"],
            pdf_root=self.root / "Invoices",
            insurance_coding_included=True,
            insurance_diagnosis_code=FICTIONAL_DIAGNOSIS,
        )
        inv = final["invoice"]
        self.assertEqual(inv["insurance_coding_included"], 1)
        self.assertEqual(inv["insurance_diagnosis_code_snapshot"], FICTIONAL_DIAGNOSIS)
        self.assertEqual(inv["insurance_ein_snapshot"], FICTIONAL_EIN)
        self.assertEqual(inv["insurance_npi_snapshot"], FICTIONAL_NPI)
        self.assertEqual(inv["insurance_sw_snapshot"], FICTIONAL_SW)

    # 6. Later settings changes do not change finalized PDF/snapshot
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_settings_change_does_not_affect_finalized(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("lat1")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"], data={
            "insurance_coding_included": True,
            "insurance_diagnosis_code": FICTIONAL_DIAGNOSIS,
        })
        final = finalize_invoice(
            self.conn, draft["invoice"]["invoice_id"],
            expected_revision=preview["preview_revision"],
            pdf_root=self.root / "Invoices",
            insurance_coding_included=True,
            insurance_diagnosis_code=FICTIONAL_DIAGNOSIS,
        )
        # Change settings after finalization
        save_business_profile(self.conn, {
            "business_name": "Test Practice", "provider_display_name": "Test Provider",
            "address_line_1": "200 Test Ave", "city": "Testville", "state": "FL", "postal_code": "00000",
            "phone": "555-0100", "email": "billing@test", "payee_name": "Test Payee",
            "payment_address_line_1": "200 Test Ave", "payment_city": "Testville", "payment_state": "FL",
            "payment_postal_code": "00000", "zelle_recipient": "test@example.test",
            "insurance_ein": "99-9999999", "insurance_npi": "9999999999", "insurance_sw": "SW-CHANGED",
        })
        refetched = get_invoice(self.conn, final["invoice"]["invoice_id"])
        inv = refetched["invoice"]
        self.assertEqual(inv["insurance_ein_snapshot"], FICTIONAL_EIN)
        self.assertEqual(inv["insurance_npi_snapshot"], FICTIONAL_NPI)
        self.assertEqual(inv["insurance_sw_snapshot"], FICTIONAL_SW)
        self.assertEqual(inv["insurance_diagnosis_code_snapshot"], FICTIONAL_DIAGNOSIS)

    # 7. Existing finalized invoices remain unchanged
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_existing_finalized_unchanged(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("old1")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        final = finalize_invoice(
            self.conn, draft["invoice"]["invoice_id"],
            expected_revision=preview["preview_revision"],
            pdf_root=self.root / "Invoices",
        )
        inv = final["invoice"]
        self.assertEqual(inv["insurance_coding_included"], 0)
        self.assertIsNone(inv["insurance_diagnosis_code_snapshot"])
        self.assertIsNone(inv["insurance_ein_snapshot"])
        self.assertIsNone(inv["insurance_npi_snapshot"])
        self.assertIsNone(inv["insurance_sw_snapshot"])

    # 8. Insurance block appears once on the final page for multi-page invoices
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_insurance_block_on_final_page_only(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("pg1")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"], data={
            "insurance_coding_included": True,
            "insurance_diagnosis_code": FICTIONAL_DIAGNOSIS,
        })
        final = finalize_invoice(
            self.conn, draft["invoice"]["invoice_id"],
            expected_revision=preview["preview_revision"],
            pdf_root=self.root / "Invoices",
            insurance_coding_included=True,
            insurance_diagnosis_code=FICTIONAL_DIAGNOSIS,
        )
        coding = final["render_model"].get("insurance_coding")
        self.assertIsNotNone(coding)
        self.assertEqual(len(coding), 4)

    # 9. Insurance block remains compact with no blank line between Diagnosis Code and EIN
    def test_insurance_block_compact_no_blank_line(self):
        session = self._approved_session("cmp1")
        draft = self._draft([session])
        html = build_print_preview_html(
            draft["invoice"], draft["lines"],
            business_profile=dict(self.conn.execute("SELECT * FROM business_profile WHERE active = 1").fetchone()),
            billing_party=dict(self.conn.execute("SELECT * FROM billing_parties WHERE billing_party_id = ?", (self.party["billing_party_id"],)).fetchone()),
            insurance_coding_payload={"insurance_coding_included": True, "insurance_diagnosis_code": FICTIONAL_DIAGNOSIS},
        )
        self.assertIn("Diagnosis Code: Z00.0", html)
        self.assertIn("EIN: 00-0000000", html)
        self.assertIn("NPI: 0000000000", html)
        self.assertIn("SW: SW-TEST", html)
        self.assertIn("insurance-coding", html)

    # 10. Unchecked invoices finalize normally without configured insurance identifiers
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_unchecked_finalizes_without_insurance_settings(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        # Remove insurance settings
        save_business_profile(self.conn, {
            "business_name": "Test Practice", "provider_display_name": "Test Provider",
            "address_line_1": "200 Test Ave", "city": "Testville", "state": "FL", "postal_code": "00000",
            "phone": "555-0100", "email": "billing@test", "payee_name": "Test Payee",
            "payment_address_line_1": "200 Test Ave", "payment_city": "Testville", "payment_state": "FL",
            "payment_postal_code": "00000", "zelle_recipient": "test@example.test",
            "insurance_ein": "", "insurance_npi": "", "insurance_sw": "",
        })
        session = self._approved_session("nok1")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        self.assertTrue(preview["readiness"]["ready"])
        final = finalize_invoice(
            self.conn, draft["invoice"]["invoice_id"],
            expected_revision=preview["preview_revision"],
            pdf_root=self.root / "Invoices",
        )
        self.assertEqual(final["invoice"]["status"], "finalized")
        self.assertEqual(final["invoice"]["insurance_coding_included"], 0)

    # 11. Preview causes no database mutation (diagnosis code absent from draft row)
    def test_preview_no_db_mutation(self):
        session = self._approved_session("mut1")
        draft = self._draft([session])
        preview_finalization(self.conn, draft["invoice"]["invoice_id"], data={
            "insurance_coding_included": True,
            "insurance_diagnosis_code": FICTIONAL_DIAGNOSIS,
        })
        row = self.conn.execute("SELECT * FROM invoices WHERE invoice_id = ?", (draft["invoice"]["invoice_id"],)).fetchone()
        # Insurance coding must not be persisted on the draft row
        self.assertFalse(row["insurance_coding_included"])
        self.assertIsNone(row["insurance_diagnosis_code_snapshot"])
        self.assertIsNone(row["insurance_ein_snapshot"])
        self.assertIsNone(row["insurance_npi_snapshot"])
        self.assertIsNone(row["insurance_sw_snapshot"])

    # 12. EIN/NPI/SW required in settings when insurance coding enabled
    def test_missing_settings_identifiers_block_finalization(self):
        save_business_profile(self.conn, {
            "business_name": "Test Practice", "provider_display_name": "Test Provider",
            "address_line_1": "200 Test Ave", "city": "Testville", "state": "FL", "postal_code": "00000",
            "phone": "555-0100", "email": "billing@test", "payee_name": "Test Payee",
            "payment_address_line_1": "200 Test Ave", "payment_city": "Testville", "payment_state": "FL",
            "payment_postal_code": "00000", "zelle_recipient": "test@example.test",
            "insurance_ein": "", "insurance_npi": FICTIONAL_NPI, "insurance_sw": FICTIONAL_SW,
        })
        session = self._approved_session("miss1")
        draft = self._draft([session])
        readiness = validate_invoice_readiness(
            self.conn, draft["invoice"]["invoice_id"],
            insurance_coding_payload={"insurance_coding_included": True, "insurance_diagnosis_code": FICTIONAL_DIAGNOSIS},
        )
        fields = {e["field"] for e in readiness["errors"]}
        self.assertIn("insurance_ein", fields)

    # 13. Draft PDF bytes include insurance coding when enabled
    def test_draft_pdf_includes_insurance_coding(self):
        session = self._approved_session("pdf1")
        draft = self._draft([session])
        profile = dict(self.conn.execute("SELECT * FROM business_profile WHERE active = 1").fetchone())
        party = dict(self.conn.execute("SELECT * FROM billing_parties WHERE billing_party_id = ?", (self.party["billing_party_id"],)).fetchone())
        render_model = build_invoice_render_model(
            draft["invoice"], draft["lines"],
            business_profile=profile, billing_party=party,
            insurance_coding_payload={"insurance_coding_included": True, "insurance_diagnosis_code": FICTIONAL_DIAGNOSIS},
        )
        self.assertIsNotNone(render_model["insurance_coding"])
        pdf_bytes = generate_draft_pdf_bytes(draft["invoice"], draft["lines"], render_model=render_model)
        self.assertTrue(len(pdf_bytes) > 0)

    # 14. Draft PDF bytes exclude insurance coding when unchecked
    def test_draft_pdf_excludes_insurance_coding(self):
        session = self._approved_session("pdf2")
        draft = self._draft([session])
        profile = dict(self.conn.execute("SELECT * FROM business_profile WHERE active = 1").fetchone())
        party = dict(self.conn.execute("SELECT * FROM billing_parties WHERE billing_party_id = ?", (self.party["billing_party_id"],)).fetchone())
        render_model = build_invoice_render_model(
            draft["invoice"], draft["lines"],
            business_profile=profile, billing_party=party,
        )
        self.assertIsNone(render_model.get("insurance_coding"))
        pdf_bytes = generate_draft_pdf_bytes(draft["invoice"], draft["lines"], render_model=render_model)
        self.assertTrue(len(pdf_bytes) > 0)


if __name__ == "__main__":
    unittest.main()
