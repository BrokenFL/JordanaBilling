import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import (
    create_invoice_draft,
    finalize_invoice,
    get_invoice,
    save_business_profile,
)
from jordana_invoice.review_services import (
    approve_candidate,
    create_account,
    create_billing_party,
    create_person,
    create_rate_rule_from_payload,
    end_rate_rule,
    get_review_candidate,
    list_review_candidates,
    preview_rate_suggestion,
    replace_rate_rule_from_payload,
    save_relationship_section,
    save_session_draft,
)


def raw_row(
    snapshot_key,
    title,
    start="2026-01-15T18:30:00-05:00",
    end="2026-01-15T19:30:00-05:00",
    duration="60",
):
    return {
        "ingested_at": "2026-06-22T02:00:00.000Z",
        "snapshot_key": snapshot_key,
        "run_id": f"run-{snapshot_key}",
        "batch_name": "test",
        "capture_window": "next_2_days",
        "captured_at": "2026-06-22T01:00:00.000Z",
        "source_device": "test",
        "timezone": "America/New_York",
        "calendar_event_id": "",
        "event_fingerprint": f"fp-{snapshot_key}",
        "event_title": title,
        "start_at": start,
        "end_at": end,
        "duration_minutes": duration,
        "calendar": "Jordana Calendar",
        "payload_version": "2",
        "raw_json": "{}",
    }


class RateCardDefaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "rate-card.sqlite3")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def import_one(
        self,
        key,
        title,
        start="2026-01-15T18:30:00-05:00",
        end="2026-01-15T19:30:00-05:00",
        duration="60",
    ):
        """Import a single raw row and return its candidate_id.

        The title must parse as a client_session (e.g. 'Fred 630 60') so that
        a session row is created.  list_review_candidates only surfaces
        session-backed candidates, so a StopIteration here means the title
        failed to produce a session.
        """
        import_rows(self.conn, [raw_row(key, title, start, end, duration)], "test")
        rows = list_review_candidates(self.conn)["items"]
        return next(row["candidate_id"] for row in rows if row["raw_title"] == title)

    def _create_default_rule(self):
        return create_rate_rule_from_payload(
            self.conn,
            {
                "amount": "350",
                "duration_minutes": "60",
                "billing_session_type": "psychotherapy",
                "time_category": "standard",
                "applies_to": "everyone",
                "effective_from": "2026-01-01",
            },
        )

    def _create_rate_rule(
        self,
        *,
        amount,
        duration_minutes,
        billing_session_type,
        time_category="standard",
        applies_to="everyone",
        **extra,
    ):
        payload = {
            "amount": str(amount),
            "duration_minutes": str(duration_minutes),
            "billing_session_type": billing_session_type,
            "time_category": time_category,
            "applies_to": applies_to,
            "effective_from": "2026-01-01",
        }
        payload.update(extra)
        return create_rate_rule_from_payload(self.conn, payload)

    # ── Rule creation ────────────────────────────────────────────────────────

    def test_create_default_rate_rule(self):
        rule = self._create_default_rule()
        self.assertEqual(rule["amount_cents"], 35000)
        self.assertEqual(rule["duration_minutes"], 60)
        self.assertEqual(rule["billing_session_type"], "psychotherapy")
        self.assertEqual(rule["appointment_status"], "scheduled")
        self.assertEqual(rule["time_category"], "standard")
        self.assertIsNone(rule["client_account_id"])
        self.assertIsNone(rule["person_id"])

    # ── Rate matching ────────────────────────────────────────────────────────

    def test_default_rate_applies_to_matching_unapproved_session(self):
        # Weekday, 6:30 PM EST → not evening (< 20:00) → billing_session_type = psychotherapy
        candidate_id = self.import_one("snap-match", "Fred 630 60")
        self._create_default_rule()
        detail = get_review_candidate(self.conn, candidate_id)
        self.assertEqual(detail["session"]["suggested_rate_cents"], 35000)
        self.assertEqual(detail["session"]["rate_source"], "default")
        self.assertIsNone(detail["session"]["approved_rate_cents"])

    def test_manual_weekend_session_type_uses_person_weekend_exception(self):
        candidate_id = self.import_one("snap-person-weekend", "Fred 630 60")
        fred = create_person(self.conn, "Fred Smith")
        save_relationship_section(
            self.conn,
            candidate_id,
            {"participants": [{"person_id": fred["person_id"], "display_name": "Fred Smith", "is_primary": True}]},
        )
        create_rate_rule_from_payload(
            self.conn,
            {
                "amount": "410",
                "duration_choice": "60",
                "billing_session_type": "psychotherapy_weekend",
                "time_category": "weekend",
                "applies_to": "person",
                "person_id": fred["person_id"],
                "effective_from": "2026-01-01",
            },
        )

        detail = save_session_draft(
            self.conn,
            candidate_id,
            {
                "approved_duration_minutes": 60,
                "billing_session_type": "psychotherapy_weekend",
                "time_category": "weekend",
                "approved_rate": "",
                "payment_status": "unpaid",
            },
        )

        self.assertEqual(detail["session"]["suggested_rate_cents"], 41000)
        self.assertEqual(detail["session"]["rate_source"], "person_exception")

    def test_manual_evening_session_type_uses_joint_evening_exception(self):
        candidate_id = self.import_one("snap-joint-evening", "Fred Bobsey 630 60")
        fred = create_person(self.conn, "Fred Smith")
        bobsey = create_person(self.conn, "Bobsey Smith")
        participants = [
            {"person_id": fred["person_id"], "display_name": "Fred Smith", "is_primary": True},
            {"person_id": bobsey["person_id"], "display_name": "Bobsey Smith"},
        ]
        save_relationship_section(self.conn, candidate_id, {"participants": participants})
        create_rate_rule_from_payload(
            self.conn,
            {
                "amount": "475",
                "duration_choice": "60",
                "billing_session_type": "psychotherapy_evening",
                "time_category": "evening",
                "applies_to": "participants",
                "participant_person_ids": [fred["person_id"], bobsey["person_id"]],
                "effective_from": "2026-01-01",
            },
        )

        detail = save_session_draft(
            self.conn,
            candidate_id,
            {
                "approved_duration_minutes": 60,
                "billing_session_type": "psychotherapy_evening",
                "time_category": "evening",
                "approved_rate": "",
                "payment_status": "unpaid",
            },
        )

        self.assertEqual(detail["session"]["suggested_rate_cents"], 47500)
        self.assertEqual(detail["session"]["rate_source"], "participant_combination_exception")

    def test_cancelled_session_requires_exact_cancelled_rule(self):
        candidate_id = self.import_one("snap-cancelled", "Fred 630 60 cancelled")
        self._create_default_rule()
        detail = get_review_candidate(self.conn, candidate_id)
        self.assertEqual(detail["session"]["appointment_status"], "cancelled")
        self.assertIsNone(detail["session"]["suggested_rate_cents"])
        cancelled_rule = self._create_rate_rule(
            amount=175,
            duration_minutes=60,
            billing_session_type="psychotherapy",
            appointment_status="cancelled",
            time_category="standard",
        )
        refreshed = get_review_candidate(self.conn, candidate_id)
        self.assertEqual(refreshed["session"]["suggested_rate_cents"], 17500)
        self.assertEqual(refreshed["session"]["rate_rule_id"], cancelled_rule["rate_rule_id"])

    def test_completed_session_uses_scheduled_rate_rule_dimension(self):
        candidate_id = self.import_one("snap-completed", "Fred 630 60")
        detail = get_review_candidate(self.conn, candidate_id)
        self.assertEqual(detail["session"]["appointment_status"], "completed")
        self._create_default_rule()
        refreshed = get_review_candidate(self.conn, candidate_id)
        self.assertEqual(refreshed["session"]["suggested_rate_cents"], 35000)

    def test_later_matching_session_gets_same_suggestion(self):
        # Rule exists before the session is imported.
        # With billing_session_type now passed at import time, the rule is
        # matched immediately in maybe_insert_session.
        self._create_default_rule()
        # 2026-02-10 is a Tuesday; 7:30 PM EST → hour 19 < 20 → not evening → psychotherapy
        # (February avoids the March DST transition that would shift hour to 20)
        candidate_id = self.import_one(
            "snap-later",
            "Fred 730 60",
            start="2026-02-10T19:30:00-05:00",
            end="2026-02-10T20:30:00-05:00",
        )
        detail = get_review_candidate(self.conn, candidate_id)
        self.assertEqual(detail["session"]["suggested_rate_cents"], 35000)
        self.assertEqual(detail["session"]["rate_source"], "default")

    def test_default_rate_effective_date_boundary(self):
        # Session on 2025-12-31 is before effective_from 2026-01-01 → no match
        # Session on 2026-01-01 is on effective_from → matches
        before_id = self.import_one(
            "snap-before",
            "Before 530 60",
            start="2025-12-31T17:30:00-05:00",
            end="2025-12-31T18:30:00-05:00",
        )
        on_id = self.import_one(
            "snap-on",
            "After 530 60",
            start="2026-01-01T17:30:00-05:00",
            end="2026-01-01T18:30:00-05:00",
        )
        self._create_default_rule()
        before = get_review_candidate(self.conn, before_id)
        on = get_review_candidate(self.conn, on_id)
        self.assertNotEqual(before["session"]["suggested_rate_cents"], 35000)
        self.assertEqual(before["session"]["rate_needs_review"], 1)
        self.assertEqual(on["session"]["suggested_rate_cents"], 35000)
        self.assertEqual(on["session"]["rate_needs_review"], 0)

    def test_default_rate_does_not_apply_different_duration(self):
        # Title "Fred 630 90" → explicit duration=90 from title; rule requires 60 → no match
        candidate_id = self.import_one("snap-90", "Fred 630 90")
        self._create_default_rule()
        detail = get_review_candidate(self.conn, candidate_id)
        self.assertNotEqual(detail["session"]["suggested_rate_cents"], 35000)
        self.assertEqual(detail["session"]["rate_needs_review"], 1)

    def test_default_rate_does_not_apply_custom_session_type(self):
        candidate_id = self.import_one(
            "snap-custom",
            "Fred 630 60",
        )
        self.conn.execute(
            "UPDATE sessions SET billing_session_type = ? WHERE candidate_id = ?",
            ("custom", candidate_id),
        )
        self.conn.commit()
        self._create_default_rule()
        detail = get_review_candidate(self.conn, candidate_id)
        self.assertNotEqual(detail["session"]["suggested_rate_cents"], 35000)
        self.assertEqual(detail["session"]["rate_needs_review"], 1)

    def test_evening_without_exact_rule_needs_rate(self):
        candidate_id = self.import_one(
            "snap-evening-fallback",
            "Fred 830 60",
            start="2026-02-10T20:30:00-05:00",
            end="2026-02-10T21:30:00-05:00",
        )
        self._create_default_rule()
        detail = get_review_candidate(self.conn, candidate_id)
        self.assertEqual(detail["session"]["billing_session_type"], "psychotherapy_evening")
        self.assertIsNone(detail["session"]["suggested_rate_cents"])
        self.assertEqual(detail["session"]["rate_needs_review"], 1)

    def test_evening_specific_rule_overrides_base(self):
        candidate_id = self.import_one(
            "snap-evening-specific",
            "Fred 830 60",
            start="2026-02-10T20:30:00-05:00",
            end="2026-02-10T21:30:00-05:00",
        )
        self._create_default_rule()
        self._create_rate_rule(
            amount=425,
            duration_minutes=60,
            billing_session_type="psychotherapy_evening",
            time_category="evening",
        )
        detail = get_review_candidate(self.conn, candidate_id)
        self.assertEqual(detail["session"]["suggested_rate_cents"], 42500)
        self.assertEqual(detail["session"]["rate_source"], "default")

    def test_weekend_and_house_call_without_exact_rule_need_rate(self):
        weekend_id = self.import_one(
            "snap-weekend-fallback",
            "Fred 530 60",
            start="2026-01-17T14:00:00-05:00",
            end="2026-01-17T15:00:00-05:00",
        )
        house_call_id = self.import_one("snap-house-fallback", "Fred 630 60")
        self.conn.execute(
            "UPDATE sessions SET billing_session_type = ? WHERE candidate_id = ?",
            ("psychotherapy_house_call", house_call_id),
        )
        self.conn.commit()
        self._create_default_rule()
        weekend = get_review_candidate(self.conn, weekend_id)
        house_call = get_review_candidate(self.conn, house_call_id)
        self.assertEqual(weekend["session"]["billing_session_type"], "psychotherapy_weekend")
        self.assertIsNone(weekend["session"]["suggested_rate_cents"])
        self.assertEqual(weekend["session"]["rate_needs_review"], 1)
        self.assertEqual(house_call["session"]["billing_session_type"], "psychotherapy_house_call")
        self.assertIsNone(house_call["session"]["suggested_rate_cents"])
        self.assertEqual(house_call["session"]["rate_needs_review"], 1)

    def test_different_duration_does_not_fall_back_from_evening_to_base(self):
        candidate_id = self.import_one(
            "snap-evening-90",
            "Fred 830 90",
            start="2026-02-10T20:30:00-05:00",
            end="2026-02-10T22:00:00-05:00",
            duration="90",
        )
        self._create_default_rule()
        detail = get_review_candidate(self.conn, candidate_id)
        self.assertEqual(detail["session"]["billing_session_type"], "psychotherapy_evening")
        self.assertIsNone(detail["session"]["suggested_rate_cents"])
        self.assertEqual(detail["session"]["rate_needs_review"], 1)

    def test_approved_historical_evening_rate_remains_unchanged(self):
        candidate_id = self.import_one(
            "snap-approved-evening",
            "Fred 830 60",
            start="2026-02-10T20:30:00-05:00",
            end="2026-02-10T21:30:00-05:00",
        )
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Colin", "display_name": "Fred Colin"})
        payer = create_billing_party(self.conn, {"billing_name": "Fred Colin", "person_id": fred["person_id"]})
        approve_candidate(
            self.conn,
            candidate_id,
            {
                "participants": [{"person_id": fred["person_id"], "display_name": "Fred Colin", "is_primary": True}],
                "billing_party_id": payer["billing_party_id"],
                "approved_duration_minutes": 60,
                "service_mode": "phone",
                "time_category": "evening",
                "approved_rate": "425.00",
                "payment_status": "unpaid",
            },
        )
        self._create_default_rule()
        detail = get_review_candidate(self.conn, candidate_id)
        self.assertEqual(detail["session"]["billing_session_type"], "psychotherapy_evening")
        self.assertEqual(detail["session"]["approved_rate_cents"], 42500)
        self.assertEqual(detail["session"]["rate_cents_snapshot"], 42500)
        self.assertEqual(detail["session"]["review_status"], "approved")

    # ── Priority overrides ───────────────────────────────────────────────────

    def test_client_specific_rule_overrides_default(self):
        candidate_id = self.import_one("snap-acct", "Fred 630 60")
        account = create_account(self.conn, "Fred Household", "household")
        self.conn.execute(
            "UPDATE sessions SET account_id = ? WHERE candidate_id = ?",
            (account["account_id"], candidate_id),
        )
        self.conn.commit()
        self._create_default_rule()
        create_rate_rule_from_payload(
            self.conn,
            {
                "amount": "400",
                "duration_minutes": "60",
                "billing_session_type": "psychotherapy",
                "time_category": "standard",
                "applies_to": "account",
                "client_account_id": account["account_id"],
                "effective_from": "2026-01-01",
            },
        )
        detail = get_review_candidate(self.conn, candidate_id)
        self.assertEqual(detail["session"]["suggested_rate_cents"], 40000)
        self.assertEqual(detail["session"]["rate_source"], "billing_relationship")

    def test_person_specific_rule_overrides_default(self):
        candidate_id = self.import_one("snap-person", "Fred 630 60")
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Colin", "display_name": "Fred Colin"})
        payer = create_billing_party(self.conn, {"billing_name": "Fred Colin", "person_id": fred["person_id"]})
        save_relationship_section(
            self.conn,
            candidate_id,
            {
                "participants": [{"person_id": fred["person_id"], "display_name": "Fred Colin", "is_primary": True}],
                "billing_party_id": payer["billing_party_id"],
            },
        )
        self._create_default_rule()
        create_rate_rule_from_payload(
            self.conn,
            {
                "amount": "500",
                "duration_minutes": "60",
                "billing_session_type": "psychotherapy",
                "time_category": "standard",
                "applies_to": "person",
                "person_id": fred["person_id"],
                "effective_from": "2026-01-01",
            },
        )
        detail = get_review_candidate(self.conn, candidate_id)
        self.assertEqual(detail["session"]["suggested_rate_cents"], 50000)
        self.assertEqual(detail["session"]["rate_source"], "person_exception")

    def test_participant_combination_rule_overrides_default(self):
        # "Bobsey and Fred 630 60" → multi-person but still client_session
        candidate_id = self.import_one("snap-joint", "Bobsey and Fred 630 60")
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Colin", "display_name": "Fred Colin"})
        bobsey = create_person(self.conn, {"first_name": "Bobsey", "last_name": "Colin", "display_name": "Bobsey Colin"})
        payer = create_billing_party(self.conn, {"billing_name": "Fred Colin", "person_id": fred["person_id"]})
        save_relationship_section(
            self.conn,
            candidate_id,
            {
                "participants": [
                    {"person_id": fred["person_id"], "display_name": "Fred Colin", "is_primary": True},
                    {"person_id": bobsey["person_id"], "display_name": "Bobsey Colin"},
                ],
                "billing_party_id": payer["billing_party_id"],
            },
        )
        self._create_default_rule()
        create_rate_rule_from_payload(
            self.conn,
            {
                "amount": "450",
                "duration_minutes": "60",
                "billing_session_type": "psychotherapy",
                "time_category": "standard",
                "applies_to": "participants",
                "participant_person_ids": [fred["person_id"], bobsey["person_id"]],
                "effective_from": "2026-01-01",
            },
        )
        detail = get_review_candidate(self.conn, candidate_id)
        self.assertEqual(detail["session"]["suggested_rate_cents"], 45000)
        self.assertEqual(detail["session"]["rate_source"], "participant_combination_exception")

    def test_session_specific_approved_rate_overrides_default(self):
        # A manually-entered approved_rate survives recalculation: approved_rate_cents
        # is preserved while suggested_rate_cents is updated to the rule value.
        candidate_id = self.import_one("snap-manual", "Fred 630 60")
        save_session_draft(
            self.conn,
            candidate_id,
            {
                "approved_duration_minutes": 60,
                "service_mode": "phone",
                "time_category": "standard",
                "approved_rate": "425.00",
                "payment_status": "unpaid",
            },
        )
        self._create_default_rule()
        detail = get_review_candidate(self.conn, candidate_id)
        self.assertEqual(detail["session"]["approved_rate_cents"], 42500)
        self.assertEqual(detail["session"]["suggested_rate_cents"], 35000)

    # ── Immutability ─────────────────────────────────────────────────────────

    def test_approved_session_rate_is_unchanged_by_new_default(self):
        candidate_id = self.import_one("snap-approved", "Fred 630 60")
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Colin", "display_name": "Fred Colin"})
        payer = create_billing_party(self.conn, {"billing_name": "Fred Colin", "person_id": fred["person_id"]})
        approve_candidate(
            self.conn,
            candidate_id,
            {
                "participants": [{"person_id": fred["person_id"], "display_name": "Fred Colin", "is_primary": True}],
                "billing_party_id": payer["billing_party_id"],
                "approved_duration_minutes": 60,
                "service_mode": "phone",
                "time_category": "standard",
                "approved_rate": "425.00",
                "payment_status": "unpaid",
            },
        )
        self._create_default_rule()
        detail = get_review_candidate(self.conn, candidate_id)
        self.assertEqual(detail["session"]["approved_rate_cents"], 42500)
        self.assertEqual(detail["session"]["rate_cents_snapshot"], 42500)
        self.assertEqual(detail["session"]["review_status"], "approved")

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_finalized_invoice_snapshot_unchanged_by_new_default(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        # 2026-06-01 is a Monday; 6:30 PM EDT (hour 18 < 20) → not evening → psychotherapy
        candidate_id = self.import_one(
            "snap-invoice",
            "Fred 630 60",
            start="2026-06-01T18:30:00-04:00",
            end="2026-06-01T19:30:00-04:00",
        )
        fred = create_person(self.conn, {"first_name": "Fred", "last_name": "Colin", "display_name": "Fred Colin"})
        payer = create_billing_party(self.conn, {
            "billing_name": "Fred Colin",
            "person_id": fred["person_id"],
            "billing_email": "fred@example.test",
            "preferred_delivery_method": "email",
        })
        approved = approve_candidate(
            self.conn,
            candidate_id,
            {
                "participants": [{"person_id": fred["person_id"], "display_name": "Fred Colin", "is_primary": True}],
                "billing_party_id": payer["billing_party_id"],
                "approved_duration_minutes": 60,
                "service_mode": "phone",
                "time_category": "standard",
                "approved_rate": "425.00",
                "payment_status": "unpaid",
            },
        )
        save_business_profile(self.conn, {
            "business_name": "Test Practice",
            "provider_display_name": "Test Provider",
            "payee_name": "Test Payee",
            "payment_address_line_1": "100 Test Ave",
            "payment_city": "Test",
            "payment_state": "FL",
            "payment_postal_code": "00000", "zelle_recipient": "demo-zelle@example.test",
            "invoice_total_label": "TOTAL DUE",
            "invoice_number_format": "YYYY-NNNN",
        })
        invoice = create_invoice_draft(self.conn, {
            "bill_to_party_id": payer["billing_party_id"],
            "billing_period_start": "2026-06-01",
            "billing_period_end": "2026-06-30",
            "invoice_date": "2026-06-30",
            "delivery_method": "email",
            "session_ids": [approved["session"]["id"]],
        })
        final = finalize_invoice(self.conn, invoice["invoice"]["invoice_id"])
        line_amount_before = final["lines"][0]["line_amount_cents"]
        self._create_default_rule()
        after = get_invoice(self.conn, invoice["invoice"]["invoice_id"])
        self.assertEqual(after["invoice"]["status"], "finalized")
        self.assertEqual(after["lines"][0]["line_amount_cents"], line_amount_before)
        self.assertEqual(after["lines"][0]["line_amount_cents"], 42500)

    # ── Duplicate / validation ───────────────────────────────────────────────

    def test_repeated_default_rule_creation_blocked(self):
        self._create_default_rule()
        with self.assertRaises(ValueError):
            self._create_default_rule()

    def test_invalid_rate_rule_payload_raises(self):
        with self.assertRaises(ValueError):
            create_rate_rule_from_payload(self.conn, {
                "amount": "",
                "duration_minutes": "60",
                "billing_session_type": "psychotherapy",
                "time_category": "standard",
                "applies_to": "everyone",
                "effective_from": "2026-01-01",
            })
        with self.assertRaises(ValueError):
            create_rate_rule_from_payload(self.conn, {
                "amount": "350",
                "duration_minutes": "60",
                "billing_session_type": "psychotherapy",
                "time_category": "standard",
                "applies_to": "everyone",
                "effective_from": "not-a-date",
            })
        with self.assertRaises(ValueError):
            create_rate_rule_from_payload(self.conn, {
                "amount": "350",
                "duration_choice": "60",
                "billing_session_type": "psychotherapy",
                "time_category": "standard",
                "applies_to": "person",
                "effective_from": "2026-01-01",
            })
        with self.assertRaises(ValueError):
            create_rate_rule_from_payload(self.conn, {
                "amount": "350",
                "duration_choice": "60",
                "billing_session_type": "psychotherapy",
                "time_category": "standard",
                "applies_to": "participants",
                "participant_person_ids": [],
                "effective_from": "2026-01-01",
            })

    def test_preview_rate_suggestion_uses_global_rule(self):
        self._create_default_rule()
        preview = preview_rate_suggestion(self.conn, {
            "duration_choice": "60",
            "billing_session_type": "psychotherapy",
            "time_category": "standard",
            "service_mode": "office",
            "session_date": "2026-06-18",
        })
        self.assertEqual(preview["amount_cents"], 35000)
        self.assertEqual(preview["rate_source"], "default")

    def test_custom_rule_matches_by_code_then_description(self):
        create_rate_rule_from_payload(self.conn, {
            "amount": "275",
            "duration_choice": "custom",
            "custom_duration_minutes": "75",
            "billing_session_type": "custom",
            "custom_service_description": "Parent coaching",
            "custom_service_code": "PC-75",
            "time_category": "standard",
            "applies_to": "everyone",
            "effective_from": "2026-01-01",
        })
        by_code = preview_rate_suggestion(self.conn, {
            "duration_choice": "custom",
            "custom_duration_minutes": "75",
            "billing_session_type": "custom",
            "custom_service_description": "Something Else",
            "custom_service_code": "PC-75",
            "time_category": "standard",
            "service_mode": "office",
            "session_date": "2026-06-18",
        })
        by_description = preview_rate_suggestion(self.conn, {
            "duration_choice": "custom",
            "custom_duration_minutes": "75",
            "billing_session_type": "custom",
            "custom_service_description": " parent   coaching ",
            "time_category": "standard",
            "service_mode": "office",
            "session_date": "2026-06-18",
        })
        self.assertEqual(by_code["amount_cents"], 27500)
        self.assertEqual(by_description["amount_cents"], 27500)

    def test_known_client_custom_code_rate_overrides_global_default(self):
        client = create_person(self.conn, {"first_name": "Fred", "last_name": "Colin", "display_name": "Fred Colin"})
        self._create_rate_rule(
            amount=350,
            duration_minutes=75,
            billing_session_type="custom",
            custom_service_description="Parent coaching",
            time_category="standard",
        )
        create_rate_rule_from_payload(self.conn, {
            "amount": "425",
            "duration_choice": "custom",
            "custom_duration_minutes": "75",
            "billing_session_type": "custom",
            "custom_service_code": "PC-75",
            "time_category": "standard",
            "applies_to": "person",
            "person_id": client["person_id"],
            "effective_from": "2026-01-01",
        })

        preview = preview_rate_suggestion(self.conn, {
            "duration_choice": "custom",
            "custom_duration_minutes": "75",
            "billing_session_type": "custom",
            "custom_service_code": "pc 75",
            "time_category": "standard",
            "service_mode": "office",
            "session_date": "2026-06-18",
            "person_id": client["person_id"],
            "participant_person_ids": [client["person_id"]],
        })

        self.assertEqual(preview["amount_cents"], 42500)
        self.assertEqual(preview["rate_source"], "person_exception")

    def test_replace_rule_ends_old_day_before_new_effective_date(self):
        original = self._create_default_rule()
        replacement = replace_rate_rule_from_payload(self.conn, original["rate_rule_id"], {
            "amount": "390",
            "duration_choice": "60",
            "billing_session_type": "psychotherapy",
            "time_category": "standard",
            "effective_from": "2026-03-01",
        })
        old_row = self.conn.execute(
            "SELECT effective_through FROM rate_rules WHERE rate_rule_id = ?",
            (original["rate_rule_id"],),
        ).fetchone()
        self.assertEqual(old_row["effective_through"], "2026-02-28")
        self.assertEqual(replacement["amount_cents"], 39000)

    def test_end_rule_sets_effective_through_and_clears_stale_rule_suggestion(self):
        candidate_id = self.import_one("snap-end", "Fred 630 60")
        rule = self._create_default_rule()
        before = get_review_candidate(self.conn, candidate_id)
        self.assertEqual(before["session"]["suggested_rate_cents"], 35000)
        ended = end_rate_rule(self.conn, rule["rate_rule_id"], "2026-01-14")
        after = get_review_candidate(self.conn, candidate_id)
        self.assertEqual(ended["effective_through"], "2026-01-14")
        self.assertIsNone(after["session"]["suggested_rate_cents"])
        self.assertEqual(after["session"]["rate_source"], "none")
        self.assertEqual(after["session"]["rate_needs_review"], 1)

    # ── Exact dimension matching ─────────────────────────────────────────────

    def test_standard_rule_does_not_match_evening_session(self):
        candidate_id = self.import_one(
            "snap-exact-eve-neg",
            "Fred 830 60",
            start="2026-02-10T20:30:00-05:00",
            end="2026-02-10T21:30:00-05:00",
        )
        self._create_default_rule()  # psychotherapy / standard / 60-min
        detail = get_review_candidate(self.conn, candidate_id)
        self.assertEqual(detail["session"]["billing_session_type"], "psychotherapy_evening")
        self.assertIsNone(detail["session"]["suggested_rate_cents"])
        self.assertEqual(detail["session"]["rate_needs_review"], 1)

    def test_exact_standard_rule_matches_standard_session(self):
        candidate_id = self.import_one("snap-exact-std", "Fred 630 60")
        self._create_default_rule()  # psychotherapy / standard / 60-min
        detail = get_review_candidate(self.conn, candidate_id)
        self.assertEqual(detail["session"]["billing_session_type"], "psychotherapy")
        self.assertEqual(detail["session"]["suggested_rate_cents"], 35000)
        self.assertEqual(detail["session"]["rate_needs_review"], 0)

    def test_exact_evening_rule_matches_evening_session(self):
        candidate_id = self.import_one(
            "snap-exact-eve-pos",
            "Fred 830 60",
            start="2026-02-10T20:30:00-05:00",
            end="2026-02-10T21:30:00-05:00",
        )
        self._create_rate_rule(
            amount=425,
            duration_minutes=60,
            billing_session_type="psychotherapy_evening",
            time_category="evening",
        )
        detail = get_review_candidate(self.conn, candidate_id)
        self.assertEqual(detail["session"]["billing_session_type"], "psychotherapy_evening")
        self.assertEqual(detail["session"]["suggested_rate_cents"], 42500)
        self.assertEqual(detail["session"]["rate_needs_review"], 0)

    def test_phone_facetime_and_office_share_equivalent_rate_matching(self):
        create_rate_rule_from_payload(self.conn, {
            "amount": "200",
            "duration_choice": "60",
            "billing_session_type": "psychotherapy",
            "time_category": "standard",
            "applies_to": "everyone",
            "effective_from": "2026-01-01",
        })
        for service_mode in ("phone", "facetime", "office"):
            preview = preview_rate_suggestion(self.conn, {
                "duration_choice": "60",
                "billing_session_type": "psychotherapy",
                "time_category": "standard",
                "service_mode": service_mode,
                "session_date": "2026-06-18",
            })
            self.assertEqual(preview["amount_cents"], 20000)


if __name__ == "__main__":
    unittest.main()
