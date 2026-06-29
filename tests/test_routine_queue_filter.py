import tempfile
import unittest
from pathlib import Path

from jordana_invoice.calendar_preferences import upsert_calendar_preference
from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.review_services import (
    list_review_candidates,
    list_sessions_ledger,
    mark_candidate,
    restore_candidate,
)


def make_row(key, title, calendar, start="2026-06-17T18:00:00-04:00"):
    return {
        "ingested_at": "2026-06-22T02:00:00.000Z",
        "snapshot_key": key,
        "run_id": "run-filter-test",
        "batch_name": "filter_test",
        "capture_window": "next_2_days",
        "captured_at": "2026-06-22T01:00:00.000Z",
        "source_device": "test",
        "timezone": "America/New_York",
        "calendar_event_id": "",
        "event_fingerprint": f"fp-{key}",
        "event_title": title,
        "start_at": start,
        "end_at": "2026-06-17T19:00:00-04:00",
        "duration_minutes": "60",
        "calendar": calendar,
        "payload_version": "2",
        "raw_json": "{}",
    }


def candidate_ids_from_result(result):
    return {item["candidate_id"] for item in result["items"]}


class RoutineQueueFilterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "filter_test.sqlite3")
        init_db(self.conn)

        upsert_calendar_preference(self.conn, "Preferred Calendar", "preferred_work")
        upsert_calendar_preference(self.conn, "Personal Calendar", "usually_personal_admin")
        upsert_calendar_preference(self.conn, "Hidden Calendar", "hidden")

        import_rows(
            self.conn,
            [
                make_row("s-pref", "Alice Smith 6", "Preferred Calendar"),
                make_row("s-norm", "Bob Jones 7", "Regular Calendar"),
                make_row("s-pers", "Carol Green 8", "Personal Calendar"),
                make_row("s-hidn", "Dana White 9", "Hidden Calendar"),
                make_row("c-pers", "Mani pedi 4", "Personal Calendar",
                         start="2026-06-17T16:00:00-04:00"),
                make_row("c-admin", "Email followup notes", "Regular Calendar",
                         start="2026-06-17T09:00:00-04:00"),
            ],
            "filter_test",
        )

        self.raw_count_before = self.conn.execute(
            "SELECT COUNT(*) AS c FROM raw_calendar_snapshots"
        ).fetchone()["c"]

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _session_candidate_id_for(self, title_fragment):
        row = self.conn.execute(
            "SELECT candidate_id FROM sessions WHERE raw_calendar_title LIKE ?",
            (f"%{title_fragment}%",),
        ).fetchone()
        return row["candidate_id"] if row else None

    def _candidate_id_for(self, title_fragment):
        row = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE title LIKE ?",
            (f"%{title_fragment}%",),
        ).fetchone()
        return row["id"] if row else None

    def test_1_preferred_work_session_in_routine(self):
        result = list_review_candidates(self.conn)
        ids = candidate_ids_from_result(result)
        cid = self._session_candidate_id_for("Alice")
        self.assertIsNotNone(cid, "Alice Smith session not found in sessions table")
        self.assertIn(cid, ids, "Preferred-work calendar session missing from routine queue")

    def test_2_normal_calendar_session_in_routine(self):
        result = list_review_candidates(self.conn)
        ids = candidate_ids_from_result(result)
        cid = self._session_candidate_id_for("Bob")
        self.assertIsNotNone(cid, "Bob Jones session not found in sessions table")
        self.assertIn(cid, ids, "Normal-calendar session missing from routine queue")

    def test_3_personal_calendar_session_in_routine_with_disposition_flag(self):
        result = list_review_candidates(self.conn)
        ids = candidate_ids_from_result(result)
        cid = self._session_candidate_id_for("Carol")
        self.assertIsNotNone(cid, "Carol Green session not found in sessions table")
        self.assertIn(
            cid, ids,
            "Session from personal/admin calendar must still appear in routine review",
        )
        item = next(i for i in result["items"] if i["candidate_id"] == cid)
        self.assertEqual(
            item["calendar_disposition"], "usually_personal_admin",
            "Session from Personal Calendar must carry usually_personal_admin disposition",
        )

    def test_4_hidden_calendar_session_in_routine(self):
        hidden_sessions = self.conn.execute(
            "SELECT id, candidate_id FROM sessions WHERE hidden_from_review = 1"
        ).fetchall()
        self.assertTrue(
            len(hidden_sessions) > 0,
            "No session with hidden_from_review=1 found; check Hidden Calendar preference setup",
        )
        result = list_review_candidates(self.conn)
        ids = candidate_ids_from_result(result)
        for row in hidden_sessions:
            self.assertIn(
                row["candidate_id"],
                ids,
                f"Session {row['candidate_id']} from hidden calendar must appear in routine review",
            )

    def test_5_personal_candidate_not_in_routine(self):
        cid = self._candidate_id_for("Mani pedi")
        self.assertIsNotNone(cid, "Mani pedi candidate not found")
        session_row = self.conn.execute(
            "SELECT id FROM sessions WHERE candidate_id = ?", (cid,)
        ).fetchone()
        self.assertIsNone(session_row, "Mani pedi must not have a session (personal classification)")
        result = list_review_candidates(self.conn)
        ids = candidate_ids_from_result(result)
        self.assertNotIn(cid, ids, "Personal candidate must not appear in routine review queue")

    def test_6_administrative_candidate_not_in_routine(self):
        cid = self._candidate_id_for("Email followup")
        self.assertIsNotNone(cid, "Email followup candidate not found")
        admin_check = self.conn.execute(
            "SELECT classification FROM calendar_event_candidates WHERE id = ?", (cid,)
        ).fetchone()
        self.assertEqual(
            admin_check["classification"], "administrative",
            "'Email followup notes' must parse as administrative",
        )
        session_row = self.conn.execute(
            "SELECT id FROM sessions WHERE candidate_id = ?", (cid,)
        ).fetchone()
        self.assertIsNone(session_row, "Administrative candidate must not have a session")
        result = list_review_candidates(self.conn)
        ids = candidate_ids_from_result(result)
        self.assertNotIn(cid, ids, "Administrative candidate must not appear in routine review")

    def test_7_personal_admin_calendar_records_in_personal_admin_filter(self):
        result = list_review_candidates(self.conn, calendar_filter="personal_admin")
        ids = candidate_ids_from_result(result)

        personal_cal_sessions = self.conn.execute(
            "SELECT candidate_id FROM sessions WHERE calendar_disposition = 'usually_personal_admin'"
        ).fetchall()
        for row in personal_cal_sessions:
            self.assertIn(
                row["candidate_id"], ids,
                f"Session from personal/admin calendar must appear in personal_admin filter",
            )

        personal_cal_candidates = self.conn.execute(
            """
            SELECT id FROM calendar_event_candidates
            WHERE id NOT IN (SELECT candidate_id FROM sessions)
              AND calendar_disposition = 'usually_personal_admin'
            """
        ).fetchall()
        self.assertTrue(
            len(personal_cal_candidates) > 0,
            "Need at least one candidate-only row from usually_personal_admin calendar",
        )
        for row in personal_cal_candidates:
            self.assertIn(
                row["id"], ids,
                f"Candidate from personal/admin calendar must appear in personal_admin filter",
            )

    def test_8_hidden_calendar_records_in_hidden_filter(self):
        hidden_sessions = self.conn.execute(
            "SELECT candidate_id FROM sessions WHERE hidden_from_review = 1"
        ).fetchall()
        self.assertTrue(len(hidden_sessions) > 0, "Need hidden sessions for this test")
        result = list_review_candidates(self.conn, calendar_filter="hidden")
        ids = candidate_ids_from_result(result)
        for row in hidden_sessions:
            self.assertIn(
                row["candidate_id"], ids,
                f"Hidden-calendar session must appear in hidden filter",
            )

    def test_9_all_calendars_returns_every_session(self):
        result = list_review_candidates(self.conn, calendar_filter="all")
        all_ids = candidate_ids_from_result(result)
        session_cids = {
            row["candidate_id"]
            for row in self.conn.execute("SELECT candidate_id FROM sessions").fetchall()
        }
        for cid in session_cids:
            self.assertIn(cid, all_ids, f"Session {cid} must appear in all-calendars filter")

    def test_10_routine_total_equals_sessions_plus_unresolved_candidates(self):
        result = list_review_candidates(self.conn)
        total = result["total"]
        session_count = self.conn.execute(
            "SELECT COUNT(*) AS c FROM sessions WHERE review_status NOT IN ('excluded', 'approved')"
        ).fetchone()["c"]
        unresolved_count = self.conn.execute(
            """
            SELECT COUNT(*) AS c FROM calendar_event_candidates
            WHERE id NOT IN (SELECT candidate_id FROM sessions)
              AND classification IN ('unresolved', 'cancelled', 'no_show')
              AND review_status NOT IN ('excluded', 'approved')
            """
        ).fetchone()["c"]
        self.assertEqual(
            total,
            session_count + unresolved_count,
            f"Routine total {total} != non-excluded/approved sessions({session_count}) + unresolved candidates({unresolved_count})",
        )

    def test_11_all_sessions_visible_regardless_of_disposition(self):
        result = list_review_candidates(self.conn)
        routine_ids = candidate_ids_from_result(result)
        all_sessions = self.conn.execute(
            "SELECT candidate_id, calendar_name, calendar_disposition, hidden_from_review FROM sessions"
        ).fetchall()
        for row in all_sessions:
            self.assertIn(
                row["candidate_id"],
                routine_ids,
                f"Session from '{row['calendar_name']}' "
                f"(disposition={row['calendar_disposition']}, hidden={row['hidden_from_review']}) "
                f"missing from routine review",
            )

    def test_12_no_raw_snapshots_deleted_or_altered(self):
        for cf in ("", "all", "personal_admin", "hidden", "preferred_work", "other"):
            list_review_candidates(self.conn, calendar_filter=cf)
        raw_count_after = self.conn.execute(
            "SELECT COUNT(*) AS c FROM raw_calendar_snapshots"
        ).fetchone()["c"]
        self.assertEqual(
            self.raw_count_before,
            raw_count_after,
            "Raw snapshot count changed after calling list_review_candidates",
        )


