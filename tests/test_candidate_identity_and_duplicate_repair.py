import os
import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import connect, migrate_database
from jordana_invoice.duplicate_repair import duplicate_repair_plan, reverse_duplicate_repair
from jordana_invoice.importer import import_rows, replay_existing_raw_snapshots
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

    def test_changed_fingerprint_with_unique_structural_match_reuses_candidate(self):
        import_rows(self.conn, [raw_row("snap-1", fingerprint="fp-old")], "test")
        original = self.conn.execute("SELECT id FROM calendar_event_candidates").fetchone()["id"]

        import_rows(
            self.conn,
            [raw_row("snap-2", fingerprint="fp-new", ingested_at="2026-06-29T12:05:30.000Z")],
            "test",
        )

        self.assertEqual(count(self.conn, "calendar_event_candidates"), 1)
        self.assertEqual(count(self.conn, "sessions"), 1)
        self.assertEqual(
            self.conn.execute("SELECT id FROM calendar_event_candidates").fetchone()["id"],
            original,
        )
        self.assertIsNotNone(
            self.conn.execute(
                """
                SELECT 1 FROM candidate_identity_aliases
                WHERE candidate_id = ? AND alias_type = 'event_fingerprint'
                """,
                (original,),
            ).fetchone()
        )

    def test_changed_event_id_with_unique_structural_match_reuses_candidate(self):
        import_rows(self.conn, [raw_row("snap-1", event_id="event-old", fingerprint="")], "test")
        original = self.conn.execute("SELECT id FROM calendar_event_candidates").fetchone()["id"]

        import_rows(
            self.conn,
            [raw_row("snap-2", event_id="event-new", fingerprint="", ingested_at="2026-06-29T12:05:40.000Z")],
            "test",
        )

        self.assertEqual(count(self.conn, "calendar_event_candidates"), 1)
        self.assertEqual(count(self.conn, "sessions"), 1)
        self.assertEqual(
            self.conn.execute("SELECT id FROM calendar_event_candidates").fetchone()["id"],
            original,
        )

    def test_event_id_and_fingerprint_resolving_different_candidates_flag_ambiguity(self):
        import_rows(
            self.conn,
            [
                raw_row("event-row", event_id="event-a", fingerprint="", title="Robin Rivers | 60 | Office"),
                raw_row(
                    "fingerprint-row",
                    event_id="",
                    fingerprint="fp-b",
                    title="Casey North | 60 | Office",
                    start="2026-06-18T10:00:00-04:00",
                    end="2026-06-18T11:00:00-04:00",
                    ingested_at="2026-06-29T12:05:50.000Z",
                ),
            ],
            "test",
        )

        import_rows(
            self.conn,
            [
                raw_row(
                    "conflict-row",
                    event_id="event-a",
                    fingerprint="fp-b",
                    title="Robin Rivers | 60 | Office",
                    ingested_at="2026-06-29T12:05:59.000Z",
                )
            ],
            "test",
        )

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

    def test_replay_dry_run_recovers_orphan_raw_rows_without_mutation(self):
        import_rows(self.conn, [raw_row("orphan-raw", event_id="event-orphan", fingerprint="")], "test")
        self._delete_derived_calendar_rows()
        before = snapshot_counts(self.conn)

        result = replay_existing_raw_snapshots(self.conn, apply=False)

        self.assertEqual(snapshot_counts(self.conn), before)
        self.assertEqual(result.raw_snapshots_seen, 1)
        self.assertEqual(result.candidates_created, 1)
        self.assertEqual(result.sessions_created, 1)
        self.assertTrue(result.dry_run)
        self.assertIsNone(result.import_run_id)

    def test_replay_apply_recovers_orphan_raw_rows_without_duplicating_raw_evidence(self):
        import_rows(self.conn, [raw_row("orphan-apply", event_id="event-orphan-apply", fingerprint="")], "test")
        self._delete_derived_calendar_rows()

        result = replay_existing_raw_snapshots(self.conn, apply=True)

        self.assertFalse(result.dry_run)
        self.assertIsNotNone(result.import_run_id)
        self.assertIsNotNone(result.backup_path)
        self.assertTrue(Path(result.backup_path).exists())
        self.assertEqual(count(self.conn, "raw_calendar_snapshots"), 1)
        self.assertEqual(count(self.conn, "calendar_event_candidates"), 1)
        self.assertEqual(count(self.conn, "sessions"), 1)

    def test_edited_event_refreshes_pending_session_to_newest_snapshot(self):
        import_rows(
            self.conn,
            [raw_row("edit-old", event_id="event-edit", fingerprint="", title="Robin Rivers | 60 | Office")],
            "test",
        )

        import_rows(
            self.conn,
            [
                raw_row(
                    "edit-new",
                    event_id="event-edit",
                    fingerprint="",
                    title="Casey North | 60 | Office",
                    ingested_at="2026-06-29T12:08:00.000Z",
                )
            ],
            "test",
        )

        self.assertEqual(count(self.conn, "calendar_event_candidates"), 1)
        self.assertEqual(count(self.conn, "sessions"), 1)
        session = self.conn.execute("SELECT id, raw_calendar_title FROM sessions").fetchone()
        self.assertEqual(session["raw_calendar_title"], "Casey North | 60 | Office")
        participants = [
            row["participant_name"]
            for row in self.conn.execute(
                "SELECT participant_name FROM session_participants WHERE session_id = ?",
                (session["id"],),
            ).fetchall()
        ]
        self.assertEqual(participants, ["Casey North"])

    def test_edited_event_does_not_rewrite_approved_session(self):
        import_rows(
            self.conn,
            [raw_row("approved-old", event_id="event-approved-edit", fingerprint="", title="Robin Rivers | 60 | Office")],
            "test",
        )
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
            [
                raw_row(
                    "approved-new",
                    event_id="event-approved-edit",
                    fingerprint="",
                    title="Casey North | 60 | Office",
                    ingested_at="2026-06-29T12:09:00.000Z",
                )
            ],
            "test",
        )

        preserved = self.conn.execute(
            """
            SELECT raw_calendar_title, duration_minutes, approved_rate_cents,
                   rate_cents_snapshot, review_status
            FROM sessions
            """
        ).fetchone()
        self.assertEqual(preserved["raw_calendar_title"], "Robin Rivers | 60 | Office")
        self.assertEqual(preserved["duration_minutes"], 90)
        self.assertEqual(preserved["approved_rate_cents"], 25000)
        self.assertEqual(preserved["rate_cents_snapshot"], 25000)
        self.assertEqual(preserved["review_status"], "approved")
        warning = self.conn.execute(
            """
            SELECT review_status, old_value, new_value
            FROM review_items
            WHERE review_status = 'source_change_warning'
            """
        ).fetchone()
        self.assertIsNotNone(warning)
        self.assertEqual(warning["old_value"], "Robin Rivers | 60 | Office")
        self.assertEqual(warning["new_value"], "Casey North | 60 | Office")

    def test_latest_non_client_revision_excludes_pending_session(self):
        import_rows(
            self.conn,
            [raw_row("client-first", event_id="event-non-client", fingerprint="", title="Robin Rivers | 60 | Office")],
            "test",
        )
        session_id = self.conn.execute("SELECT id FROM sessions").fetchone()["id"]
        import_rows(
            self.conn,
            [
                raw_row(
                    "personal-latest",
                    event_id="event-non-client",
                    fingerprint="",
                    title="Mani pedi 4",
                    ingested_at="2026-06-29T12:10:00.000Z",
                )
            ],
            "test",
        )

        session = self.conn.execute(
            "SELECT review_status, billable_status FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        self.assertEqual(session["review_status"], "excluded")
        self.assertEqual(session["billable_status"], "excluded")
        self.assertEqual(
            count_where(self.conn, "session_participants", "session_id = ?", (session_id,)),
            0,
        )

    def _delete_derived_calendar_rows(self):
        self.conn.execute("PRAGMA foreign_keys = OFF")
        for table in (
            "session_participants",
            "sessions",
            "review_queue",
            "review_items",
            "candidate_identity_aliases",
            "calendar_event_candidates",
            "audit_log",
        ):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.commit()


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
        before = state_rows(self.conn)
        duplicate_repair_plan(self.conn, apply=True, confirm=True)

        self.assertEqual(state_rows(self.conn), before)
        self.assertEqual(count(self.conn, "candidate_duplicate_reconciliations"), 1)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) AS c FROM audit_log WHERE action = 'duplicate_reconciliation_applied'"
            ).fetchone()["c"],
            1,
        )

    def test_post_apply_dry_run_reports_zero_actions_for_repaired_records(self):
        self._make_duplicate_sessions()
        duplicate_repair_plan(self.conn, apply=True, confirm=True)

        summary = duplicate_repair_plan(self.conn, apply=False)["summary"]

        self.assertEqual(summary["groups_detected"], 0)
        self.assertEqual(summary["unapproved_duplicate_candidates_proposed"], 0)
        self.assertEqual(summary["unapproved_duplicate_sessions_proposed"], 0)

    def test_reversal_restores_only_duplicate_repair_changes(self):
        self._make_duplicate_sessions()
        rows = self.conn.execute(
            """
            SELECT c.id AS candidate_id, s.id AS session_id
            FROM calendar_event_candidates c
            JOIN sessions s ON s.candidate_id = c.id
            ORDER BY c.created_at, c.id
            """
        ).fetchall()
        duplicate_id = rows[1]["candidate_id"]
        original = self.conn.execute(
            "SELECT review_status, reconciliation_status FROM calendar_event_candidates WHERE id = ?",
            (duplicate_id,),
        ).fetchone()

        duplicate_repair_plan(self.conn, apply=True, confirm=True)
        summary = reverse_duplicate_repair(self.conn, confirm=True)

        restored = self.conn.execute(
            "SELECT review_status, reconciliation_status FROM calendar_event_candidates WHERE id = ?",
            (duplicate_id,),
        ).fetchone()
        self.assertEqual(summary["reconciliations_reversed"], 1)
        self.assertEqual(restored["review_status"], original["review_status"])
        self.assertEqual(restored["reconciliation_status"], original["reconciliation_status"])
        self.assertEqual(
            self.conn.execute(
                "SELECT status, reversed_at FROM candidate_duplicate_reconciliations"
            ).fetchone()["status"],
            "reversed",
        )

    def test_second_reversal_causes_no_changes(self):
        self._make_duplicate_sessions()
        duplicate_repair_plan(self.conn, apply=True, confirm=True)
        reverse_duplicate_repair(self.conn, confirm=True)
        before = state_rows(self.conn)

        summary = reverse_duplicate_repair(self.conn, confirm=True)

        self.assertEqual(state_rows(self.conn), before)
        self.assertEqual(summary["applied_reconciliations_found"], 0)
        self.assertEqual(summary["reconciliations_reversed"], 0)

    def test_unsafe_reversal_is_refused(self):
        rows = self._make_duplicate_sessions()
        duplicate_id = rows[1]["candidate_id"]
        duplicate_repair_plan(self.conn, apply=True, confirm=True)
        self.conn.execute(
            "UPDATE calendar_event_candidates SET review_status = 'needs_rate' WHERE id = ?",
            (duplicate_id,),
        )
        self.conn.commit()

        summary = reverse_duplicate_repair(self.conn, confirm=True)

        self.assertEqual(summary["unsafe_reversals_refused"], 1)
        self.assertEqual(
            self.conn.execute("SELECT status FROM candidate_duplicate_reconciliations").fetchone()["status"],
            "applied",
        )

    def test_backup_failure_prevents_operational_apply(self):
        self._make_duplicate_sessions()
        old_db_path = os.environ.get("JORDANA_DATABASE_PATH")
        os.environ["JORDANA_DATABASE_PATH"] = str(self.db_path)

        def fail_backup(_path):
            raise RuntimeError("backup failed")

        try:
            with self.assertRaises(RuntimeError):
                duplicate_repair_plan(self.conn, apply=True, confirm=True, backup_factory=fail_backup)
        finally:
            if old_db_path is None:
                os.environ.pop("JORDANA_DATABASE_PATH", None)
            else:
                os.environ["JORDANA_DATABASE_PATH"] = old_db_path

        self.assertEqual(count(self.conn, "candidate_duplicate_reconciliations"), 0)

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

    def test_paid_record_is_protected(self):
        rows = self._make_duplicate_sessions()
        self._attach_payment(rows[1]["session_id"])

        summary = duplicate_repair_plan(self.conn, apply=False)["summary"]

        self.assertEqual(summary["protected_paid_records"], 1)
        self.assertEqual(summary["ambiguous_groups_requiring_manual_review"], 1)

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

    def _attach_payment(self, session_id):
        now = now_iso()
        self.conn.execute(
            """
            INSERT INTO billing_parties (
              billing_party_id, billing_party_type, billing_name,
              preferred_delivery_method, created_at, updated_at
            ) VALUES ('party-paid-demo', 'person', 'Demo Payer', 'email', ?, ?)
            """,
            (now, now),
        )
        self.conn.execute(
            """
            INSERT INTO payments (
              payment_id, billing_party_id, amount_cents, received_at,
              method, status, created_at, updated_at
            ) VALUES ('payment-demo', 'party-paid-demo', 15000, '2026-06-30',
              'other', 'posted', ?, ?)
            """,
            (now, now),
        )
        self.conn.execute(
            """
            INSERT INTO payment_allocations (
              allocation_id, payment_id, session_id, amount_cents,
              status, created_at, updated_at
            ) VALUES ('allocation-demo', 'payment-demo', ?, 15000,
              'active', ?, ?)
            """,
            (session_id, now, now),
        )
        self.conn.commit()


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]


def count_where(conn, table, clause, params):
    return conn.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE {clause}", params).fetchone()["c"]


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


def state_rows(conn):
    return {
        "candidates": [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, review_status, reconciliation_status, updated_at
                FROM calendar_event_candidates
                ORDER BY id
                """
            ).fetchall()
        ],
        "sessions": [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, review_status, billable_status, updated_at
                FROM sessions
                ORDER BY id
                """
            ).fetchall()
        ],
        "review_items": [
            dict(row)
            for row in conn.execute(
                """
                SELECT review_item_id, review_status, decision_source, reason, updated_at
                FROM review_items
                ORDER BY review_item_id
                """
            ).fetchall()
        ],
        "reconciliations": [
            dict(row)
            for row in conn.execute(
                """
                SELECT duplicate_candidate_id, status, applied_at, reversed_at, updated_at
                FROM candidate_duplicate_reconciliations
                ORDER BY duplicate_candidate_id
                """
            ).fetchall()
        ],
    }


if __name__ == "__main__":
    unittest.main()
