import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import connect, init_db
from jordana_invoice.rates import (
    WEEKEND_EVENING_POLICY,
    seed_rate_rule,
    set_rate_policy,
    suggest_rate,
)


class Phase2RateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "test.sqlite3")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_global_remote_rate(self):
        seed_rate_rule(
            self.conn,
            amount_cents=15000,
            effective_from="2026-01-01",
            duration_minutes=60,
            rate_group="remote",
        )
        suggestion = suggest_rate(
            self.conn,
            session_date="2026-06-18",
            duration_minutes=60,
            service_mode="phone",
            rate_group="remote",
            time_category="standard",
        )
        self.assertEqual(suggestion.suggested_rate_cents, 15000)
        self.assertFalse(suggestion.rate_needs_review)

    def test_no_matching_rate_needs_review(self):
        suggestion = suggest_rate(
            self.conn,
            session_date="2026-06-18",
            duration_minutes=90,
            service_mode="house_call",
            rate_group="house_call",
            time_category="standard",
        )
        self.assertTrue(suggestion.rate_needs_review)

    def test_account_rate_precedes_global(self):
        account_id = "account-1"
        self.conn.execute(
            """
            INSERT INTO client_accounts (account_id, account_code, account_name, created_at, updated_at)
            VALUES (?, ?, ?, '2026-01-01', '2026-01-01')
            """,
            (account_id, "BONNIE", "Bonnie Individual Account"),
        )
        seed_rate_rule(self.conn, amount_cents=10000, effective_from="2026-01-01", duration_minutes=60)
        seed_rate_rule(
            self.conn,
            amount_cents=17500,
            effective_from="2026-01-01",
            duration_minutes=60,
            client_account_id=account_id,
            priority=10,
        )
        suggestion = suggest_rate(
            self.conn,
            session_date="2026-06-18",
            duration_minutes=60,
            service_mode="office",
            rate_group="office",
            time_category="standard",
            account_id=account_id,
        )
        self.assertEqual(suggestion.suggested_rate_cents, 17500)
        self.assertEqual(suggestion.rate_source, "billing_relationship")

    def test_weekend_evening_defaults_to_manual_review(self):
        seed_rate_rule(self.conn, amount_cents=20000, effective_from="2026-01-01", duration_minutes=60)
        suggestion = suggest_rate(
            self.conn,
            session_date="2026-06-20",
            duration_minutes=60,
            service_mode="phone",
            rate_group="remote",
            time_category="weekend_evening",
        )
        self.assertTrue(suggestion.rate_needs_review)
        set_rate_policy(self.conn, WEEKEND_EVENING_POLICY, "use_highest_rate")
        suggestion = suggest_rate(
            self.conn,
            session_date="2026-06-20",
            duration_minutes=60,
            service_mode="phone",
            rate_group="remote",
            time_category="weekend_evening",
        )
        self.assertFalse(suggestion.rate_needs_review)


if __name__ == "__main__":
    unittest.main()
