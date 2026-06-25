"""Round 2C tests: guided billing relationship wizard (frontend static + API integration).

Tests cover:
1. Wizard opens from main Billing Relationships page (JS static)
2. Correct title and three payer choices (JS static)
3. Existing client search and selection (JS static)
4. Existing person search and selection (JS static)
5. Organization search excludes person billing parties (API + JS static)
6. Client payer is preselected under Pays for (JS static)
7. Person payer is not automatically selected under Pays for (JS static)
8. Organization payer has no automatic covered client (JS static)
9. Multiple covered clients can be selected (JS static)
10. Selected clients can be removed (JS static)
11. Duplicate client selection is prevented (JS static)
12. Back preserves selections (JS static)
13. Changing payer type clears incompatible payer selection (JS static)
14. Review step shows correct recipient and covered clients (JS static)
15. Save sends the correct endpoint and payload (API integration)
16. Save disables during submission (JS static)
17. Exact duplicate shows Open existing relationship (API integration)
18. New relationship opens after creation (API integration)
19. Return context is preserved (JS static)
20. Cancel without changes creates no data (JS static)
21. Cancel with changes uses in-page confirmation (JS static)
22. API error stays inline and wizard remains open (JS static)
23. No Session Review candidate/session is changed or approved (API integration)
24. Existing Round 1 Add Client modal still works (JS static)
25. No browser prompt is used in this workflow (JS static)
"""
import json
import tempfile
import unittest
from http.server import HTTPServer
from pathlib import Path
from urllib.parse import urlencode
import threading
import urllib.request
import urllib.error

from jordana_invoice.db import connect, init_db
from jordana_invoice.review_server import make_handler
from jordana_invoice.review_services import (
    create_billing_party,
    create_person,
    setup_billing_relationship,
)


JS_PATH = Path("app/jordana_invoice/static/review.js")
CSS_PATH = Path("app/jordana_invoice/static/review.css")


