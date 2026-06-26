"""Round 3A: Deactivate / Reactivate Billing Relationships.

Tests cover:
- Backend deactivate/reactivate with idempotency and audit
- Directory filtering by active/inactive/all
- Suggestion and duplicate exclusion for inactive relationships
- Historical preservation (sessions, invoices, payments, rates, members)
- Frontend JS behavior (in-page confirmation, no browser confirm/alert)
- No permanent deletion, no schema migration
"""
import json
import re
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
    add_account_member,
    create_account,
    create_billing_party,
    create_person,
    deactivate_account,
    reactivate_account,
    find_duplicate_billing_relationship,
    find_equivalent_account,
    get_account,
    get_account_record,
    list_billing_relationship_records,
    setup_billing_relationship,
    update_account,
)
from jordana_invoice.importer import import_csv

JS_PATH = Path("app/jordana_invoice/static/review.js")
HTML_PATH = Path("app/jordana_invoice/static/review.html")
CSS_PATH = Path("app/jordana_invoice/static/review.css")


class TestRound3ABackend(unittest.TestCase):
    """Backend tests for deactivate/reactivate."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = connect(str(self.db_path))
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _make_account(self, name="Test Client"):
        person = create_person(self.conn, {"display_name": name, "first_name": "Test", "last_name": "Client"})
        account = create_account(self.conn, name, "individual")
        add_account_member(self.conn, account["account_id"], person["person_id"], "primary", True)
        bp = create_billing_party(self.conn, {
            "billing_party_type": "person",
            "person_id": person["person_id"],
            "billing_name": name,
        })
        update_account(self.conn, account["account_id"], {"default_billing_party_id": bp["billing_party_id"]})
        return account, person, bp

    def _make_session(self, account_id, billing_party_id, suffix="1"):
        """Insert a minimal session with all required FK fields."""
        self.conn.execute(
            "INSERT INTO import_runs (id, source_name, imported_at, status) VALUES ('run-X', 'TEST', '2026-06-01', 'completed')"
        )
        self.conn.execute(
            "INSERT INTO raw_calendar_snapshots (id, import_run_id, source_row_number, source_hash, snapshot_key, run_id, batch_name, capture_window, captured_at, ingested_at, source_device, timezone, calendar_event_id, event_fingerprint, event_title, start_at, end_at, duration_minutes, calendar_name, payload_version, raw_json, created_at) "
            "VALUES (?, 'run-X', 1, 'hash-' || ?, 'snap-key-' || ?, 'run-X', 'TEST', 'past_30_days', '2026-06-01', '2026-06-01', 'Test', 'America/New_York', 'evt-' || ?, 'fp-' || ?, 'Test 10', '2026-06-01T10:00:00', '2026-06-01T11:00:00', 60, 'Jordana Work', 2, '{}', '2026-06-01')",
            (f'snap-{suffix}', suffix, suffix, suffix, suffix),
        )
        self.conn.execute(
            "INSERT INTO calendar_event_candidates (id, import_run_id, candidate_key, latest_raw_snapshot_id, raw_snapshot_count, title, start_at, end_at, calendar_duration_minutes, calendar_name, classification, confidence, explanation, fields_requiring_review, parser_payload, created_at, updated_at) "
            "VALUES (?, 'run-X', 'cand-key-' || ?, ?, 1, 'Test 10', '2026-06-01T10:00:00', '2026-06-01T11:00:00', 60, 'Jordana Work', 'likely_client', 0.9, 'test', '', '{}', '2026-06-01', '2026-06-01')",
            (f'cand-{suffix}', suffix, f'snap-{suffix}'),
        )
        self.conn.execute(
            "INSERT INTO sessions (id, candidate_id, account_id, billing_party_id, session_date, start_at, duration_minutes, review_status, source_raw_snapshot_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, '2026-06-01', '2026-06-01T10:00:00', 60, 'pending', ?, '2026-06-01', '2026-06-01')",
            (f'sess-{suffix}', f'cand-{suffix}', account_id, billing_party_id, f'snap-{suffix}'),
        )
        self.conn.commit()
        return f'sess-{suffix}'

    # 1. Active relationship can be deactivated
    def test_active_can_be_deactivated(self):
        account, _, _ = self._make_account()
        result = deactivate_account(self.conn, account["account_id"])
        self.assertEqual(result["active"], 0)

    # 2. Inactive relationship can be reactivated
    def test_inactive_can_be_reactivated(self):
        account, _, _ = self._make_account()
        deactivate_account(self.conn, account["account_id"])
        result = reactivate_account(self.conn, account["account_id"])
        self.assertEqual(result["active"], 1)

    # 3. Deactivation is idempotent
    def test_deactivation_idempotent(self):
        account, _, _ = self._make_account()
        deactivate_account(self.conn, account["account_id"])
        result = deactivate_account(self.conn, account["account_id"])
        self.assertEqual(result["active"], 0)

    # 4. Reactivation is idempotent
    def test_reactivation_idempotent(self):
        account, _, _ = self._make_account()
        deactivate_account(self.conn, account["account_id"])
        reactivate_account(self.conn, account["account_id"])
        result = reactivate_account(self.conn, account["account_id"])
        self.assertEqual(result["active"], 1)

    # 5. Audit entry written on actual deactivation
    def test_audit_on_deactivation(self):
        account, _, _ = self._make_account()
        deactivate_account(self.conn, account["account_id"])
        audits = self.conn.execute(
            "SELECT * FROM audit_log WHERE entity_type = 'client_account' AND entity_id = ? AND action = 'deactivated'",
            (account["account_id"],),
        ).fetchall()
        self.assertEqual(len(audits), 1)

    # 6. Audit entry written on actual reactivation
    def test_audit_on_reactivation(self):
        account, _, _ = self._make_account()
        deactivate_account(self.conn, account["account_id"])
        reactivate_account(self.conn, account["account_id"])
        audits = self.conn.execute(
            "SELECT * FROM audit_log WHERE entity_type = 'client_account' AND entity_id = ? AND action = 'reactivated'",
            (account["account_id"],),
        ).fetchall()
        self.assertEqual(len(audits), 1)

    # 7. No duplicate audit entry when state is unchanged
    def test_no_duplicate_audit_on_idempotent_deactivate(self):
        account, _, _ = self._make_account()
        deactivate_account(self.conn, account["account_id"])
        deactivate_account(self.conn, account["account_id"])
        audits = self.conn.execute(
            "SELECT * FROM audit_log WHERE entity_type = 'client_account' AND entity_id = ? AND action = 'deactivated'",
            (account["account_id"],),
        ).fetchall()
        self.assertEqual(len(audits), 1)

    def test_no_duplicate_audit_on_idempotent_reactivate(self):
        account, _, _ = self._make_account()
        deactivate_account(self.conn, account["account_id"])
        reactivate_account(self.conn, account["account_id"])
        reactivate_account(self.conn, account["account_id"])
        audits = self.conn.execute(
            "SELECT * FROM audit_log WHERE entity_type = 'client_account' AND entity_id = ? AND action = 'reactivated'",
            (account["account_id"],),
        ).fetchall()
        self.assertEqual(len(audits), 1)

    # 8. Missing account returns error
    def test_deactivate_missing_account_raises(self):
        with self.assertRaises(ValueError):
            deactivate_account(self.conn, "nonexistent-id")

    def test_reactivate_missing_account_raises(self):
        with self.assertRaises(ValueError):
            reactivate_account(self.conn, "nonexistent-id")

    # 9. Deactivation preserves account UUID and code
    def test_deactivation_preserves_uuid_and_code(self):
        account, _, _ = self._make_account()
        original_id = account["account_id"]
        original_code = account["account_code"]
        result = deactivate_account(self.conn, account["account_id"])
        self.assertEqual(result["account_id"], original_id)
        self.assertEqual(result["account_code"], original_code)

    # 10. Deactivation preserves members
    def test_deactivation_preserves_members(self):
        account, person, _ = self._make_account()
        deactivate_account(self.conn, account["account_id"])
        members = self.conn.execute(
            "SELECT * FROM account_members WHERE account_id = ?", (account["account_id"],)
        ).fetchall()
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0]["person_id"], person["person_id"])

    # 11. Deactivation preserves billing parties
    def test_deactivation_preserves_billing_party(self):
        account, _, bp = self._make_account()
        deactivate_account(self.conn, account["account_id"])
        record = get_account_record(self.conn, account["account_id"])
        self.assertIsNotNone(record["billing_party"])
        self.assertEqual(record["billing_party"]["billing_party_id"], bp["billing_party_id"])

    # 12. Inactive relationship excluded from duplicate matching
    def test_inactive_excluded_from_duplicate_matching(self):
        account, person, _ = self._make_account()
        deactivate_account(self.conn, account["account_id"])
        result = find_duplicate_billing_relationship(
            self.conn, "client", person["person_id"], None, [person["person_id"]]
        )
        self.assertIsNone(result)

    # 13. Inactive relationship excluded from equivalent account lookup
    def test_inactive_excluded_from_equivalent_account(self):
        account, person, _ = self._make_account()
        deactivate_account(self.conn, account["account_id"])
        result = find_equivalent_account(self.conn, person["person_id"], "individual")
        self.assertIsNone(result)

    # 14. New active relationship can be created with same payer/client set after deactivation
    def test_new_active_after_deactivation(self):
        account, person, _ = self._make_account()
        deactivate_account(self.conn, account["account_id"])
        result = setup_billing_relationship(
            self.conn,
            {
                "payer_kind": "client",
                "payer_person_id": person["person_id"],
                "covered_client_ids": [person["person_id"]],
                "use_for_future_sessions": True,
            },
        )
        self.assertTrue(result["created"])
        self.assertNotEqual(result["account_id"], account["account_id"])

    # 15. Audit details don't contain sensitive billing information
    def test_audit_details_no_sensitive_info(self):
        account, _, _ = self._make_account()
        deactivate_account(self.conn, account["account_id"])
        audit = self.conn.execute(
            "SELECT details FROM audit_log WHERE entity_type = 'client_account' AND entity_id = ? AND action = 'deactivated'",
            (account["account_id"],),
        ).fetchone()
        details = json.loads(audit["details"])
        self.assertNotIn("billing_email", details)
        self.assertNotIn("billing_phone", details)
        self.assertNotIn("billing_address", details)
        self.assertIn("account_name", details)

    # 16. Deactivation does not modify sessions
    def test_deactivation_preserves_sessions(self):
        account, _, _ = self._make_account()
        session_id = self._make_session(account["account_id"], account["default_billing_party_id"], "1")
        deactivate_account(self.conn, account["account_id"])
        session = self.conn.execute("SELECT account_id, billing_party_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        self.assertEqual(session["account_id"], account["account_id"])

    # 17. Deactivation does not modify invoices
    def test_deactivation_preserves_invoices(self):
        account, _, bp = self._make_account()
        self.conn.execute(
            "INSERT INTO invoices (invoice_id, invoice_number, status, invoice_date, billing_period_start, billing_period_end, subtotal_cents, adjustment_cents, total_cents, bill_to_party_id, created_at, updated_at) "
            "VALUES ('inv-1', 'INV-001', 'draft', '2026-06-01', '2026-06-01', '2026-06-30', 10000, 0, 10000, ?, '2026-06-01', '2026-06-01')",
            (bp["billing_party_id"],),
        )
        self.conn.commit()
        deactivate_account(self.conn, account["account_id"])
        inv = self.conn.execute("SELECT * FROM invoices WHERE invoice_id = 'inv-1'").fetchone()
        self.assertIsNotNone(inv)
        self.assertEqual(inv["bill_to_party_id"], bp["billing_party_id"])

    # 18. Deactivation does not modify payment status on sessions
    def test_deactivation_preserves_payment_status(self):
        account, _, _ = self._make_account()
        session_id = self._make_session(account["account_id"], account["default_billing_party_id"], "2")
        self.conn.execute("UPDATE sessions SET payment_status = 'paid' WHERE id = ?", (session_id,))
        self.conn.commit()
        deactivate_account(self.conn, account["account_id"])
        session = self.conn.execute("SELECT payment_status FROM sessions WHERE id = ?", (session_id,)).fetchone()
        self.assertEqual(session["payment_status"], 'paid')

    # 19. Deactivation does not modify rates
    def test_deactivation_preserves_rates(self):
        account, _, _ = self._make_account()
        self.conn.execute(
            "INSERT INTO rate_rules (rate_rule_id, client_account_id, amount_cents, active, priority, effective_from, created_at, updated_at) "
            "VALUES ('rr-1', ?, 15000, 1, 0, '2026-01-01', '2026-01-01', '2026-01-01')",
            (account["account_id"],),
        )
        self.conn.commit()
        deactivate_account(self.conn, account["account_id"])
        rate = self.conn.execute("SELECT * FROM rate_rules WHERE rate_rule_id = 'rr-1'").fetchone()
        self.assertIsNotNone(rate)
        self.assertEqual(rate["amount_cents"], 15000)

    # 20. Reactivation restores active state
    def test_reactivation_restores_active(self):
        account, _, _ = self._make_account()
        deactivate_account(self.conn, account["account_id"])
        result = reactivate_account(self.conn, account["account_id"])
        self.assertEqual(result["active"], 1)
        fresh = get_account(self.conn, account["account_id"])
        self.assertEqual(fresh["active"], 1)


class TestRound3ADirectory(unittest.TestCase):
    """Directory filtering tests."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = connect(str(self.db_path))
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _make_account(self, name):
        person = create_person(self.conn, {"display_name": name, "first_name": "A", "last_name": "B"})
        account = create_account(self.conn, name, "individual")
        add_account_member(self.conn, account["account_id"], person["person_id"], "primary", True)
        bp = create_billing_party(self.conn, {
            "billing_party_type": "person",
            "person_id": person["person_id"],
            "billing_name": name,
        })
        update_account(self.conn, account["account_id"], {"default_billing_party_id": bp["billing_party_id"]})
        return account

    def _make_org_account(self, org_name):
        account = create_account(self.conn, org_name, "organization")
        bp = create_billing_party(self.conn, {
            "billing_party_type": "organization",
            "organization_name": org_name,
            "billing_name": org_name,
        })
        update_account(self.conn, account["account_id"], {"default_billing_party_id": bp["billing_party_id"]})
        return account

    # 8. Active directory excludes inactive records
    def test_active_filter_excludes_inactive(self):
        active_acct = self._make_account("Active One")
        inactive_acct = self._make_account("Inactive One")
        deactivate_account(self.conn, inactive_acct["account_id"])
        records = list_billing_relationship_records(self.conn)
        active_records = [r for r in records if r["active"]]
        inactive_records = [r for r in records if not r["active"]]
        self.assertTrue(any(r["account_id"] == active_acct["account_id"] for r in active_records))
        self.assertFalse(any(r["account_id"] == active_acct["account_id"] for r in inactive_records))
        self.assertTrue(any(r["account_id"] == inactive_acct["account_id"] for r in inactive_records))

    # 9. Inactive directory shows only inactive records
    def test_inactive_filter_shows_only_inactive(self):
        active_acct = self._make_account("Active Two")
        inactive_acct = self._make_account("Inactive Two")
        deactivate_account(self.conn, inactive_acct["account_id"])
        records = list_billing_relationship_records(self.conn)
        inactive_records = [r for r in records if not r["active"]]
        self.assertTrue(all(not r["active"] for r in inactive_records))
        self.assertTrue(any(r["account_id"] == inactive_acct["account_id"] for r in inactive_records))
        self.assertFalse(any(r["account_id"] == active_acct["account_id"] for r in inactive_records))

    # 10. All directory shows both
    def test_all_filter_shows_both(self):
        active_acct = self._make_account("Active Three")
        inactive_acct = self._make_account("Inactive Three")
        deactivate_account(self.conn, inactive_acct["account_id"])
        records = list_billing_relationship_records(self.conn)
        active_ids = {r["account_id"] for r in records if r["active"]}
        inactive_ids = {r["account_id"] for r in records if not r["active"]}
        self.assertIn(active_acct["account_id"], active_ids)
        self.assertIn(inactive_acct["account_id"], inactive_ids)

    # 11. Inactive record can still be opened
    def test_inactive_record_can_be_opened(self):
        acct = self._make_account("Openable Inactive")
        deactivate_account(self.conn, acct["account_id"])
        record = get_account_record(self.conn, acct["account_id"])
        self.assertIsNotNone(record)
        self.assertEqual(record["account"]["active"], 0)

    # 12. Status field is present in directory records
    def test_directory_records_have_active_field(self):
        self._make_account("Status Test")
        records = list_billing_relationship_records(self.conn)
        self.assertTrue(all("active" in r for r in records))


