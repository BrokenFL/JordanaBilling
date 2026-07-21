import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import create_invoice_draft, get_invoice, save_business_profile
from jordana_invoice.review_services import (
    approve_candidate,
    create_billing_party,
    create_person,
    get_account_record,
    get_review_candidate,
    list_review_candidates,
    refresh_candidate_suggestions,
    save_billing_section,
    save_relationship_section,
    save_session_draft,
    setup_billing_relationship,
    update_billing_relationship,
)
from jordana_invoice.rates import seed_rate_rule


def raw_row(key, title="Avery Stone | 60 | Office", start="2026-05-10T10:00:00-04:00"):
    return {
        "ingested_at": "2026-06-22T02:00:00.000Z",
        "snapshot_key": key,
        "run_id": f"run-{key}",
        "batch_name": "test",
        "capture_window": "past_7_days",
        "captured_at": "2026-06-22T01:00:00.000Z",
        "source_device": "test",
        "timezone": "America/New_York",
        "calendar_event_id": f"event-{key}",
        "event_fingerprint": f"fp-{key}",
        "event_title": title,
        "start_at": start,
        "end_at": "2026-05-10T11:00:00-04:00",
        "duration_minutes": "60",
        "calendar": "Jordana Work",
        "payload_version": "2",
        "raw_json": "{}",
    }


class CleanAccountWorkflowFixesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "workflow.sqlite3")
        init_db(self.conn)
        self.client = create_person(self.conn, {
            "first_name": "Avery",
            "last_name": "Stone",
            "display_name": "Avery Stone",
        })
        self.other = create_person(self.conn, {
            "first_name": "Riley",
            "last_name": "North",
            "display_name": "Riley North",
        })
        self.org = create_billing_party(self.conn, {
            "billing_party_type": "organization",
            "organization_name": "Example Foundation",
            "billing_name": "Example Foundation",
            "billing_email": "billing@example.test",
            "preferred_delivery_method": "email",
        })
        self.other_org = create_billing_party(self.conn, {
            "billing_party_type": "organization",
            "organization_name": "Unrelated Foundation",
            "billing_name": "Unrelated Foundation",
            "billing_email": "other@example.test",
            "preferred_delivery_method": "email",
        })
        save_business_profile(self.conn, {
            "business_name": "Demo Practice",
            "provider_display_name": "Demo Provider",
            "address_line_1": "100 Example Avenue",
            "city": "Example",
            "state": "FL",
            "postal_code": "00000",
            "phone": "555-0100",
            "email": "billing@example.test",
            "payee_name": "Demo Payee",
            "payment_address_line_1": "100 Example Avenue",
            "payment_city": "Example",
            "payment_state": "FL",
            "payment_postal_code": "00000",
            "zelle_recipient": "demo-zelle@example.test",
        })
        seed_rate_rule(
            self.conn,
            amount_cents=35000,
            effective_from="2026-01-01",
            duration_minutes=60,
            billing_session_type="psychotherapy",
            time_category="standard",
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _candidate(self, key="session-1", title="Avery Stone | 60 | Office"):
        import_rows(self.conn, [raw_row(key, title)], "test")
        rows = list_review_candidates(self.conn)["items"]
        return next(row["candidate_id"] for row in rows if row["raw_title"] == title)

    def _confirmed_client_session(self, key="session-1"):
        candidate_id = self._candidate(key)
        return save_relationship_section(self.conn, candidate_id, {
            "participants": [{
                "person_id": self.client["person_id"],
                "display_name": self.client["display_name"],
                "is_primary": True,
            }],
            "primary_person_id": self.client["person_id"],
        })

    def test_existing_organization_relationship_becomes_eligible_bill_to_after_refresh(self):
        detail = self._confirmed_client_session()
        account = setup_billing_relationship(self.conn, {
            "payer_kind": "organization",
            "organization_billing_party_id": self.org["billing_party_id"],
            "covered_client_ids": [self.client["person_id"]],
        })
        refresh_candidate_suggestions(self.conn, detail["session"]["candidate_id"])
        self.conn.commit()
        refreshed = get_review_candidate(self.conn, detail["session"]["candidate_id"])
        option_ids = {row["billing_party_id"] for row in refreshed["bill_to_options"]}
        self.assertIn(self.org["billing_party_id"], option_ids)
        self.assertEqual(refreshed["effective_billing_party"]["billing_party_id"], self.org["billing_party_id"])

    def test_unrelated_organization_is_not_offered(self):
        detail = self._confirmed_client_session()
        account = setup_billing_relationship(self.conn, {
            "payer_kind": "organization",
            "organization_billing_party_id": self.other_org["billing_party_id"],
            "covered_client_ids": [self.other["person_id"]],
        })
        refreshed = get_review_candidate(self.conn, detail["session"]["candidate_id"])
        option_ids = {row["billing_party_id"] for row in refreshed["bill_to_options"]}
        self.assertNotIn(self.other_org["billing_party_id"], option_ids)

    def test_deliberate_and_approved_bill_to_are_not_rewritten_by_relationship_refresh(self):
        detail = self._confirmed_client_session()
        person_party_id = save_billing_section(self.conn, detail["session"]["candidate_id"], {
            "bill_to_person_id": self.client["person_id"],
        })["billing_party"]["billing_party_id"]
        account = setup_billing_relationship(self.conn, {
            "payer_kind": "organization",
            "organization_billing_party_id": self.org["billing_party_id"],
            "covered_client_ids": [self.client["person_id"]],
        })
        refresh_candidate_suggestions(self.conn, detail["session"]["candidate_id"])
        self.conn.commit()
        refreshed = get_review_candidate(self.conn, detail["session"]["candidate_id"])
        self.assertEqual(refreshed["billing_party"]["billing_party_id"], person_party_id)
        approve_candidate(self.conn, detail["session"]["candidate_id"], {
            "participants": [{"person_id": self.client["person_id"], "display_name": self.client["display_name"]}],
            "billing_party_id": person_party_id,
            "approved_duration_minutes": 60,
            "billing_session_type": "psychotherapy",
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "350.00",
            "payment_status": "unpaid",
            "appointment_status": "completed",
            "billing_treatment": "billable",
        })
        update_billing_relationship(self.conn, account["account_id"], {
            "payer_kind": "organization",
            "organization_billing_party_id": self.org["billing_party_id"],
            "covered_client_ids": [self.client["person_id"]],
        })
        approved = get_review_candidate(self.conn, detail["session"]["candidate_id"])
        self.assertEqual(approved["billing_party"]["billing_party_id"], person_party_id)

    def test_new_delivery_contact_is_created_linked_and_not_a_covered_client(self):
        account = setup_billing_relationship(self.conn, {
            "payer_kind": "organization",
            "organization_billing_party_id": self.org["billing_party_id"],
            "covered_client_ids": [self.client["person_id"]],
        })
        update_billing_relationship(self.conn, account["account_id"], {
            "payer_kind": "organization",
            "organization_billing_party_id": self.org["billing_party_id"],
            "covered_client_ids": [self.client["person_id"]],
            "delivery_contact": {
                "person": {
                    "first_name": "Jordan",
                    "last_name": "Contact",
                    "display_name": "Jordan Contact",
                    "billing_email": "jordan.contact@example.test",
                }
            },
        })
        record = get_account_record(self.conn, account["account_id"])
        linked = self.conn.execute(
            "SELECT * FROM billing_parties WHERE billing_party_id = ?",
            (self.org["billing_party_id"],),
        ).fetchone()
        contact = self.conn.execute(
            "SELECT * FROM people WHERE person_id = ?",
            (linked["person_id"],),
        ).fetchone()
        self.assertEqual(contact["display_name"], "Jordan Contact")
        self.assertEqual(linked["billing_email"], "jordan.contact@example.test")
        self.assertIn(contact["person_id"], {row["person_id"] for row in record["delivery_contacts"]})
        members = self.conn.execute(
            "SELECT person_id FROM account_members WHERE account_id = ?",
            (account["account_id"],),
        ).fetchall()
        self.assertNotIn(contact["person_id"], {row["person_id"] for row in members})

    def test_existing_delivery_contact_can_be_selected_for_person_payer_without_changing_payer(self):
        payer = create_person(self.conn, {
            "first_name": "Pat",
            "last_name": "Payer",
            "display_name": "Pat Payer",
        })
        contact = create_person(self.conn, {
            "first_name": "Casey",
            "last_name": "Contact",
            "display_name": "Casey Contact",
            "billing_email": "casey.contact@example.test",
        })
        account = setup_billing_relationship(self.conn, {
            "payer_kind": "person",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [self.client["person_id"]],
        })
        party_id = account["billing_party_id"]
        update_billing_relationship(self.conn, account["account_id"], {
            "payer_kind": "person",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [self.client["person_id"]],
            "delivery_contact": {"person_id": contact["person_id"]},
        })
        party = self.conn.execute(
            "SELECT * FROM billing_parties WHERE billing_party_id = ?",
            (party_id,),
        ).fetchone()
        self.assertEqual(party["person_id"], payer["person_id"])
        self.assertEqual(party["billing_email"], "casey.contact@example.test")

    def test_waived_late_cancellation_save_reload_approve_and_stage(self):
        detail = self._confirmed_client_session("late-1")
        setup_billing_relationship(self.conn, {
            "payer_kind": "organization",
            "organization_billing_party_id": self.org["billing_party_id"],
            "covered_client_ids": [self.client["person_id"]],
        })
        refresh_candidate_suggestions(self.conn, detail["session"]["candidate_id"])
        self.conn.commit()
        saved = save_session_draft(self.conn, detail["session"]["candidate_id"], {
            "approved_duration_minutes": 60,
            "billing_session_type": "psychotherapy",
            "service_mode": "office",
            "time_category": "standard",
            "suggested_rate": "350.00",
            "approved_rate": "0.00",
            "payment_status": "unpaid",
            "appointment_status": "late_cancellation",
            "billing_treatment": "waived",
        })
        self.assertEqual(saved["session"]["appointment_status"], "late_cancellation")
        self.assertEqual(saved["session"]["billing_treatment"], "waived")
        self.assertEqual(saved["session"]["approved_rate_cents"], 0)
        self.assertEqual(saved["session"]["suggested_rate_cents"], 35000)
        self.assertNotIn("billing_treatment", saved["session"]["unresolved_fields"])

        approved = approve_candidate(self.conn, detail["session"]["candidate_id"], {
            "participants": [{"person_id": self.client["person_id"], "display_name": self.client["display_name"]}],
            "billing_party_id": self.org["billing_party_id"],
            "approved_duration_minutes": 60,
            "billing_session_type": "psychotherapy",
            "service_mode": "office",
            "time_category": "standard",
            "suggested_rate": "350.00",
            "approved_rate": "0.00",
            "payment_status": "unpaid",
            "appointment_status": "late_cancellation",
            "billing_treatment": "waived",
        })
        self.assertEqual(approved["session"]["review_status"], "approved")
        self.assertEqual(approved["session"]["rate_cents_snapshot"], 0)
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.org["billing_party_id"],
            "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31",
            "session_ids": [approved["session"]["id"]],
        })
        invoice = get_invoice(self.conn, draft["invoice"]["invoice_id"])
        self.assertEqual(invoice["lines"][0]["line_amount_cents"], 0)
        self.assertEqual(invoice["invoice"]["total_cents"], 0)

    def test_zero_rates_remain_invalid_except_waived_late_cancellation(self):
        detail = self._confirmed_client_session("zero-1")
        saved = save_session_draft(self.conn, detail["session"]["candidate_id"], {
            "approved_duration_minutes": 60,
            "billing_session_type": "psychotherapy",
            "service_mode": "office",
            "time_category": "standard",
            "suggested_rate": "350.00",
            "approved_rate": "0.00",
            "payment_status": "unpaid",
            "appointment_status": "completed",
            "billing_treatment": "billable",
        })
        self.assertIn("approved_rate_cents", saved["session"]["unresolved_fields"])
        full_fee = save_session_draft(self.conn, detail["session"]["candidate_id"], {
            "approved_duration_minutes": 60,
            "billing_session_type": "psychotherapy",
            "service_mode": "office",
            "time_category": "standard",
            "suggested_rate": "350.00",
            "approved_rate": "0.00",
            "payment_status": "unpaid",
            "appointment_status": "late_cancellation",
            "billing_treatment": "bill_full_fee",
        })
        self.assertEqual(full_fee["session"]["approved_rate_cents"], 35000)
        self.assertEqual(full_fee["session"]["scheduled_rate_cents"], 35000)
        self.assertNotIn("approved_rate_cents", full_fee["session"]["unresolved_fields"])
        custom_fee = save_session_draft(self.conn, detail["session"]["candidate_id"], {
            "approved_duration_minutes": 60,
            "billing_session_type": "psychotherapy",
            "service_mode": "office",
            "time_category": "standard",
            "suggested_rate": "350.00",
            "approved_rate": "0.00",
            "payment_status": "unpaid",
            "appointment_status": "late_cancellation",
            "billing_treatment": "custom_fee",
        })
        self.assertIn("approved_rate_cents", custom_fee["session"]["unresolved_fields"])

    def test_cancelled_not_billable_zero_rate_is_valid(self):
        detail = self._confirmed_client_session("cancel-1")
        saved = save_session_draft(self.conn, detail["session"]["candidate_id"], {
            "approved_duration_minutes": 60,
            "billing_session_type": "psychotherapy",
            "service_mode": "office",
            "time_category": "standard",
            "suggested_rate": "350.00",
            "approved_rate": "0.00",
            "payment_status": "unpaid",
            "appointment_status": "cancelled",
            "billing_treatment": "not_billable",
        })
        self.assertEqual(saved["session"]["appointment_status"], "cancelled")
        self.assertEqual(saved["session"]["billing_treatment"], "not_billable")
        self.assertEqual(saved["session"]["approved_rate_cents"], 0)
        self.assertNotIn("approved_rate_cents", saved["session"]["unresolved_fields"])


if __name__ == "__main__":
    unittest.main()
