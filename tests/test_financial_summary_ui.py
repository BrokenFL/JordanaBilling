import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FinancialSummaryUiTests(unittest.TestCase):
    def test_invoice_and_payment_pages_use_shared_summary_endpoint(self):
        html = (ROOT / "app/jordana_invoice/static/review.html").read_text(encoding="utf-8")
        js = (ROOT / "app/jordana_invoice/static/review.js").read_text(encoding="utf-8")

        for element_id in (
            "invoiceSummaryMonth",
            "invoiceBillableValue",
            "invoiceInvoicedValue",
            "paymentsSummaryMonth",
            "paymentsAppliedValue",
            "paymentsOutstandingValue",
            "monthCloseMonth",
            "monthCloseChecks",
        ):
            self.assertIn(f'id="{element_id}"', html)

        self.assertIn("/api/financial-summary?month=", js)
        self.assertIn("total_billable_cents", js)
        self.assertIn("payments_applied_cents", js)
        self.assertIn("/api/month-close?month=", js)
        self.assertIn("state.financialSummary.month", js)

    def test_summary_styles_are_present(self):
        css = (ROOT / "app/jordana_invoice/static/review.css").read_text(encoding="utf-8")
        self.assertIn(".financial-summary-cards", css)
        self.assertIn(".financial-summary-toolbar", css)


if __name__ == "__main__":
    unittest.main()
