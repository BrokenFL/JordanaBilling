"""Focused tests for weekend session categorization.

Weekend (Saturday/Sunday) psychotherapy sessions must use time category "weekend"
regardless of time of day. The combined "weekend_evening" category is no longer
generated or selectable for new/pending sessions. Historical approved values are
preserved. The rate remains editable and chosen by Jordana case by case.
"""
import re
import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.parser import derive_time_category
from jordana_invoice.rates import seed_rate_rule, suggest_rate
from jordana_invoice.review_services import (
    approve_candidate,
    create_billing_party,
    create_person,
    list_review_candidates,
    normalize_time_category,
    recalc_unapproved_session_rates,
    save_interpretation,
)


def raw_row(snapshot_key, title, start, end):
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
        "end_at": end,
        "duration_minutes": "60",
        "calendar": "Jordana Calendar",
        "payload_version": "2",
        "raw_json": "{}",
    }


JS_PATH = Path(__file__).resolve().parent.parent / "app" / "jordana_invoice" / "static" / "review.js"
HTML_PATH = Path(__file__).resolve().parent.parent / "app" / "jordana_invoice" / "static" / "review.html"


class TestWeekendCategorization(unittest.TestCase):
    """Tests 1-4: Saturday/Sunday daytime and evening all yield 'weekend'."""

    def test_saturday_daytime_is_weekend(self):
        result = derive_time_category("2026-06-20T11:00:00-04:00")
        self.assertEqual(result["time_category"], "weekend")
        self.assertTrue(result["is_weekend"])

    def test_saturday_evening_is_weekend(self):
        result = derive_time_category("2026-06-20T20:30:00-04:00")
        self.assertEqual(result["time_category"], "weekend")
        self.assertTrue(result["is_weekend"])
        self.assertTrue(result["is_evening"])

    def test_sunday_daytime_is_weekend(self):
        result = derive_time_category("2026-06-21T11:00:00-04:00")
        self.assertEqual(result["time_category"], "weekend")
        self.assertTrue(result["is_weekend"])

    def test_sunday_evening_is_weekend(self):
        result = derive_time_category("2026-06-21T20:30:00-04:00")
        self.assertEqual(result["time_category"], "weekend")
        self.assertTrue(result["is_weekend"])
        self.assertTrue(result["is_evening"])


class TestWeekendEveningNotSelectable(unittest.TestCase):
    """Test 5: New UI choices do not include weekend_evening."""

    def test_review_js_no_weekend_evening_in_option_lists(self):
        js = JS_PATH.read_text()
        # optionSet calls for time category must not include weekend_evening
        option_sets = re.findall(r'optionSet\(\[([^\]]+)\]', js)
        for opts in option_sets:
            if "standard" in opts and "evening" in opts:
                self.assertNotIn("weekend_evening", opts,
                                 "Time category optionSet must not include weekend_evening")

    def test_review_html_no_weekend_evening_options(self):
        html = HTML_PATH.read_text()
        self.assertNotIn("weekend_evening", html,
                         "review.html must not contain weekend_evening as a selectable option")


