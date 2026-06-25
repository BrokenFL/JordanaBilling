"""Round 2B tests: transactional billing relationship setup backend.

Tests cover:
1. Client pays for self
2. Client pays for multiple clients
3. Existing non-client person pays for one client
4. Person payer is not automatically added as a member
5. Existing organization pays for one client
6. Existing organization pays for multiple clients
7. Organization relationship creates an account and memberships
8. Same payer plus exact client set returns existing relationship
9. Duplicate call creates no extra account
10. Duplicate call creates no extra members
11. Same payer with different client set is allowed
12. Different payer with same client set is allowed
13. Covered-client order does not affect duplicate detection
14. Inactive relationship does not block a new active relationship
15. Invalid payer is rejected
16. Invalid covered client is rejected
17. Empty covered-client set is rejected
18. Duplicate covered-client IDs are normalized or rejected consistently
19. Transaction failure rolls back account, billing party, members, and audit changes
20. Existing Round 1 duplicate behavior still passes
21. Account names never contain Billing Relationship
22. No session is created, attached, changed, or approved
"""
import json
import tempfile
import unittest
from http.server import HTTPServer
from pathlib import Path

from jordana_invoice.db import connect, init_db
from jordana_invoice.review_server import make_handler
from jordana_invoice.review_services import (
    create_billing_party,
    create_person,
    get_account_record,
    list_account_records,
    setup_billing_relationship,
    find_duplicate_billing_relationship,
)


