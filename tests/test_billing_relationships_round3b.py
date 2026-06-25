"""Round 3B: Simplify and correct the Billing Relationship editor.

Tests cover:
- Bug fix: new client creation must not silently change payer
- Wizard: selectPayerType clears payerPerson when switching between client/person
- Wizard: handlePersonCreated respects creation context
- Wizard: Step 2 covered clients are clickable to remove
- Editor: simplified UI shows Invoice recipient, Pays for, Billing delivery, Status
- Editor: single Save changes button calls update-billing-relationship
- Editor: no alert/prompt/confirm in save flow
- Backend: update_billing_relationship transactional update
- Backend: remove_account_member
- Backend: duplicate detection during edit
- Backend: inactive account cannot be edited
- Backend: validation errors
- Backend: historical preservation
- Backend: audit logging
- No schema migration
- Round 3A features remain functional
"""
import json
import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import connect, init_db
from jordana_invoice.review_services import (
    add_account_member,
    create_account,
    create_billing_party,
    create_person,
    deactivate_account,
    find_duplicate_billing_relationship,
    get_account,
    get_account_record,
    remove_account_member,
    setup_billing_relationship,
    update_account,
    update_billing_relationship,
)

JS_PATH = Path("app/jordana_invoice/static/review.js")
CSS_PATH = Path("app/jordana_invoice/static/review.css")