class ParserSarahCancelledRegressionTest(unittest.TestCase):
    def test_sarah_5_cancelled_does_not_create_participant_named_cancelled(self):
        from jordana_invoice.parser import parse_event
        result = parse_event({
            "event_title": "Sarah 5 cancelled",
            "start_at": "2026-06-17T17:00:00-04:00",
            "end_at": "2026-06-17T18:00:00-04:00",
            "duration_minutes": 60,
        })
        names = [n.lower() for n in (result.candidate_person_names or [])]
        self.assertNotIn("cancelled", names, "'Cancelled' must not appear as a participant name")
        self.assertEqual(result.appointment_status, "cancelled")
        self.assertEqual(
            result.classification, "client_session",
            "'Sarah 5 cancelled' must be classified as client_session, not 'cancelled'",
        )
        self.assertIn(
            "billing_treatment", result.fields_requiring_review,
            "billing_treatment must be in fields_requiring_review for a cancelled client session",
        )
        self.assertIn("Sarah", result.candidate_person_names or [], "'Sarah' must be in candidate_person_names")

    def test_no_show_variant_strips_status_and_classifies_as_client_session(self):
        from jordana_invoice.parser import parse_event
        for title, expected_status in [
            ("Bob 3 no show", "no_show"),
            ("Bob 3 no-show", "no_show"),
            ("Bob 3 noshow", "no_show"),
            ("Bob 3 did not attend", "no_show"),
            ("Bob 3 canceled", "cancelled"),
            ("Bob 3 cancel", "cancelled"),
        ]:
            with self.subTest(title=title):
                result = parse_event({
                    "event_title": title,
                    "start_at": "2026-06-17T15:00:00-04:00",
                    "end_at": "2026-06-17T16:00:00-04:00",
                    "duration_minutes": 60,
                })
                self.assertEqual(
                    result.classification, "client_session",
                    f"'{title}' must be client_session, got {result.classification}",
                )
                self.assertEqual(
                    result.appointment_status, expected_status,
                    f"'{title}' must have appointment_status={expected_status}",
                )
                names_lower = [n.lower() for n in (result.candidate_person_names or [])]
                for bad in ("cancelled", "canceled", "cancel", "no show", "no-show", "noshow", "did not attend"):
                    self.assertNotIn(bad, names_lower, f"Status term '{bad}' must not appear in participant names")
                self.assertIn(
                    "billing_treatment", result.fields_requiring_review,
                    f"billing_treatment must be in fields_requiring_review for '{title}'",
                )


class QueueExclusionAndRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "exclusion_test.sqlite3")
        init_db(self.conn)
        import_rows(
            self.conn,
            [
                make_row("s-alice", "Alice Smith 6", "Jordana Calendar"),
                make_row("s-bob", "Bob Jones 7", "Jordana Calendar"),
            ],
            "exclusion_test",
        )

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _candidate_id_for(self, title_fragment):
        row = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE title LIKE ?",
            (f"%{title_fragment}%",),
        ).fetchone()
        return row["id"] if row else None

    def test_mark_duplicate_removes_session_from_normal_queue_immediately(self):
        alice_cid = self._candidate_id_for("Alice")
        self.assertIsNotNone(alice_cid)
        result_before = list_review_candidates(self.conn)
        ids_before = {i["candidate_id"] for i in result_before["items"]}
        self.assertIn(alice_cid, ids_before, "Alice must be in queue before marking")

        mark_candidate(self.conn, alice_cid, classification="duplicate", reason="test")

        result_after = list_review_candidates(self.conn)
        ids_after = {i["candidate_id"] for i in result_after["items"]}
        self.assertNotIn(alice_cid, ids_after, "After marking duplicate, Alice must be gone from normal queue")

    def test_mark_personal_removes_session_from_normal_queue_immediately(self):
        bob_cid = self._candidate_id_for("Bob")
        self.assertIsNotNone(bob_cid)
        mark_candidate(self.conn, bob_cid, classification="personal", reason="test")
        result = list_review_candidates(self.conn)
        ids = {i["candidate_id"] for i in result["items"]}
        self.assertNotIn(bob_cid, ids, "After marking personal, Bob must be gone from normal queue")

    def test_excluded_session_preserved_in_sessions_ledger(self):
        alice_cid = self._candidate_id_for("Alice")
        mark_candidate(self.conn, alice_cid, classification="duplicate", reason="test")
        ledger = list_sessions_ledger(self.conn, date_range="all")
        ledger_cids = {s["candidate_id"] for s in ledger["items"]}
        self.assertIn(alice_cid, ledger_cids, "Excluded session must still appear in sessions ledger")

    def test_restore_candidate_returns_session_to_normal_queue(self):
        alice_cid = self._candidate_id_for("Alice")
        mark_candidate(self.conn, alice_cid, classification="duplicate", reason="test")
        ids_excluded = {i["candidate_id"] for i in list_review_candidates(self.conn)["items"]}
        self.assertNotIn(alice_cid, ids_excluded)

        restore_candidate(self.conn, alice_cid, reason="re-review needed")

        ids_restored = {i["candidate_id"] for i in list_review_candidates(self.conn)["items"]}
        self.assertIn(alice_cid, ids_restored, "After restore, session must reappear in normal queue")

    def test_restore_candidate_does_not_create_duplicate_session(self):
        alice_cid = self._candidate_id_for("Alice")
        mark_candidate(self.conn, alice_cid, classification="duplicate", reason="test")
        restore_candidate(self.conn, alice_cid, reason="re-review")
        count = self.conn.execute(
            "SELECT COUNT(*) AS c FROM sessions WHERE candidate_id = ?", (alice_cid,)
        ).fetchone()["c"]
        self.assertEqual(count, 1, "Restore must not create a duplicate session")

    def test_restore_audits_the_action(self):
        alice_cid = self._candidate_id_for("Alice")
        mark_candidate(self.conn, alice_cid, classification="personal", reason="test")
        restore_candidate(self.conn, alice_cid, reason="changed mind")
        audit_row = self.conn.execute(
            """
            SELECT action FROM audit_log
            WHERE entity_type = 'calendar_event_candidate'
              AND entity_id = ?
              AND action = 'restored_to_review_queue'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (alice_cid,),
        ).fetchone()
        self.assertIsNotNone(audit_row, "restore_candidate must write a restored_to_review_queue audit entry")


class ApprovedQueueFilterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "approved_filter_test.sqlite3")
        init_db(self.conn)
        import_rows(
            self.conn,
            [
                make_row("s-alice", "Alice Smith 6", "Jordana Calendar"),
                make_row("s-bob", "Bob Jones 7", "Jordana Calendar"),
                make_row("s-carol", "Carol Green 8", "Jordana Calendar"),
            ],
            "approved_filter_test",
        )
        from jordana_invoice.review_services import (
            approve_candidate,
            create_billing_party,
            create_person,
            save_relationship_section,
        )
        self._create_person = create_person
        self._create_billing_party = create_billing_party
        self._save_relationship_section = save_relationship_section
        self._approve_candidate = approve_candidate

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _candidate_id_for(self, title_fragment):
        row = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE title LIKE ?",
            (f"%{title_fragment}%",),
        ).fetchone()
        return row["id"] if row else None

    def _approve_session(self, title_fragment):
        cid = self._candidate_id_for(title_fragment)
        person = self._create_person(self.conn, {"first_name": "Test", "last_name": "Person", "display_name": "Test Person"})
        payer = self._create_billing_party(self.conn, {"billing_name": "Test Person", "billing_party_type": "person", "person_id": person["person_id"]})
        self._save_relationship_section(self.conn, cid, {"participants": [{"person_id": person["person_id"], "display_name": "Test Person", "is_primary": True}]})
        self._approve_candidate(self.conn, cid, {
            "participants": [{"person_id": person["person_id"], "display_name": "Test Person", "is_primary": True}],
            "billing_party_id": payer["billing_party_id"],
            "approved_duration_minutes": 60,
            "billing_session_type": "psychotherapy",
            "time_category": "standard",
            "approved_rate": "150.00",
            "payment_status": "unpaid",
        })
        return cid

    def _exclude_session(self, title_fragment):
        cid = self._candidate_id_for(title_fragment)
        mark_candidate(self.conn, cid, classification="personal", reason="test exclusion")
        return cid

    def test_default_queue_excludes_approved_sessions(self):
        alice_cid = self._approve_session("Alice")
        result = list_review_candidates(self.conn)
        ids = candidate_ids_from_result(result)
        self.assertNotIn(alice_cid, ids, "Approved session must not appear in default queue")

    def test_default_queue_excludes_excluded_sessions(self):
        bob_cid = self._exclude_session("Bob")
        result = list_review_candidates(self.conn)
        ids = candidate_ids_from_result(result)
        self.assertNotIn(bob_cid, ids, "Excluded session must not appear in default queue")

    def test_approved_filter_returns_approved_only(self):
        alice_cid = self._approve_session("Alice")
        bob_cid = self._candidate_id_for("Bob")
        carol_cid = self._candidate_id_for("Carol")
        result = list_review_candidates(self.conn, review_status="approved")
        ids = candidate_ids_from_result(result)
        self.assertIn(alice_cid, ids, "Approved session must appear in approved filter")
        self.assertNotIn(bob_cid, ids, "Non-approved session must not appear in approved filter")
        self.assertNotIn(carol_cid, ids, "Non-approved session must not appear in approved filter")

    def test_excluded_filter_returns_excluded_only(self):
        bob_cid = self._exclude_session("Bob")
        alice_cid = self._candidate_id_for("Alice")
        carol_cid = self._candidate_id_for("Carol")
        result = list_review_candidates(self.conn, review_status="excluded")
        ids = candidate_ids_from_result(result)
        self.assertIn(bob_cid, ids, "Excluded session must appear in excluded filter")
        self.assertNotIn(alice_cid, ids, "Non-excluded session must not appear in excluded filter")
        self.assertNotIn(carol_cid, ids, "Non-excluded session must not appear in excluded filter")

    def test_approved_authority_score_is_100_even_with_time_mismatch(self):
        from jordana_invoice.review_services import get_review_candidate
        cid = self._approve_session("Alice")
        self.conn.execute(
            "UPDATE sessions SET title_time_matches_calendar = 0 WHERE candidate_id = ?",
            (cid,),
        )
        self.conn.commit()
        detail = get_review_candidate(self.conn, cid)
        self.assertEqual(
            detail["session"]["authority_score"], 100,
            "Approved session must have authority_score=100 even with title/calendar time mismatch",
        )

    def test_parser_confidence_unchanged_by_approval(self):
        from jordana_invoice.review_services import get_review_candidate
        cid = self._candidate_id_for("Alice")
        before = self.conn.execute(
            "SELECT confidence FROM calendar_event_candidates WHERE id = ?", (cid,)
        ).fetchone()["confidence"]
        self._approve_session("Alice")
        after = self.conn.execute(
            "SELECT confidence FROM calendar_event_candidates WHERE id = ?", (cid,)
        ).fetchone()["confidence"]
        self.assertEqual(
            before, after,
            "Parser confidence field must remain unchanged after approval",
        )


if __name__ == "__main__":
    unittest.main()
