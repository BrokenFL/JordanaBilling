import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, init_db, migrate_database
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import (
    create_invoice_draft,
    finalize_invoice,
    get_invoice,
    save_business_profile,
    stage_approved_sessions_to_monthly_drafts,
    synchronize_draft_delivery_method,
    update_invoice_draft,
)
from jordana_invoice.review_services import approve_candidate, create_billing_party, create_person
from jordana_invoice.util import stable_hash


def raw_row(key, title, start):
    return {
        "ingested_at": "2026-08-20T12:00:00Z", "snapshot_key": key,
        "run_id": f"run-{key}", "batch_name": "delivery-demo",
        "capture_window": "past_7_days", "captured_at": "2026-08-20T12:00:00Z",
        "source_device": "test", "timezone": "America/New_York",
        "calendar_event_id": f"event-{key}", "event_fingerprint": f"fp-{key}",
        "event_title": title, "start_at": start,
        "end_at": start[:11] + "11:00:00-04:00", "duration_minutes": "60",
        "calendar": "Jordana Work", "payload_version": "2", "raw_json": "{}",
    }


class InvoiceDeliveryDefaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "delivery.sqlite3")
        migrate_database(self.root / "delivery.sqlite3")
        self.person = create_person(self.conn, {
            "first_name": "Avery", "last_name": "Stone", "display_name": "Avery Stone",
        })
        save_business_profile(self.conn, {
            "business_name": "Demo Practice", "provider_display_name": "Demo Provider",
            "address_line_1": "100 Example Avenue", "city": "Example",
            "state": "FL", "postal_code": "00000", "phone": "555-0100",
            "email": "billing@example.test", "payee_name": "Demo Payee",
            "payment_address_line_1": "100 Example Avenue", "payment_city": "Example",
            "payment_state": "FL", "payment_postal_code": "00000",
            "zelle_recipient": "demo-zelle@example.test",
            "invoice_total_label": "TOTAL DUE", "invoice_number_format": "YYYY-NNNN",
        })

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _make_party(self, delivery):
        return create_billing_party(self.conn, {
            "billing_name": "Avery Stone", "person_id": self.person["person_id"],
            "billing_email": "avery@example.test",
            "billing_address_line_1": "10 Sample Street",
            "billing_city": "Example", "billing_state": "FL",
            "billing_postal_code": "00000",
            "preferred_delivery_method": delivery,
        })

    def _approved_session(self, key, party_id, day=15):
        import_rows(self.conn, [raw_row(key, "Avery Stone | 60 | Office",
                                        f"2026-05-{day:02d}T10:00:00-04:00")], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": self.person["person_id"],
                              "display_name": "Avery Stone"}],
            "billing_party_id": party_id, "approved_duration_minutes": 60,
            "service_mode": "office", "time_category": "standard",
            "approved_rate": "150.00", "payment_status": "unpaid",
            "billing_treatment": "billable",
        })
        return self.conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)
        ).fetchone()

    # 1. Email preference inherited on manual draft creation
    def test_draft_inherits_email_preference(self):
        party = self._make_party("email")
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": party["billing_party_id"],
            "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31",
        })
        self.assertEqual(draft["invoice"]["delivery_method"], "email")

    # 2. Mail preference inherited on manual draft creation
    def test_draft_inherits_mail_preference(self):
        party = self._make_party("mail")
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": party["billing_party_id"],
            "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31",
        })
        self.assertEqual(draft["invoice"]["delivery_method"], "mail")

    # 3. Both preference inherited on manual draft creation
    def test_draft_inherits_both_preference(self):
        party = self._make_party("both")
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": party["billing_party_id"],
            "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31",
        })
        self.assertEqual(draft["invoice"]["delivery_method"], "both")

    # 4. Missing preference stays unresolved
    def test_draft_missing_preference_stays_unresolved(self):
        party = self._make_party("unresolved")
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": party["billing_party_id"],
            "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31",
        })
        self.assertEqual(draft["invoice"]["delivery_method"], "unresolved")

    # 5. Explicit override takes precedence over saved preference
    def test_explicit_override_takes_precedence(self):
        party = self._make_party("email")
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": party["billing_party_id"],
            "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31",
            "delivery_method": "mail",
        })
        self.assertEqual(draft["invoice"]["delivery_method"], "mail")

    # 6. Invoice-specific override does not change the client's saved preference
    def test_override_does_not_change_saved_preference(self):
        party = self._make_party("email")
        create_invoice_draft(self.conn, {
            "bill_to_party_id": party["billing_party_id"],
            "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31",
            "delivery_method": "mail",
        })
        saved = self.conn.execute(
            "SELECT preferred_delivery_method FROM billing_parties WHERE billing_party_id = ?",
            (party["billing_party_id"],),
        ).fetchone()
        self.assertEqual(saved["preferred_delivery_method"], "email")

    # 7. Sending "unresolved" explicitly still inherits the saved preference
    def test_sending_unresolved_inherits_preference(self):
        party = self._make_party("email")
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": party["billing_party_id"],
            "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31",
            "delivery_method": "unresolved",
        })
        self.assertEqual(draft["invoice"]["delivery_method"], "email")

    # 8. Staged draft inherits delivery method from billing party
    def test_staged_draft_inherits_delivery_method(self):
        party = self._make_party("email")
        self._approved_session("s1", party["billing_party_id"])
        stage_approved_sessions_to_monthly_drafts(self.conn)
        draft = self.conn.execute(
            "SELECT * FROM invoices WHERE bill_to_party_id = ? AND billing_month = '2026-05' AND status = 'draft'",
            (party["billing_party_id"],),
        ).fetchone()
        self.assertIsNotNone(draft)
        self.assertEqual(draft["delivery_method"], "email")

    # 9. Re-resolving Bill To does not overwrite a deliberate override
    def test_synchronize_does_not_overwrite_deliberate_override(self):
        party = self._make_party("email")
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": party["billing_party_id"],
            "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31",
            "delivery_method": "mail",
        })
        invoice_id = draft["invoice"]["invoice_id"]
        changed = synchronize_draft_delivery_method(self.conn, invoice_id)
        self.assertFalse(changed)
        result = get_invoice(self.conn, invoice_id)
        self.assertEqual(result["invoice"]["delivery_method"], "mail")

    # 10. synchronize fills unresolved from saved preference
    def test_synchronize_fills_unresolved_from_preference(self):
        party = self._make_party("email")
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": party["billing_party_id"],
            "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31",
        })
        invoice_id = draft["invoice"]["invoice_id"]
        # Already inherited "email" from creation; sync should be no-op
        changed = synchronize_draft_delivery_method(self.conn, invoice_id)
        self.assertFalse(changed)

    # 11. synchronize fills unresolved when preference added later
    def test_synchronize_fills_when_preference_added_later(self):
        party = self._make_party("unresolved")
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": party["billing_party_id"],
            "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31",
        })
        invoice_id = draft["invoice"]["invoice_id"]
        self.assertEqual(draft["invoice"]["delivery_method"], "unresolved")
        # Now update the party's preference
        self.conn.execute(
            "UPDATE billing_parties SET preferred_delivery_method = 'mail' WHERE billing_party_id = ?",
            (party["billing_party_id"],),
        )
        self.conn.commit()
        changed = synchronize_draft_delivery_method(self.conn, invoice_id)
        self.assertTrue(changed)
        result = get_invoice(self.conn, invoice_id)
        self.assertEqual(result["invoice"]["delivery_method"], "mail")

    # 12. Finalization freezes the actual invoice delivery method
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_finalization_freezes_delivery_method(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        party = self._make_party("email")
        session = self._approved_session("s1", party["billing_party_id"])
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": party["billing_party_id"],
            "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31",
            "session_ids": [session["id"]],
        })
        invoice_id = draft["invoice"]["invoice_id"]
        # Override to mail before finalizing
        update_invoice_draft(self.conn, invoice_id, {"delivery_method": "mail"})
        final = finalize_invoice(self.conn, invoice_id, pdf_root=self.root / "Invoices")
        self.assertEqual(final["invoice"]["delivery_method"], "mail")
        # Change the party's preference after finalization
        self.conn.execute(
            "UPDATE billing_parties SET preferred_delivery_method = 'both' WHERE billing_party_id = ?",
            (party["billing_party_id"],),
        )
        self.conn.commit()
        result = get_invoice(self.conn, invoice_id)
        self.assertEqual(result["invoice"]["delivery_method"], "mail")

    # 13. Invalid delivery method still rejected
    def test_invalid_delivery_method_rejected(self):
        party = self._make_party("email")
        with self.assertRaises(ValueError):
            create_invoice_draft(self.conn, {
                "bill_to_party_id": party["billing_party_id"],
                "billing_period_start": "2026-05-01",
                "billing_period_end": "2026-05-31",
                "invoice_date": "2026-05-31",
                "delivery_method": "carrier_pigeon",
            })


if __name__ == "__main__":
    unittest.main()
