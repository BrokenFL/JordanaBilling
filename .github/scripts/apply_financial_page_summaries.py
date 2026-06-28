from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def replace_once(relative_path: str, old: str, new: str) -> None:
    path = ROOT / relative_path
    content = path.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {relative_path}, found {count}")
    path.write_text(content.replace(old, new, 1), encoding="utf-8")


def write(relative_path: str, content: str) -> None:
    path = ROOT / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


write(
    "app/jordana_invoice/financial_summary.py",
    '''"""Shared read-only financial totals for invoices, payments, and future dashboards.

All monetary values are returned as integer cents. Finalized invoice totals come
from the immutable invoice snapshot, and outstanding balances use only posted
payments with active allocations.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date
from typing import Any


_MONTH_RE = re.compile(r"^\\d{4}-(0[1-9]|1[0-2])$")


def _month_bounds(month: str | None, *, today: date | None = None) -> tuple[str, str, str]:
    if month is None:
        current = today or date.today()
        month = f"{current.year:04d}-{current.month:02d}"
    if not _MONTH_RE.fullmatch(month):
        raise ValueError("billing_month must be in YYYY-MM format.")

    year, month_number = (int(part) for part in month.split("-", 1))
    start = f"{year:04d}-{month_number:02d}-01"
    if month_number == 12:
        end = f"{year + 1:04d}-01-01"
    else:
        end = f"{year:04d}-{month_number + 1:02d}-01"
    return month, start, end


def get_financial_summary(
    conn: sqlite3.Connection,
    month: str | None = None,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """Return the shared financial summary for one calendar month.

    ``draft_invoice_value_cents`` is global because drafts are work in progress,
    not finalized revenue. Monthly finalized totals use ``finalized_at`` rather
    than invoice date. Monthly payment receipts include posted payments received
    in the month; void payments are excluded. Reversing an allocation changes an
    invoice balance but does not erase a still-posted cash receipt.
    """
    selected_month, month_start, month_end = _month_bounds(month, today=today)

    draft_invoice_value_cents = int(
        conn.execute(
            "SELECT COALESCE(SUM(total_cents), 0) FROM invoices WHERE status = 'draft'"
        ).fetchone()[0]
        or 0
    )
    finalized_invoice_value_for_month_cents = int(
        conn.execute(
            """
            SELECT COALESCE(SUM(total_cents), 0)
            FROM invoices
            WHERE status = 'finalized'
              AND finalized_at >= ?
              AND finalized_at < ?
            """,
            (month_start, month_end),
        ).fetchone()[0]
        or 0
    )
    payments_received_for_month_cents = int(
        conn.execute(
            """
            SELECT COALESCE(SUM(amount_cents), 0)
            FROM payments
            WHERE status = 'posted'
              AND received_at >= ?
              AND received_at < ?
            """,
            (month_start, month_end),
        ).fetchone()[0]
        or 0
    )
    outstanding_balance_cents = int(
        conn.execute(
            """
            SELECT COALESCE(SUM(
              CASE
                WHEN i.total_cents > COALESCE(paid.paid_cents, 0)
                  THEN i.total_cents - COALESCE(paid.paid_cents, 0)
                ELSE 0
              END
            ), 0)
            FROM invoices i
            LEFT JOIN (
              SELECT li.invoice_id, SUM(pa.amount_cents) AS paid_cents
              FROM payment_allocations pa
              JOIN payments p ON p.payment_id = pa.payment_id
              JOIN invoice_line_items li
                ON li.invoice_line_item_id = pa.invoice_line_item_id
              WHERE pa.status = 'active'
                AND p.status = 'posted'
              GROUP BY li.invoice_id
            ) paid ON paid.invoice_id = i.invoice_id
            WHERE i.status = 'finalized'
            """
        ).fetchone()[0]
        or 0
    )

    return {
        "month": selected_month,
        "month_start": month_start,
        "month_end_exclusive": month_end,
        "draft_invoice_value_cents": draft_invoice_value_cents,
        "finalized_invoice_value_for_month_cents": finalized_invoice_value_for_month_cents,
        "payments_received_for_month_cents": payments_received_for_month_cents,
        "outstanding_balance_cents": outstanding_balance_cents,
    }
''',
)

