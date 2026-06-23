import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.review_server import make_handler


class ReviewServerSyncConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "server.sqlite3")
        self.handler_cls = make_handler(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def _handler(self, path, body=b"{}"):
        handler = object.__new__(self.handler_cls)
        handler.path = path
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.send_error = lambda code: (_ for _ in ()).throw(AssertionError(f"unexpected error {code}"))
        captured = {}
        handler.send_json = lambda payload, status=200: captured.setdefault("payload", payload)
        handler.finish = lambda: None
        return handler, captured

    def test_sync_status_uses_active_review_server_connection(self):
        shared_conn = object()
        handler, captured = self._handler("/api/sync/status")
        handler.conn = lambda: shared_conn

        with patch("jordana_invoice.review_server.sync_status_for_connection", side_effect=lambda conn: {"conn_id": id(conn)}) as sync_status, patch(
            "jordana_invoice.review_server.public_sync_status",
            side_effect=lambda payload: payload,
        ):
            handler.do_GET()

        sync_status.assert_called_once_with(shared_conn)
        self.assertEqual(captured["payload"]["conn_id"], id(shared_conn))

    def test_sync_run_uses_active_review_server_connection(self):
        shared_conn = object()
        handler, captured = self._handler("/api/sync/run", body=json.dumps({}).encode("utf-8"))
        handler.conn = lambda: shared_conn

        class Result:
            rows_fetched = 4
            rows_imported = 2

        with patch("jordana_invoice.review_server.sync_with_connection", return_value=Result()) as sync_run, patch(
            "jordana_invoice.review_server.review_sync_config",
            return_value={"reports_dir": "Reports"},
        ), patch(
            "jordana_invoice.review_server.sync_status_for_connection",
            return_value={"last_success": "2026-06-23T00:00:00"},
        ), patch(
            "jordana_invoice.review_server.public_sync_status",
            side_effect=lambda payload: payload,
        ):
            handler.do_POST()

        self.assertIs(sync_run.call_args.args[0], shared_conn)
        self.assertEqual(captured["payload"]["rows_fetched"], 4)
        self.assertEqual(captured["payload"]["rows_imported"], 2)


if __name__ == "__main__":
    unittest.main()
