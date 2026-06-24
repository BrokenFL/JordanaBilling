import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import connect, init_db


class RateRuleStatusMigrationTests(unittest.TestCase):
    def test_existing_rate_rules_gain_scheduled_status_default(self):
        temp = tempfile.TemporaryDirectory()
        try:
            db_path = Path(temp.name) / "migration.sqlite3"
            conn = connect(db_path)
            conn.execute(
                """
                CREATE TABLE rate_rules (
                  rate_rule_id TEXT PRIMARY KEY,
                  client_account_id TEXT,
                  person_id TEXT,
                  duration_minutes INTEGER,
                  billing_session_type TEXT,
                  custom_service_description TEXT,
                  custom_service_code TEXT,
                  service_mode TEXT,
                  rate_group TEXT,
                  time_category TEXT NOT NULL DEFAULT 'standard',
                  amount_cents INTEGER NOT NULL,
                  modifier_type TEXT,
                  modifier_amount_cents INTEGER,
                  effective_from TEXT NOT NULL,
                  effective_through TEXT,
                  priority INTEGER NOT NULL DEFAULT 100,
                  active INTEGER NOT NULL DEFAULT 1,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO rate_rules (
                  rate_rule_id, duration_minutes, billing_session_type, time_category,
                  amount_cents, effective_from, priority, active, created_at, updated_at
                ) VALUES ('rule-1', 60, 'psychotherapy', 'standard', 15000, '2026-01-01', 100, 1, '2026-01-01T00:00:00', '2026-01-01T00:00:00')
                """
            )
            init_db(conn)
            row = conn.execute(
                "SELECT appointment_status FROM rate_rules WHERE rate_rule_id = 'rule-1'"
            ).fetchone()
            self.assertEqual(row["appointment_status"], "scheduled")
            conn.close()
        finally:
            temp.cleanup()
