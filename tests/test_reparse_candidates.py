"""
Focused regression tests for reparse_unapproved_candidates.

Scenarios:
  1. Historical 'Sarah 5 cancelled' candidate (old classification='cancelled', no session)
     → after reparse: classification='client_session', appointment_status='cancelled',
       billing_treatment='unresolved', session created, in Review Queue.
  2. 'Bob 3 no show' (old classification='no_show', no session)
     → after reparse: client_session, appointment_status='no_show', session created.
  3. Normal client session candidate with an existing session is reparsed safely
     (session preserved, no duplicate created).
  4. Approved candidate is skipped (never modified).
  5. Excluded candidate is skipped (never modified).
  6. Raw calendar snapshots are never altered by reparse.
  7. Each reparsed candidate gains an audit entry with action='reparsed'.
"""

import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.review_services import (
    list_review_candidates,
    mark_candidate,
    reparse_unapproved_candidates,
)


def make_row(key, title, calendar="Jordana Calendar", start="2026-06-17T17:00:00-04:00"):
    return {
        "ingested_at": "2026-06-22T02:00:00.000Z",
        "snapshot_key": key,
        "run_id": "run-reparse-test",
        "batch_name": "reparse_test",
        "capture_window": "next_2_days",
        "captured_at": "2026-06-22T01:00:00.000Z",
        "source_device": "test",
        "timezone": "America/New_York",
        "calendar_event_id": key,
        "event_fingerprint": key,
        "event_title": title,
        "start_at": start,
        "end_at": "2026-06-17T18:00:00-04:00",
        "duration_minutes": "60",
        "location": "",
        "notes": "",
        "calendar_name": calendar,
        "payload_version": "1",
        "raw_json": "{}",
    }


class ReparseUnapprovedCandidatesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "reparse_test.sqlite3")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _cid(self, title_fragment):
        row = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE title LIKE ?",
            (f"%{title_fragment}%",),
        ).fetchone()
        return row["id"] if row else None

    def _candidate(self, cid):
        return self.conn.execute(
            "SELECT * FROM calendar_event_candidates WHERE id = ?", (cid,)
        ).fetchone()

    def _session(self, cid):
        return self.conn.execute(
            "SELECT * FROM sessions WHERE candidate_id = ?", (cid,)
        ).fetchone()

    def _raw_snapshot_count(self):
        return self.conn.execute(
            "SELECT COUNT(*) AS c FROM raw_calendar_snapshots"
        ).fetchone()["c"]

    def _delete_session_cascade(self, cid):
        """Remove a session and its FK dependents to simulate pre-parser-fix DB state."""
        session = self._session(cid)
        if not session:
            return
        sid = session["id"]
        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.conn.execute("DELETE FROM session_participants WHERE session_id = ?", (sid,))
        self.conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.commit()

    def _force_legacy_cancelled_state(self, cid):
        """Simulate a candidate that was imported by the OLD parser (classification='cancelled',
        no session, review_status='needs_classification')."""
        self._delete_session_cascade(cid)
        self.conn.execute(
            """
            UPDATE calendar_event_candidates
            SET classification = 'cancelled', appointment_status = 'cancelled',
                billing_treatment = 'not_billable', review_status = 'needs_classification'
            WHERE id = ?
            """,
            (cid,),
        )
        self.conn.commit()

    def _force_legacy_no_show_state(self, cid):
        """Simulate a candidate imported by the OLD parser as classification='no_show'."""
        self._delete_session_cascade(cid)
        self.conn.execute(
            """
            UPDATE calendar_event_candidates
            SET classification = 'no_show', appointment_status = 'no_show',
                billing_treatment = 'not_billable', review_status = 'needs_classification'
            WHERE id = ?
            """,
            (cid,),
        )
        self.conn.commit()

    def test_sarah_5_cancelled_becomes_client_session_with_unresolved_billing(self):
        import_rows(self.conn, [make_row("sarah-5-c", "Sarah 5 cancelled")], "reparse_test")
        cid = self._cid("Sarah 5")
        self.assertIsNotNone(cid)

        self._force_legacy_cancelled_state(cid)
        self.assertIsNone(self._session(cid), "Pre-condition: no session for legacy candidate")
        self.assertEqual(self._candidate(cid)["classification"], "cancelled",
                         "Pre-condition: legacy classification is 'cancelled'")

        result = reparse_unapproved_candidates(self.conn)

        self.assertGreater(result["reparsed"], 0)
        self.assertGreater(result["sessions_created"], 0)

        cand = self._candidate(cid)
        self.assertEqual(cand["classification"], "client_session",
                         "After reparse: must be client_session")
        self.assertEqual(cand["appointment_status"], "cancelled",
                         "After reparse: appointment_status must be 'cancelled'")

        session = self._session(cid)
        self.assertIsNotNone(session, "A session must be created for the reparsed candidate")
        self.assertEqual(session["billing_treatment"], "unresolved",
                         "billing_treatment must be unresolved for cancelled client session")
        self.assertNotEqual(session["review_status"], "excluded",
                            "Reparsed cancelled session must be in Review Queue, not excluded")

    def test_sarah_5_cancelled_appears_in_review_queue_after_reparse(self):
        import_rows(self.conn, [make_row("sarah-5-q", "Sarah 5 cancelled")], "reparse_test")
        cid = self._cid("Sarah 5")
        self._force_legacy_cancelled_state(cid)
        reparse_unapproved_candidates(self.conn)
        ids_in_queue = {i["candidate_id"] for i in list_review_candidates(self.conn)["items"]}
        self.assertIn(cid, ids_in_queue,
                      "After reparse, Sarah 5 cancelled must appear in the Review Queue")

    def test_no_show_variant_becomes_client_session(self):
        import_rows(self.conn, [make_row("bob-ns", "Bob 3 no show")], "reparse_test")
        cid = self._cid("Bob 3")
        self._force_legacy_no_show_state(cid)
        self.assertIsNone(self._session(cid), "Pre-condition: no session after forcing legacy state")

        reparse_unapproved_candidates(self.conn)

        cand = self._candidate(cid)
        self.assertEqual(cand["classification"], "client_session")
        self.assertEqual(cand["appointment_status"], "no_show")
        session = self._session(cid)
        self.assertIsNotNone(session)
        self.assertEqual(session["billing_treatment"], "unresolved")

    def test_existing_session_not_duplicated_on_reparse(self):
        import_rows(self.conn, [make_row("alice-norm", "Alice Smith 6")], "reparse_test")
        cid = self._cid("Alice")
        self.assertIsNotNone(self._session(cid), "Pre-condition: normal client session exists")

        reparse_unapproved_candidates(self.conn)

        count = self.conn.execute(
            "SELECT COUNT(*) AS c FROM sessions WHERE candidate_id = ?", (cid,)
        ).fetchone()["c"]
        self.assertEqual(count, 1, "Reparse must not create a duplicate session")

    def test_approved_candidate_is_skipped(self):
        import_rows(self.conn, [make_row("approved-c", "Carol Green 8")], "reparse_test")
        cid = self._cid("Carol")
        self.conn.execute(
            "UPDATE calendar_event_candidates SET review_status = 'approved' WHERE id = ?", (cid,)
        )
        self.conn.execute(
            "UPDATE sessions SET review_status = 'approved' WHERE candidate_id = ?", (cid,)
        )
        self.conn.commit()

        snap_count_before = self._raw_snapshot_count()
        result = reparse_unapproved_candidates(self.conn)

        cand = self._candidate(cid)
        self.assertEqual(cand["review_status"], "approved",
                         "Approved candidate must not be touched by reparse")
        self.assertEqual(self._raw_snapshot_count(), snap_count_before,
                         "Raw snapshots must be unchanged")
        self.assertEqual(result["skipped"], 0)

    def test_excluded_candidate_is_skipped(self):
        import_rows(self.conn, [make_row("excl-c", "Dana White 9")], "reparse_test")
        cid = self._cid("Dana")
        mark_candidate(self.conn, cid, classification="personal", reason="test")

        cand_before = self._candidate(cid)
        self.assertEqual(cand_before["review_status"], "excluded")

        reparse_unapproved_candidates(self.conn)

        cand = self._candidate(cid)
        self.assertEqual(cand["review_status"], "excluded",
                         "Excluded candidate must not be changed by reparse")
        self.assertEqual(cand["classification"], "personal",
                         "Excluded candidate classification must remain 'personal'")

    def test_raw_snapshots_never_modified_by_reparse(self):
        import_rows(self.conn, [
            make_row("snap-raw-1", "Sarah 5 cancelled"),
            make_row("snap-raw-2", "Alice Smith 6"),
        ], "reparse_test")

        before = {
            row["id"]: dict(row)
            for row in self.conn.execute("SELECT * FROM raw_calendar_snapshots").fetchall()
        }

        reparse_unapproved_candidates(self.conn)

        after = {
            row["id"]: dict(row)
            for row in self.conn.execute("SELECT * FROM raw_calendar_snapshots").fetchall()
        }
        self.assertEqual(before, after, "Raw calendar snapshots must be byte-identical after reparse")

    def test_reparse_writes_audit_entry_for_each_candidate(self):
        import_rows(self.conn, [
            make_row("audit-1", "Sarah 5 cancelled"),
            make_row("audit-2", "Alice Smith 6"),
        ], "reparse_test")

        reparse_unapproved_candidates(self.conn)

        for title_fragment in ("Sarah 5", "Alice"):
            cid = self._cid(title_fragment)
            audit_row = self.conn.execute(
                """
                SELECT id FROM audit_log
                WHERE entity_type = 'calendar_event_candidate'
                  AND entity_id = ?
                  AND action = 'reparsed'
                ORDER BY created_at DESC LIMIT 1
                """,
                (cid,),
            ).fetchone()
            self.assertIsNotNone(
                audit_row,
                f"Candidate for '{title_fragment}' must have a 'reparsed' audit entry",
            )

    def test_reparse_is_idempotent(self):
        import_rows(self.conn, [make_row("idem-1", "Sarah 5 cancelled")], "reparse_test")
        cid = self._cid("Sarah 5")
        self._force_legacy_cancelled_state(cid)

        reparse_unapproved_candidates(self.conn)
        session_after_first = dict(self._session(cid))
        cand_after_first = dict(self._candidate(cid))

        reparse_unapproved_candidates(self.conn)
        session_after_second = dict(self._session(cid))

        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) AS c FROM sessions WHERE candidate_id = ?", (cid,)
            ).fetchone()["c"],
            1,
            "A second reparse must not create a duplicate session",
        )
        self.assertEqual(
            session_after_first["candidate_id"],
            session_after_second["candidate_id"],
            "Session candidate_id must be stable across reparsing",
        )


if __name__ == "__main__":
    unittest.main()
