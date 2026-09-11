import sqlite3
import unittest

from jordana_invoice.financial_summary import get_financial_summary


class FinancialSummaryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE sessions (
              id TEXT PRIMARY KEY, session_date TEXT, review_status TEXT,
              billable_status TEXT, appointment_status TEXT, billing_treatment TEXT,
              payment_status TEXT, rate_cents_snapshot INTEGER, approved_rate_cents INTEGER
            );
            CREATE TABLE invoices (
              invoice_id TEXT PRIMARY KEY, status TEXT NOT NULL, total_cents INTEGER NOT NULL,
              finalized_at TEXT, billing_month TEXT, billing_period_start TEXT
            );
            CREATE TABLE payments (
              payment_id TEXT PRIMARY KEY, status TEXT NOT NULL,
              amount_cents INTEGER NOT NULL, received_at TEXT NOT NULL
            );
            CREATE TABLE invoice_line_items (
              invoice_line_item_id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL,
              source_session_id TEXT
            );
            CREATE TABLE payment_allocations (
              allocation_id TEXT PRIMARY KEY, payment_id TEXT NOT NULL,
              session_id TEXT NOT NULL, invoice_line_item_id TEXT,
              amount_cents INTEGER NOT NULL, status TEXT NOT NULL
            );
            """
        )

    def tearDown(self):
        self.conn.close()

    def session(self, session_id, month, amount=10000, payment_status="unpaid"):
        self.conn.execute(
            "INSERT INTO sessions VALUES (?, ?, 'approved', 'approved', 'completed', 'billable', ?, ?, ?)",
            (session_id, f"{month}-15", payment_status, amount, amount),
        )

    def invoice(self, invoice_id, status, total_cents, month, finalized_at=None):
        self.conn.execute(
            "INSERT INTO invoices VALUES (?, ?, ?, ?, ?, ?)",
            (invoice_id, status, total_cents, finalized_at, month, f"{month}-01"),
        )

    def payment(self, payment_id, status, amount_cents, received_at):
        self.conn.execute("INSERT INTO payments VALUES (?, ?, ?, ?)",
                          (payment_id, status, amount_cents, received_at))

    def line(self, line_id, invoice_id, session_id):
        self.conn.execute("INSERT INTO invoice_line_items VALUES (?, ?, ?)",
                          (line_id, invoice_id, session_id))

    def allocation(self, allocation_id, payment_id, session_id, line_id, amount_cents, status="active"):
        self.conn.execute("INSERT INTO payment_allocations VALUES (?, ?, ?, ?, ?, ?)",
                          (allocation_id, payment_id, session_id, line_id, amount_cents, status))

    def test_empty_summary_returns_integer_zeroes_and_month_bounds(self):
        result = get_financial_summary(self.conn, "2026-05")
        self.assertEqual(result["month_start"], "2026-05-01")
        self.assertEqual(result["month_end_exclusive"], "2026-06-01")
        for key in ("total_billable_cents", "total_invoiced_cents",
                    "payments_applied_cents", "outstanding_cents"):
            self.assertEqual(result[key], 0)
            self.assertIsInstance(result[key], int)

    def test_all_primary_totals_use_service_month(self):
        self.session("july-session", "2026-07", 30000)
        self.session("august-session", "2026-08", 20000)
        self.invoice("july-invoice", "finalized", 30000, "2026-07", "2026-08-01T12:00:00Z")
        self.invoice("august-invoice", "finalized", 20000, "2026-08", "2026-08-20T12:00:00Z")
        self.line("july-line", "july-invoice", "july-session")
        self.line("august-line", "august-invoice", "august-session")
        self.payment("august-cash", "posted", 10000, "2026-08-10")
        self.allocation("july-allocation", "august-cash", "july-session", "july-line", 10000)

        july = get_financial_summary(self.conn, "2026-07")
        august = get_financial_summary(self.conn, "2026-08")

        self.assertEqual(july["total_billable_cents"], 30000)
        self.assertEqual(july["total_invoiced_cents"], 30000)
        self.assertEqual(july["payments_applied_cents"], 10000)
        self.assertEqual(july["outstanding_cents"], 20000)
        self.assertEqual(august["total_billable_cents"], 20000)
        self.assertEqual(august["total_invoiced_cents"], 20000)
        self.assertEqual(august["payments_applied_cents"], 0)

    def test_paid_at_session_is_billable_and_applied_without_invoice(self):
        self.session("session", "2026-05", 12500, payment_status="paid_at_session")
        self.payment("payment", "posted", 12500, "2026-05-10")
        self.allocation("allocation", "payment", "session", None, 12500)
        result = get_financial_summary(self.conn, "2026-05")
        self.assertEqual(result["total_billable_cents"], 12500)
        self.assertEqual(result["total_invoiced_cents"], 0)
        self.assertEqual(result["payments_applied_cents"], 12500)

    def test_invalid_month_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "YYYY-MM"):
            get_financial_summary(self.conn, "May 2026")


if __name__ == "__main__":
    unittest.main()
