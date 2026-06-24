"""Round 1 tests: billing relationship usability cleanup.

Tests cover:
1. Creating a billing relationship from an existing client (backend)
2. The selected client becoming the primary account member
3. Preserving return-to-review context (JS static)
4. Adding an existing client to a relationship
5. Preventing duplicate membership
6. No fuzzy first-result selection (JS static)
7. Cancelling either in-page form without creating data (JS static)
"""
import io
import json
import tempfile
import unittest
from http.server import HTTPServer
from pathlib import Path
from urllib.parse import urlencode

from jordana_invoice.db import connect, init_db
from jordana_invoice.review_server import make_handler
from jordana_invoice.review_services import (
    add_account_member,
    create_account,
    create_person,
    get_account_record,
)


class TestCreateRelationshipFromClient(unittest.TestCase):
    """Backend: creating a billing relationship from an existing client."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = connect(str(self.db_path))
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_create_account_and_add_primary_member(self):
        """Creating a relationship from a client makes them the primary member."""
        person = create_person(self.conn, {"display_name": "Alex Demo"})
        account = create_account(self.conn, "Alex Demo Billing Relationship", "individual")
        member_id = add_account_member(
            self.conn, account["account_id"], person["person_id"], "primary", True
        )
        self.assertTrue(member_id)
        record = get_account_record(self.conn, account["account_id"])
        self.assertEqual(len(record["members"]), 1)
        self.assertEqual(record["members"][0]["person_id"], person["person_id"])
        self.assertTrue(record["members"][0]["is_primary"])

    def test_default_relationship_name_from_display_name(self):
        """The safe default name follows the pattern '<DisplayName> Billing Relationship'."""
        person = create_person(self.conn, {"display_name": "Jordan Lee"})
        safe_name = f"{person['display_name']} Billing Relationship"
        account = create_account(self.conn, safe_name, "individual")
        self.assertEqual(account["account_name"], "Jordan Lee Billing Relationship")


class TestAddClientToRelationship(unittest.TestCase):
    """Backend: adding an existing client to a relationship."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = connect(str(self.db_path))
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_add_existing_client_to_relationship(self):
        """Adding a client to a relationship succeeds and appears in members."""
        person = create_person(self.conn, {"display_name": "Sam Test"})
        account = create_account(self.conn, "Sam Test Billing", "individual")
        add_account_member(self.conn, account["account_id"], person["person_id"], "family_member", False)
        record = get_account_record(self.conn, account["account_id"])
        self.assertEqual(len(record["members"]), 1)
        self.assertEqual(record["members"][0]["person_id"], person["person_id"])

    def test_prevent_duplicate_membership(self):
        """Adding the same person twice raises a clear validation error."""
        person = create_person(self.conn, {"display_name": "Pat Demo"})
        account = create_account(self.conn, "Pat Demo Billing", "individual")
        add_account_member(self.conn, account["account_id"], person["person_id"], "primary", True)
        with self.assertRaises(ValueError) as ctx:
            add_account_member(self.conn, account["account_id"], person["person_id"], "family_member", False)
        self.assertIn("already included", str(ctx.exception))

    def test_different_people_can_be_added(self):
        """Multiple different people can be added to the same relationship."""
        person_a = create_person(self.conn, {"display_name": "Client A"})
        person_b = create_person(self.conn, {"display_name": "Client B"})
        account = create_account(self.conn, "Shared Billing", "family")
        add_account_member(self.conn, account["account_id"], person_a["person_id"], "primary", True)
        add_account_member(self.conn, account["account_id"], person_b["person_id"], "family_member", False)
        record = get_account_record(self.conn, account["account_id"])
        self.assertEqual(len(record["members"]), 2)