replace_once(
    "app/jordana_invoice/review_server.py",
    '''from .invoice_rendering import build_print_preview_html
from .payment_services import (
''',
    '''from .invoice_rendering import build_print_preview_html
from .financial_summary import get_financial_summary
from .payment_services import (
''',
)

replace_once(
    "app/jordana_invoice/review_server.py",
    '''                if parsed.path == "/api/service-catalog":
                    self.send_json(list_services(self.conn(), first(parse_qs(parsed.query), "include_inactive") == "1"))
                    return
                if parsed.path == "/api/invoices/eligible-sessions":
''',
    '''                if parsed.path == "/api/service-catalog":
                    self.send_json(list_services(self.conn(), first(parse_qs(parsed.query), "include_inactive") == "1"))
                    return
                if parsed.path == "/api/financial-summary":
                    query = parse_qs(parsed.query)
                    self.send_json(get_financial_summary(self.conn(), first(query, "month") or None))
                    return
                if parsed.path == "/api/invoices/eligible-sessions":
''',
)

replace_once(
    "app/jordana_invoice/static/review.html",
    '''          </div>
          <div id="invoiceCustomDateRange" class="invoice-custom-date-range" hidden>
''',
    '''          </div>
          <div class="financial-summary-toolbar">
            <label class="field">Summary month<input id="invoiceSummaryMonth" type="month" /></label>
          </div>
          <div class="summary-cards financial-summary-cards" id="invoiceSummaryCards" aria-live="polite">
            <div class="summary-card"><div class="summary-card-label">Draft Invoice Value</div><div class="summary-card-value" id="invoiceDraftValue">$0.00</div><div class="summary-card-note">Current drafts</div></div>
            <div class="summary-card"><div class="summary-card-label">Finalized This Month</div><div class="summary-card-value" id="invoiceFinalizedValue">$0.00</div><div class="summary-card-note">By finalization date</div></div>
            <div class="summary-card"><div class="summary-card-label">Outstanding Balance</div><div class="summary-card-value" id="invoiceOutstandingValue">$0.00</div><div class="summary-card-note">All finalized invoices</div></div>
          </div>
          <div id="invoiceCustomDateRange" class="invoice-custom-date-range" hidden>
''',
)

replace_once(
    "app/jordana_invoice/static/review.html",
    '''          </div>
          <div class="payments-tabs" id="paymentsTabs">
''',
    '''          </div>
          <div class="financial-summary-toolbar">
            <label class="field">Summary month<input id="paymentsSummaryMonth" type="month" /></label>
          </div>
          <div class="summary-cards financial-summary-cards" id="paymentsSummaryCards" aria-live="polite">
            <div class="summary-card"><div class="summary-card-label">Invoiced This Month</div><div class="summary-card-value" id="paymentsInvoicedValue">$0.00</div><div class="summary-card-note">Finalized invoices</div></div>
            <div class="summary-card"><div class="summary-card-label">Payments Received This Month</div><div class="summary-card-value" id="paymentsReceivedValue">$0.00</div><div class="summary-card-note">Posted cash receipts</div></div>
            <div class="summary-card"><div class="summary-card-label">Outstanding Balance</div><div class="summary-card-value" id="paymentsOutstandingValue">$0.00</div><div class="summary-card-note">All finalized invoices</div></div>
          </div>
          <div class="payments-tabs" id="paymentsTabs">
''',
)

replace_once(
    "app/jordana_invoice/static/review.js",
    '''  payments: {
    activeTab: "outstanding",
    paidItems: [],
    allPaymentsItems: [],
    selectedPaidInvoiceId: null,
    selectedPaymentId: null
  },
  invoiceLibrary: {
''',
    '''  payments: {
    activeTab: "outstanding",
    paidItems: [],
    allPaymentsItems: [],
    selectedPaidInvoiceId: null,
    selectedPaymentId: null
  },
  financialSummary: {
    month: "",
    data: null
  },
  invoiceLibrary: {
''',
)