class TestRound3AJSBehavior(unittest.TestCase):
    """JS behavior tests for deactivate/reactivate UI."""

    def setUp(self):
        self.js = JS_PATH.read_text()
        self.html = HTML_PATH.read_text()
        self.css = CSS_PATH.read_text()

    def _extract_function(self, name):
        pattern = rf"(?:async )?function {name}\b"
        match = re.search(pattern, self.js)
        if not match:
            return None
        start = match.start()
        brace_count = 0
        found_open = False
        for i in range(match.end(), len(self.js)):
            if self.js[i] == '{':
                brace_count += 1
                found_open = True
            elif self.js[i] == '}':
                brace_count -= 1
                if found_open and brace_count == 0:
                    return self.js[start:i + 1]
        return None

    # 22. Deactivate confirmation uses in-page UI
    def test_deactivate_uses_in_page_confirmation(self):
        fn = self._extract_function("showLifecycleConfirm")
        self.assertIsNotNone(fn)
        self.assertIn("lifecycleConfirmBox", fn)
        self.assertNotIn("confirm(", fn)
        self.assertNotIn("alert(", fn)

    # 23. Reactivate confirmation uses in-page UI
    def test_reactivate_uses_in_page_confirmation(self):
        fn = self._extract_function("showLifecycleConfirm")
        self.assertIsNotNone(fn)
        self.assertIn("Reactivate this billing relationship?", fn)
        self.assertNotIn("confirm(", fn)

    # 24. Cancel changes nothing — cancel button exists and hides the box
    def test_cancel_hides_confirmation(self):
        fn = self._extract_function("showLifecycleConfirm")
        self.assertIn("lifecycleCancelBtn", fn)
        self.assertIn("closeConfirm", fn)
        self.assertIn("box.hidden = true", fn)

    # 25. API failure stays inline
    def test_api_failure_inline(self):
        fn = self._extract_function("showLifecycleConfirm")
        self.assertIn("lifecycleError", fn)
        self.assertIn("errorDisplay.textContent", fn)

    # 26. Double-click does not send duplicate mutations
    def test_double_click_prevented(self):
        fn = self._extract_function("showLifecycleConfirm")
        self.assertIn("inFlight", fn)
        self.assertIn("if (inFlight) return;", fn)

    # 27. Buttons disabled while request is active
    def test_buttons_disabled_during_request(self):
        fn = self._extract_function("showLifecycleConfirm")
        self.assertIn("confirmBtn.disabled = true", fn)
        self.assertIn("cancelBtn.disabled = true", fn)

    # 28. Spinner text shown during deactivation
    def test_deactivating_spinner(self):
        fn = self._extract_function("showLifecycleConfirm")
        self.assertIn("Deactivating…", fn)

    # 29. Spinner text shown during reactivation
    def test_reactivating_spinner(self):
        fn = self._extract_function("showLifecycleConfirm")
        self.assertIn("Reactivating…", fn)

    # 30. Escape cancels safely
    def test_escape_cancels(self):
        fn = self._extract_function("showLifecycleConfirm")
        self.assertIn("Escape", fn)
        self.assertIn("closeConfirm", fn)

    # 31. Focus returns to initiating button
    def test_focus_returns_to_trigger(self):
        fn = self._extract_function("showLifecycleConfirm")
        self.assertIn("triggerBtn.focus()", fn)

    # 32. Deactivate button exists in account record
    def test_deactivate_button_in_record(self):
        # openAccountRecord is too long for _extract_function; search in full JS
        self.assertIn("deactivateAccountBtn", self.js)
        self.assertIn("Deactivate Billing Relationship", self.js)

    # 33. Reactivate button exists for inactive accounts
    def test_reactivate_button_in_record(self):
        self.assertIn("reactivateAccountBtn", self.js)
        self.assertIn("Reactivate Billing Relationship", self.js)

    # 34. Status pill shown in account record
    def test_status_pill_in_record(self):
        self.assertIn("status-pill", self.js)
        self.assertIn("statusPill", self.js)

    # 35. Status filter dropdown exists in HTML
    def test_status_filter_in_html(self):
        self.assertIn("billingDirStatusFilter", self.html)
        self.assertIn('value="active"', self.html)
        self.assertIn('value="inactive"', self.html)

    # 36. Status filter defaults to active
    def test_status_filter_defaults_active(self):
        self.assertIn('selected', self.html)
        # Check that the active option is selected
        active_option = re.search(r'<option value="active"[^>]*selected', self.html)
        self.assertIsNotNone(active_option)

    # 37. Status filter event listener wired
    def test_status_filter_event_wired(self):
        self.assertIn("billingDirStatusFilter", self.js)
        self.assertIn("billingDirState.statusFilter", self.js)

    # 38. renderBillingDirRows applies status filter
    def test_render_applies_status_filter(self):
        fn = self._extract_function("renderBillingDirRows")
        self.assertIsNotNone(fn)
        self.assertIn("statusFilter", fn)
        self.assertIn('statusFilter === "active"', fn)
        self.assertIn('statusFilter === "inactive"', fn)

    # 39. No permanent delete action is introduced
    def test_no_delete_action(self):
        self.assertNotIn("deleteAccount", self.js)
        self.assertNotIn("Delete Billing Relationship", self.js)
        # Check no DELETE method for accounts
        delete_pattern = re.findall(r'method:\s*["\']DELETE["\']', self.js)
        self.assertEqual(len(delete_pattern), 0)

    # 40. API calls use POST for deactivate/reactivate
    def test_api_uses_post(self):
        fn = self._extract_function("showLifecycleConfirm")
        self.assertIn("POST", fn)
        self.assertIn("/api/accounts/", fn)
        # action is interpolated as ${action} — check the template literal
        self.assertIn("${action}", fn)

    # 41. User-controlled values escaped in confirmation
    def test_confirmation_escapes_values(self):
        fn = self._extract_function("showLifecycleConfirm")
        self.assertIn("escapeHtml", fn)

    # 42. CSS for lifecycle confirmation exists
    def test_lifecycle_css_exists(self):
        self.assertIn("lifecycle-confirm-box", self.css)
        self.assertIn("lifecycle-confirm-content", self.css)
        self.assertIn("status-pill", self.css)

    # 43. Danger button CSS exists
    def test_danger_button_css(self):
        self.assertIn(".danger", self.css)

    # 44. Explanation text matches spec
    def test_deactivate_explanation_text(self):
        fn = self._extract_function("showLifecycleConfirm")
        self.assertIn("It will no longer appear in active searches or be suggested for future sessions.", fn)
        self.assertIn("Existing sessions, invoices, rates, payments, and history will remain unchanged.", fn)

    # 45. Round 2 wizard continues to work — wizard function still exists
    def test_wizard_still_exists(self):
        fn = self._extract_function("openCreateRelationshipModal")
        self.assertIsNotNone(fn)

    # 46. No schema migration introduced — check no ALTER TABLE in JS
    def test_no_migration_in_js(self):
        self.assertNotIn("ALTER TABLE", self.js)