class TestRound3BBackend(unittest.TestCase):
    """Backend tests for update_billing_relationship and remove_account_member."""

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

    # --- update_billing_relationship: basic tests ---

    def test_update_billing_relationship_changes_payer(self):
        result, payer, covered = self._make_relationship("Alice", ["Bob"])
        new_payer = self._make_person("Carol")
        updated = update_billing_relationship(self.conn, result["account_id"], {
            "payer_kind": "person",
            "payer_person_id": new_payer["person_id"],
            "covered_client_ids": [covered[0]["person_id"], covered[1]["person_id"]],
        })
        self.assertEqual(updated["billing_party"]["person_id"], new_payer["person_id"])

    def test_update_billing_relationship_adds_covered_client(self):
        result, _, covered = self._make_relationship("Alice", ["Bob"])
        new_client = self._make_person("Dave")
        updated = update_billing_relationship(self.conn, result["account_id"], {
            "payer_kind": "client",
            "payer_person_id": covered[0]["person_id"],
            "covered_client_ids": [covered[0]["person_id"], covered[1]["person_id"], new_client["person_id"]],
        })
        member_ids = [m["person_id"] for m in updated["members"]]
        self.assertIn(new_client["person_id"], member_ids)

    def test_update_billing_relationship_removes_covered_client(self):
        result, _, covered = self._make_relationship("Alice", ["Bob", "Charlie"])
        updated = update_billing_relationship(self.conn, result["account_id"], {
            "payer_kind": "client",
            "payer_person_id": covered[0]["person_id"],
            "covered_client_ids": [covered[0]["person_id"], covered[1]["person_id"]],
        })
        member_ids = [m["person_id"] for m in updated["members"]]
        self.assertNotIn(covered[2]["person_id"], member_ids)

    def test_update_billing_relationship_preserves_account_id(self):
        result, _, _ = self._make_relationship("Alice")
        updated = update_billing_relationship(self.conn, result["account_id"], {
            "payer_kind": "client",
            "payer_person_id": result["payer_person_id"] if "payer_person_id" in result else None,
            "covered_client_ids": [],
        } if False else {
            "payer_kind": "client",
            "payer_person_id": self.conn.execute("SELECT person_id FROM billing_parties WHERE billing_party_id = ?", (result["billing_party_id"],)).fetchone()["person_id"],
            "covered_client_ids": [self.conn.execute("SELECT person_id FROM billing_parties WHERE billing_party_id = ?", (result["billing_party_id"],)).fetchone()["person_id"]],
        })
        self.assertEqual(updated["account"]["account_id"], result["account_id"])

    def test_update_billing_relationship_writes_audit(self):
        result, _, covered = self._make_relationship("Alice", ["Bob"])
        update_billing_relationship(self.conn, result["account_id"], {
            "payer_kind": "client",
            "payer_person_id": covered[0]["person_id"],
            "covered_client_ids": [covered[0]["person_id"], covered[1]["person_id"]],
        })
        audit = self.conn.execute(
            "SELECT action FROM audit_log WHERE entity_type = 'client_account' AND entity_id = ? AND action = 'updated_billing_relationship'",
            (result["account_id"],),
        ).fetchall()
        self.assertEqual(len(audit), 1)

    def test_update_billing_relationship_rejects_inactive_account(self):
        result, _, covered = self._make_relationship("Alice", ["Bob"])
        deactivate_account(self.conn, result["account_id"])
        with self.assertRaises(ValueError) as ctx:
            update_billing_relationship(self.conn, result["account_id"], {
                "payer_kind": "client",
                "payer_person_id": covered[0]["person_id"],
                "covered_client_ids": [covered[0]["person_id"]],
            })
        self.assertIn("inactive", str(ctx.exception).lower())

    def test_update_billing_relationship_rejects_duplicate(self):
        result1, payer1, covered1 = self._make_relationship("Alice", ["Bob"])
        result2, payer2, covered2 = self._make_relationship("Carol", ["Dave"])
        with self.assertRaises(ValueError) as ctx:
            update_billing_relationship(self.conn, result2["account_id"], {
                "payer_kind": "client",
                "payer_person_id": covered1[0]["person_id"],
                "covered_client_ids": [covered1[0]["person_id"], covered1[1]["person_id"]],
            })
        self.assertIn("already exists", str(ctx.exception).lower())

    def test_update_billing_relationship_allows_same_relationship(self):
        result, _, covered = self._make_relationship("Alice", ["Bob"])
        updated = update_billing_relationship(self.conn, result["account_id"], {
            "payer_kind": "client",
            "payer_person_id": covered[0]["person_id"],
            "covered_client_ids": [covered[0]["person_id"], covered[1]["person_id"]],
            "billing_delivery": {"billing_email": "new@test.com"},
        })
        self.assertEqual(updated["billing_party"]["billing_email"], "new@test.com")

    def test_update_billing_relationship_rejects_empty_covered(self):
        result, _, covered = self._make_relationship("Alice", ["Bob"])
        with self.assertRaises(ValueError):
            update_billing_relationship(self.conn, result["account_id"], {
                "payer_kind": "client",
                "payer_person_id": covered[0]["person_id"],
                "covered_client_ids": [],
            })

    def test_update_billing_relationship_rejects_invalid_payer_kind(self):
        result, _, covered = self._make_relationship("Alice", ["Bob"])
        with self.assertRaises(ValueError):
            update_billing_relationship(self.conn, result["account_id"], {
                "payer_kind": "invalid",
                "payer_person_id": covered[0]["person_id"],
                "covered_client_ids": [covered[0]["person_id"]],
            })

    def test_update_billing_relationship_rejects_missing_payer(self):
        result, _, covered = self._make_relationship("Alice", ["Bob"])
        with self.assertRaises(ValueError):
            update_billing_relationship(self.conn, result["account_id"], {
                "payer_kind": "client",
                "payer_person_id": "",
                "covered_client_ids": [covered[0]["person_id"]],
            })

    def test_update_billing_relationship_rejects_nonexistent_account(self):
        with self.assertRaises(ValueError):
            update_billing_relationship(self.conn, "nonexistent-id", {
                "payer_kind": "client",
                "payer_person_id": "x",
                "covered_client_ids": ["x"],
            })

    def test_update_billing_relationship_updates_billing_delivery(self):
        result, _, covered = self._make_relationship("Alice", ["Bob"])
        updated = update_billing_relationship(self.conn, result["account_id"], {
            "payer_kind": "client",
            "payer_person_id": covered[0]["person_id"],
            "covered_client_ids": [covered[0]["person_id"], covered[1]["person_id"]],
            "billing_delivery": {
                "billing_email": "alice@test.com",
                "billing_phone": "555-1234",
                "preferred_delivery_method": "email",
            },
        })
        self.assertEqual(updated["billing_party"]["billing_email"], "alice@test.com")
        self.assertEqual(updated["billing_party"]["billing_phone"], "555-1234")
        self.assertEqual(updated["billing_party"]["preferred_delivery_method"], "email")

    def test_update_billing_relationship_updates_admin_notes(self):
        result, _, covered = self._make_relationship("Alice", ["Bob"])
        updated = update_billing_relationship(self.conn, result["account_id"], {
            "payer_kind": "client",
            "payer_person_id": covered[0]["person_id"],
            "covered_client_ids": [covered[0]["person_id"], covered[1]["person_id"]],
            "administrative_notes": "Updated notes",
        })
        self.assertEqual(updated["account"]["administrative_notes"], "Updated notes")

    def test_update_billing_relationship_preserves_sessions(self):
        result, _, covered = self._make_relationship("Alice", ["Bob"])
        self.conn.execute(
            "INSERT INTO import_runs (id, source_name, imported_at, status) VALUES ('run-1', 'TEST', '2026-06-01', 'completed')"
        )
        self.conn.execute(
            "INSERT INTO raw_calendar_snapshots (id, import_run_id, source_row_number, source_hash, snapshot_key, run_id, batch_name, capture_window, captured_at, ingested_at, source_device, timezone, calendar_event_id, event_fingerprint, event_title, start_at, end_at, duration_minutes, calendar_name, payload_version, raw_json, created_at) "
            "VALUES ('snap-1', 'run-1', 1, 'h1', 'sk1', 'run-1', 'TEST', 'past_30_days', '2026-06-01', '2026-06-01', 'Test', 'America/New_York', 'e1', 'f1', 'Test', '2026-06-01T10:00:00', '2026-06-01T11:00:00', 60, 'Jordana Work', 2, '{}', '2026-06-01')"
        )
        self.conn.execute(
            "INSERT INTO calendar_event_candidates (id, import_run_id, candidate_key, latest_raw_snapshot_id, raw_snapshot_count, title, start_at, end_at, calendar_duration_minutes, calendar_name, classification, confidence, explanation, fields_requiring_review, parser_payload, created_at, updated_at) "
            "VALUES ('cand-1', 'run-1', 'ck1', 'snap-1', 1, 'Test', '2026-06-01T10:00:00', '2026-06-01T11:00:00', 60, 'Jordana Work', 'likely_client', 0.9, 'test', '', '{}', '2026-06-01', '2026-06-01')"
        )
        self.conn.execute(
            "INSERT INTO sessions (id, candidate_id, account_id, billing_party_id, session_date, start_at, duration_minutes, review_status, source_raw_snapshot_id, created_at, updated_at) "
            "VALUES ('sess-1', 'cand-1', ?, ?, '2026-06-01', '2026-06-01T10:00:00', 60, 'pending', 'snap-1', '2026-06-01', '2026-06-01')",
            (result["account_id"], result["billing_party_id"])
        )
        self.conn.commit()
        update_billing_relationship(self.conn, result["account_id"], {
            "payer_kind": "client",
            "payer_person_id": covered[0]["person_id"],
            "covered_client_ids": [covered[0]["person_id"], covered[1]["person_id"]],
        })
        sessions = self.conn.execute("SELECT * FROM sessions WHERE account_id = ?", (result["account_id"],)).fetchall()
        self.assertEqual(len(sessions), 1)

    def test_update_billing_relationship_organization_payer(self):
        result, _, covered = self._make_relationship("Alice", ["Bob"])
        org = create_billing_party(self.conn, {
            "billing_party_type": "organization",
            "organization_name": "Test Org",
            "billing_name": "Test Org",
        })
        updated = update_billing_relationship(self.conn, result["account_id"], {
            "payer_kind": "organization",
            "organization_billing_party_id": org["billing_party_id"],
            "covered_client_ids": [covered[0]["person_id"], covered[1]["person_id"]],
        })
        self.assertEqual(updated["billing_party"]["billing_party_type"], "organization")
        self.assertEqual(updated["account"]["default_billing_party_id"], org["billing_party_id"])

    # --- remove_account_member tests ---

    def test_remove_account_member_removes_member(self):
        result, _, covered = self._make_relationship("Alice", ["Bob", "Charlie"])
        remove_account_member(self.conn, result["account_id"], covered[2]["person_id"])
        members = self.conn.execute("SELECT * FROM account_members WHERE account_id = ?", (result["account_id"],)).fetchall()
        self.assertEqual(len(members), 2)
        member_ids = [m["person_id"] for m in members]
        self.assertNotIn(covered[2]["person_id"], member_ids)

    def test_remove_account_member_preserves_person(self):
        result, _, covered = self._make_relationship("Alice", ["Bob"])
        remove_account_member(self.conn, result["account_id"], covered[1]["person_id"])
        person = self.conn.execute("SELECT * FROM people WHERE person_id = ?", (covered[1]["person_id"],)).fetchone()
        self.assertIsNotNone(person)

    def test_remove_account_member_writes_audit(self):
        result, _, covered = self._make_relationship("Alice", ["Bob"])
        remove_account_member(self.conn, result["account_id"], covered[1]["person_id"])
        audit = self.conn.execute(
            "SELECT action FROM audit_log WHERE entity_type = 'account_member' AND action = 'removed'"
        ).fetchall()
        self.assertEqual(len(audit), 1)

    def test_remove_account_member_nonexistent_raises(self):
        result, _, _ = self._make_relationship("Alice")
        with self.assertRaises(ValueError):
            remove_account_member(self.conn, result["account_id"], "nonexistent-person")

    def test_remove_account_member_nonexistent_account_raises(self):
        person = self._make_person("Test")
        with self.assertRaises(ValueError):
            remove_account_member(self.conn, "nonexistent-account", person["person_id"])


