import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.review_services import (
    apply_smart_prefill,
    create_account,
    create_billing_party,
    create_person,
    create_rate_rule_from_payload,
    get_review_candidate,
    list_review_candidates,
    save_relationship_section,
)


def raw_row(snapshot_key, title, start="2026-06-17T18:00:00-04:00"):
    return {
        "ingested_at": "2026-06-22T02:00:00.000Z",
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
        "end_at": "2026-06-17T19:00:00-04:00",
        "duration_minutes": "60",
        "calendar": "Jordana Calendar",
        "payload_version": "2",
        "raw_json": "{}",
    }


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]


class AutoLinkExactNameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "auto_link.sqlite3")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _import_one(self, key, title):
        import_rows(self.conn, [raw_row(key, title)], "test")
        items = list_review_candidates(self.conn)["items"]
        return next(i["candidate_id"] for i in items if i["raw_title"] == title)

    def _participant_row(self, session_id):
        return self.conn.execute(
            "SELECT * FROM session_participants WHERE session_id = ?", (session_id,)
        ).fetchone()

    def _session_row(self, candidate_id):
        return self.conn.execute(
            "SELECT * FROM sessions WHERE candidate_id = ?", (candidate_id,)
        ).fetchone()

    def test_exact_active_name_match_links_existing_person(self):
        cid = self._import_one("snap-1", "Robin Rivers 6")
        create_person(self.conn, {"first_name": "Robin", "last_name": "Rivers", "display_name": "Robin Rivers"})
        apply_smart_prefill(self.conn)

        session = self._session_row(cid)
        sp = self._participant_row(session["id"])
        person = self.conn.execute("SELECT * FROM people WHERE display_name = 'Robin Rivers'").fetchone()
        self.assertEqual(sp["person_id"], person["person_id"])
        self.assertEqual(count(self.conn, "people"), 1)

    def test_auto_link_refreshes_person_specific_rate_suggestion(self):
        cid = self._import_one("snap-rate-match", "Robin Rivers 6")
        create_rate_rule_from_payload(
            self.conn,
            {
                "amount": "350",
                "duration_minutes": "60",
                "billing_session_type": "psychotherapy",
                "time_category": "standard",
                "effective_from": "2026-01-01",
            },
        )
        person = create_person(self.conn, {"first_name": "Robin", "last_name": "Rivers", "display_name": "Robin Rivers"})
        create_rate_rule_from_payload(
            self.conn,
            {
                "amount": "500",
                "duration_minutes": "60",
                "billing_session_type": "psychotherapy",
                "time_category": "standard",
                "person_id": person["person_id"],
                "effective_from": "2026-01-01",
            },
        )

        apply_smart_prefill(self.conn)

        detail = get_review_candidate(self.conn, cid)
        self.assertEqual(detail["participants"][0]["person_id"], person["person_id"])
        self.assertEqual(detail["session"]["suggested_rate_cents"], 50000)
        self.assertEqual(detail["session"]["rate_source"], "person_exception")

    def test_matching_is_case_insensitive(self):
        cid = self._import_one("snap-2", "Robin Rivers 6")
        create_person(self.conn, {"first_name": "Robin", "last_name": "Rivers", "display_name": "robin rivers"})
        apply_smart_prefill(self.conn)

        session = self._session_row(cid)
        sp = self._participant_row(session["id"])
        person = self.conn.execute("SELECT * FROM people WHERE active = 1").fetchone()
        self.assertIsNotNone(sp["person_id"])
        self.assertEqual(sp["person_id"], person["person_id"])

    def test_matching_collapses_harmless_whitespace_differences(self):
        cid = self._import_one("snap-3", "Robin  Rivers  6")
        create_person(self.conn, {"first_name": "Robin", "last_name": "Rivers", "display_name": "  Robin   Rivers  "})
        apply_smart_prefill(self.conn)

        session = self._session_row(cid)
        sp = self._participant_row(session["id"])
        person = self.conn.execute("SELECT * FROM people WHERE active = 1").fetchone()
        self.assertIsNotNone(sp["person_id"])
        self.assertEqual(sp["person_id"], person["person_id"])

    def test_partial_fuzzy_names_do_not_match(self):
        cid = self._import_one("snap-4", "Robin 6")
        create_person(self.conn, {"first_name": "Robin", "last_name": "Rivers", "display_name": "Robin Rivers"})
        apply_smart_prefill(self.conn)

        session = self._session_row(cid)
        sp = self._participant_row(session["id"])
        self.assertIsNone(sp["person_id"])

    def test_duplicate_exact_active_matches_do_not_auto_link(self):
        cid = self._import_one("snap-5", "Robin Rivers 6")
        create_person(self.conn, {"first_name": "Robin", "last_name": "Rivers", "display_name": "Robin Rivers"})
        self.conn.execute(
            """
            INSERT INTO people (person_id, display_name, first_name, last_name, active, active_status, created_at, updated_at)
            VALUES (?, 'Robin   Rivers', 'Robin', 'Rivers', 1, 'active', '2026-06-23T00:00:00Z', '2026-06-23T00:00:00Z')
            """,
            ("dup-robin",),
        )
        self.conn.commit()
        apply_smart_prefill(self.conn)

        session = self._session_row(cid)
        sp = self._participant_row(session["id"])
        self.assertIsNone(sp["person_id"])

    def test_no_duplicate_person_created(self):
        cid = self._import_one("snap-6", "Robin Rivers 6")
        create_person(self.conn, {"first_name": "Robin", "last_name": "Rivers", "display_name": "Robin Rivers"})
        before = count(self.conn, "people")
        apply_smart_prefill(self.conn)
        after = count(self.conn, "people")
        self.assertEqual(before, after)

    def test_one_unique_active_billing_party_is_selected(self):
        cid = self._import_one("snap-7", "Robin Rivers 6")
        person = create_person(self.conn, {"first_name": "Robin", "last_name": "Rivers", "display_name": "Robin Rivers"})
        payer = create_billing_party(self.conn, {"billing_name": "Robin Rivers", "billing_party_type": "person", "person_id": person["person_id"]})
        apply_smart_prefill(self.conn)

        session = self._session_row(cid)
        self.assertEqual(session["billing_party_id"], payer["billing_party_id"])

    def test_multiple_active_billing_parties_remain_unresolved(self):
        cid = self._import_one("snap-8", "Robin Rivers 6")
        person = create_person(self.conn, {"first_name": "Robin", "last_name": "Rivers", "display_name": "Robin Rivers"})
        create_billing_party(self.conn, {"billing_name": "Robin Rivers", "billing_party_type": "person", "person_id": person["person_id"]})
        create_billing_party(self.conn, {"billing_name": "Robin Rivers Trust", "billing_party_type": "person", "person_id": person["person_id"]})
        apply_smart_prefill(self.conn)

        session = self._session_row(cid)
        self.assertIsNone(session["billing_party_id"])

    def test_existing_explicit_session_payer_is_not_overwritten(self):
        cid = self._import_one("snap-9", "Robin Rivers 6")
        person = create_person(self.conn, {"first_name": "Robin", "last_name": "Rivers", "display_name": "Robin Rivers"})
        other_payer = create_billing_party(self.conn, {"billing_name": "Other Payer", "billing_party_type": "person"})
        person_payer = create_billing_party(self.conn, {"billing_name": "Robin Rivers", "billing_party_type": "person", "person_id": person["person_id"]})
        session = self._session_row(cid)
        self.conn.execute(
            "UPDATE sessions SET billing_party_id = ? WHERE id = ?",
            (other_payer["billing_party_id"], session["id"]),
        )
        self.conn.commit()
        apply_smart_prefill(self.conn)

        session_after = self._session_row(cid)
        self.assertEqual(session_after["billing_party_id"], other_payer["billing_party_id"])
        self.assertNotEqual(session_after["billing_party_id"], person_payer["billing_party_id"])

    def test_account_default_payer_takes_priority(self):
        cid = self._import_one("snap-10", "Robin Rivers 6")
        person = create_person(self.conn, {"first_name": "Robin", "last_name": "Rivers", "display_name": "Robin Rivers"})
        account = create_account(self.conn, "Rivers Household", "household")
        person_payer = create_billing_party(self.conn, {"billing_name": "Robin Rivers", "billing_party_type": "person", "person_id": person["person_id"]})
        account_payer = create_billing_party(self.conn, {"billing_name": "Rivers Trust", "billing_party_type": "organization"})
        self.conn.execute(
            "UPDATE client_accounts SET default_billing_party_id = ? WHERE account_id = ?",
            (account_payer["billing_party_id"], account["account_id"]),
        )
        self.conn.execute(
            "UPDATE sessions SET account_id = ? WHERE candidate_id = ?",
            (account["account_id"], cid),
        )
        self.conn.commit()
        apply_smart_prefill(self.conn)

        session = self._session_row(cid)
        self.assertEqual(session["billing_party_id"], account_payer["billing_party_id"])
        self.assertNotEqual(session["billing_party_id"], person_payer["billing_party_id"])

    def test_audit_records_are_created(self):
        cid = self._import_one("snap-11", "Robin Rivers 6")
        person = create_person(self.conn, {"first_name": "Robin", "last_name": "Rivers", "display_name": "Robin Rivers"})
        create_billing_party(self.conn, {"billing_name": "Robin Rivers", "billing_party_type": "person", "person_id": person["person_id"]})
        apply_smart_prefill(self.conn)

        session = self._session_row(cid)
        name_audit = self.conn.execute(
            """
            SELECT * FROM audit_log
            WHERE entity_type = 'session' AND entity_id = ? AND action = 'automatic_exact_name_match'
            """,
            (session["id"],),
        ).fetchone()
        self.assertIsNotNone(name_audit, "automatic_exact_name_match audit entry must exist")

        bp_audit = self.conn.execute(
            """
            SELECT * FROM audit_log
            WHERE entity_type = 'session' AND entity_id = ? AND action = 'automatic_billing_party_assigned'
            """,
            (session["id"],),
        ).fetchone()
        self.assertIsNotNone(bp_audit, "automatic_billing_party_assigned audit entry must exist")

    def test_robin_rivers_imported_participant_becomes_clients_ready(self):
        cid = self._import_one("snap-12", "Robin Rivers 6")
        create_person(self.conn, {"first_name": "Robin", "last_name": "Rivers", "display_name": "Robin Rivers"})
        apply_smart_prefill(self.conn)

        detail = get_review_candidate(self.conn, cid)
        self.assertTrue(detail["readiness"]["clients_ready"], "clients_ready must be True after exact-name auto-link")

    def test_unique_payer_makes_billing_ready(self):
        cid = self._import_one("snap-13", "Robin Rivers 6")
        person = create_person(self.conn, {"first_name": "Robin", "last_name": "Rivers", "display_name": "Robin Rivers"})
        create_billing_party(self.conn, {"billing_name": "Robin Rivers", "billing_party_type": "person", "person_id": person["person_id"]})
        apply_smart_prefill(self.conn)

        detail = get_review_candidate(self.conn, cid)
        self.assertTrue(detail["readiness"]["billing_ready"], "billing_ready must be True after unique payer auto-assignment")

    def test_row_summary_uses_resolved_display_name_after_auto_link(self):
        cid = self._import_one("snap-14", "Robin Rivers 6")
        create_person(self.conn, {"first_name": "Robin", "last_name": "Rivers", "display_name": "Robin Rivers"})
        apply_smart_prefill(self.conn)

        items = list_review_candidates(self.conn)["items"]
        item = next(i for i in items if i["candidate_id"] == cid)
        self.assertEqual(
            item["suggested_client"], "Robin Rivers",
            "row_summary suggested_client must use resolved person display_name after auto-link, not raw participant_name",
        )


if __name__ == "__main__":
    unittest.main()