replace_once(
    "app/jordana_invoice/static/review.js",
    '''async function loadList() {
''',
    '''function currentLocalMonth() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

function summaryMoney(value) {
  return money(centString(Number(value) || 0));
}

function renderFinancialSummary(data) {
  const values = {
    invoiceDraftValue: data.draft_invoice_value_cents,
    invoiceFinalizedValue: data.finalized_invoice_value_for_month_cents,
    invoiceOutstandingValue: data.outstanding_balance_cents,
    paymentsInvoicedValue: data.finalized_invoice_value_for_month_cents,
    paymentsReceivedValue: data.payments_received_for_month_cents,
    paymentsOutstandingValue: data.outstanding_balance_cents,
  };
  Object.entries(values).forEach(([id, value]) => {
    const node = $(id);
    if (node) node.textContent = summaryMoney(value);
  });
}

async function loadFinancialSummary() {
  const month = state.financialSummary.month || currentLocalMonth();
  state.financialSummary.month = month;
  ["invoiceSummaryMonth", "paymentsSummaryMonth"].forEach(id => {
    const input = $(id);
    if (!input) return;
    input.value = month;
    input.onchange = async () => {
      if (!input.value) return;
      state.financialSummary.month = input.value;
      await loadFinancialSummary();
    };
  });
  try {
    const data = await api(`/api/financial-summary?month=${encodeURIComponent(month)}`);
    state.financialSummary.data = data;
    renderFinancialSummary(data);
  } catch (_) {
    state.financialSummary.data = null;
    ["invoiceDraftValue", "invoiceFinalizedValue", "invoiceOutstandingValue", "paymentsInvoicedValue", "paymentsReceivedValue", "paymentsOutstandingValue"].forEach(id => {
      const node = $(id);
      if (node) node.textContent = "Unavailable";
    });
  }
}

async function loadList() {
''',
)

replace_once(
    "app/jordana_invoice/static/review.js",
    '''  renderOutstandingInvoices(state.unpaid.items);
''',
    '''  renderOutstandingInvoices(state.unpaid.items);
  await loadFinancialSummary();
''',
)

replace_once(
    "app/jordana_invoice/static/review.js",
    '''  state.payments.paidItems = data.items || [];
  renderPaidInvoices(state.payments.paidItems);
''',
    '''  state.payments.paidItems = data.items || [];
  renderPaidInvoices(state.payments.paidItems);
  await loadFinancialSummary();
''',
)

replace_once(
    "app/jordana_invoice/static/review.js",
    '''  state.payments.allPaymentsItems = data.items || [];
  renderAllPayments(state.payments.allPaymentsItems);
''',
    '''  state.payments.allPaymentsItems = data.items || [];
  renderAllPayments(state.payments.allPaymentsItems);
  await loadFinancialSummary();
''',
)

replace_once(
    "app/jordana_invoice/static/review.js",
    '''  lib.loaded = true;
  renderInvoiceLibrary();
}
''',
    '''  lib.loaded = true;
  renderInvoiceLibrary();
  await loadFinancialSummary();
}
''',
)

css_path = ROOT / "app/jordana_invoice/static/review.css"
css_path.write_text(
    css_path.read_text(encoding="utf-8")
    + '''

.financial-summary-toolbar { display: flex; justify-content: flex-end; margin: 4px 0 10px; }
.financial-summary-toolbar .field { width: 190px; }
.financial-summary-cards { grid-template-columns: repeat(3, minmax(0, 1fr)); margin-bottom: 16px; }
.financial-summary-cards .summary-card { min-height: 96px; }
.summary-card-note { color: var(--muted); font-size: 11px; margin-top: 5px; }
@media (max-width: 880px) {
  .financial-summary-cards { grid-template-columns: 1fr; }
  .financial-summary-toolbar { justify-content: flex-start; }
}
''',
    encoding="utf-8",
)

replace_once(
    "README.md",
    '''- Local multi-page PDF generation
''',
    '''- Local multi-page PDF generation
- Shared invoice/payment financial summaries for draft value, monthly finalized invoices, monthly payment receipts, and outstanding balance
''',
)

