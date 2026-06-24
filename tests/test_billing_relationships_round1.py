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
    create_account_or_return_existing,
    create_person,
    find_equivalent_account,
    get_account_record,
    list_account_records,
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
        account = create_account(self.conn, "Alex Demo", "individual")
        member_id = add_account_member(
            self.conn, account["account_id"], person["person_id"], "primary", True
        )
        self.assertTrue(member_id)
        record = get_account_record(self.conn, account["account_id"])
        self.assertEqual(len(record["members"]), 1)
        self.assertEqual(record["members"][0]["person_id"], person["person_id"])
        self.assertTrue(record["members"][0]["is_primary"])

    def test_default_relationship_name_from_display_name(self):
        """The safe default name equals the client display name exactly."""
        person = create_person(self.conn, {"display_name": "Jordan Lee"})
        safe_name = person["display_name"]
        account = create_account(self.conn, safe_name, "individual")
        self.assertEqual(account["account_name"], "Jordan Lee")


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
        self.assertIn("Choose who should receive invoices", self.js)
        self.assertIn("which clients they are paying for", self.js)

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
        self.assertIn("disabled", self.js)
        self.assertIn("wizardContinue", self.js)
        self.assertIn("modal-submit", self.js)


class TestRound1CssStatic(unittest.TestCase):
    """Static CSS checks for modal styles."""

    def test_modal_overlay_css_exists(self):
        css = Path("app/jordana_invoice/static/review.css").read_text()
        self.assertIn(".billing-modal-overlay", css)
        self.assertIn(".billing-modal", css)
        self.assertIn(".modal-result-row", css)
        self.assertIn(".modal-error", css)
        self.assertIn(".modal-actions", css)


