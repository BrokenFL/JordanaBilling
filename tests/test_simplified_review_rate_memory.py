import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.rates import seed_rate_rule
from jordana_invoice.review_services import (
    approve_candidate,
    create_billing_party,
    create_person,
    get_review_candidate,
    list_review_candidates,
    save_billing_section,
    save_relationship_section,
    save_session_draft,
)


def raw_row(snapshot_key, title="Fred 830", start="2026-06-17T20:30:00-04:00", end="2026-06-17T21:30:00-04:00", duration="60"):
    return {
        "ingested_at": f"2026-06-22T02:00:{snapshot_key[-1] if snapshot_key[-1].isdigit() else '0'}.000Z",
        "snapshot_key": snapshot_key,
        "run_id": f"run-{snapshot_key}",
        "batch_name": "test",
        "capture_window": "next_2_days",
        "captured_at": "2026-06-22T01:00:00.000Z",
        "source_device": "test",
        "timezone": "America/New_York",
        "calendar_event_id": "",
        "event_fingerprint": f"fp-{snapshot_key}",
        "event_title": title,
        "start_at": start,
        "end_at": end,
        "duration_minutes": duration,
        "calendar": "Jordana Calendar",
        "payload_version": "2",
        "raw_json": "{}",
    }


class SimplifiedReviewRateMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "review-rate.sqlite3")
        init_db(self.conn)

    def tearDown(self):
        self.temp.cleanup()

    def import_one(self, key="snap-1", title="Fred 830", start="2026-06-17T20:30:00-04:00", end="2026-06-17T21:30:00-04:00", duration="60"):
        import_rows(self.conn, [raw_row(key, title, start, end, duration)], "test")
        rows = list_review_candidates(self.conn)["items"]
        return next(row["candidate_id"] for row in rows if row["raw_title"] == title)

    def test_fred_830_approves_with_participant_and_bill_to_no_account_required(self):
        candidate_id = self.import_one()
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Colin", "display_name": "Fred Colin"})
        payer = create_billing_party(self.conn, {"billing_name": "Fred Colin", "person_id": fred["person_id"]})
        approved = approve_candidate(
            self.conn,
            candidate_id,
            {
                "participants": [{"person_id": fred["person_id"], "display_name": "Fred Colin"}],
                "billing_party_id": payer["billing_party_id"],
                "approved_duration_minutes": 60,
                "service_mode": "phone",
                "time_category": "evening",
                "approved_rate": "150.00",
                "payment_status": "paid",
            },
        )
        self.assertEqual(approved["session"]["review_status"], "approved")
        self.assertIsNone(approved["session"]["account_id"])
        self.assertEqual(approved["session"]["rate_cents_snapshot"], 15000)

    def test_simon_billed_to_parent_does_not_add_parent_as_participant(self):
        candidate_id = self.import_one("snap-s", "Simon 2", "2026-06-18T14:00:00-04:00", "2026-06-18T15:00:00-04:00")
        simon = create_person(self.conn, {"first_name": "Simon", "last_name": "Client", "display_name": "Simon"})
        parent = create_billing_party(self.conn, {"billing_name": "Simon's mother", "billing_party_type": "person"})
        save_relationship_section(
            self.conn,
            candidate_id,
            {"participants": [{"person_id": simon["person_id"], "display_name": "Simon"}]},
        )
        save_billing_section(self.conn, candidate_id, {"billing_party_id": parent["billing_party_id"]})
        detail = get_review_candidate(self.conn, candidate_id)
        self.assertEqual([p["person_id"] for p in detail["participants"]], [simon["person_id"]])
        self.assertEqual(detail["billing_party"]["billing_party_id"], parent["billing_party_id"])

    def test_sole_participant_defaults_bill_to_and_reuses_later(self):
        first = self.import_one("snap-a", "Fred 5", "2026-06-18T17:00:00-04:00", "2026-06-18T18:00:00-04:00")
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Colin", "display_name": "Fred Colin"})
        save_relationship_section(
            self.conn,
            first,
            {"participants": [{"person_id": fred["person_id"], "display_name": "Fred Colin"}]},
        )
        detail = get_review_candidate(self.conn, first)
        self.assertEqual(detail["billing_party"]["person_id"], fred["person_id"])
        approve_candidate(
            self.conn,
            first,
            {
                "participants": [{"person_id": fred["person_id"], "display_name": "Fred Colin"}],
                "billing_party_id": detail["billing_party"]["billing_party_id"],
                "approved_duration_minutes": 60,
                "service_mode": "phone",
                "time_category": "standard",
                "approved_rate": "125.00",
                "payment_status": "paid",
            },
        )
        second = self.import_one("snap-b", "Fred 6", "2026-06-19T18:00:00-04:00", "2026-06-19T19:00:00-04:00")
        save_relationship_section(
            self.conn,
            second,
            {"participants": [{"person_id": fred["person_id"], "display_name": "Fred Colin"}]},
        )
        reused = get_review_candidate(self.conn, second)
        self.assertEqual(reused["billing_party"]["person_id"], fred["person_id"])

    def test_future_person_rate_exception_does_not_rewrite_old_approved_rate(self):
        seed_rate_rule(self.conn, 10000, "2026-01-01", duration_minutes=60, service_mode="phone", time_category="standard")
        candidate_id = self.import_one("snap-r1", "Fred 5", "2026-06-18T17:00:00-04:00", "2026-06-18T18:00:00-04:00")
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Colin", "display_name": "Fred Colin"})
        payer = create_billing_party(self.conn, {"billing_name": "Fred Colin", "person_id": fred["person_id"]})
        save_relationship_section(
            self.conn,
            candidate_id,
            {"participants": [{"person_id": fred["person_id"], "display_name": "Fred Colin"}]},
        )
        approve_candidate(
            self.conn,
            candidate_id,
            {
                "participants": [{"person_id": fred["person_id"], "display_name": "Fred Colin"}],
                "billing_party_id": payer["billing_party_id"],
                "approved_duration_minutes": 60,
                "service_mode": "phone",
                "time_category": "standard",
                "approved_rate": "175.00",
                "payment_status": "paid",
                "rate_scope": "future_person",
                "rate_scope_person_id": fred["person_id"],
            },
        )
        old = get_review_candidate(self.conn, candidate_id)["session"]
        self.assertEqual(old["rate_cents_snapshot"], 17500)
        self.assertEqual(old["approved_rate_cents"], 17500)
        self.assertEqual(old["approved_rate_source"], "person_exception")
        self.conn.execute("UPDATE rate_rules SET amount_cents = 22500 WHERE person_id = ?", (fred["person_id"],))
        unchanged = get_review_candidate(self.conn, candidate_id)["session"]
        self.assertEqual(unchanged["rate_cents_snapshot"], 17500)

    def test_joint_rate_exception_is_order_independent_and_requires_exact_group(self):
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Colin", "display_name": "Fred Colin"})
        bobsy = create_person(self.conn, {"first_name": "Bobsy", "last_name": "Colin", "display_name": "Bobsy Colin"})
        candidate_id = self.import_one("snap-j1", "Fred and Bobsy 6", "2026-06-18T18:00:00-04:00", "2026-06-18T19:00:00-04:00")
        save_relationship_section(
            self.conn,
            candidate_id,
            {
                "participants": [
                    {"person_id": fred["person_id"], "display_name": "Fred Colin"},
                    {"person_id": bobsy["person_id"], "display_name": "Bobsy Colin"},
                ]
            },
        )
        save_session_draft(
            self.conn,
            candidate_id,
            {
                "approved_duration_minutes": 60,
                "service_mode": "phone",
                "time_category": "standard",
                "approved_rate": "190.00",
                "payment_status": "unpaid",
                "rate_scope": "future_joint",
            },
        )
        reversed_id = self.import_one("snap-j2", "Bobsy and Fred 3", "2026-06-19T15:00:00-04:00", "2026-06-19T16:00:00-04:00")
        save_relationship_section(
            self.conn,
            reversed_id,
            {
                "participants": [
                    {"person_id": bobsy["person_id"], "display_name": "Bobsy Colin"},
                    {"person_id": fred["person_id"], "display_name": "Fred Colin"},
                ]
            },
        )
        save_session_draft(
            self.conn,
            reversed_id,
            {
                "approved_duration_minutes": 60,
                "service_mode": "phone",
                "time_category": "standard",
                "payment_status": "unpaid",
            },
        )
        detail = get_review_candidate(self.conn, reversed_id)
        self.assertEqual(detail["session"]["suggested_rate_cents"], 19000)
        self.assertEqual(detail["session"]["rate_source"], "participant_combination_exception")

        solo_id = self.import_one("snap-j3", "Fred 3", "2026-06-20T15:00:00-04:00", "2026-06-20T16:00:00-04:00")
        save_relationship_section(
            self.conn,
            solo_id,
            {"participants": [{"person_id": fred["person_id"], "display_name": "Fred Colin"}]},
        )
        solo = get_review_candidate(self.conn, solo_id)
        self.assertNotEqual(solo["session"]["suggested_rate_cents"], 19000)

    def test_rate_rule_participant_migration_is_idempotent(self):
        init_db(self.conn)
        init_db(self.conn)
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'rate_rule_participants'"
        ).fetchone()
        self.assertIsNotNone(row)


if __name__ == "__main__":
    unittest.main()
