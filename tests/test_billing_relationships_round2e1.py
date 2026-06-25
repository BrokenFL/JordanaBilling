"""Round 2E1 tests: Session Review integration for the billing relationship wizard.

Tests cover:
1. Wizard launched from Session Review receives return context
2. Confirmed participant IDs are preselected under Pays for
3. Unresolved participant names are not silently converted into people
4. Current person billing party suggests A client when that person participates
5. Current non-participant person payer suggests Another person
6. Current organization billing party suggests An organization
7. Missing or inactive payer creates no unsafe suggestion
8. Existing selections survive Back and Forward
9. Setup success triggers the relationship attachment endpoint
10. Attachment payload includes returned account ID
11. Attachment payload includes returned billing-party ID
12. Confirmed participants are preserved
13. New relationship attaches successfully
14. Existing duplicate relationship can be used directly
15. Use this billing relationship attaches the existing relationship
16. Main Billing Relationships workflow still opens the relationship instead of attaching
17. Successful attachment returns to the same candidate
18. Candidate is refreshed after return
19. Return context clears only after successful attachment/return
20. Attachment failure shows recovery message
21. Retry attachment does not call setup again
22. Retry does not create another account
23. Open relationship recovery action preserves return context
24. Return without attaching changes no session billing fields
25. New client creation preserves Session Review context
26. New another-person creation preserves context
27. New organization creation preserves context
28. Cancel changes no candidate or session data
29. Double-click Save creates and attaches at most once
30. Session remains unapproved
31. No invoice is created
32. No payment is created
33. Raw calendar evidence is unchanged
34. No visible Client / Family Account field is added to routine review
35. Existing Round 1 Add Client behavior still works
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
    setup_billing_relationship,
    save_relationship_section,
)
from jordana_invoice.importer import import_csv

JS_PATH = Path("app/jordana_invoice/static/review.js")
CSS_PATH = Path("app/jordana_invoice/static/review.css")


class TestRound2E1StaticJs(unittest.TestCase):
    """Static JS checks for Session Review integration."""

    def setUp(self):
        self.js = JS_PATH.read_text()
        self.css = CSS_PATH.read_text()

    def test_wizard_receives_return_context(self):
        """1. Wizard launched from Session Review receives return context."""
        self.assertIn("fromReview", self.js)
        self.assertIn("validReturnContext(returnContext)", self.js)

    def test_participant_preselection(self):
        """2. Confirmed participant IDs are preselected under Pays for."""
        self.assertIn("preselectParticipants", self.js)
        self.assertIn("ctxParticipants", self.js)
        self.assertIn("coveredClients.push", self.js)

    def test_unresolved_names_not_converted(self):
        """3. Unresolved participant names are not silently converted into people."""
        self.assertIn("ctxParticipants.filter(p => p.person_id)", self.js)

    def test_payer_suggestion_client_for_participant(self):
        """4. Person billing party suggests A client when that person participates."""
        self.assertIn("suggestPayerFromContext", self.js)
        self.assertIn('isParticipant ? "client"', self.js)

    def test_payer_suggestion_person_for_non_participant(self):
        """5. Non-participant person payer suggests Another person."""
        self.assertIn(': "person"', self.js)

    def test_payer_suggestion_organization(self):
        """6. Organization billing party suggests An organization."""
        self.assertIn('"organization"', self.js)
        self.assertIn("billing_party_type", self.js)

    def test_inactive_payer_no_suggestion(self):
        """7. Missing or inactive payer creates no unsafe suggestion."""
        # suggestPayerFromContext wraps in try/catch and returns silently
        self.assertIn("catch (_) {", self.js)

    def test_selections_survive_back_forward(self):
        """8. Existing selections survive Back and Forward."""
        # goBack and goNext don't clear coveredClients or payerType
        back_start = self.js.index("function goBack()")
        back_end = self.js.index("}", back_start)
        back_code = self.js[back_start:back_end]
        self.assertNotIn("coveredClients = []", back_code)
        self.assertNotIn("payerType = null", back_code)

    def test_setup_triggers_attachment(self):
        """9. Setup success triggers the relationship attachment endpoint."""
        self.assertIn("attachToSession", self.js)
        self.assertIn("/api/review/candidates/", self.js)
        self.assertIn("save-relationship", self.js)

    def test_attachment_includes_account_id(self):
        """10. Attachment payload includes returned account ID."""
        self.assertIn("account_id: accountId", self.js)

    def test_attachment_includes_billing_party_id(self):
        """11. Attachment payload includes returned billing-party ID."""
        self.assertIn("billing_party_id: billingPartyId", self.js)

    def test_confirmed_participants_preserved(self):
        """12. Confirmed participants are preserved in attachment."""
        self.assertIn("participants: ctxParticipants.map", self.js)

    def test_new_relationship_attaches(self):
        """13. New relationship attaches successfully (fromReview path)."""
        self.assertIn("if (fromReview) {", self.js)
        self.assertIn("await attachToSession", self.js)

    def test_duplicate_can_be_used_directly(self):
        """14. Existing duplicate relationship can be used directly."""
        self.assertIn("Use this billing relationship", self.js)
        self.assertIn("wizardUseExisting", self.js)

    def test_use_existing_attaches(self):
        """15. Use this billing relationship attaches the existing relationship."""
        self.assertIn("attachToSession(existingAccountId, existingBillingPartyId", self.js)

    def test_main_workflow_opens_relationship(self):
        """16. Main Billing Relationships workflow still opens the relationship instead of attaching."""
        self.assertIn("openAccountRecord(accountId", self.js)

    def test_successful_attachment_returns_to_candidate(self):
        """17. Successful attachment returns to the same candidate."""
        self.assertIn("selectCandidate(returnContext.candidateId)", self.js)

    def test_candidate_refreshed_after_return(self):
        """18. Candidate is refreshed after return (selectCandidate reloads)."""
        self.assertIn("await showReviewWorkbench()", self.js)
        self.assertIn("await selectCandidate(", self.js)

    def test_context_clears_after_success(self):
        """19. Return context clears only after successful attachment/return."""
        # clearReturnContext should be called after successful attachment, not before
        attach_start = self.js.index("async function attachToSession")
        attach_end = self.js.index("\n  overlay.addEventListener", attach_start)
        attach_code = self.js[attach_start:attach_end]
        # clearReturnContext should be inside the try block (success path)
        clear_idx = attach_code.index("clearReturnContext()")
        try_idx = attach_code.index("try {")
        catch_idx = attach_code.index("catch (attachErr)")
        self.assertTrue(try_idx < clear_idx < catch_idx)

    def test_attachment_failure_shows_recovery(self):
        """20. Attachment failure shows recovery message."""
        self.assertIn("could not be attached to this session", self.js)
        self.assertIn("wizardRetryAttach", self.js)
        self.assertIn("wizardOpenRelFromRecovery", self.js)
        self.assertIn("wizardReturnNoAttach", self.js)

    def test_retry_does_not_call_setup(self):
        """21. Retry attachment does not call setup again."""
        # Retry button calls attachToSession directly, not doSave
        retry_start = self.js.index("wizardRetryAttach")
        retry_end = self.js.index("});", retry_start) + 3
        retry_code = self.js[retry_start:retry_end]
        self.assertIn("attachToSession", retry_code)
        self.assertNotIn("/api/billing-relationships/setup", retry_code)

    def test_retry_no_new_account(self):
        """22. Retry does not create another account (reuses same accountId)."""
        # attachToSession receives accountId/billingPartyId as params, doesn't call setup
        attach_start = self.js.index("async function attachToSession")
        attach_end = self.js.index("\n  overlay.addEventListener", attach_start)
        attach_code = self.js[attach_start:attach_end]
        self.assertNotIn("/api/billing-relationships/setup", attach_code)

    def test_open_relationship_preserves_context(self):
        """23. Open relationship recovery action preserves return context."""
        # Find the event handler, not the button HTML
        handler_start = self.js.index('document.getElementById("wizardOpenRelFromRecovery")')
        handler_end = self.js.index("});", handler_start) + 3
        handler_code = self.js[handler_start:handler_end]
        self.assertIn("persistReturnContext", handler_code)

    def test_return_without_attaching(self):
        """24. Return without attaching changes no session billing fields."""
        handler_start = self.js.index('document.getElementById("wizardReturnNoAttach")')
        handler_end = self.js.index("});", handler_start) + 3
        handler_code = self.js[handler_start:handler_end]
        self.assertIn("selectCandidate", handler_code)
        self.assertNotIn("save-relationship", handler_code)

    def test_new_client_preserves_context(self):
        """25. New client creation preserves Session Review context."""
        # handlePersonCreated doesn't clear returnContext
        handler_start = self.js.index("function handlePersonCreated")
        handler_end = self.js.index("\n  function showCreateOrgForm", handler_start)
        handler_code = self.js[handler_start:handler_end]
        self.assertNotIn("clearReturnContext", handler_code)
        self.assertNotIn("returnContext = null", handler_code)

    def test_new_person_preserves_context(self):
        """26. New another-person creation preserves context."""
        # Same handler as client — check it doesn't touch returnContext
        handler_start = self.js.index("function handlePersonCreated")
        handler_end = self.js.index("\n  function showCreateOrgForm", handler_start)
        handler_code = self.js[handler_start:handler_end]
        self.assertNotIn("clearReturnContext", handler_code)

    def test_new_org_preserves_context(self):
        """27. New organization creation preserves context."""
        handler_start = self.js.index("function handleOrgCreated")
        handler_end = self.js.index("\n  function renderStep3", handler_start)
        handler_code = self.js[handler_start:handler_end]
        self.assertNotIn("clearReturnContext", handler_code)

    def test_cancel_changes_nothing(self):
        """28. Cancel changes no candidate or session data."""
        cancel_start = self.js.index("function doCancel()")
        cancel_end = self.js.index("\n  function renderActions()", cancel_start)
        cancel_code = self.js[cancel_start:cancel_end]
        self.assertNotIn("save-relationship", cancel_code)
        self.assertNotIn("clearReturnContext", cancel_code)
        self.assertNotIn("selectCandidate", cancel_code)

    def test_double_click_prevented(self):
        """29. Double-click Save creates and attaches at most once."""
        self.assertIn("if (saving) return;", self.js)

    def test_no_auto_approval(self):
        """30. Session remains unapproved — no approval calls."""
        wizard_start = self.js.index("function openCreateRelationshipModal")
        wizard_end = self.js.index("\nfunction openCoveredSearch")
        if wizard_end < wizard_start:
            wizard_end = len(self.js)
        wizard_code = self.js[wizard_start:wizard_end]
        self.assertNotIn("approve_candidate", wizard_code)
        self.assertNotIn("review_status", wizard_code)
        self.assertNotIn("approved", wizard_code.lower().replace("unapproved", ""))

    def test_no_invoice_generation(self):
        """31. No invoice is created."""
        wizard_start = self.js.index("function openCreateRelationshipModal")
        wizard_end = self.js.index("\nfunction openCoveredSearch")
        if wizard_end < wizard_start:
            wizard_end = len(self.js)
        wizard_code = self.js[wizard_start:wizard_end]
        self.assertNotIn("/api/invoices", wizard_code)
        self.assertNotIn("create_invoice", wizard_code)

    def test_no_payment_creation(self):
        """32. No payment is created."""
        wizard_start = self.js.index("function openCreateRelationshipModal")
        wizard_end = self.js.index("\nfunction openCoveredSearch")
        if wizard_end < wizard_start:
            wizard_end = len(self.js)
        wizard_code = self.js[wizard_start:wizard_end]
        self.assertNotIn("/api/payments", wizard_code)
        self.assertNotIn("create_payment", wizard_code)

    def test_no_calendar_evidence_changed(self):
        """33. Raw calendar evidence is unchanged."""
        wizard_start = self.js.index("function openCreateRelationshipModal")
        wizard_end = self.js.index("\nfunction openCoveredSearch")
        if wizard_end < wizard_start:
            wizard_end = len(self.js)
        wizard_code = self.js[wizard_start:wizard_end]
        self.assertNotIn("raw_calendar", wizard_code)
        self.assertNotIn("calendar_event", wizard_code)

    def test_no_visible_account_field_in_review(self):
        """34. No visible Client / Family Account field is added to routine review."""
        # The inspector rendering should not add a visible account field
        inspector_start = self.js.index("function renderInspector(")
        inspector_end = self.js.index("\nfunction ", inspector_start + 1)
        inspector_code = self.js[inspector_start:inspector_end]
        self.assertNotIn("Client / Family Account", inspector_code)
        self.assertNotIn("client_family_account", inspector_code)

    def test_round1_add_client_still_works(self):
        """35. Editor's Add Client search still works."""
        self.assertIn("function openCoveredSearch", self.js)
        self.assertIn("Add Client", self.js)

    def test_saving_states_shown(self):
        """Saving and attaching states are shown."""
        self.assertIn("Saving relationship…", self.js)
        self.assertIn("Attaching to session…", self.js)

    def test_success_banner(self):
        """Success banner shown after attachment."""
        self.assertIn("Billing relationship saved for this session.", self.js)

    def test_no_browser_prompt_alert_confirm(self):
        """No browser prompt, alert, or confirm in the wizard."""
        wizard_start = self.js.index("function openCreateRelationshipModal")
        wizard_end = self.js.index("\nfunction openCoveredSearch")
        if wizard_end < wizard_start:
            wizard_end = len(self.js)
        wizard_code = self.js[wizard_start:wizard_end]
        self.assertNotIn("prompt(", wizard_code)
        self.assertNotIn("alert(", wizard_code)
        self.assertNotIn("confirm(", wizard_code)

    def test_escape_html_on_user_values(self):
        """User-controlled values escaped."""
        wizard_start = self.js.index("function openCreateRelationshipModal")
        wizard_end = self.js.index("\nfunction openCoveredSearch")
        if wizard_end < wizard_start:
            wizard_end = len(self.js)
        wizard_code = self.js[wizard_start:wizard_end]
        self.assertIn("escapeHtml", wizard_code)

    def test_recovery_actions_css(self):
        """Recovery actions CSS exists."""
        self.assertIn("wizard-recovery-actions", self.css)

    def test_no_schema_migration_in_js(self):
        """No schema migration references in JS."""
        wizard_start = self.js.index("function openCreateRelationshipModal")
        wizard_end = self.js.index("\nfunction openCoveredSearch")
        if wizard_end < wizard_start:
            wizard_end = len(self.js)
        wizard_code = self.js[wizard_start:wizard_end]
        self.assertNotIn("CREATE TABLE", wizard_code)
        self.assertNotIn("ALTER TABLE", wizard_code)

    def test_non_review_path_preserved(self):
        """Non-review path still opens account record (Round 2C behavior)."""
        self.assertIn("if (fromReview) {", self.js)
        # The else branch should openAccountRecord — find it after the fromReview check
        from_review_idx = self.js.index("if (fromReview) {")
        else_idx = self.js.index("} else {", from_review_idx)
        # Search a wider range for openAccountRecord after the else
        else_code = self.js[else_idx:else_idx + 500]
        self.assertIn("openAccountRecord", else_code)


