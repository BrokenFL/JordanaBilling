import sqlite3
import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import (
    MIGRATION_002_MONTHLY_INVOICE_IDENTITY,
    MigrationError,
    connect,
    migrate_database,
)
from jordana_invoice.invoice_services import (
    create_invoice_draft,
    save_business_profile,
)
from jordana_invoice.review_services import create_billing_party, create_person

import jordana_invoice.db as db_module


def _fresh_db(root: Path) -> sqlite3.Connection:
    db_path = root / "test.sqlite3"
    migrate_database(db_path)
    return connect(db_path)


def _setup_party_and_profile(conn: sqlite3.Connection) -> str:
    person = create_person(conn, {"first_name": "Avery", "last_name": "Stone", "display_name": "Avery Stone"})
    party = create_billing_party(conn, {
        "billing_name": "Avery Stone", "person_id": person["person_id"],
        "billing_email": "avery@example.test", "billing_address_line_1": "10 Sample Street",
        "billing_city": "Example", "billing_state": "FL", "billing_postal_code": "00000",
        "preferred_delivery_method": "both",
    })
    save_business_profile(conn, {
        "business_name": "Demo Practice", "provider_display_name": "Demo Provider",
        "address_line_1": "100 Example Avenue", "city": "Example", "state": "FL", "postal_code": "00000",
        "phone": "555-0100", "email": "billing@example.test", "payee_name": "Demo Payee",
        "payment_address_line_1": "100 Example Avenue", "payment_city": "Example", "payment_state": "FL",
        "payment_postal_code": "00000", "zelle_recipient": "demo-zelle@example.test", "invoice_total_label": "TOTAL DUE", "invoice_number_format": "YYYY-NNNN",
    })
    conn.commit()
    return party["billing_party_id"]


def _insert_invoice(conn, party_id, start, end, status="draft", billing_month=None, suffix=""):
    conn.execute(
        """INSERT INTO invoices (
            invoice_id, status, bill_to_party_id, billing_period_start, billing_period_end,
            billing_month, supplement_sequence, invoice_date, delivery_method,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 0, '2026-06-01', 'email', '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z')""",
        (f"inv-{party_id}-{start}-{end}-{status}{suffix}", status, party_id, start, end, billing_month),
    )
    conn.commit()


class MonthlyInvoiceSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    # 1. Fresh database receives both columns and the index
    def test_fresh_db_has_columns_and_index(self):
        conn = _fresh_db(self.root)
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(invoices)").fetchall()}
        self.assertIn("billing_month", cols)
        self.assertIn("supplement_sequence", cols)
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='invoices'"
        ).fetchall()
        index_names = {row["name"] for row in indexes}
        self.assertIn("idx_invoices_draft_party_month", index_names)
        conn.close()

    # 2. Migration is idempotent
    def test_migration_idempotent(self):
        db_path = self.root / "test.sqlite3"
        migrate_database(db_path)
        r2 = migrate_database(db_path)
        self.assertFalse(r2["migrated"])
        conn = connect(db_path)
        rows = conn.execute(
            "SELECT migration_id FROM schema_migrations WHERE migration_id = ?",
            (MIGRATION_002_MONTHLY_INVOICE_IDENTITY,),
        ).fetchall()
        self.assertEqual(len(rows), 1)
        conn.close()

    # 3. Exact calendar-month invoices are backfilled
    def test_exact_calendar_month_backfilled(self):
        db_path = self.root / "test.sqlite3"
        migrate_database(db_path)
        conn = connect(db_path)
        party_id = _setup_party_and_profile(conn)
        # Insert a draft with exact May 2026 period but no billing_month
        conn.execute(
            """INSERT INTO invoices (
                invoice_id, status, bill_to_party_id, billing_period_start, billing_period_end,
                invoice_date, delivery_method, created_at, updated_at
            ) VALUES ('inv-backfill-1', 'draft', ?, '2026-05-01', '2026-05-31',
                '2026-06-01', 'email', '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z')""",
            (party_id,),
        )
        conn.commit()
        conn.close()

        # Simulate migration by calling the migration function directly
        conn = connect(db_path)
        db_module._apply_migration_002(conn)
        conn.commit()
        row = conn.execute("SELECT billing_month FROM invoices WHERE invoice_id = 'inv-backfill-1'").fetchone()
        self.assertEqual(row["billing_month"], "2026-05")
        conn.close()

    # 4. Partial-month invoices remain billing_month = NULL
    def test_partial_month_not_backfilled(self):
        db_path = self.root / "test.sqlite3"
        migrate_database(db_path)
        conn = connect(db_path)
        party_id = _setup_party_and_profile(conn)
        conn.execute(
            """INSERT INTO invoices (
                invoice_id, status, bill_to_party_id, billing_period_start, billing_period_end,
                invoice_date, delivery_method, created_at, updated_at
            ) VALUES ('inv-partial-1', 'draft', ?, '2026-05-15', '2026-05-28',
                '2026-06-01', 'email', '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z')""",
            (party_id,),
        )
        conn.commit()
        conn.close()

        conn = connect(db_path)
        db_module._apply_migration_002(conn)
        conn.commit()
        row = conn.execute("SELECT billing_month FROM invoices WHERE invoice_id = 'inv-partial-1'").fetchone()
        self.assertIsNone(row["billing_month"])
        conn.close()

    # 5. Cross-month invoices remain billing_month = NULL
    def test_cross_month_not_backfilled(self):
        db_path = self.root / "test.sqlite3"
        migrate_database(db_path)
        conn = connect(db_path)
        party_id = _setup_party_and_profile(conn)
        conn.execute(
            """INSERT INTO invoices (
                invoice_id, status, bill_to_party_id, billing_period_start, billing_period_end,
                invoice_date, delivery_method, created_at, updated_at
            ) VALUES ('inv-cross-1', 'draft', ?, '2026-05-01', '2026-06-15',
                '2026-06-01', 'email', '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z')""",
            (party_id,),
        )
        conn.commit()
        conn.close()

        conn = connect(db_path)
        db_module._apply_migration_002(conn)
        conn.commit()
        row = conn.execute("SELECT billing_month FROM invoices WHERE invoice_id = 'inv-cross-1'").fetchone()
        self.assertIsNone(row["billing_month"])
        conn.close()

    # 6. Invalid dates remain billing_month = NULL
    def test_invalid_dates_not_backfilled(self):
        db_path = self.root / "test.sqlite3"
        migrate_database(db_path)
        conn = connect(db_path)
        party_id = _setup_party_and_profile(conn)
        conn.execute(
            """INSERT INTO invoices (
                invoice_id, status, bill_to_party_id, billing_period_start, billing_period_end,
                invoice_date, delivery_method, created_at, updated_at
            ) VALUES ('inv-bad-1', 'draft', ?, 'not-a-date', '2026-05-31',
                '2026-06-01', 'email', '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z')""",
            (party_id,),
        )
        conn.commit()
        conn.close()

        conn = connect(db_path)
        db_module._apply_migration_002(conn)
        conn.commit()
        row = conn.execute("SELECT billing_month FROM invoices WHERE invoice_id = 'inv-bad-1'").fetchone()
        self.assertIsNone(row["billing_month"])
        conn.close()

    # 7. Duplicate monthly drafts cause migration to stop safely
    def test_duplicate_draft_months_abort_migration(self):
        db_path = self.root / "test.sqlite3"
        migrate_database(db_path)
        conn = connect(db_path)
        party_id = _setup_party_and_profile(conn)
        # Drop the index so we can insert duplicates (simulating pre-migration state)
        conn.execute("DROP INDEX IF EXISTS idx_invoices_draft_party_month")
        conn.commit()
        _insert_invoice(conn, party_id, "2026-05-01", "2026-05-31", status="draft", billing_month="2026-05", suffix="-a")
        _insert_invoice(conn, party_id, "2026-05-01", "2026-05-31", status="draft", billing_month="2026-05", suffix="-b")
        conn.close()

        conn = connect(db_path)
        with self.assertRaises(MigrationError) as ctx:
            db_module._check_duplicate_draft_months(conn)
        self.assertIn("duplicate draft invoices", str(ctx.exception).lower())
        conn.close()

    # 8. Two drafts for same Bill To and month are rejected
    def test_two_drafts_same_party_month_rejected(self):
        conn = _fresh_db(self.root)
        party_id = _setup_party_and_profile(conn)
        create_invoice_draft(conn, {
            "bill_to_party_id": party_id,
            "billing_month": "2026-05",
        })
        with self.assertRaises(sqlite3.IntegrityError):
            create_invoice_draft(conn, {
                "bill_to_party_id": party_id,
                "billing_month": "2026-05",
            })
        conn.close()

    # 9. Finalized or void invoices do not block a new draft for that month
    def test_finalized_does_not_block_new_draft(self):
        conn = _fresh_db(self.root)
        party_id = _setup_party_and_profile(conn)
        # Insert a finalized invoice directly (no sessions needed for this test)
        _insert_invoice(conn, party_id, "2026-05-01", "2026-05-31", status="finalized", billing_month="2026-05")
        # Should succeed because the first is finalized, not draft
        second = create_invoice_draft(conn, {
            "bill_to_party_id": party_id,
            "billing_month": "2026-05",
        })
        self.assertEqual(second["invoice"]["billing_month"], "2026-05")
        conn.close()

    def test_void_does_not_block_new_draft(self):
        conn = _fresh_db(self.root)
        party_id = _setup_party_and_profile(conn)
        # Insert a void invoice directly
        _insert_invoice(conn, party_id, "2026-05-01", "2026-05-31", status="void", billing_month="2026-05")
        # Should succeed because the first is void, not draft
        second = create_invoice_draft(conn, {
            "bill_to_party_id": party_id,
            "billing_month": "2026-05",
        })
        self.assertEqual(second["invoice"]["billing_month"], "2026-05")
        conn.close()

    # 10. Negative supplement_sequence is rejected
    def test_negative_supplement_sequence_rejected(self):
        conn = _fresh_db(self.root)
        party_id = _setup_party_and_profile(conn)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO invoices (
                    invoice_id, status, bill_to_party_id, billing_period_start, billing_period_end,
                    billing_month, supplement_sequence, invoice_date, delivery_method,
                    created_at, updated_at
                ) VALUES ('inv-neg-seq', 'draft', ?, '2026-05-01', '2026-05-31',
                    '2026-05', -1, '2026-06-01', 'email', '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z')""",
                (party_id,),
            )
        conn.close()

    def test_negative_supplement_sequence_rejected_by_create(self):
        conn = _fresh_db(self.root)
        party_id = _setup_party_and_profile(conn)
        with self.assertRaises(ValueError):
            create_invoice_draft(conn, {
                "bill_to_party_id": party_id,
                "billing_month": "2026-05",
                "supplement_sequence": -1,
            })
        conn.close()

    # 11. Existing draft-creation callers remain compatible
    def test_existing_callers_compatible(self):
        conn = _fresh_db(self.root)
        party_id = _setup_party_and_profile(conn)
        result = create_invoice_draft(conn, {
            "bill_to_party_id": party_id,
            "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31",
            "invoice_date": "2026-06-01",
        })
        inv = result["invoice"]
        self.assertEqual(inv["billing_period_start"], "2026-05-01")
        self.assertEqual(inv["billing_period_end"], "2026-05-31")
        self.assertEqual(inv["billing_month"], "2026-05")
        self.assertEqual(inv["supplement_sequence"], 0)
        conn.close()

    def test_existing_callers_nonmonthly_compatible(self):
        conn = _fresh_db(self.root)
        party_id = _setup_party_and_profile(conn)
        result = create_invoice_draft(conn, {
            "bill_to_party_id": party_id,
            "billing_period_start": "2026-05-15",
            "billing_period_end": "2026-05-28",
            "invoice_date": "2026-06-01",
        })
        inv = result["invoice"]
        self.assertIsNone(inv["billing_month"])
        self.assertEqual(inv["supplement_sequence"], 0)
        conn.close()

    # 12. Creating from billing_month derives correct dates, including February leap year
    def test_billing_month_derives_dates(self):
        conn = _fresh_db(self.root)
        party_id = _setup_party_and_profile(conn)
        result = create_invoice_draft(conn, {
            "bill_to_party_id": party_id,
            "billing_month": "2026-05",
        })
        inv = result["invoice"]
        self.assertEqual(inv["billing_period_start"], "2026-05-01")
        self.assertEqual(inv["billing_period_end"], "2026-05-31")
        self.assertEqual(inv["billing_month"], "2026-05")
        conn.close()

    def test_billing_month_february_leap_year(self):
        conn = _fresh_db(self.root)
        party_id = _setup_party_and_profile(conn)
        result = create_invoice_draft(conn, {
            "bill_to_party_id": party_id,
            "billing_month": "2024-02",
        })
        inv = result["invoice"]
        self.assertEqual(inv["billing_period_start"], "2024-02-01")
        self.assertEqual(inv["billing_period_end"], "2024-02-29")
        self.assertEqual(inv["billing_month"], "2024-02")
        conn.close()

    def test_billing_month_february_non_leap_year(self):
        conn = _fresh_db(self.root)
        party_id = _setup_party_and_profile(conn)
        result = create_invoice_draft(conn, {
            "bill_to_party_id": party_id,
            "billing_month": "2026-02",
        })
        inv = result["invoice"]
        self.assertEqual(inv["billing_period_start"], "2026-02-01")
        self.assertEqual(inv["billing_period_end"], "2026-02-28")
        conn.close()

    # 13. Contradictory month and period inputs are rejected
    def test_contradictory_month_and_period_rejected(self):
        conn = _fresh_db(self.root)
        party_id = _setup_party_and_profile(conn)
        with self.assertRaises(ValueError) as ctx:
            create_invoice_draft(conn, {
                "bill_to_party_id": party_id,
                "billing_month": "2026-05",
                "billing_period_start": "2026-06-01",
                "billing_period_end": "2026-06-30",
            })
        self.assertIn("do not match", str(ctx.exception))
        conn.close()

    def test_invalid_billing_month_format_rejected(self):
        conn = _fresh_db(self.root)
        party_id = _setup_party_and_profile(conn)
        with self.assertRaises(ValueError):
            create_invoice_draft(conn, {
                "bill_to_party_id": party_id,
                "billing_month": "2026-13",
            })
        conn.close()

    def test_invalid_billing_month_text_rejected(self):
        conn = _fresh_db(self.root)
        party_id = _setup_party_and_profile(conn)
        with self.assertRaises(ValueError):
            create_invoice_draft(conn, {
                "bill_to_party_id": party_id,
                "billing_month": "not-a-month",
            })
        conn.close()


if __name__ == "__main__":
    unittest.main()
