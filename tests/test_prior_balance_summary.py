import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import (
    create_invoice_draft,
    finalize_invoice,
    get_invoice,
    calculate_invoice_account_summary,
    void_invoice,
)
from jordana_invoice.review_services import approve_candidate, create_billing_party, create_person
from jordana_invoice.payment_services import record_invoice_payment
from jordana_invoice.util import stable_hash

def raw_row(key, title, start):
    return {
        "ingested_at": "2026-08-20T12:00:00Z", "snapshot_key": key, "run_id": f"run-{key}",
        "batch_name": "invoice-demo", "capture_window": "past_7_days", "captured_at": "2026-08-20T12:00:00Z",
        "source_device": "test", "timezone": "America/New_York", "calendar_event_id": f"event-{key}",
        "event_fingerprint": f"fp-{key}", "event_title": title, "start_at": start,
        "end_at": start[:11] + "11:00:00-04:00", "duration_minutes": "60", "calendar": "Jordana Work",
        "payload_version": "2", "raw_json": "{}",
    }

class PriorBalanceSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "invoice.sqlite3")
        init_db(self.conn)
        
        # Setup Person and Payer 1
        self.person = create_person(self.conn, {"first_name": "Avery", "last_name": "Stone", "display_name": "Avery Stone"})
        self.party = create_billing_party(self.conn, {
            "billing_name": "Avery Stone", "person_id": self.person["person_id"],
            "billing_email": "avery@example.test", "billing_address_line_1": "10 Sample Street",
            "billing_city": "Example", "billing_state": "FL", "billing_postal_code": "00000",
            "preferred_delivery_method": "both",
        })
        
        # Setup Person and Payer 2 (Unrelated)
        self.other_person = create_person(self.conn, {"first_name": "Blake", "last_name": "River", "display_name": "Blake River"})
        self.other_party = create_billing_party(self.conn, {
            "billing_name": "Blake River", "person_id": self.other_person["person_id"],
            "billing_email": "blake@example.test", "billing_address_line_1": "20 Test Road",
            "billing_city": "Example", "billing_state": "FL", "billing_postal_code": "00000",
            "preferred_delivery_method": "both",
        })

        self.conn.execute(
            """
            INSERT INTO business_profile (
                business_profile_id, business_name, provider_display_name,
                address_line_1, city, state, postal_code, phone, email, payee_name,
                payment_address_line_1, payment_city, payment_state, payment_postal_code,
                zelle_recipient, active, created_at, updated_at
            ) VALUES (
                'bp-1', 'Demo Practice', 'Demo Provider', '100 Ave', 'Example', 'FL', '00000',
                '555-0100', 'billing@example.test', 'Demo Payee', '100 Ave', 'Example', 'FL', '00000',
                'demo-zelle@example.test', 1, '2026-05-01T12:00:00Z', '2026-05-01T12:00:00Z'
            )
            """
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def create_approved_session(self, key, person, party, date_str, amount="150.00"):
        import_rows(self.conn, [raw_row(key, f"{person['display_name']} | 60 | Office", f"{date_str}T10:00:00-04:00")], "test")
        candidate_id = self.conn.execute("SELECT id FROM calendar_event_candidates WHERE candidate_key = ?", (stable_hash(f"calendar_event_id:event-{key}"),)).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": person["person_id"], "display_name": person["display_name"]}],
            "billing_party_id": party["billing_party_id"], "approved_duration_minutes": 60,
            "service_mode": "office", "time_category": "standard", "approved_rate": amount,
            "payment_status": "unpaid", "billing_treatment": "billable",
        })
        return self.conn.execute("SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)).fetchone()

    def create_draft(self, party, sessions, date_str):
        return create_invoice_draft(self.conn, {
            "bill_to_party_id": party["billing_party_id"],
            "billing_period_start": date_str[:7] + "-01",
            "billing_period_end": date_str,
            "invoice_date": date_str,
            "session_ids": [s["id"] for s in sessions],
        })

    def test_calculate_no_prior_invoices(self):
        s = self.create_approved_session("key1", self.person, self.party, "2026-05-15")
        draft = self.create_draft(self.party, [s], "2026-05-31")
        
        summary = calculate_invoice_account_summary(self.conn, draft["invoice"]["invoice_id"])
        self.assertEqual(summary["version"], 1)
        self.assertEqual(summary["current_invoice_total_cents"], 15000)
        self.assertEqual(summary["current_invoice_paid_cents"], 0)
        self.assertEqual(summary["current_invoice_balance_cents"], 15000)
        self.assertEqual(summary["prior_unpaid_balance_cents"], 0)
        self.assertEqual(summary["total_amount_due_cents"], 15000)
        self.assertEqual(len(summary["prior_invoices"]), 0)

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_calculate_with_unpaid_prior_invoices(self, fake_pdf):
        fake_pdf.return_value = "c" * 64
        
        # 1. Finalize prior invoice
        s1 = self.create_approved_session("key1", self.person, self.party, "2026-05-10")
        d1 = self.create_draft(self.party, [s1], "2026-05-15")
        finalized1 = finalize_invoice(self.conn, d1["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
        
        # 2. Create second draft
        s2 = self.create_approved_session("key2", self.person, self.party, "2026-05-25")
        d2 = self.create_draft(self.party, [s2], "2026-05-31")
        
        summary = calculate_invoice_account_summary(self.conn, d2["invoice"]["invoice_id"])
        self.assertEqual(summary["current_invoice_total_cents"], 15000)
        self.assertEqual(summary["prior_unpaid_balance_cents"], 15000)
        self.assertEqual(summary["total_amount_due_cents"], 30000)
        self.assertEqual(len(summary["prior_invoices"]), 1)
        self.assertEqual(summary["prior_invoices"][0]["invoice_number"], finalized1["invoice"]["invoice_number"])
        self.assertEqual(summary["prior_invoices"][0]["remaining_balance_cents"], 15000)

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_calculate_ignores_unrelated_billing_party(self, fake_pdf):
        fake_pdf.return_value = "c" * 64
        
        # Blake River gets finalized invoice
        s1 = self.create_approved_session("key1", self.other_person, self.other_party, "2026-05-10")
        d1 = self.create_draft(self.other_party, [s1], "2026-05-15")
        finalize_invoice(self.conn, d1["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
        
        # Avery Stone gets draft
        s2 = self.create_approved_session("key2", self.person, self.party, "2026-05-25")
        d2 = self.create_draft(self.party, [s2], "2026-05-31")
        
        summary = calculate_invoice_account_summary(self.conn, d2["invoice"]["invoice_id"])
        self.assertEqual(summary["prior_unpaid_balance_cents"], 0)

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_calculate_excludes_draft_and_void(self, fake_pdf):
        fake_pdf.return_value = "c" * 64
        
        # Avery gets a draft (not finalized)
        s1 = self.create_approved_session("key1", self.person, self.party, "2026-05-10")
        self.create_draft(self.party, [s1], "2026-05-15")
        
        # Avery gets a void invoice
        s2 = self.create_approved_session("key2", self.person, self.party, "2026-05-12")
        d2 = self.create_draft(self.party, [s2], "2026-05-15")
        finalize_invoice(self.conn, d2["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
        void_invoice(self.conn, d2["invoice"]["invoice_id"], "mistake")
        
        # Avery gets third draft
        s3 = self.create_approved_session("key3", self.person, self.party, "2026-05-25")
        d3 = self.create_draft(self.party, [s3], "2026-05-31")
        
        summary = calculate_invoice_account_summary(self.conn, d3["invoice"]["invoice_id"])
        self.assertEqual(summary["prior_unpaid_balance_cents"], 0)

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_calculate_excludes_fully_paid(self, fake_pdf):
        fake_pdf.return_value = "c" * 64
        
        # 1. Finalize prior
        s1 = self.create_approved_session("key1", self.person, self.party, "2026-05-10")
        d1 = self.create_draft(self.party, [s1], "2026-05-15")
        finalized1 = finalize_invoice(self.conn, d1["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
        
        # 2. Record payment fully paying it
        record_invoice_payment(
            self.conn,
            invoice_id=finalized1["invoice"]["invoice_id"],
            payment_date="2026-05-20",
            amount_cents=15000,
            payment_method="zelle",
            reference_number="TXN123",
        )
        self.conn.commit()
        
        # 3. Create second draft
        s2 = self.create_approved_session("key2", self.person, self.party, "2026-05-25")
        d2 = self.create_draft(self.party, [s2], "2026-05-31")
        
        summary = calculate_invoice_account_summary(self.conn, d2["invoice"]["invoice_id"])
        self.assertEqual(summary["prior_unpaid_balance_cents"], 0)

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_same_date_cutoff_ordering(self, fake_pdf):
        fake_pdf.return_value = "c" * 64
        
        # Create and finalize first draft
        s1 = self.create_approved_session("key1", self.person, self.party, "2026-05-31")
        d1 = self.create_draft(self.party, [s1], "2026-05-31")
        with patch("jordana_invoice.invoice_services.now_iso", return_value="2026-05-31T10:00:00.000Z"):
            finalized1 = finalize_invoice(self.conn, d1["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
            
        # Now we can create the second draft and finalize it
        s2 = self.create_approved_session("key2", self.person, self.party, "2026-05-31")
        d2 = self.create_draft(self.party, [s2], "2026-05-31")
        with patch("jordana_invoice.invoice_services.now_iso", return_value="2026-05-31T11:00:00.000Z"):
            finalized2 = finalize_invoice(self.conn, d2["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
            
        # Verify finalized2 has finalized1 as prior
        snap2 = json.loads(finalized2["invoice"]["account_summary_snapshot"])
        self.assertEqual(snap2["prior_unpaid_balance_cents"], 15000)
        self.assertEqual(len(snap2["prior_invoices"]), 1)
        self.assertEqual(snap2["prior_invoices"][0]["invoice_id"], finalized1["invoice"]["invoice_id"])
        
        # Verify finalized1 does NOT have finalized2 as prior
        snap1 = json.loads(finalized1["invoice"]["account_summary_snapshot"])
        self.assertEqual(snap1["prior_unpaid_balance_cents"], 0)

    def test_legacy_invoice_handling(self):
        s = self.create_approved_session("key1", self.person, self.party, "2026-05-15")
        draft = self.create_draft(self.party, [s], "2026-05-31")
        
        # Manually insert legacy invoice (finalized but snapshot is NULL)
        self.conn.execute(
            """
            UPDATE invoices
            SET status = 'finalized', account_summary_snapshot = NULL
            WHERE invoice_id = ?
            """,
            (draft["invoice"]["invoice_id"],)
        )
        self.conn.commit()
        
        detail = get_invoice(self.conn, draft["invoice"]["invoice_id"])
        self.assertIsNone(detail["as_finalized_summary"])
        self.assertEqual(detail["current_status"]["current_invoice_total_cents"], 15000)

    def test_versioned_json_snapshot_validation(self):
        s = self.create_approved_session("key1", self.person, self.party, "2026-05-15")
        draft = self.create_draft(self.party, [s], "2026-05-31")
        
        # Save invalid version snapshot
        bad_version = {
            "version": 2,
            "current_invoice_total_cents": 80000,
            "current_invoice_paid_cents": 0,
            "current_invoice_balance_cents": 80000,
            "prior_unpaid_balance_cents": 40000,
            "total_amount_due_cents": 120000,
            "prior_invoices": []
        }
        self.conn.execute(
            "UPDATE invoices SET status = 'finalized', account_summary_snapshot = ? WHERE invoice_id = ?",
            (json.dumps(bad_version), draft["invoice"]["invoice_id"])
        )
        self.conn.commit()
        
        detail = get_invoice(self.conn, draft["invoice"]["invoice_id"])
        self.assertIsNone(detail["as_finalized_summary"])  # Treated as unavailable

        # Save malformed JSON
        self.conn.execute(
            "UPDATE invoices SET status = 'finalized', account_summary_snapshot = 'NOT JSON' WHERE invoice_id = ?",
            (draft["invoice"]["invoice_id"],)
        )
        self.conn.commit()
        
        detail = get_invoice(self.conn, draft["invoice"]["invoice_id"])
        self.assertIsNone(detail["as_finalized_summary"])  # Treated as unavailable

        # Save valid version 1 snapshot
        valid = {
            "version": 1,
            "current_invoice_total_cents": 15000,
            "current_invoice_paid_cents": 0,
            "current_invoice_balance_cents": 15000,
            "prior_unpaid_balance_cents": 4000,
            "total_amount_due_cents": 19000,
            "prior_invoices": [{"invoice_id": "prev", "invoice_number": "JS-01", "invoice_date": "2026-05-10", "remaining_balance_cents": 4000}]
        }
        self.conn.execute(
            "UPDATE invoices SET status = 'finalized', account_summary_snapshot = ? WHERE invoice_id = ?",
            (json.dumps(valid), draft["invoice"]["invoice_id"])
        )
        self.conn.commit()
        
        detail = get_invoice(self.conn, draft["invoice"]["invoice_id"])
        self.assertIsNotNone(detail["as_finalized_summary"])
        self.assertEqual(detail["as_finalized_summary"]["prior_unpaid_balance_cents"], 4000)

if __name__ == "__main__":
    unittest.main()
