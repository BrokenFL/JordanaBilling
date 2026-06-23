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
    save_relationship_section,
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

    def test_parser_candidate_appears_as_proposed_participant_without_creating_person(self):
        candidate_id = self.import_without_persisted_participants("snap-leah", "Leah Grossman 630 30")
        detail = get_review_candidate(self.conn, candidate_id)

        self.assertEqual(detail["participants"][0]["display_name"], "Leah Grossman")
        self.assertTrue(detail["participants"][0]["is_proposed"])
        self.assertEqual(count(self.conn, "people"), 0)

    def test_confirming_exact_existing_person_links_without_duplication(self):
        candidate_id = self.import_without_persisted_participants("snap-existing", "Leah Grossman 630 30")
        person = create_person(self.conn, {"first_name": "Leah", "last_name": "Grossman", "display_name": "Leah Grossman"})
        detail = get_review_candidate(self.conn, candidate_id)

        saved = save_relationship_section(self.conn, candidate_id, {"participants": detail["participants"]})

        self.assertEqual(saved["participants"][0]["person_id"], person["person_id"])
        self.assertEqual(count(self.conn, "people"), 1)

    def test_confirming_new_complete_name_creates_person_once_and_links(self):
        candidate_id = self.import_without_persisted_participants("snap-new", "Leah Grossman 630 30")
        detail = get_review_candidate(self.conn, candidate_id)

        first = save_relationship_section(self.conn, candidate_id, {"participants": detail["participants"]})
        second = save_relationship_section(self.conn, candidate_id, {"participants": first["participants"]})

        self.assertEqual(count(self.conn, "people"), 1)
        self.assertEqual(first["participants"][0]["person_id"], second["participants"][0]["person_id"])
        person = self.conn.execute("SELECT * FROM people").fetchone()
        self.assertEqual(person["display_name"], "Leah Grossman")
        self.assertTrue(person["person_code"])

    def test_incomplete_or_ambiguous_name_stays_uncoded_session_participant(self):
        candidate_id = self.import_without_persisted_participants("snap-short", "Fred 630")
        detail = get_review_candidate(self.conn, candidate_id)

        saved = save_relationship_section(self.conn, candidate_id, {"participants": detail["participants"]})

        self.assertEqual(count(self.conn, "people"), 0)
        self.assertIsNone(saved["participants"][0]["person_id"])
        self.assertEqual(saved["participants"][0]["participant_name"], "Fred")

    def test_editing_proposed_name_before_save_uses_edited_confirmed_name(self):
        candidate_id = self.import_without_persisted_participants("snap-edit", "Leah Grossman 630 30")
        detail = get_review_candidate(self.conn, candidate_id)
        detail["participants"][0]["display_name"] = "Leah Goldberg"
        detail["participants"][0]["participant_name"] = "Leah Goldberg"

        saved = save_relationship_section(self.conn, candidate_id, {"participants": detail["participants"]})

        self.assertEqual(saved["participants"][0]["display_name"], "Leah Goldberg")
        self.assertEqual(count(self.conn, "people"), 1)
        person = self.conn.execute("SELECT * FROM people").fetchone()
        self.assertEqual(person["display_name"], "Leah Goldberg")

    def test_saving_empty_participant_list_clears_participants_and_suppresses_proposal(self):
        candidate_id = self.import_without_persisted_participants("snap-empty", "Leah Grossman 630 30")

        saved = save_relationship_section(self.conn, candidate_id, {"participants": []})

        session_id = saved["session"]["id"]
        self.assertEqual(saved["participants"], [])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS count FROM session_participants WHERE session_id = ?", (session_id,)).fetchone()["count"],
            0,
        )
        self.assertEqual(get_review_candidate(self.conn, candidate_id)["participants"], [])

    def test_confirming_participant_preserves_raw_calendar_evidence(self):
        candidate_id = self.import_without_persisted_participants("snap-raw", "Leah Grossman 630 30")
        before = self.raw_snapshot_for_candidate(candidate_id)
        detail = get_review_candidate(self.conn, candidate_id)

        save_relationship_section(self.conn, candidate_id, {"participants": detail["participants"]})

        after = self.raw_snapshot_for_candidate(candidate_id)
        self.assertEqual(dict(before), dict(after))

    def import_without_persisted_participants(self, snapshot_key, title):
        import_rows(self.conn, [raw_row(snapshot_key, title=title)], "test")
        candidate_id = next(row["candidate_id"] for row in list_review_candidates(self.conn)["items"] if row["raw_title"] == title)
        session = self.conn.execute("SELECT id FROM sessions WHERE candidate_id = ?", (candidate_id,)).fetchone()
        self.conn.execute("DELETE FROM session_participants WHERE session_id = ?", (session["id"],))
        self.conn.commit()
        return candidate_id

    def raw_snapshot_for_candidate(self, candidate_id):
        return self.conn.execute(
            """
            SELECT r.event_title, r.start_at, r.end_at, r.duration_minutes, r.raw_json
            FROM calendar_event_candidates c
            JOIN raw_calendar_snapshots r ON r.id = c.latest_raw_snapshot_id
            WHERE c.id = ?
            """,
            (candidate_id,),
        ).fetchone()


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]


if __name__ == "__main__":
    unittest.main()
