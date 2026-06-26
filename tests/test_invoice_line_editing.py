import tempfile
import unittest
import sqlite3
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, migrate_database
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import (
    create_invoice_draft,
    get_invoice,
    update_invoice_line_item,
    finalize_invoice,
    void_invoice,
    save_business_profile,
)
from jordana_invoice.review_services import approve_candidate, create_billing_party, create_person
from jordana_invoice.util import stable_hash


def raw_row(key, title, start):
    return {
        "ingested_at": "2026-08-20T12:00:00Z",
        "snapshot_key": key,
        "run_id": f"run-{key}",
        "batch_name": "invoice-demo",
        "capture_window": "past_7_days",
        "captured_at": "2026-08-20T12:00:00Z",
        "source_device": "test",
        "timezone": "America/New_York",
        "calendar_event_id": f"event-{key}",
        "event_fingerprint": f"fp-{key}",
        "event_title": title,
        "start_at": start,
        "end_at": start[:11] + "11:00:00-04:00",
        "duration_minutes": "60",
        "calendar": "Jordana Work",
        "payload_version": "2",
        "raw_json": "{}",
    }


class InvoiceLineEditingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "invoice.sqlite3"
        migrate_database(self.db_path)
        self.conn = connect(self.db_path)

        self.person = create_person(self.conn, {"first_name": "Avery", "last_name": "Stone", "display_name": "Avery Stone"})
        self.party = create_billing_party(self.conn, {
            "billing_name": "Avery Stone", "person_id": self.person["person_id"],
            "billing_email": "avery@example.test", "billing_address_line_1": "10 Sample Street",
            "billing_city": "Example", "billing_state": "FL", "billing_postal_code": "00000",
            "preferred_delivery_method": "both",
        })
        save_business_profile(self.conn, {
            "business_name": "Demo Practice", "provider_display_name": "Demo Provider",
            "address_line_1": "100 Example Avenue", "city": "Example", "state": "FL", "postal_code": "00000",
            "phone": "555-0100", "email": "billing@example.test", "payee_name": "Demo Payee",
            "payment_address_line_1": "100 Example Avenue", "payment_city": "Example", "payment_state": "FL",
            "payment_postal_code": "00000", "invoice_total_label": "TOTAL DUE", "invoice_number_format": "YYYY-NNNN",
        })

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def approved_session(self, key="one", title="Avery Stone | 60 | Office", amount="150.00", start_time=None):
        if start_time is None:
            start_time = f"2026-05-{10 + len(key):02d}T10:00:00-04:00"
        import_rows(self.conn, [raw_row(key, title, start_time)], "test")
        candidate_id = self.conn.execute("SELECT id FROM calendar_event_candidates WHERE candidate_key = ?", (stable_hash(f"calendar_event_id:event-{key}"),)).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Avery Stone"}],
            "billing_party_id": self.party["billing_party_id"], "approved_duration_minutes": 60,
            "service_mode": "office", "time_category": "standard", "approved_rate": amount,
            "payment_status": "unpaid", "billing_treatment": "billable",
        })
        return self.conn.execute("SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)).fetchone()

    def draft(self, sessions, period_start="2026-05-01", period_end="2026-05-31", invoice_date="2026-05-31"):
        return create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"], "billing_period_start": period_start,
            "billing_period_end": period_end, "invoice_date": invoice_date,
            "session_ids": [row["id"] for row in sessions],
        })

    def test_edit_description_only_succeeds(self):
        """1. Editing description on a draft line succeeds without reason."""
        session = self.approved_session("desc_only", amount="150.00")
        draft = self.draft([session])
        line = draft["lines"][0]

        updated = update_invoice_line_item(
            self.conn,
            draft["invoice"]["invoice_id"],
            line_id=line["invoice_line_item_id"],
            description="Custom Updated Therapy Session",
            amount_cents=15000,
            amount_scope="invoice_line_only",
            reason="",
            expected_revision=draft["invoice"]["revision"]
        )

        self.assertEqual(updated["lines"][0]["description_snapshot"], "Custom Updated Therapy Session")
        self.assertEqual(updated["lines"][0]["line_amount_cents"], 15000)

        # Confirm no correction audits since amount didn't change
        corr_count = self.conn.execute("SELECT COUNT(*) FROM invoice_line_item_corrections").fetchone()[0]
        self.assertEqual(corr_count, 0)

    def test_edit_amount_line_only(self):
        """2. Editing amount for invoice line only changes line but not backing session."""
        session = self.approved_session("line_only", amount="150.00")
        draft = self.draft([session])
        line = draft["lines"][0]

        updated = update_invoice_line_item(
            self.conn,
            draft["invoice"]["invoice_id"],
            line_id=line["invoice_line_item_id"],
            description=line["description_snapshot"],
            amount_cents=18000,
            amount_scope="invoice_line_only",
            reason="Custom discount reduction",
            expected_revision=draft["invoice"]["revision"]
        )

        # Line is updated
        self.assertEqual(updated["lines"][0]["line_amount_cents"], 18000)
        self.assertEqual(updated["invoice"]["total_cents"], 18000)

        # Backing session is NOT updated
        session_row = self.conn.execute("SELECT approved_rate_cents FROM sessions WHERE id = ?", (session["id"],)).fetchone()
        self.assertEqual(session_row["approved_rate_cents"], 15000)

        # Audited
        corr = self.conn.execute("SELECT * FROM invoice_line_item_corrections").fetchone()
        self.assertIsNotNone(corr)
        self.assertEqual(corr["old_amount_cents"], 15000)
        self.assertEqual(corr["new_amount_cents"], 18000)
        self.assertEqual(corr["correction_scope"], "invoice_line_only")
        self.assertEqual(corr["reason"], "Custom discount reduction")

    def test_edit_amount_line_and_session(self):
        """3. Editing amount for invoice line and approved session updates both."""
        session = self.approved_session("line_and_sess", amount="150.00")
        draft = self.draft([session])
        line = draft["lines"][0]

        updated = update_invoice_line_item(
            self.conn,
            draft["invoice"]["invoice_id"],
            line_id=line["invoice_line_item_id"],
            description=line["description_snapshot"],
            amount_cents=20000,
            amount_scope="invoice_line_and_session",
            reason="Corrected typo in rate during review",
            expected_revision=draft["invoice"]["revision"]
        )

        self.assertEqual(updated["lines"][0]["line_amount_cents"], 20000)

        # Backing session IS updated
        session_row = self.conn.execute("SELECT approved_rate_cents, rate_cents_snapshot FROM sessions WHERE id = ?", (session["id"],)).fetchone()
        self.assertEqual(session_row["approved_rate_cents"], 20000)
        self.assertEqual(session_row["rate_cents_snapshot"], 20000)

        # Correction audit
        corr = self.conn.execute("SELECT * FROM invoice_line_item_corrections").fetchone()
        self.assertEqual(corr["correction_scope"], "invoice_line_and_session")

    def test_future_rate_rules_unmodified(self):
        """5. Future rate rules remain unchanged for both scopes."""
        session = self.approved_session("rate_rules_test", amount="150.00")
        # Create a rate rule in the DB
        self.conn.execute(
            "INSERT INTO rate_rules (rate_rule_id, amount_cents, effective_from, active, created_at, updated_at) VALUES ('rule-1', 15000, '2026-01-01', 1, 'now', 'now')"
        )
        self.conn.commit()

        draft = self.draft([session])
        line = draft["lines"][0]

        update_invoice_line_item(
            self.conn,
            draft["invoice"]["invoice_id"],
            line_id=line["invoice_line_item_id"],
            description=line["description_snapshot"],
            amount_cents=20000,
            amount_scope="invoice_line_and_session",
            reason="Fix rate",
            expected_revision=draft["invoice"]["revision"]
        )

        rule_amount = self.conn.execute("SELECT amount_cents FROM rate_rules WHERE rate_rule_id = 'rule-1'").fetchone()["amount_cents"]
        self.assertEqual(rule_amount, 15000)

    def test_reason_required_when_amount_changes(self):
        """6. Reason required when amount changes."""
        session = self.approved_session("reason_req", amount="150.00")
        draft = self.draft([session])
        line = draft["lines"][0]

        with self.assertRaises(ValueError) as ctx:
            update_invoice_line_item(
                self.conn,
                draft["invoice"]["invoice_id"],
                line_id=line["invoice_line_item_id"],
                description=line["description_snapshot"],
                amount_cents=20000,
                amount_scope="invoice_line_only",
                reason="",
                expected_revision=draft["invoice"]["revision"]
            )
        self.assertIn("correction reason is required", str(ctx.exception))

    def test_blank_description_rejected(self):
        """7. Description cannot be blank."""
        session = self.approved_session("blank_desc", amount="150.00")
        draft = self.draft([session])
        line = draft["lines"][0]

        with self.assertRaises(ValueError) as ctx:
            update_invoice_line_item(
                self.conn,
                draft["invoice"]["invoice_id"],
                line_id=line["invoice_line_item_id"],
                description="   ",
                amount_cents=15000,
                amount_scope="invoice_line_only",
                reason="",
                expected_revision=draft["invoice"]["revision"]
            )
        self.assertIn("Description must be non-empty", str(ctx.exception))

    def test_negative_amount_rejected(self):
        """8. Negative amount rejected."""
        session = self.approved_session("neg_amt", amount="150.00")
        draft = self.draft([session])
        line = draft["lines"][0]

        with self.assertRaises(ValueError) as ctx:
            update_invoice_line_item(
                self.conn,
                draft["invoice"]["invoice_id"],
                line_id=line["invoice_line_item_id"],
                description=line["description_snapshot"],
                amount_cents=-100,
                amount_scope="invoice_line_only",
                reason="Neg",
                expected_revision=draft["invoice"]["revision"]
            )
        self.assertIn("Amount must be non-negative", str(ctx.exception))

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_finalized_and_voided_invoice_edit_rejected(self, fake_pdf):
        """10. Finalized invoice edit rejected. 11. Voided invoice edit rejected."""
        fake_pdf.return_value = "f" * 64
        session = self.approved_session("finalized")
        draft = self.draft([session])
        line = draft["lines"][0]

        finalized = finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")

        # Edit finalized
        with self.assertRaises(ValueError) as ctx:
            update_invoice_line_item(
                self.conn,
                draft["invoice"]["invoice_id"],
                line_id=line["invoice_line_item_id"],
                description="Edit Finalized",
                amount_cents=15000,
                amount_scope="invoice_line_only",
                reason="",
                expected_revision=finalized["invoice"]["revision"]
            )
        self.assertIn("Only a draft invoice can be changed", str(ctx.exception))

        # Void and edit voided
        voided = void_invoice(self.conn, draft["invoice"]["invoice_id"], "mistake")
        with self.assertRaises(ValueError) as ctx:
            update_invoice_line_item(
                self.conn,
                draft["invoice"]["invoice_id"],
                line_id=line["invoice_line_item_id"],
                description="Edit Voided",
                amount_cents=15000,
                amount_scope="invoice_line_only",
                reason="",
                expected_revision=voided["invoice"]["revision"]
            )
        self.assertIn("Only a draft invoice can be changed", str(ctx.exception))

    def test_line_from_another_invoice_rejected(self):
        """12. Line from another invoice rejected."""
        s1 = self.approved_session("other1")
        s2 = self.approved_session("other2", start_time="2026-06-15T10:00:00-04:00")
        d1 = self.draft([s1])
        d2 = self.draft([s2], period_start="2026-06-01", period_end="2026-06-30", invoice_date="2026-06-30")

        with self.assertRaises(ValueError) as ctx:
            update_invoice_line_item(
                self.conn,
                d1["invoice"]["invoice_id"],
                line_id=d2["lines"][0]["invoice_line_item_id"],
                description="Sneaky",
                amount_cents=15000,
                amount_scope="invoice_line_only",
                reason="",
                expected_revision=d1["invoice"]["revision"]
            )
        self.assertIn("does not belong to this invoice", str(ctx.exception))

    def test_stale_expected_revision_rejected(self):
        """13. Stale expected revision rejected. 14. Increment revision exactly once. 15. Recalculate totals."""
        session = self.approved_session("stale", amount="150.00")
        draft = self.draft([session])
        line = draft["lines"][0]

        # Succeeds and increments revision
        updated1 = update_invoice_line_item(
            self.conn,
            draft["invoice"]["invoice_id"],
            line_id=line["invoice_line_item_id"],
            description="First Update",
            amount_cents=16000,
            amount_scope="invoice_line_only",
            reason="Fix",
            expected_revision=draft["invoice"]["revision"]
        )
        self.assertEqual(updated1["invoice"]["revision"], draft["invoice"]["revision"] + 1)
        self.assertEqual(updated1["invoice"]["total_cents"], 16000)

        # Mismatched expected revision (uses original)
        with self.assertRaises(ValueError) as ctx:
            update_invoice_line_item(
                self.conn,
                draft["invoice"]["invoice_id"],
                line_id=line["invoice_line_item_id"],
                description="Stale Update",
                amount_cents=17000,
                amount_scope="invoice_line_only",
                reason="Stale",
                expected_revision=draft["invoice"]["revision"] # Stale revision
            )
        self.assertIn("Invoice has changed. Please reload and try again.", str(ctx.exception))

    def test_atomicity_failed_validation_leaves_no_changes(self):
        """17. Failed operation leaves no partial changes."""
        session = self.approved_session("failed_atomicity", amount="150.00")
        draft = self.draft([session])
        line = draft["lines"][0]

        with self.assertRaises(ValueError):
            # Description is blank, which fails validation after transaction starts
            update_invoice_line_item(
                self.conn,
                draft["invoice"]["invoice_id"],
                line_id=line["invoice_line_item_id"],
                description="",
                amount_cents=20000,
                amount_scope="invoice_line_only",
                reason="Failed reason",
                expected_revision=draft["invoice"]["revision"]
            )

        # Database connection was rolled back; line description and amount remain unchanged
        reopened = get_invoice(self.conn, draft["invoice"]["invoice_id"])
        self.assertEqual(reopened["lines"][0]["description_snapshot"], line["description_snapshot"])
        self.assertEqual(reopened["lines"][0]["line_amount_cents"], 15000)

    def test_repeated_submission_fails_due_to_revision(self):
        """18. Repeated submission does not create duplicate audit changes (rejected by stale revision)."""
        session = self.approved_session("repeated_sub", amount="150.00")
        draft = self.draft([session])
        line = draft["lines"][0]

        # First request succeeds
        update_invoice_line_item(
            self.conn,
            draft["invoice"]["invoice_id"],
            line_id=line["invoice_line_item_id"],
            description="Updated description",
            amount_cents=16000,
            amount_scope="invoice_line_only",
            reason="Correct rate",
            expected_revision=draft["invoice"]["revision"]
        )

        # Repeated request with the same expected_revision fails
        with self.assertRaises(ValueError) as ctx:
            update_invoice_line_item(
                self.conn,
                draft["invoice"]["invoice_id"],
                line_id=line["invoice_line_item_id"],
                description="Updated description",
                amount_cents=16000,
                amount_scope="invoice_line_only",
                reason="Correct rate",
                expected_revision=draft["invoice"]["revision"]  # stale!
            )
        self.assertIn("Invoice has changed. Please reload and try again.", str(ctx.exception))

    def test_api_route_update_line_success(self):
        """API: POST /api/invoices/{invoice_id}/update-line works correctly."""
        session = self.approved_session("api_success", amount="150.00")
        draft = self.draft([session])
        line = draft["lines"][0]

        from jordana_invoice.review_server import make_handler
        import io
        import json
        handler_cls = make_handler(str(self.db_path))
        
        body = json.dumps({
            "invoice_line_item_id": line["invoice_line_item_id"],
            "description": "API Update",
            "amount_cents": 16000,
            "amount_scope": "invoice_line_only",
            "reason": "API test reason",
            "expected_revision": draft["invoice"]["revision"]
        }).encode("utf-8")

        handler = object.__new__(handler_cls)
        handler.path = f"/api/invoices/{draft['invoice']['invoice_id']}/update-line"
        handler.headers = {
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
            handler_cls.write_token_header: handler_cls.write_token,
        }
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.send_error = lambda code: (_ for _ in ()).throw(AssertionError(f"unexpected error {code}"))
        
        captured = {}
        handler.send_json = lambda payload, status=200: captured.update({"payload": payload, "status": status})
        handler.finish = lambda: None
        handler.conn = lambda: self.conn
        
        handler.do_POST()
        
        self.assertEqual(captured.get("status"), 200)
        self.assertEqual(captured["payload"]["lines"][0]["description_snapshot"], "API Update")
        self.assertEqual(captured["payload"]["lines"][0]["line_amount_cents"], 16000)

    def test_api_route_unsanitized_error_handling(self):
        """API: Unsafe or unlisted validation errors are sanitized to standard error message."""
        session = self.approved_session("api_error", amount="150.00")
        draft = self.draft([session])
        line = draft["lines"][0]

        from jordana_invoice.review_server import make_handler
        import io
        import json
        handler_cls = make_handler(str(self.db_path))

        handler = object.__new__(handler_cls)
        handler.path = f"/api/invoices/{draft['invoice']['invoice_id']}/update-line"
        body = json.dumps({
            "invoice_line_item_id": line["invoice_line_item_id"],
            "description": "API Update",
            "amount_cents": 16000,
            "amount_scope": "invoice_line_only",
            "reason": "API test reason",
            "expected_revision": draft["invoice"]["revision"]
        }).encode("utf-8")
        handler.headers = {
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
            handler_cls.write_token_header: handler_cls.write_token,
        }
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.send_error = lambda code: (_ for _ in ()).throw(AssertionError(f"unexpected error {code}"))
        
        captured = {}
        handler.send_json = lambda payload, status=200: captured.update({"payload": payload, "status": status})
        handler.finish = lambda: None
        handler.conn = lambda: self.conn

        with patch("jordana_invoice.review_server.update_invoice_line_item", side_effect=ValueError("Secret DB internal path info")):
            handler.do_POST()

        self.assertEqual(captured.get("status"), 400)
        self.assertEqual(captured["payload"], {"ok": False, "error": "An unexpected error occurred."})

    def test_non_integer_amount_rejected(self):
        """9. more than two decimal places / non-integer amount rejected."""
        session = self.approved_session("non_int_amt", amount="150.00")
        draft = self.draft([session])
        line = draft["lines"][0]

        with self.assertRaises(ValueError) as ctx:
            update_invoice_line_item(
                self.conn,
                draft["invoice"]["invoice_id"],
                line_id=line["invoice_line_item_id"],
                description=line["description_snapshot"],
                amount_cents=15000.5, # float!
                amount_scope="invoice_line_only",
                reason="Fractional cents",
                expected_revision=draft["invoice"]["revision"]
            )
        self.assertIn("Amount must be non-negative", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
