"""Tests for backend payment-ledger services and financial invariants."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, migrate_database
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import (
    create_invoice_draft,
    finalize_invoice,
    invoice_ineligibility_reasons,
    preview_finalization,
    save_business_profile,
)
from jordana_invoice.payment_services import (
    allocate_payment_to_session,
    create_payment,
    get_payment_detail,
    invoice_line_paid_amount,
    link_session_allocations_to_invoice_line,
    payment_allocated_amount,
    payment_unapplied_amount,
    reverse_allocation,
    session_paid_amount,
    void_payment,
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


class PaymentServicesTests(unittest.TestCase):
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
        self.person2 = create_person(self.conn, {"first_name": "Robin", "last_name": "Other", "display_name": "Robin Other"})
        self.party2 = create_billing_party(self.conn, {
            "billing_name": "Robin Other", "person_id": self.person2["person_id"],
            "billing_email": "robin@example.test", "billing_address_line_1": "5 Sample St",
            "billing_city": "Sample", "billing_state": "FL", "billing_postal_code": "00000",
            "preferred_delivery_method": "email",
        })
        save_business_profile(self.conn, {
            "business_name": "Test Practice", "provider_display_name": "Test Provider",
            "address_line_1": "100 Test Ave", "city": "Test", "state": "FL", "postal_code": "00000",
            "phone": "555-0100", "email": "billing@test", "payee_name": "Test Payee",
            "payment_address_line_1": "100 Test Ave", "payment_city": "Test", "payment_state": "FL",
            "payment_postal_code": "00000", "zelle_recipient": "demo-zelle@example.test",
        })
        self.session = self._approved_session("s1")
        self.session_id = self.session["id"]

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _approved_session(self, key, party_id=None, amount="150.00"):
        pid = party_id or self.party["billing_party_id"]
        import_rows(self.conn, [raw_row(key, "Pat Client | 60 | Office", f"2026-05-10T10:00:00-04:00")], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Pat Client"}],
            "billing_party_id": pid,
            "approved_duration_minutes": 60, "service_mode": "office",
            "time_category": "standard", "approved_rate": amount,
            "payment_status": "unpaid", "billing_treatment": "billable",
        })
        return self.conn.execute("SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)).fetchone()

    def _draft_and_finalize(self, session_id):
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_period_start": "2026-05-01", "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31", "session_ids": [session_id],
        })
        with patch("jordana_invoice.invoice_services.generate_invoice_pdf") as fake_pdf:
            fake_pdf.return_value = "x" * 64
            preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
            final = finalize_invoice(
                self.conn, draft["invoice"]["invoice_id"],
                expected_revision=preview["preview_revision"],
                pdf_root=self.root / "Invoices",
            )
        return final

    def _get_invoice_line_id(self, session_id):
        return self.conn.execute(
            "SELECT invoice_line_item_id FROM invoice_line_items WHERE source_session_id = ?", (session_id,)
        ).fetchone()["invoice_line_item_id"]

    # 1. Create a valid posted payment
    def test_create_valid_payment(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=15000, received_at="2026-05-10T12:00:00Z")
        self.assertEqual(p["status"], "posted")
        self.assertEqual(p["amount_cents"], 15000)
        self.assertEqual(p["method"], "other")

    # 2. Invalid Bill To party is rejected
    def test_invalid_bill_to_party_rejected(self):
        with self.assertRaises(ValueError):
            create_payment(self.conn, billing_party_id="nonexistent", amount_cents=100, received_at="2026-05-10")

    # 3. Zero and negative payment amounts are rejected
    def test_zero_and_negative_payment_rejected(self):
        with self.assertRaises(ValueError):
            create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=0, received_at="2026-05-10")
        with self.assertRaises(ValueError):
            create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=-100, received_at="2026-05-10")

    # 4. Invalid received date is rejected
    def test_invalid_received_date_rejected(self):
        with self.assertRaises(ValueError):
            create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=100, received_at="")
        with self.assertRaises(ValueError):
            create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=100, received_at=None)

    # 5. Allocate a full payment to one session
    def test_allocate_full_payment_to_one_session(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=15000, received_at="2026-05-10")
        a = allocate_payment_to_session(self.conn, payment_id=p["payment_id"],
                                        session_id=self.session_id, amount_cents=15000)
        self.assertEqual(a["status"], "active")
        self.assertEqual(payment_unapplied_amount(self.conn, p["payment_id"]), 0)

    # 6. Allocate one payment across multiple sessions
    def test_allocate_across_multiple_sessions(self):
        s2 = self._approved_session("s2")["id"]
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=30000, received_at="2026-05-10")
        allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=15000)
        allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=s2, amount_cents=15000)
        self.assertEqual(payment_allocated_amount(self.conn, p["payment_id"]), 30000)

    # 7. Apply multiple payments to one session
    def test_multiple_payments_to_one_session(self):
        p1 = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=10000, received_at="2026-05-10")
        p2 = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=5000, received_at="2026-05-11")
        allocate_payment_to_session(self.conn, payment_id=p1["payment_id"], session_id=self.session_id, amount_cents=10000)
        allocate_payment_to_session(self.conn, payment_id=p2["payment_id"], session_id=self.session_id, amount_cents=5000)
        self.assertEqual(session_paid_amount(self.conn, self.session_id), 15000)

    # 8. Partial allocation leaves unapplied money
    def test_partial_allocation_leaves_unapplied(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=20000, received_at="2026-05-10")
        allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=10000)
        self.assertEqual(payment_unapplied_amount(self.conn, p["payment_id"]), 10000)

    # 9. Allocation cannot exceed payment amount
    def test_allocation_exceeds_payment_rejected(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=10000, received_at="2026-05-10")
        with self.assertRaises(ValueError):
            allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=15000)

    # 10. Allocation cannot exceed session charge
    def test_allocation_exceeds_session_charge_rejected(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=50000, received_at="2026-05-10")
        with self.assertRaises(ValueError):
            allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=20000)

    # 11. Payment and session Bill To mismatch is rejected
    def test_bill_to_mismatch_rejected(self):
        s2 = self._approved_session("s2m", party_id=self.party2["billing_party_id"])
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        with self.assertRaises(ValueError):
            allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=s2["id"], amount_cents=100)

    # 12. Allocation to a valid pre-staging session with no invoice line succeeds
    def test_pre_staging_allocation_succeeds(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        a = allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=15000)
        self.assertIsNone(a["invoice_line_item_id"])

    # 13. Supplied invoice line must belong to the same session
    def test_invoice_line_wrong_session_rejected(self):
        s2 = self._approved_session("s2l")
        draft2 = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_period_start": "2026-05-01", "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31", "session_ids": [s2["id"]],
        })
        line2 = self.conn.execute(
            "SELECT invoice_line_item_id FROM invoice_line_items WHERE invoice_id = ? AND source_session_id = ?",
            (draft2["invoice"]["invoice_id"], s2["id"]),
        ).fetchone()["invoice_line_item_id"]
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=100, received_at="2026-05-10")
        with self.assertRaises(ValueError):
            allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=100, invoice_line_item_id=line2)

    # 14. Supplied invoice line must belong to the same Bill To party
    def test_invoice_line_wrong_bill_to_rejected(self):
        s2 = self._approved_session("s2bt", party_id=self.party2["billing_party_id"])
        draft2 = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party2["billing_party_id"],
            "billing_period_start": "2026-05-01", "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31", "session_ids": [s2["id"]],
        })
        line2 = self.conn.execute(
            "SELECT invoice_line_item_id FROM invoice_line_items WHERE invoice_id = ? AND source_session_id = ?",
            (draft2["invoice"]["invoice_id"], s2["id"]),
        ).fetchone()["invoice_line_item_id"]
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=100, received_at="2026-05-10")
        with self.assertRaises(ValueError):
            allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=100, invoice_line_item_id=line2)

    # 15. Allocation to a finalized invoice line is allowed without modifying the invoice line
    def test_allocation_to_finalized_invoice_line_allowed(self):
        final = self._draft_and_finalize(self.session_id)
        self.assertEqual(final["invoice"]["status"], "finalized")
        line_id = self._get_invoice_line_id(self.session_id)
        line_before = dict(self.conn.execute("SELECT * FROM invoice_line_items WHERE invoice_line_item_id = ?", (line_id,)).fetchone())
        inv_before = dict(self.conn.execute("SELECT * FROM invoices WHERE invoice_id = ?", (final["invoice"]["invoice_id"],)).fetchone())
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        a = allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=15000, invoice_line_item_id=line_id)
        line_after = dict(self.conn.execute("SELECT * FROM invoice_line_items WHERE invoice_line_item_id = ?", (line_id,)).fetchone())
        inv_after = dict(self.conn.execute("SELECT * FROM invoices WHERE invoice_id = ?", (final["invoice"]["invoice_id"],)).fetchone())
        self.assertEqual(line_before, line_after)
        self.assertEqual(inv_before, inv_after)
        self.assertEqual(a["invoice_line_item_id"], line_id)

    # 16. Linking pre-staging allocations to a later invoice line succeeds
    def test_link_pre_staging_allocations(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=15000)
        self._draft_and_finalize(self.session_id)
        line_id = self._get_invoice_line_id(self.session_id)
        result = link_session_allocations_to_invoice_line(self.conn, session_id=self.session_id, invoice_line_item_id=line_id)
        self.assertEqual(result["linked_count"], 1)
        alloc = self.conn.execute("SELECT invoice_line_item_id FROM payment_allocations WHERE allocation_id = ?", (result["linked_ids"][0],)).fetchone()
        self.assertEqual(alloc["invoice_line_item_id"], line_id)

    # 17. Repeating link operation is idempotent
    def test_link_idempotent(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=15000)
        self._draft_and_finalize(self.session_id)
        line_id = self._get_invoice_line_id(self.session_id)
        link_session_allocations_to_invoice_line(self.conn, session_id=self.session_id, invoice_line_item_id=line_id)
        result2 = link_session_allocations_to_invoice_line(self.conn, session_id=self.session_id, invoice_line_item_id=line_id)
        self.assertEqual(result2["linked_count"], 0)

    # 18. Reversing an allocation preserves the row and removes it from active totals
    def test_reverse_allocation_preserves_row(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        a = allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=15000)
        reversed_alloc = reverse_allocation(self.conn, a["allocation_id"])
        self.assertEqual(reversed_alloc["status"], "reversed")
        self.assertIsNotNone(reversed_alloc["reversed_at"])
        self.assertEqual(session_paid_amount(self.conn, self.session_id), 0)
        self.assertEqual(payment_unapplied_amount(self.conn, p["payment_id"]), 15000)

    # 19. Voiding a payment with active allocations is rejected
    def test_void_with_active_allocations_rejected(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=15000)
        with self.assertRaises(ValueError):
            void_payment(self.conn, p["payment_id"])

    # 20. Payment can be voided after allocations are reversed
    def test_void_after_reversing_allocations(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        a = allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=15000)
        reverse_allocation(self.conn, a["allocation_id"])
        voided = void_payment(self.conn, p["payment_id"])
        self.assertEqual(voided["status"], "void")
        self.assertIsNotNone(voided["voided_at"])

    # 21. Void payment contributes zero to active paid totals
    def test_void_payment_zero_paid(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        a = allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=15000)
        reverse_allocation(self.conn, a["allocation_id"])
        void_payment(self.conn, p["payment_id"])
        self.assertEqual(session_paid_amount(self.conn, self.session_id), 0)
        self.assertEqual(payment_allocated_amount(self.conn, p["payment_id"]), 0)
        self.assertEqual(payment_unapplied_amount(self.conn, p["payment_id"]), 0)

    # 22. Concurrent or repeated allocations cannot over-allocate the payment
    def test_cannot_over_allocate_payment(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=10000)
        with self.assertRaises(ValueError):
            allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=10000)

    # 23. Concurrent or repeated allocations cannot over-allocate the session charge
    def test_cannot_over_allocate_session(self):
        p1 = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=20000, received_at="2026-05-10")
        p2 = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=20000, received_at="2026-05-11")
        allocate_payment_to_session(self.conn, payment_id=p1["payment_id"], session_id=self.session_id, amount_cents=10000)
        allocate_payment_to_session(self.conn, payment_id=p2["payment_id"], session_id=self.session_id, amount_cents=5000)
        with self.assertRaises(ValueError):
            allocate_payment_to_session(self.conn, payment_id=p1["payment_id"], session_id=self.session_id, amount_cents=1)

    # 24. Payment detail reports allocated and unapplied amounts correctly
    def test_payment_detail_amounts(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=20000, received_at="2026-05-10")
        allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=12000)
        detail = get_payment_detail(self.conn, p["payment_id"])
        self.assertEqual(detail["allocated_cents"], 12000)
        self.assertEqual(detail["unapplied_cents"], 8000)
        self.assertEqual(len(detail["allocations"]), 1)

    # 25. Audit records are created without private text
    def test_audit_records_no_private_text(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=15000, received_at="2026-05-10",
                           reference_number="CHECK-123", received_from_name="Grandma",
                           administrative_note="Private note about patient")
        a = allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=15000)
        reverse_allocation(self.conn, a["allocation_id"])
        void_payment(self.conn, p["payment_id"])
        audits = self.conn.execute(
            "SELECT entity_type, entity_id, action, details FROM audit_log WHERE entity_type IN ('payment', 'payment_allocation') ORDER BY created_at"
        ).fetchall()
        actions = [r["action"] for r in audits]
        self.assertIn("payment_created", actions)
        self.assertIn("allocation_created", actions)
        self.assertIn("allocation_reversed", actions)
        self.assertIn("payment_voided", actions)
        for r in audits:
            details_str = r["details"] or ""
            self.assertNotIn("CHECK-123", details_str)
            self.assertNotIn("Grandma", details_str)
            self.assertNotIn("Private note", details_str)
            self.assertNotIn("patient", details_str.lower())

    # 26. Existing invoice eligibility and paid-at-session exclusion remain unchanged
    def test_paid_at_session_exclusion_unchanged(self):
        import_rows(self.conn, [raw_row("paid1", "Pat Client | 60 | Office", "2026-05-15T10:00:00-04:00")], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash("calendar_event_id:event-paid1"),),
        ).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Pat Client"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60, "service_mode": "office",
            "time_category": "standard", "approved_rate": "150.00",
            "payment_status": "paid_at_session", "billing_treatment": "billable",
        })
        paid_session = self.conn.execute("SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)).fetchone()
        reasons = invoice_ineligibility_reasons(self.conn, paid_session)
        self.assertTrue(any("paid at time of session" in r.lower() for r in reasons))
        unpaid_reasons = invoice_ineligibility_reasons(self.conn, self.session)
        self.assertEqual(unpaid_reasons, [])


if __name__ == "__main__":
    unittest.main()
