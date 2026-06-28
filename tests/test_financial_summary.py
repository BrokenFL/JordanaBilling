import sqlite3
import unittest

from jordana_invoice.financial_summary import get_financial_summary


class FinancialSummaryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE invoices (
              invoice_id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              total_cents INTEGER NOT NULL,
              finalized_at TEXT
            );
            CREATE TABLE payments (
              payment_id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              amount_cents INTEGER NOT NULL,
              received_at TEXT NOT NULL
            );
            CREATE TABLE invoice_line_items (
              invoice_line_item_id TEXT PRIMARY KEY,
              invoice_id TEXT NOT NULL
            );
            CREATE TABLE payment_allocations (
              allocation_id TEXT PRIMARY KEY,
              payment_id TEXT NOT NULL,
              invoice_line_item_id TEXT,
              amount_cents INTEGER NOT NULL,
              status TEXT NOT NULL
            );
            """
        )

    def tearDown(self):
        self.conn.close()

    def invoice(self, invoice_id, status, total_cents, finalized_at=None):
        self.conn.execute(
            "INSERT INTO invoices VALUES (?, ?, ?, ?)",
            (invoice_id, status, total_cents, finalized_at),
        )

    def payment(self, payment_id, status, amount_cents, received_at):
        self.conn.execute(
            "INSERT INTO payments VALUES (?, ?, ?, ?)",
            (payment_id, status, amount_cents, received_at),
        )

    def line(self, line_id, invoice_id):
        self.conn.execute("INSERT INTO invoice_line_items VALUES (?, ?)", (line_id, invoice_id))

    def allocation(self, allocation_id, payment_id, line_id, amount_cents, status="active"):
        self.conn.execute(
            "INSERT INTO payment_allocations VALUES (?, ?, ?, ?, ?)",
            (allocation_id, payment_id, line_id, amount_cents, status),
        )

    def test_empty_summary_returns_integer_zeroes_and_month_bounds(self):
        result = get_financial_summary(self.conn, "2026-05")
        self.assertEqual(result["month"], "2026-05")
        self.assertEqual(result["month_start"], "2026-05-01")
        self.assertEqual(result["month_end_exclusive"], "2026-06-01")
        for key in (
            "draft_invoice_value_cents",
            "finalized_invoice_value_for_month_cents",
            "payments_received_for_month_cents",
            "outstanding_balance_cents",
        ):
            self.assertEqual(result[key], 0)
            self.assertIsInstance(result[key], int)

    def test_drafts_and_finalized_snapshot_totals_are_separate(self):
        self.invoice("draft-1", "draft", 10000)
        self.invoice("draft-2", "draft", 2500)
        self.invoice("final-may", "finalized", 30000, "2026-05-15T12:00:00Z")
        self.invoice("final-april", "finalized", 40000, "2026-04-30T23:59:59Z")
        self.invoice("void-may", "void", 50000, "2026-05-20T12:00:00Z")

        result = get_financial_summary(self.conn, "2026-05")

        self.assertEqual(result["draft_invoice_value_cents"], 12500)
        self.assertEqual(result["finalized_invoice_value_for_month_cents"], 30000)
        self.assertEqual(result["outstanding_balance_cents"], 70000)

    def test_payments_reversals_voids_and_outstanding_balance(self):
        self.invoice("inv-1", "finalized", 30000, "2026-05-02T12:00:00Z")
        self.invoice("inv-2", "finalized", 20000, "2026-05-03T12:00:00Z")
        self.invoice("inv-3", "finalized", 10000, "2026-05-04T12:00:00Z")
        for number in (1, 2, 3):
            self.line(f"line-{number}", f"inv-{number}")

        self.payment("posted-may-1", "posted", 10000, "2026-05-10")
        self.payment("posted-may-2", "posted", 20000, "2026-05-11T09:00:00-04:00")
        self.payment("posted-april", "posted", 5000, "2026-04-30")
        self.payment("posted-reversed", "posted", 7000, "2026-05-12")
        self.payment("void-may", "void", 9000, "2026-05-13")

        self.allocation("a-1", "posted-may-1", "line-1", 10000)
        self.allocation("a-2", "posted-may-2", "line-2", 20000)
        self.allocation("a-3", "posted-april", "line-1", 5000)
        self.allocation("a-4", "posted-reversed", "line-3", 7000, "reversed")
        self.allocation("a-5", "void-may", "line-3", 9000)

        result = get_financial_summary(self.conn, "2026-05")

        self.assertEqual(result["payments_received_for_month_cents"], 37000)
        self.assertEqual(result["outstanding_balance_cents"], 25000)

    def test_invalid_month_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "YYYY-MM"):
            get_financial_summary(self.conn, "May 2026")


if __name__ == "__main__":
    unittest.main()
