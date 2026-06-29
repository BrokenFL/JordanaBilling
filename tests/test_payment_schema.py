"""Schema-level tests for the payment ledger foundation (migration 003)."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import (
    MIGRATION_003_PAYMENT_LEDGER_FOUNDATION,
    connect,
    init_db,
    migrate_database,
)
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import (
    create_invoice_draft,
    invoice_ineligibility_reasons,
    save_business_profile,
)
from jordana_invoice.review_services import (
    approve_candidate,
    create_billing_party,
    create_person,
)
from jordana_invoice.util import new_id, stable_hash


def raw_row(key, title, start):
    return {
        "ingested_at": "2026-08-20T12:00:00Z", "snapshot_key": key, "run_id": f"run-{key}",
        "batch_name": "test", "capture_window": "past_7_days", "captured_at": "2026-08-20T12:00:00Z",
        "source_device": "test", "timezone": "America/New_York", "calendar_event_id": f"event-{key}",
        "event_fingerprint": f"fp-{key}", "event_title": title, "start_at": start,
        "end_at": start[:11] + "11:00:00-04:00", "duration_minutes": "60", "calendar": "Jordana Work",
        "payload_version": "2", "raw_json": "{}",
    }


class PaymentSchemaTests(unittest.TestCase):
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

    def _approved_session(self, key, amount="150.00"):
        import_rows(self.conn, [raw_row(key, "Pat Client | 60 | Office", f"2026-05-10T10:00:00-04:00")], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Pat Client"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60, "service_mode": "office",
            "time_category": "standard", "approved_rate": amount,
            "payment_status": "unpaid", "billing_treatment": "billable",
        })
        return self.conn.execute("SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)).fetchone()

    def _insert_payment(self, amount_cents=15000, party_id=None, status="posted"):
        pid = party_id or self.party["billing_party_id"]
        payment_id = new_id()
        self.conn.execute(
            """INSERT INTO payments (payment_id, billing_party_id, amount_cents, received_at, method, status, created_at, updated_at)
            VALUES (?, ?, ?, '2026-05-10T12:00:00Z', 'cash', ?, '2026-05-10T12:00:00Z', '2026-05-10T12:00:00Z')""",
            (payment_id, pid, amount_cents, status),
        )
        self.conn.commit()
        return payment_id

    def _insert_allocation(self, payment_id, session_id, amount_cents=15000, invoice_line_item_id=None, status="active"):
        allocation_id = new_id()
        self.conn.execute(
            """INSERT INTO payment_allocations (allocation_id, payment_id, session_id, invoice_line_item_id, amount_cents, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, '2026-05-10T12:00:00Z', '2026-05-10T12:00:00Z')""",
            (allocation_id, payment_id, session_id, invoice_line_item_id, amount_cents, status),
        )
        self.conn.commit()
        return allocation_id

    # 1. Fresh database contains both tables
    def test_fresh_db_contains_both_tables(self):
        tables = {row["name"] for row in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        self.assertIn("payments", tables)
        self.assertIn("payment_allocations", tables)

    # 2. Existing database receives both tables through migration 003
    def test_existing_db_receives_tables_through_migration(self):
        temp2 = tempfile.TemporaryDirectory()
        try:
            root2 = Path(temp2.name)
            db_path2 = root2 / "existing.sqlite3"
            migrate_database(db_path2)
            conn2 = connect(db_path2)
            tables = {row["name"] for row in conn2.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            self.assertIn("payments", tables)
            self.assertIn("payment_allocations", tables)
            migrations = {row["migration_id"] for row in conn2.execute(
                "SELECT migration_id FROM schema_migrations"
            ).fetchall()}
            self.assertIn(MIGRATION_003_PAYMENT_LEDGER_FOUNDATION, migrations)
            conn2.close()
        finally:
            temp2.cleanup()

    # 3. Migration is idempotent
    def test_migration_idempotent(self):
        r2 = migrate_database(self.db_path)
        self.assertFalse(r2["migrated"])
        rows = self.conn.execute(
            "SELECT migration_id FROM schema_migrations WHERE migration_id = ?",
            (MIGRATION_003_PAYMENT_LEDGER_FOUNDATION,),
        ).fetchall()
        self.assertEqual(len(rows), 1)

    # 4. Expected columns, nullability, defaults, and foreign keys exist
    def test_payments_columns_and_defaults(self):
        cols = {row["name"]: row for row in self.conn.execute("PRAGMA table_info(payments)").fetchall()}
        self.assertIn("payment_id", cols)
        self.assertIn("billing_party_id", cols)
        self.assertIn("amount_cents", cols)
        self.assertIn("received_at", cols)
        self.assertIn("method", cols)
        self.assertIn("reference_number", cols)
        self.assertIn("received_from_name", cols)
        self.assertIn("administrative_note", cols)
        self.assertIn("status", cols)
        self.assertIn("voided_at", cols)
        self.assertIn("created_at", cols)
        self.assertIn("updated_at", cols)
        self.assertEqual(cols["billing_party_id"]["notnull"], 1)
        self.assertEqual(cols["amount_cents"]["notnull"], 1)
        self.assertEqual(cols["received_at"]["notnull"], 1)
        self.assertEqual(cols["status"]["notnull"], 1)
        self.assertEqual(cols["created_at"]["notnull"], 1)
        self.assertEqual(cols["updated_at"]["notnull"], 1)
        self.assertEqual(cols["method"]["dflt_value"], "'other'")
        self.assertEqual(cols["status"]["dflt_value"], "'posted'")
        self.assertEqual(cols["voided_at"]["notnull"], 0)
        self.assertEqual(cols["reference_number"]["notnull"], 0)
        self.assertEqual(cols["received_from_name"]["notnull"], 0)
        self.assertEqual(cols["administrative_note"]["notnull"], 0)

    def test_payment_allocations_columns_and_defaults(self):
        cols = {row["name"]: row for row in self.conn.execute("PRAGMA table_info(payment_allocations)").fetchall()}
        self.assertIn("allocation_id", cols)
        self.assertIn("payment_id", cols)
        self.assertIn("session_id", cols)
        self.assertIn("invoice_line_item_id", cols)
        self.assertIn("amount_cents", cols)
        self.assertIn("status", cols)
        self.assertIn("reversed_at", cols)
        self.assertIn("created_at", cols)
        self.assertIn("updated_at", cols)
        self.assertEqual(cols["payment_id"]["notnull"], 1)
        self.assertEqual(cols["session_id"]["notnull"], 1)
        self.assertEqual(cols["amount_cents"]["notnull"], 1)
        self.assertEqual(cols["status"]["notnull"], 1)
        self.assertEqual(cols["invoice_line_item_id"]["notnull"], 0)
        self.assertEqual(cols["reversed_at"]["notnull"], 0)
        self.assertEqual(cols["status"]["dflt_value"], "'active'")

    def test_payments_foreign_keys(self):
        fks = self.conn.execute("PRAGMA foreign_key_list(payments)").fetchall()
        fk_targets = {(fk["table"], fk["from"]) for fk in fks}
        self.assertIn(("billing_parties", "billing_party_id"), fk_targets)

    def test_payment_allocations_foreign_keys(self):
        fks = self.conn.execute("PRAGMA foreign_key_list(payment_allocations)").fetchall()
        fk_targets = {(fk["table"], fk["from"]) for fk in fks}
        self.assertIn(("payments", "payment_id"), fk_targets)
        self.assertIn(("sessions", "session_id"), fk_targets)
        self.assertIn(("invoice_line_items", "invoice_line_item_id"), fk_targets)

    # 5. Expected indexes exist
    def test_expected_indexes_exist(self):
        payment_indexes = {row["name"] for row in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='payments'"
        ).fetchall()}
        self.assertIn("idx_payments_billing_party", payment_indexes)
        self.assertIn("idx_payments_status", payment_indexes)

        allocation_indexes = {row["name"] for row in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='payment_allocations'"
        ).fetchall()}
        self.assertIn("idx_allocations_payment", allocation_indexes)
        self.assertIn("idx_allocations_session", allocation_indexes)
        self.assertIn("idx_allocations_invoice_line", allocation_indexes)
        self.assertIn("idx_allocations_session_active", allocation_indexes)

    # 6. Payment amount of zero is rejected
    def test_payment_amount_zero_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_payment(amount_cents=0)

    # 7. Negative payment amount is rejected
    def test_negative_payment_amount_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_payment(amount_cents=-100)

    # 8. Allocation amount of zero is rejected
    def test_allocation_amount_zero_rejected(self):
        payment_id = self._insert_payment()
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_allocation(payment_id, self.session_id, amount_cents=0)

    # 9. Negative allocation amount is rejected
    def test_negative_allocation_amount_rejected(self):
        payment_id = self._insert_payment()
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_allocation(payment_id, self.session_id, amount_cents=-50)

    # 10. Invalid payment status is rejected
    def test_invalid_payment_status_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_payment(status="refunded")

    # 11. Invalid allocation status is rejected
    def test_invalid_allocation_status_rejected(self):
        payment_id = self._insert_payment()
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_allocation(payment_id, self.session_id, status="void")

    # 12. Orphaned billing party is rejected
    def test_orphaned_billing_party_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_payment(party_id="nonexistent-party-id")

    # 13. Orphaned payment allocation is rejected
    def test_orphaned_payment_allocation_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_allocation("nonexistent-payment-id", self.session_id)

    # 14. Orphaned session allocation is rejected
    def test_orphaned_session_allocation_rejected(self):
        payment_id = self._insert_payment()
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_allocation(payment_id, "nonexistent-session-id")

    # 15. Invalid non-null invoice-line reference is rejected
    def test_invalid_invoice_line_reference_rejected(self):
        payment_id = self._insert_payment()
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_allocation(payment_id, self.session_id, invoice_line_item_id="nonexistent-line-id")

    # 16. Allocation with a valid session and NULL invoice line is accepted
    def test_allocation_with_null_invoice_line_accepted(self):
        payment_id = self._insert_payment()
        allocation_id = self._insert_allocation(payment_id, self.session_id, invoice_line_item_id=None)
        row = self.conn.execute(
            "SELECT * FROM payment_allocations WHERE allocation_id = ?", (allocation_id,)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertIsNone(row["invoice_line_item_id"])
        self.assertEqual(row["status"], "active")

    # 17. Multiple allocations to one session are accepted
    def test_multiple_allocations_to_one_session_accepted(self):
        payment1 = self._insert_payment(amount_cents=10000)
        payment2 = self._insert_payment(amount_cents=5000)
        self._insert_allocation(payment1, self.session_id, amount_cents=10000)
        self._insert_allocation(payment2, self.session_id, amount_cents=5000)
        count = self.conn.execute(
            "SELECT COUNT(*) FROM payment_allocations WHERE session_id = ? AND status = 'active'",
            (self.session_id,),
        ).fetchone()[0]
        self.assertEqual(count, 2)

    # 18. One payment may allocate across multiple sessions
    def test_one_payment_allocates_across_multiple_sessions(self):
        session2 = self._approved_session("s2")["id"]
        payment_id = self._insert_payment(amount_cents=30000)
        self._insert_allocation(payment_id, self.session_id, amount_cents=15000)
        self._insert_allocation(payment_id, session2, amount_cents=15000)
        count = self.conn.execute(
            "SELECT COUNT(*) FROM payment_allocations WHERE payment_id = ? AND status = 'active'",
            (payment_id,),
        ).fetchone()[0]
        self.assertEqual(count, 2)

    # 19. Payment and allocation rows are not cascade-deleted
    def test_no_cascade_delete(self):
        payment_id = self._insert_payment()
        allocation_id = self._insert_allocation(payment_id, self.session_id)
        self.conn.execute("DELETE FROM payment_allocations WHERE allocation_id = ?", (allocation_id,))
        self.conn.commit()
        payment_row = self.conn.execute(
            "SELECT * FROM payments WHERE payment_id = ?", (payment_id,)
        ).fetchone()
        self.assertIsNotNone(payment_row)

        allocation_id2 = self._insert_allocation(payment_id, self.session_id)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM payments WHERE payment_id = ?", (payment_id,))
        self.conn.rollback()
        alloc_row = self.conn.execute(
            "SELECT * FROM payment_allocations WHERE allocation_id = ?", (allocation_id2,)
        ).fetchone()
        self.assertIsNotNone(alloc_row)
        payment_row2 = self.conn.execute(
            "SELECT * FROM payments WHERE payment_id = ?", (payment_id,)
        ).fetchone()
        self.assertIsNotNone(payment_row2)

    # 20. Existing invoice, staging, and approval behavior remains unchanged
    def test_existing_behavior_unchanged(self):
        session = self.conn.execute("SELECT * FROM sessions WHERE id = ?", (self.session_id,)).fetchone()
        self.assertEqual(session["review_status"], "approved")
        reasons = invoice_ineligibility_reasons(self.conn, session)
        self.assertEqual(reasons, [])

        from jordana_invoice.review_services import approve_candidate
        import_rows(self.conn, [raw_row("paid1", "Pat Client | 60 | Office", "2026-05-10T10:00:00-04:00")], "test")
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
            "amount_received": "150.00", "payment_date": "2026-05-10", "payment_method": "zelle",
        })
        paid_session_row = self.conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)
        ).fetchone()
        paid_reasons = invoice_ineligibility_reasons(self.conn, paid_session_row)
        self.assertTrue(any("paid at time of session" in r.lower() for r in paid_reasons))

        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_period_start": "2026-05-01", "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31", "session_ids": [self.session_id],
        })
        self.assertEqual(draft["invoice"]["status"], "draft")
        self.assertEqual(len(draft["lines"]), 1)


if __name__ == "__main__":
    unittest.main()
