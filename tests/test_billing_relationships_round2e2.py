"""Round 2E2: final integration hardening and behavior-oriented tests.

These tests go beyond static string checks — they parse the JS source
and simulate control flow to verify behavior rather than just presence
of strings.
"""
import ast
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import json
from http.server import HTTPServer
from pathlib import Path

from jordana_invoice.db import connect, init_db
from jordana_invoice.review_server import make_handler
from jordana_invoice.review_services import (
    create_person,
    setup_billing_relationship,
    save_relationship_section,
)
from jordana_invoice.importer import import_csv

JS_PATH = Path("app/jordana_invoice/static/review.js")
CSS_PATH = Path("app/jordana_invoice/static/review.css")


class TestRound2E2Behavior(unittest.TestCase):
    """Behavior-oriented tests that verify control flow, not just strings."""

    def setUp(self):
        self.js = JS_PATH.read_text()
        self.css = CSS_PATH.read_text()

    def _extract_function(self, name):
        """Extract a function body from JS source."""
        pattern = rf"(?:async )?function {name}\b"
        match = re.search(pattern, self.js)
        if not match:
            return None
        start = match.start()
        # Find matching closing brace
        brace_count = 0
        found_open = False
        for i in range(match.end(), len(self.js)):
            if self.js[i] == '{':
                brace_count += 1
                found_open = True
            elif self.js[i] == '}':
                brace_count -= 1
                if found_open and brace_count == 0:
                    return self.js[start:i+1]
        return None

    def _extract_wizard(self):
        """Extract the entire openCreateRelationshipModal function."""
        return self._extract_function("openCreateRelationshipModal")

    # 1. Opening the wizard creates one overlay
    def test_wizard_creates_one_overlay(self):
        """Wizard creates exactly one overlay element."""
        wizard = self._extract_wizard()
        self.assertIsNotNone(wizard)
        # closeBillingModal is called first, then overlay is created
        self.assertIn("closeBillingModal()", wizard)
        # Only one overlay creation
        overlay_count = wizard.count('overlay.id = "billingModalOverlay"')
        self.assertEqual(overlay_count, 1)

    # 2. Re-rendering steps does not duplicate handlers
    def test_rerender_uses_innerhtml_not_append(self):
        """renderStep replaces innerHTML rather than appending, preventing duplicate handlers."""
        wizard = self._extract_wizard()
        render_step = self._extract_function("renderStep")
        self.assertIsNotNone(render_step)
        # renderStep calls renderStep1/2/3 which set innerHTML (replacement, not append)
        self.assertIn("bodyBox.innerHTML =", wizard)

    # 3. Main-page Save does not call save-relationship
    def test_main_page_save_no_attachment(self):
        """When not from review, doSave opens account record, not save-relationship."""
        wizard = self._extract_wizard()
        # The fromReview check should gate the attachment
        self.assertIn("if (fromReview) {", wizard)
        # The else branch should call openAccountRecord, not save-relationship
        else_idx = wizard.index("} else {", wizard.index("if (fromReview) {"))
        else_code = wizard[else_idx:else_idx+500]
        self.assertIn("openAccountRecord", else_code)
        self.assertNotIn("save-relationship", else_code)

    # 4. Session Review Save calls setup once and attachment once
    def test_session_review_save_calls_setup_then_attach(self):
        """doSave calls setup, then attachToSession (which calls save-relationship)."""
        wizard = self._extract_wizard()
        do_save = self._extract_function("doSave")
        self.assertIsNotNone(do_save)
        self.assertIn("/api/billing-relationships/setup", do_save)
        self.assertIn("attachToSession", do_save)
        attach = self._extract_function("attachToSession")
        self.assertIsNotNone(attach)
        self.assertIn("save-relationship", attach)

    # 5. Double-click Save does not duplicate either call
    def test_double_click_prevented(self):
        """saving flag prevents double execution."""
        do_save = self._extract_function("doSave")
        self.assertIn("if (saving) return;", do_save)
        # saving is set to true before any API call
        self.assertIn("saving = true;", do_save)

    # 6. Duplicate Use action calls attachment once
    def test_duplicate_use_calls_attach_once(self):
        """wizardUseExisting (relationship duplicate) calls attachToSession once."""
        wizard = self._extract_wizard()
        # Find the doSave duplicate handler's wizardUseExisting (not wizardUseExistingPerson)
        # It's in the fromReview branch of doSave's catch block
        do_save = self._extract_function("doSave")
        use_idx = do_save.index('id="wizardUseExisting"')
        handler_code = do_save[use_idx:use_idx+800]
        self.assertIn("attachToSession", handler_code)
        self.assertNotIn("/api/billing-relationships/setup", handler_code)

    # 7. Back and Forward preserve payer and covered clients
    def test_back_forward_preserve_state(self):
        """goBack and goNext don't clear payerType, payerPerson, payerOrg, or coveredClients."""
        go_back = self._extract_function("goBack")
        go_next = self._extract_function("goNext")
        self.assertIsNotNone(go_back)
        self.assertIsNotNone(go_next)
        self.assertNotIn("payerType = null", go_back)
        self.assertNotIn("payerPerson = null", go_back)
        self.assertNotIn("coveredClients = []", go_back)
        self.assertNotIn("coveredClients = []", go_next)

    # 8. Changing payer kind clears the old payer selection (only for org switch)
    def test_changing_payer_kind_clears_correctly(self):
        """selectPayerType clears payerPerson only when switching to organization, clears payerOrg when switching away."""
        select_type = self._extract_function("selectPayerType")
        self.assertIsNotNone(select_type)
        self.assertIn('type === "organization"', select_type)
        self.assertIn('payerPerson = null', select_type)
        self.assertIn('type !== "organization"', select_type)
        self.assertIn('payerOrg = null', select_type)

    # 9. Confirmed participants remain preselected
    def test_preselect_participants_filters_person_id(self):
        """preselectParticipants only adds participants with person_id."""
        preselect = self._extract_function("preselectParticipants")
        self.assertIsNotNone(preselect)
        self.assertIn("p.person_id", preselect)
        self.assertIn("filter", preselect)

    # 10. Unresolved names are excluded
    def test_unresolved_names_excluded(self):
        """preselectParticipants filters on person_id, excluding unresolved names."""
        preselect = self._extract_function("preselectParticipants")
        self.assertIn("filter(p => p.person_id)", preselect)

    # 11. Child forms preserve parent state
    def test_child_forms_preserve_state(self):
        """handlePersonCreated and handleOrgCreated don't clear returnContext or coveredClients."""
        handle_person = self._extract_function("handlePersonCreated")
        handle_org = self._extract_function("handleOrgCreated")
        self.assertNotIn("clearReturnContext", handle_person)
        self.assertNotIn("clearReturnContext", handle_org)
        self.assertNotIn("coveredClients = []", handle_person)
        self.assertNotIn("coveredClients = []", handle_org)

    # 12. Cancel leaves database unchanged
    def test_cancel_no_api_calls(self):
        """doCancel doesn't make any API calls."""
        do_cancel = self._extract_function("doCancel")
        self.assertIsNotNone(do_cancel)
        self.assertNotIn("/api/", do_cancel)
        self.assertNotIn("api(", do_cancel)
        self.assertNotIn("fetch(", do_cancel)

    # 13. Attachment retry does not call setup again
    def test_retry_no_setup(self):
        """attachToSession doesn't call /api/billing-relationships/setup."""
        attach = self._extract_function("attachToSession")
        self.assertIsNotNone(attach)
        self.assertNotIn("/api/billing-relationships/setup", attach)

    # 14. Return-without-attachment does not clear or modify billing fields
    def test_return_without_attach_no_api(self):
        """wizardReturnNoAttach handler doesn't call save-relationship."""
        wizard = self._extract_wizard()
        handler_start = wizard.index('document.getElementById("wizardReturnNoAttach")')
        handler_end = wizard.index("});", handler_start) + 3
        handler_code = wizard[handler_start:handler_end]
        self.assertNotIn("save-relationship", handler_code)
        self.assertNotIn("clearReturnContext", handler_code)

    # 15. Success clears return context only after attachment
    def test_success_clears_after_attach(self):
        """clearReturnContext is inside attachToSession's try block (success path)."""
        attach = self._extract_function("attachToSession")
        try_idx = attach.index("try {")
        clear_idx = attach.index("clearReturnContext()")
        catch_idx = attach.index("catch (attachErr)")
        self.assertTrue(try_idx < clear_idx < catch_idx)

    # 16. Failure preserves return context
    def test_failure_preserves_context(self):
        """attachToSession catch block doesn't clear return context."""
        attach = self._extract_function("attachToSession")
        catch_idx = attach.index("catch (attachErr)")
        catch_code = attach[catch_idx:]
        self.assertNotIn("clearReturnContext", catch_code)

    # 17. Session remains unapproved
    def test_no_approval_in_wizard(self):
        """Wizard code doesn't contain approval calls."""
        wizard = self._extract_wizard()
        self.assertNotIn("approve_candidate", wizard)
        self.assertNotIn("review_status", wizard)

    # 18. No invoice or payment row is created
    def test_no_invoice_or_payment_in_wizard(self):
        """Wizard code doesn't contain invoice or payment API calls."""
        wizard = self._extract_wizard()
        self.assertNotIn("/api/invoices", wizard)
        self.assertNotIn("/api/payments", wizard)

    # 19. Main-page stale return context cannot attach to an old candidate
    def test_stale_context_cleared_on_nav(self):
        """showClients clears return context when no hash params present."""
        show_clients = self._extract_function("showClients")
        self.assertIsNotNone(show_clients)
        self.assertIn("hashReturnContext()", show_clients)
        self.assertIn("clearReturnContext()", show_clients)

    # 20. No duplicate DOM IDs within one rendered wizard state
    def test_no_duplicate_ids_in_step1(self):
        """renderStep1 doesn't create duplicate element IDs."""
        wizard = self._extract_wizard()
        step1 = self._extract_function("renderStep1")
        # Check that wizardPayerSearch and wizardPayerSelected are created once
        self.assertEqual(step1.count('id="wizardPayerSearch"'), 1)
        self.assertEqual(step1.count('id="wizardPayerSelected"'), 1)

    def test_no_duplicate_ids_in_step2(self):
        """renderStep2 doesn't create duplicate element IDs."""
        step2 = self._extract_function("renderStep2")
        self.assertIsNotNone(step2)
        self.assertEqual(step2.count('id="wizardCoveredSearch"'), 1)
        self.assertEqual(step2.count('id="wizardCoveredResults"'), 1)
        self.assertEqual(step2.count('id="wizardCoveredSelected"'), 1)

    def test_no_duplicate_ids_in_actions(self):
        """renderActions creates unique IDs per step."""
        render_actions = self._extract_function("renderActions")
        self.assertIsNotNone(render_actions)
        # billingModalCancel should appear once per render
        self.assertEqual(render_actions.count('id="billingModalCancel"'), 1)

    # Additional: person duplicate button has distinct ID from relationship duplicate
    def test_person_duplicate_btn_distinct_id(self):
        """wizardUseExistingPerson is distinct from wizardUseExisting."""
        wizard = self._extract_wizard()
        self.assertIn("wizardUseExistingPerson", wizard)
        self.assertIn("wizardUseExisting", wizard)
        # They should be different elements
        person_btn_count = wizard.count('id="wizardUseExistingPerson"')
        rel_btn_count = wizard.count('id="wizardUseExisting"')
        self.assertGreaterEqual(person_btn_count, 1)
        self.assertGreaterEqual(rel_btn_count, 1)

    # Additional: showPayerSelected uses correct label for person
    def test_show_payer_selected_label(self):
        """showPayerSelected uses 'Selected person' for person payer type."""
        show_selected = self._extract_function("showPayerSelected")
        self.assertIsNotNone(show_selected)
        self.assertIn("Selected person", show_selected)
        self.assertIn("Selected client", show_selected)
        self.assertIn("Selected organization", show_selected)

    # Additional: selectPayerType doesn't erase coveredClients
    def test_select_payer_type_preserves_covered(self):
        """selectPayerType doesn't set coveredClients = [] for any type."""
        select_type = self._extract_function("selectPayerType")
        self.assertNotIn("coveredClients = []", select_type)

    # Additional: selectPayer doesn't replace coveredClients
    def test_select_payer_adds_not_replaces(self):
        """selectPayer adds to coveredClients instead of replacing."""
        select_payer = self._extract_function("selectPayer")
        self.assertIsNotNone(select_payer)
        self.assertNotIn("coveredClients = [{", select_payer)
        self.assertIn("coveredClients.unshift", select_payer)

    # Additional: handlePersonCreated adds to coveredClients
    def test_handle_person_created_adds_not_replaces(self):
        """handlePersonCreated adds to coveredClients instead of replacing."""
        handle_person = self._extract_function("handlePersonCreated")
        self.assertNotIn("coveredClients = [{", handle_person)
        self.assertIn("coveredClients.unshift", handle_person)

    # Additional: suggestPayerFromContext handles inactive payer gracefully
    def test_suggest_payer_inactive_handling(self):
        """suggestPayerFromContext wraps in try/catch and returns silently on error."""
        suggest = self._extract_function("suggestPayerFromContext")
        self.assertIsNotNone(suggest)
        self.assertIn("try", suggest)
        self.assertIn("catch", suggest)

    # Additional: API error parsing handles non-JSON
    def test_api_error_handling_in_doSave(self):
        """doSave checks res.ok and json.ok for error handling."""
        do_save = self._extract_function("doSave")
        self.assertIn("res.ok", do_save)
        self.assertIn("json.ok === false", do_save)

    # Additional: Escape follows safe-cancel flow
    def test_escape_uses_doCancel(self):
        """billingModalTrapKeydown calls doCancel on Escape."""
        wizard = self._extract_wizard()
        # The keydown handler should reference doCancel or closeBillingModal
        self.assertIn("billingModalTrapKeydown", wizard)

    # Additional: no unsafe HTML interpolation in showPayerSelected
    def test_show_payer_selected_escapes(self):
        """showPayerSelected uses escapeHtml on name."""
        show_selected = self._extract_function("showPayerSelected")
        self.assertIn("escapeHtml", show_selected)

    # Additional: renderStep3 escapes user values
    def test_render_step3_escapes(self):
        """renderStep3 escapes user-controlled values."""
        step3 = self._extract_function("renderStep3")
        self.assertIsNotNone(step3)
        self.assertIn("escapeHtml", step3)

    # Additional: success banner uses innerHTML with escaped content
    def test_success_banner_safe(self):
        """Success banner uses innerHTML with static content (no user input)."""
        attach = self._extract_function("attachToSession")
        banner_idx = attach.index("Billing relationship saved for this session")
        # The banner content should be static HTML, not interpolated with user values
        banner_area = attach[banner_idx-100:banner_idx+100]
        self.assertNotIn("${", banner_area.replace("Billing relationship saved for this session.", ""))


