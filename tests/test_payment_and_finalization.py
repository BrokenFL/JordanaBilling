"""Tests for simplified payment status and safe invoice finalization."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import (
    add_sessions_to_draft,
    create_invoice_draft,
    finalize_invoice,
    get_invoice,
    invoice_ineligibility_reasons,
    preview_finalization,
    remove_line_from_draft,
    save_business_profile,
    update_invoice_draft,
    void_invoice,
)
from jordana_invoice.review_services import approve_candidate, create_billing_party, create_person
from jordana_invoice.util import normalize_payment_status, stable_hash


def raw_row(key, title, start):
    return {
        "ingested_at": "2026-08-20T12:00:00Z", "snapshot_key": key, "run_id": f"run-{key}",
        "batch_name": "test", "capture_window": "past_7_days", "captured_at": "2026-08-20T12:00:00Z",
        "source_device": "test", "timezone": "America/New_York", "calendar_event_id": f"event-{key}",
        "event_fingerprint": f"fp-{key}", "event_title": title, "start_at": start,
        "end_at": start[:11] + "11:00:00-04:00", "duration_minutes": "60", "calendar": "Jordana Work",
        "payload_version": "2", "raw_json": "{}",
    }


class PaymentStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "test.sqlite3")
        init_db(self.conn)
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
            "payment_postal_code": "00000",
        })

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _approved_session(self, key, payment_status="unpaid", amount="150.00"):
        import_rows(self.conn, [raw_row(key, f"Pat Client | 60 | Office", f"2026-05-{10 + len(key):02d}T10:00:00-04:00")], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Pat Client"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60, "service_mode": "office",
            "time_category": "standard", "approved_rate": amount,
            "payment_status": payment_status, "billing_treatment": "billable",
        })
        return self.conn.execute("SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)).fetchone()

    def test_new_session_defaults_to_unpaid(self):
        """New sessions should default to 'unpaid', not 'unresolved'."""
        import_rows(self.conn, [raw_row("default", "Pat Client | 60 | Office", "2026-05-15T10:00:00-04:00")], "test")
        session = self.conn.execute(
            "SELECT payment_status FROM sessions s JOIN calendar_event_candidates c ON s.candidate_id = c.id WHERE c.candidate_key = ?",
            (stable_hash("calendar_event_id:event-default"),),
        ).fetchone()
        self.assertEqual(session["payment_status"], "unpaid")

    def test_normalize_legacy_values(self):
        self.assertEqual(normalize_payment_status("paid"), "paid_at_session")
        self.assertEqual(normalize_payment_status("unresolved"), "unpaid")
        self.assertEqual(normalize_payment_status("partially_paid"), "unpaid")
        self.assertEqual(normalize_payment_status("waived"), "unpaid")
        self.assertEqual(normalize_payment_status("not_billable"), "unpaid")
        self.assertEqual(normalize_payment_status(""), "unpaid")
        self.assertEqual(normalize_payment_status(None), "unpaid")
        self.assertEqual(normalize_payment_status("unpaid"), "unpaid")
        self.assertEqual(normalize_payment_status("paid_at_session"), "paid_at_session")

    def test_paid_at_session_excluded_from_invoicing(self):
        """Sessions marked paid_at_session should be ineligible for invoicing."""
        session = self._approved_session("paid1", payment_status="paid_at_session")
        reasons = invoice_ineligibility_reasons(self.conn, session)
        self.assertTrue(any("paid at time of session" in r.lower() for r in reasons))

    def test_unpaid_session_remains_eligible(self):
        """Unpaid sessions should remain invoice eligible."""
        session = self._approved_session("unpaid1", payment_status="unpaid")
        reasons = invoice_ineligibility_reasons(self.conn, session)
        self.assertEqual(reasons, [])

    def test_legacy_paid_normalized_to_paid_at_session(self):
        """Legacy 'paid' value should be normalized and block invoicing."""
        session = self._approved_session("legacy", payment_status="paid")
        self.assertEqual(session["payment_status"], "paid_at_session")
        reasons = invoice_ineligibility_reasons(self.conn, session)
        self.assertTrue(any("paid at time of session" in r.lower() for r in reasons))

    def test_payment_status_not_required_for_approval(self):
        """Payment status should not block review readiness."""
        session = self._approved_session("no_payment", payment_status="unpaid")
        self.assertEqual(session["review_status"], "approved")

    def test_draft_with_paid_at_session_session_fails(self):
        """Adding a paid_at_session session to a draft should fail."""
        session = self._approved_session("draft_fail", payment_status="paid_at_session")
        with self.assertRaises(ValueError):
            create_invoice_draft(self.conn, {
                "bill_to_party_id": self.party["billing_party_id"],
                "billing_period_start": "2026-05-01", "billing_period_end": "2026-05-31",
                "invoice_date": "2026-05-31", "session_ids": [session["id"]],
            })


class SafeFinalizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "test.sqlite3")
        init_db(self.conn)
        self.person = create_person(self.conn, {"first_name": "Robin", "last_name": "Test", "display_name": "Robin Test"})
        self.party = create_billing_party(self.conn, {
            "billing_name": "Robin Test", "person_id": self.person["person_id"],
            "billing_email": "robin@example.test", "billing_address_line_1": "5 Sample St",
            "billing_city": "Sample", "billing_state": "FL", "billing_postal_code": "00000",
            "preferred_delivery_method": "email",
        })
        save_business_profile(self.conn, {
            "business_name": "Sample Practice", "provider_display_name": "Sample Provider",
            "address_line_1": "200 Sample Ave", "city": "Sample", "state": "FL", "postal_code": "00000",
            "phone": "555-0200", "email": "billing@sample", "payee_name": "Sample Payee",
            "payment_address_line_1": "200 Sample Ave", "payment_city": "Sample", "payment_state": "FL",
            "payment_postal_code": "00000",
        })

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _approved_session(self, key, amount="150.00"):
        import_rows(self.conn, [raw_row(key, f"Robin Test | 60 | Office", f"2026-05-{10 + len(key):02d}T10:00:00-04:00")], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Robin Test"}],
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

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_preview_saves_draft_and_returns_revision(self, fake_pdf):
        """preview_finalization should save the draft and return a revision."""
        fake_pdf.return_value = "x" * 64
        session = self._approved_session("preview1")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        self.assertIn("preview_revision", preview)
        self.assertIsInstance(preview["preview_revision"], int)
        self.assertEqual(preview["invoice"]["status"], "draft")
        self.assertEqual(preview["invoice"]["invoice_id"], draft["invoice"]["invoice_id"])
        self.assertGreater(len(preview["lines"]), 0)
        self.assertIsNotNone(preview["business_profile"])
        self.assertIsNotNone(preview["billing_party"])

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_preview_with_data_updates_draft(self, fake_pdf):
        """preview_finalization with data should update the draft first."""
        fake_pdf.return_value = "x" * 64
        session = self._approved_session("preview2")
        draft = self._draft([session])
        preview = preview_finalization(
            self.conn, draft["invoice"]["invoice_id"],
            data={"delivery_method": "mail"},
        )
        self.assertEqual(preview["invoice"]["delivery_method"], "mail")

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_finalize_with_matching_revision_succeeds(self, fake_pdf):
        """Finalize with correct revision should succeed."""
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("finalize1")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        final = finalize_invoice(
            self.conn, draft["invoice"]["invoice_id"],
            expected_revision=preview["preview_revision"],
            pdf_root=self.root / "Invoices",
        )
        self.assertEqual(final["invoice"]["status"], "finalized")
        self.assertEqual(final["invoice"]["invoice_number"], "2026-0001")

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_finalize_with_stale_revision_rejected(self, fake_pdf):
        """Finalize with stale revision should be rejected."""
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("stale1")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        # Mutate the draft after preview
        update_invoice_draft(self.conn, draft["invoice"]["invoice_id"], {"delivery_method": "mail"})
        with self.assertRaises(ValueError) as ctx:
            finalize_invoice(
                self.conn, draft["invoice"]["invoice_id"],
                expected_revision=preview["preview_revision"],
                pdf_root=self.root / "Invoices",
            )
        self.assertIn("changed since preview", str(ctx.exception))

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_finalize_without_revision_still_works(self, fake_pdf):
        """Finalize without expected_revision should still work (backward compat)."""
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("norev")
        draft = self._draft([session])
        final = finalize_invoice(
            self.conn, draft["invoice"]["invoice_id"],
            pdf_root=self.root / "Invoices",
        )
        self.assertEqual(final["invoice"]["status"], "finalized")

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_double_finalization_rejected(self, fake_pdf):
        """Finalizing an already-finalized invoice should fail."""
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("double")
        draft = self._draft([session])
        finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
        with self.assertRaises(ValueError):
            finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_finalization_snapshot_matches_preview(self, fake_pdf):
        """Finalized invoice snapshots should match what was in the preview."""
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("snapshot")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        final = finalize_invoice(
            self.conn, draft["invoice"]["invoice_id"],
            expected_revision=preview["preview_revision"],
            pdf_root=self.root / "Invoices",
        )
        # Snapshots should match preview values
        self.assertEqual(final["invoice"]["bill_to_name_snapshot"], "Robin Test")
        self.assertEqual(final["invoice"]["business_name_snapshot"], "Sample Practice")
        self.assertEqual(final["invoice"]["total_cents"], preview["invoice"]["total_cents"])
        self.assertEqual(len(final["lines"]), len(preview["lines"]))
        for f_line, p_line in zip(final["lines"], preview["lines"]):
            self.assertEqual(f_line["line_amount_cents"], p_line["line_amount_cents"])
            self.assertEqual(f_line["description_snapshot"], p_line["description_snapshot"])

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_validation_failure_leaves_draft_unchanged(self, fake_pdf):
        """If finalization fails validation, invoice should remain a draft."""
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("valfail")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        # Make session ineligible after preview
        self.conn.execute("UPDATE sessions SET review_status = 'needs_review' WHERE id = ?", (session["id"],))
        self.conn.commit()
        with self.assertRaises(ValueError):
            finalize_invoice(
                self.conn, draft["invoice"]["invoice_id"],
                expected_revision=preview["preview_revision"],
                pdf_root=self.root / "Invoices",
            )
        # Invoice should still be a draft
        result = get_invoice(self.conn, draft["invoice"]["invoice_id"])
        self.assertEqual(result["invoice"]["status"], "draft")

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_void_after_finalize_prevents_reedit(self, fake_pdf):
        """Voiding a finalized invoice should prevent further edits."""
        fake_pdf.return_value = "b" * 64
        session = self._approved_session("void1")
        draft = self._draft([session])
        finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
        voided = void_invoice(self.conn, draft["invoice"]["invoice_id"], "Test void")
        self.assertEqual(voided["invoice"]["status"], "void")
        with self.assertRaises(ValueError):
            update_invoice_draft(self.conn, draft["invoice"]["invoice_id"], {"notes": "edit after void"})

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_revision_increments_on_draft_update(self, fake_pdf):
        """Revision should increment when draft is updated."""
        session = self._approved_session("rev1")
        draft = self._draft([session])
        initial_rev = draft["invoice"]["revision"]
        update_invoice_draft(self.conn, draft["invoice"]["invoice_id"], {"delivery_method": "mail"})
        updated = get_invoice(self.conn, draft["invoice"]["invoice_id"])
        self.assertEqual(updated["invoice"]["revision"], initial_rev + 1)

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_revision_increments_on_add_sessions(self, fake_pdf):
        """Revision should increment when sessions are added."""
        session1 = self._approved_session("rev2a")
        draft = self._draft([session1])
        initial_rev = draft["invoice"]["revision"]
        session2 = self._approved_session("rev2b", amount="100.00")
        add_sessions_to_draft(self.conn, draft["invoice"]["invoice_id"], [session2["id"]])
        updated = get_invoice(self.conn, draft["invoice"]["invoice_id"])
        self.assertEqual(updated["invoice"]["revision"], initial_rev + 1)

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_revision_increments_on_remove_line(self, fake_pdf):
        """Revision should increment when a line is removed."""
        session1 = self._approved_session("rev3a")
        session2 = self._approved_session("rev3b", amount="100.00")
        draft = self._draft([session1, session2])
        initial_rev = draft["invoice"]["revision"]
        remove_line_from_draft(self.conn, draft["invoice"]["invoice_id"], draft["lines"][0]["invoice_line_item_id"])
        updated = get_invoice(self.conn, draft["invoice"]["invoice_id"])
        self.assertEqual(updated["invoice"]["revision"], initial_rev + 1)

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_preview_rejects_empty_draft(self, fake_pdf):
        """Preview should return readiness errors for a draft with no lines."""
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_period_start": "2026-05-01", "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31",
        })
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        self.assertIn("readiness", preview)
        self.assertFalse(preview["readiness"]["ready"])
        error_fields = {e["field"] for e in preview["readiness"]["errors"]}
        self.assertIn("lines", error_fields)


if __name__ == "__main__":
    unittest.main()
