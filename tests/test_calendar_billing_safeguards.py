import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import (
    add_sessions_to_draft,
    calendar_source_identity_for_session,
    create_invoice_draft,
    stage_approved_sessions_to_monthly_drafts,
)
from jordana_invoice.review_services import approve_candidate, create_billing_party, create_person
from jordana_invoice.util import stable_hash


def raw_row(
    snapshot_key: str,
    *,
    fingerprint: str,
    start_at: str,
    end_at: str,
) -> dict[str, str]:
    return {
        "ingested_at": "2026-08-20T12:00:00Z",
        "snapshot_key": snapshot_key,
        "run_id": f"run-{snapshot_key}",
        "batch_name": "calendar-billing-safeguard-test",
        "capture_window": "past_3_days",
        "captured_at": "2026-08-20T12:00:00Z",
        "source_device": "test",
        "timezone": "America/New_York",
        "calendar_event_id": "",
        "event_fingerprint": fingerprint,
        "event_title": "Taylor Example | 60 | Office",
        "start_at": start_at,
        "end_at": end_at,
        "duration_minutes": "60",
        "location": "",
        "notes": "",
        "calendar": "Jordana Work",
        "payload_version": "2",
        "raw_json": "{}",
    }


class CalendarBillingSafeguardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "safeguards.sqlite3")
        init_db(self.conn)
        self.person = create_person(
            self.conn,
            {"first_name": "Taylor", "last_name": "Example", "display_name": "Taylor Example"},
        )
        self.party = create_billing_party(
            self.conn,
            {
                "billing_name": "Taylor Example",
                "person_id": self.person["person_id"],
                "billing_email": "taylor@example.test",
                "billing_address_line_1": "1 Test Street",
                "billing_city": "Test",
                "billing_state": "FL",
                "billing_postal_code": "00000",
                "preferred_delivery_method": "email",
            },
        )

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _approved_historical_offset_duplicate(self):
        first = raw_row(
            "first-offset",
            fingerprint="legacy-fingerprint-one",
            start_at="2026-05-10T10:00:00-04:00",
            end_at="2026-05-10T11:00:00-04:00",
        )
        import_rows(self.conn, [first], "test")
        first_candidate = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash("event_fingerprint:legacy-fingerprint-one"),),
        ).fetchone()["id"]
        first_session = self._approve(first_candidate)

        second = raw_row(
            "second-offset",
            fingerprint="legacy-fingerprint-two",
            start_at="2026-05-10T08:00:00-06:00",
            end_at="2026-05-10T09:00:00-06:00",
        )
        import_rows(self.conn, [second], "test")
        second_candidate = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash("event_fingerprint:legacy-fingerprint-two"),),
        ).fetchone()["id"]
        second_session = self._approve(second_candidate)
        return first_session, second_session

    def _approve(self, candidate_id: str):
        detail = approve_candidate(
            self.conn,
            candidate_id,
            {
                "participants": [
                    {
                        "person_id": self.person["person_id"],
                        "display_name": "Taylor Example",
                    }
                ],
                "billing_party_id": self.party["billing_party_id"],
                "approved_duration_minutes": 60,
                "service_mode": "office",
                "time_category": "standard",
                "approved_rate": "150.00",
                "payment_status": "unpaid",
                "billing_treatment": "billable",
            },
        )
        return self.conn.execute(
            "SELECT * FROM sessions WHERE id = ?",
            (detail["session"]["id"],),
        ).fetchone()

    def test_offset_variants_share_structural_billing_identity(self):
        first, second = self._approved_historical_offset_duplicate()
        self.assertEqual(
            calendar_source_identity_for_session(self.conn, first),
            calendar_source_identity_for_session(self.conn, second),
        )

    def test_monthly_staging_keeps_only_one_confirmed_calendar_event(self):
        first, second = self._approved_historical_offset_duplicate()

        result = stage_approved_sessions_to_monthly_drafts(self.conn)

        self.assertEqual(result["sessions_staged"], 1)
        self.assertEqual(len(result["sessions_skipped"]), 1)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM invoice_line_items").fetchone()[0],
            1,
        )
        warning = self.conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM review_items
            WHERE reviewed_at IS NULL
              AND unresolved_fields LIKE '%duplicate_billing_identity_warning%'
            """
        ).fetchone()
        self.assertEqual(warning["c"], 1)

    def test_manual_add_rejects_same_confirmed_calendar_event_and_keeps_warning(self):
        first, second = self._approved_historical_offset_duplicate()
        draft = create_invoice_draft(
            self.conn,
            {
                "bill_to_party_id": self.party["billing_party_id"],
                "billing_period_start": "2026-05-01",
                "billing_period_end": "2026-05-31",
                "invoice_date": "2026-05-31",
                "session_ids": [first["id"]],
            },
        )

        with self.assertRaisesRegex(ValueError, "same confirmed calendar identity"):
            add_sessions_to_draft(self.conn, draft["invoice"]["invoice_id"], [second["id"]])

        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM invoice_line_items").fetchone()[0],
            1,
        )
        warning = self.conn.execute(
            """
            SELECT review_item_id
            FROM review_items
            WHERE session_id = ?
              AND reviewed_at IS NULL
              AND unresolved_fields LIKE '%duplicate_billing_identity_warning%'
            """,
            (second["id"],),
        ).fetchone()
        self.assertIsNotNone(warning)


if __name__ == "__main__":
    unittest.main()