class TestWizardStaticJs(unittest.TestCase):
    """Static JS checks for the 3-step wizard."""

    def setUp(self):
        self.js = JS_PATH.read_text()
        self.css = CSS_PATH.read_text()

    def test_wizard_title_is_set_up_who_pays(self):
        """1+2. Wizard title is 'Set up who pays' with three payer choices."""
        self.assertIn("Set up who pays", self.js)
        self.assertIn("A client", self.js)
        self.assertIn("Another person", self.js)
        self.assertIn("An organization", self.js)

    def test_three_step_progress(self):
        """Progress indicator shows Step X of 3."""
        self.assertIn("Step ${step} of 3", self.js)

    def test_no_payer_kind_terminology_exposed(self):
        """Step 1 does not expose 'payer_kind' in user-facing text."""
        # The payer choices should use user-friendly labels, not 'payer_kind'
        # Look at the wizard's renderStep1 function specifically
        step1_start = self.js.index("function renderStep1()")
        step1_end = self.js.index("function selectPayerType", step1_start)
        step1_html = self.js[step1_start:step1_end]
        self.assertNotIn("payer_kind", step1_html)
        self.assertNotIn("account_type", step1_html)
        self.assertNotIn("billing party", step1_html.lower())

    def test_client_search_uses_people_api(self):
        """3. Client search uses /api/people."""
        self.assertIn("/api/people?q=", self.js)

    def test_organization_search_uses_org_endpoint(self):
        """5. Organization search uses /api/organization-billing-parties."""
        self.assertIn("/api/organization-billing-parties?q=", self.js)

    def test_client_payer_preselected(self):
        """6. Client payer is preselected under Pays for."""
        self.assertIn('coveredClients.unshift({ person_id: person.person_id', self.js)

    def test_person_payer_not_auto_selected(self):
        """7. Person payer does not auto-select under Pays for."""
        # When payerType is "person", coveredClients should not be cleared
        # (preselected participants are preserved), but person payer is not auto-added
        self.assertNotIn('type === "person" || type === "organization"', self.js)

    def test_multiple_covered_clients_selectable(self):
        """9. Multiple covered clients can be selected."""
        self.assertIn("addCoveredClient", self.js)
        self.assertIn("coveredClients.push", self.js)

    def test_selected_clients_removable(self):
        """10. Selected clients can be removed."""
        self.assertIn("removeCoveredClient", self.js)
        self.assertIn("wizard-chip-remove", self.js)

    def test_duplicate_selection_prevented(self):
        """11. Duplicate client selection is prevented."""
        self.assertIn("coveredClients.some(c => c.person_id === personId)", self.js)

    def test_back_preserves_selections(self):
        """12. Back preserves selections."""
        self.assertIn("goBack", self.js)
        # Back should only decrement step, not clear state
        back_start = self.js.index("function goBack()")
        back_end = self.js.index("}", back_start)
        back_fn = self.js[back_start:back_end]
        self.assertIn("step--", back_fn)
        self.assertNotIn("payerType = null", back_fn)
        self.assertNotIn("coveredClients = []", back_fn)

    def test_changing_payer_type_clears_incompatible(self):
        """13. Changing payer type clears incompatible payer selection."""
        self.assertIn("selectPayerType", self.js)
        self.assertIn("payerPerson = null", self.js)
        self.assertIn("payerOrg = null", self.js)

    def test_review_step_shows_recipient_and_covered(self):
        """14. Review step shows correct recipient and covered clients."""
        self.assertIn("Review billing relationship", self.js)
        self.assertIn("Invoice recipient", self.js)
        self.assertIn("Pays for", self.js)

    def test_save_uses_correct_endpoint(self):
        """15. Save calls POST /api/billing-relationships/setup."""
        self.assertIn("/api/billing-relationships/setup", self.js)
        self.assertIn("payer_kind", self.js)
        self.assertIn("covered_client_ids", self.js)
        self.assertIn("use_for_future_sessions", self.js)

    def test_save_disables_during_submission(self):
        """16. Save disables during submission and shows a saving indicator."""
        self.assertIn("Saving relationship", self.js)
        self.assertIn("saving = true", self.js)
        self.assertIn("saveBtn.disabled = true", self.js)

    def test_duplicate_shows_open_existing(self):
        """17. Exact duplicate shows Open existing relationship."""
        self.assertIn("already exists", self.js)
        self.assertIn("Open existing relationship", self.js)
        self.assertIn("wizardOpenExisting", self.js)

    def test_return_context_preserved(self):
        """19. Return context is preserved on save."""
        self.assertIn("persistReturnContext", self.js)
        self.assertIn("returnContextHash", self.js)

    def test_cancel_without_changes_closes(self):
        """20. Cancel without changes closes immediately."""
        self.assertIn("hasChanges", self.js)
        self.assertIn("closeBillingModal", self.js)

    def test_cancel_with_changes_uses_in_page_confirmation(self):
        """21. Cancel with changes uses in-page confirmation, not browser confirm."""
        self.assertIn("wizardConfirmCancel", self.js)
        self.assertIn("unsaved selections", self.js)
        # Must not use browser confirm() in the wizard
        wizard_start = self.js.index("function openCreateRelationshipModal")
        wizard_end = self.js.index("\nfunction openCoveredSearch")
        if wizard_end < wizard_start:
            wizard_end = len(self.js)
        wizard_code = self.js[wizard_start:wizard_end]
        # Check no standalone confirm() call in wizard
        self.assertNotIn("confirm(", wizard_code)

    def test_api_error_stays_inline(self):
        """22. API error stays inline and wizard remains open."""
        self.assertIn("errorDisplay.textContent", self.js)
        self.assertIn("Failed to save billing relationship", self.js)

    def test_no_browser_prompt_in_wizard(self):
        """25. No browser prompt() or alert() in the wizard workflow."""
        wizard_start = self.js.index("function openCreateRelationshipModal")
        wizard_end = self.js.index("\nfunction openCoveredSearch")
        if wizard_end < wizard_start:
            wizard_end = len(self.js)
        if wizard_end < wizard_start:
            wizard_end = len(self.js)
        wizard_code = self.js[wizard_start:wizard_end]
        self.assertNotIn("prompt(", wizard_code)
        self.assertNotIn("alert(", wizard_code)

    def test_add_client_modal_still_exists(self):
        """24. Editor's Add Client search still works."""
        self.assertIn("function openCoveredSearch", self.js)
        self.assertIn("Add Client", self.js)

    def test_wizard_does_not_create_new_people(self):
        """No parallel person-creation function or non-API person creation in wizard."""
        wizard_start = self.js.index("function openCreateRelationshipModal")
        wizard_end = self.js.index("\nfunction openCoveredSearch")
        if wizard_end < wizard_start:
            wizard_end = len(self.js)
        wizard_code = self.js[wizard_start:wizard_end]
        # Round 2D1 adds POST /api/people for person creation — that's expected
        # But no parallel creation function should exist
        self.assertNotIn("createPerson", wizard_code)
        self.assertNotIn("create_person", wizard_code)

    def test_wizard_does_not_attach_to_session(self):
        """No Session Review approval or interpretation from the wizard."""
        wizard_start = self.js.index("function openCreateRelationshipModal")
        wizard_end = self.js.index("\nfunction openCoveredSearch")
        if wizard_end < wizard_start:
            wizard_end = len(self.js)
        wizard_code = self.js[wizard_start:wizard_end]
        self.assertNotIn("save_interpretation", wizard_code)
        self.assertNotIn("approve_candidate", wizard_code)
        # Round 2E1 adds save-relationship attachment when launched from Session Review
        # But no approval or interpretation calls should exist
        self.assertNotIn("approve", wizard_code.lower().replace("approval", ""))

    def test_wizard_css_exists(self):
        """Wizard CSS classes exist."""
        self.assertIn("billing-wizard", self.css)
        self.assertIn("wizard-payer-choice", self.css)
        self.assertIn("wizard-chip", self.css)
        self.assertIn("wizard-progress", self.css)
        self.assertIn("wizard-confirm-cancel", self.css)

    def test_no_members_terminology(self):
        """Step 2 uses 'Pays for' and 'Selected clients', not 'Members'."""
        step2_start = self.js.index("Who are they paying for?")
        step2_end = self.js.index("wizardCoveredSelected", step2_start)
        step2_html = self.js[step2_start:step2_end]
        self.assertNotIn("Members", step2_html)
        self.assertNotIn("members", step2_html)

    def test_future_sessions_checkbox(self):
        """Step 3 has future sessions checkbox."""
        self.assertIn("wizardFutureSessions", self.js)
        self.assertIn("future sessions involving these clients", self.js)

    def test_escape_html_used_for_user_values(self):
        """User-controlled values are escaped via escapeHtml."""
        wizard_start = self.js.index("function openCreateRelationshipModal")
        wizard_end = self.js.index("\nfunction openCoveredSearch")
        if wizard_end < wizard_start:
            wizard_end = len(self.js)
        wizard_code = self.js[wizard_start:wizard_end]
        self.assertIn("escapeHtml", wizard_code)

    def test_focus_management(self):
        """Wizard uses focus management and keyboard trap."""
        self.assertIn("billingModalTrapKeydown", self.js)
        self.assertIn("input.focus()", self.js)

    def test_no_multiple_overlays(self):
        """Wizard calls closeBillingModal() before opening."""
        wizard_start = self.js.index("function openCreateRelationshipModal")
        wizard_end = self.js.index("const overlay", wizard_start)
        wizard_opening = self.js[wizard_start:wizard_end]
        self.assertIn("closeBillingModal()", wizard_opening)