class TestRound3AApi(unittest.TestCase):
    """API integration tests for deactivate/reactivate via HTTP."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = connect(str(self.db_path))
        init_db(self.conn)
        csv_path = Path("data/samples/sanitized_demo_calendar_snapshots.csv")
        if csv_path.exists():
            import_csv(self.conn, csv_path)
        self.handler_cls = make_handler(str(self.db_path), write_token="test-write-token")
        self.server = HTTPServer(("127.0.0.1", 0), self.handler_cls)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.conn.close()
        self.tmp.cleanup()

    def _post(self, path, body=None):
        data = json.dumps(body or {}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header(self.handler_cls.write_token_header, self.handler_cls.write_token)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def _get(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())

    def _make_account_via_api(self, name="API Test Client"):
        person = create_person(self.conn, {"display_name": name, "first_name": "API", "last_name": "Test"})
        result = setup_billing_relationship(self.conn, {
            "payer_kind": "client",
            "payer_person_id": person["person_id"],
            "covered_client_ids": [person["person_id"]],
            "use_for_future_sessions": True,
        })
        return result["account_id"], person

    # API: deactivate via HTTP
    def test_api_deactivate(self):
        account_id, _ = self._make_account_via_api("Deactivate API")
        status, body = self._post(f"/api/accounts/{account_id}/deactivate")
        self.assertEqual(status, 200)
        self.assertEqual(body["active"], 0)

    # API: reactivate via HTTP
    def test_api_reactivate(self):
        account_id, _ = self._make_account_via_api("Reactivate API")
        self._post(f"/api/accounts/{account_id}/deactivate")
        status, body = self._post(f"/api/accounts/{account_id}/reactivate")
        self.assertEqual(status, 200)
        self.assertEqual(body["active"], 1)

    # API: 404 for missing account
    def test_api_deactivate_404(self):
        status, body = self._post("/api/accounts/nonexistent/deactivate")
        self.assertEqual(status, 404)
        self.assertIn("not found", body["error"].lower())

    def test_api_reactivate_404(self):
        status, body = self._post("/api/accounts/nonexistent/reactivate")
        self.assertEqual(status, 404)
        self.assertIn("not found", body["error"].lower())

    # API: idempotent deactivate
    def test_api_deactivate_idempotent(self):
        account_id, _ = self._make_account_via_api("Idempotent Deact")
        self._post(f"/api/accounts/{account_id}/deactivate")
        status, body = self._post(f"/api/accounts/{account_id}/deactivate")
        self.assertEqual(status, 200)
        self.assertEqual(body["active"], 0)

    # API: idempotent reactivate
    def test_api_reactivate_idempotent(self):
        account_id, _ = self._make_account_via_api("Idempotent React")
        self._post(f"/api/accounts/{account_id}/deactivate")
        self._post(f"/api/accounts/{account_id}/reactivate")
        status, body = self._post(f"/api/accounts/{account_id}/reactivate")
        self.assertEqual(status, 200)
        self.assertEqual(body["active"], 1)

    # API: billing-relationships directory returns active field
    def test_directory_returns_active_field(self):
        status, records = self._get("/api/billing-relationships")
        self.assertEqual(status, 200)
        self.assertTrue(isinstance(records, list))
        if records:
            self.assertIn("active", records[0])

    # API: round 2 wizard setup still works
    def test_wizard_setup_still_works(self):
        person = create_person(self.conn, {"display_name": "Wizard Still Works", "first_name": "Wizard", "last_name": "Works"})
        status, body = self._post("/api/billing-relationships/setup", {
            "payer_kind": "client",
            "payer_person_id": person["person_id"],
            "covered_client_ids": [person["person_id"]],
            "use_for_future_sessions": True,
        })
        self.assertEqual(status, 200)
        self.assertIn("account_id", body)

    # API: raw calendar evidence unchanged after deactivation
    def test_raw_evidence_unchanged(self):
        account_id, _ = self._make_account_via_api("Evidence Test")
        before = self.conn.execute("SELECT COUNT(*) AS c FROM raw_calendar_snapshots").fetchone()["c"]
        self._post(f"/api/accounts/{account_id}/deactivate")
        after = self.conn.execute("SELECT COUNT(*) AS c FROM raw_calendar_snapshots").fetchone()["c"]
        self.assertEqual(before, after)

    # API: no schema migration table introduced
    def test_no_schema_migration(self):
        tables = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%migration%'"
        ).fetchall()
        # The schema_migrations table may already exist from the schema; that's fine.
        # We're checking no NEW migration tables were introduced.
        existing = [t["name"] for t in tables]
        # schema_migrations is expected if it was already there
        for t in existing:
            if t != "schema_migrations":
                self.fail(f"Unexpected migration table: {t}")

    # API: deactivation preserves account members
    def test_deactivation_preserves_members_api(self):
        account_id, person = self._make_account_via_api("Members API")
        self._post(f"/api/accounts/{account_id}/deactivate")
        members = self.conn.execute(
            "SELECT * FROM account_members WHERE account_id = ?", (account_id,)
        ).fetchall()
        self.assertTrue(len(members) >= 1)
        self.assertEqual(members[0]["person_id"], person["person_id"])

    # API: double deactivate doesn't create duplicate audit
    def test_no_duplicate_audit_via_api(self):
        account_id, _ = self._make_account_via_api("Audit Dup")
        self._post(f"/api/accounts/{account_id}/deactivate")
        self._post(f"/api/accounts/{account_id}/deactivate")
        audits = self.conn.execute(
            "SELECT * FROM audit_log WHERE entity_type = 'client_account' AND entity_id = ? AND action = 'deactivated'",
            (account_id,),
        ).fetchall()
        self.assertEqual(len(audits), 1)


if __name__ == "__main__":
    unittest.main()
