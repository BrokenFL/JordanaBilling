"""Tests for payment provenance schema (migration 004) and service validation."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import (
    MIGRATION_004_PAYMENT_PROVENANCE,
    connect,
    migrate_database,
)
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import invoice_ineligibility_reasons, save_business_profile
from jordana_invoice.payment_services import (
    allocate_payment_to_session,
    create_payment,
    get_payment_detail,
    reverse_allocation,
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


class PaymentProvenanceTests(unittest.TestCase):
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
        self.session_id = self._approved_session("s1")["id"]

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

    # 1. Migration 004 adds both columns
    def test_migration_adds_columns(self):
        cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(payments)").fetchall()}
        self.assertIn("source_type", cols)
        self.assertIn("source_session_id", cols)

    # 2. Migration is idempotent
    def test_migration_idempotent(self):
        r2 = migrate_database(self.db_path)
        self.assertFalse(r2["migrated"])
        rows = self.conn.execute(
            "SELECT migration_id FROM schema_migrations WHERE migration_id = ?",
            (MIGRATION_004_PAYMENT_PROVENANCE,),
        ).fetchall()
        self.assertEqual(len(rows), 1)

    # 3. Existing payment rows receive source_type = 'manual'
    def test_existing_payment_gets_manual_default(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=100, received_at="2026-05-10")
        self.assertEqual(p["source_type"], "manual")

    # 4. Existing rows retain source_session_id = NULL
    def test_existing_payment_source_session_null(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=100, received_at="2026-05-10")
        self.assertIsNone(p["source_session_id"])

    # 5. Manual payment without source session succeeds
    def test_manual_payment_without_source_session_succeeds(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=100, received_at="2026-05-10",
                           source_type="manual", source_session_id=None)
        self.assertEqual(p["source_type"], "manual")
        self.assertIsNone(p["source_session_id"])

    # 6. Manual payment with source session is rejected
    def test_manual_payment_with_source_session_rejected(self):
        with self.assertRaises(ValueError):
            create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=100, received_at="2026-05-10",
                           source_type="manual", source_session_id=self.session_id)

    # 7. Backfill-source payment without source session is rejected
    def test_backfill_without_source_session_rejected(self):
        with self.assertRaises(ValueError):
            create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=100, received_at="2026-05-10",
                           source_type="paid_at_session_backfill", source_session_id=None)

    # 8. Unsupported source type is rejected
    def test_unsupported_source_type_rejected(self):
        with self.assertRaises(ValueError):
            create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=100, received_at="2026-05-10",
                           source_type="import", source_session_id=None)

    # 9. Backfill-source payment with valid matching session succeeds
    def test_backfill_with_valid_session_succeeds(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=15000, received_at="2026-05-10",
                           source_type="paid_at_session_backfill", source_session_id=self.session_id)
        self.assertEqual(p["source_type"], "paid_at_session_backfill")
        self.assertEqual(p["source_session_id"], self.session_id)

    # 10. Backfill-source payment with missing session is rejected
    def test_backfill_with_missing_session_rejected(self):
        with self.assertRaises(ValueError):
            create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=100, received_at="2026-05-10",
                           source_type="paid_at_session_backfill", source_session_id="nonexistent-session")

    # 11. Backfill-source payment with Bill To mismatch is rejected
    def test_backfill_bill_to_mismatch_rejected(self):
        s2 = self._approved_session("s2m", party_id=self.party2["billing_party_id"])
        with self.assertRaises(ValueError):
            create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=100, received_at="2026-05-10",
                           source_type="paid_at_session_backfill", source_session_id=s2["id"])

    # 12. A second backfill-source payment for the same session is rejected
    def test_second_backfill_for_same_session_rejected(self):
        create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                       amount_cents=15000, received_at="2026-05-10",
                       source_type="paid_at_session_backfill", source_session_id=self.session_id)
        with self.assertRaises(sqlite3.IntegrityError):
            create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=15000, received_at="2026-05-10",
                           source_type="paid_at_session_backfill", source_session_id=self.session_id)

    # 13. A voided backfill payment still prevents another backfill payment for that session
    def test_voided_backfill_prevents_recreation(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=15000, received_at="2026-05-10",
                           source_type="paid_at_session_backfill", source_session_id=self.session_id)
        void_payment(self.conn, p["payment_id"])
        with self.assertRaises(sqlite3.IntegrityError):
            create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=15000, received_at="2026-05-10",
                           source_type="paid_at_session_backfill", source_session_id=self.session_id)

    # 14. Reversed allocations do not permit a replacement backfill payment
    def test_reversed_allocation_prevents_replacement_backfill(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=15000, received_at="2026-05-10",
                           source_type="paid_at_session_backfill", source_session_id=self.session_id)
        a = allocate_payment_to_session(self.conn, payment_id=p["payment_id"],
                                        session_id=self.session_id, amount_cents=15000)
        reverse_allocation(self.conn, a["allocation_id"])
        with self.assertRaises(sqlite3.IntegrityError):
            create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=15000, received_at="2026-05-10",
                           source_type="paid_at_session_backfill", source_session_id=self.session_id)

    # 15. Manual payment for a session does not occupy the backfill provenance slot
    def test_manual_payment_does_not_occupy_backfill_slot(self):
        p_manual = create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                                  amount_cents=5000, received_at="2026-05-10")
        allocate_payment_to_session(self.conn, payment_id=p_manual["payment_id"],
                                    session_id=self.session_id, amount_cents=5000)
        p_backfill = create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                                    amount_cents=10000, received_at="2026-05-10",
                                    source_type="paid_at_session_backfill", source_session_id=self.session_id)
        self.assertEqual(p_backfill["source_type"], "paid_at_session_backfill")

    # 16. Existing manual create_payment() callers remain compatible
    def test_existing_callers_compatible(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=15000, received_at="2026-05-10",
                           method="cash", reference_number="REF-1",
                           received_from_name="Someone", administrative_note="A note")
        self.assertEqual(p["status"], "posted")
        self.assertEqual(p["source_type"], "manual")
        self.assertIsNone(p["source_session_id"])

    # 17. get_payment_detail() returns provenance fields
    def test_get_payment_detail_returns_provenance(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=15000, received_at="2026-05-10",
                           source_type="paid_at_session_backfill", source_session_id=self.session_id)
        detail = get_payment_detail(self.conn, p["payment_id"])
        self.assertIn("source_type", detail["payment"])
        self.assertIn("source_session_id", detail["payment"])
        self.assertEqual(detail["payment"]["source_type"], "paid_at_session_backfill")
        self.assertEqual(detail["payment"]["source_session_id"], self.session_id)

    # 18. Audit entries contain no private text
    def test_audit_no_private_text(self):
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=15000, received_at="2026-05-10",
                           reference_number="CHECK-999", received_from_name="Grandma",
                           administrative_note="Secret patient note",
                           source_type="paid_at_session_backfill", source_session_id=self.session_id)
        audits = self.conn.execute(
            "SELECT details FROM audit_log WHERE entity_type = 'payment' AND entity_id = ?", (p["payment_id"],)
        ).fetchall()
        for a in audits:
            details_str = a["details"] or ""
            self.assertNotIn("CHECK-999", details_str)
            self.assertNotIn("Grandma", details_str)
            self.assertNotIn("Secret", details_str)
            self.assertNotIn("patient", details_str.lower())
            self.assertIn("source_type", details_str)

    # 19. Paid-at-session eligibility remains unchanged
    def test_paid_at_session_eligibility_unchanged(self):
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

    # 20. No payment or allocation records are automatically created by migration 004
    def test_migration_creates_no_payment_records(self):
        temp2 = tempfile.TemporaryDirectory()
        try:
            root2 = Path(temp2.name)
            db2 = root2 / "test2.sqlite3"
            migrate_database(db2)
            conn2 = connect(db2)
            count = conn2.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
            self.assertEqual(count, 0)
            alloc_count = conn2.execute("SELECT COUNT(*) FROM payment_allocations").fetchone()[0]
            self.assertEqual(alloc_count, 0)
            conn2.close()
        finally:
            temp2.cleanup()


if __name__ == "__main__":
    unittest.main()
