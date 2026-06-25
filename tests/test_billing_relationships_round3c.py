"""Round 3C: Final integration hardening, usability polish, and merge readiness.

Tests cover:
- XSS: return links use escapeHtml for raw_calendar_title
- Dead code: openAddClientModal, payerDisplayOptions, recordBillingPartyDraft, renderModalSearchResults removed
- Dead code: ACCOUNT_TYPE_LABELS removed
- Terminology: "Default bill to" replaced with "Invoice recipient"
- Terminology: account_code not shown in editor
- Terminology: account_type not shown in org linked accounts table
- Unsaved changes: editor has dirty-form detection
- Unsaved changes: editor return link checks dirty flag
- No confirm(): org deactivation uses in-page confirmation
- No confirm(): billing party deactivation uses in-page confirmation
- Organization name field only shown for organization payers
- Backend: update_billing_relationship with organization_name in billing_delivery
- Backend: update_billing_relationship preserves historical rates
- Backend: update_billing_relationship with same covered clients is idempotent
- Backend: find_duplicate_billing_relationship with organization payer
- Backend: remove_account_member does not affect other accounts
- Backend: update_billing_relationship does not change account_name
- Backend: update_billing_relationship with null billing_delivery preserves existing
- Backend: update_billing_relationship with empty billing_delivery fields sets null
- Backend: deactivate then reactivate then update works
- Backend: update with person payer who is also a covered client
- JS: wizard has no confirm() calls
- JS: editor has no confirm() calls
- JS: editor has no prompt() calls
- JS: editor save button re-enables on error
- JS: editor duplicate box has Open existing and Cancel
- JS: editor has lifecycle confirm box
- JS: org deactivation confirm has Cancel and Deactivate buttons
- JS: billing party deactivation confirm has Cancel and Deactivate buttons
- JS: editor dirty confirm has Keep editing and Return without saving
- CSS: wizard-confirm-actions class exists
- No schema migration
- Round 3A features still work
- Round 3B features still work
"""
import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import connect, init_db, SCHEMA
from jordana_invoice.review_services import (
    create_billing_party,
    create_person,
    deactivate_account,
    find_duplicate_billing_relationship,
    get_account_record,
    reactivate_account,
    remove_account_member,
    setup_billing_relationship,
    update_billing_relationship,
)

JS_PATH = Path("app/jordana_invoice/static/review.js")
CSS_PATH = Path("app/jordana_invoice/static/review.css")


class TestRound3CXSS(unittest.TestCase):
    """XSS fixes: return links must escape user-provided calendar titles."""

    def setUp(self):
        self.js = JS_PATH.read_text()

    def test_return_link_uses_escape_html_for_calendar_title(self):
        start = self.js.index("function openAccountRecord")
        end = self.js.index("function openRecipientSearch", start)
        editor = self.js[start:end]
        self.assertIn("escapeHtml(state.detail?.session?.raw_calendar_title", editor)

    def test_return_link_uses_escape_html_for_session_date(self):
        start = self.js.index("function openAccountRecord")
        end = self.js.index("function openRecipientSearch", start)
        editor = self.js[start:end]
        self.assertIn("escapeHtml(state.detail?.session?.session_date", editor)

    def test_person_return_link_uses_escape_html_for_calendar_title(self):
        start = self.js.index("function openPersonRecord")
        end = self.js.index("function showBillingSetupMessage", start)
        person_func = self.js[start:end]
        self.assertIn("escapeHtml(state.detail?.session?.raw_calendar_title", person_func)

    def test_fmt_not_used_for_raw_calendar_title_in_editor(self):
        start = self.js.index("function openAccountRecord")
        end = self.js.index("function openRecipientSearch", start)
        editor = self.js[start:end]
        self.assertNotIn("fmt(state.detail?.session?.raw_calendar_title)", editor)


class TestRound3CDeadCodeRemoval(unittest.TestCase):
    """Verify dead code from old editor has been removed."""

    def setUp(self):
        self.js = JS_PATH.read_text()

    def test_openAddClientModal_removed(self):
        self.assertNotIn("function openAddClientModal", self.js)

    def test_payerDisplayOptions_removed(self):
        self.assertNotIn("function payerDisplayOptions", self.js)

    def test_recordBillingPartyDraft_removed(self):
        self.assertNotIn("function recordBillingPartyDraft", self.js)

    def test_renderModalSearchResults_removed(self):
        self.assertNotIn("function renderModalSearchResults", self.js)

    def test_ACCOUNT_TYPE_LABELS_removed(self):
        self.assertNotIn("ACCOUNT_TYPE_LABELS", self.js)

    def test_relationshipNameSuggestion_removed(self):
        self.assertNotIn("function relationshipNameSuggestion", self.js)


