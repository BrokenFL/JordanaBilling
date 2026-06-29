import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import connect, migrate_database
from jordana_invoice.duplicate_repair import duplicate_repair_plan
from jordana_invoice.importer import import_rows
from jordana_invoice.util import now_iso, stable_hash


def raw_row(
    snapshot_key,
    *,
    title="Robin Rivers | 60 | Office",
    start="2026-06-17T10:00:00-04:00",
    end="2026-06-17T11:00:00-04:00",
    duration="60",
    event_id="",
    fingerprint="fp-robin-1",
    calendar="Jordana Work",
    capture_window="past_3_days",
    ingested_at="2026-06-29T12:00:00.000Z",
):
    return {
        "ingested_at": ingested_at,
        "snapshot_key": snapshot_key,
        "run_id": f"run-{snapshot_key}",
        "batch_name": "identity-test",
        "capture_window": capture_window,
        "captured_at": ingested_at,
        "source_device": "test",
        "timezone": "America/New_York",
        "calendar_event_id": event_id,
        "event_fingerprint": fingerprint,
        "event_title": title,
        "start_at": start,
        "end_at": end,
        "duration_minutes": duration,
        "location": "",
        "notes": "",
        "calendar": calendar,
        "payload_version": "2",
        "raw_json": "{}",
    }


class CandidateIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "identity.sqlite3"
        migrate_database(self.db_path)
        self.conn = connect(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_fingerprinted_snapshot_then_missing_fingerprint_reuses_candidate(self):
        import_rows(self.conn, [raw_row("snap-1", fingerprint="fp-stable")], "test")
        original_candidate = self.conn.execute(
            "SELECT id, candidate_key FROM calendar_event_candidates"
        ).fetchone()

        import_rows(
            self.conn,
            [
                raw_row(
                    "snap-2",
                    fingerprint="",
                    capture_window="next_7_days",
                    ingested_at="2026-06-29T12:01:00.000Z",
                )
            ],
            "test",
        )

        self.assertEqual(count(self.conn, "raw_calendar_snapshots"), 2)
        self.assertEqual(count(self.conn, "calendar_event_candidates"), 1)
        self.assertEqual(count(self.conn, "sessions"), 1)
        self.assertEqual(count(self.conn, "review_items"), 1)
        candidate = self.conn.execute(
            "SELECT id, candidate_key, raw_snapshot_count FROM calendar_event_candidates"
        ).fetchone()
        self.assertEqual(candidate["id"], original_candidate["id"])
        self.assertEqual(candidate["candidate_key"], original_candidate["candidate_key"])
        self.assertEqual(original_candidate["candidate_key"], stable_hash("event_fingerprint:fp-stable"))
        self.assertGreaterEqual(count(self.conn, "candidate_identity_aliases"), 2)

    def test_overlapping_capture_window_new_snapshot_key_preserves_raw_without_duplicate_session(self):
        import_rows(
            self.conn,
            [
                raw_row("past-window", fingerprint="fp-window", capture_window="past_3_days"),
                raw_row(
                    "future-window",
                    fingerprint="",
                    capture_window="next_7_days",
                    ingested_at="2026-06-29T12:02:00.000Z",
                ),
            ],
            "test",
        )

        self.assertEqual(count(self.conn, "raw_calendar_snapshots"), 2)
        self.assertEqual(count(self.conn, "calendar_event_candidates"), 1)
        self.assertEqual(count(self.conn, "sessions"), 1)
        self.assertEqual(count(self.conn, "review_items"), 1)

    def test_ambiguous_structural_identity_does_not_auto_merge(self):
        import_rows(
            self.conn,
            [
                raw_row("snap-a", fingerprint="fp-a"),
                raw_row("snap-b", fingerprint="fp-b", ingested_at="2026-06-29T12:03:00.000Z"),
            ],
            "test",
        )
        self.assertEqual(count(self.conn, "calendar_event_candidates"), 2)

        import_rows(
            self.conn,
            [raw_row("snap-c", fingerprint="", ingested_at="2026-06-29T12:04:00.000Z")],
            "test",
        )

        self.assertEqual(count(self.conn, "raw_calendar_snapshots"), 3)
        self.assertEqual(count(self.conn, "calendar_event_candidates"), 3)
        ambiguous = self.conn.execute(
            """
            SELECT unresolved_fields, review_reasons
            FROM calendar_event_candidates
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        self.assertIn("identity_resolution", ambiguous["unresolved_fields"])
        self.assertIn("Ambiguous calendar identity", ambiguous["review_reasons"])

    def test_distinct_similar_title_appointment_does_not_merge(self):
        import_rows(self.conn, [raw_row("snap-1", fingerprint="fp-one")], "test")
        import_rows(
            self.conn,
            [
                raw_row(
                    "snap-2",
                    title="Robin Rivers | 30 | Office",
                    start="2026-06-17T10:30:00-04:00",
                    end="2026-06-17T11:00:00-04:00",
                    duration="30",
                    fingerprint="",
                    ingested_at="2026-06-29T12:05:00.000Z",
                )
            ],
            "test",
        )

        self.assertEqual(count(self.conn, "raw_calendar_snapshots"), 2)
        self.assertEqual(count(self.conn, "calendar_event_candidates"), 2)
        self.assertEqual(count(self.conn, "sessions"), 2)

    def test_approved_existing_session_values_are_preserved_during_identity_reuse(self):
        import_rows(self.conn, [raw_row("snap-1", fingerprint="fp-approved")], "test")
        session = self.conn.execute("SELECT id FROM sessions").fetchone()
        self.conn.execute(
            """
            UPDATE sessions
            SET review_status = 'approved',
                duration_minutes = 90,
                approved_rate_cents = 25000,
                rate_cents_snapshot = 25000
            WHERE id = ?
            """,
            (session["id"],),
        )
        self.conn.commit()

        import_rows(
            self.conn,
            [raw_row("snap-2", fingerprint="", ingested_at="2026-06-29T12:06:00.000Z")],
            "test",
        )

        self.assertEqual(count(self.conn, "sessions"), 1)
        preserved = self.conn.execute(
            "SELECT review_status, duration_minutes, approved_rate_cents, rate_cents_snapshot FROM sessions"
        ).fetchone()
        self.assertEqual(preserved["review_status"], "approved")
        self.assertEqual(preserved["duration_minutes"], 90)
        self.assertEqual(preserved["approved_rate_cents"], 25000)
        self.assertEqual(preserved["rate_cents_snapshot"], 25000)


class DuplicateRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "repair.sqlite3"
        migrate_database(self.db_path)
        self.conn = connect(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _make_duplicate_sessions(self):
        import_rows(
            self.conn,
            [
                raw_row("dup-a", fingerprint="fp-dup-a"),
                raw_row("dup-b", fingerprint="fp-dup-b", ingested_at="2026-06-29T12:07:00.000Z"),
            ],
            "test",
        )
        return self.conn.execute(
            """
            SELECT c.id AS candidate_id, s.id AS session_id
            FROM calendar_event_candidates c
            JOIN sessions s ON s.candidate_id = c.id
            ORDER BY c.created_at, c.id
            """
        ).fetchall()

    def test_dry_run_reports_duplicate_plan_without_mutation(self):
        self._make_duplicate_sessions()
        before = snapshot_counts(self.conn)

        result = duplicate_repair_plan(self.conn, apply=False)

        self.assertEqual(snapshot_counts(self.conn), before)
        summary = result["summary"]
        self.assertEqual(summary["groups_detected"], 1)
        self.assertEqual(summary["canonical_records_selected"], 1)
        self.assertEqual(summary["unapproved_duplicate_candidates_proposed"], 1)
        self.assertEqual(summary["unapproved_duplicate_sessions_proposed"], 1)
        self.assertEqual(summary["review_items_proposed_for_closure"], 1)
        self.assertEqual(summary["ambiguous_groups_requiring_manual_review"], 0)

    def test_apply_is_idempotent_and_audit_is_idempotent_on_temp_database(self):
        self._make_duplicate_sessions()

        duplicate_repair_plan(self.conn, apply=True, confirm=True)
        duplicate_repair_plan(self.conn, apply=True, confirm=True)

        self.assertEqual(count(self.conn, "candidate_duplicate_reconciliations"), 1)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) AS c FROM audit_log WHERE action = 'duplicate_reconciliation_applied'"
            ).fetchone()["c"],
            1,
        )

    def test_approved_canonical_is_preserved_and_duplicate_is_proposed(self):
        rows = self._make_duplicate_sessions()
        self.conn.execute(
            "UPDATE sessions SET review_status = 'approved' WHERE id = ?",
            (rows[0]["session_id"],),
        )
        self.conn.commit()

        summary = duplicate_repair_plan(self.conn, apply=False)["summary"]

        self.assertEqual(summary["protected_approved_records"], 1)
        self.assertEqual(summary["unapproved_duplicate_sessions_proposed"], 1)

    def test_invoiced_canonical_is_preserved_even_when_created_later(self):
        rows = self._make_duplicate_sessions()
        self._attach_invoice_line(rows[1]["session_id"])

        summary = duplicate_repair_plan(self.conn, apply=False)["summary"]

        self.assertEqual(summary["protected_invoiced_records"], 1)
        self.assertEqual(summary["unapproved_duplicate_sessions_proposed"], 1)

    def _attach_invoice_line(self, session_id):
        now = now_iso()
        self.conn.execute(
            """
            INSERT INTO billing_parties (
              billing_party_id, billing_party_type, billing_name,
              preferred_delivery_method, created_at, updated_at
            ) VALUES ('party-demo', 'person', 'Demo Payer', 'email', ?, ?)
            """,
            (now, now),
        )
        self.conn.execute(
            """
            INSERT INTO invoices (
              invoice_id, invoice_number, status, bill_to_party_id,
              billing_period_start, billing_period_end, invoice_date,
              total_cents, created_at, updated_at
            ) VALUES ('invoice-demo', '2026-0001', 'finalized', 'party-demo',
              '2026-06-01', '2026-06-30', '2026-06-30', 15000, ?, ?)
            """,
            (now, now),
        )
        self.conn.execute(
            """
            INSERT INTO invoice_line_items (
              invoice_line_item_id, invoice_id, source_session_id,
              service_date, participants_snapshot, service_name_snapshot,
              description_snapshot, quantity, unit_amount_cents,
              line_amount_cents, created_at, updated_at
            ) VALUES ('line-demo', 'invoice-demo', ?, '2026-06-17',
              'Demo Client', 'Psychotherapy Session',
              'Psychotherapy Session', 1, 15000, 15000, ?, ?)
            """,
            (session_id, now, now),
        )
        self.conn.commit()


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]


def snapshot_counts(conn):
    return {
        table: count(conn, table)
        for table in (
            "raw_calendar_snapshots",
            "calendar_event_candidates",
            "sessions",
            "review_items",
            "candidate_duplicate_reconciliations",
            "audit_log",
        )
    }


if __name__ == "__main__":
    unittest.main()
