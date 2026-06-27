"""Focused tests for invoice-readiness validation before finalization."""
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
    update_invoice_draft,
    validate_invoice_readiness,
    void_invoice,
)
from jordana_invoice.review_services import approve_candidate, create_billing_party, create_person, update_billing_party
from jordana_invoice.util import stable_hash


def raw_row(key, title, start):
    return {
        "ingested_at": "2026-08-20T12:00:00Z", "snapshot_key": key, "run_id": f"run-{key}",
        "batch_name": "readiness-test", "capture_window": "past_7_days", "captured_at": "2026-08-20T12:00:00Z",
        "source_device": "test", "timezone": "America/New_York", "calendar_event_id": f"event-{key}",
        "event_fingerprint": f"fp-{key}", "event_title": title, "start_at": start,
        "end_at": start[:11] + "11:00:00-04:00", "duration_minutes": "60", "calendar": "Jordana Work",
        "payload_version": "2", "raw_json": "{}",
    }


class InvoiceReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "readiness.sqlite3")
        init_db(self.conn)
        self.person = create_person(self.conn, {"first_name": "Dana", "last_name": "Ready", "display_name": "Dana Ready"})
        self.party = create_billing_party(self.conn, {
            "billing_name": "Dana Ready", "person_id": self.person["person_id"],
            "billing_email": "dana@example.test", "billing_address_line_1": "12 Ready Ln",
            "billing_city": "Readyville", "billing_state": "FL", "billing_postal_code": "00000",
            "preferred_delivery_method": "both",
        })
        save_business_profile(self.conn, {
            "business_name": "Ready Practice", "provider_display_name": "Ready Provider",
            "address_line_1": "200 Ready Ave", "city": "Readyville", "state": "FL", "postal_code": "00000",
            "phone": "555-0300", "email": "billing@ready", "payee_name": "Ready Payee",
            "payment_address_line_1": "200 Ready Ave", "payment_city": "Readyville", "payment_state": "FL",
            "payment_postal_code": "00000", "zelle_recipient": "ready@example.test",
        })

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _approved_session(self, key, amount="150.00"):
        import_rows(self.conn, [raw_row(key, f"Dana Ready | 60 | Office", f"2026-05-{10 + len(key):02d}T10:00:00-04:00")], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Dana Ready"}],
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

    # 1. Valid invoice can preview and finalize
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_valid_invoice_previews_and_finalizes(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("valid1")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        self.assertTrue(preview["readiness"]["ready"])
        self.assertEqual(preview["readiness"]["errors"], [])
        final = finalize_invoice(
            self.conn, draft["invoice"]["invoice_id"],
            expected_revision=preview["preview_revision"],
            pdf_root=self.root / "Invoices",
        )
        self.assertEqual(final["invoice"]["status"], "finalized")
        self.assertEqual(final["invoice"]["invoice_number"], "2026-0001")

    # 2. Missing bill-to blocks finalization
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_missing_bill_to_blocks_finalization(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("noto1")
        draft = self._draft([session])
        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.conn.execute("UPDATE invoices SET bill_to_party_id = 'nonexistent-uuid' WHERE invoice_id = ?", (draft["invoice"]["invoice_id"],))
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.commit()
        readiness = validate_invoice_readiness(self.conn, draft["invoice"]["invoice_id"])
        self.assertFalse(readiness["ready"])
        fields = {e["field"] for e in readiness["errors"]}
        self.assertIn("bill_to", fields)
        with self.assertRaises(ValueError):
            finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")

    # 3. No lines blocks finalization
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_no_lines_blocks_finalization(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_period_start": "2026-05-01", "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31",
        })
        readiness = validate_invoice_readiness(self.conn, draft["invoice"]["invoice_id"])
        self.assertFalse(readiness["ready"])
        fields = {e["field"] for e in readiness["errors"]}
        self.assertIn("lines", fields)
        with self.assertRaises(ValueError):
            finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")

    # 4. Invalid amount/date blocks finalization
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_invalid_amount_blocks_finalization(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("neg1")
        draft = self._draft([session])
        self.conn.execute("UPDATE invoice_line_items SET line_amount_cents = 0 WHERE invoice_id = ?", (draft["invoice"]["invoice_id"],))
        self.conn.commit()
        readiness = validate_invoice_readiness(self.conn, draft["invoice"]["invoice_id"])
        self.assertFalse(readiness["ready"])
        fields = {e["field"] for e in readiness["errors"]}
        self.assertIn("line_amount", fields)
        with self.assertRaises(ValueError):
            finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_invalid_date_blocks_finalization(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("baddate1")
        draft = self._draft([session])
        update_invoice_draft(self.conn, draft["invoice"]["invoice_id"], {"invoice_date": "not-a-date"})
        readiness = validate_invoice_readiness(self.conn, draft["invoice"]["invoice_id"])
        self.assertFalse(readiness["ready"])
        fields = {e["field"] for e in readiness["errors"]}
        self.assertIn("invoice_date", fields)
        with self.assertRaises(ValueError):
            finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")

    # 5. Missing delivery-required email/address blocks finalization
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_missing_delivery_email_blocks_finalization(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("noemail1")
        # Create a party with no email
        party2 = create_billing_party(self.conn, {
            "billing_name": "No Email Party", "person_id": self.person["person_id"],
            "billing_email": None, "billing_address_line_1": "99 No Email St",
            "billing_city": "NoEmail", "billing_state": "FL", "billing_postal_code": "00000",
            "preferred_delivery_method": "email",
        })
        self.conn.execute("UPDATE sessions SET billing_party_id = ? WHERE id = ?", (party2["billing_party_id"], session["id"]))
        self.conn.commit()
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": party2["billing_party_id"],
            "billing_period_start": "2026-05-01", "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31", "session_ids": [session["id"]],
        })
        readiness = validate_invoice_readiness(self.conn, draft["invoice"]["invoice_id"])
        self.assertFalse(readiness["ready"])
        fields = {e["field"] for e in readiness["errors"]}
        self.assertIn("delivery_email", fields)
        with self.assertRaises(ValueError):
            finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_missing_delivery_address_blocks_finalization(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("noaddr1")
        # Create a party with no mailing address
        party2 = create_billing_party(self.conn, {
            "billing_name": "No Addr Party", "person_id": self.person["person_id"],
            "billing_email": "noaddr@example.test", "billing_address_line_1": None,
            "billing_city": None, "billing_state": None, "billing_postal_code": None,
            "preferred_delivery_method": "mail",
        })
        self.conn.execute("UPDATE sessions SET billing_party_id = ? WHERE id = ?", (party2["billing_party_id"], session["id"]))
        self.conn.commit()
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": party2["billing_party_id"],
            "billing_period_start": "2026-05-01", "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31", "session_ids": [session["id"]],
            "delivery_method": "mail",
        })
        readiness = validate_invoice_readiness(self.conn, draft["invoice"]["invoice_id"])
        self.assertFalse(readiness["ready"])
        fields = {e["field"] for e in readiness["errors"]}
        self.assertIn("delivery_address", fields)
        with self.assertRaises(ValueError):
            finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_unresolved_delivery_blocks_finalization(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("unresolved1")
        draft = self._draft([session])
        update_invoice_draft(self.conn, draft["invoice"]["invoice_id"], {"delivery_method": "unresolved"})
        readiness = validate_invoice_readiness(self.conn, draft["invoice"]["invoice_id"])
        self.assertFalse(readiness["ready"])
        fields = {e["field"] for e in readiness["errors"]}
        self.assertIn("delivery_method", fields)
        with self.assertRaises(ValueError):
            finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_blank_zelle_blocks_finalization(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("zelle1")
        draft = self._draft([session])
        self.conn.execute("UPDATE business_profile SET zelle_recipient = '   ' WHERE active = 1")
        self.conn.commit()
        readiness = validate_invoice_readiness(self.conn, draft["invoice"]["invoice_id"])
        self.assertFalse(readiness["ready"])
        fields = {e["field"] for e in readiness["errors"]}
        self.assertIn("zelle_recipient", fields)
        with self.assertRaises(ValueError):
            finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")

    # 6. Incomplete business profile blocks finalization
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_incomplete_business_profile_blocks_finalization(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("noprofile1")
        draft = self._draft([session])
        # Remove payee_name and payment_address to make profile incomplete
        self.conn.execute("UPDATE business_profile SET payee_name = NULL, payment_address_line_1 = NULL WHERE active = 1")
        self.conn.commit()
        readiness = validate_invoice_readiness(self.conn, draft["invoice"]["invoice_id"])
        self.assertFalse(readiness["ready"])
        fields = {e["field"] for e in readiness["errors"]}
        self.assertIn("payee_name", fields)
        self.assertIn("payment_address", fields)
        with self.assertRaises(ValueError):
            finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")

    # 7. Ineligible session blocks finalization
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_ineligible_session_blocks_finalization(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("inelig1")
        draft = self._draft([session])
        # Make session ineligible by changing review status
        self.conn.execute("UPDATE sessions SET review_status = 'needs_review' WHERE id = ?", (session["id"],))
        self.conn.commit()
        readiness = validate_invoice_readiness(self.conn, draft["invoice"]["invoice_id"])
        self.assertFalse(readiness["ready"])
        fields = {e["field"] for e in readiness["errors"]}
        self.assertIn("session", fields)
        with self.assertRaises(ValueError):
            finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")

    # 8. Stale revision still blocks finalization
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_stale_revision_blocks_finalization(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("stale1")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        self.assertTrue(preview["readiness"]["ready"])
        # Mutate the draft after preview
        update_invoice_draft(self.conn, draft["invoice"]["invoice_id"], {"delivery_method": "mail"})
        with self.assertRaises(ValueError) as ctx:
            finalize_invoice(
                self.conn, draft["invoice"]["invoice_id"],
                expected_revision=preview["preview_revision"],
                pdf_root=self.root / "Invoices",
            )
        self.assertIn("changed since preview", str(ctx.exception))

    # 9. Failed readiness leaves invoice as draft
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_failed_readiness_leaves_invoice_as_draft(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("faildraft1")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        self.assertTrue(preview["readiness"]["ready"])
        # Make session ineligible after preview
        self.conn.execute("UPDATE sessions SET review_status = 'needs_review' WHERE id = ?", (session["id"],))
        self.conn.commit()
        with self.assertRaises(ValueError):
            finalize_invoice(
                self.conn, draft["invoice"]["invoice_id"],
                expected_revision=preview["preview_revision"],
                pdf_root=self.root / "Invoices",
            )
        result = get_invoice(self.conn, draft["invoice"]["invoice_id"])
        self.assertEqual(result["invoice"]["status"], "draft")

    # 10. Existing void/reissue still works
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_void_and_reissue_still_works(self, fake_pdf):
        fake_pdf.return_value = "b" * 64
        session = self._approved_session("void1")
        first = self._draft([session])
        preview = preview_finalization(self.conn, first["invoice"]["invoice_id"])
        self.assertTrue(preview["readiness"]["ready"])
        finalized = finalize_invoice(
            self.conn, first["invoice"]["invoice_id"],
            expected_revision=preview["preview_revision"],
            pdf_root=self.root / "Invoices",
        )
        self.assertEqual(finalized["invoice"]["invoice_number"], "2026-0001")
        voided = void_invoice(self.conn, first["invoice"]["invoice_id"], "Test void")
        self.assertEqual(voided["invoice"]["status"], "void")
        second = self._draft([session])
        preview2 = preview_finalization(self.conn, second["invoice"]["invoice_id"])
        self.assertTrue(preview2["readiness"]["ready"])
        reissued = finalize_invoice(
            self.conn, second["invoice"]["invoice_id"],
            expected_revision=preview2["preview_revision"],
            pdf_root=self.root / "Invoices",
        )
        self.assertEqual(reissued["invoice"]["invoice_number"], "2026-0002")

    # 11. Immutability after finalization
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_finalized_invoice_is_immutable(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("imm1")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        finalize_invoice(
            self.conn, draft["invoice"]["invoice_id"],
            expected_revision=preview["preview_revision"],
            pdf_root=self.root / "Invoices",
        )
        with self.assertRaises(ValueError):
            update_invoice_draft(self.conn, draft["invoice"]["invoice_id"], {"notes": "late edit"})

    # 12. Preview includes readiness field
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_preview_includes_readiness(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("prev1")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        self.assertIn("readiness", preview)
        self.assertTrue(preview["readiness"]["ready"])
        self.assertEqual(preview["readiness"]["errors"], [])
        self.assertEqual(preview["readiness"]["preview_revision"], preview["preview_revision"])

    # 13. Active setup email satisfies invoice readiness
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_active_setup_email_satisfies_readiness(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("actemail1")
        party = create_billing_party(self.conn, {
            "billing_name": "Active Email Party", "person_id": self.person["person_id"],
            "billing_email": "active@example.test",
            "preferred_delivery_method": "email",
        })
        self.conn.execute("UPDATE sessions SET billing_party_id = ? WHERE id = ?", (party["billing_party_id"], session["id"]))
        self.conn.commit()
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": party["billing_party_id"],
            "billing_period_start": "2026-05-01", "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31", "session_ids": [session["id"]],
        })
        readiness = validate_invoice_readiness(self.conn, draft["invoice"]["invoice_id"])
        self.assertTrue(readiness["ready"])

    # 14. Inactive setup email does not satisfy readiness
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_inactive_setup_email_does_not_satisfy_readiness(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("inact1")
        party = create_billing_party(self.conn, {
            "billing_name": "Will Be Inactive", "person_id": self.person["person_id"],
            "billing_email": "inactive@example.test",
            "preferred_delivery_method": "email",
        })
        self.conn.execute("UPDATE sessions SET billing_party_id = ? WHERE id = ?", (party["billing_party_id"], session["id"]))
        self.conn.commit()
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": party["billing_party_id"],
            "billing_period_start": "2026-05-01", "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31", "session_ids": [session["id"]],
        })
        update_billing_party(self.conn, party["billing_party_id"], {"active": False})
        readiness = validate_invoice_readiness(self.conn, draft["invoice"]["invoice_id"])
        self.assertFalse(readiness["ready"])
        fields = {e["field"] for e in readiness["errors"]}
        self.assertIn("bill_to", fields)

    # 15. Active blank plus inactive populated still blocks finalization
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_active_blank_plus_inactive_populated_still_blocks(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("dup1")
        active_party = create_billing_party(self.conn, {
            "billing_name": "Fred Colin",
            "person_id": self.person["person_id"],
            "billing_email": None,
            "preferred_delivery_method": "email",
        })
        inactive_party = create_billing_party(self.conn, {
            "billing_name": "Fred Colin",
            "person_id": self.person["person_id"],
            "billing_email": "fred@example.test",
            "preferred_delivery_method": "email",
        })
        update_billing_party(self.conn, inactive_party["billing_party_id"], {"active": False})
        self.conn.execute("UPDATE sessions SET billing_party_id = ? WHERE id = ?", (active_party["billing_party_id"], session["id"]))
        self.conn.commit()
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": active_party["billing_party_id"],
            "billing_period_start": "2026-05-01", "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31", "session_ids": [session["id"]],
        })
        readiness = validate_invoice_readiness(self.conn, draft["invoice"]["invoice_id"])
        self.assertFalse(readiness["ready"])
        fields = {e["field"] for e in readiness["errors"]}
        self.assertIn("delivery_email", fields)
        messages = " ".join(e["message"] for e in readiness["errors"])
        self.assertIn("active billing setup", messages)

    # 16. Readiness message references active billing setup
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_readiness_message_references_active_billing_setup(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("msg1")
        party_no_email = create_billing_party(self.conn, {
            "billing_name": "No Email", "person_id": self.person["person_id"],
            "billing_email": None,
            "preferred_delivery_method": "email",
        })
        self.conn.execute("UPDATE sessions SET billing_party_id = ? WHERE id = ?", (party_no_email["billing_party_id"], session["id"]))
        self.conn.commit()
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": party_no_email["billing_party_id"],
            "billing_period_start": "2026-05-01", "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31", "session_ids": [session["id"]],
        })
        readiness = validate_invoice_readiness(self.conn, draft["invoice"]["invoice_id"])
        self.assertFalse(readiness["ready"])
        email_errors = [e for e in readiness["errors"] if e["field"] == "delivery_email"]
        self.assertTrue(email_errors)
        self.assertIn("active billing setup", email_errors[0]["message"])


if __name__ == "__main__":
    unittest.main()