class TestDuplicateRelationshipPrevention(unittest.TestCase):
    """Backend: preventing duplicate billing relationships for the same client."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = connect(str(self.db_path))
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_create_account_or_return_existing_creates_new(self):
        """First creation for a client creates a new account with primary member."""
        person = create_person(self.conn, {"display_name": "New Client"})
        result = create_account_or_return_existing(
            self.conn, person["person_id"], "New Client", "individual"
        )
        self.assertFalse(result["existing"])
        self.assertTrue(result["account"]["account_id"])
        record = get_account_record(self.conn, result["account"]["account_id"])
        self.assertEqual(len(record["members"]), 1)
        self.assertEqual(record["members"][0]["person_id"], person["person_id"])
        self.assertTrue(record["members"][0]["is_primary"])

    def test_create_account_or_return_existing_returns_existing(self):
        """Second creation for the same client returns the existing account."""
        person = create_person(self.conn, {"display_name": "Dup Client"})
        first = create_account_or_return_existing(
            self.conn, person["person_id"], "Dup Client", "individual"
        )
        self.assertFalse(first["existing"])
        second = create_account_or_return_existing(
            self.conn, person["person_id"], "Dup Client Alt", "individual"
        )
        self.assertTrue(second["existing"])
        self.assertEqual(second["account"]["account_id"], first["account"]["account_id"])

    def test_no_additional_account_row_created(self):
        """Repeated create calls do not add new account rows."""
        person = create_person(self.conn, {"display_name": "Repeat Client"})
        create_account_or_return_existing(
            self.conn, person["person_id"], "Repeat Client", "individual"
        )
        create_account_or_return_existing(
            self.conn, person["person_id"], "Repeat Client Alt", "individual"
        )
        create_account_or_return_existing(
            self.conn, person["person_id"], "Repeat Client Alt 2", "individual"
        )
        accounts = list_account_records(self.conn)
        individual_accounts = [a for a in accounts if a["account_type"] == "individual"]
        self.assertEqual(len(individual_accounts), 1)

    def test_find_equivalent_account_finds_sole_member(self):
        """find_equivalent_account finds an account where the person is the sole member."""
        person = create_person(self.conn, {"display_name": "Sole Test"})
        account = create_account(self.conn, "Sole Test Billing", "individual")
        add_account_member(self.conn, account["account_id"], person["person_id"], "primary", True)
        found = find_equivalent_account(self.conn, person["person_id"], "individual")
        self.assertIsNotNone(found)
        self.assertEqual(found["account_id"], account["account_id"])

    def test_find_equivalent_account_finds_primary_member(self):
        """find_equivalent_account finds an account where the person is primary among multiple members."""
        person_a = create_person(self.conn, {"display_name": "Primary Test"})
        person_b = create_person(self.conn, {"display_name": "Secondary Test"})
        account = create_account(self.conn, "Family Billing", "individual")
        add_account_member(self.conn, account["account_id"], person_a["person_id"], "primary", True)
        add_account_member(self.conn, account["account_id"], person_b["person_id"], "family_member", False)
        found = find_equivalent_account(self.conn, person_a["person_id"], "individual")
        self.assertIsNotNone(found)
        self.assertEqual(found["account_id"], account["account_id"])

    def test_find_equivalent_account_returns_none_for_non_primary(self):
        """find_equivalent_account does not return an account where the person is a non-primary member."""
        person_a = create_person(self.conn, {"display_name": "Primary A"})
        person_b = create_person(self.conn, {"display_name": "Non Primary B"})
        account = create_account(self.conn, "Family Billing 2", "individual")
        add_account_member(self.conn, account["account_id"], person_a["person_id"], "primary", True)
        add_account_member(self.conn, account["account_id"], person_b["person_id"], "family_member", False)
        found = find_equivalent_account(self.conn, person_b["person_id"], "individual")
        self.assertIsNone(found)

    def test_find_equivalent_account_ignores_inactive(self):
        """find_equivalent_account ignores inactive accounts."""
        person = create_person(self.conn, {"display_name": "Inactive Test"})
        account = create_account(self.conn, "Inactive Test Billing", "individual")
        add_account_member(self.conn, account["account_id"], person["person_id"], "primary", True)
        self.conn.execute("UPDATE client_accounts SET active = 0 WHERE account_id = ?", (account["account_id"],))
        self.conn.commit()
        found = find_equivalent_account(self.conn, person["person_id"], "individual")
        self.assertIsNone(found)


class TestDuplicateRelationshipApi(unittest.TestCase):
    """API-level: /api/accounts/from-client rejects duplicate creation."""

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

    def test_api_creates_new_for_new_client(self):
        person = create_person(self.conn, {"display_name": "API New"})
        status, body = self._post("/api/accounts/from-client", {
            "person_id": person["person_id"],
            "account_name": "API New",
            "account_type": "individual",
        })
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))
        self.assertFalse(body.get("existing"))
        self.assertTrue(body.get("account_id"))

    def test_api_returns_409_for_duplicate(self):
        person = create_person(self.conn, {"display_name": "API Dup"})
        self._post("/api/accounts/from-client", {
            "person_id": person["person_id"],
            "account_name": "API Dup",
            "account_type": "individual",
        })
        status, body = self._post("/api/accounts/from-client", {
            "person_id": person["person_id"],
            "account_name": "API Dup Alt",
            "account_type": "individual",
        })
        self.assertEqual(status, 409)
        self.assertFalse(body.get("ok", True))
        self.assertTrue(body.get("existing"))
        self.assertIn("already exists", body.get("error", ""))
        self.assertTrue(body.get("account_id"))

    def test_repeated_create_clicks_no_duplicate_accounts(self):
        """Multiple calls to /api/accounts/from-client for same person produce only one account."""
        person = create_person(self.conn, {"display_name": "Repeat API"})
        for i in range(5):
            self._post("/api/accounts/from-client", {
                "person_id": person["person_id"],
                "account_name": f"Repeat API {i}",
                "account_type": "individual",
            })
        accounts = list_account_records(self.conn)
        individual = [a for a in accounts if a["account_type"] == "individual"]
        self.assertEqual(len(individual), 1)


class TestRound1CorrectionJsStatic(unittest.TestCase):
    """Static JS checks for Round 1 correction: duplicate prevention and Add Client fixes."""

    def setUp(self):
        self.js = Path("app/jordana_invoice/static/review.js").read_text()

    def test_create_modal_uses_from_client_endpoint(self):
        self.assertIn("/api/billing-relationships/setup", self.js)

    def test_create_modal_handles_existing_flag(self):
        self.assertIn("err.duplicate", self.js)
        self.assertIn("err.account_id", self.js)

    def test_create_modal_has_open_existing_button(self):
        self.assertIn("wizardOpenExisting", self.js)
        self.assertIn("Open existing relationship", self.js)

    def test_create_modal_duplicate_message(self):
        self.assertIn("This billing relationship already exists.", self.js)

    def test_create_modal_preserves_context_on_existing(self):
        """The Open existing handler preserves return context."""
        start = self.js.index("wizardOpenExisting")
        end = self.js.index("});", start) + 3
        block = self.js[start:end]
        self.assertIn("returnContext", block)
        self.assertIn("persistReturnContext", block)

    def test_render_modal_search_results_supports_known_ids(self):
        self.assertIn("knownIds", self.js)
        self.assertIn("already-included", self.js)
        self.assertIn("Already included", self.js)

    def test_existing_members_not_clickable(self):
        """renderModalSearchResults skips attaching click handlers for known members."""
        start = self.js.index("function renderModalSearchResults")
        end = self.js.index("}", self.js.index("container.querySelectorAll", start))
        func_body = self.js[start:end]
        self.assertIn("if (known.has(personId)) return;", func_body)

    def test_add_client_modal_passes_known_ids_to_render(self):
        start = self.js.index("function openAddClientModal")
        end = self.js.index("function openBillingRelationshipEditor")
        if end < start:
            end = len(self.js)
        func_body = self.js[start:end]
        self.assertIn("handleSelect, knownIds)", func_body)

    def test_add_client_modal_stays_open_on_backend_error(self):
        """The catch block in Add Client submit does not call closeBillingModal."""
        modal_start = self.js.index("function openAddClientModal")
        modal_end = self.js.index("\n}", modal_start + 10)
        modal_body = self.js[modal_start:modal_end]
        submit_start = modal_body.rindex('submitBtn.addEventListener("click"')
        handler = modal_body[submit_start:]
        self.assertIn("catch", handler)
        catch_start = handler.index("catch")
        catch_block = handler[catch_start:]
        self.assertNotIn("closeBillingModal", catch_block)
        self.assertIn("submitBtn.disabled = false", catch_block)

    def test_no_api_accounts_post_in_create_modal(self):
        """The create modal no longer uses the old /api/accounts POST directly."""
        start = self.js.index("function openCreateRelationshipModal")
        end = self.js.index("function openAddClientModal")
        func_body = self.js[start:end]
        self.assertNotIn('api("/api/accounts"', func_body)
        self.assertNotIn('api("/api/account-members"', func_body)


class TestRound1CorrectionCssStatic(unittest.TestCase):
    """Static CSS checks for Round 1 correction styles."""

    def test_already_included_css_exists(self):
        css = Path("app/jordana_invoice/static/review.css").read_text()
        self.assertIn(".already-included", css)
        self.assertIn(".already-included-label", css)

    def test_modal_link_btn_css_exists(self):
        css = Path("app/jordana_invoice/static/review.css").read_text()
        self.assertIn(".modal-link-btn", css)


class TestSessionReviewDuplicatePath(unittest.TestCase):
    """Reproduce the actual Session Review creation path that failed in smoke testing."""

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

    def test_avery_style_self_pay_then_create_no_duplicate(self):
        """Simulate: Avery has a self-pay billing party. First create makes an account.
        Second create for same client returns existing, no duplicate."""
        from jordana_invoice.review_services import create_billing_party
        person = create_person(self.conn, {"display_name": "Avery Stone"})
        party = create_billing_party(self.conn, {
            "billing_name": "Avery Stone",
            "person_id": person["person_id"],
            "billing_party_type": "person",
        })
        # First create — should succeed
        status1, body1 = self._post("/api/accounts/from-client", {
            "person_id": person["person_id"],
            "account_name": "Avery Stone",
            "account_type": "individual",
        })
        self.assertEqual(status1, 200)
        self.assertTrue(body1.get("ok"))
        account_id = body1["account_id"]
        # Second create — should return 409 with existing
        status2, body2 = self._post("/api/accounts/from-client", {
            "person_id": person["person_id"],
            "account_name": "Avery Stone",
            "account_type": "individual",
        })
        self.assertEqual(status2, 409)
        self.assertTrue(body2.get("existing"))
        self.assertEqual(body2["account_id"], account_id)
        # No duplicate account
        accounts = list_account_records(self.conn)
        individual = [a for a in accounts if a["account_type"] == "individual"]
        self.assertEqual(len(individual), 1)

    def test_account_name_is_exactly_display_name(self):
        """Account name equals the client display name, no suffix."""
        person = create_person(self.conn, {"display_name": "Test Name"})
        status, body = self._post("/api/accounts/from-client", {
            "person_id": person["person_id"],
            "account_name": "Test Name",
            "account_type": "individual",
        })
        self.assertEqual(status, 200)
        from jordana_invoice.review_services import get_account_record
        record = get_account_record(self.conn, body["account_id"])
        self.assertEqual(record["account"]["account_name"], "Test Name")
        self.assertNotIn("Billing Relationship", record["account"]["account_name"])

    def test_no_billing_relationship_suffix_in_name(self):
        """Static check: JS does not append Billing Relationship to the account name."""
        js = Path("app/jordana_invoice/static/review.js").read_text()
        # The wizard uses the setup endpoint which generates names server-side
        self.assertIn("/api/billing-relationships/setup", js)
        # The wizard should not construct account names client-side with "Billing Relationship" suffix
        wizard_start = js.index("function openCreateRelationshipModal")
        wizard_end = js.index("\nfunction openAddClientModal")
        wizard_code = js[wizard_start:wizard_end]
        # "Review billing relationship" is a heading, not a name — that's fine
        # But no account_name field should be set with "Billing Relationship" in it
        self.assertNotIn("account_name", wizard_code)

    def test_repeated_create_idempotent(self):
        """5 repeated creates for same person produce 1 account."""
        person = create_person(self.conn, {"display_name": "Idempotent Test"})
        for _ in range(5):
            self._post("/api/accounts/from-client", {
                "person_id": person["person_id"],
                "account_name": "Idempotent Test",
                "account_type": "individual",
            })
        accounts = list_account_records(self.conn)
        self.assertEqual(len(accounts), 1)

    def test_shared_relationship_not_treated_as_duplicate(self):
        """A shared family account where the person is a non-primary member
        should NOT block creating a new individual relationship for that person."""
        from jordana_invoice.review_services import create_billing_party
        person_a = create_person(self.conn, {"display_name": "Shared Primary"})
        person_b = create_person(self.conn, {"display_name": "Shared Secondary"})
        family_account = create_account(self.conn, "Family Group", "family")
        add_account_member(self.conn, family_account["account_id"], person_a["person_id"], "primary", True)
        add_account_member(self.conn, family_account["account_id"], person_b["person_id"], "family_member", False)
        # person_b is a non-primary member of the family account
        # Creating an individual relationship for person_b should succeed
        status, body = self._post("/api/accounts/from-client", {
            "person_id": person_b["person_id"],
            "account_name": "Shared Secondary",
            "account_type": "individual",
        })
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))
        # The family account should not be returned as equivalent
        self.assertNotEqual(body["account_id"], family_account["account_id"])

    def test_billing_party_linked_account_detected_as_equivalent(self):
        """If an account's default billing party belongs to this person,
        it should be detected as equivalent even with no membership record."""
        from jordana_invoice.review_services import create_billing_party
        person = create_person(self.conn, {"display_name": "Linked Party"})
        party = create_billing_party(self.conn, {
            "billing_name": "Linked Party",
            "person_id": person["person_id"],
            "billing_party_type": "person",
        })
        account = create_account(self.conn, "Linked Party", "individual")
        # Link billing party as default but don't add membership
        self.conn.execute(
            "UPDATE client_accounts SET default_billing_party_id = ? WHERE account_id = ?",
            (party["billing_party_id"], account["account_id"]),
        )
        self.conn.commit()
        # Now create via from-client — should detect existing
        status, body = self._post("/api/accounts/from-client", {
            "person_id": person["person_id"],
            "account_name": "Linked Party",
            "account_type": "individual",
        })
        self.assertEqual(status, 409)
        self.assertTrue(body.get("existing"))
        self.assertEqual(body["account_id"], account["account_id"])

    def test_legacy_account_no_primary_flag_detected(self):
        """Legacy account with sole member but no is_primary flag should be detected."""
        person = create_person(self.conn, {"display_name": "Legacy Test"})
        account = create_account(self.conn, "Legacy Test", "individual")
        # Add membership without is_primary
        self.conn.execute(
            "INSERT INTO account_members (account_member_id, account_id, person_id, relationship_role, is_primary, created_at, updated_at) VALUES (?, ?, ?, ?, 0, ?, ?)",
            ("legacy-mid", account["account_id"], person["person_id"], "member", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        self.conn.commit()
        status, body = self._post("/api/accounts/from-client", {
            "person_id": person["person_id"],
            "account_name": "Legacy Test",
            "account_type": "individual",
        })
        self.assertEqual(status, 409)
        self.assertTrue(body.get("existing"))


if __name__ == "__main__":
    unittest.main()
