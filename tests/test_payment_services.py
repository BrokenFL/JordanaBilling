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
    void_invoice,
)
from jordana_invoice.payment_services import (
    allocate_payment_to_session,
    client_account_summary,
    create_payment,
    get_payment_detail,
    get_payment_detail_view,
    invoice_line_paid_amount,
    link_session_allocations_to_invoice_line,
    list_all_payments,
    list_invoice_payment_history,
    list_outstanding_invoices,
    list_paid_invoices,
    list_payment_service_period_options,
    apply_available_funds,
    get_payment_correction_history,
    payment_allocated_amount,
    payment_unapplied_amount,
    record_invoice_payment,
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
        return self._approved_session_at(key, "2026-05-10T10:00:00-04:00", party_id=party_id, amount=amount)

    def _approved_session_at(self, key, start_at, party_id=None, amount="150.00"):
        pid = party_id or self.party["billing_party_id"]
        import_rows(self.conn, [raw_row(key, "Pat Client | 60 | Office", start_at)], "test")
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

    def _approved_session_for(self, key, start_at, person, party, amount="150.00", payment_status="unpaid"):
        import_rows(self.conn, [raw_row(key, f"{person['display_name']} | 60 | Office", start_at)], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        payload = {
            "participants": [{"person_id": person["person_id"], "display_name": person["display_name"]}],
            "billing_party_id": party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": amount,
            "payment_status": payment_status,
            "billing_treatment": "billable",
        }
        if payment_status == "paid_at_session":
            payload.update({
                "amount_received": amount,
                "payment_date": start_at[:10],
                "payment_method": "zelle",
            })
        detail = approve_candidate(self.conn, candidate_id, payload)
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
        reversed_alloc = reverse_allocation(self.conn, a["allocation_id"], reason="Duplicate allocation")
        self.assertEqual(reversed_alloc["status"], "reversed")
        self.assertIsNotNone(reversed_alloc["reversed_at"])
        self.assertEqual(reversed_alloc["reversal_reason"], "Duplicate allocation")
        self.assertEqual(session_paid_amount(self.conn, self.session_id), 0)
        self.assertEqual(payment_unapplied_amount(self.conn, p["payment_id"]), 15000)

    # 19. Voiding a payment with active allocations is rejected
    def test_void_with_active_allocations_rejected(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=15000)
        with self.assertRaises(ValueError):
            void_payment(self.conn, p["payment_id"], reason="Test void")

    # 20. Payment can be voided after allocations are reversed
    def test_void_after_reversing_allocations(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        a = allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=15000)
        reverse_allocation(self.conn, a["allocation_id"], reason="Correcting error")
        voided = void_payment(self.conn, p["payment_id"], reason="Payment was reversed")
        self.assertEqual(voided["status"], "void")
        self.assertIsNotNone(voided["voided_at"])
        self.assertEqual(voided["void_reason"], "Payment was reversed")

    # 21. Void payment contributes zero to active paid totals
    def test_void_payment_zero_paid(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        a = allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=15000)
        reverse_allocation(self.conn, a["allocation_id"], reason="Correcting error")
        void_payment(self.conn, p["payment_id"], reason="Payment was reversed")
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
        reverse_allocation(self.conn, a["allocation_id"], reason="Audit test reversal")
        void_payment(self.conn, p["payment_id"], reason="Audit test void")
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
            "amount_received": "150.00", "payment_date": "2026-05-15", "payment_method": "zelle",
        })
        paid_session = self.conn.execute("SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)).fetchone()
        reasons = invoice_ineligibility_reasons(self.conn, paid_session)
        self.assertTrue(any("paid at time of session" in r.lower() for r in reasons))
        unpaid_reasons = invoice_ineligibility_reasons(self.conn, self.session)
        self.assertEqual(unpaid_reasons, [])

    def test_outstanding_invoices_include_unpaid_and_partial_only(self):
        unpaid_session = self._approved_session_at("ou1", "2026-05-11T10:00:00-04:00", amount="200.00")
        partial_session = self._approved_session_at("ou2", "2026-05-12T10:00:00-04:00", amount="300.00")
        paid_session = self._approved_session_at("ou3", "2026-05-13T10:00:00-04:00", amount="250.00")
        void_session = self._approved_session_at("ou4", "2026-05-14T10:00:00-04:00", amount="180.00")

        unpaid_invoice = self._draft_and_finalize(unpaid_session["id"])["invoice"]["invoice_id"]
        partial_invoice = self._draft_and_finalize(partial_session["id"])["invoice"]["invoice_id"]
        paid_invoice = self._draft_and_finalize(paid_session["id"])["invoice"]["invoice_id"]
        void_invoice_id = self._draft_and_finalize(void_session["id"])["invoice"]["invoice_id"]
        void_invoice(self.conn, void_invoice_id, "void test")

        p_partial = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=10000, received_at="2026-05-20", method="check")
        allocate_payment_to_session(
            self.conn,
            payment_id=p_partial["payment_id"],
            session_id=partial_session["id"],
            amount_cents=10000,
            invoice_line_item_id=self._get_invoice_line_id(partial_session["id"]),
        )

        p_paid = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=25000, received_at="2026-05-21", method="ach")
        paid_allocation = allocate_payment_to_session(
            self.conn,
            payment_id=p_paid["payment_id"],
            session_id=paid_session["id"],
            amount_cents=25000,
            invoice_line_item_id=self._get_invoice_line_id(paid_session["id"]),
        )
        reverse_allocation(self.conn, paid_allocation["allocation_id"], reason="Test reversal")
        p_void = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=25000, received_at="2026-05-21", method="ach")
        allocate_payment_to_session(
            self.conn,
            payment_id=p_void["payment_id"],
            session_id=paid_session["id"],
            amount_cents=25000,
            invoice_line_item_id=self._get_invoice_line_id(paid_session["id"]),
        )
        paid_active = self.conn.execute(
            "SELECT allocation_id FROM payment_allocations WHERE payment_id = ?",
            (p_void["payment_id"],),
        ).fetchone()["allocation_id"]
        reverse_allocation(self.conn, paid_active, reason="Test reversal")
        void_payment(self.conn, p_void["payment_id"], reason="Test void")
        p_paid_active = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=25000, received_at="2026-05-22", method="card")
        allocate_payment_to_session(
            self.conn,
            payment_id=p_paid_active["payment_id"],
            session_id=paid_session["id"],
            amount_cents=25000,
            invoice_line_item_id=self._get_invoice_line_id(paid_session["id"]),
        )

        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31",
            "session_ids": [self.session_id],
        })
        self.assertEqual(draft["invoice"]["status"], "draft")

        outstanding = list_outstanding_invoices(self.conn)
        ids = {row["invoice_id"] for row in outstanding}
        self.assertIn(unpaid_invoice, ids)
        self.assertIn(partial_invoice, ids)
        self.assertNotIn(paid_invoice, ids)
        self.assertNotIn(void_invoice_id, ids)
        partial_row = next(row for row in outstanding if row["invoice_id"] == partial_invoice)
        self.assertEqual(partial_row["paid_cents"], 10000)
        self.assertEqual(partial_row["balance_cents"], 20000)
        self.assertEqual(partial_row["payment_status"], "partially_paid")

    def test_record_invoice_payment_full_payment_succeeds(self):
        final = self._draft_and_finalize(self.session_id)
        result = record_invoice_payment(
            self.conn,
            invoice_id=final["invoice"]["invoice_id"],
            payment_date="2026-05-25",
            amount_cents=15000,
            payment_method="zelle",
            received_from_name="Pat Client",
        )
        self.assertFalse(result["duplicate_submission_ignored"])
        self.assertEqual(result["invoice"]["balance_cents"], 0)
        self.assertEqual(result["invoice"]["payment_status"], "paid")
        self.assertEqual(len(result["allocations"]), 1)

    def test_record_invoice_payment_partial_allocates_oldest_service_date_first(self):
        newer = self._approved_session_at("rp-new", "2026-05-12T10:00:00-04:00", amount="300.00")
        older = self._approved_session_at("rp-old", "2026-05-09T10:00:00-04:00", amount="200.00")
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31",
            "session_ids": [newer["id"], older["id"]],
        })
        with patch("jordana_invoice.invoice_services.generate_invoice_pdf") as fake_pdf:
            fake_pdf.return_value = "x" * 64
            preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
            final = finalize_invoice(
                self.conn,
                draft["invoice"]["invoice_id"],
                expected_revision=preview["preview_revision"],
                pdf_root=self.root / "Invoices",
            )

        result = record_invoice_payment(
            self.conn,
            invoice_id=final["invoice"]["invoice_id"],
            payment_date="2026-05-26",
            amount_cents=25000,
            payment_method="check",
        )
        allocated = {
            alloc["session_id"]: alloc["amount_cents"]
            for alloc in result["allocations"]
        }
        self.assertEqual(allocated[older["id"]], 20000)
        self.assertEqual(allocated[newer["id"]], 5000)
        self.assertEqual(result["invoice"]["paid_cents"], 25000)
        self.assertEqual(result["invoice"]["balance_cents"], 25000)

    def test_record_invoice_payment_rejects_invalid_states(self):
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31",
            "session_ids": [self.session_id],
        })
        with self.assertRaisesRegex(ValueError, "draft invoice"):
            record_invoice_payment(
                self.conn,
                invoice_id=draft["invoice"]["invoice_id"],
                payment_date="2026-05-25",
                amount_cents=100,
                payment_method="cash",
            )
        self.conn.execute("DELETE FROM invoice_line_items WHERE invoice_id = ?", (draft["invoice"]["invoice_id"],))
        self.conn.execute("DELETE FROM invoices WHERE invoice_id = ?", (draft["invoice"]["invoice_id"],))
        self.conn.commit()

        final_session = self._approved_session_at("reject-final", "2026-05-18T10:00:00-04:00", amount="150.00")
        final = self._draft_and_finalize(final_session["id"])
        with self.assertRaisesRegex(ValueError, "Payment amount must be greater than zero"):
            record_invoice_payment(
                self.conn,
                invoice_id=final["invoice"]["invoice_id"],
                payment_date="2026-05-25",
                amount_cents=0,
                payment_method="cash",
            )
        with self.assertRaisesRegex(ValueError, "Payment method is required"):
            record_invoice_payment(
                self.conn,
                invoice_id=final["invoice"]["invoice_id"],
                payment_date="2026-05-25",
                amount_cents=100,
                payment_method="",
            )
        with self.assertRaisesRegex(ValueError, "Payment Bill To party does not match"):
            record_invoice_payment(
                self.conn,
                invoice_id=final["invoice"]["invoice_id"],
                payment_date="2026-05-25",
                amount_cents=100,
                payment_method="cash",
                billing_party_id=self.party2["billing_party_id"],
            )
        with self.assertRaisesRegex(ValueError, "cannot exceed the current invoice balance"):
            record_invoice_payment(
                self.conn,
                invoice_id=final["invoice"]["invoice_id"],
                payment_date="2026-05-25",
                amount_cents=20000,
                payment_method="cash",
            )
        record_invoice_payment(
            self.conn,
            invoice_id=final["invoice"]["invoice_id"],
            payment_date="2026-05-25",
            amount_cents=15000,
            payment_method="cash",
        )
        with self.assertRaisesRegex(ValueError, "already fully paid"):
            record_invoice_payment(
                self.conn,
                invoice_id=final["invoice"]["invoice_id"],
                payment_date="2026-05-25",
                amount_cents=100,
                payment_method="cash",
            )
        voided_invoice = self._draft_and_finalize(self._approved_session_at("voidable", "2026-05-19T10:00:00-04:00")["id"])
        void_invoice(self.conn, voided_invoice["invoice"]["invoice_id"], "void")
        with self.assertRaisesRegex(ValueError, "void invoice"):
            record_invoice_payment(
                self.conn,
                invoice_id=voided_invoice["invoice"]["invoice_id"],
                payment_date="2026-05-25",
                amount_cents=100,
                payment_method="cash",
            )

    def test_record_invoice_payment_rolls_back_payment_and_allocations_on_failure(self):
        final = self._draft_and_finalize(self.session_id)
        payment_count_before = self.conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
        allocation_count_before = self.conn.execute("SELECT COUNT(*) FROM payment_allocations").fetchone()[0]
        with patch("jordana_invoice.payment_services._allocate_payment_to_session_locked", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                record_invoice_payment(
                    self.conn,
                    invoice_id=final["invoice"]["invoice_id"],
                    payment_date="2026-05-25",
                    amount_cents=15000,
                    payment_method="cash",
                )
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0], payment_count_before)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM payment_allocations").fetchone()[0], allocation_count_before)

    def test_record_invoice_payment_ignores_duplicate_submission(self):
        final = self._draft_and_finalize(self.session_id)
        first = record_invoice_payment(
            self.conn,
            invoice_id=final["invoice"]["invoice_id"],
            payment_date="2026-05-27",
            amount_cents=5000,
            payment_method="ach",
            reference_number="ACH-1",
            received_from_name="Pat Client",
            administrative_note="Front desk payment",
        )
        second = record_invoice_payment(
            self.conn,
            invoice_id=final["invoice"]["invoice_id"],
            payment_date="2026-05-27",
            amount_cents=5000,
            payment_method="ach",
            reference_number="ACH-1",
            received_from_name="Pat Client",
            administrative_note="Front desk payment",
        )
        self.assertFalse(first["duplicate_submission_ignored"])
        self.assertTrue(second["duplicate_submission_ignored"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0], 1)
        self.assertEqual(second["invoice"]["paid_cents"], 5000)
        self.assertEqual(second["invoice"]["balance_cents"], 10000)

    def test_list_invoice_payment_history_marks_void_and_reversed(self):
        final = self._draft_and_finalize(self.session_id)
        posted = record_invoice_payment(
            self.conn,
            invoice_id=final["invoice"]["invoice_id"],
            payment_date="2026-05-28",
            amount_cents=5000,
            payment_method="card",
        )
        reversed_payment = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=2500, received_at="2026-05-28", method="card")
        reversed_allocation = allocate_payment_to_session(
            self.conn,
            payment_id=reversed_payment["payment_id"],
            session_id=self.session_id,
            amount_cents=2500,
            invoice_line_item_id=self._get_invoice_line_id(self.session_id),
        )
        reverse_allocation(self.conn, reversed_allocation["allocation_id"], reason="Test reversal")
        voided_payment = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=2500, received_at="2026-05-28", method="check")
        voided_allocation = allocate_payment_to_session(
            self.conn,
            payment_id=voided_payment["payment_id"],
            session_id=self.session_id,
            amount_cents=2500,
            invoice_line_item_id=self._get_invoice_line_id(self.session_id),
        )
        reverse_allocation(self.conn, voided_allocation["allocation_id"], reason="Test reversal")
        void_payment(self.conn, voided_payment["payment_id"], reason="Test void")

        history = list_invoice_payment_history(self.conn, final["invoice"]["invoice_id"])
        statuses = {row["payment_id"]: row["payment_status"] for row in history["payments"]}
        self.assertEqual(statuses[posted["payment"]["payment_id"]], "posted")
        self.assertEqual(statuses[reversed_payment["payment_id"]], "reversed")
        self.assertEqual(statuses[voided_payment["payment_id"]], "void")

    # 33. list_paid_invoices returns fully-paid finalized invoices
    def test_list_paid_invoices(self):
        final = self._draft_and_finalize(self.session_id)
        invoice_id = final["invoice"]["invoice_id"]
        record_invoice_payment(self.conn, invoice_id=invoice_id, payment_date="2026-05-15", amount_cents=15000, payment_method="zelle")
        paid = list_paid_invoices(self.conn)
        self.assertEqual(len(paid), 1)
        self.assertEqual(paid[0]["invoice_id"], invoice_id)
        self.assertEqual(paid[0]["balance_cents"], 0)
        self.assertEqual(paid[0]["paid_cents"], 15000)
        self.assertIsNotNone(paid[0]["paid_date"])
        self.assertEqual(paid[0]["payment_method"], "zelle")
        self.assertEqual(paid[0]["invoice_period"], "2026-05")
        self.assertEqual(paid[0]["invoice_period_display"], "May 2026")

    # 34. list_paid_invoices excludes partially-paid and outstanding
    def test_list_paid_invoices_excludes_partial(self):
        final = self._draft_and_finalize(self.session_id)
        record_invoice_payment(self.conn, invoice_id=final["invoice"]["invoice_id"], payment_date="2026-05-15", amount_cents=5000, payment_method="cash")
        paid = list_paid_invoices(self.conn)
        self.assertEqual(len(paid), 0)

    # 35. list_all_payments returns the payment ledger
    def test_list_all_payments(self):
        final = self._draft_and_finalize(self.session_id)
        p1 = record_invoice_payment(self.conn, invoice_id=final["invoice"]["invoice_id"], payment_date="2026-05-15", amount_cents=10000, payment_method="zelle")
        p2 = create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=5000, received_at="2026-05-20", method="check")
        all_payments = list_all_payments(self.conn)
        self.assertEqual(len(all_payments), 2)
        ids = {p["payment_id"] for p in all_payments}
        self.assertIn(p1["payment"]["payment_id"], ids)
        self.assertIn(p2["payment_id"], ids)
        for p in all_payments:
            if p["payment_id"] == p1["payment"]["payment_id"]:
                self.assertEqual(p["amount_applied_cents"], 10000)
                self.assertEqual(p["bill_to_name"], "Pat Client")
            if p["payment_id"] == p2["payment_id"]:
                self.assertEqual(p["amount_applied_cents"], 0)
                self.assertEqual(p["status"], "posted")

    def test_payment_lists_filter_by_invoice_period_and_sort_by_first_name(self):
        robin_session = self._approved_session_for(
            "robin-filter",
            "2026-06-12T10:00:00-04:00",
            self.person2,
            self.party2,
        )
        pat_final = self._draft_and_finalize(self.session_id)
        robin_draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party2["billing_party_id"],
            "billing_period_start": "2026-06-01",
            "billing_period_end": "2026-06-30",
            "invoice_date": "2026-06-30",
            "session_ids": [robin_session["id"]],
        })
        with patch("jordana_invoice.invoice_services.generate_invoice_pdf") as fake_pdf:
            fake_pdf.return_value = "x" * 64
            preview = preview_finalization(self.conn, robin_draft["invoice"]["invoice_id"])
            robin_final = finalize_invoice(
                self.conn,
                robin_draft["invoice"]["invoice_id"],
                expected_revision=preview["preview_revision"],
                pdf_root=self.root / "Invoices",
            )

        outstanding = list_outstanding_invoices(self.conn)
        self.assertEqual([row["bill_to_display_name"] for row in outstanding], ["Pat Client", "Robin Other"])
        june = list_outstanding_invoices(self.conn, billing_month="2026-06")
        self.assertEqual([row["invoice_id"] for row in june], [robin_final["invoice"]["invoice_id"]])
        may = list_outstanding_invoices(self.conn, billing_month="2026-05")
        self.assertEqual([row["invoice_id"] for row in may], [pat_final["invoice"]["invoice_id"]])

        options = list_payment_service_period_options(self.conn)
        self.assertIn({"value": "2026-05", "label": "May 2026"}, options)
        self.assertIn({"value": "2026-06", "label": "June 2026"}, options)

    def test_paid_at_session_payments_appear_in_paid_and_all_payment_periods(self):
        paid_session = self._approved_session_for(
            "paid-session-list",
            "2026-06-18T10:00:00-04:00",
            self.person2,
            self.party2,
            payment_status="paid_at_session",
        )
        paid_rows = list_paid_invoices(self.conn, billing_month="2026-06")
        paid_at_session_rows = [row for row in paid_rows if row.get("row_type") == "paid_at_session"]
        self.assertEqual(len(paid_at_session_rows), 1)
        self.assertEqual(paid_at_session_rows[0]["session_id"], paid_session["id"])
        self.assertEqual(paid_at_session_rows[0]["bill_to_display_name"], "Robin Other")
        self.assertEqual(paid_at_session_rows[0]["invoice_number"], "Paid at session")
        self.assertEqual(paid_at_session_rows[0]["invoice_period_display"], "June 2026")

        ledger = list_all_payments(self.conn, billing_month="2026-06")
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["source_type"], "paid_at_session_backfill")
        self.assertEqual(ledger[0]["bill_to_name"], "Robin Other")

    # 36. get_payment_detail_view returns payment with invoice info
    def test_get_payment_detail_view(self):
        final = self._draft_and_finalize(self.session_id)
        result = record_invoice_payment(self.conn, invoice_id=final["invoice"]["invoice_id"], payment_date="2026-05-15", amount_cents=15000, payment_method="zelle")
        payment_id = result["payment"]["payment_id"]
        detail = get_payment_detail_view(self.conn, payment_id)
        self.assertEqual(detail["payment_id"], payment_id)
        self.assertEqual(detail["amount_cents"], 15000)
        self.assertEqual(detail["allocated_cents"], 15000)
        self.assertEqual(detail["unapplied_cents"], 0)
        self.assertEqual(len(detail["allocations"]), 1)
        alloc = detail["allocations"][0]
        self.assertIsNotNone(alloc["invoice_info"])
        self.assertEqual(alloc["invoice_info"]["invoice_number"], final["invoice"]["invoice_number"])

    # 37. client_account_summary returns correct totals
    def test_client_account_summary(self):
        final = self._draft_and_finalize(self.session_id)
        record_invoice_payment(self.conn, invoice_id=final["invoice"]["invoice_id"], payment_date="2026-05-15", amount_cents=10000, payment_method="zelle")
        summary = client_account_summary(self.conn, self.person["person_id"])
        self.assertEqual(summary["total_finalized_invoices"], 1)
        self.assertEqual(summary["total_billed_cents"], 15000)
        self.assertEqual(summary["total_paid_cents"], 10000)
        self.assertEqual(summary["current_balance_cents"], 5000)
        self.assertEqual(summary["account_status"], "Balance Due")

    # 38. client_account_summary shows Current when fully paid
    def test_client_account_summary_current(self):
        final = self._draft_and_finalize(self.session_id)
        record_invoice_payment(self.conn, invoice_id=final["invoice"]["invoice_id"], payment_date="2026-05-15", amount_cents=15000, payment_method="zelle")
        summary = client_account_summary(self.conn, self.person["person_id"])
        self.assertEqual(summary["current_balance_cents"], 0)
        self.assertEqual(summary["account_status"], "Current")

    # 39. client_account_summary for person with no invoices
    def test_client_account_summary_no_invoices(self):
        summary = client_account_summary(self.conn, self.person2["person_id"])
        self.assertEqual(summary["total_finalized_invoices"], 0)
        self.assertEqual(summary["total_billed_cents"], 0)
        self.assertEqual(summary["total_paid_cents"], 0)
        self.assertEqual(summary["current_balance_cents"], 0)
        self.assertEqual(summary["account_status"], "Current")


