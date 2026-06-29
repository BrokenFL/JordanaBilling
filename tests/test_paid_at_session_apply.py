"""Tests for the Paid-at-Session Apply Workflow."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, migrate_database
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import (
    invoice_ineligibility_reasons,
    save_business_profile,
    stage_approved_sessions_to_monthly_drafts,
)
from jordana_invoice.payment_services import (
    create_payment,
    record_or_validate_paid_at_session_payment_locked,
)
from jordana_invoice.review_services import (
    _save_interpretation_locked,
    approve_candidate,
    create_billing_party,
    create_person,
    get_review_candidate,
    save_interpretation,
)
from jordana_invoice.util import stable_hash


def raw_row(key, title, start):
    return {
        "ingested_at": "2026-08-20T12:00:00Z",
        "snapshot_key": key,
        "run_id": f"run-{key}",
        "batch_name": "test",
        "capture_window": "past_7_days",
        "captured_at": "2026-08-20T12:00:00Z",
        "source_device": "test",
        "timezone": "America/New_York",
        "calendar_event_id": f"event-{key}",
        "event_fingerprint": f"fp-{key}",
        "event_title": title,
        "start_at": start,
        "end_at": start[:11] + "11:00:00-04:00",
        "duration_minutes": "60",
        "calendar": "Jordana Work",
        "payload_version": "2",
        "raw_json": "{}",
    }


class PaidAtSessionApplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "test.sqlite3"
        migrate_database(self.db_path)
        self.conn = connect(self.db_path)
        
        # Setup test data
        self.person = create_person(self.conn, {"first_name": "Casey", "last_name": "Sample", "display_name": "Casey Sample"})
        self.party = create_billing_party(self.conn, {
            "billing_name": "Casey Sample",
            "person_id": self.person["person_id"],
            "billing_email": "casey@example.test",
            "billing_address_line_1": "123 Fictional Rd",
            "billing_city": "Miami",
            "billing_state": "FL",
            "billing_postal_code": "33101",
            "preferred_delivery_method": "email",
        })
        self.person2 = create_person(self.conn, {"first_name": "Other", "last_name": "Client", "display_name": "Other Client"})
        self.party2 = create_billing_party(self.conn, {
            "billing_name": "Other Client",
            "person_id": self.person2["person_id"],
            "billing_email": "other@example.test",
            "billing_address_line_1": "456 Fictional Rd",
            "billing_city": "Miami",
            "billing_state": "FL",
            "billing_postal_code": "33101",
            "preferred_delivery_method": "email",
        })

        save_business_profile(self.conn, {
            "business_name": "Test Practice", "provider_display_name": "Test Provider",
            "address_line_1": "100 Test Ave", "city": "Test", "state": "FL", "postal_code": "00000",
            "phone": "555-0100", "email": "billing@test", "payee_name": "Test Payee",
            "payment_address_line_1": "100 Test Ave", "payment_city": "Test", "payment_state": "FL",
            "payment_postal_code": "00000", "zelle_recipient": "demo-zelle@example.test",
        })

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _import_candidate(self, key, title="Casey Sample | 60 | Office", start="2026-07-10T10:00:00-04:00"):
        import_rows(self.conn, [raw_row(key, title, start)], "test")
        candidate = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()
        return candidate["id"]

    # 1. First-time approval of paid_at_session session creates one payment and allocation
    def test_first_time_approval_creates_payment_and_allocation(self):
        cid = self._import_candidate("s1")
        payload = {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Casey Sample"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "paid_at_session",
            "billing_treatment": "billable",
            "amount_received": "200.00",
            "payment_date": "2026-07-10",
            "payment_method": "zelle",
        }
        res = approve_candidate(self.conn, cid, payload)
        self.assertEqual(res["session"]["review_status"], "approved")
        self.assertEqual(res["paid_at_session_outcome"], "created")

        # Verify payment
        payments = self.conn.execute("SELECT * FROM payments WHERE source_session_id = ?", (res["session"]["id"],)).fetchall()
        self.assertEqual(len(payments), 1)
        pay = payments[0]
        self.assertEqual(pay["amount_cents"], 20000)
        self.assertEqual(pay["billing_party_id"], self.party["billing_party_id"])
        self.assertEqual(pay["source_type"], "paid_at_session_backfill")
        self.assertEqual(pay["status"], "posted")

        # Verify allocation
        allocations = self.conn.execute("SELECT * FROM payment_allocations WHERE payment_id = ?", (pay["payment_id"],)).fetchall()
        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0]["amount_cents"], 20000)
        self.assertEqual(allocations[0]["status"], "active")

    # 2. Approved paid_at_session session is excluded from monthly invoice staging
    def test_session_excluded_from_invoice_staging(self):
        cid = self._import_candidate("s1")
        payload = {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Casey Sample"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "paid_at_session",
            "billing_treatment": "billable",
            "amount_received": "200.00",
            "payment_date": "2026-07-10",
            "payment_method": "zelle",
        }
        res = approve_candidate(self.conn, cid, payload)
        session_id = res["session"]["id"]
        
        # Verify exclusion from staging
        reasons = invoice_ineligibility_reasons(self.conn, res["session"])
        self.assertTrue(any("paid at time of session" in r.lower() for r in reasons))

        staging = stage_approved_sessions_to_monthly_drafts(self.conn, session_ids=[session_id])
        self.assertEqual(staging.get("drafts_created"), 0)
        self.assertEqual(staging.get("sessions_staged"), 0)

    # 3. Repeated approval is idempotent
    def test_repeated_approval_is_idempotent(self):
        cid = self._import_candidate("s1")
        payload = {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Casey Sample"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "paid_at_session",
            "billing_treatment": "billable",
            "amount_received": "200.00",
            "payment_date": "2026-07-10",
            "payment_method": "zelle",
        }
        res1 = approve_candidate(self.conn, cid, payload)
        self.assertEqual(res1["paid_at_session_outcome"], "created")

        # Second call
        res2 = approve_candidate(self.conn, cid, payload)
        self.assertEqual(res2["session"]["review_status"], "approved")
        self.assertEqual(res2["paid_at_session_outcome"], "reused")

        # Check only one payment and allocation exist
        payments = self.conn.execute("SELECT * FROM payments WHERE source_session_id = ?", (res1["session"]["id"],)).fetchall()
        self.assertEqual(len(payments), 1)

    # 4. Regression test: Normal invoice billing session stages normally
    def test_unapproved_session_staging_regression(self):
        cid = self._import_candidate("s1")
        payload = {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Casey Sample"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "unpaid",
            "billing_treatment": "billable",
        }
        res = approve_candidate(self.conn, cid, payload)
        self.assertEqual(res["session"]["review_status"], "approved")
        
        # Verify no payment was created
        payments = self.conn.execute("SELECT * FROM payments WHERE source_session_id = ?", (res["session"]["id"],)).fetchall()
        self.assertEqual(len(payments), 0)

        # Stages normally
        self.conn.execute("UPDATE sessions SET appointment_status = 'completed' WHERE id = ?", (res["session"]["id"],))
        self.conn.commit()
        staging = stage_approved_sessions_to_monthly_drafts(self.conn, session_ids=[res["session"]["id"]])
        self.assertEqual(staging.get("sessions_staged"), 1)

    # 5. Amount mismatch gets rejected
    def test_amount_received_mismatch_rejected(self):
        cid = self._import_candidate("s1")
        base_payload = {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Casey Sample"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "paid_at_session",
            "billing_treatment": "billable",
            "payment_date": "2026-07-10",
            "payment_method": "zelle",
        }
        
        # Greater amount
        p1 = dict(base_payload, amount_received="250.00")
        with self.assertRaises(ValueError) as ctx:
            approve_candidate(self.conn, cid, p1)
        self.assertIn("cannot exceed", str(ctx.exception))

        # Smaller amount
        p2 = dict(base_payload, amount_received="150.00")
        with self.assertRaises(ValueError) as ctx:
            approve_candidate(self.conn, cid, p2)
        self.assertIn("must exactly equal", str(ctx.exception))

        # Zero amount
        p3 = dict(base_payload, amount_received="0.00")
        with self.assertRaises(ValueError) as ctx:
            approve_candidate(self.conn, cid, p3)
        self.assertIn("must be greater than zero", str(ctx.exception))

    # 6. Missing payment date is rejected
    def test_missing_payment_date_rejected(self):
        cid = self._import_candidate("s1")
        payload = {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Casey Sample"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "paid_at_session",
            "billing_treatment": "billable",
            "amount_received": "200.00",
            "payment_date": "",
            "payment_method": "zelle",
        }
        with self.assertRaises(ValueError) as ctx:
            approve_candidate(self.conn, cid, payload)
        self.assertIn("date is required", str(ctx.exception))

    # 7. Unsupported payment method is rejected
    def test_unsupported_payment_method_rejected(self):
        cid = self._import_candidate("s1")
        payload = {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Casey Sample"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "paid_at_session",
            "billing_treatment": "billable",
            "amount_received": "200.00",
            "payment_date": "2026-07-10",
            "payment_method": "bitcoin",
        }
        with self.assertRaises(ValueError) as ctx:
            approve_candidate(self.conn, cid, payload)
        self.assertIn("Unsupported payment method", str(ctx.exception))

    # 8. Switching back to invoice billing creates no payment
    def test_switching_back_to_invoice_billing_no_payment(self):
        cid = self._import_candidate("s1")
        # Save interpretation as paid_at_session first
        save_interpretation(self.conn, cid, {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Casey Sample"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "paid_at_session",
            "billing_treatment": "billable",
        })

        # Now approve, but payload switches to invoice billing (unpaid)
        payload = {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Casey Sample"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "unpaid",
            "billing_treatment": "billable",
        }
        res = approve_candidate(self.conn, cid, payload)
        self.assertEqual(res["session"]["review_status"], "approved")
        self.assertEqual(res["session"]["payment_status"], "unpaid")

        # Verify no payment was created
        payments = self.conn.execute("SELECT * FROM payments WHERE source_session_id = ?", (res["session"]["id"],)).fetchall()
        self.assertEqual(len(payments), 0)

    # 9. Approval failure does not leave a partial payment/allocation (rolls back)
    def test_approval_failure_rolls_back(self):
        cid = self._import_candidate("s1")
        # To fail approval, set billing_party_id to None so it remains unresolved
        payload = {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Casey Sample"}],
            "billing_party_id": None,
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "paid_at_session",
            "billing_treatment": "billable",
            "amount_received": "200.00",
            "payment_date": "2026-07-10",
            "payment_method": "zelle",
        }
        
        session_id = self.conn.execute("SELECT id FROM sessions").fetchone()["id"]
        with self.assertRaises(ValueError):
            approve_candidate(self.conn, cid, payload)

        # Verify no payments were created
        payments = self.conn.execute("SELECT * FROM payments WHERE source_session_id = ?", (session_id,)).fetchall()
        self.assertEqual(len(payments), 0)

    # 10. Report generation occurs only after commit
    def test_report_generation_after_commit(self):
        cid = self._import_candidate("s1")
        payload = {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Casey Sample"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "paid_at_session",
            "billing_treatment": "billable",
            "amount_received": "200.00",
            "payment_date": "2026-07-10",
            "payment_method": "zelle",
        }
        
        # Mock write_reports to verify it is called, and that the database transaction has committed when it runs
        called = []
        def mock_write_reports(conn):
            # Check if database has committed (i.e. the session is approved in DB)
            res = conn.execute("SELECT review_status FROM sessions WHERE id = ?", (session_id,)).fetchone()
            called.append(res["review_status"])

        session_id = self.conn.execute("SELECT id FROM sessions").fetchone()["id"]
        with patch("jordana_invoice.review_services.write_reports", side_effect=mock_write_reports):
            approve_candidate(self.conn, cid, payload)
        
        self.assertEqual(called, ["approved"])

    # 11. Report generation failure does not roll back or duplicate payment
    def test_report_generation_failure_does_not_rollback(self):
        cid = self._import_candidate("s1")
        payload = {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Casey Sample"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "paid_at_session",
            "billing_treatment": "billable",
            "amount_received": "200.00",
            "payment_date": "2026-07-10",
            "payment_method": "zelle",
        }
        
        def failing_write_reports(conn):
            raise RuntimeError("Filesystem full!")

        with patch("jordana_invoice.review_services.write_reports", side_effect=failing_write_reports):
            res = approve_candidate(self.conn, cid, payload)
        
        self.assertEqual(res["session"]["review_status"], "approved")
        self.assertIn("Report generation warning", res.get("report_warning", ""))

        # Check that the payment is still created successfully
        payments = self.conn.execute("SELECT * FROM payments WHERE source_session_id = ?", (res["session"]["id"],)).fetchall()
        self.assertEqual(len(payments), 1)

    # 12. Already-approved recovery with no payment details is rejected
    def test_recovery_without_payment_details_rejected(self):
        cid = self._import_candidate("s1")
        # First approve as unpaid
        payload = {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Casey Sample"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "unpaid",
            "billing_treatment": "billable",
        }
        res = approve_candidate(self.conn, cid, payload)
        
        # Now update payment status to paid_at_session in DB to simulate incomplete state
        self.conn.execute("UPDATE sessions SET payment_status = 'paid_at_session' WHERE id = ?", (res["session"]["id"],))
        self.conn.commit()

        # Retry approval with no payment details in payload
        with self.assertRaises(ValueError) as ctx:
            approve_candidate(self.conn, cid, {})
        self.assertIn("Amount received is required", str(ctx.exception))

    # 13. Already-approved recovery with payment details succeeds
    def test_recovery_with_payment_details_succeeds(self):
        cid = self._import_candidate("s1")
        payload = {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Casey Sample"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "unpaid",
            "billing_treatment": "billable",
        }
        res = approve_candidate(self.conn, cid, payload)
        
        # Mutilate DB: set payment status to paid_at_session, but no payment exists
        self.conn.execute("UPDATE sessions SET payment_status = 'paid_at_session' WHERE id = ?", (res["session"]["id"],))
        self.conn.commit()

        # Retry approval with valid payment details
        retry_payload = {
            "amount_received": "200.00",
            "payment_date": "2026-07-10",
            "payment_method": "zelle",
        }
        res2 = approve_candidate(self.conn, cid, retry_payload)
        self.assertEqual(res2["paid_at_session_outcome"], "created")

        # Verify payment and allocation
        payments = self.conn.execute("SELECT * FROM payments WHERE source_session_id = ?", (res["session"]["id"],)).fetchall()
        self.assertEqual(len(payments), 1)

    # 14. Valid payment with missing allocation can be repaired exactly once
    def test_repair_missing_allocation_exactly_once(self):
        cid = self._import_candidate("s1")
        payload = {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Casey Sample"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "paid_at_session",
            "billing_treatment": "billable",
            "amount_received": "200.00",
            "payment_date": "2026-07-10",
            "payment_method": "zelle",
        }
        res = approve_candidate(self.conn, cid, payload)
        session_id = res["session"]["id"]
        
        # Mutilate allocation: delete it
        self.conn.execute("DELETE FROM payment_allocations WHERE session_id = ?", (session_id,))
        self.conn.commit()

        # Retry approval: should repair the missing allocation
        res2 = approve_candidate(self.conn, cid, payload)
        self.assertEqual(res2["paid_at_session_outcome"], "repaired_allocation")

        # Verify exactly one active allocation exists
        allocs = self.conn.execute("SELECT * FROM payment_allocations WHERE session_id = ?", (session_id,)).fetchall()
        self.assertEqual(len(allocs), 1)
        self.assertEqual(allocs[0]["status"], "active")

        # Next retry should just reuse it
        res3 = approve_candidate(self.conn, cid, payload)
        self.assertEqual(res3["paid_at_session_outcome"], "reused")

    # 15. Void or mismatched existing payment is not repaired
    def test_void_or_mismatched_payment_fails_repair(self):
        cid = self._import_candidate("s1")
        payload = {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Casey Sample"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "paid_at_session",
            "billing_treatment": "billable",
            "amount_received": "200.00",
            "payment_date": "2026-07-10",
            "payment_method": "zelle",
        }
        res = approve_candidate(self.conn, cid, payload)
        session_id = res["session"]["id"]

        # 15a. Void payment
        self.conn.execute("UPDATE payments SET status = 'void' WHERE source_session_id = ?", (session_id,))
        self.conn.commit()
        with self.assertRaises(ValueError) as ctx:
            approve_candidate(self.conn, cid, payload)
        self.assertIn("payment is not posted", str(ctx.exception))

        # 15b. Restore status, but mismatch amount
        self.conn.execute("UPDATE payments SET status = 'posted', amount_cents = 15000 WHERE source_session_id = ?", (session_id,))
        self.conn.commit()
        with self.assertRaises(ValueError) as ctx:
            approve_candidate(self.conn, cid, payload)
        self.assertIn("does not match session charge", str(ctx.exception))

        # 15c. Restore amount, but mismatch billing party
        self.conn.execute("UPDATE payments SET amount_cents = 20000, billing_party_id = ? WHERE source_session_id = ?", (self.party2["billing_party_id"], session_id))
        self.conn.commit()
        with self.assertRaises(ValueError) as ctx:
            approve_candidate(self.conn, cid, payload)
        self.assertIn("billing party does not match", str(ctx.exception))

    # 16. Conflicting allocation is not silently rewritten
    def test_conflicting_allocation_fails_repair(self):
        cid = self._import_candidate("s1")
        payload = {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Casey Sample"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "paid_at_session",
            "billing_treatment": "billable",
            "amount_received": "200.00",
            "payment_date": "2026-07-10",
            "payment_method": "zelle",
        }
        res = approve_candidate(self.conn, cid, payload)
        session_id = res["session"]["id"]

        # Insert a second active allocation to the same session pointing to a DIFFERENT manual payment
        p = create_payment(
            self.conn,
            billing_party_id=self.party["billing_party_id"],
            amount_cents=20000,
            received_at="2026-07-10",
            method="zelle",
        )
        self.conn.execute(
            "INSERT INTO payment_allocations (allocation_id, payment_id, session_id, amount_cents, status, created_at, updated_at) VALUES ('alloc-conflict', ?, ?, 20000, 'active', ?, ?)",
            (p["payment_id"], session_id, "2026-07-10T12:00:00Z", "2026-07-10T12:00:00Z"),
        )
        self.conn.commit()

        # Retry: should fail due to conflicting active allocation
        with self.assertRaises(ValueError) as ctx:
            approve_candidate(self.conn, cid, payload)
        self.assertIn("conflicting active allocations", str(ctx.exception))

    # 17. Extraction save helper _save_interpretation_locked performs no commit
    def test_non_committing_save_helper(self):
        cid = self._import_candidate("s1")
        payload = {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Casey Sample"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "unpaid",
            "billing_treatment": "billable",
        }
        
        initial_status = self.conn.execute("SELECT review_status FROM calendar_event_candidates WHERE id = ?", (cid,)).fetchone()["review_status"]
        
        # Verify that calling the helper directly writes to connection but does not commit
        _save_interpretation_locked(self.conn, cid, payload, "2026-07-10T12:00:00Z")
        
        # Open a different connection to the same SQLite DB: it should see NO changes because transaction is uncommitted
        conn2 = connect(self.db_path)
        row = conn2.execute("SELECT review_status FROM calendar_event_candidates WHERE id = ?", (cid,)).fetchone()
        self.assertEqual(row["review_status"], initial_status)
        conn2.close()

    # 18. Normal public save_interpretation commits correctly
    def test_normal_save_still_commits(self):
        cid = self._import_candidate("s1")
        payload = {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Casey Sample"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "unpaid",
            "billing_treatment": "billable",
        }
        save_interpretation(self.conn, cid, payload)
        
        # Verify that on a separate connection, the changes ARE committed
        conn2 = connect(self.db_path)
        row = conn2.execute("SELECT review_status FROM calendar_event_candidates WHERE id = ?", (cid,)).fetchone()
        self.assertEqual(row["review_status"], "ready_for_approval")
        conn2.close()

    # 19. Simultaneous/concurrent repeated calls to record_or_validate_paid_at_session_payment_locked fail due to unique constraint
    def test_concurrent_payment_uniqueness(self):
        cid = self._import_candidate("s1")
        # Ensure we have session details
        save_interpretation(self.conn, cid, {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Casey Sample"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "paid_at_session",
            "billing_treatment": "billable",
        })
        session_id = self.conn.execute("SELECT id FROM sessions").fetchone()["id"]

        self.conn.execute("BEGIN IMMEDIATE")
        
        # First call inserts payment
        record_or_validate_paid_at_session_payment_locked(
            self.conn,
            session_id=session_id,
            billing_party_id=self.party["billing_party_id"],
            amount_cents=20000,
            payment_date="2026-07-10",
            payment_method="zelle",
        )

        # Directly try to insert a second one (simulating constraint failure)
        # Because we're in the same transaction, index uniqueness is checked on constraint/insert.
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO payments (payment_id, billing_party_id, amount_cents, received_at, method, source_type, source_session_id, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("payment-2", self.party["billing_party_id"], 20000, "2026-07-10", "zelle", "paid_at_session_backfill", session_id, "posted"),
            )
        self.conn.rollback()


if __name__ == "__main__":
    unittest.main()
