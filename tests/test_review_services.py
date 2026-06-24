import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.rates import seed_rate_rule
from jordana_invoice.review_services import (
    approve_candidate,
    create_account,
    create_billing_party,
    create_person,
    create_rate_rule_from_payload,
    get_person_record,
    get_review_candidate,
    list_review_candidates,
    recalc_unapproved_session_rates,
    refresh_candidate_suggestions,
    save_person_alias,
    save_billing_section,
    save_relationship_section,
    save_interpretation,
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
        payer = create_billing_party(self.conn, {"billing_name": "Fred Smith", "billing_party_type": "person", "person_id": fred["person_id"]})
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
                "payment_status": "paid",
            },
        )
        self.assertEqual(approved["session"]["review_status"], "approved")
        self.assertEqual(count(self.conn, "sessions"), 1)
        self.assertEqual(count(self.conn, "session_participants"), 2)
        self.assertGreaterEqual(count(self.conn, "calendar_aliases"), 1)
        self.assertGreater(count(self.conn, "audit_log"), 0)

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
        payer = create_billing_party(self.conn, {"billing_name": "Fred Smith", "billing_party_type": "person", "person_id": fred["person_id"]})
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
                "payment_status": "paid",
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


if __name__ == "__main__":
    unittest.main()