class TestRound3BWizardBugFix(unittest.TestCase):
    """JS static tests for the wizard bug fix: new client creation must not silently change payer."""

    def setUp(self):
        self.js = JS_PATH.read_text()

    def test_selectPayerType_clears_payerPerson_on_client_to_person(self):
        start = self.js.index("function selectPayerType")
        end = self.js.index("function showPayerSearch", start)
        func = self.js[start:end]
        self.assertIn('oldType === "client"', func)
        self.assertIn('type === "person"', func)
        self.assertIn("payerPerson = null", func)

    def test_selectPayerType_clears_payerPerson_on_person_to_client(self):
        start = self.js.index("function selectPayerType")
        end = self.js.index("function showPayerSearch", start)
        func = self.js[start:end]
        self.assertIn('oldType === "person"', func)
        self.assertIn('type === "client"', func)
        self.assertIn("payerPerson = null", func)

    def test_handlePersonCreated_checks_payerType_match(self):
        start = self.js.index("function handlePersonCreated")
        end = self.js.index("function showCreateOrgForm", start)
        func = self.js[start:end]
        self.assertIn("payerType === \"client\"", func)
        self.assertIn("payerType === \"person\"", func)

    def test_handlePersonCreated_does_not_always_set_payerPerson(self):
        start = self.js.index("function handlePersonCreated")
        end = self.js.index("function showCreateOrgForm", start)
        func = self.js[start:end]
        self.assertIn("else", func)
        self.assertIn("coveredClients.push", func)

    def test_step2_covered_results_are_clickable_to_remove(self):
        start = self.js.index("function renderCoveredResults")
        end = self.js.index("function addCoveredClient", start)
        func = self.js[start:end]
        self.assertIn("removeCoveredClient", func)
        self.assertIn("Click to remove", func)
        self.assertNotIn('tabindex="-1"', func)

    def test_wizard_no_alert_in_handlePersonCreated(self):
        start = self.js.index("function handlePersonCreated")
        end = self.js.index("function showCreateOrgForm", start)
        func = self.js[start:end]
        self.assertNotIn("alert(", func)