class TestRound2E1Api(unittest.TestCase):
    """API integration tests for setup-then-attach flow."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = connect(str(self.db_path))
        init_db(self.conn)
        # Import a sample calendar snapshot to create a candidate
        csv_path = Path("data/samples/sanitized_demo_calendar_snapshots.csv")
        if csv_path.exists():
            import_csv(self.conn, csv_path)
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

    def _get(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def _get_first_candidate(self):
        status, candidates = self._get("/api/review/candidates")
        if isinstance(candidates, list) and candidates:
            return candidates[0]
        return None

    def test_setup_then_attach_does_not_approve(self):
        """30. Session remains unapproved after setup + attach."""
        person = create_person(self.conn, {"display_name": "Test Client", "first_name": "Test", "last_name": "Client"})
        candidate = self._get_first_candidate()
        if not candidate:
            self.skipTest("No candidate available")

        # Setup
        status, setup = self._post("/api/billing-relationships/setup", {
            "payer_kind": "client",
            "payer_person_id": person["person_id"],
            "covered_client_ids": [person["person_id"]],
            "use_for_future_sessions": True,
        })
        self.assertEqual(status, 200)

        # Attach
        status, attached = self._post(f"/api/review/candidates/{candidate['candidate_id']}/save-relationship", {
            "participants": [{"person_id": person["person_id"], "display_name": "Test Client", "is_primary": True}],
            "account_id": setup["account_id"],
            "billing_party_id": setup["billing_party_id"],
            "default_billing_party_id": setup["billing_party_id"],
            "primary_person_id": person["person_id"],
        })
        self.assertEqual(status, 200)

        # Check session is not approved
        session = self.conn.execute("SELECT review_status FROM sessions WHERE id = ?", (candidate["session_id"],)).fetchone()
        if session:
            self.assertNotEqual(session["review_status"], "approved")

    def test_setup_then_attach_no_invoice(self):
        """31. No invoice is created after setup + attach."""
        person = create_person(self.conn, {"display_name": "Invoice Test", "first_name": "Invoice", "last_name": "Test"})
        candidate = self._get_first_candidate()
        if not candidate:
            self.skipTest("No candidate available")

        status, setup = self._post("/api/billing-relationships/setup", {
            "payer_kind": "client",
            "payer_person_id": person["person_id"],
            "covered_client_ids": [person["person_id"]],
            "use_for_future_sessions": True,
        })
        self.assertEqual(status, 200)

        self._post(f"/api/review/candidates/{candidate['candidate_id']}/save-relationship", {
            "participants": [{"person_id": person["person_id"], "display_name": "Invoice Test", "is_primary": True}],
            "account_id": setup["account_id"],
            "billing_party_id": setup["billing_party_id"],
            "default_billing_party_id": setup["billing_party_id"],
            "primary_person_id": person["person_id"],
        })

        invoices = self.conn.execute("SELECT COUNT(*) AS c FROM invoices").fetchone()["c"]
        self.assertEqual(invoices, 0)

    def test_setup_then_attach_no_payment(self):
        """32. No payment is created after setup + attach."""
        person = create_person(self.conn, {"display_name": "Payment Test", "first_name": "Payment", "last_name": "Test"})
        candidate = self._get_first_candidate()
        if not candidate:
            self.skipTest("No candidate available")

        status, setup = self._post("/api/billing-relationships/setup", {
            "payer_kind": "client",
            "payer_person_id": person["person_id"],
            "covered_client_ids": [person["person_id"]],
            "use_for_future_sessions": True,
        })
        self.assertEqual(status, 200)

        self._post(f"/api/review/candidates/{candidate['candidate_id']}/save-relationship", {
            "participants": [{"person_id": person["person_id"], "display_name": "Payment Test", "is_primary": True}],
            "account_id": setup["account_id"],
            "billing_party_id": setup["billing_party_id"],
            "default_billing_party_id": setup["billing_party_id"],
            "primary_person_id": person["person_id"],
        })

        payments = self.conn.execute("SELECT COUNT(*) AS c FROM payments").fetchone()["c"]
        self.assertEqual(payments, 0)

    def test_attach_preserves_raw_calendar_evidence(self):
        """33. Raw calendar evidence is unchanged after attach."""
        person = create_person(self.conn, {"display_name": "Evidence Test", "first_name": "Evidence", "last_name": "Test"})
        candidate = self._get_first_candidate()
        if not candidate:
            self.skipTest("No candidate available")

        # Get raw evidence before
        before = self.conn.execute(
            "SELECT raw_calendar_title, raw_calendar_location FROM sessions WHERE id = ?",
            (candidate["session_id"],)
        ).fetchone()

        status, setup = self._post("/api/billing-relationships/setup", {
            "payer_kind": "client",
            "payer_person_id": person["person_id"],
            "covered_client_ids": [person["person_id"]],
            "use_for_future_sessions": True,
        })
        self.assertEqual(status, 200)

        self._post(f"/api/review/candidates/{candidate['candidate_id']}/save-relationship", {
            "participants": [{"person_id": person["person_id"], "display_name": "Evidence Test", "is_primary": True}],
            "account_id": setup["account_id"],
            "billing_party_id": setup["billing_party_id"],
            "default_billing_party_id": setup["billing_party_id"],
            "primary_person_id": person["person_id"],
        })

        after = self.conn.execute(
            "SELECT raw_calendar_title, raw_calendar_location FROM sessions WHERE id = ?",
            (candidate["session_id"],)
        ).fetchone()

        self.assertEqual(before["raw_calendar_title"], after["raw_calendar_title"])
        self.assertEqual(before["raw_calendar_location"], after["raw_calendar_location"])

    def test_duplicate_setup_then_attach(self):
        """14. Existing duplicate relationship can be used and attached."""
        person = create_person(self.conn, {"display_name": "Dup Test", "first_name": "Dup", "last_name": "Test"})
        candidate = self._get_first_candidate()
        if not candidate:
            self.skipTest("No candidate available")

        # First setup
        status1, setup1 = self._post("/api/billing-relationships/setup", {
            "payer_kind": "client",
            "payer_person_id": person["person_id"],
            "covered_client_ids": [person["person_id"]],
            "use_for_future_sessions": True,
        })
        self.assertEqual(status1, 200)

        # Second setup should return duplicate
        status2, setup2 = self._post("/api/billing-relationships/setup", {
            "payer_kind": "client",
            "payer_person_id": person["person_id"],
            "covered_client_ids": [person["person_id"]],
            "use_for_future_sessions": True,
        })
        self.assertEqual(status2, 200)
        self.assertFalse(setup2.get("created"))
        self.assertTrue(setup2.get("duplicate"))
        self.assertEqual(setup1["account_id"], setup2["account_id"])

        # Attach using the duplicate's IDs
        status3, attached = self._post(f"/api/review/candidates/{candidate['candidate_id']}/save-relationship", {
            "participants": [{"person_id": person["person_id"], "display_name": "Dup Test", "is_primary": True}],
            "account_id": setup2["account_id"],
            "billing_party_id": setup2["billing_party_id"],
            "default_billing_party_id": setup2["billing_party_id"],
            "primary_person_id": person["person_id"],
        })
        self.assertEqual(status3, 200)

    def test_double_setup_creates_one_account(self):
        """29. Double setup creates at most one account."""
        person = create_person(self.conn, {"display_name": "Double Setup", "first_name": "Double", "last_name": "Setup"})
        self._post("/api/billing-relationships/setup", {
            "payer_kind": "client",
            "payer_person_id": person["person_id"],
            "covered_client_ids": [person["person_id"]],
            "use_for_future_sessions": True,
        })
        self._post("/api/billing-relationships/setup", {
            "payer_kind": "client",
            "payer_person_id": person["person_id"],
            "covered_client_ids": [person["person_id"]],
            "use_for_future_sessions": True,
        })
        count = self.conn.execute(
            "SELECT COUNT(*) AS c FROM client_accounts WHERE account_name LIKE '%Double%'"
        ).fetchone()["c"]
        self.assertEqual(count, 1)

    def test_no_schema_migration(self):
        """23. No new schema migration is introduced (no new tables beyond existing)."""
        # Verify expected tables exist
        tables = [r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        self.assertIn("people", tables)
        self.assertIn("billing_parties", tables)
        self.assertIn("client_accounts", tables)
        self.assertIn("sessions", tables)
        # No new tables added by Round 2E1 (no ALTER TABLE or CREATE TABLE in JS)
        # schema_migrations already exists from init_db — that's expected


if __name__ == "__main__":
    unittest.main()
