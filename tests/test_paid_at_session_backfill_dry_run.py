"""Tests for the read-only paid-at-session backfill dry-run analyzer."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import connect, migrate_database
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import invoice_ineligibility_reasons, save_business_profile
from jordana_invoice.payment_services import (
    allocate_payment_to_session,
    create_payment,
    dry_run_paid_at_session_backfill,
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


class DryRunBackfillTests(unittest.TestCase):
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
            "payment_postal_code": "00000",
        })

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _make_paid_at_session(self, key, party_id=None, amount="150.00", start="2026-05-10T10:00:00-04:00",
                              approve=True, payment_status="paid_at_session"):
        pid = party_id or self.party["billing_party_id"]
        import_rows(self.conn, [raw_row(key, "Pat Client | 60 | Office", start)], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        if approve:
            detail = approve_candidate(self.conn, candidate_id, {
                "participants": [{"person_id": self.person["person_id"], "display_name": "Pat Client"}],
                "billing_party_id": pid,
                "approved_duration_minutes": 60, "service_mode": "office",
                "time_category": "standard", "approved_rate": amount,
                "payment_status": payment_status, "billing_treatment": "billable",
            })
            return self.conn.execute("SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)).fetchone()
        else:
            return self.conn.execute("SELECT * FROM sessions WHERE id = ?", (candidate_id,)).fetchone()

    def _get_session_id(self, key):
        return self.conn.execute(
            "SELECT s.id FROM sessions s JOIN calendar_event_candidates c ON c.candidate_key = ? WHERE s.calendar_event_candidate_id = c.id",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]

    # 1. No paid-at-session sessions returns all zeros
    def test_no_paid_at_session_sessions(self):
        report = dry_run_paid_at_session_backfill(self.conn)
        self.assertEqual(report["sessions_considered"], 0)
        self.assertEqual(report["sessions_eligible"], 0)
        self.assertEqual(report["total_amount_proposed_cents"], 0)

    # 2. Valid approved session is eligible
    def test_valid_approved_session_eligible(self):
        self._make_paid_at_session("s1")
        report = dry_run_paid_at_session_backfill(self.conn)
        self.assertEqual(report["sessions_eligible"], 1)
        self.assertEqual(report["total_amount_proposed_cents"], 15000)

    # 3. Already-backfilled payment is recognized even when posted
    def test_already_backfilled_posted(self):
        s = self._make_paid_at_session("s1")
        create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                       amount_cents=15000, received_at="2026-05-10",
                       source_type="paid_at_session_backfill", source_session_id=s["id"])
        report = dry_run_paid_at_session_backfill(self.conn)
        self.assertEqual(report["sessions_already_backfilled"], 1)
        self.assertEqual(report["sessions_eligible"], 0)

    # 4. Already-backfilled payment is recognized when void
    def test_already_backfilled_void(self):
        s = self._make_paid_at_session("s1")
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=15000, received_at="2026-05-10",
                           source_type="paid_at_session_backfill", source_session_id=s["id"])
        void_payment(self.conn, p["payment_id"])
        report = dry_run_paid_at_session_backfill(self.conn)
        self.assertEqual(report["sessions_already_backfilled"], 1)
        self.assertEqual(report["sessions_eligible"], 0)

    # 5. Already-backfilled payment is recognized with reversed allocation
    def test_already_backfilled_reversed_allocation(self):
        s = self._make_paid_at_session("s1")
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=15000, received_at="2026-05-10",
                           source_type="paid_at_session_backfill", source_session_id=s["id"])
        a = allocate_payment_to_session(self.conn, payment_id=p["payment_id"],
                                        session_id=s["id"], amount_cents=15000)
        reverse_allocation(self.conn, a["allocation_id"])
        report = dry_run_paid_at_session_backfill(self.conn)
        self.assertEqual(report["sessions_already_backfilled"], 1)
        self.assertEqual(report["sessions_eligible"], 0)

    # 6. Unapproved session is skipped
    def test_unapproved_session_skipped(self):
        s = self._make_paid_at_session("s1")
        self.conn.execute(
            "UPDATE sessions SET review_status = 'proposed' WHERE id = ?", (s["id"],),
        )
        self.conn.commit()
        report = dry_run_paid_at_session_backfill(self.conn)
        self.assertEqual(report["sessions_skipped"]["not_approved"], 1)
        self.assertEqual(report["sessions_eligible"], 0)

    # 7. Missing Bill To is skipped
    def test_missing_bill_to_skipped(self):
        s = self._make_paid_at_session("s1")
        self.conn.execute(
            "UPDATE sessions SET billing_party_id = NULL WHERE id = ?", (s["id"],),
        )
        self.conn.commit()
        report = dry_run_paid_at_session_backfill(self.conn)
        self.assertEqual(report["sessions_skipped"]["missing_billing_party"], 1)

    # 8. Positive rate snapshot is preferred
    def test_rate_snapshot_preferred(self):
        s = self._make_paid_at_session("s1", amount="150.00")
        self.conn.execute(
            "UPDATE sessions SET rate_cents_snapshot = 20000, approved_rate_cents = 15000 WHERE id = ?",
            (s["id"],),
        )
        self.conn.commit()
        report = dry_run_paid_at_session_backfill(self.conn)
        self.assertEqual(report["sessions_eligible"], 1)
        self.assertEqual(report["total_amount_proposed_cents"], 20000)

    # 9. Approved rate is used when snapshot is missing
    def test_approved_rate_when_snapshot_missing(self):
        s = self._make_paid_at_session("s1", amount="150.00")
        self.conn.execute(
            "UPDATE sessions SET rate_cents_snapshot = NULL WHERE id = ?", (s["id"],),
        )
        self.conn.commit()
        report = dry_run_paid_at_session_backfill(self.conn)
        self.assertEqual(report["sessions_eligible"], 1)
        self.assertEqual(report["total_amount_proposed_cents"], 15000)

    # 10. Zero, negative, and missing amounts are skipped
    def test_zero_negative_missing_amounts_skipped(self):
        s1 = self._make_paid_at_session("s1", amount="150.00")
        self.conn.execute("UPDATE sessions SET rate_cents_snapshot = 0, approved_rate_cents = 0 WHERE id = ?", (s1["id"],))
        s2 = self._make_paid_at_session("s2", amount="150.00")
        self.conn.execute("UPDATE sessions SET rate_cents_snapshot = -100, approved_rate_cents = -100 WHERE id = ?", (s2["id"],))
        s3 = self._make_paid_at_session("s3", amount="150.00")
        self.conn.execute("UPDATE sessions SET rate_cents_snapshot = NULL, approved_rate_cents = NULL WHERE id = ?", (s3["id"],))
        self.conn.commit()
        report = dry_run_paid_at_session_backfill(self.conn)
        self.assertEqual(report["sessions_skipped"]["missing_or_invalid_amount"], 3)
        self.assertEqual(report["sessions_eligible"], 0)

    # 11. Rate disagreement increments informational count
    def test_rate_disagreement_count(self):
        s = self._make_paid_at_session("s1", amount="150.00")
        self.conn.execute(
            "UPDATE sessions SET rate_cents_snapshot = 20000, approved_rate_cents = 15000 WHERE id = ?",
            (s["id"],),
        )
        self.conn.commit()
        report = dry_run_paid_at_session_backfill(self.conn)
        self.assertEqual(report["rate_disagreement_count"], 1)
        self.assertEqual(report["sessions_eligible"], 1)

    # 12. Valid session date is accepted
    def test_valid_session_date_accepted(self):
        self._make_paid_at_session("s1")
        report = dry_run_paid_at_session_backfill(self.conn)
        self.assertEqual(report["sessions_eligible"], 1)
        self.assertEqual(report["sessions_skipped"]["missing_or_invalid_date"], 0)

    # 13. Start timestamp is fallback when session date is unavailable
    def test_start_at_fallback_for_date(self):
        s = self._make_paid_at_session("s1")
        self.conn.execute("UPDATE sessions SET session_date = NULL WHERE id = ?", (s["id"],))
        self.conn.commit()
        report = dry_run_paid_at_session_backfill(self.conn)
        self.assertEqual(report["sessions_eligible"], 1)
        self.assertEqual(report["sessions_skipped"]["missing_or_invalid_date"], 0)

    # 14. Invalid or missing dates are skipped
    def test_missing_dates_skipped(self):
        s = self._make_paid_at_session("s1")
        self.conn.execute("UPDATE sessions SET session_date = NULL, start_at = '' WHERE id = ?", (s["id"],))
        self.conn.commit()
        report = dry_run_paid_at_session_backfill(self.conn)
        self.assertEqual(report["sessions_skipped"]["missing_or_invalid_date"], 1)
        self.assertEqual(report["sessions_eligible"], 0)

    # 15. Active manual allocation is classified as conflict
    def test_active_manual_allocation_conflict(self):
        s = self._make_paid_at_session("s1")
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=5000, received_at="2026-05-10")
        allocate_payment_to_session(self.conn, payment_id=p["payment_id"],
                                    session_id=s["id"], amount_cents=5000)
        report = dry_run_paid_at_session_backfill(self.conn)
        self.assertEqual(report["sessions_skipped"]["existing_manual_allocation_conflict"], 1)
        self.assertEqual(report["sessions_eligible"], 0)

    # 16. Reversed manual allocation is not an active conflict
    def test_reversed_manual_allocation_not_conflict(self):
        s = self._make_paid_at_session("s1")
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=15000, received_at="2026-05-10")
        a = allocate_payment_to_session(self.conn, payment_id=p["payment_id"],
                                        session_id=s["id"], amount_cents=15000)
        reverse_allocation(self.conn, a["allocation_id"])
        report = dry_run_paid_at_session_backfill(self.conn)
        self.assertEqual(report["sessions_skipped"]["existing_manual_allocation_conflict"], 0)
        self.assertEqual(report["sessions_eligible"], 1)
        self.assertEqual(report["existing_reversed_manual_allocation_count"], 1)

    # 17. Primary categories are mutually exclusive
    def test_categories_mutually_exclusive(self):
        self._make_paid_at_session("s1")
        s2 = self._make_paid_at_session("s2")
        self.conn.execute("UPDATE sessions SET review_status = 'proposed' WHERE id = ?", (s2["id"],))
        self.conn.commit()
        report = dry_run_paid_at_session_backfill(self.conn)
        self.assertEqual(report["sessions_eligible"], 1)
        self.assertEqual(report["sessions_skipped"]["not_approved"], 1)
        total = (report["sessions_eligible"] + report["sessions_already_backfilled"]
                 + sum(report["sessions_skipped"].values()))
        self.assertEqual(total, report["sessions_considered"])

    # 18. Category totals equal sessions considered
    def test_totals_equal_sessions_considered(self):
        self._make_paid_at_session("s1")
        self._make_paid_at_session("s2", approve=False)
        self._make_paid_at_session("s3")
        s3 = self.conn.execute("SELECT id FROM sessions WHERE payment_status = 'paid_at_session' AND review_status = 'approved' ORDER BY id LIMIT 1 OFFSET 1").fetchone()
        create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                       amount_cents=15000, received_at="2026-05-10",
                       source_type="paid_at_session_backfill", source_session_id=s3["id"])
        report = dry_run_paid_at_session_backfill(self.conn)
        total = (report["sessions_eligible"] + report["sessions_already_backfilled"]
                 + sum(report["sessions_skipped"].values()))
        self.assertEqual(total, report["sessions_considered"])

    # 19. Proposed total includes only eligible sessions
    def test_proposed_total_only_eligible(self):
        self._make_paid_at_session("s1", amount="100.00")
        self._make_paid_at_session("s2", amount="200.00", approve=False)
        report = dry_run_paid_at_session_backfill(self.conn)
        self.assertEqual(report["sessions_eligible"], 1)
        self.assertEqual(report["total_amount_proposed_cents"], 10000)

    # 20. Returned structure contains no identifying IDs or private text
    def test_no_identifying_data(self):
        self._make_paid_at_session("s1")
        report = dry_run_paid_at_session_backfill(self.conn)
        report_str = json.dumps(report)
        self.assertNotIn("payment_id", report_str)
        self.assertNotIn("allocation_id", report_str)
        self.assertNotIn("billing_party_id", report_str)
        self.assertNotIn("Pat", report_str)
        self.assertNotIn("Client", report_str)
        self.assertNotIn("pat@", report_str)
        self.assertNotIn("session_id", report_str)

    # 21. No database rows or values change
    def test_no_database_changes(self):
        self._make_paid_at_session("s1")
        p = create_payment(self.conn, billing_party_id=self.party["billing_party_id"],
                           amount_cents=15000, received_at="2026-05-10")
        a = allocate_payment_to_session(self.conn, payment_id=p["payment_id"],
                                        session_id=self.conn.execute("SELECT id FROM sessions WHERE payment_status = 'paid_at_session'").fetchone()[0],
                                        amount_cents=15000)
        tables = ["sessions", "payments", "payment_allocations", "audit_log", "invoice_line_items", "invoices"]
        before = {}
        for t in tables:
            before[t] = self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        snapshots = {}
        for t in tables:
            rows = self.conn.execute(f"SELECT * FROM {t}").fetchall()
            snapshots[t] = [dict(r) for r in rows]
        report = dry_run_paid_at_session_backfill(self.conn)
        for t in tables:
            after_count = self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            self.assertEqual(after_count, before[t], f"Row count changed for {t}")
            after_rows = self.conn.execute(f"SELECT * FROM {t}").fetchall()
            after_list = [dict(r) for r in after_rows]
            self.assertEqual(after_list, snapshots[t], f"Data changed for {t}")

    # 22. Existing paid-at-session invoice exclusion remains unchanged
    def test_paid_at_session_exclusion_unchanged(self):
        s = self._make_paid_at_session("s1")
        report = dry_run_paid_at_session_backfill(self.conn)
        self.assertEqual(report["sessions_eligible"], 1)
        reasons = invoice_ineligibility_reasons(self.conn, s)
        self.assertTrue(any("paid at time of session" in r.lower() for r in reasons))


if __name__ == "__main__":
    unittest.main()
