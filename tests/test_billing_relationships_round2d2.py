"""Round 2D2 tests: in-wizard organization creation.

Tests cover:
1. Create new organization appears for organization payer
2. It does not appear for client payer
3. It does not appear for another-person payer
4. Organization name is required
5. Whitespace-only name is rejected
6. Supported optional fields are sent correctly
7. Billing contact name maps to billing_name
8. Successful creation selects the organization as payer
9. Organization creation preserves selected covered clients
10. Organization is not added under Pays for
11. Existing organization duplicate is detected
12. Duplicate warning is explicit
13. Use existing organization selects the existing record
14. Duplicate does not create another billing-party row
15. Back to search preserves parent state
16. API failure remains inline
17. Double submission creates at most one organization
18. Existing organization search still excludes person billing parties
19. Existing client/person child forms still work
20. Round 2C setup payload for an organization uses organization_billing_party_id
21. No browser prompt, alert, or confirm is used
22. No session or review candidate is modified or approved
23. No schema migration is introduced
"""
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
from jordana_invoice.review_services import create_person

JS_PATH = Path("app/jordana_invoice/static/review.js")
CSS_PATH = Path("app/jordana_invoice/static/review.css")


class TestCreateOrgStaticJs(unittest.TestCase):
    """Static JS checks for in-wizard organization creation."""

    def setUp(self):
        self.js = JS_PATH.read_text()
        self.css = CSS_PATH.read_text()

    def test_create_new_org_appears_for_organization(self):
        """1. Create new organization appears for organization payer."""
        self.assertIn("Create new organization", self.js)
        self.assertIn("wizardCreateNewOrg", self.js)

    def test_no_create_org_for_client(self):
        """2. It does not appear for client payer."""
        client_start = self.js.index('if (payerType === "client" || payerType === "person") {')
        client_end = self.js.index('} else if (payerType === "organization") {')
        client_code = self.js[client_start:client_end]
        self.assertNotIn("wizardCreateNewOrg", client_code)
        self.assertNotIn("Create new organization", client_code)

    def test_no_create_org_for_person(self):
        """3. It does not appear for another-person payer."""
        # The client/person branch is the same block, already checked above
        # Also verify the person-specific create label is different
        self.assertIn("Create another person", self.js)
        person_start = self.js.index("Create another person")
        person_end = self.js.index("\n", person_start)
        person_line = self.js[person_start:person_end]
        self.assertNotIn("organization", person_line.lower())

    def test_org_name_required(self):
        """4. Organization name is required."""
        self.assertIn("Organization name is required.", self.js)
        self.assertIn("wizardOrgName", self.js)

    def test_whitespace_only_rejected(self):
        """5. Whitespace-only name is rejected (trim check)."""
        self.assertIn("nameInput.value.trim()", self.js)
        org_form_start = self.js.index("function showCreateOrgForm")
        org_form_end = self.js.index("\n  function handleOrgCreated")
        org_form_code = self.js[org_form_start:org_form_end]
        self.assertIn("!orgName", org_form_code)

    def test_optional_fields_sent(self):
        """6. Supported optional fields are sent in payload."""
        org_form_start = self.js.index("function showCreateOrgForm")
        org_form_end = self.js.index("\n  function handleOrgCreated")
        org_form_code = self.js[org_form_start:org_form_end]
        self.assertIn("billing_email", org_form_code)
        self.assertIn("billing_phone", org_form_code)
        self.assertIn("billing_address_line_1", org_form_code)
        self.assertIn("billing_address_line_2", org_form_code)
        self.assertIn("billing_city", org_form_code)
        self.assertIn("billing_state", org_form_code)
        self.assertIn("billing_postal_code", org_form_code)
        self.assertIn("preferred_delivery_method", org_form_code)
        self.assertIn("administrative_notes", org_form_code)

    def test_billing_contact_name_maps_to_billing_name(self):
        """7. Billing contact name maps to billing_name."""
        org_form_start = self.js.index("function showCreateOrgForm")
        org_form_end = self.js.index("\n  function handleOrgCreated")
        org_form_code = self.js[org_form_start:org_form_end]
        self.assertIn("wizardOrgBillingName", org_form_code)
        self.assertIn("billing_name: billingName", org_form_code)

    def test_successful_creation_selects_org(self):
        """8. Successful creation selects the organization as payer."""
        self.assertIn("handleOrgCreated", self.js)
        self.assertIn("payerOrg = org", self.js)

    def test_org_creation_preserves_covered(self):
        """9. Organization creation preserves selected covered clients."""
        handler_start = self.js.index("function handleOrgCreated")
        handler_end = self.js.index("\n  function renderStep3", handler_start)
        handler_code = self.js[handler_start:handler_end]
        # handleOrgCreated should NOT clear coveredClients
        self.assertNotIn("coveredClients = []", handler_code)
        self.assertNotIn("coveredClients = [", handler_code)

    def test_org_not_added_to_pays_for(self):
        """10. Organization is not added under Pays for."""
        handler_start = self.js.index("function handleOrgCreated")
        handler_end = self.js.index("\n  function renderStep3", handler_start)
        handler_code = self.js[handler_start:handler_end]
        self.assertNotIn("coveredClients.push", handler_code)

    def test_duplicate_org_detected(self):
        """11. Existing organization duplicate is detected."""
        self.assertIn("org.existing", self.js)
        self.assertIn("!org.created", self.js)

    def test_duplicate_warning_explicit(self):
        """12. Duplicate warning is explicit."""
        self.assertIn("An organization with this name already exists.", self.js)
        self.assertIn("wizardOrgFormDuplicate", self.js)

    def test_use_existing_org_option(self):
        """13. Use existing organization selects the existing record."""
        self.assertIn("Use existing organization", self.js)
        self.assertIn("wizardOrgUseExisting", self.js)

    def test_go_back_and_edit_org(self):
        """Go back and edit option exists for org."""
        self.assertIn("Go back and edit", self.js)
        self.assertIn("wizardOrgEditAgain", self.js)

    def test_back_to_search_preserves_state(self):
        """15. Back to search preserves parent wizard state."""
        self.assertIn("Back to search", self.js)
        self.assertIn("closeOrgForm", self.js)
        close_start = self.js.index("function closeOrgForm()")
        close_end = self.js.index("}", close_start)
        close_code = self.js[close_start:close_end]
        self.assertNotIn("payerType = null", close_code)
        self.assertNotIn("coveredClients = []", close_code)

    def test_api_failure_stays_inline(self):
        """16. API failure remains inline."""
        self.assertIn("Failed to create organization.", self.js)
        self.assertIn("wizardOrgFormError", self.js)

    def test_double_submit_prevented(self):
        """17. Double submission is prevented."""
        org_form_start = self.js.index("function showCreateOrgForm")
        org_form_end = self.js.index("\n  function handleOrgCreated")
        org_form_code = self.js[org_form_start:org_form_end]
        self.assertIn("if (creating) return;", org_form_code)
        self.assertIn("creating = true", org_form_code)
        self.assertIn("submitBtn.disabled = true", org_form_code)

    def test_no_browser_prompt_alert_confirm(self):
        """21. No browser prompt, alert, or confirm in the org creation flow."""
        wizard_start = self.js.index("function openCreateRelationshipModal")
        wizard_end = self.js.index("\nfunction openAddClientModal")
        wizard_code = self.js[wizard_start:wizard_end]
        self.assertNotIn("prompt(", wizard_code)
        self.assertNotIn("alert(", wizard_code)
        self.assertNotIn("confirm(", wizard_code)

    def test_org_search_still_excludes_person_parties(self):
        """18. Existing organization search still excludes person billing parties."""
        self.assertIn("/api/organization-billing-parties", self.js)

    def test_client_person_forms_still_work(self):
        """19. Existing client/person child forms still work."""
        self.assertIn("showCreatePersonForm", self.js)
        self.assertIn("Create new client", self.js)
        self.assertIn("Create another person", self.js)

    def test_setup_payload_uses_org_billing_party_id(self):
        """20. Round 2C setup payload for an organization uses organization_billing_party_id."""
        self.assertIn("organization_billing_party_id", self.js)
        self.assertIn("payerOrg.billing_party_id", self.js)

    def test_uses_api_billing_parties_for_creation(self):
        """Form calls POST /api/billing-parties."""
        org_form_start = self.js.index("function showCreateOrgForm")
        org_form_end = self.js.index("\n  function handleOrgCreated")
        org_form_code = self.js[org_form_start:org_form_end]
        self.assertIn('api("/api/billing-parties"', org_form_code)

    def test_creating_indicator(self):
        """Creating… indicator shown during submission."""
        org_form_start = self.js.index("function showCreateOrgForm")
        org_form_end = self.js.index("\n  function handleOrgCreated")
        org_form_code = self.js[org_form_start:org_form_end]
        self.assertIn("Creating…", org_form_code)

    def test_escape_html_on_user_values(self):
        """User-controlled values escaped in org form."""
        org_form_start = self.js.index("function showCreateOrgForm")
        org_form_end = self.js.index("\n  function handleOrgCreated")
        org_form_code = self.js[org_form_start:org_form_end]
        self.assertIn("escapeHtml", org_form_code)

    def test_delivery_method_select(self):
        """Delivery method uses a select with existing supported values."""
        self.assertIn("wizardOrgDelivery", self.js)
        self.assertIn('value="unresolved"', self.js)
        self.assertIn('value="email"', self.js)
        self.assertIn('value="mail"', self.js)
        self.assertIn('value="both"', self.js)

    def test_no_session_review_attachment(self):
        """No Session Review attachment."""
        wizard_start = self.js.index("function openCreateRelationshipModal")
        wizard_end = self.js.index("\nfunction openAddClientModal")
        wizard_code = self.js[wizard_start:wizard_end]
        self.assertNotIn("save_interpretation", wizard_code)
        self.assertNotIn("approve_candidate", wizard_code)

    def test_no_new_tables_or_columns(self):
        """23. No schema migration is introduced (no new table or column references)."""
        wizard_start = self.js.index("function openCreateRelationshipModal")
        wizard_end = self.js.index("\nfunction openAddClientModal")
        wizard_code = self.js[wizard_start:wizard_end]
        self.assertNotIn("CREATE TABLE", wizard_code)
        self.assertNotIn("ALTER TABLE", wizard_code)
        self.assertNotIn("ADD COLUMN", wizard_code)

    def test_child_form_css_exists(self):
        """Child form CSS classes are reused from Round 2D1."""
        self.assertIn("wizard-child-form", self.css)
        self.assertIn("wizard-create-btn", self.css)
        self.assertIn("wizard-form-grid", self.css)

    def test_no_clinical_fields(self):
        """No clinical fields in the org form."""
        org_form_start = self.js.index("function showCreateOrgForm")
        org_form_end = self.js.index("\n  function handleOrgCreated")
        org_form_code = self.js[org_form_start:org_form_end]
        self.assertNotIn("clinical", org_form_code.lower())
        self.assertNotIn("diagnosis", org_form_code.lower())
        self.assertNotIn("session_notes", org_form_code.lower())


