import tempfile
import unittest
from pathlib import Path

from jordana_invoice.calendar_recovery import (
    LEGACY_SUPPRESSION_STATUS,
    calendar_recovery_plan,
    reverse_calendar_recovery,
)
from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import (
    _recalculate,
    create_invoice_draft,
)
from jordana_invoice.review_services import approve_candidate, create_billing_party, create_person
from jordana_invoice.util import new_id, now_iso


def raw_row(
    snapshot_key: str,
    *,
    fingerprint: str,
    start_at: str = "2026-08-10T10:00:00-04:00",
    end_at: str = "2026-08-10T11:00:00-04:00",
    duration: str = "60",
    capture_window: str = "past_3_days",
    captured_at: str = "2026-08-11T16:00:00Z",
) -> dict[str, str]:
    return {
        "ingested_at": captured_at,
        "snapshot_key": snapshot_key,
        "run_id": f"run-{snapshot_key}",
        "batch_name": "calendar-recovery-test",
        "capture_window": capture_window,
        "captured_at": captured_at,
        "source_device": "test",
        "timezone": "America/New_York",
        "calendar_event_id": "",
        "event_fingerprint": fingerprint,
        "event_title": "Taylor Example | 60 | Office",
        "start_at": start_at,
        "end_at": end_at,
        "duration_minutes": duration,
        "location": "",
        "notes": "",
        "calendar": "Jordana Work",
        "payload_version": "2",
        "raw_json": "{}",
    }


class CalendarRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "calendar-recovery.sqlite3")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _candidate_ids(self) -> list[str]:
        return [row["id"] for row in self.conn.execute(
            "SELECT id FROM calendar_event_candidates ORDER BY created_at, id"
        ).fetchall()]

    def _simulate_legacy_suppression(self, candidate_id: str) -> None:
        now = now_iso()
        self.conn.execute(
            """
            UPDATE calendar_event_candidates
            SET review_status = 'excluded', hidden_from_review = 1,
                reconciliation_status = ?, updated_at = ?
            WHERE id = ?
            """,
            (LEGACY_SUPPRESSION_STATUS, now, candidate_id),
        )
        self.conn.execute(
            """
            UPDATE sessions
            SET review_status = 'excluded', billable_status = 'excluded',
                payment_status = 'unpaid', hidden_from_review = 1, updated_at = ?
            WHERE candidate_id = ?
            """,
            (now, candidate_id),
        )
        self.conn.commit()

    def test_restores_only_post_end_past_capture_and_reverses_safely(self):
        import_rows(
            self.conn,
            [
                raw_row("positive", fingerprint="positive"),
                raw_row(
                    "future-only",
                    fingerprint="future-only",
                    start_at="2026-08-15T10:00:00-04:00",
                    end_at="2026-08-15T11:00:00-04:00",
                    capture_window="next_7_days",
                    captured_at="2026-08-14T16:00:00Z",
                ),
            ],
            "test",
        )
        positive, future_only = self._candidate_ids()
        self._simulate_legacy_suppression(positive)
        self._simulate_legacy_suppression(future_only)

        plan = calendar_recovery_plan(self.conn, month="2026-08")
        self.assertEqual(plan["summary"]["legacy_candidates_restorable"], 1)
        self.assertEqual(plan["summary"]["draft_duplicate_lines_to_remove"], 0)
        self.assertEqual(
            plan["summary"]["legacy_candidates_without_post_end_past_evidence"],
            1,
        )

        with self.assertRaisesRegex(ValueError, "explicit confirmation"):
            calendar_recovery_plan(self.conn, apply=True, month="2026-08")
        result = calendar_recovery_plan(self.conn, apply=True, confirm=True, month="2026-08")
        self.assertEqual(result["summary"]["candidates_restored"], 1)

        restored = self.conn.execute(
            """
            SELECT c.review_status, c.hidden_from_review, c.reconciliation_status,
                   s.review_status AS session_review_status, s.billable_status
            FROM calendar_event_candidates c JOIN sessions s ON s.candidate_id = c.id
            WHERE c.id = ?
            """,
            (positive,),
        ).fetchone()
        self.assertEqual(restored["review_status"], "needs_review")
        self.assertEqual(restored["hidden_from_review"], 0)
        self.assertEqual(restored["reconciliation_status"], "restored_from_legacy_calendar_suppression")
        self.assertEqual(restored["session_review_status"], "needs_review")
        self.assertEqual(restored["billable_status"], "proposed")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM invoice_line_items").fetchone()[0],
            0,
        )
        untouched = self.conn.execute(
            "SELECT review_status, reconciliation_status FROM calendar_event_candidates WHERE id = ?",
            (future_only,),
        ).fetchone()
        self.assertEqual(untouched["review_status"], "excluded")
        self.assertEqual(untouched["reconciliation_status"], LEGACY_SUPPRESSION_STATUS)

        reversal = reverse_calendar_recovery(self.conn, confirm=True)
        self.assertEqual(reversal["actions_reversed"], 1)
        reversed_row = self.conn.execute(
            """
            SELECT c.review_status, c.hidden_from_review, c.reconciliation_status,
                   s.review_status AS session_review_status, s.billable_status
            FROM calendar_event_candidates c JOIN sessions s ON s.candidate_id = c.id
            WHERE c.id = ?
            """,
            (positive,),
        ).fetchone()
        self.assertEqual(reversed_row["review_status"], "excluded")
        self.assertEqual(reversed_row["hidden_from_review"], 1)
        self.assertEqual(reversed_row["reconciliation_status"], LEGACY_SUPPRESSION_STATUS)
        self.assertEqual(reversed_row["session_review_status"], "excluded")
        self.assertEqual(reversed_row["billable_status"], "excluded")

    def test_offset_variants_restore_one_candidate_and_quarantine_the_duplicate(self):
        import_rows(
            self.conn,
            [
                raw_row("east", fingerprint="legacy-east"),
                raw_row(
                    "west",
                    fingerprint="legacy-west",
                    start_at="2026-08-10T08:00:00-06:00",
                    end_at="2026-08-10T09:00:00-06:00",
                ),
            ],
            "test",
        )
        candidates = self._candidate_ids()
        self.assertEqual(len(candidates), 2)
        for candidate_id in candidates:
            self._simulate_legacy_suppression(candidate_id)

        plan = calendar_recovery_plan(self.conn, month="2026-08")
        self.assertEqual(plan["summary"]["legacy_candidates_restorable"], 1)
        self.assertEqual(plan["summary"]["legacy_duplicate_candidates_to_quarantine"], 1)
        result = calendar_recovery_plan(self.conn, apply=True, confirm=True, month="2026-08")
        self.assertEqual(result["summary"]["candidates_restored"], 1)
        self.assertEqual(result["summary"]["duplicate_candidates_quarantined"], 1)
        rows = self.conn.execute(
            "SELECT review_status, reconciliation_status FROM calendar_event_candidates ORDER BY id"
        ).fetchall()
        self.assertEqual(sum(row["review_status"] == "needs_review" for row in rows), 1)
        self.assertEqual(
            sum(row["reconciliation_status"] == "reconciled_legacy_calendar_duplicate" for row in rows),
            1,
        )

    def test_conflicting_legacy_duration_remains_a_warning_not_a_restored_session(self):
        import_rows(
            self.conn,
            [
                raw_row("sixty", fingerprint="legacy-sixty"),
                raw_row(
                    "thirty",
                    fingerprint="legacy-thirty",
                    end_at="2026-08-10T10:30:00-04:00",
                    duration="30",
                ),
            ],
            "test",
        )
        for candidate_id in self._candidate_ids():
            self._simulate_legacy_suppression(candidate_id)

        plan = calendar_recovery_plan(self.conn, month="2026-08")
        self.assertEqual(plan["summary"]["legacy_candidates_restorable"], 0)
        self.assertEqual(plan["summary"]["legacy_duration_conflicts_to_flag"], 2)
        calendar_recovery_plan(self.conn, apply=True, confirm=True, month="2026-08")
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM calendar_event_candidates WHERE review_status = 'excluded'"
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            self.conn.execute(
                """
                SELECT COUNT(*) FROM review_items
                WHERE review_status = 'source_change_warning'
                  AND unresolved_fields LIKE '%legacy_calendar_duration_conflict%'
                  AND reviewed_at IS NULL
                """
            ).fetchone()[0],
            2,
        )

    def test_removes_and_restores_an_exact_duplicate_line_in_a_draft(self):
        import_rows(
            self.conn,
            [
                raw_row("first", fingerprint="invoice-first"),
                raw_row("second", fingerprint="invoice-second"),
            ],
            "test",
        )
        person = create_person(
            self.conn,
            {"first_name": "Taylor", "last_name": "Example", "display_name": "Taylor Example"},
        )
        party = create_billing_party(
            self.conn,
            {
                "billing_name": "Taylor Example",
                "person_id": person["person_id"],
                "billing_email": "taylor@example.test",
                "billing_address_line_1": "1 Test Street",
                "billing_city": "Test",
                "billing_state": "FL",
                "billing_postal_code": "00000",
                "preferred_delivery_method": "email",
            },
        )
        sessions = []
        for candidate_id in self._candidate_ids():
            approved = approve_candidate(
                self.conn,
                candidate_id,
                {
                    "participants": [{"person_id": person["person_id"], "display_name": "Taylor Example"}],
                    "billing_party_id": party["billing_party_id"],
                    "approved_duration_minutes": 60,
                    "service_mode": "office",
                    "time_category": "standard",
                    "approved_rate": "150.00",
                    "payment_status": "unpaid",
                    "billing_treatment": "billable",
                },
            )
            sessions.append(approved["session"]["id"])
        draft = create_invoice_draft(
            self.conn,
            {
                "bill_to_party_id": party["billing_party_id"],
                "billing_period_start": "2026-08-01",
                "billing_period_end": "2026-08-31",
                "invoice_date": "2026-08-31",
                "session_ids": [sessions[0]],
            },
        )["invoice"]
        invoice_id = draft["invoice_id"]
        original = dict(self.conn.execute(
            "SELECT * FROM invoice_line_items WHERE invoice_id = ?", (invoice_id,)
        ).fetchone())
        duplicate = dict(original)
        duplicate["invoice_line_item_id"] = new_id()
        duplicate["source_session_id"] = sessions[1]
        duplicate["sort_order"] = int(duplicate["sort_order"]) + 1
        duplicate["created_at"] = now_iso()
        duplicate["updated_at"] = now_iso()
        columns = tuple(duplicate.keys())
        self.conn.execute(
            f"INSERT INTO invoice_line_items ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            tuple(duplicate[column] for column in columns),
        )
        _recalculate(self.conn, invoice_id)
        self.conn.execute("UPDATE invoices SET revision = revision + 1 WHERE invoice_id = ?", (invoice_id,))
        self.conn.commit()

        plan = calendar_recovery_plan(self.conn, month="2026-08")
        self.assertEqual(plan["summary"]["draft_duplicate_lines_to_remove"], 1)
        result = calendar_recovery_plan(self.conn, apply=True, confirm=True, month="2026-08")
        self.assertEqual(result["summary"]["draft_duplicate_lines_removed"], 1)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM invoice_line_items WHERE invoice_id = ?", (invoice_id,)).fetchone()[0],
            1,
        )
        reversal = reverse_calendar_recovery(self.conn, confirm=True)
        self.assertEqual(reversal["actions_reversed"], 1)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM invoice_line_items WHERE invoice_id = ?", (invoice_id,)).fetchone()[0],
            2,
        )


if __name__ == "__main__":
    unittest.main()
