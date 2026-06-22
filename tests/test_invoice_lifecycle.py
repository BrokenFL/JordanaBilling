import tempfile
import unittest
import warnings
import base64
import io
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import (
    add_sessions_to_draft,
    create_invoice_draft,
    eligible_sessions,
    finalize_invoice,
    get_invoice,
    invoice_ineligibility_reasons,
    remove_line_from_draft,
    save_business_profile,
    update_invoice_draft,
    void_invoice,
)
from jordana_invoice.invoice_pdf import generate_invoice_pdf
from jordana_invoice.review_services import approve_candidate, create_billing_party, create_person, list_review_candidates
from jordana_invoice.service_catalog import learn_service, list_services, set_service_active
from jordana_invoice.util import stable_hash


def raw_row(key, title, start, status_suffix=""):
    return {
        "ingested_at": "2026-08-20T12:00:00Z", "snapshot_key": key, "run_id": f"run-{key}",
        "batch_name": "invoice-demo", "capture_window": "past_7_days", "captured_at": "2026-08-20T12:00:00Z",
        "source_device": "test", "timezone": "America/New_York", "calendar_event_id": f"event-{key}",
        "event_fingerprint": f"fp-{key}", "event_title": f"{title}{status_suffix}", "start_at": start,
        "end_at": start[:11] + "11:00:00-04:00", "duration_minutes": "60", "calendar": "Jordana Work",
        "payload_version": "2", "raw_json": "{}",
    }


class InvoiceLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "invoice.sqlite3")
        init_db(self.conn)
        self.person = create_person(self.conn, {"first_name": "Avery", "last_name": "Stone", "display_name": "Avery Stone"})
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
            "payment_address_line_1": "100 Example Avenue", "payment_city": "Example", "payment_state": "FL",
            "payment_postal_code": "00000", "invoice_total_label": "TOTAL DUE", "invoice_number_format": "YYYY-NNNN",
        })

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def approved_session(self, key="one", title="Avery Stone | 60 | Office", appointment=None, treatment="billable", amount="150.00"):
        import_rows(self.conn, [raw_row(key, title, f"2026-05-{10 + len(key):02d}T10:00:00-04:00")], "test")
        candidate_id = self.conn.execute("SELECT id FROM calendar_event_candidates WHERE candidate_key = ?", (stable_hash(f"calendar_event_id:event-{key}"),)).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Avery Stone"}],
            "billing_party_id": self.party["billing_party_id"], "approved_duration_minutes": 60,
            "service_mode": "office", "time_category": "standard", "approved_rate": amount,
            "payment_status": "unpaid", "billing_treatment": treatment,
        })
        if appointment:
            self.conn.execute("UPDATE sessions SET appointment_status = ?, billing_treatment = ? WHERE id = ?", (appointment, treatment, detail["session"]["id"]))
            self.conn.commit()
        return self.conn.execute("SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)).fetchone()

    def draft(self, sessions):
        return create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"], "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31", "invoice_date": "2026-05-31",
            "session_ids": [row["id"] for row in sessions],
        })

    def test_schema_is_additive_idempotent_and_seeded(self):
        init_db(self.conn)
        init_db(self.conn)
        tables = {row[0] for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"business_profile", "service_catalog", "invoices", "invoice_line_items", "invoice_sequences"} <= tables)
        self.assertEqual(len(list_services(self.conn)), 8)
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_legacy_billing_party_migration_adds_delivery_to_correct_table(self):
        legacy = connect(self.root / "legacy.sqlite3")
        init_db(legacy)
        legacy.execute("ALTER TABLE billing_parties DROP COLUMN preferred_delivery_method")
        init_db(legacy)
        billing_columns = {row[1] for row in legacy.execute("PRAGMA table_info(billing_parties)")}
        account_columns = {row[1] for row in legacy.execute("PRAGMA table_info(client_accounts)")}
        self.assertIn("preferred_delivery_method", billing_columns)
        self.assertNotIn("preferred_delivery_method", account_columns)
        legacy.close()

    def test_service_catalog_deduplicates_learns_audits_and_deactivates(self):
        first = learn_service(self.conn, "Preparation")
        second = learn_service(self.conn, "  preparation  ")
        self.assertEqual(first["service_catalog_id"], second["service_catalog_id"])
        custom = learn_service(self.conn, "Case Conference")
        self.assertEqual(custom["display_name"], "Case Conference")
        set_service_active(self.conn, custom["service_catalog_id"], False)
        self.assertFalse(self.conn.execute("SELECT active FROM service_catalog WHERE service_catalog_id = ?", (custom["service_catalog_id"],)).fetchone()[0])
        self.assertGreater(self.conn.execute("SELECT COUNT(*) FROM audit_log WHERE entity_type='service_catalog'").fetchone()[0], 0)

    def test_eligibility_protects_cancelled_no_show_and_duplicates(self):
        normal = self.approved_session("normal")
        cancelled = self.approved_session("cancel", appointment="cancelled", treatment="unresolved")
        cancelled_billable = self.approved_session("cancelb", appointment="cancelled", treatment="billable")
        no_show_waived = self.approved_session("noshow", appointment="no_show", treatment="waived")
        self.assertEqual(invoice_ineligibility_reasons(self.conn, normal), [])
        self.assertTrue(invoice_ineligibility_reasons(self.conn, cancelled))
        self.assertEqual(invoice_ineligibility_reasons(self.conn, cancelled_billable), [])
        self.assertTrue(invoice_ineligibility_reasons(self.conn, no_show_waived))
        draft = self.draft([normal])
        self.assertIn("already attached", " ".join(invoice_ineligibility_reasons(self.conn, normal)).lower())
        with self.assertRaises(ValueError):
            add_sessions_to_draft(self.conn, draft["invoice"]["invoice_id"], [normal["id"]])
        with self.assertRaises(ValueError):
            self.draft([normal])

    def test_draft_save_reopen_edit_remove_and_total(self):
        one = self.approved_session("draft1", amount="125.00")
        two = self.approved_session("draft2", title="Avery Stone | 60 | Correspondence", amount="75.00")
        draft = self.draft([one, two])
        self.assertIsNone(draft["invoice"]["invoice_number"])
        self.assertEqual(draft["invoice"]["total_cents"], 20000)
        line = draft["lines"][0]
        updated = update_invoice_draft(self.conn, draft["invoice"]["invoice_id"], {
            "delivery_method": "mail", "lines": [{"invoice_line_item_id": line["invoice_line_item_id"], "description_snapshot": "Office session", "sort_order": 1}],
        })
        self.assertEqual(updated["invoice"]["delivery_method"], "mail")
        self.assertEqual(get_invoice(self.conn, draft["invoice"]["invoice_id"])["lines"][0]["description_snapshot"], "Office session")
        removed = remove_line_from_draft(self.conn, draft["invoice"]["invoice_id"], draft["lines"][1]["invoice_line_item_id"])
        self.assertEqual(removed["invoice"]["total_cents"], 12500)

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_finalization_freezes_snapshots_numbers_and_prevents_edits(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self.approved_session("final")
        draft = self.draft([session])
        final = finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
        self.assertEqual(final["invoice"]["invoice_number"], "2026-0001")
        self.assertEqual(final["invoice"]["bill_to_name_snapshot"], "Avery Stone")
        self.conn.execute("UPDATE people SET display_name='Changed Name' WHERE person_id = ?", (self.person["person_id"],))
        self.conn.execute("UPDATE sessions SET approved_rate_cents=99999 WHERE id = ?", (session["id"],))
        save_business_profile(self.conn, {"business_name": "Changed Practice"})
        unchanged = get_invoice(self.conn, draft["invoice"]["invoice_id"])
        self.assertEqual(unchanged["lines"][0]["participants_snapshot"], "Avery Stone")
        self.assertEqual(unchanged["lines"][0]["line_amount_cents"], 15000)
        self.assertEqual(unchanged["invoice"]["business_name_snapshot"], "Demo Practice")
        with self.assertRaises(ValueError): update_invoice_draft(self.conn, draft["invoice"]["invoice_id"], {"notes": "late edit"})

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_finalization_revalidates_current_billing_period(self, fake_pdf):
        fake_pdf.return_value = "c" * 64
        session = self.approved_session("period")
        draft = self.draft([session])
        update_invoice_draft(self.conn, draft["invoice"]["invoice_id"], {"billing_period_start": "2026-05-01", "billing_period_end": "2026-05-01"})
        with self.assertRaises(ValueError):
            finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_void_preserves_number_and_releases_session_for_reissue(self, fake_pdf):
        fake_pdf.return_value = "b" * 64
        session = self.approved_session("void")
        first = self.draft([session])
        finalized = finalize_invoice(self.conn, first["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
        with self.assertRaises(ValueError): void_invoice(self.conn, first["invoice"]["invoice_id"], "")
        voided = void_invoice(self.conn, first["invoice"]["invoice_id"], "Incorrect billing period")
        self.assertEqual(voided["invoice"]["invoice_number"], "2026-0001")
        second = self.draft([session])
        reissued = finalize_invoice(self.conn, second["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
        self.assertEqual(reissued["invoice"]["invoice_number"], "2026-0002")

    def test_real_pdf_generation_and_missing_logo_fallback(self):
        try:
            import reportlab  # noqa: F401
        except ImportError:
            self.skipTest("reportlab is not installed in the active test interpreter")
        session = self.approved_session("pdf")
        draft = self.draft([session])
        final = finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
        path = Path(final["invoice"]["pdf_path"])
        self.assertTrue(path.is_file())
        self.assertEqual(path.name, "Invoice_2026-0001.pdf")
        self.assertEqual(len(final["invoice"]["pdf_sha256"]), 64)

    def test_multi_page_pdf_repeats_headers_and_keeps_footer_on_final_page(self):
        try:
            from PIL import Image
            from pypdf import PdfReader
        except ImportError:
            self.skipTest("PDF inspection dependencies are not installed")
        png = io.BytesIO()
        Image.new("RGB", (40, 20), "white").save(png, format="PNG")
        logo = self.root / "sanitized-logo.svg"
        logo.write_text(f'<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/png;base64,{base64.b64encode(png.getvalue()).decode()}"/></svg>', encoding="utf-8")
        invoice = {
            "invoice_number": "2026-0042", "invoice_date": "2026-06-01", "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31", "business_name_snapshot": "Demo Practice", "provider_name_snapshot": "Demo Provider",
            "bill_to_name_snapshot": "Avery Stone", "bill_to_address_snapshot": "10 Sample Street\nExample, FL 00000",
            "bill_to_email_snapshot": "avery@example.test", "total_cents": 600000, "total_label_snapshot": "TOTAL DUE",
            "payee_name_snapshot": "Demo Payee", "payment_address_snapshot": "Demo Payee\n100 Example Avenue\nExample, FL 00000",
            "logo_reference_snapshot": str(logo), "show_email_below_logo_snapshot": 0,
        }
        lines = [{"service_date": f"2026-05-{1 + (i % 28):02d}", "participants_snapshot": "Avery Stone & Taylor Reed", "description_snapshot": "Correspondence - Weekend Evening", "duration_minutes": 60, "line_amount_cents": 15000} for i in range(40)]
        path = self.root / "Invoice_2026-0042.pdf"
        generate_invoice_pdf(invoice, lines, path)
        reader = PdfReader(path)
        self.assertGreater(len(reader.pages), 1)
        texts = [page.extract_text() or "" for page in reader.pages]
        self.assertTrue(all("Date\nParticipants\nService\nDuration\nAmount" in text for text in texts))
        self.assertNotIn("TOTAL DUE", "\n".join(texts[:-1]))
        self.assertIn("TOTAL DUE", texts[-1])
        self.assertIn("Please make all checks payable to:", texts[-1])
        self.assertNotIn(str(logo), "\n".join(texts))


if __name__ == "__main__":
    unittest.main()
