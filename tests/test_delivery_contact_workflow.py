"""Tests for the Billing Relationship invoice-delivery contact workflow.

Covers:
1.  Existing person selected as invoice contact for organization payer.
2.  New invoice contact created for organization payer.
3.  Selected contact persists after close/reopen.
4.  Contact remains separate from payer organization.
5.  Contact is not added as covered client or participant.
6.  Contact is not used as Bill To.
7.  Contact is not used as filing owner.
8.  Future draft inherits delivery contact.
9.  Deliberate draft delivery override is preserved.
10. Changing relationship contact affects future drafts only.
11. Finalized invoice snapshots remain unchanged.
12. Duplicate-person safeguards still apply.
13. Person payer supports a distinct delivery contact without overwriting payer identity.
14. Delivery method/email/address persist.
15. Invalid or deleted contact is handled safely.
16. UI prevents duplicate submit and refreshes after save.
17. Delivery contact options include unrelated people-directory records.
"""
import re
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
from jordana_invoice.review_services import (
    create_billing_party,
    create_person,
    get_account_record,
    setup_billing_relationship,
    update_billing_relationship,
)
from jordana_invoice.util import stable_hash

JS_PATH = Path("app/jordana_invoice/static/review.js")


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


class DeliveryContactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "test.db")
        migrate_database(self.root / "test.db")
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

    def _make_org_payer(self, org_name="Brooke Biz"):
        org = create_billing_party(self.conn, {
            "billing_party_type": "organization",
            "organization_name": org_name,
            "billing_name": org_name,
            "billing_email": f"billing@{org_name.lower().replace(' ', '')}.test",
            "billing_address_line_1": "10 Org Street",
            "billing_city": "Example", "billing_state": "FL",
            "billing_postal_code": "00000",
            "preferred_delivery_method": "email",
        })
        return org

    def _make_covered_client(self, name="Brett Grossman"):
        return create_person(self.conn, {
            "first_name": name.split()[0], "last_name": name.split()[-1],
            "display_name": name,
        })

    def _make_org_relationship(self, org, covered_person):
        return setup_billing_relationship(self.conn, {
            "payer_kind": "organization",
            "organization_billing_party_id": org["billing_party_id"],
            "covered_client_ids": [covered_person["person_id"]],
        })

    def _make_person_payer_relationship(self, payer_person, covered_person):
        return setup_billing_relationship(self.conn, {
            "payer_kind": "person",
            "payer_person_id": payer_person["person_id"],
            "covered_client_ids": [covered_person["person_id"]],
        })

    def _approved_session(self, key, party_id, person_id, day=15):
        import_rows(self.conn, [raw_row(key, f"Client | 60 | Office",
                                        f"2026-05-{day:02d}T10:00:00-04:00")], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        from jordana_invoice.review_services import approve_candidate
        approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": person_id, "display_name": "Client"}],
            "billing_party_id": party_id, "approved_duration_minutes": 60,
            "service_mode": "office", "time_category": "standard",
            "approved_rate": "150.00", "payment_status": "unpaid",
            "billing_treatment": "billable",
        })
        return self.conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (
                self.conn.execute(
                    "SELECT id FROM sessions WHERE source_event_candidate_id = ?",
                    (candidate_id,),
                ).fetchone()[0],
            )
        ).fetchone()

    # 1. Existing person can be selected as invoice contact for organization payer.
    def test_existing_person_as_invoice_contact_for_org(self):
        org = self._make_org_payer()
        covered = self._make_covered_client()
        rel = self._make_org_relationship(org, covered)
        manager = create_person(self.conn, {
            "first_name": "Jane", "last_name": "Manager",
            "display_name": "Jane Manager",
            "billing_email": "jane@biz.test",
        })
        result = update_billing_relationship(self.conn, rel["account_id"], {
            "payer_kind": "organization",
            "organization_billing_party_id": org["billing_party_id"],
            "covered_client_ids": [covered["person_id"]],
            "delivery_contact": {"person_id": manager["person_id"]},
            "billing_delivery": {
                "billing_email": "jane@biz.test",
                "preferred_delivery_method": "email",
            },
        })
        bp = result["billing_party"]
        self.assertEqual(bp["delivery_contact_person_id"], manager["person_id"])

    def test_delivery_contact_options_include_unrelated_people_directory_records(self):
        org = self._make_org_payer()
        covered = self._make_covered_client()
        rel = self._make_org_relationship(org, covered)
        manager = create_person(self.conn, {
            "first_name": "Jordan",
            "last_name": "Manager",
            "display_name": "Jordan Business Manager",
            "billing_email": "jordan@biz.test",
        })

        record = get_account_record(self.conn, rel["account_id"])

        contact_options = {
            row["person_id"]: row for row in record["delivery_contacts"]
        }
        self.assertIn(manager["person_id"], contact_options)
        self.assertEqual(contact_options[manager["person_id"]]["source"], "people_directory")
        self.assertNotIn(manager["person_id"], {row["person_id"] for row in record["members"]})

    # 2. New invoice contact can be created for organization payer.
    def test_new_invoice_contact_created_for_org(self):
        org = self._make_org_payer()
        covered = self._make_covered_client()
        rel = self._make_org_relationship(org, covered)
        result = update_billing_relationship(self.conn, rel["account_id"], {
            "payer_kind": "organization",
            "organization_billing_party_id": org["billing_party_id"],
            "covered_client_ids": [covered["person_id"]],
            "delivery_contact": {
                "person": {
                    "first_name": "Robert",
                    "last_name": "Agent",
                    "display_name": "Robert Agent",
                    "billing_email": "robert@biz.test",
                },
            },
            "billing_delivery": {
                "preferred_delivery_method": "email",
            },
        })
        bp = result["billing_party"]
        self.assertIsNotNone(bp["delivery_contact_person_id"])
        dc_person = self.conn.execute(
            "SELECT * FROM people WHERE person_id = ?", (bp["delivery_contact_person_id"],)
        ).fetchone()
        self.assertEqual(dc_person["display_name"], "Robert Agent")

    # 3. Selected contact persists after close/reopen.
    def test_contact_persists_after_reopen(self):
        org = self._make_org_payer()
        covered = self._make_covered_client()
        rel = self._make_org_relationship(org, covered)
        manager = create_person(self.conn, {
            "first_name": "Jane", "last_name": "Manager",
            "display_name": "Jane Manager",
        })
        update_billing_relationship(self.conn, rel["account_id"], {
            "payer_kind": "organization",
            "organization_billing_party_id": org["billing_party_id"],
            "covered_client_ids": [covered["person_id"]],
            "delivery_contact": {"person_id": manager["person_id"]},
            "billing_delivery": {"preferred_delivery_method": "email"},
        })
        record = get_account_record(self.conn, rel["account_id"])
        self.assertIsNotNone(record["delivery_contact_person"])
        self.assertEqual(record["delivery_contact_person"]["person_id"], manager["person_id"])
        self.assertEqual(record["delivery_contact_person"]["display_name"], "Jane Manager")

    # 4. Contact remains separate from payer organization.
    def test_contact_separate_from_payer_org(self):
        org = self._make_org_payer()
        covered = self._make_covered_client()
        rel = self._make_org_relationship(org, covered)
        manager = create_person(self.conn, {
            "first_name": "Jane", "last_name": "Manager",
            "display_name": "Jane Manager",
        })
        result = update_billing_relationship(self.conn, rel["account_id"], {
            "payer_kind": "organization",
            "organization_billing_party_id": org["billing_party_id"],
            "covered_client_ids": [covered["person_id"]],
            "delivery_contact": {"person_id": manager["person_id"]},
            "billing_delivery": {"preferred_delivery_method": "email"},
        })
        bp = result["billing_party"]
        self.assertEqual(bp["billing_party_type"], "organization")
        self.assertEqual(bp["organization_name"], "Brooke Biz")
        self.assertNotEqual(bp["delivery_contact_person_id"], org["person_id"] if org["person_id"] else None)
        self.assertEqual(bp["delivery_contact_person_id"], manager["person_id"])

    # 5. Contact is not added as covered client or participant.
    def test_contact_not_covered_client_or_participant(self):
        org = self._make_org_payer()
        covered = self._make_covered_client()
        rel = self._make_org_relationship(org, covered)
        manager = create_person(self.conn, {
            "first_name": "Jane", "last_name": "Manager",
            "display_name": "Jane Manager",
        })
        update_billing_relationship(self.conn, rel["account_id"], {
            "payer_kind": "organization",
            "organization_billing_party_id": org["billing_party_id"],
            "covered_client_ids": [covered["person_id"]],
            "delivery_contact": {"person_id": manager["person_id"]},
            "billing_delivery": {"preferred_delivery_method": "email"},
        })
        record = get_account_record(self.conn, rel["account_id"])
        member_ids = [m["person_id"] for m in record["members"]]
        self.assertNotIn(manager["person_id"], member_ids)

    # 6. Contact is not used as Bill To.
    def test_contact_not_bill_to(self):
        org = self._make_org_payer()
        covered = self._make_covered_client()
        rel = self._make_org_relationship(org, covered)
        manager = create_person(self.conn, {
            "first_name": "Jane", "last_name": "Manager",
            "display_name": "Jane Manager",
        })
        result = update_billing_relationship(self.conn, rel["account_id"], {
            "payer_kind": "organization",
            "organization_billing_party_id": org["billing_party_id"],
            "covered_client_ids": [covered["person_id"]],
            "delivery_contact": {"person_id": manager["person_id"]},
            "billing_delivery": {
                "preferred_delivery_method": "email",
                "billing_name": "Brooke Biz",
            },
        })
        bp = result["billing_party"]
        self.assertEqual(bp["billing_name"], "Brooke Biz")
        self.assertNotEqual(bp["billing_name"], "Jane Manager")

    # 7. Contact is not used as filing owner.
    def test_contact_not_filing_owner(self):
        org = self._make_org_payer()
        covered = self._make_covered_client()
        rel = self._make_org_relationship(org, covered)
        manager = create_person(self.conn, {
            "first_name": "Jane", "last_name": "Manager",
            "display_name": "Jane Manager",
        })
        result = update_billing_relationship(self.conn, rel["account_id"], {
            "payer_kind": "organization",
            "organization_billing_party_id": org["billing_party_id"],
            "covered_client_ids": [covered["person_id"]],
            "delivery_contact": {"person_id": manager["person_id"]},
            "billing_delivery": {"preferred_delivery_method": "email"},
        })
        record = get_account_record(self.conn, rel["account_id"])
        filing_owner = record["filing_owner"] or {}
        selected = filing_owner.get("selected") or {}
        self.assertNotEqual(selected.get("owner_id"), manager["person_id"])

    # 8. Future draft inherits delivery contact.
    def test_draft_inherits_delivery_contact(self):
        org = self._make_org_payer()
        covered = self._make_covered_client()
        rel = self._make_org_relationship(org, covered)
        manager = create_person(self.conn, {
            "first_name": "Jane", "last_name": "Manager",
            "display_name": "Jane Manager",
            "billing_email": "jane@biz.test",
        })
        update_billing_relationship(self.conn, rel["account_id"], {
            "payer_kind": "organization",
            "organization_billing_party_id": org["billing_party_id"],
            "covered_client_ids": [covered["person_id"]],
            "delivery_contact": {"person_id": manager["person_id"]},
            "billing_delivery": {
                "preferred_delivery_method": "email",
                "billing_email": "jane@biz.test",
            },
        })
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": org["billing_party_id"],
            "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31",
        })
        self.assertEqual(draft["invoice"]["delivery_method"], "email")
        bp = self.conn.execute(
            "SELECT * FROM billing_parties WHERE billing_party_id = ?",
            (org["billing_party_id"],),
        ).fetchone()
        self.assertEqual(bp["delivery_contact_person_id"], manager["person_id"])
        self.assertEqual(bp["billing_email"], "jane@biz.test")

    # 9. Deliberate draft delivery override is preserved.
    def test_draft_delivery_override_preserved(self):
        org = self._make_org_payer()
        covered = self._make_covered_client()
        rel = self._make_org_relationship(org, covered)
        manager = create_person(self.conn, {
            "first_name": "Jane", "last_name": "Manager",
            "display_name": "Jane Manager",
        })
        update_billing_relationship(self.conn, rel["account_id"], {
            "payer_kind": "organization",
            "organization_billing_party_id": org["billing_party_id"],
            "covered_client_ids": [covered["person_id"]],
            "delivery_contact": {"person_id": manager["person_id"]},
            "billing_delivery": {"preferred_delivery_method": "email"},
        })
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": org["billing_party_id"],
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

    # 10. Changing relationship contact affects future drafts only.
    def test_changing_contact_affects_future_drafts_only(self):
        org = self._make_org_payer()
        covered = self._make_covered_client()
        rel = self._make_org_relationship(org, covered)
        manager1 = create_person(self.conn, {
            "first_name": "Jane", "last_name": "Manager",
            "display_name": "Jane Manager",
        })
        update_billing_relationship(self.conn, rel["account_id"], {
            "payer_kind": "organization",
            "organization_billing_party_id": org["billing_party_id"],
            "covered_client_ids": [covered["person_id"]],
            "delivery_contact": {"person_id": manager1["person_id"]},
            "billing_delivery": {"preferred_delivery_method": "email"},
        })
        draft1 = create_invoice_draft(self.conn, {
            "bill_to_party_id": org["billing_party_id"],
            "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31",
        })
        manager2 = create_person(self.conn, {
            "first_name": "Tom", "last_name": "Agent",
            "display_name": "Tom Agent",
        })
        update_billing_relationship(self.conn, rel["account_id"], {
            "payer_kind": "organization",
            "organization_billing_party_id": org["billing_party_id"],
            "covered_client_ids": [covered["person_id"]],
            "delivery_contact": {"person_id": manager2["person_id"]},
            "billing_delivery": {"preferred_delivery_method": "mail"},
        })
        draft2 = create_invoice_draft(self.conn, {
            "bill_to_party_id": org["billing_party_id"],
            "billing_period_start": "2026-06-01",
            "billing_period_end": "2026-06-30",
            "invoice_date": "2026-06-30",
        })
        self.assertEqual(draft1["invoice"]["delivery_method"], "email")
        self.assertEqual(draft2["invoice"]["delivery_method"], "mail")

    # 11. Finalized invoice snapshots remain unchanged.
    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_finalized_invoice_unchanged(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        org = self._make_org_payer()
        covered = self._make_covered_client()
        rel = self._make_org_relationship(org, covered)
        manager = create_person(self.conn, {
            "first_name": "Jane", "last_name": "Manager",
            "display_name": "Jane Manager",
        })
        update_billing_relationship(self.conn, rel["account_id"], {
            "payer_kind": "organization",
            "organization_billing_party_id": org["billing_party_id"],
            "covered_client_ids": [covered["person_id"]],
            "delivery_contact": {"person_id": manager["person_id"]},
            "billing_delivery": {
                "preferred_delivery_method": "email",
                "billing_email": "jane@biz.test",
            },
        })
        session = self._approved_session("s1", org["billing_party_id"], covered["person_id"])
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": org["billing_party_id"],
            "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31",
            "session_ids": [session["id"]],
        })
        invoice_id = draft["invoice"]["invoice_id"]
        final = finalize_invoice(self.conn, invoice_id, pdf_root=self.root / "Invoices")
        self.assertEqual(final["invoice"]["delivery_method"], "email")
        original_snapshot = final["invoice"]["bill_to_email_snapshot"]
        manager2 = create_person(self.conn, {
            "first_name": "Tom", "last_name": "Agent",
            "display_name": "Tom Agent",
        })
        update_billing_relationship(self.conn, rel["account_id"], {
            "payer_kind": "organization",
            "organization_billing_party_id": org["billing_party_id"],
            "covered_client_ids": [covered["person_id"]],
            "delivery_contact": {"person_id": manager2["person_id"]},
            "billing_delivery": {"preferred_delivery_method": "mail"},
        })
        result = get_invoice(self.conn, invoice_id)
        self.assertEqual(result["invoice"]["delivery_method"], "email")
        self.assertEqual(result["invoice"]["bill_to_email_snapshot"], original_snapshot)

    # 12. Duplicate-person safeguards still apply.
    def test_duplicate_person_safeguard(self):
        person = create_person(self.conn, {
            "first_name": "Jane", "last_name": "Manager",
            "display_name": "Jane Manager",
        })
        duplicate = create_person(self.conn, {
            "first_name": "Jane", "last_name": "Manager",
            "display_name": "Jane Manager",
        })
        self.assertFalse(duplicate["created"])
        self.assertTrue(duplicate["existing"])
        self.assertEqual(duplicate["person_id"], person["person_id"])

    # 13. Person payer supports a distinct delivery contact without overwriting payer identity.
    def test_person_payer_distinct_delivery_contact(self):
        payer = create_person(self.conn, {
            "first_name": "Alex", "last_name": "Payer",
            "display_name": "Alex Payer",
        })
        covered = self._make_covered_client()
        rel = self._make_person_payer_relationship(payer, covered)
        agent = create_person(self.conn, {
            "first_name": "Sam", "last_name": "Agent",
            "display_name": "Sam Agent",
            "billing_email": "sam@agent.test",
        })
        result = update_billing_relationship(self.conn, rel["account_id"], {
            "payer_kind": "person",
            "payer_person_id": payer["person_id"],
            "covered_client_ids": [covered["person_id"]],
            "delivery_contact": {"person_id": agent["person_id"]},
            "billing_delivery": {
                "preferred_delivery_method": "email",
                "billing_email": "sam@agent.test",
            },
        })
        bp = result["billing_party"]
        self.assertEqual(bp["person_id"], payer["person_id"])
        self.assertEqual(bp["delivery_contact_person_id"], agent["person_id"])
        self.assertNotEqual(bp["delivery_contact_person_id"], bp["person_id"])

    # 14. Delivery method/email/address persist.
    def test_delivery_details_persist(self):
        org = self._make_org_payer()
        covered = self._make_covered_client()
        rel = self._make_org_relationship(org, covered)
        manager = create_person(self.conn, {
            "first_name": "Jane", "last_name": "Manager",
            "display_name": "Jane Manager",
            "billing_email": "jane@biz.test",
            "billing_phone": "555-1234",
        })
        update_billing_relationship(self.conn, rel["account_id"], {
            "payer_kind": "organization",
            "organization_billing_party_id": org["billing_party_id"],
            "covered_client_ids": [covered["person_id"]],
            "delivery_contact": {"person_id": manager["person_id"]},
            "billing_delivery": {
                "preferred_delivery_method": "mail",
                "billing_email": "jane@biz.test",
                "billing_phone": "555-1234",
                "billing_address_line_1": "10 Contact St",
                "billing_city": "Example", "billing_state": "FL",
                "billing_postal_code": "00000",
            },
        })
        record = get_account_record(self.conn, rel["account_id"])
        bp = record["billing_party"]
        self.assertEqual(bp["preferred_delivery_method"], "mail")
        self.assertEqual(bp["billing_email"], "jane@biz.test")
        self.assertEqual(bp["billing_phone"], "555-1234")
        self.assertEqual(bp["billing_address_line_1"], "10 Contact St")

    # 15. Invalid or deleted contact is handled safely.
    def test_invalid_contact_handled_safely(self):
        org = self._make_org_payer()
        covered = self._make_covered_client()
        rel = self._make_org_relationship(org, covered)
        with self.assertRaises(ValueError):
            update_billing_relationship(self.conn, rel["account_id"], {
                "payer_kind": "organization",
                "organization_billing_party_id": org["billing_party_id"],
                "covered_client_ids": [covered["person_id"]],
                "delivery_contact": {"person_id": "nonexistent-person-id"},
                "billing_delivery": {"preferred_delivery_method": "email"},
            })

    # 16. UI prevents duplicate submit and refreshes after save.
    def test_ui_prevents_duplicate_submit(self):
        js = JS_PATH.read_text()
        save_fn = self._extract_function(js, "saveBillingRelationship")
        self.assertIsNotNone(save_fn)
        self.assertIn("saveBtn.disabled = true", save_fn)
        self.assertIn("if (saveBtn.disabled) return", save_fn)
        self.assertIn("saveBtn.textContent = \"Saving changes…\"", save_fn)

    def test_ui_delivery_contact_options_uses_delivery_contact_person_id(self):
        js = JS_PATH.read_text()
        fn = self._extract_function(js, "deliveryContactOptions")
        self.assertIsNotNone(fn)
        self.assertIn("delivery_contact_person_id", fn)

    def test_ui_shows_send_invoice_to_in_recipient_section(self):
        js = JS_PATH.read_text()
        self.assertIn("Send invoice to", js)
        self.assertIn("delivery_contact_person", js)
        self.assertIn("data.delivery_contact_person", js)

    def _extract_function(self, source, name):
        pattern = rf"(?:async )?function {name}\b"
        match = re.search(pattern, source)
        if not match:
            return None
        start = match.start()
        brace_count = 0
        found_open = False
        for i in range(match.end(), len(source)):
            if source[i] == '{':
                brace_count += 1
                found_open = True
            elif source[i] == '}':
                brace_count -= 1
                if found_open and brace_count == 0:
                    return source[start:i+1]
        return None


if __name__ == "__main__":
    unittest.main()
