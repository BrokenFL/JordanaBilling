import json
import unittest

import test_approval_staging as fixtures
from jordana_invoice.review_services import approve_candidate, create_billing_party, create_person
from jordana_invoice.invoice_services import stage_approved_sessions_to_monthly_drafts, _participant_names
from jordana_invoice.util import new_id


class InactiveBillingRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.ApprovalStagingTests()
        self.fx.setUp()
        self.conn = self.fx.conn
        self.old = self.fx.party["billing_party_id"]
        self.sid = self.conn.execute("SELECT id FROM sessions WHERE candidate_id=?", (self.fx.candidate_id,)).fetchone()[0]

    def tearDown(self):
        self.fx.tearDown()

    def replacement(self, label="Morgan Parent"):
        person = create_person(self.conn, {"display_name": label})
        party = create_billing_party(self.conn, {"billing_name": label, "person_id": person["person_id"]})
        account = new_id()
        self.conn.execute("INSERT INTO client_accounts(account_id,account_code,account_name,default_billing_party_id,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                          (account, account, label, party["billing_party_id"], "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"))
        self.conn.execute("INSERT INTO account_members(account_member_id,account_id,person_id,created_at,updated_at) VALUES(?,?,?,?,?)",
                          (new_id(), account, self.fx.person["person_id"], "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"))
        self.conn.commit()
        return account, party["billing_party_id"]

    def retire(self):
        self.conn.execute("UPDATE billing_parties SET active=0 WHERE billing_party_id=?", (self.old,))
        self.conn.commit()

    def approve(self):
        return approve_candidate(self.conn, self.fx.candidate_id,
            fixtures.approval_payload(self.fx.person["person_id"], self.old))

    def test_approval_resolves_retired_payer_and_stages_correct_draft(self):
        account, payer = self.replacement()
        self.retire()
        result = self.fx._approve_via_http()
        self.assertEqual(result["status"], 200)
        row = self.conn.execute("SELECT s.billing_party_id,i.bill_to_party_id,li.line_amount_cents FROM sessions s JOIN invoice_line_items li ON li.source_session_id=s.id JOIN invoices i ON i.invoice_id=li.invoice_id WHERE s.id=?", (self.sid,)).fetchone()
        self.assertEqual(tuple(row), (payer, payer, 15000))

    def test_old_unbilled_approval_repaired_once_preserving_charged_values(self):
        account, payer = self.replacement()
        self.approve()
        self.retire()
        before = dict(self.conn.execute("SELECT * FROM sessions WHERE id=?", (self.sid,)).fetchone())
        result = stage_approved_sessions_to_monthly_drafts(self.conn)
        self.assertEqual(result["payer_links_repaired"], 1)
        self.assertEqual(result["sessions_staged"], 1)
        after = dict(self.conn.execute("SELECT * FROM sessions WHERE id=?", (self.sid,)).fetchone())
        for key in before:
            if key not in {"account_id", "billing_party_id", "updated_at"}:
                self.assertEqual(before[key], after[key], key)
        self.assertEqual(after["billing_party_id"], payer)
        again = stage_approved_sessions_to_monthly_drafts(self.conn)
        self.assertEqual(again["payer_links_repaired"], 0)
        self.assertEqual(again["sessions_staged"], 0)

    def test_ambiguous_replacement_blocks_new_approval(self):
        self.replacement()
        self.replacement("Taylor Parent")
        self.retire()
        with self.assertRaisesRegex(ValueError, "billing_party_id"):
            self.approve()
        self.assertNotEqual(self.conn.execute("SELECT review_status FROM sessions WHERE id=?", (self.sid,)).fetchone()[0], "approved")

    def test_relationship_created_after_approval_cannot_rewrite_approved_payer(self):
        account, payer = self.replacement()
        self.approve()
        self.retire()
        self.conn.execute("UPDATE account_members SET created_at='2099-01-01T00:00:00Z' WHERE account_id=?", (account,))
        self.conn.commit()
        result = stage_approved_sessions_to_monthly_drafts(self.conn)
        self.assertEqual(result["payer_links_repaired"], 0)
        self.assertEqual(result["sessions_staged"], 0)
        self.assertTrue(any("inactive" in reason for item in result["sessions_skipped"] for reason in item["reasons"]))

    def test_existing_draft_line_is_preserved_when_payer_is_retired(self):
        self.approve()
        stage_approved_sessions_to_monthly_drafts(self.conn)
        line = dict(self.conn.execute("SELECT * FROM invoice_line_items WHERE source_session_id=?", (self.sid,)).fetchone())
        self.replacement()
        self.retire()
        result = stage_approved_sessions_to_monthly_drafts(self.conn)
        self.assertEqual(result["payer_links_repaired"], 0)
        self.assertEqual(line, dict(self.conn.execute("SELECT * FROM invoice_line_items WHERE source_session_id=?", (self.sid,)).fetchone()))

    def test_paid_at_session_is_not_repaired(self):
        self.replacement()
        self.approve()
        self.retire()
        self.conn.execute("UPDATE sessions SET payment_status='paid_at_session' WHERE id=?", (self.sid,))
        self.conn.commit()
        result = stage_approved_sessions_to_monthly_drafts(self.conn)
        self.assertEqual(result["payer_links_repaired"], 0)
        self.assertEqual(result["sessions_staged"], 0)

    def test_expired_membership_does_not_route_session(self):
        account, payer = self.replacement()
        self.approve()
        self.retire()
        self.conn.execute("UPDATE account_members SET effective_through='2025-12-31' WHERE account_id=?", (account,))
        self.conn.commit()
        self.assertEqual(stage_approved_sessions_to_monthly_drafts(self.conn)["payer_links_repaired"], 0)

    def test_duplicate_person_is_rendered_once_without_changing_participants(self):
        self.approve()
        self.conn.execute("INSERT INTO session_participants(session_participant_id,session_id,person_id,participant_name,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (new_id(), self.sid, self.fx.person["person_id"], "Avery Stone", "2026-01-01", "2026-01-01"))
        self.conn.commit()
        self.assertEqual(_participant_names(self.conn, self.sid), "Avery Stone")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM session_participants WHERE session_id=?", (self.sid,)).fetchone()[0], 2)