class TestWeekendRateEditable(unittest.TestCase):
    """Tests 6-7: Weekend rate remains editable and no special auto-rate is forced."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "weekend_rate.sqlite3")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_weekend_does_not_force_special_rate(self):
        seed_rate_rule(
            self.conn,
            amount_cents=35000,
            effective_from="2026-01-01",
            duration_minutes=60,
            billing_session_type="psychotherapy",
            time_category="standard",
        )
        suggestion = suggest_rate(
            self.conn,
            session_date="2026-06-20",
            duration_minutes=60,
            service_mode="phone",
            rate_group="remote",
            time_category="weekend",
        )
        self.assertNotEqual(suggestion.rate_source, "manual_review",
                            "Weekend must not trigger manual_review like weekend_evening did")
        self.assertIsNone(suggestion.suggested_rate_cents,
                          "No weekend-specific rule exists; suggestion must be None, not a forced rate")

    def test_weekend_rate_can_be_set_explicitly(self):
        seed_rate_rule(
            self.conn,
            amount_cents=45000,
            effective_from="2026-01-01",
            duration_minutes=60,
            billing_session_type="psychotherapy",
            time_category="weekend",
        )
        suggestion = suggest_rate(
            self.conn,
            session_date="2026-06-20",
            duration_minutes=60,
            billing_session_type="psychotherapy",
            service_mode="phone",
            rate_group="remote",
            time_category="weekend",
        )
        self.assertEqual(suggestion.suggested_rate_cents, 45000)
        self.assertFalse(suggestion.rate_needs_review)


class TestLegacyWeekendEveningNormalization(unittest.TestCase):
    """Test 8: Pending legacy weekend_evening normalizes to weekend on save/recalc."""

    def test_normalize_time_category_maps_weekend_evening_to_weekend(self):
        self.assertEqual(normalize_time_category("weekend_evening"), "weekend")
        self.assertEqual(normalize_time_category("weekend_+_evening"), "weekend")
        self.assertEqual(normalize_time_category("Weekend + Evening"), "weekend")

    def test_normalize_time_category_preserves_standard_evening_weekend(self):
        self.assertEqual(normalize_time_category("standard"), "standard")
        self.assertEqual(normalize_time_category("evening"), "evening")
        self.assertEqual(normalize_time_category("weekend"), "weekend")


class TestApprovedHistoricalPreserved(unittest.TestCase):
    """Test 9: Approved historical weekend_evening remains unchanged."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "hist.sqlite3")
        init_db(self.conn)
        import_rows(
            self.conn,
            [raw_row("snap-hist", "Alice Smith 6", "2026-06-20T20:00:00-04:00", "2026-06-20T21:00:00-04:00")],
            "test",
        )
        candidate = list_review_candidates(self.conn)["items"][0]
        self.candidate_id = candidate["candidate_id"]
        person = create_person(self.conn, {"first_name": "Alice", "last_name": "Smith", "display_name": "Alice Smith"})
        payer = create_billing_party(self.conn, {"billing_name": "Alice Smith", "billing_party_type": "person", "person_id": person["person_id"]})
        approve_candidate(self.conn, self.candidate_id, {
            "participants": [{"person_id": person["person_id"], "display_name": "Alice Smith", "is_primary": True}],
            "billing_party_id": payer["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "phone",
            "time_category": "weekend",
            "approved_rate": "200.00",
            "payment_status": "unpaid",
        })
        # Simulate a historical record that was approved before the fix:
        # directly set time_category to weekend_evening as it would have been stored.
        self.conn.execute(
            "UPDATE sessions SET time_category = 'weekend_evening' WHERE candidate_id = ?",
            (self.candidate_id,),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_approved_weekend_evening_time_category_preserved(self):
        row = self.conn.execute(
            "SELECT time_category, approved_rate_cents FROM sessions WHERE candidate_id = ?",
            (self.candidate_id,),
        ).fetchone()
        self.assertEqual(row["time_category"], "weekend_evening",
                         "Approved historical weekend_evening must be preserved as-is")
        self.assertEqual(row["approved_rate_cents"], 20000)

    def test_recalc_does_not_touch_approved_sessions(self):
        updated = recalc_unapproved_session_rates(self.conn)
        self.assertEqual(updated, 0, "No unapproved sessions to recalc")
        row = self.conn.execute(
            "SELECT time_category FROM sessions WHERE candidate_id = ?",
            (self.candidate_id,),
        ).fetchone()
        self.assertEqual(row["time_category"], "weekend_evening")


class TestPendingLegacyNormalizesOnSave(unittest.TestCase):
    """Test 8b: Pending weekend_evening normalizes to weekend when saved via save_interpretation."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "pend.sqlite3")
        init_db(self.conn)
        import_rows(
            self.conn,
            [raw_row("snap-pend", "Bob Smith 6", "2026-06-20T20:00:00-04:00", "2026-06-20T21:00:00-04:00")],
            "test",
        )
        candidate = list_review_candidates(self.conn)["items"][0]
        self.candidate_id = candidate["candidate_id"]
        person = create_person(self.conn, {"first_name": "Bob", "last_name": "Smith", "display_name": "Bob Smith"})
        payer = create_billing_party(self.conn, {"billing_name": "Bob Smith", "billing_party_type": "person", "person_id": person["person_id"]})
        self.person_id = person["person_id"]
        self.payer_id = payer["billing_party_id"]

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_save_interpretation_normalizes_weekend_evening_to_weekend(self):
        saved = save_interpretation(self.conn, self.candidate_id, {
            "participants": [{"person_id": self.person_id, "display_name": "Bob Smith", "is_primary": True}],
            "billing_party_id": self.payer_id,
            "approved_duration_minutes": 60,
            "service_mode": "phone",
            "time_category": "weekend_evening",
            "approved_rate": "150.00",
            "payment_status": "unpaid",
        })
        self.assertEqual(saved["session"]["time_category"], "weekend",
                         "Pending session saved with weekend_evening must normalize to weekend")


class TestWeekdayEveningUnchanged(unittest.TestCase):
    """Test 10: Weekday evening behavior remains unchanged."""

    def test_weekday_evening_is_evening(self):
        result = derive_time_category("2026-06-18T20:30:00-04:00")
        self.assertEqual(result["time_category"], "evening")
        self.assertFalse(result["is_weekend"])
        self.assertTrue(result["is_evening"])

    def test_weekday_standard_is_standard(self):
        result = derive_time_category("2026-06-18T11:00:00-04:00")
        self.assertEqual(result["time_category"], "standard")
        self.assertFalse(result["is_weekend"])
        self.assertFalse(result["is_evening"])


class TestNoSchemaMigration(unittest.TestCase):
    """Test 12: No schema migration is added."""

    def test_no_migration_files_added(self):
        migrations_dir = Path(__file__).resolve().parent.parent / "app" / "jordana_invoice" / "migrations"
        self.assertFalse(migrations_dir.exists(),
                         "No migrations directory should exist for this change")


class TestNoInvoiceOrPaymentChanges(unittest.TestCase):
    """Test 11: No invoice or payment history changes."""

    def test_invoice_services_still_references_weekend_evening_for_read_compat(self):
        from jordana_invoice.invoice_services import _service_description
        from jordana_invoice.db import connect, init_db
        import tempfile
        from pathlib import Path
        tmp = tempfile.TemporaryDirectory()
        conn = connect(Path(tmp.name) / "inv.sqlite3")
        init_db(conn)
        from jordana_invoice.importer import import_rows
        import_rows(
            conn,
            [raw_row("snap-inv", "Carol Smith 6", "2026-06-20T20:00:00-04:00", "2026-06-20T21:00:00-04:00")],
            "test",
        )
        from jordana_invoice.review_services import list_review_candidates, approve_candidate, create_person, create_billing_party
        candidate = list_review_candidates(conn)["items"][0]
        cid = candidate["candidate_id"]
        person = create_person(conn, {"first_name": "Carol", "last_name": "Smith", "display_name": "Carol Smith"})
        payer = create_billing_party(conn, {"billing_name": "Carol Smith", "billing_party_type": "person", "person_id": person["person_id"]})
        approve_candidate(conn, cid, {
            "participants": [{"person_id": person["person_id"], "display_name": "Carol Smith", "is_primary": True}],
            "billing_party_id": payer["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "phone",
            "time_category": "weekend",
            "approved_rate": "150.00",
            "payment_status": "unpaid",
        })
        # Simulate historical record with weekend_evening stored
        conn.execute("UPDATE sessions SET time_category = 'weekend_evening' WHERE candidate_id = ?", (cid,))
        conn.commit()
        session = conn.execute("SELECT * FROM sessions WHERE candidate_id = ?", (cid,)).fetchone()
        label = _service_description(session, "Psychotherapy Session")
        self.assertIn("Weekend", label,
                      "Read compatibility: approved weekend_evening session label must still render")
        conn.close()
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