class TestRound3CTerminology(unittest.TestCase):
    """User-facing terminology: no backend-only labels in UI."""

    def setUp(self):
        self.js = JS_PATH.read_text()

    def test_editor_does_not_show_account_code(self):
        start = self.js.index("function openAccountRecord")
        end = self.js.index("function openRecipientSearch", start)
        editor = self.js[start:end]
        self.assertNotIn("data.account.account_code", editor)

    def test_directory_uses_invoice_recipient_not_default_bill_to(self):
        self.assertIn("Invoice recipient:", self.js)
        self.assertNotIn("Default bill to:", self.js)

    def test_org_linked_accounts_table_no_code_column(self):
        start = self.js.index("function openOrganizationRecord")
        end = self.js.index("function openPersonRecord", start)
        org_func = self.js[start:end]
        self.assertNotIn("account_code", org_func)

    def test_org_linked_accounts_table_no_type_column(self):
        start = self.js.index("function openOrganizationRecord")
        end = self.js.index("function openPersonRecord", start)
        org_func = self.js[start:end]
        self.assertNotIn("account_type", org_func)

    def test_organization_name_field_only_for_org_payers(self):
        start = self.js.index("function openAccountRecord")
        end = self.js.index("function openRecipientSearch", start)
        editor = self.js[start:end]
        self.assertIn('payerType === "organization"', editor)
        self.assertIn("Organization name", editor)

    def test_no_contact_name_label_for_person_payers(self):
        start = self.js.index("function openAccountRecord")
        end = self.js.index("function openRecipientSearch", start)
        editor = self.js[start:end]
        self.assertNotIn("Contact name", editor)


class TestRound3CUnsavedChanges(unittest.TestCase):
    """Editor has unsaved-changes detection."""

    def setUp(self):
        self.js = JS_PATH.read_text()
        start = self.js.index("async function openAccountRecord")
        end = self.js.index("function openRecipientSearch", start)
        self.editor = self.js[start:end]

    def test_editor_has_dirty_flag(self):
        self.assertIn("editorDirty", self.editor)

    def test_editor_has_markEditorDirty_function(self):
        self.assertIn("markEditorDirty", self.editor)

    def test_editor_dirty_confirm_has_keep_editing(self):
        self.assertIn("Keep editing", self.editor)

    def test_editor_dirty_confirm_has_return_without_saving(self):
        self.assertIn("Return without saving", self.editor)

    def test_editor_return_link_checks_dirty(self):
        self.assertIn("if (editorDirty)", self.editor)

    def test_editor_covered_remove_marks_dirty(self):
        self.assertIn("markEditorDirty()", self.editor)

    def test_editor_delivery_inputs_mark_dirty(self):
        self.assertIn("editAdminNotes", self.editor)
        self.assertIn("addEventListener(\"change\", markEditorDirty)", self.editor)


class TestRound3CNoBrowserConfirm(unittest.TestCase):
    """No browser confirm() in deactivation flows."""

    def setUp(self):
        self.js = JS_PATH.read_text()

    def test_org_deactivation_no_confirm(self):
        start = self.js.index("function openOrganizationRecord")
        end = self.js.index("function openPersonRecord", start)
        org_func = self.js[start:end]
        self.assertNotIn("confirm(", org_func)

    def test_org_deactivation_has_in_page_confirm(self):
        start = self.js.index("function openOrganizationRecord")
        end = self.js.index("function openPersonRecord", start)
        org_func = self.js[start:end]
        self.assertIn("orgDeactivateConfirm", org_func)
        self.assertIn("Deactivate", org_func)
        self.assertIn("Cancel", org_func)

    def test_billing_party_deactivation_no_confirm(self):
        start = self.js.index("function openPersonRecord")
        end = self.js.index("function showBillingSetupMessage", start)
        person_func = self.js[start:end]
        self.assertNotIn("confirm(", person_func)

    def test_billing_party_deactivation_has_in_page_confirm(self):
        start = self.js.index("function openPersonRecord")
        end = self.js.index("function showBillingSetupMessage", start)
        person_func = self.js[start:end]
        self.assertIn("billingDeactConfirm", person_func)

    def test_wizard_has_no_confirm(self):
        start = self.js.index("function openCreateRelationshipModal")
        end = len(self.js)
        wizard = self.js[start:end]
        self.assertNotIn("confirm(", wizard)

    def test_editor_save_has_no_confirm(self):
        start = self.js.index("async function saveBillingRelationship")
        end = self.js.index("async function loadPeople", start)
        save_func = self.js[start:end]
        self.assertNotIn("confirm(", save_func)


