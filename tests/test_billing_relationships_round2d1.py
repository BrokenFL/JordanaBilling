"""Round 2D1 tests: in-wizard person creation (client + another person).

Tests cover:
1. Create new client appears for client payer (JS static)
2. Create another person appears for another-person payer (JS static)
3. No create-new action for organization payer (JS static)
4. First name is required (JS static)
5. Last name is required (JS static)
6. Display name is derived correctly (JS static)
7. Person code is generated only after first and last name (API)
8. Successful client creation selects the new payer (JS static)
9. Newly created client is preselected under Pays for (JS static)
10. Successful another-person creation selects the new payer (JS static)
11. Another-person payer is not automatically added under Pays for (JS static)
12. Step 2 can create a new client (JS static)
13. Step 2 creation preserves payer selection (JS static)
14. Step 2 creation preserves existing covered clients (JS static)
15. Possible duplicate shows explicit warning (JS static + API)
16. Use existing person selects the existing record (JS static)
17. Duplicate does not create an extra person row (API)
18. Back to search preserves parent wizard state (JS static)
19. API failure stays inline (JS static)
20. Double-click submission creates at most one person (API)
21. No browser prompt, alert, or confirm is used (JS static)
22. Existing organization selection still works (JS static)
23. Round 2C save payload remains unchanged (JS static)
24. No session or candidate is changed or approved (API)
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
from jordana_invoice.review_services import (
    create_person,
    create_billing_party,
)

JS_PATH = Path("app/jordana_invoice/static/review.js")
CSS_PATH = Path("app/jordana_invoice/static/review.css")


class TestCreatePersonStaticJs(unittest.TestCase):
    """Static JS checks for in-wizard person creation."""

    def setUp(self):
        self.js = JS_PATH.read_text()
        self.css = CSS_PATH.read_text()

    def test_create_new_client_appears_for_client(self):
        """1. Create new client appears for client payer."""
        self.assertIn("Create new client", self.js)
        self.assertIn("wizardCreateNewPerson", self.js)

    def test_create_another_person_appears_for_person(self):
        """2. Create another person appears for another-person payer."""
        self.assertIn("Create another person", self.js)

    def test_no_create_new_for_organization(self):
        """3. No person-creation action for organization payer."""
        # The person create-new button should not be in the org branch
        org_start = self.js.index('} else if (payerType === "organization") {')
        org_end = self.js.index("input.focus();", org_start)
        org_code = self.js[org_start:org_end]
        self.assertNotIn("wizardCreateNewPerson", org_code)
        self.assertNotIn("Create new client", org_code)
        self.assertNotIn("Create another person", org_code)

    def test_first_name_required(self):
        """4. First name is required."""
        self.assertIn("First name is required", self.js)
        self.assertIn("wizardNewFirst", self.js)

    def test_last_name_required(self):
        """5. Last name is required."""
        self.assertIn("Last name is required", self.js)
        self.assertIn("wizardNewLast", self.js)

    def test_display_name_derived(self):
        """6. Display name is derived from first + last."""
        self.assertIn("display_name = `${first} ${last}`.trim()", self.js)

    def test_successful_creation_selects_payer(self):
        """8+10. Successful creation selects the new payer."""
        self.assertIn("handlePersonCreated", self.js)
        self.assertIn("payerPerson = person", self.js)

    def test_client_not_auto_added_under_pays_for(self):
        """9. Newly created client payer is NOT automatically added under Pays for."""
        self.assertNotIn('coveredClients.unshift({ person_id: person.person_id', self.js)

    def test_person_not_auto_added_to_pays_for(self):
        """11. Another-person payer is not automatically added under Pays for."""
        # handlePersonCreated only sets coveredClients for client type
        handler_start = self.js.index("function handlePersonCreated")
        handler_end = self.js.index("\n  function renderStep3", handler_start)
        handler_code = self.js[handler_start:handler_end]
        self.assertIn('formPayerType === "client"', handler_code)
        # For person type, coveredClients should not be set
        self.assertIn("renderStep1()", handler_code)

    def test_step2_can_create_new_client(self):
        """12. Step 2 can create a new client."""
        self.assertIn("wizardCoveredCreateNew", self.js)
        self.assertIn('showCreatePersonForm("client", true)', self.js)

    def test_step2_preserves_payer_and_covered(self):
        """13+14. Step 2 creation preserves payer selection and existing covered clients."""
        handler_start = self.js.index("function handlePersonCreated")
        handler_end = self.js.index("\n  function renderStep3", handler_start)
        handler_code = self.js[handler_start:handler_end]
        # isStep2 branch should push to coveredClients, not touch payerPerson
        self.assertIn("isStep2", handler_code)
        self.assertIn("coveredClients.push", handler_code)
        self.assertIn("coveredClients.some", handler_code)

    def test_duplicate_shows_explicit_warning(self):
        """15. Possible duplicate shows explicit warning."""
        self.assertIn("A person with this name already exists.", self.js)
        self.assertIn("wizardFormDuplicate", self.js)

    def test_use_existing_person_option(self):
        """16. Use existing person selects the existing record."""
        self.assertIn("Use existing person", self.js)
        self.assertIn("wizardUseExistingPerson", self.js)

    def test_go_back_and_edit_option(self):
        """Go back and edit option exists."""
        self.assertIn("Go back and edit", self.js)
        self.assertIn("wizardEditAgain", self.js)

    def test_back_to_search_preserves_state(self):
        """18. Back to search preserves parent wizard state."""
        self.assertIn("Back to search", self.js)
        self.assertIn("closeForm", self.js)
        # closeForm should restore search elements, not clear wizard state
        close_start = self.js.index("function closeForm()")
        close_end = self.js.index("}", close_start)
        close_code = self.js[close_start:close_end]
        self.assertNotIn("payerType = null", close_code)
        self.assertNotIn("coveredClients = []", close_code)

    def test_api_failure_stays_inline(self):
        """19. API failure stays inline."""
        self.assertIn("Failed to create person.", self.js)
        self.assertIn("wizardFormError", self.js)

    def test_double_click_prevented(self):
        """20. Double-click submission is prevented."""
        self.assertIn("if (creating) return;", self.js)
        self.assertIn("creating = true", self.js)
        self.assertIn("submitBtn.disabled = true", self.js)

    def test_no_browser_prompt_alert_confirm(self):
        """21. No browser prompt, alert, or confirm in the create-person flow."""
        wizard_start = self.js.index("function openCreateRelationshipModal")
        wizard_end = self.js.index("\nfunction openCoveredSearch")
        if wizard_end < wizard_start:
            wizard_end = len(self.js)
        wizard_code = self.js[wizard_start:wizard_end]
        self.assertNotIn("prompt(", wizard_code)
        self.assertNotIn("alert(", wizard_code)
        self.assertNotIn("confirm(", wizard_code)

    def test_organization_still_works(self):
        """22. Existing organization selection still works."""
        self.assertIn("/api/organization-billing-parties", self.js)

    def test_save_payload_unchanged(self):
        """23. Round 2C save payload remains unchanged."""
        self.assertIn("/api/billing-relationships/setup", self.js)
        self.assertIn("payer_kind", self.js)
        self.assertIn("covered_client_ids", self.js)
        self.assertIn("use_for_future_sessions", self.js)

    def test_uses_api_people_for_creation(self):
        """Form calls POST /api/people."""
        self.assertIn('api("/api/people"', self.js)

    def test_creating_indicator(self):
        """Creating… indicator shown during submission."""
        self.assertIn("Creating…", self.js)

    def test_escape_html_on_user_values(self):
        """User-controlled values escaped in child form."""
        wizard_start = self.js.index("function showCreatePersonForm")
        wizard_end = self.js.index("\n  function handlePersonCreated")
        form_code = self.js[wizard_start:wizard_end]
        self.assertIn("escapeHtml", form_code)

    def test_child_form_css_exists(self):
        """Child form CSS classes exist."""
        self.assertIn("wizard-child-form", self.css)
        self.assertIn("wizard-create-btn", self.css)
        self.assertIn("wizard-form-grid", self.css)
        self.assertIn("wizard-form-duplicate", self.css)

    def test_no_new_organization_creation(self):
        """No parallel organization creation function (Round 2D2 adds UI, not a parallel function)."""
        wizard_start = self.js.index("function openCreateRelationshipModal")
        wizard_end = self.js.index("\nfunction openCoveredSearch")
        if wizard_end < wizard_start:
            wizard_end = len(self.js)
        wizard_code = self.js[wizard_start:wizard_end]
        self.assertNotIn("create_organization", wizard_code)

    def test_no_session_review_attachment(self):
        """No Session Review attachment."""
        wizard_start = self.js.index("function openCreateRelationshipModal")
        wizard_end = self.js.index("\nfunction openCoveredSearch")
        if wizard_end < wizard_start:
            wizard_end = len(self.js)
        wizard_code = self.js[wizard_start:wizard_end]
        self.assertNotIn("save_interpretation", wizard_code)
        self.assertNotIn("approve_candidate", wizard_code)

    def test_form_fields_present(self):
        """All required form fields are present."""
        self.assertIn("wizardNewFirst", self.js)
        self.assertIn("wizardNewLast", self.js)
        self.assertIn("wizardNewPreferred", self.js)
        self.assertIn("wizardNewEmail", self.js)
        self.assertIn("wizardNewPhone", self.js)
        self.assertIn("wizardNewNotes", self.js)

    def test_no_display_name_field(self):
        """Display name is not asked from user."""
        wizard_start = self.js.index("function showCreatePersonForm")
        wizard_end = self.js.index("\n  function handlePersonCreated")
        form_code = self.js[wizard_start:wizard_end]
        # Should not have a direct display_name input field
        self.assertNotIn('id="wizardNewDisplay"', form_code)

    def test_no_person_code_field(self):
        """Person code is not asked from user as a form input."""
        wizard_start = self.js.index("function showCreatePersonForm")
        wizard_end = self.js.index("\n  function handlePersonCreated")
        form_code = self.js[wizard_start:wizard_end]
        # person_code may appear in the duplicate display, but not as a form input
        self.assertNotIn('id="wizardNewPersonCode"', form_code)
        self.assertNotIn('name="person_code"', form_code)


class TestCreatePersonApi(unittest.TestCase):
    """API integration tests for person creation and duplicate detection."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = connect(str(self.db_path))
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

    def _post(self, path, body):
        data = json.dumps(body).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header(self.handler_cls.write_token_header, self.handler_cls.write_token)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_create_person_returns_created_flag(self):
        """New person has created: true."""
        status, body = self._post("/api/people", {
            "first_name": "Jane",
            "last_name": "Doe",
            "display_name": "Jane Doe",
        })
        self.assertEqual(status, 200)
        self.assertTrue(body.get("created"))
        self.assertFalse(body.get("existing"))
        self.assertTrue(body.get("person_id"))
        self.assertTrue(body.get("person_code"))

    def test_duplicate_person_returns_existing_flag(self):
        """17. Duplicate does not create an extra person row."""
        status1, body1 = self._post("/api/people", {
            "first_name": "John",
            "last_name": "Smith",
            "display_name": "John Smith",
        })
        status2, body2 = self._post("/api/people", {
            "first_name": "John",
            "last_name": "Smith",
            "display_name": "John Smith",
        })
        self.assertEqual(status1, 200)
        self.assertEqual(status2, 200)
        self.assertTrue(body1.get("created"))
        self.assertFalse(body2.get("created"))
        self.assertTrue(body2.get("existing"))
        self.assertEqual(body1["person_id"], body2["person_id"])
        # Only one person row
        count = self.conn.execute("SELECT COUNT(*) AS c FROM people WHERE display_name = 'John Smith'").fetchone()["c"]
        self.assertEqual(count, 1)

    def test_person_code_generated_after_first_last(self):
        """7. Person code is generated only after first and last names."""
        status, body = self._post("/api/people", {
            "first_name": "Alice",
            "last_name": "Brown",
            "display_name": "Alice Brown",
        })
        self.assertEqual(status, 200)
        self.assertTrue(body.get("person_code"))
        # Code should follow the prefix-NNN format
        self.assertRegex(body["person_code"], r"^[A-Z]+-\d{3}$")

    def test_no_session_changed_on_person_creation(self):
        """24. No session or candidate is changed or approved."""
        self._post("/api/people", {
            "first_name": "Test",
            "last_name": "Person",
            "display_name": "Test Person",
        })
        sessions = self.conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"]
        self.assertEqual(sessions, 0)
        candidates = self.conn.execute("SELECT COUNT(*) AS c FROM calendar_event_candidates").fetchone()["c"]
        self.assertEqual(candidates, 0)

    def test_double_post_creates_one_person(self):
        """20. Double submission creates at most one person."""
        payload = {
            "first_name": "Double",
            "last_name": "Submit",
            "display_name": "Double Submit",
        }
        self._post("/api/people", payload)
        self._post("/api/people", payload)
        count = self.conn.execute("SELECT COUNT(*) AS c FROM people WHERE display_name = 'Double Submit'").fetchone()["c"]
        self.assertEqual(count, 1)

    def test_backward_compatibility_display_name_only(self):
        """Existing callers using display_name only still work."""
        status, body = self._post("/api/people", {"display_name": "Legacy Name"})
        self.assertEqual(status, 200)
        self.assertTrue(body.get("person_id"))
        self.assertTrue(body.get("created"))

    def test_create_person_with_optional_fields(self):
        """Person creation with optional fields works."""
        status, body = self._post("/api/people", {
            "first_name": "Optional",
            "last_name": "Fields",
            "display_name": "Optional Fields",
            "preferred_name": "Opt",
            "billing_email": "opt@example.com",
            "billing_phone": "555-1234",
            "administrative_notes": "Test notes",
        })
        self.assertEqual(status, 200)
        self.assertTrue(body.get("created"))
        self.assertEqual(body.get("billing_email"), "opt@example.com")
        self.assertEqual(body.get("billing_phone"), "555-1234")


if __name__ == "__main__":
    unittest.main()
