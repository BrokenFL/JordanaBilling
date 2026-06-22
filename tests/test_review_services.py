import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.review_services import (
    approve_candidate,
    create_account,
    create_billing_party,
    create_person,
    get_review_candidate,
    list_review_candidates,
    save_interpretation,
)


def raw_row(snapshot_key, title="Bobsey and Fred 6", start="2026-06-17T18:00:00-04:00"):
    return {
        "ingested_at": "2026-06-22T02:00:00.000Z",
        "snapshot_key": snapshot_key,
        "run_id": "run-1",
        "batch_name": "test",
        "capture_window": "next_2_days",
        "captured_at": "2026-06-22T01:00:00.000Z",
        "source_device": "test",
        "timezone": "America/New_York",
        "calendar_event_id": "",
        "event_fingerprint": f"fp-{snapshot_key}",
        "event_title": title,
        "start_at": start,
        "end_at": "2026-06-17T19:00:00-04:00",
        "duration_minutes": "60",
        "calendar": "Jordana Calendar",
        "payload_version": "2",
        "raw_json": "{}",
    }


class ReviewServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "review.sqlite3")
        init_db(self.conn)
        import_rows(self.conn, [raw_row("snap-1")], "test")
        first = list_review_candidates(self.conn)["items"][0]
        self.candidate_id = first["candidate_id"]

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_inline_create_records_and_select_immediately(self):
        fred = create_person(self.conn, "Fred Smith")
        bobsey = create_person(self.conn, "Bobsey Smith")
        account = create_account(self.conn, "Fred Household", "household")
        payer = create_billing_party(self.conn, {"billing_name": "Fred Smith", "billing_party_type": "person", "person_id": fred["person_id"]})
        saved = save_interpretation(
            self.conn,
            self.candidate_id,
            {
                "participants": [
                    {"person_id": fred["person_id"], "display_name": "Fred Smith", "is_primary": True},
                    {"person_id": bobsey["person_id"], "display_name": "Bobsey Smith"},
                ],
                "account_id": account["account_id"],
                "billing_party_id": payer["billing_party_id"],
                "approved_duration_minutes": 60,
                "service_mode": "office",
                "time_category": "standard",
                "approved_rate": "150.00",
                "payment_status": "unpaid",
            },
        )
        self.assertEqual(len(saved["participants"]), 2)
        self.assertEqual(saved["account"]["account_name"], "Fred Household")
        self.assertEqual(saved["billing_party"]["billing_name"], "Fred Smith")

    def test_approval_saves_alias_and_one_charge_for_two_people(self):
        fred = create_person(self.conn, "Fred Smith")
        bobsey = create_person(self.conn, "Bobsey Smith")
        account = create_account(self.conn, "Fred Household", "household")
        payer = create_billing_party(self.conn, {"billing_name": "Fred Smith", "billing_party_type": "person"})
        approved = approve_candidate(
            self.conn,
            self.candidate_id,
            {
                "participants": [
                    {"person_id": fred["person_id"], "display_name": "Fred Smith", "is_primary": True},
                    {"person_id": bobsey["person_id"], "display_name": "Bobsey Smith"},
                ],
                "account_id": account["account_id"],
                "billing_party_id": payer["billing_party_id"],
                "approved_duration_minutes": 60,
                "service_mode": "office",
                "time_category": "standard",
                "approved_rate": "150.00",
                "payment_status": "paid",
            },
        )
        self.assertEqual(approved["session"]["review_status"], "approved")
        self.assertEqual(count(self.conn, "sessions"), 1)
        self.assertEqual(count(self.conn, "session_participants"), 2)
        self.assertGreaterEqual(count(self.conn, "calendar_aliases"), 1)
        self.assertGreater(count(self.conn, "audit_log"), 0)

    def test_approval_fails_when_required_fields_missing(self):
        with self.assertRaises(ValueError):
            approve_candidate(self.conn, self.candidate_id, {"participants": []})


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]


if __name__ == "__main__":
    unittest.main()
