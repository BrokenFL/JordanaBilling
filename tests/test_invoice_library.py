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
    list_invoice_records,
    save_business_profile,
    void_invoice,
)
from jordana_invoice.invoice_rendering import build_print_preview_html
from jordana_invoice.review_services import approve_candidate, create_billing_party, create_person
from jordana_invoice.util import stable_hash


def raw_row(key, title, start):
    return {
        "ingested_at": "2026-08-20T12:00:00Z", "snapshot_key": key, "run_id": f"run-{key}",
        "batch_name": "invoice-demo", "capture_window": "past_7_days", "captured_at": "2026-08-20T12:00:00Z",
        "source_device": "test", "timezone": "America/New_York", "calendar_event_id": f"event-{key}",
        "event_fingerprint": f"fp-{key}", "event_title": title, "start_at": start,
        "end_at": start[:11] + "11:00:00-04:00", "duration_minutes": "60", "calendar": "Jordana Work",
        "payload_version": "2", "raw_json": "{}",
    }


class InvoiceLibraryTests(unittest.TestCase):
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
            "payment_postal_code": "00000", "zelle_recipient": "demo-zelle@example.test",
            "invoice_total_label": "TOTAL DUE", "invoice_number_format": "YYYY-NNNN",
        })

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def approved_session(self, key, title="Avery Stone | 60 | Office", start=None, amount="150.00"):
        start = start or f"2026-05-{10 + len(key):02d}T10:00:00-04:00"
        import_rows(self.conn, [raw_row(key, title, start)], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Avery Stone"}],
            "billing_party_id": self.party["billing_party_id"], "approved_duration_minutes": 60,
            "service_mode": "office", "time_category": "standard", "approved_rate": amount,
            "payment_status": "unpaid", "billing_treatment": "billable",
        })
        return self.conn.execute("SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)).fetchone()

    def draft(self, sessions, invoice_date="2026-05-31", period_start="2026-05-01", period_end="2026-05-31"):
        return create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"], "billing_period_start": period_start,
            "billing_period_end": period_end, "invoice_date": invoice_date,
            "session_ids": [row["id"] for row in sessions],
        })

    def test_list_returns_paginated_dict_with_payment_fields(self):
        s = self.approved_session("lib1")
        self.draft([s])
        result = list_invoice_records(self.conn)
        self.assertIn("items", result)
        self.assertIn("total", result)
        self.assertIn("limit", result)
        self.assertIn("offset", result)
        self.assertEqual(result["total"], 1)
        item = result["items"][0]
        self.assertIn("paid_cents", item)
        self.assertIn("balance_cents", item)
        self.assertIn("payment_status", item)
        self.assertEqual(item["payment_status"], "unpaid")
        self.assertIn("participants_display", item)

    def test_search_by_invoice_number(self):
        s = self.approved_session("search1")
        draft = self.draft([s])
        # Search by "draft" should find it (no invoice_number yet)
        result = list_invoice_records(self.conn, search="Avery")
        self.assertEqual(result["total"], 1)

        # Search by something that doesn't match
        result_empty = list_invoice_records(self.conn, search="NonExistent")
        self.assertEqual(result_empty["total"], 0)

    def test_filter_by_status(self):
        s = self.approved_session("status1")
        self.draft([s])
        drafts = list_invoice_records(self.conn, status="draft")
        self.assertEqual(drafts["total"], 1)
        finalized = list_invoice_records(self.conn, status="finalized")
        self.assertEqual(finalized["total"], 0)

    def test_filter_by_payment_status(self):
        s = self.approved_session("pay1")
        self.draft([s])
        unpaid = list_invoice_records(self.conn, payment_status="unpaid")
        self.assertEqual(unpaid["total"], 1)
        paid = list_invoice_records(self.conn, payment_status="paid")
        self.assertEqual(paid["total"], 0)

    def test_filter_by_bill_to_party_id(self):
        s = self.approved_session("party1")
        self.draft([s])
        result = list_invoice_records(self.conn, bill_to_party_id=self.party["billing_party_id"])
        self.assertEqual(result["total"], 1)
        other = list_invoice_records(self.conn, bill_to_party_id="nonexistent-id")
        self.assertEqual(other["total"], 0)

    def test_filter_by_invoice_date_range(self):
        s = self.approved_session("date1")
        self.draft([s], invoice_date="2026-05-15")
        result = list_invoice_records(self.conn, invoice_date_from="2026-05-01", invoice_date_to="2026-05-31")
        self.assertEqual(result["total"], 1)
        result_empty = list_invoice_records(self.conn, invoice_date_from="2026-06-01", invoice_date_to="2026-06-30")
        self.assertEqual(result_empty["total"], 0)

    def test_service_period_filter_still_uses_raw_period_values(self):
        may = self.approved_session("period-may", start="2026-05-10T10:00:00-04:00")
        june = self.approved_session("period-june", start="2026-06-10T10:00:00-04:00")
        self.draft([may], invoice_date="2026-05-31", period_start="2026-05-01", period_end="2026-05-31")
        self.draft([june], invoice_date="2026-06-30", period_start="2026-06-01", period_end="2026-06-30")

        result = list_invoice_records(
            self.conn,
            service_period_from="2026-06-01",
            service_period_to="2026-06-30",
        )

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["billing_period_start"], "2026-06-01")
        self.assertEqual(result["items"][0]["billing_period_end"], "2026-06-30")

    def test_pagination(self):
        for i in range(3):
            s = self.approved_session(f"page{i}", start=f"2026-0{1+i}-10T10:00:00-04:00")
            self.draft([s], invoice_date=f"2026-0{1+i}-10", period_start=f"2026-0{1+i}-01", period_end=f"2026-0{1+i}-28")
        page1 = list_invoice_records(self.conn, limit=2, offset=0)
        self.assertEqual(len(page1["items"]), 2)
        self.assertEqual(page1["total"], 3)
        page2 = list_invoice_records(self.conn, limit=2, offset=2)
        self.assertEqual(len(page2["items"]), 1)

    def test_sort_by_total_ascending(self):
        s1 = self.approved_session("sort1", amount="100.00", start="2026-05-10T10:00:00-04:00")
        s2 = self.approved_session("sort2", amount="200.00", start="2026-06-10T10:00:00-04:00")
        self.draft([s1], invoice_date="2026-05-10", period_start="2026-05-01", period_end="2026-05-31")
        self.draft([s2], invoice_date="2026-06-10", period_start="2026-06-01", period_end="2026-06-30")
        result = list_invoice_records(self.conn, sort_by="total_cents", sort_dir="asc")
        self.assertLess(result["items"][0]["total_cents"], result["items"][1]["total_cents"])

    def test_get_invoice_includes_payment_summary(self):
        s = self.approved_session("getinv")
        draft = self.draft([s])
        data = get_invoice(self.conn, draft["invoice"]["invoice_id"])
        self.assertIn("paid_cents", data["invoice"])
        self.assertIn("balance_cents", data["invoice"])
        self.assertIn("payment_status", data["invoice"])
        self.assertEqual(data["invoice"]["payment_status"], "unpaid")

    def test_print_preview_html_contains_draft_watermark(self):
        s = self.approved_session("preview")
        draft = self.draft([s])
        data = get_invoice(self.conn, draft["invoice"]["invoice_id"])
        html = build_print_preview_html(
            data["invoice"], data["lines"],
            business_profile=data.get("business_profile"),
            billing_party=data.get("billing_party"),
        )
        self.assertIn("DRAFT", html)
        self.assertIn("draft-watermark", html)
        self.assertIn("draft-banner", html)
        self.assertIn("Avery Stone", html)

    def test_print_preview_is_read_only(self):
        s = self.approved_session("readonly")
        draft = self.draft([s])
        data = get_invoice(self.conn, draft["invoice"]["invoice_id"])
        before = dict(data["invoice"])
        build_print_preview_html(
            data["invoice"], data["lines"],
            business_profile=data.get("business_profile"),
            billing_party=data.get("billing_party"),
        )
        after = get_invoice(self.conn, draft["invoice"]["invoice_id"])
        self.assertEqual(before["invoice_id"], after["invoice"]["invoice_id"])
        self.assertEqual(before["status"], after["invoice"]["status"])
        self.assertEqual(before["revision"], after["invoice"]["revision"])

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_void_invoice_shows_void_payment_status(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        s = self.approved_session("void")
        draft = self.draft([s])
        finalized = finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
        voided = void_invoice(self.conn, draft["invoice"]["invoice_id"], "Test void")
        result = list_invoice_records(self.conn, status="void")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["payment_status"], "void")

    def test_participants_display_deduplicated(self):
        s = self.approved_session("dedup")
        draft = self.draft([s])
        result = list_invoice_records(self.conn)
        item = result["items"][0]
        self.assertIn("Avery Stone", item["participants_display"])


if __name__ == "__main__":
    unittest.main()