replace_once(
    "README.md",
    '''The `Unpaid` sidebar screen is the first payment workspace. It lists finalized invoices with a remaining balance greater than zero, derives paid/balance amounts from posted payments plus active allocations, allows one full or partial payment to be recorded against one invoice at a time, and shows compact payment history. It does not yet support credits, reversals, voiding, or multi-invoice payments.
''',
    '''The `Payments` sidebar screen lists outstanding and paid finalized invoices, supports payment entry and corrections, and exposes a payment ledger. Its summary cards show finalized invoice value and posted payment receipts for a selected month plus the current outstanding balance. The `Invoices` screen shows the same shared finalized and outstanding totals alongside current draft value. Both screens use the same `/api/financial-summary` backend calculation so a future dashboard can reuse it without redefining accounting logic. Multi-invoice payment entry and formal reconciliation remain unfinished.
''',
)

write(
    "tests/test_financial_summary.py",
    '''import sqlite3
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
''',
)

write(
    "tests/test_financial_summary_api.py",
    '''import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

from jordana_invoice.db import migrate_database
from jordana_invoice.review_server import make_handler


class FinancialSummaryApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "test.sqlite3"
        migrate_database(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    @contextmanager
    def server(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(str(self.db_path)))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{httpd.server_address[1]}"
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()

    def test_shared_summary_endpoint_returns_zero_state(self):
        with self.server() as base_url:
            with urllib.request.urlopen(f"{base_url}/api/financial-summary?month=2026-05") as response:
                result = json.loads(response.read().decode("utf-8"))

        self.assertEqual(result["month"], "2026-05")
        self.assertEqual(result["draft_invoice_value_cents"], 0)
        self.assertEqual(result["finalized_invoice_value_for_month_cents"], 0)
        self.assertEqual(result["payments_received_for_month_cents"], 0)
        self.assertEqual(result["outstanding_balance_cents"], 0)

    def test_invalid_month_returns_sanitized_validation_error(self):
        with self.server() as base_url:
            with self.assertRaises(urllib.error.HTTPError) as context:
                urllib.request.urlopen(f"{base_url}/api/financial-summary?month=not-a-month")
        self.assertEqual(context.exception.code, 400)
        payload = json.loads(context.exception.read().decode("utf-8"))
        context.exception.close()
        self.assertEqual(payload, {"ok": False, "error": "billing_month must be in YYYY-MM format."})


if __name__ == "__main__":
    unittest.main()
''',
)

write(
    "tests/test_financial_summary_ui.py",
    '''import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FinancialSummaryUiTests(unittest.TestCase):
    def test_invoice_and_payment_pages_use_shared_summary_endpoint(self):
        html = (ROOT / "app/jordana_invoice/static/review.html").read_text(encoding="utf-8")
        js = (ROOT / "app/jordana_invoice/static/review.js").read_text(encoding="utf-8")

        for element_id in (
            "invoiceSummaryMonth",
            "invoiceDraftValue",
            "invoiceFinalizedValue",
            "invoiceOutstandingValue",
            "paymentsSummaryMonth",
            "paymentsInvoicedValue",
            "paymentsReceivedValue",
            "paymentsOutstandingValue",
        ):
            self.assertIn(f'id="{element_id}"', html)

        self.assertIn("/api/financial-summary?month=", js)
        self.assertIn("financialized_invoice_value_for_month_cents", js.replace("finalized", "financialized", 1))
        self.assertIn("state.financialSummary.month", js)

    def test_summary_styles_are_present(self):
        css = (ROOT / "app/jordana_invoice/static/review.css").read_text(encoding="utf-8")
        self.assertIn(".financial-summary-cards", css)
        self.assertIn(".financial-summary-toolbar", css)


if __name__ == "__main__":
    unittest.main()
''',
)

# Remove the one-use GitHub patching machinery before the feature commit is made.
for relative_path in (
    ".github/scripts/apply_financial_page_summaries.py",
    ".github/workflows/apply-financial-page-summaries.yml",
):
    path = ROOT / relative_path
    if path.exists():
        path.unlink()