class Round2TestBase(unittest.TestCase):
    """Shared setup for Round 2 backend tests."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = connect(str(self.db_path))
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _make_person(self, display_name):
        return create_person(self.conn, {"display_name": display_name})

    def _make_org_billing_party(self, org_name, billing_name=None):
        return create_billing_party(self.conn, {
            "billing_party_type": "organization",
            "organization_name": org_name,
            "billing_name": billing_name or org_name,
        })


class TestClientPaysForSelf(Round2TestBase):

    def test_client_pays_for_self(self):
        """1. Client pays for self → individual account, primary member, billing party linked."""
        person = self._make_person("Alex Self")
        result = setup_billing_relationship(self.conn, {
            "payer_kind": "client",
            "payer_person_id": person["person_id"],
            "covered_client_ids": [person["person_id"]],
        })
        self.assertTrue(result["created"])
        self.assertFalse(result["duplicate"])
        self.assertEqual(result["account_type"], "individual")
        self.assertEqual(result["account_name"], "Alex Self")
        record = get_account_record(self.conn, result["account_id"])
        self.assertEqual(len(record["members"]), 1)
        self.assertEqual(record["members"][0]["person_id"], person["person_id"])
        self.assertTrue(record["members"][0]["is_primary"])
        self.assertEqual(record["account"]["default_billing_party_id"], result["billing_party_id"])

    def test_billing_party_linked_to_person(self):
        """The billing party on the account belongs to the payer person."""
        person = self._make_person("Self Pay Person")
        result = setup_billing_relationship(self.conn, {
            "payer_kind": "client",
            "payer_person_id": person["person_id"],
            "covered_client_ids": [person["person_id"]],
        })
        bp = self.conn.execute(
            "SELECT * FROM billing_parties WHERE billing_party_id = ?",
            (result["billing_party_id"],),
        ).fetchone()
        self.assertEqual(bp["person_id"], person["person_id"])
        self.assertEqual(bp["billing_party_type"], "person")


class TestClientPaysForMultiple(Round2TestBase):

    def test_client_pays_for_multiple_clients(self):
        """2. Client pays for multiple clients → family account, payer is primary."""
        payer = self._make_person("Multi Payer")
        client2 = self._make_person("Client Two")
        client3 = self._make_person("Client Three")
        result = setup_billing_relationship(self.conn, {
            "payer_kind": "client",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [payer["person_id"], client2["person_id"], client3["person_id"]],
        })
        self.assertTrue(result["created"])
        self.assertEqual(result["account_type"], "family")
        record = get_account_record(self.conn, result["account_id"])
        self.assertEqual(len(record["members"]), 3)
        primary = [m for m in record["members"] if m["is_primary"]]
        self.assertEqual(len(primary), 1)
        self.assertEqual(primary[0]["person_id"], payer["person_id"])

    def test_client_payer_not_in_covered_is_not_member(self):
        """If payer is not in covered_client_ids, they are not a member."""
        payer = self._make_person("Outside Payer")
        client1 = self._make_person("Covered One")
        result = setup_billing_relationship(self.conn, {
            "payer_kind": "client",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [client1["person_id"]],
        })
        record = get_account_record(self.conn, result["account_id"])
        member_ids = {m["person_id"] for m in record["members"]}
        self.assertNotIn(payer["person_id"], member_ids)
        self.assertIn(client1["person_id"], member_ids)


class TestPersonPaysForClients(Round2TestBase):

    def test_person_pays_for_one_client(self):
        """3. Existing non-client person pays for one client."""
        payer = self._make_person("Parent Person")
        client = self._make_person("Child Client")
        result = setup_billing_relationship(self.conn, {
            "payer_kind": "person",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [client["person_id"]],
        })
        self.assertTrue(result["created"])
        self.assertEqual(result["account_type"], "family")
        record = get_account_record(self.conn, result["account_id"])
        self.assertEqual(len(record["members"]), 1)
        self.assertEqual(record["members"][0]["person_id"], client["person_id"])

    def test_person_payer_not_auto_added_as_member(self):
        """4. Person payer is not automatically added as a member."""
        payer = self._make_person("Non Client Payer")
        client1 = self._make_person("Covered A")
        client2 = self._make_person("Covered B")
        result = setup_billing_relationship(self.conn, {
            "payer_kind": "person",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [client1["person_id"], client2["person_id"]],
        })
        record = get_account_record(self.conn, result["account_id"])
        member_ids = {m["person_id"] for m in record["members"]}
        self.assertNotIn(payer["person_id"], member_ids)
        self.assertEqual(len(record["members"]), 2)


class TestOrganizationPaysForClients(Round2TestBase):

    def test_org_pays_for_one_client(self):
        """5. Existing organization pays for one client."""
        org = self._make_org_billing_party("Acme Org")
        client = self._make_person("Org Client")
        result = setup_billing_relationship(self.conn, {
            "payer_kind": "organization",
            "organization_billing_party_id": org["billing_party_id"],
            "covered_client_ids": [client["person_id"]],
        })
        self.assertTrue(result["created"])
        record = get_account_record(self.conn, result["account_id"])
        self.assertEqual(len(record["members"]), 1)
        self.assertEqual(record["members"][0]["person_id"], client["person_id"])
        self.assertEqual(record["account"]["default_billing_party_id"], org["billing_party_id"])

    def test_org_pays_for_multiple_clients(self):
        """6. Existing organization pays for multiple clients."""
        org = self._make_org_billing_party("Multi Org")
        c1 = self._make_person("Org Client 1")
        c2 = self._make_person("Org Client 2")
        c3 = self._make_person("Org Client 3")
        result = setup_billing_relationship(self.conn, {
            "payer_kind": "organization",
            "organization_billing_party_id": org["billing_party_id"],
            "covered_client_ids": [c1["person_id"], c2["person_id"], c3["person_id"]],
        })
        self.assertTrue(result["created"])
        record = get_account_record(self.conn, result["account_id"])
        self.assertEqual(len(record["members"]), 3)

    def test_org_creates_account_and_memberships(self):
        """7. Organization relationship creates an account and memberships."""
        org = self._make_org_billing_party("Account Org")
        client = self._make_person("Account Client")
        result = setup_billing_relationship(self.conn, {
            "payer_kind": "organization",
            "organization_billing_party_id": org["billing_party_id"],
            "covered_client_ids": [client["person_id"]],
        })
        account = self.conn.execute(
            "SELECT * FROM client_accounts WHERE account_id = ?",
            (result["account_id"],),
        ).fetchone()
        self.assertIsNotNone(account)
        self.assertEqual(account["default_billing_party_id"], org["billing_party_id"])
        members = self.conn.execute(
            "SELECT * FROM account_members WHERE account_id = ?",
            (result["account_id"],),
        ).fetchall()
        self.assertEqual(len(members), 1)


class TestDuplicateDetection(Round2TestBase):

    def test_same_payer_exact_set_returns_existing(self):
        """8. Same payer plus exact client set returns existing relationship."""
        payer = self._make_person("Dup Payer")
        c1 = self._make_person("Dup Client 1")
        c2 = self._make_person("Dup Client 2")
        payload = {
            "payer_kind": "client",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [payer["person_id"], c1["person_id"], c2["person_id"]],
        }
        first = setup_billing_relationship(self.conn, payload)
        second = setup_billing_relationship(self.conn, payload)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["account_id"], second["account_id"])
        self.assertEqual(first["billing_party_id"], second["billing_party_id"])

    def test_duplicate_creates_no_extra_account(self):
        """9. Duplicate call creates no extra account."""
        payer = self._make_person("No Extra Payer")
        c1 = self._make_person("No Extra Client")
        payload = {
            "payer_kind": "client",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [payer["person_id"], c1["person_id"]],
        }
        setup_billing_relationship(self.conn, payload)
        setup_billing_relationship(self.conn, payload)
        accounts = list_account_records(self.conn)
        self.assertEqual(len(accounts), 1)

    def test_duplicate_creates_no_extra_members(self):
        """10. Duplicate call creates no extra members."""
        payer = self._make_person("No Extra Members Payer")
        c1 = self._make_person("No Extra Members C1")
        payload = {
            "payer_kind": "client",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [payer["person_id"], c1["person_id"]],
        }
        first = setup_billing_relationship(self.conn, payload)
        setup_billing_relationship(self.conn, payload)
        members = self.conn.execute(
            "SELECT * FROM account_members WHERE account_id = ?",
            (first["account_id"],),
        ).fetchall()
        self.assertEqual(len(members), 2)

    def test_same_payer_different_client_set_allowed(self):
        """11. Same payer with different client set is allowed."""
        payer = self._make_person("Diff Set Payer")
        c1 = self._make_person("Diff Set C1")
        c2 = self._make_person("Diff Set C2")
        first = setup_billing_relationship(self.conn, {
            "payer_kind": "client",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [payer["person_id"], c1["person_id"]],
        })
        second = setup_billing_relationship(self.conn, {
            "payer_kind": "client",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [payer["person_id"], c2["person_id"]],
        })
        self.assertTrue(first["created"])
        self.assertTrue(second["created"])
        self.assertNotEqual(first["account_id"], second["account_id"])

    def test_different_payer_same_client_set_allowed(self):
        """12. Different payer with same client set is allowed."""
        payer_a = self._make_person("Payer A")
        payer_b = self._make_person("Payer B")
        c1 = self._make_person("Shared Client 1")
        c2 = self._make_person("Shared Client 2")
        first = setup_billing_relationship(self.conn, {
            "payer_kind": "person",
            "payer_person_id": payer_a["person_id"],
            "covered_client_ids": [c1["person_id"], c2["person_id"]],
        })
        second = setup_billing_relationship(self.conn, {
            "payer_kind": "person",
            "payer_person_id": payer_b["person_id"],
            "covered_client_ids": [c1["person_id"], c2["person_id"]],
        })
        self.assertTrue(first["created"])
        self.assertTrue(second["created"])
        self.assertNotEqual(first["account_id"], second["account_id"])

    def test_covered_client_order_does_not_affect_duplicate(self):
        """13. Covered-client order does not affect duplicate detection."""
        payer = self._make_person("Order Payer")
        c1 = self._make_person("Order C1")
        c2 = self._make_person("Order C2")
        first = setup_billing_relationship(self.conn, {
            "payer_kind": "client",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [c1["person_id"], c2["person_id"]],
        })
        second = setup_billing_relationship(self.conn, {
            "payer_kind": "client",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [c2["person_id"], c1["person_id"]],
        })
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["account_id"], second["account_id"])

    def test_inactive_relationship_does_not_block(self):
        """14. Inactive relationship does not block a new active relationship."""
        payer = self._make_person("Inactive Payer")
        c1 = self._make_person("Inactive C1")
        first = setup_billing_relationship(self.conn, {
            "payer_kind": "client",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [payer["person_id"], c1["person_id"]],
        })
        self.conn.execute(
            "UPDATE client_accounts SET active = 0 WHERE account_id = ?",
            (first["account_id"],),
        )
        self.conn.commit()
        second = setup_billing_relationship(self.conn, {
            "payer_kind": "client",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [payer["person_id"], c1["person_id"]],
        })
        self.assertTrue(second["created"])
        self.assertNotEqual(first["account_id"], second["account_id"])


class TestValidation(Round2TestBase):

    def test_invalid_payer_kind_rejected(self):
        """15. Invalid payer kind is rejected."""
        person = self._make_person("Valid Person")
        with self.assertRaises(ValueError) as ctx:
            setup_billing_relationship(self.conn, {
                "payer_kind": "invalid_kind",
                "payer_person_id": person["person_id"],
                "covered_client_ids": [person["person_id"]],
            })
        self.assertIn("payer_kind", str(ctx.exception))

    def test_invalid_payer_person_rejected(self):
        """15b. Non-existent payer person is rejected."""
        c1 = self._make_person("Valid Client")
        with self.assertRaises(ValueError) as ctx:
            setup_billing_relationship(self.conn, {
                "payer_kind": "client",
                "payer_person_id": "nonexistent-uuid",
                "covered_client_ids": [c1["person_id"]],
            })
        self.assertIn("Payer person", str(ctx.exception))

    def test_invalid_organization_rejected(self):
        """15c. Non-existent organization billing party is rejected."""
        c1 = self._make_person("Org Validation Client")
        with self.assertRaises(ValueError) as ctx:
            setup_billing_relationship(self.conn, {
                "payer_kind": "organization",
                "organization_billing_party_id": "nonexistent-org-id",
                "covered_client_ids": [c1["person_id"]],
            })
        self.assertIn("Organization billing party", str(ctx.exception))

    def test_invalid_covered_client_rejected(self):
        """16. Invalid covered client is rejected."""
        payer = self._make_person("Valid Payer")
        with self.assertRaises(ValueError) as ctx:
            setup_billing_relationship(self.conn, {
                "payer_kind": "client",
                "payer_person_id": payer["person_id"],
                "covered_client_ids": ["nonexistent-client-id"],
            })
        self.assertIn("does not exist or is not active", str(ctx.exception))

    def test_empty_covered_client_set_rejected(self):
        """17. Empty covered-client set is rejected."""
        payer = self._make_person("Empty Set Payer")
        with self.assertRaises(ValueError) as ctx:
            setup_billing_relationship(self.conn, {
                "payer_kind": "client",
                "payer_person_id": payer["person_id"],
                "covered_client_ids": [],
            })
        self.assertIn("At least one covered client", str(ctx.exception))

    def test_duplicate_covered_client_ids_rejected(self):
        """18. Duplicate covered-client IDs are rejected."""
        payer = self._make_person("Dup IDs Payer")
        c1 = self._make_person("Dup IDs C1")
        with self.assertRaises(ValueError) as ctx:
            setup_billing_relationship(self.conn, {
                "payer_kind": "client",
                "payer_person_id": payer["person_id"],
                "covered_client_ids": [c1["person_id"], c1["person_id"]],
            })
        self.assertIn("Duplicate covered client IDs", str(ctx.exception))


class TestTransactionSafety(Round2TestBase):

    def test_transaction_rollback_on_failure(self):
        """19. Transaction failure rolls back account, billing party, members, and audit changes."""
        from jordana_invoice.review_services import create_account, add_account_member

        payer = self._make_person("Rollback Payer")
        c1 = self._make_person("Rollback C1")
        c2 = self._make_person("Rollback C2")

        # Pre-create an account with the exact name _derive_account_name will produce.
        # The payer is a "person" (not client), covered = [c2, c1], so the name will be:
        # "Rollback Payer — pays for Rollback C2 & Rollback C1"
        predicted_name = "Rollback Payer — pays for Rollback C2 & Rollback C1"
        existing_account = create_account(self.conn, predicted_name, "family")
        # Pre-add c1 as a member so the second add_account_member call will fail.
        add_account_member(self.conn, existing_account["account_id"], c1["person_id"], "family_member", False)

        accounts_before = len(list_account_records(self.conn))
        members_before = self.conn.execute("SELECT COUNT(*) AS c FROM account_members").fetchone()["c"]
        audit_before = self.conn.execute("SELECT COUNT(*) AS c FROM audit_log").fetchone()["c"]

        # Call setup with covered = [c2, c1] so c2 is added first (succeeds, uncommitted),
        # then c1 fails (already a member). The rollback should undo c2's addition.
        with self.assertRaises(ValueError):
            setup_billing_relationship(self.conn, {
                "payer_kind": "person",
                "payer_person_id": payer["person_id"],
                "covered_client_ids": [c2["person_id"], c1["person_id"]],
            })

        accounts_after = len(list_account_records(self.conn))
        members_after = self.conn.execute("SELECT COUNT(*) AS c FROM account_members").fetchone()["c"]
        audit_after = self.conn.execute("SELECT COUNT(*) AS c FROM audit_log").fetchone()["c"]
        self.assertEqual(accounts_after, accounts_before, "No new account should have been created")
        self.assertEqual(members_after, members_before, "Rolled-back member addition should not persist")
        self.assertEqual(audit_after, audit_before, "No new audit entries from failed setup")


class TestRound1StillPasses(Round2TestBase):

    def test_round1_create_account_or_return_existing_still_works(self):
        """20. Existing Round 1 duplicate behavior still passes."""
        from jordana_invoice.review_services import create_account_or_return_existing
        person = self._make_person("Round1 Person")
        first = create_account_or_return_existing(
            self.conn, person["person_id"], "Round1 Person", "individual"
        )
        self.assertFalse(first["existing"])
        second = create_account_or_return_existing(
            self.conn, person["person_id"], "Round1 Person Alt", "individual"
        )
        self.assertTrue(second["existing"])
        self.assertEqual(first["account"]["account_id"], second["account"]["account_id"])


class TestAccountNames(Round2TestBase):

    def test_account_names_never_contain_billing_relationship(self):
        """21. Account names never contain 'Billing Relationship'."""
        payer = self._make_person("Name Test Payer")
        c1 = self._make_person("Name Test C1")
        c2 = self._make_person("Name Test C2")

        # Self-pay
        r1 = setup_billing_relationship(self.conn, {
            "payer_kind": "client",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [payer["person_id"]],
        })
        self.assertNotIn("Billing Relationship", r1["account_name"])

        # Multi-client
        r2 = setup_billing_relationship(self.conn, {
            "payer_kind": "client",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [c1["person_id"], c2["person_id"]],
        })
        self.assertNotIn("Billing Relationship", r2["account_name"])
        self.assertNotIn("Account", r2["account_name"])
        self.assertNotIn("Household", r2["account_name"])

    def test_self_pay_account_name_is_display_name(self):
        """Self-pay account name equals client display name exactly."""
        person = self._make_person("Exact Name")
        result = setup_billing_relationship(self.conn, {
            "payer_kind": "client",
            "payer_person_id": person["person_id"],
            "covered_client_ids": [person["person_id"]],
        })
        self.assertEqual(result["account_name"], "Exact Name")

    def test_multi_client_name_has_pays_for(self):
        """Multi-client name contains 'pays for'."""
        payer = self._make_person("Named Payer")
        c1 = self._make_person("Named C1")
        c2 = self._make_person("Named C2")
        result = setup_billing_relationship(self.conn, {
            "payer_kind": "client",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [c1["person_id"], c2["person_id"]],
        })
        self.assertIn("pays for", result["account_name"])
        self.assertIn("Named Payer", result["account_name"])


class TestNoSessionChanges(Round2TestBase):

    def test_no_session_created_or_changed(self):
        """22. No session is created, attached, changed, or approved."""
        payer = self._make_person("No Session Payer")
        c1 = self._make_person("No Session C1")
        setup_billing_relationship(self.conn, {
            "payer_kind": "client",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [payer["person_id"], c1["person_id"]],
        })
        sessions = self.conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"]
        self.assertEqual(sessions, 0)
        candidates = self.conn.execute("SELECT COUNT(*) AS c FROM calendar_event_candidates").fetchone()["c"]
        self.assertEqual(candidates, 0)


class TestApiEndpoint(Round2TestBase):

    def setUp(self):
        super().setUp()
        handler_cls = make_handler(str(self.db_path))
        self.server = HTTPServer(("127.0.0.1", 0), handler_cls)
        self.port = self.server.server_address[1]
        import threading
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        super().tearDown()

    def _post(self, path, body):
        import urllib.request
        data = json.dumps(body).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_api_creates_relationship(self):
        """API endpoint creates a relationship successfully."""
        person = self._make_person("API Person")
        status, body = self._post("/api/billing-relationships/setup", {
            "payer_kind": "client",
            "payer_person_id": person["person_id"],
            "covered_client_ids": [person["person_id"]],
        })
        self.assertEqual(status, 200)
        self.assertTrue(body.get("created"))
        self.assertTrue(body.get("account_id"))
        self.assertTrue(body.get("billing_party_id"))

    def test_api_returns_existing_for_duplicate(self):
        """API endpoint returns existing relationship for duplicate."""
        person = self._make_person("API Dup Person")
        payload = {
            "payer_kind": "client",
            "payer_person_id": person["person_id"],
            "covered_client_ids": [person["person_id"]],
        }
        s1, b1 = self._post("/api/billing-relationships/setup", payload)
        s2, b2 = self._post("/api/billing-relationships/setup", payload)
        self.assertEqual(s1, 200)
        self.assertEqual(s2, 200)
        self.assertTrue(b1.get("created"))
        self.assertFalse(b2.get("created"))
        self.assertTrue(b2.get("duplicate"))
        self.assertEqual(b1["account_id"], b2["account_id"])

    def test_api_rejects_invalid_payer_kind(self):
        """API endpoint rejects invalid payer kind with 400."""
        person = self._make_person("API Invalid")
        status, body = self._post("/api/billing-relationships/setup", {
            "payer_kind": "bad_kind",
            "payer_person_id": person["person_id"],
            "covered_client_ids": [person["person_id"]],
        })
        self.assertEqual(status, 400)
        self.assertFalse(body.get("ok", True))
        self.assertIn("payer_kind", body.get("error", ""))


class TestFindDuplicateDirect(Round2TestBase):

    def test_find_duplicate_returns_none_when_no_match(self):
        """find_duplicate_billing_relationship returns None when no match exists."""
        payer = self._make_person("Find Dup Payer")
        c1 = self._make_person("Find Dup C1")
        result = find_duplicate_billing_relationship(
            self.conn, "client", payer["person_id"], None, [c1["person_id"]]
        )
        self.assertIsNone(result)

    def test_find_duplicate_finds_match(self):
        """find_duplicate_billing_relationship finds an exact match."""
        payer = self._make_person("Find Match Payer")
        c1 = self._make_person("Find Match C1")
        c2 = self._make_person("Find Match C2")
        setup_billing_relationship(self.conn, {
            "payer_kind": "client",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [payer["person_id"], c1["person_id"], c2["person_id"]],
        })
        result = find_duplicate_billing_relationship(
            self.conn, "client", payer["person_id"], None,
            [c2["person_id"], c1["person_id"], payer["person_id"]]
        )
        self.assertIsNotNone(result)
        self.assertTrue(result["account_id"])
        self.assertTrue(result["billing_party_id"])


if __name__ == "__main__":
    unittest.main()