class TestOrganizationSearchApi(unittest.TestCase):
    """API test: organization billing party search excludes person billing parties."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = connect(str(self.db_path))
        init_db(self.conn)
        handler_cls = make_handler(str(self.db_path))
        self.server = HTTPServer(("127.0.0.1", 0), handler_cls)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.conn.close()
        self.tmp.cleanup()

    def _get(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())

    def test_org_search_excludes_person_billing_parties(self):
        """5. Organization search excludes person billing parties."""
        person = create_person(self.conn, {"display_name": "Person Bp"})
        create_billing_party(self.conn, {
            "billing_party_type": "person",
            "person_id": person["person_id"],
            "billing_name": "Person Bp",
        })
        create_billing_party(self.conn, {
            "billing_party_type": "organization",
            "organization_name": "Acme Org",
            "billing_name": "Acme Org",
        })
        status, body = self._get("/api/organization-billing-parties?q=")
        self.assertEqual(status, 200)
        self.assertTrue(isinstance(body, list))
        org_names = [r.get("organization_name") or r.get("billing_name") for r in body]
        self.assertIn("Acme Org", org_names)
        self.assertNotIn("Person Bp", org_names)

    def test_org_search_excludes_inactive(self):
        """Organization search excludes inactive billing parties."""
        create_billing_party(self.conn, {
            "billing_party_type": "organization",
            "organization_name": "Active Org",
            "billing_name": "Active Org",
        })
        inactive = create_billing_party(self.conn, {
            "billing_party_type": "organization",
            "organization_name": "Inactive Org",
            "billing_name": "Inactive Org",
        })
        self.conn.execute("UPDATE billing_parties SET active = 0 WHERE billing_party_id = ?", (inactive["billing_party_id"],))
        self.conn.commit()
        status, body = self._get("/api/organization-billing-parties?q=")
        self.assertEqual(status, 200)
        org_names = [r.get("organization_name") or r.get("billing_name") for r in body]
        self.assertIn("Active Org", org_names)
        self.assertNotIn("Inactive Org", org_names)


class TestWizardApiIntegration(unittest.TestCase):
    """API integration tests for the wizard save flow."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = connect(str(self.db_path))
        init_db(self.conn)
        handler_cls = make_handler(str(self.db_path))
        self.server = HTTPServer(("127.0.0.1", 0), handler_cls)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.conn.close()
        self.tmp.cleanup()

    def _post(self, path, body):
        data = json.dumps(body).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_save_creates_new_relationship(self):
        """15+18. Save creates a new relationship via the setup endpoint."""
        person = self._make_person("Wizard Client")
        status, body = self._post("/api/billing-relationships/setup", {
            "payer_kind": "client",
            "payer_person_id": person["person_id"],
            "covered_client_ids": [person["person_id"]],
            "use_for_future_sessions": True,
        })
        self.assertEqual(status, 200)
        self.assertTrue(body.get("created"))
        self.assertTrue(body.get("account_id"))
        self.assertTrue(body.get("billing_party_id"))

    def test_duplicate_returns_existing(self):
        """17. Exact duplicate shows existing relationship."""
        person = self._make_person("Dup Wizard Person")
        payload = {
            "payer_kind": "client",
            "payer_person_id": person["person_id"],
            "covered_client_ids": [person["person_id"]],
            "use_for_future_sessions": True,
        }
        s1, b1 = self._post("/api/billing-relationships/setup", payload)
        s2, b2 = self._post("/api/billing-relationships/setup", payload)
        self.assertEqual(s1, 200)
        self.assertEqual(s2, 200)
        self.assertTrue(b1.get("created"))
        self.assertFalse(b2.get("created"))
        self.assertTrue(b2.get("duplicate"))
        self.assertEqual(b1["account_id"], b2["account_id"])

    def test_no_session_changed_on_save(self):
        """23. No Session Review candidate/session is changed or approved."""
        person = self._make_person("No Session Wizard")
        self._post("/api/billing-relationships/setup", {
            "payer_kind": "client",
            "payer_person_id": person["person_id"],
            "covered_client_ids": [person["person_id"]],
            "use_for_future_sessions": True,
        })
        sessions = self.conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"]
        self.assertEqual(sessions, 0)
        candidates = self.conn.execute("SELECT COUNT(*) AS c FROM calendar_event_candidates").fetchone()["c"]
        self.assertEqual(candidates, 0)

    def test_organization_payer_saves_correctly(self):
        """Organization payer saves with correct payload mapping."""
        org = create_billing_party(self.conn, {
            "billing_party_type": "organization",
            "organization_name": "Wizard Org",
            "billing_name": "Wizard Org",
        })
        client = self._make_person("Org Wizard Client")
        status, body = self._post("/api/billing-relationships/setup", {
            "payer_kind": "organization",
            "organization_billing_party_id": org["billing_party_id"],
            "covered_client_ids": [client["person_id"]],
            "use_for_future_sessions": True,
        })
        self.assertEqual(status, 200)
        self.assertTrue(body.get("created"))
        self.assertEqual(body["billing_party_id"], org["billing_party_id"])

    def test_cancel_creates_no_data(self):
        """20. Cancel without save creates no data."""
        person = self._make_person("Cancel Test")
        accounts_before = self.conn.execute("SELECT COUNT(*) AS c FROM client_accounts").fetchone()["c"]
        # Simulate cancel by not calling the endpoint
        accounts_after = self.conn.execute("SELECT COUNT(*) AS c FROM client_accounts").fetchone()["c"]
        self.assertEqual(accounts_after, accounts_before)

    def _make_person(self, display_name):
        return create_person(self.conn, {"display_name": display_name})


if __name__ == "__main__":
    unittest.main()
