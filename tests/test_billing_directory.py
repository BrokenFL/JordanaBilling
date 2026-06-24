import io
import json
import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.review_server import make_handler
from jordana_invoice.review_services import (
    approve_candidate,
    create_account,
    add_account_member,
    create_billing_party,
    create_person,
    list_account_records,
    list_billing_relationship_records,
    list_review_candidates,
    save_billing_section,
    save_relationship_section,
)


def raw_row(snapshot_key, title="Robin Rivers 6", start="2026-06-17T18:00:00-04:00"):
    return {
        "ingested_at": "2026-06-22T02:00:00.000Z",
        "snapshot_key": snapshot_key,
        "run_id": "run-dir",
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
    return conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]


class BillingDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "dir.sqlite3")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _import_and_approve(self, snapshot_key, title, participant_ids, billing_party_id, start=None):
        row = raw_row(snapshot_key, title=title, start=start or "2026-06-17T18:00:00-04:00")
        import_rows(self.conn, [row], "test")
        candidate_id = next(
            row["candidate_id"]
            for row in list_review_candidates(self.conn)["items"]
            if row["raw_title"] == title
        )
        approve_candidate(self.conn, candidate_id, {
            "participants": [
                {"person_id": pid, "display_name": pid, "is_primary": idx == 0}
                for idx, pid in enumerate(participant_ids)
            ],
            "billing_party_id": billing_party_id,
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "unpaid",
        })
        return candidate_id

    def _find_record(self, records, billing_party_id):
        return next(r for r in records if r["billing_party_id"] == billing_party_id)

    def test_empty_database_returns_empty_list(self):
        result = list_billing_relationship_records(self.conn)
        self.assertEqual(result, [])

    def test_self_pay_appears_without_client_account(self):
        robin = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        payer = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers",
            "billing_party_type": "person",
            "person_id": robin["person_id"],
        })
        self._import_and_approve("snap-robin", "Robin Rivers 6", [robin["person_id"]], payer["billing_party_id"])

        records = list_billing_relationship_records(self.conn)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["record_type"], "self_pay")
        self.assertEqual(rec["billing_party_id"], payer["billing_party_id"])
        self.assertEqual(rec["payer_person_id"], robin["person_id"])
        self.assertEqual(rec["payer_display_name"], "Robin Rivers")
        self.assertEqual(rec["session_count"], 1)
        self.assertIsNone(rec["account_id"])
        self.assertIsNone(rec["account_type"])
        self.assertTrue(rec["active"])

    def test_third_party_payer_appears_with_covered_client(self):
        taylor = create_person(self.conn, {"display_name": "Taylor Reed", "first_name": "Taylor", "last_name": "Reed"})
        avery = create_person(self.conn, {"display_name": "Avery Stone", "first_name": "Avery", "last_name": "Stone"})
        payer = create_billing_party(self.conn, {
            "billing_name": "Avery Stone",
            "billing_party_type": "person",
            "person_id": avery["person_id"],
        })
        self._import_and_approve("snap-taylor", "Taylor Reed 6", [taylor["person_id"]], payer["billing_party_id"])

        records = list_billing_relationship_records(self.conn)
        rec = self._find_record(records, payer["billing_party_id"])
        self.assertEqual(rec["record_type"], "third_party")
        self.assertEqual(rec["payer_person_id"], avery["person_id"])
        self.assertEqual(rec["payer_display_name"], "Avery Stone")
        covered_ids = [p["person_id"] for p in rec["covered_people"]]
        self.assertIn(taylor["person_id"], covered_ids)
        self.assertNotIn(avery["person_id"], covered_ids)
        self.assertEqual(rec["session_count"], 1)

    def test_one_payer_covering_multiple_people_produces_one_record(self):
        taylor = create_person(self.conn, {"display_name": "Taylor Reed", "first_name": "Taylor", "last_name": "Reed"})
        morgan = create_person(self.conn, {"display_name": "Morgan Lee", "first_name": "Morgan", "last_name": "Lee"})
        avery = create_person(self.conn, {"display_name": "Avery Stone", "first_name": "Avery", "last_name": "Stone"})
        payer = create_billing_party(self.conn, {
            "billing_name": "Avery Stone",
            "billing_party_type": "person",
            "person_id": avery["person_id"],
        })
        self._import_and_approve("snap-t1", "Taylor Reed 6", [taylor["person_id"]], payer["billing_party_id"])
        self._import_and_approve("snap-m1", "Morgan Lee 6", [morgan["person_id"]], payer["billing_party_id"],
                                 start="2026-06-18T18:00:00-04:00")

        records = list_billing_relationship_records(self.conn)
        rec = self._find_record(records, payer["billing_party_id"])
        self.assertEqual(rec["record_type"], "third_party")
        self.assertEqual(rec["session_count"], 2)
        covered_ids = [p["person_id"] for p in rec["covered_people"]]
        self.assertIn(taylor["person_id"], covered_ids)
        self.assertIn(morgan["person_id"], covered_ids)
        self.assertNotIn(avery["person_id"], covered_ids)

    def test_organization_payer_appears_without_account(self):
        taylor = create_person(self.conn, {"display_name": "Taylor Reed", "first_name": "Taylor", "last_name": "Reed"})
        org_payer = create_billing_party(self.conn, {
            "billing_name": "Reed Family Trust",
            "billing_party_type": "organization",
            "organization_name": "Reed Family Trust",
        })
        self._import_and_approve("snap-org", "Taylor Reed 6", [taylor["person_id"]], org_payer["billing_party_id"])

        records = list_billing_relationship_records(self.conn)
        rec = self._find_record(records, org_payer["billing_party_id"])
        self.assertEqual(rec["record_type"], "organization")
        self.assertEqual(rec["billing_party_type"], "organization")
        self.assertEqual(rec["organization_name"], "Reed Family Trust")
        self.assertIsNone(rec["payer_person_id"])
        self.assertIsNone(rec["account_id"])
        covered_ids = [p["person_id"] for p in rec["covered_people"]]
        self.assertIn(taylor["person_id"], covered_ids)

    def test_genuine_account_records_still_appear(self):
        account = create_account(self.conn, "Stone Household", "household")
        records = list_billing_relationship_records(self.conn)
        acct_records = [r for r in records if r["record_type"] == "account"]
        self.assertEqual(len(acct_records), 1)
        self.assertEqual(acct_records[0]["account_id"], account["account_id"])
        self.assertEqual(acct_records[0]["account_name"], "Stone Household")
        self.assertEqual(acct_records[0]["account_type"], "household")
        self.assertTrue(acct_records[0]["active"])

    def test_account_membership_not_invented_from_session_billing(self):
        taylor = create_person(self.conn, {"display_name": "Taylor Reed", "first_name": "Taylor", "last_name": "Reed"})
        avery = create_person(self.conn, {"display_name": "Avery Stone", "first_name": "Avery", "last_name": "Stone"})
        payer = create_billing_party(self.conn, {
            "billing_name": "Avery Stone",
            "billing_party_type": "person",
            "person_id": avery["person_id"],
        })
        self._import_and_approve("snap-no-acct", "Taylor Reed 6", [taylor["person_id"]], payer["billing_party_id"])

        records = list_billing_relationship_records(self.conn)
        acct_records = [r for r in records if r["record_type"] == "account"]
        self.assertEqual(len(acct_records), 0)
        member_count = self.conn.execute("SELECT COUNT(*) FROM account_members").fetchone()[0]
        self.assertEqual(member_count, 0)

    def test_duplicate_sessions_do_not_duplicate_directory_rows(self):
        robin = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        payer = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers",
            "billing_party_type": "person",
            "person_id": robin["person_id"],
        })
        self._import_and_approve("snap-r1", "Robin Rivers 6", [robin["person_id"]], payer["billing_party_id"])
        self._import_and_approve("snap-r2", "Robin Rivers 6", [robin["person_id"]], payer["billing_party_id"],
                                 start="2026-06-18T18:00:00-04:00")

        records = list_billing_relationship_records(self.conn)
        bp_records = [r for r in records if r["billing_party_id"] == payer["billing_party_id"]]
        self.assertEqual(len(bp_records), 1)
        self.assertEqual(bp_records[0]["session_count"], 2)

    def test_inactive_billing_party_remains_visible_and_marked_inactive(self):
        robin = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        payer = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers",
            "billing_party_type": "person",
            "person_id": robin["person_id"],
        })
        self._import_and_approve("snap-inactive", "Robin Rivers 6", [robin["person_id"]], payer["billing_party_id"])
        self.conn.execute("UPDATE billing_parties SET active = 0 WHERE billing_party_id = ?", (payer["billing_party_id"],))
        self.conn.commit()

        records = list_billing_relationship_records(self.conn)
        rec = self._find_record(records, payer["billing_party_id"])
        self.assertFalse(rec["active"])

    def test_unrelated_billing_party_without_evidence_is_excluded(self):
        create_billing_party(self.conn, {
            "billing_name": "Ghost Payer",
            "billing_party_type": "person",
        })
        records = list_billing_relationship_records(self.conn)
        ghost = [r for r in records if r["billing_name"] == "Ghost Payer"]
        self.assertEqual(len(ghost), 0)

    def test_billing_party_linked_as_account_default_still_appears(self):
        fred = create_person(self.conn, {"display_name": "Fred Smith", "first_name": "Fred", "last_name": "Smith"})
        payer = create_billing_party(self.conn, {
            "billing_name": "Fred Smith",
            "billing_party_type": "person",
            "person_id": fred["person_id"],
        })
        account = create_account(self.conn, "Smith Household", "household")
        self.conn.execute(
            "UPDATE client_accounts SET default_billing_party_id = ? WHERE account_id = ?",
            (payer["billing_party_id"], account["account_id"]),
        )
        self.conn.commit()

        records = list_billing_relationship_records(self.conn)
        bp_rec = self._find_record(records, payer["billing_party_id"])
        self.assertIsNotNone(bp_rec)
        self.assertEqual(bp_rec["account_id"], account["account_id"])
        self.assertEqual(bp_rec["account_name"], "Smith Household")
        acct_records = [r for r in records if r["record_type"] == "account"]
        self.assertEqual(len(acct_records), 1)

    def test_genuine_multi_member_account_not_collapsed_to_self_pay(self):
        fred = create_person(self.conn, {"display_name": "Fred Smith", "first_name": "Fred", "last_name": "Smith"})
        bobsey = create_person(self.conn, {"display_name": "Bobsey Smith", "first_name": "Bobsey", "last_name": "Smith"})
        account = create_account(self.conn, "Smith Household", "household")
        add_account_member(self.conn, account["account_id"], fred["person_id"], "primary", True)
        add_account_member(self.conn, account["account_id"], bobsey["person_id"], "family_member", False)
        payer = create_billing_party(self.conn, {
            "billing_name": "Fred Smith",
            "billing_party_type": "person",
            "person_id": fred["person_id"],
        })
        self.conn.execute(
            "UPDATE client_accounts SET default_billing_party_id = ? WHERE account_id = ?",
            (payer["billing_party_id"], account["account_id"]),
        )
        self.conn.commit()
        self._import_and_approve("snap-multi", "Fred and Bobsey 6", [fred["person_id"], bobsey["person_id"]], payer["billing_party_id"])

        records = list_billing_relationship_records(self.conn)
        acct_records = [r for r in records if r["record_type"] == "account"]
        self.assertEqual(len(acct_records), 1)
        self.assertEqual(acct_records[0]["account_id"], account["account_id"])
        member_ids = [m["person_id"] for m in acct_records[0]["covered_people"]]
        self.assertIn(fred["person_id"], member_ids)
        self.assertIn(bobsey["person_id"], member_ids)

    def test_endpoint_reads_create_or_modify_no_records(self):
        robin = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        payer = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers",
            "billing_party_type": "person",
            "person_id": robin["person_id"],
        })
        self._import_and_approve("snap-readonly", "Robin Rivers 6", [robin["person_id"]], payer["billing_party_id"])
        self.conn.commit()

        before = {
            "people": count(self.conn, "people"),
            "billing_parties": count(self.conn, "billing_parties"),
            "sessions": count(self.conn, "sessions"),
            "client_accounts": count(self.conn, "client_accounts"),
            "account_members": count(self.conn, "account_members"),
            "audit_log": count(self.conn, "audit_log"),
        }

        list_billing_relationship_records(self.conn)

        after = {
            "people": count(self.conn, "people"),
            "billing_parties": count(self.conn, "billing_parties"),
            "sessions": count(self.conn, "sessions"),
            "client_accounts": count(self.conn, "client_accounts"),
            "account_members": count(self.conn, "account_members"),
            "audit_log": count(self.conn, "audit_log"),
        }
        self.assertEqual(before, after)

    def test_existing_accounts_endpoint_remains_unchanged(self):
        account = create_account(self.conn, "Test Household", "household")
        fred = create_person(self.conn, {"display_name": "Fred Smith", "first_name": "Fred", "last_name": "Smith"})
        add_account_member(self.conn, account["account_id"], fred["person_id"], "primary", True)

        accounts = list_account_records(self.conn)
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["account_id"], account["account_id"])
        self.assertEqual(accounts[0]["account_name"], "Test Household")

    def test_sorting_active_before_inactive_then_alphabetical(self):
        zoe = create_person(self.conn, {"display_name": "Zoe Active", "first_name": "Zoe", "last_name": "Active"})
        amy = create_person(self.conn, {"display_name": "Amy Inactive", "first_name": "Amy", "last_name": "Inactive"})
        zoe_payer = create_billing_party(self.conn, {
            "billing_name": "Zoe Active", "billing_party_type": "person", "person_id": zoe["person_id"],
        })
        amy_payer = create_billing_party(self.conn, {
            "billing_name": "Amy Inactive", "billing_party_type": "person", "person_id": amy["person_id"],
        })
        self._import_and_approve("snap-zoe", "Zoe Active 6", [zoe["person_id"]], zoe_payer["billing_party_id"])
        self._import_and_approve("snap-amy", "Amy Inactive 6", [amy["person_id"]], amy_payer["billing_party_id"],
                                 start="2026-06-18T18:00:00-04:00")
        self.conn.execute("UPDATE billing_parties SET active = 0 WHERE billing_party_id = ?", (amy_payer["billing_party_id"],))
        self.conn.commit()

        records = list_billing_relationship_records(self.conn)
        bp_records = [r for r in records if r["record_type"] in ("self_pay", "third_party")]
        self.assertTrue(bp_records[0]["active"])
        self.assertFalse(bp_records[1]["active"])
        self.assertEqual(bp_records[0]["payer_display_name"], "Zoe Active")
        self.assertEqual(bp_records[1]["payer_display_name"], "Amy Inactive")

    def test_self_pay_payer_also_paying_for_other_classified_as_third_party(self):
        robin = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        taylor = create_person(self.conn, {"display_name": "Taylor Reed", "first_name": "Taylor", "last_name": "Reed"})
        payer = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers", "billing_party_type": "person", "person_id": robin["person_id"],
        })
        self._import_and_approve("snap-self", "Robin Rivers 6", [robin["person_id"]], payer["billing_party_id"])
        self._import_and_approve("snap-other", "Taylor Reed 6", [taylor["person_id"]], payer["billing_party_id"],
                                 start="2026-06-18T18:00:00-04:00")

        records = list_billing_relationship_records(self.conn)
        rec = self._find_record(records, payer["billing_party_id"])
        self.assertEqual(rec["record_type"], "third_party")
        covered_ids = [p["person_id"] for p in rec["covered_people"]]
        self.assertIn(robin["person_id"], covered_ids)
        self.assertIn(taylor["person_id"], covered_ids)
        self.assertEqual(rec["session_count"], 2)

    def test_organization_payer_without_sessions_but_linked_to_account_appears(self):
        org_payer = create_billing_party(self.conn, {
            "billing_name": "Charity Fund",
            "billing_party_type": "organization",
            "organization_name": "Charity Fund",
        })
        account = create_account(self.conn, "Charity Account", "organization")
        self.conn.execute(
            "UPDATE client_accounts SET default_billing_party_id = ? WHERE account_id = ?",
            (org_payer["billing_party_id"], account["account_id"]),
        )
        self.conn.commit()

        records = list_billing_relationship_records(self.conn)
        bp_rec = next(
            r for r in records
            if r["billing_party_id"] == org_payer["billing_party_id"] and r["record_type"] != "account"
        )
        self.assertIsNotNone(bp_rec)
        self.assertEqual(bp_rec["record_type"], "organization")
        self.assertEqual(bp_rec["account_id"], account["account_id"])


class BillingDirectoryEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "server.sqlite3")
        self.handler_cls = make_handler(self.db_path)
        self.conn = connect(Path(self.temp.name) / "server.sqlite3")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _handler(self, path):
        handler = object.__new__(self.handler_cls)
        handler.path = path
        handler.headers = {}
        handler.rfile = io.BytesIO(b"")
        handler.wfile = io.BytesIO()
        handler.send_error = lambda code: (_ for _ in ()).throw(AssertionError(f"unexpected error {code}"))
        captured = {}
        handler.send_json = lambda payload, status=200: captured.setdefault("payload", payload)
        handler.finish = lambda: None
        return handler, captured

    def test_endpoint_returns_billing_directory(self):
        robin = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        payer = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers", "billing_party_type": "person", "person_id": robin["person_id"],
        })
        import_rows(self.conn, [raw_row("snap-ep", title="Robin Rivers 6")], "test")
        candidate_id = list_review_candidates(self.conn)["items"][0]["candidate_id"]
        approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": robin["person_id"], "display_name": "Robin Rivers", "is_primary": True}],
            "billing_party_id": payer["billing_party_id"],
            "approved_duration_minutes": 60, "service_mode": "office",
            "time_category": "standard", "approved_rate": "200.00", "payment_status": "unpaid",
        })

        handler, captured = self._handler("/api/billing-relationships")
        handler.conn = lambda: self.conn
        handler.do_GET()

        payload = captured["payload"]
        self.assertIsInstance(payload, list)
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["record_type"], "self_pay")
        self.assertEqual(payload[0]["payer_display_name"], "Robin Rivers")

    def test_endpoint_does_not_modify_records(self):
        robin = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        payer = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers", "billing_party_type": "person", "person_id": robin["person_id"],
        })
        import_rows(self.conn, [raw_row("snap-ep2", title="Robin Rivers 6")], "test")
        candidate_id = list_review_candidates(self.conn)["items"][0]["candidate_id"]
        approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": robin["person_id"], "display_name": "Robin Rivers", "is_primary": True}],
            "billing_party_id": payer["billing_party_id"],
            "approved_duration_minutes": 60, "service_mode": "office",
            "time_category": "standard", "approved_rate": "200.00", "payment_status": "unpaid",
        })
        self.conn.commit()

        before_audit = count(self.conn, "audit_log")
        before_bp = count(self.conn, "billing_parties")

        handler, _ = self._handler("/api/billing-relationships")
        handler.conn = lambda: self.conn
        handler.do_GET()

        self.assertEqual(count(self.conn, "audit_log"), before_audit)
        self.assertEqual(count(self.conn, "billing_parties"), before_bp)


if __name__ == "__main__":
    unittest.main()
