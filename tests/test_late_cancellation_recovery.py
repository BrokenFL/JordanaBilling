"""Fictional regression coverage for cancellation charges stranded before Review."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import stage_approved_sessions_to_monthly_drafts
from jordana_invoice.parser import parse_event
from jordana_invoice.review_server import make_handler
from jordana_invoice.review_services import (
    approve_candidate, create_billing_party, create_person, dashboard_status,
    get_review_candidate, list_review_candidates, mark_candidate,
    reparse_candidate_only_duration_suffixes, restore_candidate,
)


def raw_event(title="Alex Example 3 late cx charge", **overrides):
    return {
        "snapshot_key": "fictional-cancellation", "run_id": "fictional-capture",
        "batch_name": "fictional", "calendar_name": "Work", "payload_version": "3",
        "event_title": title, "start_at": "2026-08-10T15:00:00-04:00",
        "end_at": "2026-08-10T16:00:00-04:00", "duration_minutes": "60",
        "captured_at": "2026-08-11T12:00:00-04:00",
        "ingested_at": "2026-08-11T16:00:00Z", "capture_window": "past_3_days",
        **overrides,
    }


class LateCancellationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "fictional.sqlite3"
        self.conn = connect(self.db)
        init_db(self.conn)
        self.reports = patch("jordana_invoice.review_services.refresh_reports_after_commit")
        self.reports.start()

    def tearDown(self):
        self.reports.stop()
        self.conn.close()
        self.temp.cleanup()

    def legacy_candidate(self, **overrides):
        # The old suffix parser failed on the extra word "charge". Reproduce
        # that import without copying real data or removing derived sessions.
        with patch("jordana_invoice.parser.strip_title_appointment_status", side_effect=lambda t: (t, None)):
            import_rows(self.conn, [raw_event(**overrides)], "fictional-test")
        row = self.conn.execute("SELECT * FROM calendar_event_candidates").fetchone()
        self.assertEqual(row["classification"], "late_cancellation")
        self.assertEqual(row["review_status"], "needs_billing_treatment")
        self.assertIsNone(self.session(row["id"]))
        return row["id"]

    def session(self, cid):
        return self.conn.execute("SELECT * FROM sessions WHERE candidate_id=?", (cid,)).fetchone()

    def raw_rows(self):
        return [tuple(r) for r in self.conn.execute("SELECT * FROM raw_calendar_snapshots ORDER BY id")]

    def test_charge_suffix_preserves_identity_time_and_separate_billing_decision(self):
        for suffix in ("late cx charge", "late cancellation charge", "late cancelled charge", "LATE CANCEL CHARGE"):
            with self.subTest(suffix=suffix):
                parsed = parse_event(raw_event(title=f"Alex Example 3 {suffix}"))
                self.assertEqual(parsed.classification, "client_session")
                self.assertEqual(parsed.appointment_status, "late_cancellation")
                self.assertEqual(parsed.candidate_person_names, ["Alex Example"])
                self.assertEqual(parsed.proposed_start_at, "2026-08-10T15:00:00-04:00")
                self.assertEqual(parsed.proposed_duration_minutes, 60)
                self.assertTrue(parsed.title_time_matches_calendar)
                self.assertIn("billing_treatment", parsed.unresolved_fields)
        parsed = parse_event(raw_event(title="Alex Example 4 30 late cx charge"))
        self.assertEqual(parsed.proposed_duration_minutes, 30)
        self.assertFalse(parsed.title_time_matches_calendar)

    def test_legacy_candidate_visible_before_repair_and_counted_after_repair(self):
        cid = self.legacy_candidate()
        self.assertIn(cid, [r["candidate_id"] for r in list_review_candidates(self.conn)["items"]])
        reparse_candidate_only_duration_suffixes(self.conn)
        self.assertEqual(dashboard_status(self.conn)["needs_review"], 1)

    def test_recovery_is_pending_idempotent_and_preserves_raw_evidence(self):
        cid = self.legacy_candidate()
        raw_before = self.raw_rows()
        result = reparse_candidate_only_duration_suffixes(self.conn)
        self.assertEqual(result["sessions_created"], 1)
        session = dict(self.session(cid))
        self.assertEqual(session["appointment_status"], "late_cancellation")
        self.assertEqual(session["billing_treatment"], "unresolved")
        self.assertEqual(session["review_status"], "needs_billing_treatment")
        self.assertIsNone(session["approved_rate_cents"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM people").fetchone()[0], 0)
        self.assertEqual(stage_approved_sessions_to_monthly_drafts(self.conn)["sessions_staged"], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM invoices").fetchone()[0], 0)
        with self.assertRaises(ValueError):
            approve_candidate(self.conn, cid, {})
        # Failed approval and repeated preparation cannot create duplicates.
        audit_count = self.conn.execute("SELECT count(*) FROM audit_log").fetchone()[0]
        self.assertEqual(reparse_candidate_only_duration_suffixes(self.conn)["sessions_created"], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM audit_log").fetchone()[0], audit_count)
        self.assertEqual(dict(self.session(cid)), session)
        self.assertEqual(self.raw_rows(), raw_before)

    def test_no_new_rows_review_preparation_recovers_existing_candidate(self):
        cid = self.legacy_candidate()
        handler_cls = make_handler(str(self.db))
        handler = object.__new__(handler_cls)
        handler.path = "/api/review/reconcile-calendar"
        handler.headers = {"Content-Length": "2", "Content-Type": "application/json",
                           handler_cls.write_token_header: handler_cls.write_token}
        handler.rfile, handler.wfile = io.BytesIO(b"{}"), io.BytesIO()
        handler._database_connection = self.conn
        captured = {}
        handler.send_json = lambda payload, status=200: captured.update(payload=payload, status=status)
        handler.log_message = lambda *args: None
        handler.do_POST()
        self.assertEqual(captured["status"], 200)
        self.assertIsNotNone(self.session(cid))
        self.assertEqual(list_review_candidates(self.conn)["total"], 1)

    def test_future_only_legacy_evidence_is_not_recovered(self):
        cid = self.legacy_candidate(payload_version="2", capture_window="next_7_days",
                                    captured_at="2026-08-09T12:00:00-04:00")
        self.assertEqual(reparse_candidate_only_duration_suffixes(self.conn)["sessions_created"], 0)
        self.assertIsNone(self.session(cid))
        self.assertEqual(list_review_candidates(self.conn)["total"], 0)

    def test_approved_and_excluded_candidates_remain_unchanged(self):
        cid = self.legacy_candidate()
        for status in ("approved", "excluded"):
            with self.subTest(status=status):
                self.conn.execute("UPDATE calendar_event_candidates SET review_status=? WHERE id=?", (status, cid))
                before = tuple(self.conn.execute("SELECT * FROM calendar_event_candidates WHERE id=?", (cid,)).fetchone())
                self.assertEqual(reparse_candidate_only_duration_suffixes(self.conn)["sessions_created"], 0)
                self.assertEqual(tuple(self.conn.execute("SELECT * FROM calendar_event_candidates WHERE id=?", (cid,)).fetchone()), before)
                self.assertIsNone(self.session(cid))

    def test_ambiguous_and_personal_status_titles_are_not_promoted(self):
        for title in ("late cx charge", "Alex Example 3 late cx charge maybe", "Massage 3 late cx charge"):
            parsed = parse_event(raw_event(title=title))
            self.assertNotEqual(parsed.classification, "client_session")

    def test_existing_session_and_saved_fee_are_unchanged_by_upgrade(self):
        import_rows(self.conn, [raw_event()], "fictional-test")
        self.conn.execute("UPDATE sessions SET approved_rate_cents=12500, billing_treatment='custom_fee', approved_duration_minutes=60")
        before = tuple(self.conn.execute("SELECT * FROM sessions").fetchone())
        self.assertEqual(reparse_candidate_only_duration_suffixes(self.conn)["sessions_created"], 0)
        self.assertEqual(tuple(self.conn.execute("SELECT * FROM sessions").fetchone()), before)

    def test_excluded_candidate_can_be_opened_and_deliberately_restored(self):
        from jordana_invoice.historical_review import reconcile_historical_review
        cid = self.legacy_candidate()
        mark_candidate(self.conn, cid, classification="nonbillable")
        reconcile_historical_review(self.conn)
        self.assertEqual(self.conn.execute("SELECT calendar_review_state FROM calendar_event_candidates WHERE id=?", (cid,)).fetchone()[0], "manual_exclusion")
        raw_before = self.raw_rows()
        opened = get_review_candidate(self.conn, cid)
        self.assertEqual(opened["session"]["review_status"], "excluded")
        self.assertIsNone(self.session(cid))
        restored = restore_candidate(self.conn, cid, reason="Fictional reviewer correction")
        self.assertNotEqual(restored["session"]["review_status"], "excluded")
        self.assertEqual(restored["session"]["appointment_status"], "late_cancellation")
        self.assertIn(cid, [r["candidate_id"] for r in list_review_candidates(self.conn)["items"]])
        self.assertEqual(self.raw_rows(), raw_before)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM invoices").fetchone()[0], 0)

    def test_failed_excluded_candidate_restore_rolls_back_entire_promotion(self):
        cid = self.legacy_candidate()
        mark_candidate(self.conn, cid, classification="nonbillable")
        before = tuple(self.conn.execute("SELECT * FROM calendar_event_candidates WHERE id=?", (cid,)).fetchone())
        audits = self.conn.execute("SELECT count(*) FROM audit_log").fetchone()[0]
        with patch("jordana_invoice.review_services.add_review_item", side_effect=RuntimeError("test failure")):
            with self.assertRaises(RuntimeError):
                restore_candidate(self.conn, cid)
        self.assertIsNone(self.session(cid))
        self.assertEqual(tuple(self.conn.execute("SELECT * FROM calendar_event_candidates WHERE id=?", (cid,)).fetchone()), before)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM audit_log").fetchone()[0], audits)

    def test_opening_approved_session_preserves_values_and_restore_cannot_bypass_edit_guard(self):
        import_rows(self.conn, [raw_event()], "fictional-test")
        self.conn.execute("UPDATE sessions SET review_status='approved', approved_rate_cents=12500, rate_cents_snapshot=12500")
        self.conn.execute("UPDATE calendar_event_candidates SET review_status='approved'")
        self.conn.commit()
        session = self.conn.execute("SELECT * FROM sessions").fetchone()
        before = tuple(session)
        detail = get_review_candidate(self.conn, session["candidate_id"])
        self.assertEqual(detail["session"]["review_status"], "approved")
        with self.assertRaisesRegex(ValueError, "Edit Session"):
            restore_candidate(self.conn, session["candidate_id"])
        self.assertEqual(tuple(self.conn.execute("SELECT * FROM sessions").fetchone()), before)

    def test_recovered_fee_can_be_reviewed_and_staged_once_after_approval(self):
        cid = self.legacy_candidate()
        reparse_candidate_only_duration_suffixes(self.conn)
        person = create_person(self.conn, {"first_name": "Alex", "last_name": "Example"})
        party = create_billing_party(self.conn, {"billing_name": "Alex Example", "person_id": person["person_id"]})
        approved = approve_candidate(self.conn, cid, {
            "participants": [{"person_id": person["person_id"], "display_name": "Alex Example"}],
            "billing_party_id": party["billing_party_id"], "approved_duration_minutes": 60,
            "service_mode": "phone", "billing_session_type": "psychotherapy",
            "time_category": "standard", "appointment_status": "late_cancellation",
            "billing_treatment": "custom_fee", "approved_rate": "125.00", "payment_status": "unpaid",
        })
        self.assertEqual(approved["session"]["rate_cents_snapshot"], 12500)
        sid = approved["session"]["id"]
        self.assertEqual(stage_approved_sessions_to_monthly_drafts(self.conn, session_ids=[sid])["sessions_staged"], 1)
        self.assertEqual(stage_approved_sessions_to_monthly_drafts(self.conn, session_ids=[sid])["sessions_staged"], 0)
        line = self.conn.execute("SELECT * FROM invoice_line_items WHERE source_session_id=?", (sid,)).fetchone()
        self.assertEqual(line["description_snapshot"], "Late Cancellation Fee - Psychotherapy Session")
        self.assertEqual(line["line_amount_cents"], 12500)
