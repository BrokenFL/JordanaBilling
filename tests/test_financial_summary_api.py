import json
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
