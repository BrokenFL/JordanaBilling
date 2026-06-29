"""Tests for the read-only paid-at-session backfill dry-run CLI."""
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import connect, migrate_database
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import invoice_ineligibility_reasons, save_business_profile
from jordana_invoice.payment_services import dry_run_paid_at_session_backfill
from jordana_invoice.review_services import approve_candidate, create_billing_party, create_person
from jordana_invoice.util import stable_hash

PYTHON = sys.executable
APP_DIR = str(Path(__file__).resolve().parent.parent / "app")


def raw_row(key, title, start):
    return {
        "ingested_at": "2026-08-20T12:00:00Z", "snapshot_key": key, "run_id": f"run-{key}",
        "batch_name": "test", "capture_window": "past_7_days", "captured_at": "2026-08-20T12:00:00Z",
        "source_device": "test", "timezone": "America/New_York", "calendar_event_id": f"event-{key}",
        "event_fingerprint": f"fp-{key}", "event_title": title, "start_at": start,
        "end_at": start[:11] + "11:00:00-04:00", "duration_minutes": "60", "calendar": "Jordana Work",
        "payload_version": "2", "raw_json": "{}",
    }


def _run_cli(db_path: str, *extra: str) -> tuple[int, str, str]:
    cmd = [PYTHON, "-m", "jordana_invoice.payment_backfill_cli", "--dry-run", "--db", str(db_path)]
    cmd.extend(extra)
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=APP_DIR)
    return proc.returncode, proc.stdout, proc.stderr


class PaymentBackfillCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "test.sqlite3"
        migrate_database(self.db_path)
        conn = connect(self.db_path)
        self.person = create_person(conn, {"first_name": "Pat", "last_name": "Client", "display_name": "Pat Client"})
        self.party = create_billing_party(conn, {
            "billing_name": "Pat Client", "person_id": self.person["person_id"],
            "billing_email": "pat@example.test", "billing_address_line_1": "1 Test St",
            "billing_city": "Test", "billing_state": "FL", "billing_postal_code": "00000",
            "preferred_delivery_method": "email",
        })
        save_business_profile(conn, {
            "business_name": "Test Practice", "provider_display_name": "Test Provider",
            "address_line_1": "100 Test Ave", "city": "Test", "state": "FL", "postal_code": "00000",
            "phone": "555-0100", "email": "billing@test", "payee_name": "Test Payee",
            "payment_address_line_1": "100 Test Ave", "payment_city": "Test", "payment_state": "FL",
            "payment_postal_code": "00000", "zelle_recipient": "demo-zelle@example.test",
        })
        import_rows(conn, [raw_row("s1", "Pat Client | 60 | Office", "2026-05-10T10:00:00-04:00")], "test")
        candidate_id = conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash("calendar_event_id:event-s1"),),
        ).fetchone()[0]
        approve_candidate(conn, candidate_id, {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Pat Client"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60, "service_mode": "office",
            "time_category": "standard", "approved_rate": "150.00",
            "payment_status": "paid_at_session", "billing_treatment": "billable",
            "amount_received": "150.00", "payment_date": "2026-05-10", "payment_method": "zelle",
        })
        self.session_id = conn.execute(
            "SELECT id FROM sessions WHERE payment_status = 'paid_at_session'"
        ).fetchone()[0]
        conn.execute("PRAGMA journal_mode = DELETE")
        conn.close()

    def tearDown(self):
        self.temp.cleanup()

    # 1. Explicit dry-run against a valid temporary database succeeds
    def test_dry_run_succeeds(self):
        rc, out, err = _run_cli(self.db_path)
        self.assertEqual(rc, 0, f"stderr: {err}")

    # 2. Output matches the analyzer's aggregate report
    def test_output_matches_analyzer(self):
        rc, out, err = _run_cli(self.db_path)
        self.assertEqual(rc, 0)
        lines = out.strip().split("\n")
        json_part = "\n".join(lines[:-1])
        report = json.loads(json_part)
        conn = connect(self.db_path)
        expected = dry_run_paid_at_session_backfill(conn)
        conn.close()
        self.assertEqual(report, expected)

    # 3. Output contains the read-only safety statement
    def test_output_contains_safety_statement(self):
        rc, out, err = _run_cli(self.db_path)
        self.assertEqual(rc, 0)
        self.assertIn("READ-ONLY DRY RUN", out)
        self.assertIn("no payments, allocations, sessions, invoices, audits, or reports were changed", out)

    # 4. Missing --dry-run is rejected
    def test_missing_dry_run_rejected(self):
        proc = subprocess.run(
            [PYTHON, "-m", "jordana_invoice.payment_backfill_cli", "--db", str(self.db_path)],
            capture_output=True, text=True, cwd=APP_DIR,
        )
        self.assertNotEqual(proc.returncode, 0)

    # 5. Missing --db is rejected
    def test_missing_db_rejected(self):
        proc = subprocess.run(
            [PYTHON, "-m", "jordana_invoice.payment_backfill_cli", "--dry-run"],
            capture_output=True, text=True, cwd=APP_DIR,
        )
        self.assertNotEqual(proc.returncode, 0)

    # 6. Nonexistent path is rejected
    def test_nonexistent_path_rejected(self):
        rc, out, err = _run_cli(self.root / "nonexistent.sqlite3")
        self.assertEqual(rc, 2)

    # 7. Directory path is rejected
    def test_directory_path_rejected(self):
        rc, out, err = _run_cli(self.root)
        self.assertEqual(rc, 2)

    # 8. Empty or invalid database file fails safely
    def test_empty_db_fails_safely(self):
        empty = self.root / "empty.sqlite3"
        empty.touch()
        rc, out, err = _run_cli(empty)
        self.assertIn(rc, (1, 2, 3))

    # 9. Database missing migration 004 fails safely
    def test_missing_migration_004_fails(self):
        old_db = self.root / "old.sqlite3"
        conn = sqlite3.connect(str(old_db))
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
        conn.close()
        rc, out, err = _run_cli(old_db)
        self.assertEqual(rc, 3)

    # 10. No default operational database is opened when --db is omitted
    def test_no_default_db(self):
        proc = subprocess.run(
            [PYTHON, "-m", "jordana_invoice.payment_backfill_cli", "--dry-run"],
            capture_output=True, text=True, cwd=APP_DIR,
        )
        self.assertNotEqual(proc.returncode, 0)

    # 11. No migrations are run
    def test_no_migrations_run(self):
        old_db = self.root / "unmigrated.sqlite3"
        conn = sqlite3.connect(str(old_db))
        conn.execute("CREATE TABLE schema_migrations (migration_id TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        conn.execute("INSERT INTO schema_migrations VALUES ('001_base', '2026-01-01T00:00:00Z')")
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, payment_status TEXT, billing_party_id TEXT, review_status TEXT, rate_cents_snapshot INTEGER, approved_rate_cents INTEGER, session_date TEXT, start_at TEXT)")
        conn.commit()
        conn.close()
        rc, out, err = _run_cli(old_db)
        self.assertEqual(rc, 3)
        conn2 = sqlite3.connect(str(old_db))
        rows = conn2.execute("SELECT migration_id FROM schema_migrations").fetchall()
        conn2.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "001_base")

    # 12. No WAL, SHM, journal, backup, report, or log files are created by the CLI
    def test_no_sidecar_files_created(self):
        for ext in ["-wal", "-shm", "-journal"]:
            sidecar = self.db_path.with_name(self.db_path.name + ext)
            if sidecar.exists():
                sidecar.unlink()
        rc, out, err = _run_cli(self.db_path)
        self.assertEqual(rc, 0)
        for ext in ["-wal", "-shm", "-journal", "-bak"]:
            self.assertFalse((self.db_path.with_name(self.db_path.name + ext)).exists(),
                             f"Sidecar file {ext} was created")

    # 13. Sessions, payments, allocations, audits, invoice lines, and invoices remain unchanged
    def test_no_database_changes(self):
        tables = ["sessions", "payments", "payment_allocations", "audit_log", "invoice_line_items", "invoices"]
        conn = connect(self.db_path)
        before = {t: [dict(r) for r in conn.execute(f"SELECT * FROM {t}").fetchall()] for t in tables}
        conn.close()
        rc, out, err = _run_cli(self.db_path)
        self.assertEqual(rc, 0)
        conn = connect(self.db_path)
        for t in tables:
            after = [dict(r) for r in conn.execute(f"SELECT * FROM {t}").fetchall()]
            self.assertEqual(after, before[t], f"Table {t} changed")
        conn.close()

    # 14. Output contains no internal IDs or private fixture text
    def test_no_identifying_data_in_output(self):
        rc, out, err = _run_cli(self.db_path)
        self.assertEqual(rc, 0)
        self.assertNotIn("Pat", out)
        self.assertNotIn("Client", out)
        self.assertNotIn("pat@", out)
        self.assertNotIn(self.session_id, out)
        self.assertNotIn(self.party["billing_party_id"], out)

    # 15. Raw SQL and stack traces are not printed
    def test_no_sql_or_stack_traces(self):
        rc, out, err = _run_cli(self.db_path)
        self.assertEqual(rc, 0)
        self.assertNotIn("SELECT", out)
        self.assertNotIn("Traceback", out)
        self.assertNotIn("INSERT", out)

    # 16. Successful command exits 0
    def test_success_exit_0(self):
        rc, out, err = _run_cli(self.db_path)
        self.assertEqual(rc, 0)

    # 17. Invalid arguments exit 2
    def test_invalid_args_exit_2(self):
        rc, out, err = _run_cli(self.root / "nonexistent.sqlite3")
        self.assertEqual(rc, 2)

    # 18. Schema/open failures exit 3
    def test_schema_failure_exit_3(self):
        old_db = self.root / "no_schema.sqlite3"
        conn = sqlite3.connect(str(old_db))
        conn.execute("CREATE TABLE foo (id TEXT)")
        conn.close()
        rc, out, err = _run_cli(old_db)
        self.assertEqual(rc, 3)

    # 19. Existing paid-at-session invoice exclusion remains unchanged
    def test_paid_at_session_exclusion_unchanged(self):
        rc, out, err = _run_cli(self.db_path)
        self.assertEqual(rc, 0)
        conn = connect(self.db_path)
        session = conn.execute("SELECT * FROM sessions WHERE id = ?", (self.session_id,)).fetchone()
        reasons = invoice_ineligibility_reasons(conn, session)
        conn.close()
        self.assertTrue(any("paid at time of session" in r.lower() for r in reasons))

    # 20. No --apply argument exists
    def test_no_apply_argument(self):
        proc = subprocess.run(
            [PYTHON, "-m", "jordana_invoice.payment_backfill_cli", "--dry-run", "--db", str(self.db_path), "--apply"],
            capture_output=True, text=True, cwd=APP_DIR,
        )
        self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
