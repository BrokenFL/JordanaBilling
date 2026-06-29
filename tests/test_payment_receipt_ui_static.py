import unittest
from pathlib import Path


class PaymentReceiptUiStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = Path("app/jordana_invoice/static/review.js").read_text()

    def test_payment_detail_overlay_has_receipt_actions(self):
        self.assertIn("Preview Receipt", self.js)
        self.assertIn("Create Receipt", self.js)
        self.assertIn("Open Receipt", self.js)
        self.assertIn("Show in Finder", self.js)
        self.assertIn("receipt-preview", self.js)
        self.assertIn("receipt-document-action", self.js)

    def test_create_receipt_is_only_for_posted_payments(self):
        start = self.js.index("const receiptActions =")
        end = self.js.index("const allocRows =", start)
        block = self.js[start:end]
        self.assertIn('data.status === "posted"', block)
        self.assertIn("createReceiptBtn", block)


if __name__ == "__main__":
    unittest.main()
