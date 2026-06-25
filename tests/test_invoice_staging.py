import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, init_db, migrate_database
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import (
    create_invoice_draft,
    finalize_invoice,
    get_invoice,
    save_business_profile,
    stage_approved_sessions_to_monthly_drafts,
    void_invoice,
)
from jordana_invoice.review_services import approve_candidate, create_billing_party, create_person
from jordana_invoice.util import stable_hash


def raw_row(key, title, start, status_suffix=""):
    return {
        "ingested_at": "2026-08-20T12:00:00Z", "snapshot_key": key, "run_id": f"run-{key}",
        "batch_name": "invoice-demo", "capture_window": "past_7_days", "captured_at": "2026-08-20T12:00:00Z",
        "source_device": "test", "timezone": "America/New_York", "calendar_event_id": f"event-{key}",
        "event_fingerprint": f"fp-{key}", "event_title": f"{title}{status_suffix}", "start_at": start,
        "end_at": start[:11] + "11:00:00-04:00", "duration_minutes": "60", "calendar": "Jordana Work",
        "payload_version": "2", "raw_json": "{}",
    }


class InvoiceStagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "staging.sqlite3")
        migrate_database(self.root / "staging.sqlite3")
        self.person = create_person(self.conn, {"first_name": "Avery", "last_name": "Stone", "display_name": "Avery Stone"})
        self.party = create_billing_party(self.conn, {
            "billing_name": "Avery Stone", "person_id": self.person["person_id"],
            "billing_email": "avery@example.test", "billing_address_line_1": "10 Sample Street",
            "billing_city": "Example", "billing_state": "FL", "billing_postal_code": "00000",
            "preferred_delivery_method": "both",
        })
        self.person2 = create_person(self.conn, {"first_name": "Jordan", "last_name": "Lee", "display_name": "Jordan Lee"})
        self.party2 = create_billing_party(self.conn, {
            "billing_name": "Jordan Lee", "person_id": self.person2["person_id"],
            "billing_email": "jordan@example.test", "billing_address_line_1": "20 Sample Street",
            "billing_city": "Example", "billing_state": "FL", "billing_postal_code": "00000",
            "preferred_delivery_method": "both",
        })
        save_business_profile(self.conn, {
            "business_name": "Demo Practice", "provider_display_name": "Demo Provider",
            "address_line_1": "100 Example Avenue", "city": "Example", "state": "FL", "postal_code": "00000",
            "phone": "555-0100", "email": "billing@example.test", "payee_name": "Demo Payee",
            "payment_address_line_1": "100 Example Avenue", "payment_city": "Example", "payment_state": "FL",
            "payment_postal_code": "00000", "invoice_total_label": "TOTAL DUE", "invoice_number_format": "YYYY-NNNN",
        })

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def approved_session(self, key="one", title="Avery Stone | 60 | Office", day=15,
                         party_id=None, amount="150.00", payment_status="unpaid",
                         treatment="billable", appointment=None):
        if party_id is None:
            party_id = self.party["billing_party_id"]
        import_rows(self.conn, [raw_row(key, title, f"2026-05-{day:02d}T10:00:00-04:00")], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Avery Stone"}],
            "billing_party_id": party_id, "approved_duration_minutes": 60,
            "service_mode": "office", "time_category": "standard", "approved_rate": amount,
            "payment_status": payment_status, "billing_treatment": treatment,
        })
        if appointment:
            self.conn.execute(
                "UPDATE sessions SET appointment_status = ?, billing_treatment = ? WHERE id = ?",
                (appointment, treatment, detail["session"]["id"]),
            )
            self.conn.commit()
        return self.conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)
        ).fetchone()

    def approved_session_month(self, key, month, day, party_id=None, person_id=None, amount="150.00"):
        if party_id is None:
            party_id = self.party["billing_party_id"]
        if person_id is None:
            person_id = self.person["person_id"]
        import_rows(self.conn, [raw_row(key, "Avery Stone | 60 | Office", f"{month}-{day:02d}T10:00:00-04:00")], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": person_id, "display_name": "Avery Stone"}],
            "billing_party_id": party_id, "approved_duration_minutes": 60,
            "service_mode": "office", "time_category": "standard", "approved_rate": amount,
            "payment_status": "unpaid", "billing_treatment": "billable",
        })
        return self.conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)
        ).fetchone()

    def count_drafts(self, party_id=None, billing_month=None):
        sql = "SELECT COUNT(*) FROM invoices WHERE status = 'draft' AND billing_month IS NOT NULL"
        params = []
        if party_id:
            sql += " AND bill_to_party_id = ?"
            params.append(party_id)
        if billing_month:
            sql += " AND billing_month = ?"
            params.append(billing_month)
        return self.conn.execute(sql, params).fetchone()[0]

    def count_lines(self, invoice_id):
        return self.conn.execute(
            "SELECT COUNT(*) FROM invoice_line_items WHERE invoice_id = ?", (invoice_id,)
        ).fetchone()[0]

    def get_monthly_draft(self, party_id, billing_month):
        return self.conn.execute(
            "SELECT * FROM invoices WHERE bill_to_party_id = ? AND billing_month = ? AND status = 'draft'",
            (party_id, billing_month),
        ).fetchone()

    # 1. Eligible approved session creates one monthly draft
    def test_eligible_session_creates_one_monthly_draft(self):
        session = self.approved_session("s1")
        result = stage_approved_sessions_to_monthly_drafts(self.conn)
        self.assertEqual(result["drafts_created"], 1)
        self.assertEqual(result["sessions_staged"], 1)
        self.assertEqual(result["errors"], [])
        draft = self.get_monthly_draft(self.party["billing_party_id"], "2026-05")
        self.assertIsNotNone(draft)
        self.assertEqual(draft["billing_month"], "2026-05")
        self.assertEqual(draft["billing_period_start"], "2026-05-01")
        self.assertEqual(draft["billing_period_end"], "2026-05-31")
        self.assertEqual(self.count_lines(draft["invoice_id"]), 1)

    # 2. Multiple sessions for same Bill To and month share one draft
    def test_multiple_sessions_share_one_draft(self):
        self.approved_session("s1", day=10)
        self.approved_session("s2", day=20)
        self.approved_session("s3", day=30)
        result = stage_approved_sessions_to_monthly_drafts(self.conn)
        self.assertEqual(result["drafts_created"], 1)
        self.assertEqual(result["sessions_staged"], 3)
        self.assertEqual(self.count_drafts(self.party["billing_party_id"], "2026-05"), 1)
        draft = self.get_monthly_draft(self.party["billing_party_id"], "2026-05")
        self.assertEqual(self.count_lines(draft["invoice_id"]), 3)

    # 3. Multiple participants in one session create one invoice line
    def test_multiple_participants_one_line(self):
        import_rows(self.conn, [raw_row("multi", "Avery Stone & Jordan Lee | 60 | Office", "2026-05-15T10:00:00-04:00")], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash("calendar_event_id:event-multi"),),
        ).fetchone()[0]
        approve_candidate(self.conn, candidate_id, {
            "participants": [
                {"person_id": self.person["person_id"], "display_name": "Avery Stone"},
                {"person_id": self.person2["person_id"], "display_name": "Jordan Lee"},
            ],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60, "service_mode": "office",
            "time_category": "standard", "approved_rate": "150.00",
            "payment_status": "unpaid", "billing_treatment": "billable",
        })
        result = stage_approved_sessions_to_monthly_drafts(self.conn)
        self.assertEqual(result["sessions_staged"], 1)
        draft = self.get_monthly_draft(self.party["billing_party_id"], "2026-05")
        self.assertEqual(self.count_lines(draft["invoice_id"]), 1)

    # 4. Re-running staging creates no duplicate draft or line
    def test_rerun_staging_no_duplicates(self):
        self.approved_session("s1", day=10)
        self.approved_session("s2", day=20)
        stage_approved_sessions_to_monthly_drafts(self.conn)
        result = stage_approved_sessions_to_monthly_drafts(self.conn)
        self.assertEqual(result["drafts_created"], 0)
        self.assertEqual(result["drafts_reused"], 1)
        self.assertEqual(result["sessions_staged"], 0)
        self.assertEqual(result["sessions_already_staged"], 2)
        self.assertEqual(self.count_drafts(self.party["billing_party_id"], "2026-05"), 1)
        draft = self.get_monthly_draft(self.party["billing_party_id"], "2026-05")
        self.assertEqual(self.count_lines(draft["invoice_id"]), 2)

    # 5. Repeated approval history does not affect staging idempotency
    def test_repeated_approval_history_idempotent(self):
        session = self.approved_session("s1", day=10)
        # Add a review item (simulating repeated approval history)
        self.conn.execute(
            """INSERT INTO review_items (review_item_id, candidate_id, session_id, review_status,
               unresolved_fields, review_reasons, created_at, updated_at)
               VALUES ('ri-1', (SELECT id FROM calendar_event_candidates WHERE candidate_key = ?),
               ?, 'approved', '[]', '[]', '2026-05-10T00:00:00Z', '2026-05-10T00:00:00Z')""",
            (stable_hash("calendar_event_id:event-s1"), session["id"]),
        )
        self.conn.commit()
        stage_approved_sessions_to_monthly_drafts(self.conn)
        result = stage_approved_sessions_to_monthly_drafts(self.conn)
        self.assertEqual(result["drafts_created"], 0)
        self.assertEqual(result["sessions_already_staged"], 1)
        self.assertEqual(self.count_drafts(self.party["billing_party_id"], "2026-05"), 1)

    # 6. Different Bill To parties create separate drafts
    def test_different_parties_separate_drafts(self):
        self.approved_session("s1", day=10, party_id=self.party["billing_party_id"])
        self.approved_session("s2", day=15, party_id=self.party2["billing_party_id"])
        result = stage_approved_sessions_to_monthly_drafts(self.conn)
        self.assertEqual(result["drafts_created"], 2)
        self.assertEqual(result["sessions_staged"], 2)
        self.assertIsNotNone(self.get_monthly_draft(self.party["billing_party_id"], "2026-05"))
        self.assertIsNotNone(self.get_monthly_draft(self.party2["billing_party_id"], "2026-05"))

    # 7. Different months create separate drafts
    def test_different_months_separate_drafts(self):
        self.approved_session_month("s1", "2026-05", 10)
        self.approved_session_month("s2", "2026-06", 15)
        result = stage_approved_sessions_to_monthly_drafts(self.conn)
        self.assertEqual(result["drafts_created"], 2)
        self.assertEqual(result["sessions_staged"], 2)
        self.assertIsNotNone(self.get_monthly_draft(self.party["billing_party_id"], "2026-05"))
        self.assertIsNotNone(self.get_monthly_draft(self.party["billing_party_id"], "2026-06"))

    # 8. Existing open draft is reused
    def test_existing_open_draft_reused(self):
        self.approved_session("s1", day=10)
        # Create a manual draft for this party+month
        manual = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_month": "2026-05",
        })
        result = stage_approved_sessions_to_monthly_drafts(self.conn)
        self.assertEqual(result["drafts_created"], 0)
        self.assertEqual(result["drafts_reused"], 1)
        self.assertEqual(result["sessions_staged"], 1)
        # The manual draft should now have the session
        self.assertEqual(self.count_lines(manual["invoice"]["invoice_id"]), 1)

    # 9. Finalized monthly invoice causes a supplemental draft with next sequence
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf", return_value="a" * 64)
    def test_finalized_causes_supplemental_draft(self, fake_pdf):
        self.approved_session("s1", day=10)
        # Create and finalize a draft for May
        first = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_month": "2026-05",
            "session_ids": [self.approved_session("s0", day=5)["id"]],
        })
        finalize_invoice(self.conn, first["invoice"]["invoice_id"], pdf_root=self.root / "pdfs")
        # Now stage — should create a supplemental draft
        result = stage_approved_sessions_to_monthly_drafts(self.conn)
        self.assertEqual(result["drafts_created"], 1)
        drafts = self.conn.execute(
            "SELECT * FROM invoices WHERE bill_to_party_id = ? AND billing_month = '2026-05' ORDER BY supplement_sequence",
            (self.party["billing_party_id"],),
        ).fetchall()
        self.assertEqual(len(drafts), 2)
        self.assertEqual(drafts[0]["supplement_sequence"], 0)
        self.assertEqual(drafts[0]["status"], "finalized")
        self.assertEqual(drafts[1]["supplement_sequence"], 1)
        self.assertEqual(drafts[1]["status"], "draft")

    # 10. Void invoice history is included when calculating next sequence
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf", return_value="b" * 64)
    def test_void_invoice_included_in_sequence_calculation(self, fake_pdf):
        self.approved_session("s1", day=10)
        # Create, finalize, void
        first = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_month": "2026-05",
            "session_ids": [self.approved_session("s0", day=5)["id"]],
        })
        finalized = finalize_invoice(self.conn, first["invoice"]["invoice_id"], pdf_root=self.root / "pdfs")
        void_invoice(self.conn, finalized["invoice"]["invoice_id"], "Test void")
        # Now stage — should create supplemental with sequence 1
        result = stage_approved_sessions_to_monthly_drafts(self.conn)
        self.assertEqual(result["drafts_created"], 1)
        draft = self.get_monthly_draft(self.party["billing_party_id"], "2026-05")
        self.assertEqual(draft["supplement_sequence"], 1)

    # 11. Bill To change before finalization moves the line atomically
    def test_bill_to_change_moves_line(self):
        session = self.approved_session("s1", day=10, party_id=self.party["billing_party_id"])
        stage_approved_sessions_to_monthly_drafts(self.conn)
        # Verify it's in party1's draft
        draft1 = self.get_monthly_draft(self.party["billing_party_id"], "2026-05")
        self.assertEqual(self.count_lines(draft1["invoice_id"]), 1)
        # Change session's billing party
        self.conn.execute(
            "UPDATE sessions SET billing_party_id = ? WHERE id = ?",
            (self.party2["billing_party_id"], session["id"]),
        )
        self.conn.commit()
        # Re-stage
        result = stage_approved_sessions_to_monthly_drafts(self.conn)
        self.assertEqual(result["sessions_moved"], 1)
        # Line should be removed from party1's draft
        draft1_after = self.get_monthly_draft(self.party["billing_party_id"], "2026-05")
        # draft1 might be empty now but still exists
        self.assertEqual(self.count_lines(draft1["invoice_id"]), 0)
        # Line should be in party2's draft
        draft2 = self.get_monthly_draft(self.party2["billing_party_id"], "2026-05")
        self.assertIsNotNone(draft2)
        self.assertEqual(self.count_lines(draft2["invoice_id"]), 1)

    # 12. Session-date month change before finalization moves the line atomically
    def test_session_date_month_change_moves_line(self):
        session = self.approved_session_month("s1", "2026-05", 10)
        stage_approved_sessions_to_monthly_drafts(self.conn)
        draft_may = self.get_monthly_draft(self.party["billing_party_id"], "2026-05")
        self.assertEqual(self.count_lines(draft_may["invoice_id"]), 1)
        # Change session date to June
        self.conn.execute(
            "UPDATE sessions SET session_date = '2026-06-10' WHERE id = ?",
            (session["id"],),
        )
        self.conn.commit()
        result = stage_approved_sessions_to_monthly_drafts(self.conn)
        self.assertEqual(result["sessions_moved"], 1)
        # May draft should be empty
        self.assertEqual(self.count_lines(draft_may["invoice_id"]), 0)
        # June draft should have the session
        draft_june = self.get_monthly_draft(self.party["billing_party_id"], "2026-06")
        self.assertIsNotNone(draft_june)
        self.assertEqual(self.count_lines(draft_june["invoice_id"]), 1)

    # 13. Session becoming ineligible removes it from the stale draft and does not restage
    def test_session_ineligible_removed_from_draft(self):
        session = self.approved_session("s1", day=10)
        stage_approved_sessions_to_monthly_drafts(self.conn)
        draft = self.get_monthly_draft(self.party["billing_party_id"], "2026-05")
        self.assertEqual(self.count_lines(draft["invoice_id"]), 1)
        # Make session ineligible by setting payment_status to paid_at_session
        self.conn.execute(
            "UPDATE sessions SET payment_status = 'paid_at_session' WHERE id = ?",
            (session["id"],),
        )
        self.conn.commit()
        result = stage_approved_sessions_to_monthly_drafts(self.conn)
        self.assertEqual(result["sessions_removed_ineligible"], 1)
        self.assertEqual(self.count_lines(draft["invoice_id"]), 0)
        # Should not be restaged
        result2 = stage_approved_sessions_to_monthly_drafts(self.conn)
        self.assertEqual(result2["sessions_staged"], 0)

    # 14. Finalized invoice lines are never moved or modified
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf", return_value="c" * 64)
    def test_finalized_lines_never_moved(self, fake_pdf):
        session = self.approved_session("s1", day=10)
        first = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_month": "2026-05",
            "session_ids": [session["id"]],
        })
        finalize_invoice(self.conn, first["invoice"]["invoice_id"], pdf_root=self.root / "pdfs")
        finalized_lines_before = self.conn.execute(
            "SELECT * FROM invoice_line_items WHERE invoice_id = ?",
            (first["invoice"]["invoice_id"],),
        ).fetchall()
        # Change session's party
        self.conn.execute(
            "UPDATE sessions SET billing_party_id = ? WHERE id = ?",
            (self.party2["billing_party_id"], session["id"]),
        )
        self.conn.commit()
        result = stage_approved_sessions_to_monthly_drafts(self.conn)
        # Finalized lines should be unchanged
        finalized_lines_after = self.conn.execute(
            "SELECT * FROM invoice_line_items WHERE invoice_id = ?",
            (first["invoice"]["invoice_id"],),
        ).fetchall()
        self.assertEqual(len(finalized_lines_before), len(finalized_lines_after))
        self.assertEqual(result["sessions_moved"], 0)

    # 15. Injected failure during a move rolls back removal and insertion
    def test_injected_failure_rolls_back_move(self):
        session = self.approved_session("s1", day=10, party_id=self.party["billing_party_id"])
        stage_approved_sessions_to_monthly_drafts(self.conn)
        draft1 = self.get_monthly_draft(self.party["billing_party_id"], "2026-05")
        self.assertEqual(self.count_lines(draft1["invoice_id"]), 1)
        # Change session's billing party
        self.conn.execute(
            "UPDATE sessions SET billing_party_id = ? WHERE id = ?",
            (self.party2["billing_party_id"], session["id"]),
        )
        self.conn.commit()
        # Patch _insert_line_item to fail during the move
        def failing_insert(conn, invoice_id, sess, order):
            raise RuntimeError("Injected failure during move")

        with patch("jordana_invoice.invoice_services._insert_line_item", side_effect=failing_insert):
            result = stage_approved_sessions_to_monthly_drafts(self.conn)
        # The error should be captured
        self.assertTrue(len(result["errors"]) > 0)
        # The session line should still exist (rollback prevented orphaning)
        lines = self.conn.execute(
            "SELECT * FROM invoice_line_items WHERE source_session_id = ?",
            (session["id"],),
        ).fetchall()
        self.assertTrue(len(lines) >= 1, "Session line was lost due to partial failure")

    # 16. Concurrent or repeated staging cannot create two open drafts for one party/month
    def test_repeated_staging_no_duplicate_drafts(self):
        self.approved_session("s1", day=10)
        self.approved_session("s2", day=20)
        stage_approved_sessions_to_monthly_drafts(self.conn)
        stage_approved_sessions_to_monthly_drafts(self.conn)
        stage_approved_sessions_to_monthly_drafts(self.conn)
        self.assertEqual(self.count_drafts(self.party["billing_party_id"], "2026-05"), 1)
        draft = self.get_monthly_draft(self.party["billing_party_id"], "2026-05")
        self.assertEqual(self.count_lines(draft["invoice_id"]), 2)

    # 17. Paid-at-session remains excluded for now
    def test_paid_at_session_excluded(self):
        self.approved_session("s1", day=10, payment_status="paid_at_session")
        result = stage_approved_sessions_to_monthly_drafts(self.conn)
        self.assertEqual(result["sessions_staged"], 0)
        self.assertEqual(result["drafts_created"], 0)
        # Session should be in skipped list
        skipped_ids = [s["session_id"] for s in result["sessions_skipped"]]
        self.assertTrue(len(skipped_ids) > 0)

    # 18. Nonmonthly or invalid session dates are skipped safely
    def test_invalid_session_dates_skipped(self):
        # Create a session with invalid date
        session = self.approved_session("s1", day=10)
        self.conn.execute("UPDATE sessions SET session_date = 'not-a-date' WHERE id = ?", (session["id"],))
        self.conn.commit()
        result = stage_approved_sessions_to_monthly_drafts(self.conn)
        self.assertEqual(result["sessions_staged"], 0)
        self.assertEqual(result["drafts_created"], 0)

    def test_null_session_date_skipped(self):
        session = self.approved_session("s1", day=10)
        self.conn.execute("UPDATE sessions SET session_date = NULL WHERE id = ?", (session["id"],))
        self.conn.commit()
        result = stage_approved_sessions_to_monthly_drafts(self.conn)
        self.assertEqual(result["sessions_staged"], 0)

    # 19. Structured summary reports accurate counts
    def test_structured_summary_accurate_counts(self):
        self.approved_session("s1", day=10, party_id=self.party["billing_party_id"])
        self.approved_session("s2", day=20, party_id=self.party["billing_party_id"])
        self.approved_session("s3", day=15, party_id=self.party2["billing_party_id"])
        self.approved_session("s4", day=25, party_id=self.party["billing_party_id"], payment_status="paid_at_session")
        result = stage_approved_sessions_to_monthly_drafts(self.conn)
        self.assertEqual(result["drafts_created"], 2)
        self.assertEqual(result["drafts_reused"], 0)
        self.assertEqual(result["sessions_staged"], 3)
        self.assertEqual(result["sessions_already_staged"], 0)
        self.assertEqual(result["sessions_moved"], 0)
        self.assertEqual(result["sessions_removed_ineligible"], 0)
        self.assertEqual(result["errors"], [])
        # Paid-at-session should be in skipped
        self.assertTrue(len(result["sessions_skipped"]) >= 1)
        # Run again
        result2 = stage_approved_sessions_to_monthly_drafts(self.conn)
        self.assertEqual(result2["drafts_created"], 0)
        self.assertEqual(result2["drafts_reused"], 2)
        self.assertEqual(result2["sessions_staged"], 0)
        self.assertEqual(result2["sessions_already_staged"], 3)
        self.assertEqual(result2["errors"], [])

    # Additional: session_ids filter works
    def test_session_ids_filter(self):
        s1 = self.approved_session("s1", day=10)
        s2 = self.approved_session("s2", day=20)
        result = stage_approved_sessions_to_monthly_drafts(self.conn, session_ids=[s1["id"]])
        self.assertEqual(result["sessions_staged"], 1)
        draft = self.get_monthly_draft(self.party["billing_party_id"], "2026-05")
        self.assertEqual(self.count_lines(draft["invoice_id"]), 1)
        # Now stage the other
        result2 = stage_approved_sessions_to_monthly_drafts(self.conn, session_ids=[s2["id"]])
        self.assertEqual(result2["sessions_staged"], 1)
        self.assertEqual(self.count_lines(draft["invoice_id"]), 2)

    def test_empty_session_ids_returns_empty(self):
        result = stage_approved_sessions_to_monthly_drafts(self.conn, session_ids=[])
        self.assertEqual(result["drafts_created"], 0)
        self.assertEqual(result["sessions_staged"], 0)


if __name__ == "__main__":
    unittest.main()
