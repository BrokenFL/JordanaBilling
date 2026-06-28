"""Regression tests for the compact invoice account-summary visual refinement.

These tests prove:
1. Payments Applied is absent when current paid amount is zero.
2. Payments Applied appears with a negative formatted amount when greater than zero.
3. Customer-facing HTML does not contain (As Finalized).
4. Customer-facing PDF text does not contain (As Finalized).
5. TOTAL AMOUNT DUE remains present.
6. Prior invoice number, date, and remaining balance remain present.
7. Current invoice total_cents remains unchanged.
8. Finalized internal UI still distinguishes frozen historical values from live status.
9. No-prior-balance invoices render compactly without an empty prior-invoice section.
10. Multi-page invoice layout still passes existing rendering tests.
"""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, migrate_database
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_pdf import generate_draft_pdf_bytes, generate_invoice_pdf
from jordana_invoice.invoice_rendering import build_print_preview_html, build_invoice_render_model
from jordana_invoice.invoice_services import (
    create_invoice_draft,
    finalize_invoice,
    get_invoice,
    save_business_profile,
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


def _has_pdf_deps():
    try:
        import reportlab  # noqa: F401
        from pypdf import PdfReader  # noqa: F401
        return True
    except ImportError:
        return False


class CompactAccountSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "test.sqlite3"
        migrate_database(self.db_path)
        self.conn = connect(self.db_path)

        self.person = create_person(self.conn, {
            "first_name": "Casey", "last_name": "Testcase",
            "display_name": "Casey Testcase",
        })
        self.party = create_billing_party(self.conn, {
            "billing_name": "Casey Testcase",
            "person_id": self.person["person_id"],
            "billing_email": "casey@example.test",
            "billing_address_line_1": "1 Test St",
            "billing_city": "Test", "billing_state": "FL",
            "billing_postal_code": "00000",
            "preferred_delivery_method": "email",
        })
        save_business_profile(self.conn, {
            "business_name": "Test Practice",
            "provider_display_name": "Test Provider",
            "address_line_1": "100 Test Ave",
            "city": "Test", "state": "FL", "postal_code": "00000",
            "phone": "555-0100", "email": "billing@test",
            "payee_name": "Test Payee",
            "payment_address_line_1": "100 Test Ave",
            "payment_city": "Test", "payment_state": "FL",
            "payment_postal_code": "00000",
            "zelle_recipient": "demo-zelle@example.test",
        })

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _approved_session(self, key, start_at="2026-05-15T10:00:00-04:00", amount="150.00"):
        import_rows(self.conn, [raw_row(key, "Casey Testcase | 60 | Office", start_at)], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Casey Testcase"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office", "time_category": "standard",
            "approved_rate": amount,
            "payment_status": "unpaid", "billing_treatment": "billable",
        })
        return self.conn.execute("SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)).fetchone()

    def _draft(self, sessions, date_str="2026-05-31"):
        return create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_period_start": date_str[:7] + "-01",
            "billing_period_end": date_str,
            "invoice_date": date_str,
            "session_ids": [row["id"] for row in sessions],
        })

    def _finalize(self, draft):
        pdf_root = self.root / "Invoices"
        with patch("jordana_invoice.invoice_services.generate_invoice_pdf",
                   side_effect=lambda inv, lines, path, **kw: (
                       Path(path).parent.mkdir(parents=True, exist_ok=True),
                       Path(path).write_bytes(b"%PDF-1.4 fake"),
                       "a" * 64,
                   )[-1]):
            return finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=pdf_root)

    def _setup_prior_unpaid(self):
        """Create a prior finalized unpaid invoice and a newer draft with prior balance."""
        s1 = self._approved_session("prior", "2026-04-10T10:00:00-04:00")
        d1 = self._draft([s1], "2026-04-30")
        self._finalize(d1)
        s2 = self._approved_session("newer", "2026-05-15T10:00:00-04:00")
        d2 = self._draft([s2], "2026-05-31")
        return d2

    # ── 1. Payments Applied absent when zero ──

    def test_payments_applied_absent_when_zero_html(self):
        d2 = self._setup_prior_unpaid()
        data = get_invoice(self.conn, d2["invoice"]["invoice_id"])
        html = build_print_preview_html(
            data["invoice"], data["lines"],
            business_profile=data.get("business_profile"),
            billing_party=data.get("billing_party"),
            account_summary=(data.get("render_model") or {}).get("account_summary"),
        )
        self.assertNotIn("Payments Applied", html)

    def test_payments_applied_absent_when_zero_pdf(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from pypdf import PdfReader
        d2 = self._setup_prior_unpaid()
        data = get_invoice(self.conn, d2["invoice"]["invoice_id"])
        render_model = build_invoice_render_model(
            data["invoice"], data["lines"],
            business_profile=data.get("business_profile"),
            billing_party=data.get("billing_party"),
            account_summary=(data.get("render_model") or {}).get("account_summary"),
        )
        pdf_bytes = generate_draft_pdf_bytes(data["invoice"], data["lines"], render_model=render_model)
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = " ".join((p.extract_text() or "") for p in reader.pages)
        self.assertNotIn("Payments Applied", text)

    # ── 2. Payments Applied appears when > 0 ──

    def test_payments_applied_present_when_nonzero_html(self):
        d2 = self._setup_prior_unpaid()
        # Record a payment on the prior invoice so the newer draft's account summary
        # shows current_invoice_paid_cents > 0 — but that's on the prior invoice, not
        # the current draft. We need to pay the *current* draft, which is impossible
        # since it's a draft. Instead, we test the render model directly.
        data = get_invoice(self.conn, d2["invoice"]["invoice_id"])
        summary = (data.get("render_model") or {}).get("account_summary") or {}
        # Simulate a payment on the current draft by modifying the summary
        summary_with_payment = dict(summary)
        summary_with_payment["current_invoice_paid_cents"] = 10000
        summary_with_payment["current_invoice_paid_display"] = "$100.00"
        summary_with_payment["current_invoice_balance_cents"] = 5000
        summary_with_payment["current_invoice_balance_display"] = "$50.00"
        summary_with_payment["total_amount_due_cents"] = 25000
        summary_with_payment["total_amount_due_display"] = "$250.00"
        html = build_print_preview_html(
            data["invoice"], data["lines"],
            business_profile=data.get("business_profile"),
            billing_party=data.get("billing_party"),
            account_summary=summary_with_payment,
        )
        self.assertIn("Payments Applied", html)
        self.assertIn("-$100.00", html)

    # ── 3. Customer-facing HTML does not contain (As Finalized) ──

    def test_html_does_not_contain_as_finalized(self):
        d2 = self._setup_prior_unpaid()
        data = get_invoice(self.conn, d2["invoice"]["invoice_id"])
        html = build_print_preview_html(
            data["invoice"], data["lines"],
            business_profile=data.get("business_profile"),
            billing_party=data.get("billing_party"),
            account_summary=(data.get("render_model") or {}).get("account_summary"),
        )
        self.assertNotIn("(As Finalized)", html)
        self.assertNotIn("As-Finalized", html)

    # ── 4. Customer-facing PDF text does not contain (As Finalized) ──

    def test_pdf_does_not_contain_as_finalized(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from pypdf import PdfReader
        d2 = self._setup_prior_unpaid()
        data = get_invoice(self.conn, d2["invoice"]["invoice_id"])
        render_model = build_invoice_render_model(
            data["invoice"], data["lines"],
            business_profile=data.get("business_profile"),
            billing_party=data.get("billing_party"),
            account_summary=(data.get("render_model") or {}).get("account_summary"),
        )
        pdf_bytes = generate_draft_pdf_bytes(data["invoice"], data["lines"], render_model=render_model)
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = " ".join((p.extract_text() or "") for p in reader.pages)
        self.assertNotIn("(As Finalized)", text)
        self.assertNotIn("As-Finalized", text)

    # ── 5. TOTAL AMOUNT DUE remains present ──

    def test_total_amount_due_present_html(self):
        d2 = self._setup_prior_unpaid()
        data = get_invoice(self.conn, d2["invoice"]["invoice_id"])
        html = build_print_preview_html(
            data["invoice"], data["lines"],
            business_profile=data.get("business_profile"),
            billing_party=data.get("billing_party"),
            account_summary=(data.get("render_model") or {}).get("account_summary"),
        )
        self.assertIn("TOTAL AMOUNT DUE", html)
        self.assertIn("$300.00", html)

    def test_total_amount_due_present_pdf(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from pypdf import PdfReader
        d2 = self._setup_prior_unpaid()
        data = get_invoice(self.conn, d2["invoice"]["invoice_id"])
        render_model = build_invoice_render_model(
            data["invoice"], data["lines"],
            business_profile=data.get("business_profile"),
            billing_party=data.get("billing_party"),
            account_summary=(data.get("render_model") or {}).get("account_summary"),
        )
        pdf_bytes = generate_draft_pdf_bytes(data["invoice"], data["lines"], render_model=render_model)
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = " ".join((p.extract_text() or "") for p in reader.pages)
        self.assertIn("TOTAL AMOUNT DUE", text)

    # ── 6. Prior invoice number, date, and remaining balance present ──

    def test_prior_invoice_details_present_html(self):
        d2 = self._setup_prior_unpaid()
        data = get_invoice(self.conn, d2["invoice"]["invoice_id"])
        html = build_print_preview_html(
            data["invoice"], data["lines"],
            business_profile=data.get("business_profile"),
            billing_party=data.get("billing_party"),
            account_summary=(data.get("render_model") or {}).get("account_summary"),
        )
        # Prior invoice number should appear
        prior_inv = self.conn.execute(
            "SELECT invoice_number, invoice_date FROM invoices WHERE status = 'finalized'"
        ).fetchone()
        self.assertIn(prior_inv["invoice_number"], html)
        self.assertIn("April 30, 2026", html)
        self.assertIn("$150.00", html)

    def test_prior_invoice_details_present_pdf(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from pypdf import PdfReader
        d2 = self._setup_prior_unpaid()
        data = get_invoice(self.conn, d2["invoice"]["invoice_id"])
        render_model = build_invoice_render_model(
            data["invoice"], data["lines"],
            business_profile=data.get("business_profile"),
            billing_party=data.get("billing_party"),
            account_summary=(data.get("render_model") or {}).get("account_summary"),
        )
        pdf_bytes = generate_draft_pdf_bytes(data["invoice"], data["lines"], render_model=render_model)
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = " ".join((p.extract_text() or "") for p in reader.pages)
        prior_inv = self.conn.execute(
            "SELECT invoice_number FROM invoices WHERE status = 'finalized'"
        ).fetchone()
        self.assertIn(prior_inv["invoice_number"], text)
        self.assertIn("remaining", text)

    # ── 7. Current invoice total_cents remains unchanged ──

    def test_invoice_total_cents_unchanged(self):
        d2 = self._setup_prior_unpaid()
        data = get_invoice(self.conn, d2["invoice"]["invoice_id"])
        self.assertEqual(data["invoice"]["total_cents"], 15000)
        summary = (data.get("render_model") or {}).get("account_summary") or {}
        self.assertEqual(summary["current_invoice_total_cents"], 15000)
        self.assertEqual(summary["prior_unpaid_balance_cents"], 15000)
        self.assertEqual(summary["total_amount_due_cents"], 30000)

    # ── 8. Finalized internal UI still distinguishes frozen vs live ──

    def test_finalized_invoice_still_has_as_finalized_summary(self):
        s1 = self._approved_session("frozen1", "2026-04-10T10:00:00-04:00")
        d1 = self._draft([s1], "2026-04-30")
        self._finalize(d1)
        data = get_invoice(self.conn, d1["invoice"]["invoice_id"])
        # as_finalized_summary should be present for finalized invoices
        self.assertIsNotNone(data["as_finalized_summary"])
        # current_status should also be present (live status)
        self.assertIsNotNone(data["current_status"])
        self.assertIn("current_invoice_total_cents", data["current_status"])

    # ── 9. No-prior-balance renders compactly without prior section ──

    def test_no_prior_balance_no_prior_section_html(self):
        s = self._approved_session("noprior", "2026-05-15T10:00:00-04:00")
        draft = self._draft([s], "2026-05-31")
        data = get_invoice(self.conn, draft["invoice"]["invoice_id"])
        html = build_print_preview_html(
            data["invoice"], data["lines"],
            business_profile=data.get("business_profile"),
            billing_party=data.get("billing_party"),
            account_summary=(data.get("render_model") or {}).get("account_summary"),
        )
        # No prior balance section should appear
        self.assertNotIn("Prior Unpaid Balance", html)
        self.assertNotIn("Prior unpaid invoices", html)
        self.assertNotIn("Includes prior invoice", html)
        # But the standard total should still be present
        self.assertIn("$150.00", html)

    # ── 10. Multi-page invoice layout still passes ──

    def test_multi_page_invoice_renders_with_summary(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from pypdf import PdfReader
        # Create many sessions to force multi-page
        sessions = []
        for i in range(15):
            s = self._approved_session(f"multi{i}", f"2026-05-{i+1:02d}T10:00:00-04:00")
            sessions.append(s)
        draft = self._draft(sessions, "2026-05-31")
        data = get_invoice(self.conn, draft["invoice"]["invoice_id"])
        render_model = build_invoice_render_model(
            data["invoice"], data["lines"],
            business_profile=data.get("business_profile"),
            billing_party=data.get("billing_party"),
            account_summary=(data.get("render_model") or {}).get("account_summary"),
        )
        pdf_bytes = generate_draft_pdf_bytes(data["invoice"], data["lines"], render_model=render_model)
        reader = PdfReader(io.BytesIO(pdf_bytes))
        self.assertGreater(len(reader.pages), 1)
        # Total should appear on the last page
        last_text = reader.pages[-1].extract_text() or ""
        self.assertIn("TOTAL DUE", last_text)

    # ── Additional: single prior invoice uses compact note format ──

    def test_single_prior_invoice_compact_note_html(self):
        d2 = self._setup_prior_unpaid()
        data = get_invoice(self.conn, d2["invoice"]["invoice_id"])
        html = build_print_preview_html(
            data["invoice"], data["lines"],
            business_profile=data.get("business_profile"),
            billing_party=data.get("billing_party"),
            account_summary=(data.get("render_model") or {}).get("account_summary"),
        )
        # Should use "Includes prior invoice" not "Prior unpaid invoices:"
        self.assertIn("Includes prior invoice", html)
        self.assertNotIn("Prior unpaid invoices:", html)


if __name__ == "__main__":
    unittest.main()