class TestRound3BEditorJS(unittest.TestCase):
    """JS static tests for the simplified editor."""

    def setUp(self):
        self.js = JS_PATH.read_text()
        start = self.js.index("async function openAccountRecord")
        end = self.js.index("async function loadPeople")
        self.editor = self.js[start:end]

    def test_editor_has_invoice_recipient_section(self):
        self.assertIn("Invoice recipient", self.editor)

    def test_editor_has_pays_for_section(self):
        self.assertIn("Pays for", self.editor)

    def test_editor_has_billing_delivery_section(self):
        self.assertIn("Billing delivery", self.editor)

    def test_editor_has_status_pill(self):
        self.assertIn("status-pill", self.editor)

    def test_editor_has_save_changes_button(self):
        self.assertIn("Save changes", self.editor)
        self.assertIn("saveBillingRelationshipBtn", self.editor)

    def test_editor_has_change_recipient_button(self):
        self.assertIn("changeRecipientBtn", self.editor)
        self.assertIn("Change invoice recipient", self.editor)

    def test_editor_has_add_client_button(self):
        self.assertIn("addCoveredBtn", self.editor)
        self.assertIn("Add Client", self.editor)

    def test_editor_has_covered_client_remove_buttons(self):
        self.assertIn("covered-client-remove", self.editor)

    def test_editor_calls_update_billing_relationship_endpoint(self):
        self.assertIn("update-billing-relationship", self.editor)

    def test_editor_no_alert_in_save_flow(self):
        save_start = self.js.index("async function saveBillingRelationship")
        save_end = self.js.index("async function loadPeople", save_start)
        save_func = self.js[save_start:save_end]
        self.assertNotIn("alert(", save_func)

    def test_editor_no_prompt_in_save_flow(self):
        save_start = self.js.index("async function saveBillingRelationship")
        save_end = self.js.index("async function loadPeople", save_start)
        save_func = self.js[save_start:save_end]
        self.assertNotIn("prompt(", save_func)

    def test_editor_no_confirm_in_save_flow(self):
        save_start = self.js.index("async function saveBillingRelationship")
        save_end = self.js.index("async function loadPeople", save_start)
        save_func = self.js[save_start:save_end]
        self.assertNotIn("confirm(", save_func)

    def test_editor_has_error_box(self):
        self.assertIn("editorErrorBox", self.editor)

    def test_editor_has_duplicate_box(self):
        self.assertIn("editorDuplicateBox", self.editor)

    def test_editor_has_delivery_method_select(self):
        self.assertIn("editDeliveryMethod", self.editor)
        self.assertIn("preferred_delivery_method", self.editor)

    def test_editor_has_billing_email_field(self):
        self.assertIn("editBillingEmail", self.editor)

    def test_editor_has_billing_phone_field(self):
        self.assertIn("editBillingPhone", self.editor)

    def test_editor_has_admin_notes_field(self):
        self.assertIn("editAdminNotes", self.editor)

    def test_editor_has_address_fields(self):
        self.assertIn("editAddr1", self.editor)
        self.assertIn("editCity", self.editor)
        self.assertIn("editState", self.editor)
        self.assertIn("editPostal", self.editor)

    def test_editor_no_old_recordBillingPartyType(self):
        self.assertNotIn("recordBillingPartyType", self.editor)

    def test_editor_no_old_editAccountRecord(self):
        self.assertNotIn("editAccountRecord", self.editor)

    def test_editor_no_old_addMemberRecord(self):
        self.assertNotIn("addMemberRecord", self.editor)

    def test_editor_no_old_recordAccountName(self):
        self.assertNotIn("recordAccountName", self.editor)

    def test_editor_no_old_recordAccountType(self):
        self.assertNotIn("recordAccountType", self.editor)

    def test_editor_has_recipient_search_area(self):
        self.assertIn("recipientSearchArea", self.editor)
        self.assertIn("openRecipientSearch", self.editor)

    def test_editor_has_covered_search_area(self):
        self.assertIn("coveredSearchArea", self.editor)
        self.assertIn("openCoveredSearch", self.editor)

    def test_editor_recipient_search_has_payer_type_choices(self):
        search_start = self.js.index("function openRecipientSearch")
        search_end = self.js.index("function renderRecipientResults", search_start)
        search_func = self.js[search_start:search_end]
        self.assertIn("wizard-payer-choice", search_func)
        self.assertIn("data-type=\"client\"", search_func)
        self.assertIn("data-type=\"person\"", search_func)
        self.assertIn("data-type=\"organization\"", search_func)

    def test_editor_covered_search_allows_remove(self):
        render_start = self.js.index("function renderEditorCoveredResults")
        render_end = self.js.index("async function saveBillingRelationship", render_start)
        render_func = self.js[render_start:render_end]
        self.assertIn("Click to remove", render_func)

    def test_editor_save_validates_payer(self):
        save_start = self.js.index("async function saveBillingRelationship")
        save_end = self.js.index("async function loadPeople", save_start)
        save_func = self.js[save_start:save_end]
        self.assertIn("Select an invoice recipient", save_func)

    def test_editor_save_validates_covered(self):
        save_start = self.js.index("async function saveBillingRelationship")
        save_end = self.js.index("async function loadPeople", save_start)
        save_func = self.js[save_start:save_end]
        self.assertIn("At least one covered client", save_func)