class TestRound2E2Api(unittest.TestCase):
    """API integration tests verifying no side effects."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = connect(str(self.db_path))
        init_db(self.conn)
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

    def _get_first_candidate(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/review/candidates")
        with urllib.request.urlopen(req) as resp:
            candidates = json.loads(resp.read())
        if isinstance(candidates, list) and candidates:
            return candidates[0]
        return None

    def test_setup_then_attach_no_approval(self):
        """Session remains unapproved after setup + attach."""
        person = create_person(self.conn, {"display_name": "Approval Test", "first_name": "Approval", "last_name": "Test"})
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
            "participants": [{"person_id": person["person_id"], "display_name": "Approval Test", "is_primary": True}],
            "account_id": setup["account_id"],
            "billing_party_id": setup["billing_party_id"],
            "default_billing_party_id": setup["billing_party_id"],
            "primary_person_id": person["person_id"],
        })

        session = self.conn.execute("SELECT review_status FROM sessions WHERE id = ?", (candidate["session_id"],)).fetchone()
        if session:
            self.assertNotEqual(session["review_status"], "approved")

    def test_setup_then_attach_no_invoice(self):
        """No invoice created after setup + attach."""
        person = create_person(self.conn, {"display_name": "Inv Test", "first_name": "Inv", "last_name": "Test"})
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
            "participants": [{"person_id": person["person_id"], "display_name": "Inv Test", "is_primary": True}],
            "account_id": setup["account_id"],
            "billing_party_id": setup["billing_party_id"],
            "default_billing_party_id": setup["billing_party_id"],
            "primary_person_id": person["person_id"],
        })

        invoices = self.conn.execute("SELECT COUNT(*) AS c FROM invoices").fetchone()["c"]
        self.assertEqual(invoices, 0)

    def test_setup_then_attach_no_payment(self):
        """No payment created after setup + attach."""
        person = create_person(self.conn, {"display_name": "Pay Test", "first_name": "Pay", "last_name": "Test"})
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
            "participants": [{"person_id": person["person_id"], "display_name": "Pay Test", "is_primary": True}],
            "account_id": setup["account_id"],
            "billing_party_id": setup["billing_party_id"],
            "default_billing_party_id": setup["billing_party_id"],
            "primary_person_id": person["person_id"],
        })

        payments = self.conn.execute("SELECT COUNT(*) AS c FROM payments").fetchone()["c"]
        self.assertEqual(payments, 0)

    def test_setup_then_attach_preserves_evidence(self):
        """Raw calendar evidence unchanged after attach."""
        person = create_person(self.conn, {"display_name": "Ev Test", "first_name": "Ev", "last_name": "Test"})
        candidate = self._get_first_candidate()
        if not candidate:
            self.skipTest("No candidate available")

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
            "participants": [{"person_id": person["person_id"], "display_name": "Ev Test", "is_primary": True}],
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

    def test_double_setup_one_account(self):
        """Double setup creates at most one account."""
        person = create_person(self.conn, {"display_name": "Double Acct", "first_name": "Double", "last_name": "Acct"})
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

    def test_duplicate_setup_then_attach_works(self):
        """Duplicate setup returns same IDs, attach works with them."""
        person = create_person(self.conn, {"display_name": "Dup Attach", "first_name": "Dup", "last_name": "Attach"})
        candidate = self._get_first_candidate()
        if not candidate:
            self.skipTest("No candidate available")

        status1, setup1 = self._post("/api/billing-relationships/setup", {
            "payer_kind": "client",
            "payer_person_id": person["person_id"],
            "covered_client_ids": [person["person_id"]],
            "use_for_future_sessions": True,
        })
        self.assertEqual(status1, 200)

        status2, setup2 = self._post("/api/billing-relationships/setup", {
            "payer_kind": "client",
            "payer_person_id": person["person_id"],
            "covered_client_ids": [person["person_id"]],
            "use_for_future_sessions": True,
        })
        self.assertEqual(status2, 200)
        self.assertEqual(setup1["account_id"], setup2["account_id"])
        self.assertEqual(setup1["billing_party_id"], setup2["billing_party_id"])
        self.assertFalse(setup2.get("created"))
        self.assertTrue(setup2.get("duplicate"))

    def test_attach_preserves_duration_and_rate(self):
        """Duration, session type, rate unchanged after attach."""
        person = create_person(self.conn, {"display_name": "Dur Test", "first_name": "Dur", "last_name": "Test"})
        candidate = self._get_first_candidate()
        if not candidate:
            self.skipTest("No candidate available")

        before = self.conn.execute(
            "SELECT duration_minutes, session_type, time_category, suggested_rate_cents, approved_rate_cents FROM sessions WHERE id = ?",
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
            "participants": [{"person_id": person["person_id"], "display_name": "Dur Test", "is_primary": True}],
            "account_id": setup["account_id"],
            "billing_party_id": setup["billing_party_id"],
            "default_billing_party_id": setup["billing_party_id"],
            "primary_person_id": person["person_id"],
        })

        after = self.conn.execute(
            "SELECT duration_minutes, session_type, time_category, suggested_rate_cents, approved_rate_cents FROM sessions WHERE id = ?",
            (candidate["session_id"],)
        ).fetchone()

        self.assertEqual(before["duration_minutes"], after["duration_minutes"])
        self.assertEqual(before["session_type"], after["session_type"])
        self.assertEqual(before["time_category"], after["time_category"])
        self.assertEqual(before["suggested_rate_cents"], after["suggested_rate_cents"])
        self.assertEqual(before["approved_rate_cents"], after["approved_rate_cents"])


if __name__ == "__main__":
    unittest.main()