class PaymentCorrectionTests(unittest.TestCase):
    """Tests for payment correction features: reversal reason, void reason,
    idempotency keys, apply_available_funds, correction history, and
    expanded detail view."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        migrate_database(self.db_path)
        self.conn = connect(self.db_path)
        self.person = create_person(self.conn, {"first_name": "Alice", "last_name": "Test", "display_name": "Alice Test"})
        self.party = create_billing_party(self.conn, {
            "billing_name": "Alice Billing", "person_id": self.person["person_id"],
            "billing_email": "alice@example.test", "billing_address_line_1": "1 Test St",
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
        row = raw_row("s1", "Alice Test | 60 | Office", "2026-05-10T10:00:00-04:00")
        import_rows(self.conn, [row], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash("calendar_event_id:event-s1"),),
        ).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Alice Test"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60, "service_mode": "office",
            "time_category": "standard", "approved_rate": "150.00",
            "payment_status": "unpaid", "billing_treatment": "billable",
        })
        self.session_id = detail["session"]["id"]

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def _draft_and_finalize(self, session_id):
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_period_start": "2026-05-01", "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31", "session_ids": [session_id],
        })
        with patch("jordana_invoice.invoice_services.generate_invoice_pdf") as fake_pdf:
            fake_pdf.return_value = "x" * 64
            return finalize_invoice(
                self.conn, draft["invoice"]["invoice_id"],
            )

    # 40. reverse_allocation requires a reason
    def test_reverse_allocation_requires_reason(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        a = allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=15000)
        with self.assertRaises(ValueError) as ctx:
            reverse_allocation(self.conn, a["allocation_id"])
        self.assertIn("reversal reason", str(ctx.exception).lower())

    # 41. reverse_allocation stores reversal_reason
    def test_reverse_allocation_stores_reason(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        a = allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=15000)
        result = reverse_allocation(self.conn, a["allocation_id"], reason="Wrong session")
        self.assertEqual(result["reversal_reason"], "Wrong session")

    # 42. void_payment requires a reason
    def test_void_payment_requires_reason(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        with self.assertRaises(ValueError) as ctx:
            void_payment(self.conn, p["payment_id"])
        self.assertIn("void reason", str(ctx.exception).lower())

    # 43. void_payment stores void_reason
    def test_void_payment_stores_reason(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        result = void_payment(self.conn, p["payment_id"], reason="Bank error")
        self.assertEqual(result["void_reason"], "Bank error")

    # 44. Idempotency key prevents duplicate reversal
    def test_idempotency_key_prevents_duplicate_reversal(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        a = allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=15000)
        reverse_allocation(self.conn, a["allocation_id"], reason="Test", idempotency_key="key-1")
        with self.assertRaises(ValueError) as ctx:
            reverse_allocation(self.conn, a["allocation_id"], reason="Test", idempotency_key="key-1")
        self.assertIn("already been processed", str(ctx.exception))

    # 45. Idempotency key prevents duplicate void
    def test_idempotency_key_prevents_duplicate_void(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        void_payment(self.conn, p["payment_id"], reason="Test", idempotency_key="key-2")
        with self.assertRaises(ValueError) as ctx:
            void_payment(self.conn, p["payment_id"], reason="Test", idempotency_key="key-2")
        self.assertIn("already been processed", str(ctx.exception))

    # 46. Idempotency key is optional (no key = no dedup)
    def test_idempotency_key_optional(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        a = allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=15000)
        reverse_allocation(self.conn, a["allocation_id"], reason="Test")
        # Second call without key still raises because allocation is already reversed
        with self.assertRaises(ValueError):
            reverse_allocation(self.conn, a["allocation_id"], reason="Test")

    # 47. apply_available_funds creates new allocations
    def test_apply_available_funds_creates_allocations(self):
        final = self._draft_and_finalize(self.session_id)
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        result = apply_available_funds(self.conn, p["payment_id"], invoice_id=final["invoice"]["invoice_id"], amount_cents=15000)
        self.assertEqual(len(result["allocations"]), 1)
        self.assertEqual(result["invoice"]["balance_cents"], 0)
        self.assertEqual(result["payment"]["amount_cents"], 15000)

    # 48. apply_available_funds rejects non-posted payment
    def test_apply_funds_rejects_non_posted(self):
        final = self._draft_and_finalize(self.session_id)
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        void_payment(self.conn, p["payment_id"], reason="Voided")
        with self.assertRaises(ValueError) as ctx:
            apply_available_funds(self.conn, p["payment_id"], invoice_id=final["invoice"]["invoice_id"], amount_cents=15000)
        self.assertIn("not posted", str(ctx.exception))

    # 49. apply_available_funds rejects draft invoice
    def test_apply_funds_rejects_draft_invoice(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_period_start": "2026-05-01", "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31", "session_ids": [self.session_id],
        })
        with self.assertRaises(ValueError) as ctx:
            apply_available_funds(self.conn, p["payment_id"], invoice_id=draft["invoice"]["invoice_id"], amount_cents=15000)
        self.assertIn("finalized", str(ctx.exception))

    # 50. apply_available_funds rejects mismatched Bill To
    def test_apply_funds_rejects_mismatched_bill_to(self):
        final = self._draft_and_finalize(self.session_id)
        person2 = create_person(self.conn, {"display_name": "Bob Test"})
        party2 = create_billing_party(self.conn, {
            "billing_name": "Bob Billing",
            "person_id": person2["person_id"],
        })
        p = create_payment(self.conn, billing_party_id=party2["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        with self.assertRaises(ValueError) as ctx:
            apply_available_funds(self.conn, p["payment_id"], invoice_id=final["invoice"]["invoice_id"], amount_cents=15000)
        self.assertIn("Bill To", str(ctx.exception))

    # 51. apply_available_funds rejects amount exceeding available
    def test_apply_funds_rejects_excess_amount(self):
        final = self._draft_and_finalize(self.session_id)
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=10000, received_at="2026-05-10")
        with self.assertRaises(ValueError) as ctx:
            apply_available_funds(self.conn, p["payment_id"], invoice_id=final["invoice"]["invoice_id"], amount_cents=15000)
        self.assertIn("exceeds available", str(ctx.exception))

    # 52. apply_available_funds rejects zero/negative amount
    def test_apply_funds_rejects_zero_amount(self):
        final = self._draft_and_finalize(self.session_id)
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        with self.assertRaises(ValueError):
            apply_available_funds(self.conn, p["payment_id"], invoice_id=final["invoice"]["invoice_id"], amount_cents=0)
        with self.assertRaises(ValueError):
            apply_available_funds(self.conn, p["payment_id"], invoice_id=final["invoice"]["invoice_id"], amount_cents=-100)

    # 53. apply_available_funds with idempotency key prevents duplicate
    def test_apply_funds_idempotency(self):
        final = self._draft_and_finalize(self.session_id)
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=30000, received_at="2026-05-10")
        apply_available_funds(self.conn, p["payment_id"], invoice_id=final["invoice"]["invoice_id"], amount_cents=15000, idempotency_key="apply-1")
        with self.assertRaises(ValueError) as ctx:
            apply_available_funds(self.conn, p["payment_id"], invoice_id=final["invoice"]["invoice_id"], amount_cents=15000, idempotency_key="apply-1")
        self.assertIn("already been processed", str(ctx.exception))

    # 54. get_payment_correction_history returns entries after reversal
    def test_correction_history_after_reversal(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        a = allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=15000)
        reverse_allocation(self.conn, a["allocation_id"], reason="Test reversal")
        history = get_payment_correction_history(self.conn, p["payment_id"])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["action"], "allocation_reversed")
        self.assertEqual(history[0]["reason"], "Test reversal")

    # 55. get_payment_correction_history includes void entry
    def test_correction_history_after_void(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        void_payment(self.conn, p["payment_id"], reason="Bank error")
        history = get_payment_correction_history(self.conn, p["payment_id"])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["action"], "payment_voided")
        self.assertEqual(history[0]["reason"], "Bank error")

    # 56. get_payment_detail_view includes correction history and void reason
    def test_detail_view_includes_corrections(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        a = allocate_payment_to_session(self.conn, payment_id=p["payment_id"], session_id=self.session_id, amount_cents=15000)
        reverse_allocation(self.conn, a["allocation_id"], reason="Wrong session")
        void_payment(self.conn, p["payment_id"], reason="All reversed")
        detail = get_payment_detail_view(self.conn, p["payment_id"])
        self.assertEqual(detail["status"], "void")
        self.assertEqual(detail["void_reason"], "All reversed")
        self.assertIsNotNone(detail["voided_at"])
        self.assertEqual(len(detail["correction_history"]), 2)
        self.assertEqual(len(detail["allocations"]), 1)
        self.assertEqual(detail["allocations"][0]["reversal_reason"], "Wrong session")
        self.assertEqual(detail["allocations"][0]["status"], "reversed")

    # 57. get_payment_detail_view includes funds_applied in correction history
    def test_detail_view_includes_funds_applied(self):
        final = self._draft_and_finalize(self.session_id)
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"], amount_cents=15000, received_at="2026-05-10")
        apply_available_funds(self.conn, p["payment_id"], invoice_id=final["invoice"]["invoice_id"], amount_cents=15000)
        detail = get_payment_detail_view(self.conn, p["payment_id"])
        actions = [h["action"] for h in detail["correction_history"]]
        self.assertIn("funds_applied", actions)


if __name__ == "__main__":
    unittest.main()