class TestAccountMemberApiDuplicateRejection(unittest.TestCase):
    """API-level: the /api/account-members endpoint rejects duplicates."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = connect(str(self.db_path))
        init_db(self.conn)
        handler_cls = make_handler(str(self.db_path))
        self.server = HTTPServer(("127.0.0.1", 0), handler_cls)
        self.port = self.server.server_address[1]
        import threading
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.conn.close()
        self.tmp.cleanup()

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

    def test_api_rejects_duplicate_member(self):
        person = create_person(self.conn, {"display_name": "Dup Test"})
        account = create_account(self.conn, "Dup Test Billing", "individual")
        status, body = self._post("/api/account-members", {
            "account_id": account["account_id"],
            "person_id": person["person_id"],
            "relationship_role": "primary",
            "is_primary": True,
        })
        self.assertEqual(status, 200)
        status2, body2 = self._post("/api/account-members", {
            "account_id": account["account_id"],
            "person_id": person["person_id"],
            "relationship_role": "family_member",
            "is_primary": False,
        })
        self.assertEqual(status2, 400)
        self.assertFalse(body2.get("ok", True))
        self.assertIn("already included", body2.get("error", ""))


class TestRound1JsStatic(unittest.TestCase):
    """Static JS checks for the in-page modal replacements."""

    def setUp(self):
        self.js = Path("app/jordana_invoice/static/review.js").read_text()

    def test_create_relationship_modal_function_exists(self):
        self.assertIn("function openCreateRelationshipModal", self.js)

    def test_add_client_modal_function_exists(self):
        self.assertIn("function openAddClientModal", self.js)

    def test_close_billing_modal_function_exists(self):
        self.assertIn("function closeBillingModal", self.js)

    def test_no_prompt_for_billing_relationship_name(self):
        self.assertNotIn('prompt("Billing relationship name"', self.js)

    def test_no_prompt_for_add_member(self):
        self.assertNotIn('prompt("Add which existing client', self.js)

    def test_no_fuzzy_first_result_selection(self):
        """The old add-member code selected rows[0] as fallback — verify it's gone from addMemberRecord handler."""
        start = self.js.index('$("addMemberRecord").onclick')
        end = self.js.index("};", start) + 2
        handler = self.js[start:end]
        self.assertNotIn("|| rows[0]", handler)

    def test_no_alert_for_no_matching_client(self):
        self.assertNotIn('alert("No matching client found."', self.js)

    def test_create_modal_has_instruction_text(self):
        self.assertIn("Select an existing client to begin", self.js)
        self.assertIn("A more detailed payer setup will be completed in the next workflow step", self.js)

    def test_add_client_button_label(self):
        self.assertIn("Add Client", self.js)
        self.assertNotIn(">Add Member<", self.js)

    def test_duplicate_warning_text(self):
        self.assertIn("This client is already included in this billing relationship.", self.js)

    def test_search_existing_clients_label(self):
        self.assertIn("Search existing clients", self.js)

    def test_modal_has_cancel_button(self):
        self.assertIn("billingModalCancel", self.js)

    def test_modal_has_create_button(self):
        self.assertIn("billingModalSubmit", self.js)

    def test_modal_uses_escape_html_for_results(self):
        self.assertIn("escapeHtml(row.display_name", self.js)

    def test_modal_has_aria_modal(self):
        self.assertIn('aria-modal="true"', self.js)

    def test_modal_has_role_dialog(self):
        self.assertIn('role="dialog"', self.js)

    def test_modal_has_label_elements(self):
        self.assertIn('<label for="billingModalSearch">', self.js)

    def test_modal_focuses_search_input(self):
        self.assertIn("searchInput.focus()", self.js)

    def test_modal_returns_focus_on_cancel(self):
        self.assertIn("originatingBtn.focus()", self.js)

    def test_modal_esc_key_handler(self):
        self.assertIn('e.key === "Escape"', self.js)

    def test_modal_tab_trap(self):
        self.assertIn('e.key === "Tab"', self.js)

    def test_newAccountBtn_uses_modal_not_prompt(self):
        start = self.js.index('$("newAccountBtn").onclick')
        end = self.js.index("\n", self.js.index("};", start) + 2)
        handler = self.js[start:end]
        self.assertIn("openCreateRelationshipModal", handler)
        self.assertNotIn("prompt(", handler)

    def test_createRelationshipForReturn_uses_modal_not_prompt(self):
        start = self.js.index('$("createRelationshipForReturn").onclick')
        end = self.js.index("};", start) + 2
        handler = self.js[start:end]
        self.assertIn("openCreateRelationshipModal", handler)
        self.assertNotIn("prompt(", handler)

    def test_addMemberRecord_uses_modal_not_prompt(self):
        start = self.js.index('$("addMemberRecord").onclick')
        end = self.js.index("};", start) + 2
        handler = self.js[start:end]
        self.assertIn("openAddClientModal", handler)
        self.assertNotIn("prompt(", handler)
        self.assertNotIn("rows[0]", handler)

    def test_dead_showAccountEditor_removed(self):
        self.assertNotIn("function showAccountEditor", self.js)

    def test_return_context_preserved_in_create_modal(self):
        """The create modal passes returnContext through to openAccountRecord."""
        self.assertIn("returnContext: nextContext", self.js)

    def test_existing_member_ids_passed_to_add_modal(self):
        """The add-member handler collects existing member person_ids."""
        self.assertIn("existingIds", self.js)
        self.assertIn("data.members", self.js)

    def test_modal_submit_disabled_until_selection(self):
        self.assertIn("submitBtn.disabled = false", self.js)
        self.assertIn("disabled>Create", self.js)
        self.assertIn("disabled>Add", self.js)


class TestRound1CssStatic(unittest.TestCase):
    """Static CSS checks for modal styles."""

    def test_modal_overlay_css_exists(self):
        css = Path("app/jordana_invoice/static/review.css").read_text()
        self.assertIn(".billing-modal-overlay", css)
        self.assertIn(".billing-modal", css)
        self.assertIn(".modal-result-row", css)
        self.assertIn(".modal-error", css)
        self.assertIn(".modal-actions", css)


if __name__ == "__main__":
    unittest.main()
