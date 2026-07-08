import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import (
    add_sessions_to_draft,
    create_invoice_draft,
    finalize_invoice,
    save_business_profile,
    void_invoice,
)
from jordana_invoice.rates import seed_rate_rule
from jordana_invoice.review_services import (
    approve_candidate,
    create_account,
    create_billing_party,
    create_person,
    create_rate_rule_from_payload,
    get_person_record,
    get_review_candidate,
    list_billing_relationship_records,
    list_review_candidates,
    recalc_unapproved_session_rates,
    refresh_candidate_suggestions,
    return_approved_session_to_review,
    save_person_alias,
    save_billing_section,
    save_relationship_section,
    save_interpretation,
    save_session_draft,
    setup_billing_relationship,
    update_billing_relationship,
)


def raw_row(snapshot_key, title="Bobsey and Fred 6", start="2026-06-17T18:00:00-04:00"):
    return {
        "ingested_at": "2026-06-22T02:00:00.000Z",
        "snapshot_key": snapshot_key,
        "run_id": "run-1",
        "batch_name": "test",
        "capture_window": "next_2_days",
        "captured_at": "2026-06-22T01:00:00.000Z",
        "source_device": "test",
        "timezone": "America/New_York",
        "calendar_event_id": "",
        "event_fingerprint": f"fp-{snapshot_key}",
        "event_title": title,
        "start_at": start,
        "end_at": "2026-06-17T19:00:00-04:00",
        "duration_minutes": "60",
        "calendar": "Jordana Calendar",
        "payload_version": "2",
        "raw_json": "{}",
    }


class ReviewServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "review.sqlite3")
        init_db(self.conn)
        import_rows(self.conn, [raw_row("snap-1")], "test")
        first = list_review_candidates(self.conn)["items"][0]
        self.candidate_id = first["candidate_id"]

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_inline_create_records_and_select_immediately(self):
        fred = create_person(self.conn, "Fred Smith")
        bobsey = create_person(self.conn, "Bobsey Smith")
        account = create_account(self.conn, "Fred Household", "household")
        payer = create_billing_party(self.conn, {"billing_name": "Fred Smith", "billing_party_type": "person", "person_id": fred["person_id"], "preferred_delivery_method": "email", "billing_email": "fred@example.test"})
        saved = save_interpretation(
            self.conn,
            self.candidate_id,
            {
                "participants": [
                    {"person_id": fred["person_id"], "display_name": "Fred Smith", "is_primary": True},
                    {"person_id": bobsey["person_id"], "display_name": "Bobsey Smith"},
                ],
                "account_id": account["account_id"],
                "billing_party_id": payer["billing_party_id"],
                "approved_duration_minutes": 60,
                "service_mode": "office",
                "time_category": "standard",
                "approved_rate": "150.00",
                "payment_status": "unpaid",
            },
        )
        self.assertEqual(len(saved["participants"]), 2)
        self.assertEqual(saved["account"]["account_name"], "Fred Household")
        self.assertEqual(saved["billing_party"]["billing_name"], "Fred Smith")

    def test_approval_saves_alias_and_one_charge_for_two_people(self):
        fred = create_person(self.conn, "Fred Smith")
        bobsey = create_person(self.conn, "Bobsey Smith")
        account = create_account(self.conn, "Fred Household", "household")
        payer = create_billing_party(self.conn, {"billing_name": "Fred Smith", "billing_party_type": "person"})
        approved = approve_candidate(
            self.conn,
            self.candidate_id,
            {
                "participants": [
                    {"person_id": fred["person_id"], "display_name": "Fred Smith", "is_primary": True},
                    {"person_id": bobsey["person_id"], "display_name": "Bobsey Smith"},
                ],
                "account_id": account["account_id"],
                "billing_party_id": payer["billing_party_id"],
                "approved_duration_minutes": 60,
                "service_mode": "office",
                "time_category": "standard",
                "approved_rate": "150.00",
                "payment_status": "unpaid",
            },
        )
        self.assertEqual(approved["session"]["review_status"], "approved")
        self.assertEqual(count(self.conn, "sessions"), 1)
        self.assertEqual(count(self.conn, "session_participants"), 2)
        self.assertGreaterEqual(count(self.conn, "calendar_aliases"), 1)
        self.assertGreater(count(self.conn, "audit_log"), 0)

    def test_approved_session_rejects_relationship_and_bill_to_edits(self):
        fred = create_person(self.conn, "Fred Smith")
        payer = create_billing_party(
            self.conn,
            {"billing_name": "Fred Smith", "billing_party_type": "person", "person_id": fred["person_id"]},
        )
        approved = approve_candidate(
            self.conn,
            self.candidate_id,
            {
                "participants": [{"person_id": fred["person_id"], "display_name": "Fred Smith", "is_primary": True}],
                "billing_party_id": payer["billing_party_id"],
                "approved_duration_minutes": 60,
                "service_mode": "office",
                "time_category": "standard",
                "approved_rate": "150.00",
                "payment_status": "unpaid",
            },
        )
        self.assertEqual(approved["session"]["review_status"], "approved")

        with self.assertRaisesRegex(ValueError, "Approved sessions cannot change Billing Relationship"):
            save_relationship_section(
                self.conn,
                self.candidate_id,
                {"participants": [{"person_id": fred["person_id"], "display_name": "Fred Smith", "is_primary": True}]},
            )
        with self.assertRaisesRegex(ValueError, "Approved sessions cannot change Bill To"):
            save_billing_section(self.conn, self.candidate_id, {"billing_party_id": payer["billing_party_id"]})

    def _approved_return_fixture(self, *, payment_status="unpaid"):
        fred = create_person(self.conn, "Fred Smith")
        payer = create_billing_party(
            self.conn,
            {"billing_name": "Fred Smith", "billing_party_type": "person", "person_id": fred["person_id"]},
        )
        approved = approve_candidate(
            self.conn,
            self.candidate_id,
            {
                "participants": [{"person_id": fred["person_id"], "display_name": "Fred Smith", "is_primary": True}],
                "billing_party_id": payer["billing_party_id"],
                "approved_duration_minutes": 60,
                "service_mode": "office",
                "time_category": "standard",
                "approved_rate": "150.00",
                "payment_status": "unpaid",
            },
        )
        if payment_status != "unpaid":
            self.conn.execute(
                "UPDATE sessions SET payment_status = ? WHERE id = ?",
                (payment_status, approved["session"]["id"]),
            )
            self.conn.commit()
            approved = get_review_candidate(self.conn, self.candidate_id)
        return fred, payer, approved

    def test_return_approved_session_to_review_without_reason_preserves_values_and_audits(self):
        _, payer, approved = self._approved_return_fixture()
        session_id = approved["session"]["id"]
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": payer["billing_party_id"],
            "billing_period_start": "2026-06-01",
            "billing_period_end": "2026-06-30",
            "invoice_date": "2026-06-30",
            "session_ids": [session_id],
        })
        self.assertEqual(draft["invoice"]["total_cents"], 15000)
        draft_revision_before = draft["invoice"]["revision"]

        result = return_approved_session_to_review(
            self.conn,
            self.candidate_id,
        )

        self.assertTrue(result["returned_to_review"])
        self.assertEqual(result["session"]["review_status"], "needs_review")
        self.assertEqual(result["session"]["billing_party_id"], payer["billing_party_id"])
        self.assertEqual(result["session"]["approved_rate_cents"], 15000)
        self.assertEqual(result["session"]["approved_duration_minutes"], 60)
        self.assertEqual(result["session"]["payment_status"], "unpaid")
        self.assertEqual(len(result["participants"]), 1)
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM invoice_line_items WHERE source_session_id = ?",
            (session_id,),
        ).fetchone())
        refreshed_draft = self.conn.execute(
            "SELECT total_cents, revision FROM invoices WHERE invoice_id = ?",
            (draft["invoice"]["invoice_id"],),
        ).fetchone()
        self.assertEqual(refreshed_draft["total_cents"], 0)
        self.assertEqual(refreshed_draft["revision"], draft_revision_before + 1)
        audit = self.conn.execute(
            "SELECT details FROM audit_log WHERE entity_type = 'session' AND entity_id = ? AND action = 'returned_to_review'",
            (session_id,),
        ).fetchone()
        self.assertIsNotNone(audit)
        self.assertIn("System correction: approved unfinalized session opened for editing.", audit["details"])
        self.assertIn('"reason_required":false', audit["details"])
        queue = list_review_candidates(self.conn)
        self.assertTrue(any(row["candidate_id"] == self.candidate_id for row in queue["items"]))

    def test_return_approved_session_still_records_typed_reason_when_supplied(self):
        _, _, approved = self._approved_return_fixture()

        return_approved_session_to_review(
            self.conn,
            self.candidate_id,
            reason="Correct Bill To before billing",
        )

        audit = self.conn.execute(
            "SELECT details FROM audit_log WHERE entity_type = 'session' AND entity_id = ? AND action = 'returned_to_review'",
            (approved["session"]["id"],),
        ).fetchone()
        self.assertIsNotNone(audit)
        self.assertIn("Correct Bill To before billing", audit["details"])

    def test_return_session_already_in_review_cleans_up_stale_draft_line(self):
        _, payer, approved = self._approved_return_fixture()
        session_id = approved["session"]["id"]
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": payer["billing_party_id"],
            "billing_period_start": "2026-06-01",
            "billing_period_end": "2026-06-30",
            "invoice_date": "2026-06-30",
            "session_ids": [session_id],
        })
        self.conn.execute(
            "UPDATE sessions SET review_status = 'needs_review', billable_status = 'proposed' WHERE id = ?",
            (session_id,),
        )
        self.conn.execute(
            "UPDATE calendar_event_candidates SET review_status = 'needs_review' WHERE id = ?",
            (self.candidate_id,),
        )
        self.conn.commit()

        result = return_approved_session_to_review(self.conn, self.candidate_id, reason="Continue edit")

        self.assertTrue(result["already_in_review"])
        self.assertEqual(result["session"]["review_status"], "needs_review")
        self.assertEqual(result["draft_invoice_ids_removed"], [draft["invoice"]["invoice_id"]])
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM invoice_line_items WHERE source_session_id = ?",
            (session_id,),
        ).fetchone())
        refreshed_draft = self.conn.execute(
            "SELECT total_cents FROM invoices WHERE invoice_id = ?",
            (draft["invoice"]["invoice_id"],),
        ).fetchone()
        self.assertEqual(refreshed_draft["total_cents"], 0)
        audit = self.conn.execute(
            "SELECT details FROM audit_log WHERE entity_type = 'session' AND entity_id = ? AND action = 'draft_invoice_line_removed_for_review'",
            (session_id,),
        ).fetchone()
        self.assertIsNotNone(audit)
        self.assertIn("Continue edit", audit["details"])

    def test_return_approved_session_removes_only_selected_draft_line(self):
        fred, payer, approved = self._approved_return_fixture()
        first_session_id = approved["session"]["id"]
        import_rows(
            self.conn,
            [raw_row("snap-return-draft-other", title="Fred 7", start="2026-06-18T19:00:00-04:00")],
            "test",
        )
        other_candidate = [
            row["candidate_id"]
            for row in list_review_candidates(self.conn)["items"]
            if row["candidate_id"] != self.candidate_id
        ][0]
        second = approve_candidate(
            self.conn,
            other_candidate,
            {
                "participants": [{"person_id": fred["person_id"], "display_name": "Fred Smith", "is_primary": True}],
                "billing_party_id": payer["billing_party_id"],
                "approved_duration_minutes": 60,
                "service_mode": "office",
                "time_category": "standard",
                "approved_rate": "175.00",
                "payment_status": "unpaid",
            },
        )
        second_session_id = second["session"]["id"]
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": payer["billing_party_id"],
            "billing_period_start": "2026-06-01",
            "billing_period_end": "2026-06-30",
            "invoice_date": "2026-06-30",
            "session_ids": [first_session_id, second_session_id],
        })

        return_approved_session_to_review(self.conn, self.candidate_id, reason="Correct first session")

        remaining_lines = self.conn.execute(
            "SELECT source_session_id, line_amount_cents FROM invoice_line_items WHERE invoice_id = ?",
            (draft["invoice"]["invoice_id"],),
        ).fetchall()
        self.assertEqual([(row["source_session_id"], row["line_amount_cents"]) for row in remaining_lines], [(second_session_id, 17500)])
        refreshed_draft = self.conn.execute(
            "SELECT total_cents FROM invoices WHERE invoice_id = ?",
            (draft["invoice"]["invoice_id"],),
        ).fetchone()
        self.assertEqual(refreshed_draft["total_cents"], 17500)
        other = self.conn.execute("SELECT review_status FROM sessions WHERE id = ?", (second_session_id,)).fetchone()
        self.assertEqual(other["review_status"], "approved")

    def test_return_approved_session_requires_reapproval_and_leaves_other_sessions(self):
        _, _, approved = self._approved_return_fixture()
        import_rows(self.conn, [raw_row("snap-return-other", title="Fred 7", start="2026-06-18T19:00:00-04:00")], "test")
        other_candidate = [row for row in list_review_candidates(self.conn)["items"] if row["candidate_id"] != self.candidate_id][0]["candidate_id"]
        fred2 = create_person(self.conn, "Frederick Other")
        payer2 = create_billing_party(self.conn, {"billing_name": "Frederick Other", "billing_party_type": "person", "person_id": fred2["person_id"]})
        approve_candidate(self.conn, other_candidate, {
            "participants": [{"person_id": fred2["person_id"], "display_name": "Frederick Other", "is_primary": True}],
            "billing_party_id": payer2["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "175.00",
            "payment_status": "unpaid",
        })

        return_approved_session_to_review(self.conn, self.candidate_id, reason="Correct participant")

        first = self.conn.execute("SELECT review_status FROM sessions WHERE id = ?", (approved["session"]["id"],)).fetchone()
        other = self.conn.execute("SELECT review_status FROM sessions WHERE candidate_id = ?", (other_candidate,)).fetchone()
        self.assertEqual(first["review_status"], "needs_review")
        self.assertEqual(other["review_status"], "approved")

    def test_return_approved_session_blocks_finalized_invoice(self):
        _, payer, approved = self._approved_return_fixture()
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": payer["billing_party_id"],
            "billing_period_start": "2026-06-01",
            "billing_period_end": "2026-06-30",
            "invoice_date": "2026-06-30",
            "session_ids": [approved["session"]["id"]],
        })
        self.conn.execute("UPDATE invoices SET status = 'finalized' WHERE invoice_id = ?", (draft["invoice"]["invoice_id"],))
        self.conn.commit()

        with self.assertRaisesRegex(ValueError, "finalized invoice"):
            return_approved_session_to_review(self.conn, self.candidate_id, reason="Correct Bill To")

    def test_return_approved_session_blocks_actual_payment_transaction(self):
        _, payer, approved = self._approved_return_fixture()
        self.conn.execute(
            """INSERT INTO payments (
              payment_id, billing_party_id, amount_cents, received_at, method, status,
              source_type, source_session_id, created_at, updated_at
            ) VALUES ('pay-direct', ?, 15000, '2026-06-18', 'check', 'posted',
              'paid_at_session_backfill', ?, '2026-06-18T12:00:00Z', '2026-06-18T12:00:00Z')""",
            (payer["billing_party_id"], approved["session"]["id"]),
        )
        self.conn.commit()

        with self.assertRaisesRegex(ValueError, "actual payment transaction"):
            return_approved_session_to_review(self.conn, self.candidate_id, reason="Correct rate")

    def test_return_approved_session_blocks_payment_allocation_and_no_partial_change(self):
        _, payer, approved = self._approved_return_fixture()
        session_id = approved["session"]["id"]
        self.conn.execute(
            """INSERT INTO payments (
              payment_id, billing_party_id, amount_cents, received_at, method, status,
              source_type, created_at, updated_at
            ) VALUES ('pay-alloc', ?, 15000, '2026-06-18', 'check', 'posted',
              'manual', '2026-06-18T12:00:00Z', '2026-06-18T12:00:00Z')""",
            (payer["billing_party_id"],),
        )
        self.conn.execute(
            """INSERT INTO payment_allocations (
              allocation_id, payment_id, session_id, amount_cents, status, created_at, updated_at
            ) VALUES ('alloc-1', 'pay-alloc', ?, 15000, 'active', '2026-06-18T12:00:00Z', '2026-06-18T12:00:00Z')""",
            (session_id,),
        )
        self.conn.commit()

        before_audit = count(self.conn, "audit_log")
        with self.assertRaisesRegex(ValueError, "payment allocation"):
            return_approved_session_to_review(self.conn, self.candidate_id, reason="Correct rate")
        session = self.conn.execute("SELECT review_status FROM sessions WHERE id = ?", (session_id,)).fetchone()
        self.assertEqual(session["review_status"], "approved")
        self.assertEqual(count(self.conn, "audit_log"), before_audit)

    def test_return_approved_session_blocks_receipt(self):
        _, payer, approved = self._approved_return_fixture()
        session_id = approved["session"]["id"]
        self.conn.execute(
            """INSERT INTO payments (
              payment_id, billing_party_id, amount_cents, received_at, method, status,
              source_type, source_session_id, created_at, updated_at
            ) VALUES ('pay-receipt', ?, 15000, '2026-06-18', 'check', 'posted',
              'paid_at_session_backfill', ?, '2026-06-18T12:00:00Z', '2026-06-18T12:00:00Z')""",
            (payer["billing_party_id"], session_id),
        )
        self.conn.execute(
            """INSERT INTO payment_receipts (
              receipt_id, payment_id, receipt_number, status, payment_received_at,
              amount_cents, snapshot_json, pdf_path, pdf_sha256, created_at, updated_at
            ) VALUES ('receipt-1', 'pay-receipt', 'R-1', 'finalized', '2026-06-18',
              15000, '{}', '/tmp/demo-receipt.pdf', 'abc123',
              '2026-06-18T12:00:00Z', '2026-06-18T12:00:00Z')"""
        )
        self.conn.commit()

        with self.assertRaisesRegex(ValueError, "receipt"):
            return_approved_session_to_review(self.conn, self.candidate_id, reason="Correct rate")

    def test_payment_status_label_alone_does_not_block_return(self):
        _, _, approved = self._approved_return_fixture(payment_status="paid_at_session")

        result = return_approved_session_to_review(
            self.conn,
            self.candidate_id,
            reason="Payment status was selected before actual payment recording",
        )

        self.assertEqual(result["session"]["review_status"], "needs_review")
        self.assertEqual(result["session"]["payment_status"], "paid_at_session")
        payments = self.conn.execute(
            "SELECT 1 FROM payments WHERE source_session_id = ?",
            (approved["session"]["id"],),
        ).fetchall()
        self.assertEqual(payments, [])

    def test_approval_fails_when_required_fields_missing(self):
        with self.assertRaises(ValueError):
            approve_candidate(self.conn, self.candidate_id, {"participants": []})

    def test_parser_candidate_appears_as_proposed_participant_without_creating_person(self):
        candidate_id = self.import_without_persisted_participants("snap-leah", "Leah Grossman 630 30")
        detail = get_review_candidate(self.conn, candidate_id)

        self.assertEqual(detail["participants"][0]["display_name"], "Leah Grossman")
        self.assertTrue(detail["participants"][0]["is_proposed"])
        self.assertIsNone(detail["participants"][0]["person_id"])
        self.assertEqual(count(self.conn, "people"), 0)

    def test_one_exact_match_auto_links_proposed_participant(self):
        candidate_id = self.import_without_persisted_participants("snap-exact-match", "Leah Grossman 630 30")
        person = create_person(self.conn, {"first_name": "Leah", "last_name": "Grossman", "display_name": "Leah Grossman"})

        detail = get_review_candidate(self.conn, candidate_id)

        self.assertEqual(detail["participants"][0]["person_id"], person["person_id"])
        self.assertEqual(detail["participants"][0]["display_name"], "Leah Grossman")
        self.assertFalse(detail["participants"][0].get("is_proposed", False))
        self.assertEqual(count(self.conn, "people"), 1)

    def test_case_and_extra_space_exact_match_auto_links(self):
        candidate_id = self.import_without_persisted_participants("snap-spaces", "Leah Grossman 630 30")
        person = create_person(self.conn, {"first_name": "Leah", "last_name": "Grossman", "display_name": "  leah   grossman  "})

        detail = get_review_candidate(self.conn, candidate_id)

        self.assertEqual(detail["participants"][0]["person_id"], person["person_id"])
        self.assertFalse(detail["participants"][0].get("is_proposed", False))

    def test_multiple_exact_matches_do_not_auto_link(self):
        candidate_id = self.import_without_persisted_participants("snap-duplicate-match", "Leah Grossman 630 30")
        first = create_person(self.conn, {"first_name": "Leah", "last_name": "Grossman", "display_name": "Leah Grossman"})
        self.conn.execute(
            """
            INSERT INTO people (
              person_id, display_name, first_name, last_name, active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            ("duplicate-leah", "Leah   Grossman", "Leah", "Grossman", "2026-06-23T00:00:00Z", "2026-06-23T00:00:00Z"),
        )
        self.conn.commit()

        detail = get_review_candidate(self.conn, candidate_id)
        saved = save_relationship_section(self.conn, candidate_id, {"participants": detail["participants"]})

        self.assertEqual(first["display_name"], "Leah Grossman")
        self.assertIsNone(detail["participants"][0]["person_id"])
        self.assertTrue(detail["participants"][0]["is_proposed"])
        self.assertIsNone(saved["participants"][0]["person_id"])
        self.assertEqual(count(self.conn, "people"), 2)

    def test_confirming_exact_existing_person_links_without_duplication(self):
        candidate_id = self.import_without_persisted_participants("snap-existing", "Leah Grossman 630 30")
        person = create_person(self.conn, {"first_name": "Leah", "last_name": "Grossman", "display_name": "Leah Grossman"})
        detail = get_review_candidate(self.conn, candidate_id)

        saved = save_relationship_section(self.conn, candidate_id, {"participants": detail["participants"]})

        self.assertEqual(saved["participants"][0]["person_id"], person["person_id"])
        self.assertEqual(count(self.conn, "people"), 1)

    def test_confirming_new_complete_name_creates_person_once_and_links(self):
        candidate_id = self.import_without_persisted_participants("snap-new", "Leah Grossman 630 30")
        detail = get_review_candidate(self.conn, candidate_id)

        first = save_relationship_section(self.conn, candidate_id, {"participants": detail["participants"]})
        second = save_relationship_section(self.conn, candidate_id, {"participants": first["participants"]})

        self.assertEqual(count(self.conn, "people"), 1)
        self.assertEqual(first["participants"][0]["person_id"], second["participants"][0]["person_id"])
        person = self.conn.execute("SELECT * FROM people").fetchone()
        self.assertEqual(person["display_name"], "Leah Grossman")
        self.assertTrue(person["person_code"])

    def test_incomplete_or_ambiguous_name_stays_uncoded_session_participant(self):
        candidate_id = self.import_without_persisted_participants("snap-short", "Fred 630")
        detail = get_review_candidate(self.conn, candidate_id)

        saved = save_relationship_section(self.conn, candidate_id, {"participants": detail["participants"]})

        self.assertEqual(count(self.conn, "people"), 0)
        self.assertIsNone(saved["participants"][0]["person_id"])
        self.assertEqual(saved["participants"][0]["participant_name"], "Fred")

    def test_editing_proposed_name_before_save_uses_edited_confirmed_name(self):
        candidate_id = self.import_without_persisted_participants("snap-edit", "Leah Grossman 630 30")
        detail = get_review_candidate(self.conn, candidate_id)
        detail["participants"][0]["display_name"] = "Leah Goldberg"
        detail["participants"][0]["participant_name"] = "Leah Goldberg"

        saved = save_relationship_section(self.conn, candidate_id, {"participants": detail["participants"]})

        self.assertEqual(saved["participants"][0]["display_name"], "Leah Goldberg")
        self.assertEqual(count(self.conn, "people"), 1)
        person = self.conn.execute("SELECT * FROM people").fetchone()
        self.assertEqual(person["display_name"], "Leah Goldberg")

    def test_edited_new_client_contact_fields_are_used_on_confirmation(self):
        candidate_id = self.import_without_persisted_participants("snap-contact", "Leah Grossman 630 30")
        detail = get_review_candidate(self.conn, candidate_id)
        detail["participants"][0].update(
            {
                "first_name": "Leah",
                "last_name": "Goldberg",
                "display_name": "Leah Goldberg",
                "participant_name": "Leah Goldberg",
                "billing_email": "leah@example.test",
                "billing_phone": "555-0100",
            }
        )

        saved = save_relationship_section(self.conn, candidate_id, {"participants": detail["participants"]})

        person = self.conn.execute("SELECT * FROM people").fetchone()
        self.assertEqual(saved["participants"][0]["person_id"], person["person_id"])
        self.assertEqual(person["display_name"], "Leah Goldberg")
        self.assertEqual(person["billing_email"], "leah@example.test")
        self.assertEqual(person["billing_phone"], "555-0100")
        self.assertTrue(person["person_code"])

    def test_save_bill_to_by_confirmed_client_id_creates_and_reuses_billing_party(self):
        candidate_id = self.import_without_persisted_participants("snap-payer", "Leah Grossman 630 30")
        detail = get_review_candidate(self.conn, candidate_id)
        saved = save_relationship_section(self.conn, candidate_id, {"participants": detail["participants"]})
        person_id = saved["participants"][0]["person_id"]

        first = save_billing_section(self.conn, candidate_id, {"bill_to_person_id": person_id})
        second = save_billing_section(self.conn, candidate_id, {"bill_to_person_id": person_id})

        self.assertEqual(first["billing_party"]["person_id"], person_id)
        self.assertEqual(second["billing_party"]["billing_party_id"], first["billing_party"]["billing_party_id"])
        self.assertEqual(count(self.conn, "billing_parties"), 1)

    def test_relationship_and_bill_to_save_persist_session_account_and_payer(self):
        candidate_id = self.import_without_persisted_participants("snap-return", "Leah Grossman 630 30")
        detail = get_review_candidate(self.conn, candidate_id)
        saved_participants = save_relationship_section(self.conn, candidate_id, {"participants": detail["participants"]})
        payer_person_id = saved_participants["participants"][0]["person_id"]
        account = create_account(self.conn, "Grossman Family Billing", "family")

        save_relationship_section(
            self.conn,
            candidate_id,
            {
                "participants": saved_participants["participants"],
                "account_id": account["account_id"],
                "primary_person_id": payer_person_id,
            },
        )
        billed = save_billing_section(self.conn, candidate_id, {"bill_to_person_id": payer_person_id})
        reloaded = get_review_candidate(self.conn, candidate_id)

        self.assertEqual(billed["session"]["id"], reloaded["session"]["id"])
        self.assertEqual(reloaded["session"]["account_id"], account["account_id"])
        self.assertEqual(reloaded["billing_party"]["person_id"], payer_person_id)
        self.assertEqual(reloaded["billing_party"]["billing_name"], "Leah Grossman")
        self.assertNotEqual(reloaded["billing_party"]["billing_name"], "Grossman Family Billing")

    def test_existing_billing_relationship_edit_keeps_selected_payer_name_on_review_reload(self):
        candidate_id = self.import_without_persisted_participants("snap-existing-relationship", "Leah Grossman 630 30")
        detail = get_review_candidate(self.conn, candidate_id)
        saved_participants = save_relationship_section(self.conn, candidate_id, {"participants": detail["participants"]})
        payer_person_id = saved_participants["participants"][0]["person_id"]
        account = create_account(self.conn, "Simon Household", "household")
        billing_party = create_billing_party(
            self.conn,
            {
                "billing_party_type": "person",
                "person_id": payer_person_id,
                "billing_name": "Leah Grossman",
                "billing_email": "leah@example.test",
            },
        )

        save_relationship_section(
            self.conn,
            candidate_id,
            {
                "participants": saved_participants["participants"],
                "account_id": account["account_id"],
                "primary_person_id": payer_person_id,
                "default_billing_party_id": billing_party["billing_party_id"],
                "billing_party_id": billing_party["billing_party_id"],
            },
        )
        reloaded = get_review_candidate(self.conn, candidate_id)

        self.assertEqual(reloaded["session"]["id"], saved_participants["session"]["id"])
        self.assertEqual(reloaded["billing_party"]["billing_name"], "Leah Grossman")
        self.assertNotEqual(reloaded["billing_party"]["billing_name"], "Simon Household")

    def test_person_record_returns_enriched_session_history(self):
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Smith", "display_name": "Fred Smith"})
        bobsey = create_person(self.conn, {"first_name": "Bobsey", "last_name": "Smith", "display_name": "Bobsey Smith"})
        payer = create_billing_party(self.conn, {"billing_name": "Fred Smith", "billing_party_type": "person", "person_id": fred["person_id"], "preferred_delivery_method": "email", "billing_email": "fred@example.test"})
        save_interpretation(
            self.conn,
            self.candidate_id,
            {
                "participants": [
                    {"person_id": fred["person_id"], "display_name": "Fred Smith", "is_primary": True},
                    {"person_id": bobsey["person_id"], "display_name": "Bobsey Smith"},
                ],
                "billing_party_id": payer["billing_party_id"],
                "approved_duration_minutes": 60,
                "service_mode": "office",
                "time_category": "standard",
                "approved_rate": "150.00",
                "payment_status": "unpaid",
            },
        )

        record = get_person_record(self.conn, fred["person_id"])

        session = record["sessions"][0]
        self.assertEqual(session["candidate_id"], self.candidate_id)
        self.assertTrue(session["session_id"])
        self.assertEqual(session["billing_session_type"], "psychotherapy")
        self.assertEqual(session["duration_minutes"], 60)
        self.assertEqual(session["payment_status"], "unpaid")
        self.assertIn("review_status", session)
        self.assertEqual(session["other_participant_names"], "Bobsey Smith")

    def test_custom_client_rate_creates_person_scoped_rate_rule(self):
        person = create_person(self.conn, {"first_name": "Leah", "last_name": "Grossman", "display_name": "Leah Grossman"})

        rule = create_rate_rule_from_payload(
            self.conn,
            {
                "person_id": person["person_id"],
                "amount": "425",
                "duration_minutes": "60",
                "billing_session_type": "psychotherapy_evening",
                "time_category": "evening",
                "effective_from": "2026-01-01",
            },
        )
        record = get_person_record(self.conn, person["person_id"])

        self.assertEqual(rule["person_id"], person["person_id"])
        self.assertIsNone(rule["client_account_id"])
        self.assertEqual(record["active_rate_exceptions"][0]["person_id"], person["person_id"])
        self.assertEqual(record["active_rate_exceptions"][0]["amount_cents"], 42500)

    def test_approved_alias_exact_match_auto_links(self):
        candidate_id = self.import_without_persisted_participants("snap-alias-match", "Leah Green 630 30")
        person = create_person(self.conn, {"first_name": "Leah", "last_name": "Grossman", "display_name": "Leah Grossman"})
        save_person_alias(self.conn, person["person_id"], raw_alias="Leah Green", approved_by_user=True)

        detail = get_review_candidate(self.conn, candidate_id)

        self.assertEqual(detail["participants"][0]["person_id"], person["person_id"])
        self.assertFalse(detail["participants"][0].get("is_proposed", False))

    def test_unapproved_alias_does_not_match(self):
        candidate_id = self.import_without_persisted_participants("snap-alias-unapproved", "Leah Green 630 30")
        person = create_person(self.conn, {"first_name": "Leah", "last_name": "Grossman", "display_name": "Leah Grossman"})
        save_person_alias(self.conn, person["person_id"], raw_alias="Leah Green", approved_by_user=False)

        detail = get_review_candidate(self.conn, candidate_id)

        self.assertIsNone(detail["participants"][0]["person_id"])
        self.assertTrue(detail["participants"][0]["is_proposed"])

    def test_conflicting_alias_is_rejected(self):
        first = create_person(self.conn, {"first_name": "Leah", "last_name": "Grossman", "display_name": "Leah Grossman"})
        second = create_person(self.conn, {"first_name": "Mia", "last_name": "Grossman", "display_name": "Mia Grossman"})
        save_person_alias(self.conn, first["person_id"], raw_alias="Leah Green", approved_by_user=True)

        with self.assertRaises(ValueError):
            save_person_alias(self.conn, second["person_id"], raw_alias="Leah Green", approved_by_user=True)

    def test_alias_deactivation_preserves_row_but_stops_matching(self):
        candidate_id = self.import_without_persisted_participants("snap-alias-deactivated", "Leah Green 630 30")
        person = create_person(self.conn, {"first_name": "Leah", "last_name": "Grossman", "display_name": "Leah Grossman"})
        alias = save_person_alias(self.conn, person["person_id"], raw_alias="Leah Green", approved_by_user=True)

        save_person_alias(
            self.conn,
            person["person_id"],
            alias_id=alias["alias_id"],
            raw_alias="Leah Green",
            approved_by_user=False,
        )
        detail = get_review_candidate(self.conn, candidate_id)
        stored = self.conn.execute("SELECT * FROM calendar_aliases WHERE alias_id = ?", (alias["alias_id"],)).fetchone()

        self.assertEqual(stored["approved_by_user"], 0)
        self.assertIsNone(detail["participants"][0]["person_id"])
        self.assertTrue(detail["participants"][0]["is_proposed"])

    def test_saved_session_bill_to_is_used_for_readiness(self):
        person = create_person(self.conn, "Fred Smith")
        payer = create_billing_party(self.conn, {"billing_name": "Fred Smith", "billing_party_type": "person", "person_id": person["person_id"]})
        save_interpretation(
            self.conn,
            self.candidate_id,
            {
                "participants": [{"person_id": person["person_id"], "display_name": "Fred Smith", "is_primary": True}],
                "billing_party_id": payer["billing_party_id"],
                "approved_duration_minutes": 60,
                "billing_session_type": "psychotherapy",
                "time_category": "standard",
                "approved_rate": "150.00",
                "payment_status": "unpaid",
            },
        )
        detail = get_review_candidate(self.conn, self.candidate_id)
        self.assertTrue(detail["readiness"]["billing_ready"])
        self.assertEqual(detail["readiness"]["billing_party_source"], "session")
        self.assertEqual(detail["effective_billing_party"]["billing_party_id"], payer["billing_party_id"])

    def test_account_default_payer_is_used_for_readiness(self):
        person = create_person(self.conn, "Fred Smith")
        account = create_account(self.conn, "Fred Household", "household")
        payer = create_billing_party(self.conn, {"billing_name": "Household Payer", "billing_party_type": "organization"})
        self.conn.execute("UPDATE client_accounts SET default_billing_party_id = ? WHERE account_id = ?", (payer["billing_party_id"], account["account_id"]))
        self.conn.commit()
        save_relationship_section(self.conn, self.candidate_id, {"participants": [{"person_id": person["person_id"], "display_name": "Fred Smith", "is_primary": True}], "account_id": account["account_id"]})
        detail = get_review_candidate(self.conn, self.candidate_id)
        self.assertEqual(detail["readiness"]["billing_party_source"], "account_default")
        self.assertEqual(detail["effective_billing_party"]["billing_party_id"], payer["billing_party_id"])

    def test_self_pay_billing_detaches_stale_account_for_review_session(self):
        person = create_person(self.conn, "Fred Smith")
        account = create_account(self.conn, "Fred Household", "household")
        payer = create_billing_party(self.conn, {"billing_name": "Household Payer", "billing_party_type": "organization"})
        self.conn.execute(
            "UPDATE client_accounts SET default_billing_party_id = ? WHERE account_id = ?",
            (payer["billing_party_id"], account["account_id"]),
        )
        self.conn.commit()
        save_relationship_section(
            self.conn,
            self.candidate_id,
            {
                "participants": [{"person_id": person["person_id"], "display_name": "Fred Smith", "is_primary": True}],
                "account_id": account["account_id"],
            },
        )

        saved = save_billing_section(
            self.conn,
            self.candidate_id,
            {"bill_to_person_id": person["person_id"], "detach_account": True},
        )

        session = self.conn.execute(
            "SELECT account_id, billing_party_id FROM sessions WHERE candidate_id = ?",
            (self.candidate_id,),
        ).fetchone()
        self.assertIsNone(session["account_id"])
        self.assertEqual(session["billing_party_id"], saved["billing_party"]["billing_party_id"])
        self.assertEqual(saved["billing_party"]["person_id"], person["person_id"])
        self.assertEqual(saved["readiness"]["billing_party_source"], "session")
        self.assertIsNone(saved["account"])

    def test_saving_different_bill_to_detaches_archived_relationship_account(self):
        person = create_person(self.conn, "Fred Smith")
        account = create_account(self.conn, "Old Shared Billing", "household")
        old_payer = create_billing_party(self.conn, {"billing_name": "Old Payer", "billing_party_type": "organization"})
        self.conn.execute(
            "UPDATE client_accounts SET default_billing_party_id = ?, active = 0 WHERE account_id = ?",
            (old_payer["billing_party_id"], account["account_id"]),
        )
        self.conn.commit()
        save_relationship_section(
            self.conn,
            self.candidate_id,
            {
                "participants": [{"person_id": person["person_id"], "display_name": "Fred Smith", "is_primary": True}],
                "account_id": account["account_id"],
            },
        )
        new_payer = create_billing_party(
            self.conn,
            {"billing_name": "Fred Smith", "billing_party_type": "person", "person_id": person["person_id"]},
        )

        saved = save_billing_section(
            self.conn,
            self.candidate_id,
            {"billing_party_id": new_payer["billing_party_id"]},
        )

        session = self.conn.execute(
            "SELECT account_id, billing_party_id FROM sessions WHERE candidate_id = ?",
            (self.candidate_id,),
        ).fetchone()
        self.assertIsNone(session["account_id"])
        self.assertEqual(session["billing_party_id"], new_payer["billing_party_id"])
        self.assertIsNone(saved["account"])
        self.assertEqual(saved["effective_billing_party"]["billing_party_id"], new_payer["billing_party_id"])

    def test_one_participant_with_exactly_one_active_billing_record_is_suggested(self):
        person = create_person(self.conn, "Fred Smith")
        payer = create_billing_party(self.conn, {"billing_name": "Fred Smith", "billing_party_type": "person", "person_id": person["person_id"]})
        save_relationship_section(self.conn, self.candidate_id, {"participants": [{"person_id": person["person_id"], "display_name": "Fred Smith", "is_primary": True}]})
        detail = get_review_candidate(self.conn, self.candidate_id)
        self.assertEqual(detail["readiness"]["billing_party_source"], "person_default")
        self.assertEqual(detail["effective_billing_party"]["billing_party_id"], payer["billing_party_id"])

    def test_confirming_new_single_client_does_not_create_billing_party_record(self):
        person = create_person(self.conn, "Fred Smith")
        before = count(self.conn, "billing_parties")
        save_relationship_section(self.conn, self.candidate_id, {"participants": [{"person_id": person["person_id"], "display_name": "Fred Smith", "is_primary": True}]})
        self.assertEqual(count(self.conn, "billing_parties"), before)

    def test_bill_to_remains_unresolved_after_client_confirmation_without_prior_payer(self):
        person = create_person(self.conn, "Fred Smith")
        save_relationship_section(self.conn, self.candidate_id, {"participants": [{"person_id": person["person_id"], "display_name": "Fred Smith", "is_primary": True}]})
        detail = get_review_candidate(self.conn, self.candidate_id)
        self.assertTrue(detail["readiness"]["clients_ready"])
        self.assertFalse(detail["readiness"]["billing_ready"])
        self.assertIsNone(detail["effective_billing_party"])

    def test_explicit_save_bill_to_creates_person_billing_party_and_unlocks_session_details(self):
        person = create_person(self.conn, "Fred Smith")
        create_rate_rule_from_payload(self.conn, {"amount": "150", "duration_minutes": "60", "billing_session_type": "psychotherapy", "time_category": "standard", "person_id": person["person_id"], "effective_from": "2026-01-01"})
        save_relationship_section(self.conn, self.candidate_id, {"participants": [{"person_id": person["person_id"], "display_name": "Fred Smith", "is_primary": True}]})
        billed = save_billing_section(self.conn, self.candidate_id, {"bill_to_person_id": person["person_id"]})
        save_interpretation(self.conn, self.candidate_id, {"participants": [{"person_id": person["person_id"], "display_name": "Fred Smith", "is_primary": True}], "billing_party_id": billed["billing_party"]["billing_party_id"], "approved_duration_minutes": 60, "billing_session_type": "psychotherapy", "time_category": "standard", "approved_rate": "", "payment_status": "paid"})
        detail = get_review_candidate(self.conn, self.candidate_id)
        self.assertEqual(detail["billing_party"]["person_id"], person["person_id"])
        self.assertTrue(detail["readiness"]["billing_ready"])
        self.assertTrue(detail["readiness"]["session_ready"])

    def test_ambiguous_billing_records_remain_unresolved(self):
        person = create_person(self.conn, "Fred Smith")
        create_billing_party(self.conn, {"billing_name": "Fred Smith", "billing_party_type": "person", "person_id": person["person_id"]})
        create_billing_party(self.conn, {"billing_name": "Fred Smith 2", "billing_party_type": "person", "person_id": person["person_id"]})
        save_relationship_section(self.conn, self.candidate_id, {"participants": [{"person_id": person["person_id"], "display_name": "Fred Smith", "is_primary": True}]})
        detail = get_review_candidate(self.conn, self.candidate_id)
        self.assertFalse(detail["readiness"]["billing_ready"])
        self.assertEqual(detail["readiness"]["billing_party_source"], "unresolved")

    def test_get_readiness_lookup_creates_no_records(self):
        person = create_person(self.conn, "Fred Smith")
        save_relationship_section(self.conn, self.candidate_id, {"participants": [{"person_id": person["person_id"], "display_name": "Fred Smith", "is_primary": True}]})
        before = count(self.conn, "billing_parties")
        get_review_candidate(self.conn, self.candidate_id)
        self.assertEqual(count(self.conn, "billing_parties"), before)

    def test_known_client_payer_and_exact_rate_produces_high_authority(self):
        person = create_person(self.conn, "Fred Smith")
        payer = create_billing_party(self.conn, {"billing_name": "Fred Smith", "billing_party_type": "person", "person_id": person["person_id"]})
        create_rate_rule_from_payload(self.conn, {"amount": "150", "duration_minutes": "60", "billing_session_type": "psychotherapy", "time_category": "standard", "person_id": person["person_id"], "effective_from": "2026-01-01"})
        save_relationship_section(self.conn, self.candidate_id, {"participants": [{"person_id": person["person_id"], "display_name": "Fred Smith", "is_primary": True}], "billing_party_id": payer["billing_party_id"]})
        save_interpretation(self.conn, self.candidate_id, {"participants": [{"person_id": person["person_id"], "display_name": "Fred Smith", "is_primary": True}], "billing_party_id": payer["billing_party_id"], "approved_duration_minutes": 60, "billing_session_type": "psychotherapy", "time_category": "standard", "approved_rate": "", "payment_status": "paid"})
        detail = get_review_candidate(self.conn, self.candidate_id)
        self.assertTrue(detail["readiness"]["all_ready"])
        self.assertGreaterEqual(detail["session"]["authority_score"], 75)
        self.assertIn("Known client", detail["session"]["authority_reasons"])

    def test_unresolved_client_locks_later_steps(self):
        detail = get_review_candidate(self.conn, self.candidate_id)
        self.assertFalse(detail["readiness"]["clients_ready"])
        self.assertFalse(detail["readiness"]["billing_ready"])
        self.assertFalse(detail["readiness"]["session_ready"])

    def test_unresolved_payer_locks_session_details(self):
        person = create_person(self.conn, "Fred Smith")
        create_billing_party(self.conn, {"billing_name": "Fred Smith", "billing_party_type": "person", "person_id": person["person_id"]})
        create_billing_party(self.conn, {"billing_name": "Fred Smith 2", "billing_party_type": "person", "person_id": person["person_id"]})
        save_relationship_section(self.conn, self.candidate_id, {"participants": [{"person_id": person["person_id"], "display_name": "Fred Smith", "is_primary": True}]})
        detail = get_review_candidate(self.conn, self.candidate_id)
        self.assertTrue(detail["readiness"]["clients_ready"])
        self.assertFalse(detail["readiness"]["billing_ready"])
        self.assertFalse(detail["readiness"]["session_ready"])

    def test_exact_suggested_rate_counts_as_ready_without_creating_override(self):
        person = create_person(self.conn, "Fred Smith")
        payer = create_billing_party(self.conn, {"billing_name": "Fred Smith", "billing_party_type": "person", "person_id": person["person_id"]})
        create_rate_rule_from_payload(self.conn, {"amount": "150", "duration_minutes": "60", "billing_session_type": "psychotherapy", "time_category": "standard", "person_id": person["person_id"], "effective_from": "2026-01-01"})
        save_relationship_section(self.conn, self.candidate_id, {"participants": [{"person_id": person["person_id"], "display_name": "Fred Smith", "is_primary": True}], "billing_party_id": payer["billing_party_id"]})
        save_interpretation(self.conn, self.candidate_id, {"participants": [{"person_id": person["person_id"], "display_name": "Fred Smith", "is_primary": True}], "billing_party_id": payer["billing_party_id"], "approved_duration_minutes": 60, "billing_session_type": "psychotherapy", "time_category": "standard", "approved_rate": "", "payment_status": "paid"})
        detail = get_review_candidate(self.conn, self.candidate_id)
        self.assertTrue(detail["readiness"]["session_ready"])
        self.assertIsNone(detail["session"]["approved_rate_cents"])
        self.assertEqual(detail["session"]["suggested_rate_cents"], 15000)

    def test_backend_time_display_uses_eastern_and_raw_timestamp_remains_unchanged(self):
        import_rows(self.conn, [raw_row("snap-time", title="Late 630 60", start="2026-06-17T13:30:00-04:00")], "test")
        row = next(item for item in list_review_candidates(self.conn)["items"] if item["raw_title"] == "Late 630 60")
        detail = get_review_candidate(self.conn, row["candidate_id"])
        self.assertEqual(row["time"], "1:30 PM")
        self.assertEqual(detail["session"]["start_at"], "2026-06-17T13:30:00-04:00")

    def test_saving_empty_participant_list_clears_participants_and_suppresses_proposal(self):
        candidate_id = self.import_without_persisted_participants("snap-empty", "Leah Grossman 630 30")

        saved = save_relationship_section(self.conn, candidate_id, {"participants": []})

        session_id = saved["session"]["id"]
        self.assertEqual(saved["participants"], [])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS count FROM session_participants WHERE session_id = ?", (session_id,)).fetchone()["count"],
            0,
        )
        self.assertEqual(get_review_candidate(self.conn, candidate_id)["participants"], [])

    def test_confirming_participant_preserves_raw_calendar_evidence(self):
        candidate_id = self.import_without_persisted_participants("snap-raw", "Leah Grossman 630 30")
        before = self.raw_snapshot_for_candidate(candidate_id)
        detail = get_review_candidate(self.conn, candidate_id)

        save_relationship_section(self.conn, candidate_id, {"participants": detail["participants"]})

        after = self.raw_snapshot_for_candidate(candidate_id)
        self.assertEqual(dict(before), dict(after))

    def import_without_persisted_participants(self, snapshot_key, title):
        import_rows(self.conn, [raw_row(snapshot_key, title=title)], "test")
        candidate_id = next(row["candidate_id"] for row in list_review_candidates(self.conn)["items"] if row["raw_title"] == title)
        session = self.conn.execute("SELECT id FROM sessions WHERE candidate_id = ?", (candidate_id,)).fetchone()
        self.conn.execute("DELETE FROM session_participants WHERE session_id = ?", (session["id"],))
        self.conn.commit()
        return candidate_id

    def raw_snapshot_for_candidate(self, candidate_id):
        return self.conn.execute(
            """
            SELECT r.event_title, r.start_at, r.end_at, r.duration_minutes, r.raw_json
            FROM calendar_event_candidates c
            JOIN raw_calendar_snapshots r ON r.id = c.latest_raw_snapshot_id
            WHERE c.id = ?
            """,
            (candidate_id,),
        ).fetchone()

    def test_participant_save_creates_approved_alias(self):
        candidate_id = self.import_without_persisted_participants("snap-lou-1", "Lou 630 30")
        person = create_person(self.conn, {"first_name": "Lou", "last_name": "Yeager", "display_name": "Lou Yeager"})
        save_relationship_section(
            self.conn,
            candidate_id,
            {
                "participants": [
                    {"person_id": person["person_id"], "display_name": "Lou Yeager", "is_primary": True},
                ],
            },
        )
        alias = self.conn.execute(
            "SELECT * FROM calendar_aliases WHERE normalized_alias = ?", ("lou",),
        ).fetchone()
        self.assertIsNotNone(alias)
        self.assertEqual(alias["person_id"], person["person_id"])
        self.assertEqual(alias["approved_by_user"], 1)

    def test_participant_save_alias_enables_future_smart_prefill(self):
        candidate_id = self.import_without_persisted_participants("snap-lou-prefill", "Lou 630 30")
        person = create_person(self.conn, {"first_name": "Lou", "last_name": "Yeager", "display_name": "Lou Yeager"})
        save_relationship_section(
            self.conn,
            candidate_id,
            {
                "participants": [
                    {"person_id": person["person_id"], "display_name": "Lou Yeager", "is_primary": True},
                ],
            },
        )
        future_id = self.import_without_persisted_participants("snap-lou-future", "Lou 600 60")
        detail = get_review_candidate(self.conn, future_id)
        self.assertEqual(detail["participants"][0]["person_id"], person["person_id"])
        self.assertFalse(detail["participants"][0].get("is_proposed", False))

    def test_participant_save_alias_idempotent_on_duplicate_save(self):
        candidate_id = self.import_without_persisted_participants("snap-lou-dup", "Lou 630 30")
        person = create_person(self.conn, {"first_name": "Lou", "last_name": "Yeager", "display_name": "Lou Yeager"})
        for _ in range(2):
            save_relationship_section(
                self.conn,
                candidate_id,
                {
                    "participants": [
                        {"person_id": person["person_id"], "display_name": "Lou Yeager", "is_primary": True},
                    ],
                },
            )
        aliases = self.conn.execute(
            "SELECT * FROM calendar_aliases WHERE normalized_alias = ?", ("lou",),
        ).fetchall()
        self.assertEqual(len(aliases), 1)
        self.assertEqual(aliases[0]["person_id"], person["person_id"])

    def test_participant_save_alias_conflict_skips_existing(self):
        candidate_id = self.import_without_persisted_participants("snap-lou-conflict", "Lou 630 30")
        first = create_person(self.conn, {"first_name": "Lou", "last_name": "Yeager", "display_name": "Lou Yeager"})
        second = create_person(self.conn, {"first_name": "Lou", "last_name": "Smith", "display_name": "Lou Smith"})
        save_person_alias(self.conn, first["person_id"], raw_alias="Lou", approved_by_user=True)
        save_relationship_section(
            self.conn,
            candidate_id,
            {
                "participants": [
                    {"person_id": second["person_id"], "display_name": "Lou Smith", "is_primary": True},
                ],
            },
        )
        alias = self.conn.execute(
            "SELECT * FROM calendar_aliases WHERE normalized_alias = ?", ("lou",),
        ).fetchone()
        self.assertEqual(alias["person_id"], first["person_id"])

    def test_participant_save_no_alias_for_multi_person_title(self):
        candidate_id = self.import_without_persisted_participants("snap-multi", "Bobsey and Fred 6")
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Smith", "display_name": "Fred Smith"})
        bobsey = create_person(self.conn, {"first_name": "Bobsey", "last_name": "Smith", "display_name": "Bobsey Smith"})
        save_relationship_section(
            self.conn,
            candidate_id,
            {
                "participants": [
                    {"person_id": fred["person_id"], "display_name": "Fred Smith", "is_primary": True},
                    {"person_id": bobsey["person_id"], "display_name": "Bobsey Smith"},
                ],
            },
        )
        alias = self.conn.execute(
            "SELECT * FROM calendar_aliases WHERE normalized_alias = ?", ("bobsey and fred",),
        ).fetchone()
        self.assertIsNone(alias)

    def test_candidate_only_joint_session_saves_and_approves_once(self):
        candidate_id = self.import_candidate_only("snap-joint-save", "Bobsey and Fred 6")
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Colin", "display_name": "Fred Colin"})
        bobsey = create_person(self.conn, {"first_name": "Bobsey", "last_name": "Colin", "display_name": "Bobsey Colin"})
        payer = create_billing_party(
            self.conn,
            {"billing_name": "Fred Colin", "billing_party_type": "person", "person_id": fred["person_id"]},
        )

        participants = [
            {"person_id": bobsey["person_id"], "display_name": "Bobsey Colin", "is_primary": True},
            {"person_id": fred["person_id"], "display_name": "Fred Colin"},
        ]
        relationship = save_relationship_section(self.conn, candidate_id, {"participants": participants})
        session_id = relationship["session"]["id"]
        self.assertTrue(session_id)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS count FROM sessions WHERE candidate_id = ?", (candidate_id,)).fetchone()["count"],
            1,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS count FROM session_participants WHERE session_id = ?", (session_id,)).fetchone()["count"],
            2,
        )

        save_billing_section(self.conn, candidate_id, {"billing_party_id": payer["billing_party_id"]})
        saved = save_session_draft(
            self.conn,
            candidate_id,
            {
                "approved_duration_minutes": 60,
                "billing_session_type": "psychotherapy",
                "time_category": "standard",
                "approved_rate": "400.00",
                "payment_status": "unpaid",
                "billing_treatment": "billable",
            },
        )
        self.assertEqual(saved["session"]["id"], session_id)
        self.assertEqual(saved["session"]["billing_party_id"], payer["billing_party_id"])
        self.assertEqual(saved["session"]["approved_rate_cents"], 40000)

        approved = approve_candidate(
            self.conn,
            candidate_id,
            {
                "participants": participants,
                "billing_party_id": payer["billing_party_id"],
                "approved_duration_minutes": 60,
                "billing_session_type": "psychotherapy",
                "time_category": "standard",
                "approved_rate": "400.00",
                "payment_status": "unpaid",
                "billing_treatment": "billable",
            },
        )
        self.assertEqual(approved["session"]["id"], session_id)
        self.assertEqual(approved["session"]["review_status"], "approved")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS count FROM sessions WHERE candidate_id = ?", (candidate_id,)).fetchone()["count"],
            1,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS count FROM session_participants WHERE session_id = ?", (session_id,)).fetchone()["count"],
            2,
        )
        self.assertTrue(
            self.conn.execute(
                "SELECT 1 FROM review_items WHERE candidate_id = ? AND session_id = ? AND review_status = 'approved'",
                (candidate_id, session_id),
            ).fetchone()
        )

        repeated = approve_candidate(
            self.conn,
            candidate_id,
            {
                "participants": participants,
                "billing_party_id": payer["billing_party_id"],
                "approved_duration_minutes": 60,
                "billing_session_type": "psychotherapy",
                "time_category": "standard",
                "approved_rate": "400.00",
                "payment_status": "unpaid",
                "billing_treatment": "billable",
            },
        )
        self.assertEqual(repeated["session"]["id"], session_id)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS count FROM sessions WHERE candidate_id = ?", (candidate_id,)).fetchone()["count"],
            1,
        )

    def test_candidate_only_single_session_behavior_still_saves(self):
        candidate_id = self.import_candidate_only("snap-single-save", "Fred 6")
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Colin", "display_name": "Fred Colin"})

        saved = save_relationship_section(
            self.conn,
            candidate_id,
            {"participants": [{"person_id": fred["person_id"], "display_name": "Fred Colin", "is_primary": True}]},
        )

        self.assertTrue(saved["session"]["id"])
        self.assertEqual(len(saved["participants"]), 1)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS count FROM sessions WHERE candidate_id = ?", (candidate_id,)).fetchone()["count"],
            1,
        )

    def import_candidate_only(self, snapshot_key, title):
        import_rows(self.conn, [raw_row(snapshot_key, title=title)], "test")
        candidate_id = next(row["candidate_id"] for row in list_review_candidates(self.conn)["items"] if row["raw_title"] == title)
        session = self.conn.execute("SELECT id FROM sessions WHERE candidate_id = ?", (candidate_id,)).fetchone()
        if session:
            self.conn.execute(
                "UPDATE review_items SET session_id = NULL WHERE candidate_id = ?",
                (candidate_id,),
            )
            self.conn.execute("DELETE FROM session_participants WHERE session_id = ?", (session["id"],))
            self.conn.execute("DELETE FROM sessions WHERE id = ?", (session["id"],))
            self.conn.commit()
        self.assertIsNone(self.conn.execute("SELECT id FROM sessions WHERE candidate_id = ?", (candidate_id,)).fetchone())
        return candidate_id


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]


class EveningSuggestRateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "rate_test.sqlite3")
        from jordana_invoice.db import init_db
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _import_evening_session(self, key="snap-evening"):
        from jordana_invoice.importer import import_rows
        row = raw_row(key, title="Alice Smith 6", start="2026-06-17T20:00:00-04:00")
        import_rows(self.conn, [row], "rate_test")
        result = list_review_candidates(self.conn)
        return next(i["candidate_id"] for i in result["items"] if "Alice" in i["raw_title"])

    def test_evening_session_gets_no_suggestion_when_only_standard_rule_exists(self):
        seed_rate_rule(
            self.conn,
            amount_cents=35000,
            effective_from="2020-01-01",
            duration_minutes=60,
            billing_session_type="psychotherapy",
            time_category="standard",
        )
        self.conn.commit()
        candidate_id = self._import_evening_session()
        session = self.conn.execute(
            "SELECT suggested_rate_cents, rate_needs_review, review_status, time_category FROM sessions WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        self.assertEqual(session["time_category"], "evening",
                         "Session starting at 8 PM must have time_category=evening")
        self.assertIsNone(session["suggested_rate_cents"],
                          "No rate rule for evening: suggested_rate_cents must be NULL")
        self.assertTrue(bool(session["rate_needs_review"]),
                        "rate_needs_review must be True when no matching evening rule exists")

    def test_recalc_clears_stale_standard_suggestion_on_evening_session(self):
        seed_rate_rule(
            self.conn,
            amount_cents=35000,
            effective_from="2020-01-01",
            duration_minutes=60,
            billing_session_type="psychotherapy",
            time_category="standard",
        )
        self.conn.commit()
        candidate_id = self._import_evening_session("snap-ev2")
        self.conn.execute(
            "UPDATE sessions SET suggested_rate_cents = 35000, rate_source = 'default', rate_needs_review = 0 WHERE candidate_id = ?",
            (candidate_id,),
        )
        self.conn.commit()
        stale = self.conn.execute(
            "SELECT suggested_rate_cents FROM sessions WHERE candidate_id = ?", (candidate_id,)
        ).fetchone()
        self.assertEqual(stale["suggested_rate_cents"], 35000, "Pre-condition: stale rate must be set")

        updated = recalc_unapproved_session_rates(self.conn)

        self.assertGreater(updated, 0)
        after = self.conn.execute(
            "SELECT suggested_rate_cents, rate_needs_review, review_status FROM sessions WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        self.assertIsNone(after["suggested_rate_cents"],
                          "After recalc, stale Standard rate must be cleared for evening session")
        self.assertTrue(bool(after["rate_needs_review"]),
                        "rate_needs_review must be True after recalc clears stale evening rate")

    def test_evening_rule_matches_evening_session(self):
        seed_rate_rule(
            self.conn,
            amount_cents=40000,
            effective_from="2020-01-01",
            duration_minutes=60,
            billing_session_type="psychotherapy_evening",
            time_category="evening",
        )
        self.conn.commit()
        candidate_id = self._import_evening_session("snap-ev3")
        session = self.conn.execute(
            "SELECT suggested_rate_cents, rate_needs_review FROM sessions WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        self.assertEqual(session["suggested_rate_cents"], 40000,
                         "Evening rate rule must produce $400 suggestion for 60-min evening session")
        self.assertFalse(bool(session["rate_needs_review"]),
                         "rate_needs_review must be False when an evening rule matches")


class PersonRecordBillingEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "enriched.sqlite3")
        init_db(self.conn)
        seed_rate_rule(self.conn, amount_cents=20000, effective_from="2020-01-01", duration_minutes=60)
        save_business_profile(self.conn, {
            "business_name": "Test Practice", "provider_display_name": "Test Provider",
            "address_line_1": "100 Test Ave", "city": "Test", "state": "FL", "postal_code": "00000",
            "phone": "555-0100", "email": "test@example.test", "payee_name": "Test Payee",
            "payment_address_line_1": "100 Test Ave", "payment_city": "Test", "payment_state": "FL",
            "payment_postal_code": "00000", "zelle_recipient": "demo-zelle@example.test",
        })
        self.conn.commit()
        import_rows(self.conn, [raw_row("snap-a")], "test")
        self.candidate_id = list_review_candidates(self.conn)["items"][0]["candidate_id"]

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _approve_session(self, participant_ids, billing_party_id, candidate_id=None):
        cid = candidate_id or self.candidate_id
        approve_candidate(self.conn, cid, {
            "participants": [
                {"person_id": pid, "display_name": pid, "is_primary": idx == 0}
                for idx, pid in enumerate(participant_ids)
            ],
            "billing_party_id": billing_party_id,
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "unpaid",
        })

    def test_self_pay_appears_in_payers_for_client(self):
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Smith", "display_name": "Fred Smith"})
        payer = create_billing_party(self.conn, {"billing_name": "Fred Smith", "billing_party_type": "person", "person_id": fred["person_id"], "preferred_delivery_method": "email", "billing_email": "fred@example.test"})
        self._approve_session([fred["person_id"]], payer["billing_party_id"])

        record = get_person_record(self.conn, fred["person_id"])
        payers = record["payers_for_client"]
        self.assertEqual(len(payers), 1)
        self.assertEqual(payers[0]["billing_party_id"], payer["billing_party_id"])
        self.assertEqual(payers[0]["payer_person_id"], fred["person_id"])
        self.assertEqual(payers[0]["payer_display_name"], "Fred Smith")
        self.assertEqual(payers[0]["session_count"], 1)

    def test_third_party_payer_appears_for_participant(self):
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Smith", "display_name": "Fred Smith"})
        bobsey = create_person(self.conn, {"first_name": "Bobsey", "last_name": "Smith", "display_name": "Bobsey Smith"})
        payer = create_billing_party(self.conn, {"billing_name": "Bobsey Smith", "billing_party_type": "person", "person_id": bobsey["person_id"], "preferred_delivery_method": "email", "billing_email": "bobsey@example.test"})
        self._approve_session([fred["person_id"]], payer["billing_party_id"])

        record = get_person_record(self.conn, fred["person_id"])
        payers = record["payers_for_client"]
        self.assertEqual(len(payers), 1)
        self.assertEqual(payers[0]["billing_party_id"], payer["billing_party_id"])
        self.assertEqual(payers[0]["payer_person_id"], bobsey["person_id"])
        self.assertEqual(payers[0]["payer_display_name"], "Bobsey Smith")

    def test_payer_record_lists_people_they_pay_for(self):
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Smith", "display_name": "Fred Smith"})
        bobsey = create_person(self.conn, {"first_name": "Bobsey", "last_name": "Smith", "display_name": "Bobsey Smith"})
        payer = create_billing_party(self.conn, {"billing_name": "Bobsey Smith", "billing_party_type": "person", "person_id": bobsey["person_id"], "preferred_delivery_method": "email", "billing_email": "bobsey@example.test"})
        self._approve_session([fred["person_id"]], payer["billing_party_id"])

        record = get_person_record(self.conn, bobsey["person_id"])
        people_billed_for = record["people_billed_for"]
        self.assertEqual(len(people_billed_for), 1)
        self.assertEqual(people_billed_for[0]["participant_person_id"], fred["person_id"])
        self.assertEqual(people_billed_for[0]["participant_display_name"], "Fred Smith")
        self.assertEqual(people_billed_for[0]["session_count"], 1)

    def test_relationship_summaries_include_account_id_from_client_and_payer_sides(self):
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Smith", "display_name": "Fred Smith"})
        bobsey = create_person(self.conn, {"first_name": "Bobsey", "last_name": "Smith", "display_name": "Bobsey Smith"})
        relationship = setup_billing_relationship(
            self.conn,
            {
                "payer_kind": "person",
                "payer_person_id": bobsey["person_id"],
                "covered_client_ids": [fred["person_id"]],
            },
        )
        self._approve_session([fred["person_id"]], relationship["billing_party_id"])

        client_record = get_person_record(self.conn, fred["person_id"])
        payer_record = get_person_record(self.conn, bobsey["person_id"])

        self.assertEqual(client_record["payers_for_client"][0]["account_id"], relationship["account_id"])
        self.assertEqual(client_record["payers_for_client"][0]["account_name"], relationship["account_name"])
        self.assertEqual(payer_record["people_billed_for"][0]["account_id"], relationship["account_id"])
        self.assertEqual(payer_record["people_billed_for"][0]["account_name"], relationship["account_name"])

    def test_update_relationship_can_change_third_party_client_to_self_pay_without_duplicate_account(self):
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Smith", "display_name": "Fred Smith"})
        bobsey = create_person(self.conn, {"first_name": "Bobsey", "last_name": "Smith", "display_name": "Bobsey Smith"})
        relationship = setup_billing_relationship(
            self.conn,
            {
                "payer_kind": "person",
                "payer_person_id": bobsey["person_id"],
                "covered_client_ids": [fred["person_id"]],
            },
        )
        before_account_count = self.conn.execute("SELECT COUNT(*) FROM client_accounts").fetchone()[0]

        updated = update_billing_relationship(
            self.conn,
            relationship["account_id"],
            {
                "payer_kind": "client",
                "payer_person_id": fred["person_id"],
                "covered_client_ids": [fred["person_id"]],
                "billing_delivery": {"billing_name": "Fred Smith"},
            },
        )

        self.assertEqual(updated["account"]["account_id"], relationship["account_id"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM client_accounts").fetchone()[0], before_account_count)
        account = self.conn.execute(
            """
            SELECT ca.default_billing_party_id, bp.person_id
            FROM client_accounts ca
            JOIN billing_parties bp ON bp.billing_party_id = ca.default_billing_party_id
            WHERE ca.account_id = ?
            """,
            (relationship["account_id"],),
        ).fetchone()
        self.assertEqual(account["person_id"], fred["person_id"])
        members = self.conn.execute(
            "SELECT person_id FROM account_members WHERE account_id = ? ORDER BY person_id",
            (relationship["account_id"],),
        ).fetchall()
        self.assertEqual([row["person_id"] for row in members], [fred["person_id"]])
        client_record = get_person_record(self.conn, fred["person_id"])
        self.assertEqual(client_record["accounts"][0]["account_id"], relationship["account_id"])
        directory_records = list_billing_relationship_records(self.conn)
        self.assertTrue(any(row.get("account_id") == relationship["account_id"] for row in directory_records))

    def test_implicit_self_pay_can_be_initialized_as_canonical_relationship_once(self):
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Smith", "display_name": "Fred Smith"})
        payer = create_billing_party(
            self.conn,
            {"billing_name": "Fred Smith", "billing_party_type": "person", "person_id": fred["person_id"]},
        )
        self._approve_session([fred["person_id"]], payer["billing_party_id"])

        before_records = list_billing_relationship_records(self.conn)
        self_pay = [row for row in before_records if row["record_type"] == "self_pay" and row["payer_person_id"] == fred["person_id"]][0]
        self.assertIsNone(self_pay["account_id"])
        approved_before = get_review_candidate(self.conn, self.candidate_id)

        relationship = setup_billing_relationship(
            self.conn,
            {
                "payer_kind": "client",
                "payer_person_id": fred["person_id"],
                "covered_client_ids": [fred["person_id"]],
            },
        )
        duplicate = setup_billing_relationship(
            self.conn,
            {
                "payer_kind": "client",
                "payer_person_id": fred["person_id"],
                "covered_client_ids": [fred["person_id"]],
            },
        )

        self.assertEqual(duplicate["account_id"], relationship["account_id"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM client_accounts").fetchone()[0], 1)
        after_records = list_billing_relationship_records(self.conn)
        self.assertTrue(any(row.get("account_id") == relationship["account_id"] for row in after_records))
        approved_after = get_review_candidate(self.conn, self.candidate_id)
        self.assertEqual(approved_after["session"]["review_status"], "approved")
        self.assertEqual(approved_after["session"]["billing_party_id"], approved_before["session"]["billing_party_id"])
        self.assertEqual(approved_after["session"]["approved_rate_cents"], approved_before["session"]["approved_rate_cents"])

    def test_update_relationship_add_remove_and_change_payer_persist_on_same_account(self):
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Smith", "display_name": "Fred Smith"})
        bobsey = create_person(self.conn, {"first_name": "Bobsey", "last_name": "Smith", "display_name": "Bobsey Smith"})
        sage = create_person(self.conn, {"first_name": "Sage", "last_name": "Smith", "display_name": "Sage Smith"})
        diana = create_person(self.conn, {"first_name": "Diana", "last_name": "Smith", "display_name": "Diana Smith"})
        relationship = setup_billing_relationship(
            self.conn,
            {
                "payer_kind": "person",
                "payer_person_id": bobsey["person_id"],
                "covered_client_ids": [fred["person_id"]],
            },
        )

        update_billing_relationship(
            self.conn,
            relationship["account_id"],
            {
                "payer_kind": "person",
                "payer_person_id": diana["person_id"],
                "covered_client_ids": [sage["person_id"]],
                "billing_delivery": {"billing_name": "Diana Smith"},
            },
        )

        account = self.conn.execute(
            """
            SELECT ca.default_billing_party_id, bp.person_id
            FROM client_accounts ca
            JOIN billing_parties bp ON bp.billing_party_id = ca.default_billing_party_id
            WHERE ca.account_id = ?
            """,
            (relationship["account_id"],),
        ).fetchone()
        self.assertEqual(account["person_id"], diana["person_id"])
        members = self.conn.execute(
            "SELECT person_id FROM account_members WHERE account_id = ? ORDER BY person_id",
            (relationship["account_id"],),
        ).fetchall()
        self.assertEqual([row["person_id"] for row in members], [sage["person_id"]])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM client_accounts").fetchone()[0], 1)

    def test_payer_pays_for_self_and_other(self):
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Smith", "display_name": "Fred Smith"})
        bobsey = create_person(self.conn, {"first_name": "Bobsey", "last_name": "Smith", "display_name": "Bobsey Smith"})
        payer = create_billing_party(self.conn, {"billing_name": "Bobsey Smith", "billing_party_type": "person", "person_id": bobsey["person_id"], "preferred_delivery_method": "email", "billing_email": "bobsey@example.test"})
        self._approve_session([fred["person_id"], bobsey["person_id"]], payer["billing_party_id"])

        record = get_person_record(self.conn, bobsey["person_id"])
        people = record["people_billed_for"]
        names = sorted(p["participant_display_name"] for p in people)
        self.assertEqual(names, ["Bobsey Smith", "Fred Smith"])

    def test_duplicate_sessions_do_not_create_duplicate_payer_rows(self):
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Smith", "display_name": "Fred Smith"})
        payer = create_billing_party(self.conn, {"billing_name": "Fred Smith", "billing_party_type": "person", "person_id": fred["person_id"], "preferred_delivery_method": "email", "billing_email": "fred@example.test"})
        self._approve_session([fred["person_id"]], payer["billing_party_id"])
        import_rows(self.conn, [raw_row("snap-b", title="Fred 6", start="2026-06-18T18:00:00-04:00")], "test")
        cid2 = list_review_candidates(self.conn)["items"][0]["candidate_id"]
        self._approve_session([fred["person_id"]], payer["billing_party_id"], candidate_id=cid2)

        record = get_person_record(self.conn, fred["person_id"])
        payers = record["payers_for_client"]
        self.assertEqual(len(payers), 1)
        self.assertEqual(payers[0]["session_count"], 2)

    def test_invoice_totals_and_balances_returned_correctly(self):
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Smith", "display_name": "Fred Smith"})
        payer = create_billing_party(self.conn, {"billing_name": "Fred Smith", "billing_party_type": "person", "person_id": fred["person_id"], "preferred_delivery_method": "email", "billing_email": "fred@example.test"})
        self._approve_session([fred["person_id"]], payer["billing_party_id"])
        session = self.conn.execute("SELECT id FROM sessions WHERE candidate_id = ?", (self.candidate_id,)).fetchone()
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": payer["billing_party_id"],
            "billing_period_start": "2026-06-01",
            "billing_period_end": "2026-06-30",
            "invoice_date": "2026-06-30",
            "session_ids": [session["id"]],
        })
        self.assertEqual(draft["invoice"]["total_cents"], 20000)

        record = get_person_record(self.conn, fred["person_id"])
        invoices = record["invoices"]
        self.assertEqual(len(invoices), 1)
        self.assertEqual(invoices[0]["invoice_id"], draft["invoice"]["invoice_id"])
        self.assertEqual(invoices[0]["total_cents"], 20000)
        self.assertEqual(invoices[0]["balance_cents"], 20000)
        self.assertEqual(invoices[0]["status"], "draft")
        self.assertIsNone(invoices[0]["finalized_at"])

    def test_client_invoice_history_includes_billing_month_without_changing_period(self):
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Smith", "display_name": "Fred Smith"})
        payer = create_billing_party(self.conn, {"billing_name": "Fred Smith", "billing_party_type": "person", "person_id": fred["person_id"], "preferred_delivery_method": "email", "billing_email": "fred@example.test"})
        self._approve_session([fred["person_id"]], payer["billing_party_id"])
        session = self.conn.execute("SELECT id FROM sessions WHERE candidate_id = ?", (self.candidate_id,)).fetchone()
        create_invoice_draft(self.conn, {
            "bill_to_party_id": payer["billing_party_id"],
            "billing_month": "2026-06",
            "invoice_date": "2026-06-30",
            "session_ids": [session["id"]],
        })

        record = get_person_record(self.conn, fred["person_id"])
        invoice = record["invoices"][0]
        self.assertEqual(invoice["billing_month"], "2026-06")
        self.assertEqual(invoice["billing_period_start"], "2026-06-01")
        self.assertEqual(invoice["billing_period_end"], "2026-06-30")

    def test_finalized_invoice_has_finalized_at(self):
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Smith", "display_name": "Fred Smith"})
        payer = create_billing_party(self.conn, {"billing_name": "Fred Smith", "billing_party_type": "person", "person_id": fred["person_id"], "preferred_delivery_method": "email", "billing_email": "fred@example.test"})
        self._approve_session([fred["person_id"]], payer["billing_party_id"])
        session = self.conn.execute("SELECT id FROM sessions WHERE candidate_id = ?", (self.candidate_id,)).fetchone()
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": payer["billing_party_id"],
            "billing_period_start": "2026-06-01",
            "billing_period_end": "2026-06-30",
            "invoice_date": "2026-06-30",
            "session_ids": [session["id"]],
        })
        with patch("jordana_invoice.invoice_services.generate_invoice_pdf", return_value="a" * 64):
            finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "pdf")

        record = get_person_record(self.conn, fred["person_id"])
        inv = record["invoices"][0]
        self.assertEqual(inv["status"], "finalized")
        self.assertIsNotNone(inv["finalized_at"])
        self.assertIsNotNone(inv["invoice_number"])

    def test_void_invoice_has_zero_balance(self):
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Smith", "display_name": "Fred Smith"})
        payer = create_billing_party(self.conn, {"billing_name": "Fred Smith", "billing_party_type": "person", "person_id": fred["person_id"], "preferred_delivery_method": "email", "billing_email": "fred@example.test"})
        self._approve_session([fred["person_id"]], payer["billing_party_id"])
        session = self.conn.execute("SELECT id FROM sessions WHERE candidate_id = ?", (self.candidate_id,)).fetchone()
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": payer["billing_party_id"],
            "billing_period_start": "2026-06-01",
            "billing_period_end": "2026-06-30",
            "invoice_date": "2026-06-30",
            "session_ids": [session["id"]],
        })
        with patch("jordana_invoice.invoice_services.generate_invoice_pdf", return_value="a" * 64):
            finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "pdf")
        void_invoice(self.conn, draft["invoice"]["invoice_id"], "Test void")

        record = get_person_record(self.conn, fred["person_id"])
        inv = record["invoices"][0]
        self.assertEqual(inv["status"], "void")
        self.assertEqual(inv["balance_cents"], 0)

    def test_invoices_for_unrelated_billing_parties_excluded(self):
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Smith", "display_name": "Fred Smith"})
        other = create_person(self.conn, {"first_name": "Other", "last_name": "Person", "display_name": "Other Person"})
        fred_payer = create_billing_party(self.conn, {"billing_name": "Fred Smith", "billing_party_type": "person", "person_id": fred["person_id"], "preferred_delivery_method": "email", "billing_email": "fred@example.test"})
        other_payer = create_billing_party(self.conn, {"billing_name": "Other Person", "billing_party_type": "person", "person_id": other["person_id"], "preferred_delivery_method": "email", "billing_email": "other@example.test"})
        self._approve_session([fred["person_id"]], fred_payer["billing_party_id"])
        session = self.conn.execute("SELECT id FROM sessions WHERE candidate_id = ?", (self.candidate_id,)).fetchone()
        create_invoice_draft(self.conn, {
            "bill_to_party_id": fred_payer["billing_party_id"],
            "billing_period_start": "2026-06-01", "billing_period_end": "2026-06-30",
            "invoice_date": "2026-06-30", "session_ids": [session["id"]],
        })
        import_rows(self.conn, [raw_row("snap-c", title="Other 6", start="2026-06-19T18:00:00-04:00")], "test")
        cid2 = list_review_candidates(self.conn)["items"][0]["candidate_id"]
        approve_candidate(self.conn, cid2, {
            "participants": [{"person_id": other["person_id"], "display_name": "Other Person", "is_primary": True}],
            "billing_party_id": other_payer["billing_party_id"],
            "approved_duration_minutes": 60, "service_mode": "office",
            "time_category": "standard", "approved_rate": "200.00", "payment_status": "unpaid",
        })
        session2 = self.conn.execute("SELECT id FROM sessions WHERE candidate_id = ?", (cid2,)).fetchone()
        create_invoice_draft(self.conn, {
            "bill_to_party_id": other_payer["billing_party_id"],
            "billing_period_start": "2026-06-01", "billing_period_end": "2026-06-30",
            "invoice_date": "2026-06-30", "session_ids": [session2["id"]],
        })

        record = get_person_record(self.conn, fred["person_id"])
        self.assertEqual(len(record["invoices"]), 1)
        self.assertEqual(record["invoices"][0]["bill_to_party_id"], fred_payer["billing_party_id"])

    def test_inactive_billing_parties_handled_consistently(self):
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Smith", "display_name": "Fred Smith"})
        payer = create_billing_party(self.conn, {"billing_name": "Fred Smith", "billing_party_type": "person", "person_id": fred["person_id"], "preferred_delivery_method": "email", "billing_email": "fred@example.test"})
        self._approve_session([fred["person_id"]], payer["billing_party_id"])
        self.conn.execute("UPDATE billing_parties SET active = 0 WHERE billing_party_id = ?", (payer["billing_party_id"],))
        self.conn.commit()

        record = get_person_record(self.conn, fred["person_id"])
        setup = record["billing_setup"]
        self.assertEqual(len(setup), 1)
        self.assertEqual(setup[0]["active"], 0)
        summary = record["billing_summary"]
        self.assertEqual(summary["active_billing_parties"], 0)
        payers = record["payers_for_client"]
        self.assertEqual(len(payers), 1)
        self.assertEqual(payers[0]["billing_party_active"], 0)

    def test_empty_client_record_returns_empty_arrays_and_zero_totals(self):
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Smith", "display_name": "Fred Smith"})

        record = get_person_record(self.conn, fred["person_id"])
        self.assertEqual(record["billing_setup"], [])
        self.assertEqual(record["payers_for_client"], [])
        self.assertEqual(record["people_billed_for"], [])
        self.assertEqual(record["invoices"], [])
        summary = record["billing_summary"]
        self.assertEqual(summary["active_billing_parties"], 0)
        self.assertEqual(summary["invoice_count"], 0)
        self.assertEqual(summary["total_invoiced_cents"], 0)
        self.assertEqual(summary["finalized_invoice_total_cents"], 0)
        self.assertEqual(summary["approved_uninvoiced_sessions"], 0)

    def test_approved_uninvoiced_sessions_counted_in_summary(self):
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Smith", "display_name": "Fred Smith"})
        payer = create_billing_party(self.conn, {"billing_name": "Fred Smith", "billing_party_type": "person", "person_id": fred["person_id"]})
        self._approve_session([fred["person_id"]], payer["billing_party_id"])

        record = get_person_record(self.conn, fred["person_id"])
        self.assertEqual(record["billing_summary"]["approved_uninvoiced_sessions"], 1)

    def test_read_operation_creates_no_records(self):
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Smith", "display_name": "Fred Smith"})
        payer = create_billing_party(self.conn, {"billing_name": "Fred Smith", "billing_party_type": "person", "person_id": fred["person_id"]})
        self._approve_session([fred["person_id"]], payer["billing_party_id"])
        self.conn.commit()

        before_people = self.conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
        before_bp = self.conn.execute("SELECT COUNT(*) FROM billing_parties").fetchone()[0]
        before_sessions = self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        before_invoices = self.conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
        before_audit = self.conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]

        get_person_record(self.conn, fred["person_id"])

        after_people = self.conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
        after_bp = self.conn.execute("SELECT COUNT(*) FROM billing_parties").fetchone()[0]
        after_sessions = self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        after_invoices = self.conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
        after_audit = self.conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]

        self.assertEqual(after_people, before_people)
        self.assertEqual(after_bp, before_bp)
        self.assertEqual(after_sessions, before_sessions)
        self.assertEqual(after_invoices, before_invoices)
        self.assertEqual(after_audit, before_audit)

    def test_billing_setup_includes_address_and_delivery_fields(self):
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Smith", "display_name": "Fred Smith"})
        payer = create_billing_party(self.conn, {
            "billing_name": "Fred Smith",
            "billing_party_type": "person",
            "person_id": fred["person_id"],
            "billing_email": "fred@example.com",
            "billing_phone": "555-1234",
            "billing_address_line_1": "123 Main St",
            "billing_city": "Anytown",
            "billing_state": "CA",
            "billing_postal_code": "90210",
            "preferred_delivery_method": "email",
        })

        record = get_person_record(self.conn, fred["person_id"])
        setup = record["billing_setup"]
        self.assertEqual(len(setup), 1)
        self.assertEqual(setup[0]["billing_email"], "fred@example.com")
        self.assertEqual(setup[0]["billing_phone"], "555-1234")
        self.assertEqual(setup[0]["billing_address_line_1"], "123 Main St")
        self.assertEqual(setup[0]["preferred_delivery_method"], "email")

    def test_update_billing_relationship_propagates_to_non_approved_sessions(self):
        from jordana_invoice.review_services import update_billing_relationship

        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Smith", "display_name": "Fred Smith"})
        bobsey = create_person(self.conn, {"first_name": "Bobsey", "last_name": "Smith", "display_name": "Bobsey Smith"})
        account = create_account(self.conn, "Fred Household", "household")
        old_payer = create_billing_party(self.conn, {"billing_name": "Old Payer", "billing_party_type": "person", "person_id": fred["person_id"]})
        new_payer = create_billing_party(self.conn, {"billing_name": "New Payer", "billing_party_type": "person", "person_id": bobsey["person_id"]})

        save_interpretation(
            self.conn,
            self.candidate_id,
            {
                "participants": [
                    {"person_id": fred["person_id"], "display_name": "Fred Smith", "is_primary": True},
                    {"person_id": bobsey["person_id"], "display_name": "Bobsey Smith"},
                ],
                "account_id": account["account_id"],
                "billing_party_id": old_payer["billing_party_id"],
                "approved_duration_minutes": 60,
                "service_mode": "office",
                "time_category": "standard",
                "approved_rate": "150.00",
                "payment_status": "unpaid",
            },
        )

        candidate = get_review_candidate(self.conn, self.candidate_id)
        self.assertEqual(candidate["billing_party"]["billing_name"], "Old Payer")

        update_billing_relationship(
            self.conn,
            account["account_id"],
            {
                "payer_kind": "person",
                "payer_person_id": bobsey["person_id"],
                "covered_client_ids": [fred["person_id"], bobsey["person_id"]],
                "billing_delivery": {"billing_name": "New Payer"},
            },
        )

        candidate = get_review_candidate(self.conn, self.candidate_id)
        self.assertEqual(candidate["billing_party"]["billing_name"], "New Payer")

    def test_update_billing_relationship_does_not_modify_approved_sessions(self):
        from jordana_invoice.review_services import update_billing_relationship

        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Smith", "display_name": "Fred Smith"})
        bobsey = create_person(self.conn, {"first_name": "Bobsey", "last_name": "Smith", "display_name": "Bobsey Smith"})
        account = create_account(self.conn, "Fred Household", "household")
        old_payer = create_billing_party(self.conn, {"billing_name": "Old Payer", "billing_party_type": "person", "person_id": fred["person_id"]})
        new_payer = create_billing_party(self.conn, {"billing_name": "New Payer", "billing_party_type": "person", "person_id": bobsey["person_id"]})

        approve_candidate(
            self.conn,
            self.candidate_id,
            {
                "participants": [
                    {"person_id": fred["person_id"], "display_name": "Fred Smith", "is_primary": True},
                    {"person_id": bobsey["person_id"], "display_name": "Bobsey Smith"},
                ],
                "account_id": account["account_id"],
                "billing_party_id": old_payer["billing_party_id"],
                "approved_duration_minutes": 60,
                "service_mode": "office",
                "time_category": "standard",
                "approved_rate": "150.00",
                "payment_status": "unpaid",
            },
        )

        update_billing_relationship(
            self.conn,
            account["account_id"],
            {
                "payer_kind": "person",
                "payer_person_id": bobsey["person_id"],
                "covered_client_ids": [fred["person_id"], bobsey["person_id"]],
                "billing_delivery": {"billing_name": "New Payer"},
            },
        )

        candidate = get_review_candidate(self.conn, self.candidate_id)
        self.assertEqual(candidate["session"]["review_status"], "approved")
        self.assertEqual(candidate["billing_party"]["billing_name"], "Old Payer")


if __name__ == "__main__":
    unittest.main()