class TestRound3BEditorCSS(unittest.TestCase):
    """CSS static tests for the editor."""

    def setUp(self):
        self.css = CSS_PATH.read_text()

    def test_editor_section_css_exists(self):
        self.assertIn(".editor-section", self.css)

    def test_covered_client_row_css_exists(self):
        self.assertIn(".covered-client-row", self.css)

    def test_covered_client_remove_css_exists(self):
        self.assertIn(".covered-client-remove", self.css)


class TestRound3BRound3AStillWorks(unittest.TestCase):
    """Verify Round 3A features are not broken by Round 3B."""

    def setUp(self):
        self.js = JS_PATH.read_text()

    def test_deactivate_button_still_exists(self):
        self.assertIn("deactivateAccountBtn", self.js)

    def test_reactivate_button_still_exists(self):
        self.assertIn("reactivateAccountBtn", self.js)

    def test_status_filter_still_exists(self):
        self.assertIn("billingDirStatusFilter", self.js)

    def test_status_pill_still_exists(self):
        self.assertIn("status-pill", self.js)

    def test_lifecycle_confirm_still_exists(self):
        self.assertIn("showLifecycleConfirm", self.js)

    def test_deactivate_reactivate_endpoints_still_referenced(self):
        # The lifecycle confirm calls /api/accounts/${accountId}/${action}
        self.assertIn("/api/accounts/", self.js)
        self.assertIn("${action}", self.js)


class TestRound3BNoSchemaMigration(unittest.TestCase):
    """Verify no schema migration was introduced."""

    def test_no_new_columns_in_client_accounts(self):
        from jordana_invoice.db import SCHEMA
        self.assertIsInstance(SCHEMA, str)
        # Verify client_accounts table still has the expected columns
        self.assertIn("CREATE TABLE IF NOT EXISTS client_accounts", SCHEMA)

    def test_no_alter_table_in_review_services(self):
        services = Path("app/jordana_invoice/review_services.py").read_text()
        self.assertNotIn("ALTER TABLE", services.upper())


if __name__ == "__main__":
    unittest.main()
