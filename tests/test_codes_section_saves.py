import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.rates import seed_rate_rule
from jordana_invoice.review_services import (
    create_account,
    create_billing_party,
    create_person,
    get_review_candidate,
    list_review_candidates,
    merge_people,
    save_billing_section,
    save_relationship_section,
    save_session_draft,
    update_person,
)


def raw_row(snapshot_key, title="Fred 830", start="2026-06-17T20:30:00-04:00"):
    return {
        "ingested_at": "2026-06-22T02:00:00.000Z",
        "snapshot_key": snapshot_key,
        "run_id": "run-codes",
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


class CodesAndSectionSaveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "codes.sqlite3")
        init_db(self.conn)
        import_rows(self.conn, [raw_row("snap-1")], "test")
        self.candidate_id = list_review_candidates(self.conn)["items"][0]["candidate_id"]

    def tearDown(self):
        self.temp.cleanup()

    def test_person_code_waits_for_full_name_then_uses_stable_prefix(self):
        fred = create_person(self.conn, "Fred")
        self.assertIsNone(fred["person_code"])
        updated = update_person(self.conn, fred["person_id"], {"display_name": "Fred Colin"})
        self.assertEqual(updated["person_code"], "FCOL-001")
        renamed = update_person(self.conn, fred["person_id"], {"display_name": "Frederick Colin"})
        self.assertEqual(renamed["person_code"], "FCOL-001")

    def test_person_code_suffix_increments_and_merge_preserves_survivor(self):
        first = create_person(self.conn, "Fred Colin")
        second = create_person(self.conn, "Fiona Cole")
        self.assertEqual(first["person_code"], "FCOL-001")
        self.assertEqual(second["person_code"], "FCOL-002")
        merged = merge_people(self.conn, first["person_id"], second["person_id"], "duplicate")
        self.assertEqual(merged["person_code"], "FCOL-001")

    def test_account_code_sequence_and_duplicate_account_reuse(self):
        account = create_account(self.conn, "Fred Household", "household")
        duplicate = create_account(self.conn, "Fred Household", "household")
        next_account = create_account(self.conn, "Simon Family Account", "family")
        self.assertEqual(account["account_code"], "ACCT-0001")
        self.assertEqual(duplicate["account_id"], account["account_id"])
        self.assertEqual(next_account["account_code"], "ACCT-0002")

    def test_section_saves_are_independent_and_refresh_suggestions(self):
        fred = create_person(self.conn, "Fred Colin")
        account = create_account(self.conn, "Fred Household", "household")
        payer = create_billing_party(self.conn, {"billing_name": "Fred Colin", "person_id": fred["person_id"]})
        seed_rate_rule(
            self.conn,
            amount_cents=15000,
            effective_from="2026-01-01",
            duration_minutes=60,
            service_mode="office",
            time_category="standard",
            client_account_id=account["account_id"],
            priority=10,
        )
        self.conn.commit()

        save_session_draft(
            self.conn,
            self.candidate_id,
            {
                "approved_duration_minutes": 60,
                "service_mode": "office",
                "time_category": "standard",
                "approved_rate": "175.00",
                "payment_status": "unpaid",
            },
        )
        save_relationship_section(
            self.conn,
            self.candidate_id,
            {
                "participants": [{"person_id": fred["person_id"], "display_name": "Fred Colin", "is_primary": True}],
                "account_id": account["account_id"],
                "default_billing_party_id": payer["billing_party_id"],
            },
        )
        save_billing_section(self.conn, self.candidate_id, {"billing_party_id": payer["billing_party_id"]})
        detail = get_review_candidate(self.conn, self.candidate_id)
        self.assertEqual(detail["account"]["account_id"], account["account_id"])
        self.assertEqual(detail["billing_party"]["billing_party_id"], payer["billing_party_id"])
        self.assertEqual(detail["session"]["approved_rate_cents"], 17500)
        self.assertEqual(detail["session"]["suggested_rate_cents"], 15000)


if __name__ == "__main__":
    unittest.main()