class TestRound3CBackendRuntime(unittest.TestCase):
    """Runtime backend tests for update_billing_relationship edge cases."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = connect(str(self.db_path))
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _make_person(self, name="Test Person"):
        return create_person(self.conn, {"display_name": name, "first_name": name.split()[0], "last_name": name.split()[-1] if len(name.split()) > 1 else ""})

    def _make_relationship(self, payer_name="Payer Client", covered_names=None):
        payer = self._make_person(payer_name)
        covered = [payer]
        for n in (covered_names or []):
            covered.append(self._make_person(n))
        result = setup_billing_relationship(self.conn, {
            "payer_kind": "client",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [p["person_id"] for p in covered],
        })
        return result, payer, covered

    def test_update_preserves_historical_rates(self):
        result, _, covered = self._make_relationship("Alice", ["Bob"])
        self.conn.execute(
            "INSERT INTO rate_rules (rate_rule_id, client_account_id, billing_session_type, duration_minutes, time_category, amount_cents, effective_from, active, created_at, updated_at) "
            "VALUES ('rr-1', ?, 'psychotherapy', 60, 'standard', 20000, '2026-01-01', 1, '2026-01-01', '2026-01-01')",
            (result["account_id"],)
        )
        self.conn.commit()
        update_billing_relationship(self.conn, result["account_id"], {
            "payer_kind": "client",
            "payer_person_id": covered[0]["person_id"],
            "covered_client_ids": [covered[0]["person_id"], covered[1]["person_id"]],
        })
        rates = self.conn.execute("SELECT * FROM rate_rules WHERE client_account_id = ?", (result["account_id"],)).fetchall()
        self.assertEqual(len(rates), 1)

    def test_update_idempotent_same_covered(self):
        result, _, covered = self._make_relationship("Alice", ["Bob"])
        updated = update_billing_relationship(self.conn, result["account_id"], {
            "payer_kind": "client",
            "payer_person_id": covered[0]["person_id"],
            "covered_client_ids": [covered[0]["person_id"], covered[1]["person_id"]],
        })
        member_ids = sorted(m["person_id"] for m in updated["members"])
        expected = sorted([covered[0]["person_id"], covered[1]["person_id"]])
        self.assertEqual(member_ids, expected)

    def test_find_duplicate_with_organization_payer(self):
        org = create_billing_party(self.conn, {
            "billing_party_type": "organization",
            "organization_name": "Test Org",
            "billing_name": "Test Org",
        })
        alice = self._make_person("Alice")
        result = setup_billing_relationship(self.conn, {
            "payer_kind": "organization",
            "organization_billing_party_id": org["billing_party_id"],
            "covered_client_ids": [alice["person_id"]],
        })
        dup = find_duplicate_billing_relationship(
            self.conn,
            "organization",
            None,
            org["billing_party_id"],
            [alice["person_id"]],
        )
        self.assertIsNotNone(dup)
        self.assertEqual(dup["account_id"], result["account_id"])

    def test_remove_member_does_not_affect_other_accounts(self):
        result1, _, covered1 = self._make_relationship("Alice", ["Bob"])
        result2, _, covered2 = self._make_relationship("Carol", ["Dave"])
        remove_account_member(self.conn, result1["account_id"], covered1[1]["person_id"])
        members2 = self.conn.execute("SELECT * FROM account_members WHERE account_id = ?", (result2["account_id"],)).fetchall()
        self.assertEqual(len(members2), 2)

    def test_update_does_not_change_account_name(self):
        result, _, covered = self._make_relationship("Alice", ["Bob"])
        original_name = result["account_name"]
        update_billing_relationship(self.conn, result["account_id"], {
            "payer_kind": "client",
            "payer_person_id": covered[0]["person_id"],
            "covered_client_ids": [covered[0]["person_id"], covered[1]["person_id"]],
        })
        record = get_account_record(self.conn, result["account_id"])
        self.assertEqual(record["account"]["account_name"], original_name)

    def test_update_null_billing_delivery_preserves_existing(self):
        result, _, covered = self._make_relationship("Alice", ["Bob"])
        update_billing_relationship(self.conn, result["account_id"], {
            "payer_kind": "client",
            "payer_person_id": covered[0]["person_id"],
            "covered_client_ids": [covered[0]["person_id"], covered[1]["person_id"]],
            "billing_delivery": {"billing_email": "first@test.com"},
        })
        update_billing_relationship(self.conn, result["account_id"], {
            "payer_kind": "client",
            "payer_person_id": covered[0]["person_id"],
            "covered_client_ids": [covered[0]["person_id"], covered[1]["person_id"]],
        })
        record = get_account_record(self.conn, result["account_id"])
        self.assertEqual(record["billing_party"]["billing_email"], "first@test.com")

    def test_update_empty_billing_delivery_sets_null(self):
        result, _, covered = self._make_relationship("Alice", ["Bob"])
        update_billing_relationship(self.conn, result["account_id"], {
            "payer_kind": "client",
            "payer_person_id": covered[0]["person_id"],
            "covered_client_ids": [covered[0]["person_id"], covered[1]["person_id"]],
            "billing_delivery": {"billing_email": "temp@test.com"},
        })
        update_billing_relationship(self.conn, result["account_id"], {
            "payer_kind": "client",
            "payer_person_id": covered[0]["person_id"],
            "covered_client_ids": [covered[0]["person_id"], covered[1]["person_id"]],
            "billing_delivery": {"billing_email": None},
        })
        record = get_account_record(self.conn, result["account_id"])
        self.assertFalse(record["billing_party"]["billing_email"])

    def test_deactivate_reactivate_then_update(self):
        result, _, covered = self._make_relationship("Alice", ["Bob"])
        deactivate_account(self.conn, result["account_id"])
        reactivate_account(self.conn, result["account_id"])
        updated = update_billing_relationship(self.conn, result["account_id"], {
            "payer_kind": "client",
            "payer_person_id": covered[0]["person_id"],
            "covered_client_ids": [covered[0]["person_id"], covered[1]["person_id"]],
        })
        self.assertTrue(updated["account"]["active"])

    def test_update_person_payer_also_covered_client(self):
        result, _, covered = self._make_relationship("Alice", ["Bob"])
        updated = update_billing_relationship(self.conn, result["account_id"], {
            "payer_kind": "person",
            "payer_person_id": covered[1]["person_id"],
            "covered_client_ids": [covered[0]["person_id"], covered[1]["person_id"]],
        })
        member_ids = [m["person_id"] for m in updated["members"]]
        self.assertIn(covered[1]["person_id"], member_ids)

    def test_update_organization_name_in_billing_delivery(self):
        result, _, covered = self._make_relationship("Alice", ["Bob"])
        org = create_billing_party(self.conn, {
            "billing_party_type": "organization",
            "organization_name": "Original Org",
            "billing_name": "Original Org",
        })
        updated = update_billing_relationship(self.conn, result["account_id"], {
            "payer_kind": "organization",
            "organization_billing_party_id": org["billing_party_id"],
            "covered_client_ids": [covered[0]["person_id"], covered[1]["person_id"]],
            "billing_delivery": {"organization_name": "Updated Org Name"},
        })
        self.assertEqual(updated["billing_party"]["organization_name"], "Updated Org Name")


class TestRound3CJSRuntimeChecks(unittest.TestCase):
    """JS static checks for runtime behavior patterns."""

    def setUp(self):
        self.js = JS_PATH.read_text()

    def test_editor_save_re_enables_button_on_error(self):
        start = self.js.index("async function saveBillingRelationship")
        end = self.js.index("async function loadPeople", start)
        save_func = self.js[start:end]
        self.assertIn("saveBtn.disabled = false", save_func)
        self.assertIn("saveBtn.textContent = \"Save changes\"", save_func)

    def test_editor_duplicate_box_has_open_existing(self):
        start = self.js.index("async function saveBillingRelationship")
        end = self.js.index("async function loadPeople", start)
        save_func = self.js[start:end]
        self.assertIn("editorOpenExisting", save_func)
        self.assertIn("Open existing relationship", save_func)

    def test_editor_duplicate_box_has_cancel(self):
        start = self.js.index("async function saveBillingRelationship")
        end = self.js.index("async function loadPeople", start)
        save_func = self.js[start:end]
        self.assertIn("editorCancelDup", save_func)

    def test_editor_has_lifecycle_confirm_box(self):
        start = self.js.index("async function openAccountRecord")
        end = self.js.index("function openRecipientSearch", start)
        editor = self.js[start:end]
        self.assertIn("lifecycleConfirmBox", editor)

    def test_org_deact_confirm_has_cancel_and_deactivate(self):
        start = self.js.index("function openOrganizationRecord")
        end = self.js.index("function openPersonRecord", start)
        org_func = self.js[start:end]
        self.assertIn("orgDeactNo", org_func)
        self.assertIn("orgDeactYes", org_func)

    def test_billing_deact_confirm_has_cancel_and_deactivate(self):
        start = self.js.index("function openPersonRecord")
        end = self.js.index("function showBillingSetupMessage", start)
        person_func = self.js[start:end]
        self.assertIn("billingDeactNo", person_func)
        self.assertIn("billingDeactYes", person_func)

    def test_editor_dirty_confirm_has_buttons(self):
        start = self.js.index("async function openAccountRecord")
        end = self.js.index("function openRecipientSearch", start)
        editor = self.js[start:end]
        self.assertIn("editorDirtyNo", editor)
        self.assertIn("editorDirtyYes", editor)


class TestRound3CCSS(unittest.TestCase):
    """CSS checks for confirm boxes."""

    def setUp(self):
        self.css = CSS_PATH.read_text()

    def test_wizard_confirm_actions_css_exists(self):
        self.assertIn(".wizard-confirm-actions", self.css)

    def test_lifecycle_confirm_box_css_exists(self):
        self.assertIn(".lifecycle-confirm-box", self.css)


class TestRound3CNoSchemaMigration(unittest.TestCase):
    """Verify no schema migration was introduced."""

    def test_no_alter_table_in_review_services(self):
        services = Path("app/jordana_invoice/review_services.py").read_text()
        self.assertNotIn("ALTER TABLE", services.upper())

    def test_schema_has_client_accounts(self):
        self.assertIsInstance(SCHEMA, str)
        self.assertIn("CREATE TABLE IF NOT EXISTS client_accounts", SCHEMA)


class TestRound3CRound3AStillWorks(unittest.TestCase):
    """Verify Round 3A features are not broken by Round 3C."""

    def setUp(self):
        self.js = JS_PATH.read_text()

    def test_deactivate_button_still_exists(self):
        self.assertIn("deactivateAccountBtn", self.js)

    def test_reactivate_button_still_exists(self):
        self.assertIn("reactivateAccountBtn", self.js)

    def test_lifecycle_confirm_still_exists(self):
        self.assertIn("showLifecycleConfirm", self.js)

    def test_status_filter_still_exists(self):
        self.assertIn("billingDirStatusFilter", self.js)


class TestRound3CRound3BStillWorks(unittest.TestCase):
    """Verify Round 3B features are not broken by Round 3C."""

    def setUp(self):
        self.js = JS_PATH.read_text()

    def test_editor_has_invoice_recipient_section(self):
        start = self.js.index("async function openAccountRecord")
        end = self.js.index("function openRecipientSearch", start)
        editor = self.js[start:end]
        self.assertIn("Invoice recipient", editor)

    def test_editor_has_pays_for_section(self):
        start = self.js.index("async function openAccountRecord")
        end = self.js.index("function openRecipientSearch", start)
        editor = self.js[start:end]
        self.assertIn("Pays for", editor)

    def test_editor_calls_update_endpoint(self):
        start = self.js.index("async function openAccountRecord")
        end = self.js.index("async function loadPeople", start)
        editor = self.js[start:end]
        self.assertIn("update-billing-relationship", editor)

    def test_editor_has_covered_search(self):
        self.assertIn("function openCoveredSearch", self.js)

    def test_editor_has_recipient_search(self):
        self.assertIn("function openRecipientSearch", self.js)


if __name__ == "__main__":
    unittest.main()
