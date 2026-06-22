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
    mark_candidate,
    merge_people,
    similar_people,
    update_person,
)


def raw_row(snapshot_key, title="Fred 830", start="2026-06-17T20:30:00-04:00"):
    return {
        "ingested_at": f"2026-06-22T02:00:{snapshot_key[-1]}.000Z",
        "snapshot_key": snapshot_key,
        "run_id": "run-identity",
        "batch_name": "test",
        "capture_window": "next_2_days",
        "captured_at": "2026-06-22T01:00:00.000Z",
        "source_device": "test",
        "timezone": "America/New_York",
        "calendar_event_id": "",
        "event_fingerprint": f"fp-{snapshot_key}",
        "event_title": title,
        "start_at": start,
        "end_at": "2026-06-17T21:30:00-04:00",
        "duration_minutes": "60",
        "calendar": "Jordana Calendar",
        "payload_version": "2",
        "raw_json": "{}",
    }


class IdentityRelationshipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "identity.sqlite3")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def import_one(self, title="Fred 830", key="snap-1"):
        import_rows(self.conn, [raw_row(key, title=title)], "test")
        return list_review_candidates(self.conn)["items"][0]["candidate_id"]

    def test_correct_fred_to_fred_colin_without_duplicate(self):
        fred = create_person(self.conn, "Fred")
        updated = update_person(self.conn, fred["person_id"], {"display_name": "Fred Colin"})
        self.assertEqual(updated["person_id"], fred["person_id"])
        self.assertEqual(updated["display_name"], "Fred Colin")
        self.assertEqual(count(self.conn, "people"), 1)
        alias = self.conn.execute(
            "SELECT * FROM calendar_aliases WHERE normalized_alias = 'fred'"
        ).fetchone()
        self.assertEqual(alias["person_id"], fred["person_id"])
        self.assertGreater(count(self.conn, "audit_log"), 0)

    def test_duplicate_warning_finds_fred_for_fred_colin(self):
        create_person(self.conn, "Fred")
        matches = similar_people(self.conn, "Fred Colin")
        self.assertEqual(matches[0]["display_name"], "Fred")

    def test_merge_people_transfers_sessions_aliases_memberships(self):
        candidate_id = self.import_one()
        fred = create_person(self.conn, "Fred")
        fred_colin = create_person(self.conn, "Fred Colin")
        account = create_account(self.conn, "Fred Household", "household")
        payer = create_billing_party(self.conn, {"billing_name": "Fred Colin", "person_id": fred_colin["person_id"]})
        approve_candidate(
            self.conn,
            candidate_id,
            {
                "participants": [{"person_id": fred["person_id"], "display_name": "Fred", "is_primary": True}],
                "account_id": account["account_id"],
                "billing_party_id": payer["billing_party_id"],
                "approved_duration_minutes": 60,
                "service_mode": "office",
                "time_category": "evening",
                "approved_rate": "150.00",
                "payment_status": "paid",
            },
        )
        merge_people(self.conn, fred_colin["person_id"], fred["person_id"], "same human")
        participant = self.conn.execute("SELECT person_id FROM session_participants").fetchone()
        duplicate = self.conn.execute("SELECT active_status FROM people WHERE person_id = ?", (fred["person_id"],)).fetchone()
        self.assertEqual(participant["person_id"], fred_colin["person_id"])
        self.assertEqual(duplicate["active_status"], "merged")

    def test_smart_prefill_after_approved_alias(self):
        candidate_id = self.import_one(key="snap-1")
        fred = create_person(self.conn, "Fred Colin")
        account = create_account(self.conn, "Fred Household", "household")
        payer = create_billing_party(self.conn, {"billing_name": "Fred Colin", "person_id": fred["person_id"]})
        self.conn.execute(
            "UPDATE client_accounts SET default_billing_party_id = ? WHERE account_id = ?",
            (payer["billing_party_id"], account["account_id"]),
        )
        approve_candidate(
            self.conn,
            candidate_id,
            {
                "participants": [{"person_id": fred["person_id"], "display_name": "Fred Colin", "is_primary": True}],
                "account_id": account["account_id"],
                "billing_party_id": payer["billing_party_id"],
                "approved_duration_minutes": 60,
                "service_mode": "office",
                "time_category": "evening",
                "approved_rate": "150.00",
                "payment_status": "paid",
            },
        )
        new_candidate = self.import_one(key="snap-2")
        detail = get_review_candidate(self.conn, new_candidate)
        self.assertEqual(detail["account"]["account_id"], account["account_id"])
        self.assertEqual(detail["billing_party"]["billing_party_id"], payer["billing_party_id"])
        self.assertEqual(detail["participants"][0]["person_id"], fred["person_id"])

    def test_personal_admin_rows_are_reviewable(self):
        import_rows(self.conn, [raw_row("snap-p", title="Mani pedi 4", start="2026-06-17T16:00:00-04:00")], "test")
        rows = list_review_candidates(self.conn)["items"]
        self.assertTrue(any(row["classification"] == "personal" for row in rows))
        personal = next(row for row in rows if row["classification"] == "personal")
        mark_candidate(self.conn, personal["candidate_id"], classification="personal", reason="confirmed personal")
        alias = self.conn.execute("SELECT * FROM calendar_aliases WHERE classification = 'personal'").fetchone()
        self.assertIsNotNone(alias)


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]


if __name__ == "__main__":
    unittest.main()
