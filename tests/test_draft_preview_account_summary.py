"""Regression tests for the draft preview account-summary wiring fix.

These tests prove that the print-preview and draft-PDF endpoints pass the
already-calculated account_summary (including prior unpaid balance) into the
rendering functions.  They would have failed before the fix because the
endpoints omitted the account_summary argument.
"""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, migrate_database
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_rendering import build_print_preview_html, build_invoice_render_model
from jordana_invoice.invoice_services import (
    create_invoice_draft,
    finalize_invoice,
    get_invoice,
    save_business_profile,
)
from jordana_invoice.review_server import make_handler
from jordana_invoice.review_services import approve_candidate, create_billing_party, create_person
from jordana_invoice.util import stable_hash


def raw_row(key, title, start):
    return {
        "ingested_at": "2026-08-20T12:00:00Z", "snapshot_key": key, "run_id": f"run-{key}",
        "batch_name": "test", "capture_window": "past_7_days", "captured_at": "2026-08-20T12:00:00Z",
        "source_device": "test", "timezone": "America/New_York", "calendar_event_id": f"event-{key}",
        "event_fingerprint": f"fp-{key}", "event_title": title, "start_at": start,
        "end_at": start[:11] + "11:00:00-04:00", "duration_minutes": "60", "calendar": "Jordana Work",
        "payload_version": "2", "raw_json": "{}",
    }


class DraftPreviewAccountSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "test.sqlite3"
        migrate_database(self.db_path)
        self.conn = connect(self.db_path)

        self.person = create_person(self.conn, {
            "first_name": "Dana", "last_name": "Testcase",
            "display_name": "Dana Testcase",
        })
        self.party = create_billing_party(self.conn, {
            "billing_name": "Dana Testcase",
            "person_id": self.person["person_id"],
            "billing_email": "dana@example.test",
            "billing_address_line_1": "1 Test St",
            "billing_city": "Test", "billing_state": "FL",
            "billing_postal_code": "00000",
            "preferred_delivery_method": "email",
        })
        save_business_profile(self.conn, {
            "business_name": "Test Practice",
            "provider_display_name": "Test Provider",
            "address_line_1": "100 Test Ave",
            "city": "Test", "state": "FL", "postal_code": "00000",
            "phone": "555-0100", "email": "billing@test",
            "payee_name": "Test Payee",
            "payment_address_line_1": "100 Test Ave",
            "payment_city": "Test", "payment_state": "FL",
            "payment_postal_code": "00000",
            "zelle_recipient": "demo-zelle@example.test",
        })
        self.handler_cls = make_handler(str(self.db_path))

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _handler(self, path, body=b""):
        handler = object.__new__(self.handler_cls)
        handler.path = path
        handler.headers = {
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
            self.handler_cls.write_token_header: self.handler_cls.write_token,
        }
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.send_error = lambda code: (_ for _ in ()).throw(AssertionError(f"unexpected error {code}"))
        captured = {}

        def mock_send_json(payload, status=200):
            captured["payload"] = payload
            captured["status"] = status

        handler.send_json = mock_send_json
        handler.send_response = lambda code: captured.setdefault("response_code", code)
        handler.send_header = lambda key, value: captured.setdefault("headers", {}).update({key: value})
        handler.end_headers = lambda: None
        handler._apply_security_headers = lambda nonce=None: None
        handler._security_headers_applied = True
        handler._apply_pdf_safe_headers = lambda: None
        handler.finish = lambda: None
        return handler, captured

    def _approved_session(self, key, start_at="2026-05-15T10:00:00-04:00", amount="150.00"):
        import_rows(self.conn, [raw_row(key, "Dana Testcase | 60 | Office", start_at)], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Dana Testcase"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office", "time_category": "standard",
            "approved_rate": amount,
            "payment_status": "unpaid", "billing_treatment": "billable",
        })
        return self.conn.execute("SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)).fetchone()

    def _draft(self, sessions, date_str="2026-05-31"):
        return create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_period_start": date_str[:7] + "-01",
            "billing_period_end": date_str,
            "invoice_date": date_str,
            "session_ids": [row["id"] for row in sessions],
        })

    def _finalize(self, draft):
        pdf_root = self.root / "Invoices"
        with patch("jordana_invoice.invoice_services.generate_invoice_pdf",
                   side_effect=lambda inv, lines, path, **kw: (
                       Path(path).parent.mkdir(parents=True, exist_ok=True),
                       Path(path).write_bytes(b"%PDF-1.4 fake"),
                       "a" * 64,
                   )[-1]):
            return finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=pdf_root)

    # ── 1. Draft with prior unpaid shows prior balance in HTML preview ──

    def test_print_preview_shows_prior_unpaid_balance(self):
        """The print-preview endpoint HTML must include prior unpaid balance
        when a prior finalized unpaid invoice exists for the same person."""
        # Finalize a prior invoice
        s1 = self._approved_session("prior1", "2026-04-10T10:00:00-04:00")
        d1 = self._draft([s1], "2026-04-30")
        self._finalize(d1)

        # Create a newer draft
        s2 = self._approved_session("newer1", "2026-05-15T10:00:00-04:00")
        d2 = self._draft([s2], "2026-05-31")

        handler, captured = self._handler(f"/api/invoices/{d2['invoice']['invoice_id']}/print-preview")
        handler.conn = lambda: self.conn
        handler.do_GET()

        self.assertEqual(captured.get("response_code"), 200)
        body = handler.wfile.getvalue().decode("utf-8")
        self.assertIn("Prior Unpaid Balance", body)
        self.assertIn("$150.00", body)
        self.assertIn("TOTAL AMOUNT DUE", body)
        self.assertIn("$300.00", body)

    # ── 2. Draft PDF render model receives the same summary ──────────

    def test_draft_pdf_endpoint_passes_account_summary(self):
        """The draft-PDF endpoint must pass account_summary into the render
        model so the PDF includes prior balance information."""
        s1 = self._approved_session("prior2", "2026-04-10T10:00:00-04:00")
        d1 = self._draft([s1], "2026-04-30")
        self._finalize(d1)

        s2 = self._approved_session("newer2", "2026-05-15T10:00:00-04:00")
        d2 = self._draft([s2], "2026-05-31")

        # Spy on build_invoice_render_model to capture the account_summary arg
        original = build_invoice_render_model
        captured_summary = {}

        def spy(*args, **kwargs):
            captured_summary["account_summary"] = kwargs.get("account_summary")
            return original(*args, **kwargs)

        with patch("jordana_invoice.review_server.build_invoice_render_model", side_effect=spy):
            handler, captured = self._handler(f"/api/invoices/{d2['invoice']['invoice_id']}/draft-pdf")
            handler.conn = lambda: self.conn
            handler.do_GET()

        self.assertEqual(captured.get("response_code"), 200)
        summary = captured_summary.get("account_summary")
        self.assertIsNotNone(summary, "account_summary was not passed to build_invoice_render_model")
        self.assertEqual(summary["prior_unpaid_balance_cents"], 15000)
        self.assertEqual(summary["total_amount_due_cents"], 30000)

    # ── 3. Current invoice total remains unchanged ────────────────────

    def test_draft_total_cents_excludes_prior_balance(self):
        """The invoice total_cents must remain current charges only."""
        s1 = self._approved_session("prior3", "2026-04-10T10:00:00-04:00")
        d1 = self._draft([s1], "2026-04-30")
        self._finalize(d1)

        s2 = self._approved_session("newer3", "2026-05-15T10:00:00-04:00")
        d2 = self._draft([s2], "2026-05-31")

        data = get_invoice(self.conn, d2["invoice"]["invoice_id"])
        self.assertEqual(data["invoice"]["total_cents"], 15000)
        rm = data["render_model"]
        self.assertEqual(rm["account_summary"]["current_invoice_total_cents"], 15000)
        self.assertEqual(rm["account_summary"]["prior_unpaid_balance_cents"], 15000)
        self.assertEqual(rm["account_summary"]["total_amount_due_cents"], 30000)

    # ── 4. No prior balance → renders normally ────────────────────────

    def test_print_preview_no_prior_balance_renders_normally(self):
        """A draft with no prior unpaid invoices must still render normally."""
        s = self._approved_session("noprior", "2026-05-15T10:00:00-04:00")
        draft = self._draft([s], "2026-05-31")

        handler, captured = self._handler(f"/api/invoices/{draft['invoice']['invoice_id']}/print-preview")
        handler.conn = lambda: self.conn
        handler.do_GET()

        self.assertEqual(captured.get("response_code"), 200)
        body = handler.wfile.getvalue().decode("utf-8")
        self.assertIn("Dana Testcase", body)
        self.assertIn("$150.00", body)
        # Prior Unpaid Balance should not appear when there is none
        self.assertNotIn("Prior Unpaid Balance", body)

    # ── 5. Finalized invoice behavior not regressed ───────────────────

    def test_finalized_invoice_preview_still_rejected(self):
        """Print preview must still reject finalized invoices."""
        s = self._approved_session("finalized1", "2026-05-15T10:00:00-04:00")
        draft = self._draft([s], "2026-05-31")
        self._finalize(draft)

        handler, captured = self._handler(f"/api/invoices/{draft['invoice']['invoice_id']}/print-preview")
        handler.conn = lambda: self.conn
        handler.do_GET()

        self.assertEqual(captured.get("status"), 400)
        self.assertIn("error", captured.get("payload", {}))

    def test_finalized_invoice_uses_snapshot_not_dynamic(self):
        """Finalized invoices must use the persisted account_summary_snapshot,
        not recalculate dynamically."""
        s1 = self._approved_session("snap1", "2026-04-10T10:00:00-04:00")
        d1 = self._draft([s1], "2026-04-30")
        self._finalize(d1)

        s2 = self._approved_session("snap2", "2026-05-15T10:00:00-04:00")
        d2 = self._draft([s2], "2026-05-31")
        self._finalize(d2)

        data = get_invoice(self.conn, d2["invoice"]["invoice_id"])
        # Finalized invoice should have a snapshot
        self.assertIsNotNone(data["as_finalized_summary"])
        # The snapshot should show the prior balance
        self.assertEqual(data["as_finalized_summary"]["prior_unpaid_balance_cents"], 15000)
        # The render_model should use the snapshot, not recalculate
        rm_summary = data["render_model"]["account_summary"]
        self.assertEqual(rm_summary["prior_unpaid_balance_cents"], 15000)


if __name__ == "__main__":
    unittest.main()