class TestCreateOrgApi(unittest.TestCase):
    """API integration tests for organization creation and duplicate detection."""

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

    def test_create_org_returns_created_flag(self):
        """New organization has created: true."""
        status, body = self._post("/api/billing-parties", {
            "billing_party_type": "organization",
            "organization_name": "Acme Corp",
            "billing_name": "Acme Corp",
        })
        self.assertEqual(status, 200)
        self.assertTrue(body.get("created"))
        self.assertFalse(body.get("existing"))
        self.assertTrue(body.get("billing_party_id"))

    def test_duplicate_org_returns_existing_flag(self):
        """14. Duplicate does not create another billing-party row."""
        status1, body1 = self._post("/api/billing-parties", {
            "billing_party_type": "organization",
            "organization_name": "Test Org",
            "billing_name": "Test Org",
        })
        status2, body2 = self._post("/api/billing-parties", {
            "billing_party_type": "organization",
            "organization_name": "Test Org",
            "billing_name": "Test Org",
        })
        self.assertEqual(status1, 200)
        self.assertEqual(status2, 200)
        self.assertTrue(body1.get("created"))
        self.assertFalse(body2.get("created"))
        self.assertTrue(body2.get("existing"))
        self.assertEqual(body1["billing_party_id"], body2["billing_party_id"])
        count = self.conn.execute(
            "SELECT COUNT(*) AS c FROM billing_parties WHERE organization_name = 'Test Org' AND billing_party_type = 'organization'"
        ).fetchone()["c"]
        self.assertEqual(count, 1)

    def test_create_org_with_all_fields(self):
        """6. All optional fields are stored correctly."""
        status, body = self._post("/api/billing-parties", {
            "billing_party_type": "organization",
            "organization_name": "Full Org",
            "billing_name": "Full Org Billing",
            "billing_email": "billing@fullorg.com",
            "billing_phone": "555-9999",
            "billing_address_line_1": "123 Main St",
            "billing_address_line_2": "Suite 100",
            "billing_city": "Anytown",
            "billing_state": "CA",
            "billing_postal_code": "90210",
            "preferred_delivery_method": "email",
            "administrative_notes": "Test admin notes",
        })
        self.assertEqual(status, 200)
        self.assertTrue(body.get("created"))
        self.assertEqual(body.get("organization_name"), "Full Org")
        self.assertEqual(body.get("billing_name"), "Full Org Billing")
        self.assertEqual(body.get("billing_email"), "billing@fullorg.com")
        self.assertEqual(body.get("billing_phone"), "555-9999")
        self.assertEqual(body.get("billing_address_line_1"), "123 Main St")
        self.assertEqual(body.get("preferred_delivery_method"), "email")
        self.assertEqual(body.get("administrative_notes"), "Test admin notes")

    def test_double_post_creates_one_org(self):
        """17. Double submission creates at most one organization."""
        payload = {
            "billing_party_type": "organization",
            "organization_name": "Double Org",
            "billing_name": "Double Org",
        }
        self._post("/api/billing-parties", payload)
        self._post("/api/billing-parties", payload)
        count = self.conn.execute(
            "SELECT COUNT(*) AS c FROM billing_parties WHERE organization_name = 'Double Org' AND billing_party_type = 'organization'"
        ).fetchone()["c"]
        self.assertEqual(count, 1)

    def test_no_session_changed_on_org_creation(self):
        """22. No session or review candidate is modified or approved."""
        self._post("/api/billing-parties", {
            "billing_party_type": "organization",
            "organization_name": "No Session Org",
            "billing_name": "No Session Org",
        })
        sessions = self.conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"]
        self.assertEqual(sessions, 0)
        candidates = self.conn.execute("SELECT COUNT(*) AS c FROM calendar_event_candidates").fetchone()["c"]
        self.assertEqual(candidates, 0)

    def test_org_search_excludes_person_parties(self):
        """18. Organization search excludes person billing parties."""
        # Create a person billing party
        person = create_person(self.conn, {"display_name": "Jane Doe"})
        # Create an org billing party
        self._post("/api/billing-parties", {
            "billing_party_type": "organization",
            "organization_name": "Searchable Org",
            "billing_name": "Searchable Org",
        })
        # Search org billing parties
        import urllib.request as ur
        req = ur.Request(f"http://127.0.0.1:{self.port}/api/organization-billing-parties?q=Searchable")
        with ur.urlopen(req) as resp:
            results = json.loads(resp.read())
        self.assertTrue(any(r["organization_name"] == "Searchable Org" for r in results))
        # Person billing party should not appear
        self.assertFalse(any(r.get("billing_party_id") == person.get("billing_party_id") for r in results))

    def test_backward_compatibility_person_billing_party(self):
        """Existing person billing party creation still works without created/existing flags being checked."""
        person = create_person(self.conn, {"display_name": "Compat Person"})
        status, body = self._post("/api/billing-parties", {
            "billing_party_type": "person",
            "person_id": person["person_id"],
            "billing_name": "Compat Person",
        })
        self.assertEqual(status, 200)
        self.assertTrue(body.get("billing_party_id"))

    def test_duplicate_case_insensitive(self):
        """Duplicate detection is case-insensitive."""
        self._post("/api/billing-parties", {
            "billing_party_type": "organization",
            "organization_name": "Case Test Org",
            "billing_name": "Case Test Org",
        })
        status, body = self._post("/api/billing-parties", {
            "billing_party_type": "organization",
            "organization_name": "case test org",
            "billing_name": "case test org",
        })
        self.assertFalse(body.get("created"))
        self.assertTrue(body.get("existing"))


if __name__ == "__main__":
    unittest.main()
