import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path

from jordana_invoice.db import connect, init_db
from jordana_invoice.review_server import make_handler
from jordana_invoice.review_services import (
    add_account_member,
    analyze_billing_relationship_duplicates,
    create_account,
    create_billing_party,
    create_person,
    get_account_record,
    reactivate_account,
    setup_billing_relationship,
    update_billing_relationship,
)


def _count(conn, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


class BillingRelationshipDuplicatePreventionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.conn = connect(self.db_path)
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _person(self, name: str):
        first, _, last = name.partition(" ")
        return create_person(self.conn, {
            "display_name": name,
            "first_name": first,
            "last_name": last or "Client",
        })

    def test_new_valid_relationship_reuses_existing_payer_billing_party(self):
        payer = self._person("Rebecca Colin")
        barbara = self._person("Barbara Colin")
        existing_bp = create_billing_party(self.conn, {
            "billing_name": "Rebecca Colin",
            "billing_party_type": "person",
            "person_id": payer["person_id"],
            "billing_email": "rebecca@example.test",
            "preferred_delivery_method": "email",
        })

        self_pay = setup_billing_relationship(self.conn, {
            "payer_kind": "client",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [payer["person_id"]],
        })
        shared = setup_billing_relationship(self.conn, {
            "payer_kind": "client",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [payer["person_id"], barbara["person_id"]],
        })

        self.assertEqual(self_pay["billing_party_id"], existing_bp["billing_party_id"])
        self.assertEqual(shared["billing_party_id"], existing_bp["billing_party_id"])
        self.assertEqual(_count(self.conn, "billing_parties"), 1)

    def test_editing_delivery_updates_shared_canonical_bill_to_record(self):
        payer = self._person("Rebecca Colin")
        barbara = self._person("Barbara Colin")
        create_billing_party(self.conn, {
            "billing_name": "Rebecca Colin",
            "billing_party_type": "person",
            "person_id": payer["person_id"],
            "billing_email": "old@example.test",
            "preferred_delivery_method": "email",
        })
        self_pay = setup_billing_relationship(self.conn, {
            "payer_kind": "client",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [payer["person_id"]],
        })
        shared = setup_billing_relationship(self.conn, {
            "payer_kind": "client",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [payer["person_id"], barbara["person_id"]],
        })

        update_billing_relationship(self.conn, self_pay["account_id"], {
            "payer_kind": "client",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [payer["person_id"]],
            "billing_delivery": {
                "billing_name": "Rebecca Colin",
                "billing_email": "shared@example.test",
                "billing_phone": "555-2222",
                "preferred_delivery_method": "both",
            },
        })

        self_pay_record = get_account_record(self.conn, self_pay["account_id"])
        shared_record = get_account_record(self.conn, shared["account_id"])
        self.assertEqual(
            self_pay_record["account"]["default_billing_party_id"],
            shared_record["account"]["default_billing_party_id"],
        )
        self.assertEqual(shared_record["billing_party"]["billing_email"], "shared@example.test")
        self.assertEqual(shared_record["billing_party"]["billing_phone"], "555-2222")
        self.assertEqual(shared_record["billing_party"]["preferred_delivery_method"], "both")

    def test_reactivating_inactive_duplicate_is_blocked(self):
        payer = self._person("Robin Rivers")
        first = setup_billing_relationship(self.conn, {
            "payer_kind": "client",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [payer["person_id"]],
        })
        self.conn.execute(
            "UPDATE client_accounts SET active = 0 WHERE account_id = ?",
            (first["account_id"],),
        )
        self.conn.execute(
            "DELETE FROM billing_relationship_keys WHERE account_id = ?",
            (first["account_id"],),
        )
        self.conn.commit()

        second = setup_billing_relationship(self.conn, {
            "payer_kind": "client",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [payer["person_id"]],
        })
        self.assertNotEqual(first["account_id"], second["account_id"])

        with self.assertRaises(ValueError) as ctx:
            reactivate_account(self.conn, first["account_id"])
        self.assertIn("already exists", str(ctx.exception))

    def test_duplicate_analyzer_is_read_only_and_detects_legacy_conflicts(self):
        payer = self._person("Rebecca Colin")
        barbara = self._person("Barbara Colin")
        bp1 = create_billing_party(self.conn, {
            "billing_name": "Rebecca Colin",
            "billing_party_type": "person",
            "person_id": payer["person_id"],
            "billing_email": "one@example.test",
        })
        bp2 = create_billing_party(self.conn, {
            "billing_name": "Rebecca Colin Alt",
            "billing_party_type": "person",
            "person_id": payer["person_id"],
            "billing_email": "two@example.test",
        })
        acct1 = create_account(self.conn, "Rebecca pays for Rebecca and Barbara", "family")
        add_account_member(self.conn, acct1["account_id"], payer["person_id"], "primary", True)
        add_account_member(self.conn, acct1["account_id"], barbara["person_id"], "family_member", False)
        self.conn.execute(
            "UPDATE client_accounts SET default_billing_party_id = ? WHERE account_id = ?",
            (bp1["billing_party_id"], acct1["account_id"]),
        )
        acct2 = create_account(self.conn, "Rebecca alt label", "family")
        add_account_member(self.conn, acct2["account_id"], payer["person_id"], "primary", True)
        add_account_member(self.conn, acct2["account_id"], barbara["person_id"], "family_member", False)
        self.conn.execute(
            "UPDATE client_accounts SET default_billing_party_id = ? WHERE account_id = ?",
            (bp2["billing_party_id"], acct2["account_id"]),
        )
        self.conn.commit()

        before = {
            "accounts": _count(self.conn, "client_accounts"),
            "members": _count(self.conn, "account_members"),
            "billing_parties": _count(self.conn, "billing_parties"),
            "audit_log": _count(self.conn, "audit_log"),
        }
        analysis = analyze_billing_relationship_duplicates(self.conn)
        after = {
            "accounts": _count(self.conn, "client_accounts"),
            "members": _count(self.conn, "account_members"),
            "billing_parties": _count(self.conn, "billing_parties"),
            "audit_log": _count(self.conn, "audit_log"),
        }

        self.assertEqual(before, after)
        self.assertEqual(analysis["summary"]["exact_active_duplicate_group_count"], 1)
        self.assertEqual(analysis["summary"]["payer_record_conflict_count"], 1)
        self.assertIn(acct1["account_id"], analysis["duplicate_account_ids"])
        self.assertIn(acct2["account_id"], analysis["duplicate_account_ids"])
        self.assertIn(acct1["account_id"], analysis["payer_conflict_account_ids"])
        self.assertIn(acct2["account_id"], analysis["payer_conflict_account_ids"])


class BillingRelationshipDuplicateAnalysisApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "api.sqlite3"
        self.conn = connect(self.db_path)
        init_db(self.conn)
        self.handler_cls = make_handler(str(self.db_path), write_token="test-write-token")
        self.server = HTTPServer(("127.0.0.1", 0), self.handler_cls)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.conn.close()
        self.tmp.cleanup()

    def _get(self, path: str):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method="GET")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def test_duplicate_analysis_endpoint_returns_summary(self):
        status, body = self._get("/api/billing-relationships/duplicate-analysis")
        self.assertEqual(status, 200)
        self.assertIn("summary", body)
        self.assertIn("recommended_resolution", body)
