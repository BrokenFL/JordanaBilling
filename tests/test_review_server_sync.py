import json
import os
import tempfile
import threading
import unittest
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.review_server import REVIEW_SYNC_TRANSPORT, make_handler
import jordana_invoice.review_server as review_server


def raw_row(snapshot_key: str, title: str = "Bonnie 5") -> dict[str, str]:
    return {
        "ingested_at": "2026-06-22T02:00:00.000Z",
        "snapshot_key": snapshot_key,
        "run_id": "run-1",
        "batch_name": "test",
        "capture_window": "next_2_days",
        "captured_at": "2026-06-22T01:00:00.000Z",
        "source_device": "test",
        "timezone": "America/New_York",
        "calendar_event_id": "",
        "event_fingerprint": f"fp-{snapshot_key}",
        "event_title": title,
        "start_at": "2026-06-23T17:00:00-04:00",
        "end_at": "2026-06-23T18:00:00-04:00",
        "duration_minutes": "60",
        "location": "",
        "notes": "",
        "calendar": "Jordana Work",
        "payload_version": "2",
        "raw_json": "{}",
    }


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)

    def __call__(self, url, payload, timeout_seconds):
        return self.responses.pop(0)


class ReviewServerSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.active_db_path = self.root / "active.sqlite3"
        self.env_db_path = self.root / "env.sqlite3"
        self.active_conn = connect(self.active_db_path)
        self.env_conn = connect(self.env_db_path)
        init_db(self.active_conn)
        init_db(self.env_conn)
        import_rows(self.active_conn, [raw_row("snap-existing")], "test")
        (self.root / ".env").write_text(
            "\n".join(
                [
                    "JORDANA_APPS_SCRIPT_URL=https://example.test/exec",
                    "JORDANA_INGEST_API_KEY=test-key",
                    f"JORDANA_DATABASE_PATH={self.env_db_path}",
                    f"JORDANA_REPORTS_DIR={self.root / 'Reports'}",
                    "JORDANA_SYNC_TIMEOUT_SECONDS=5",
                ]
            ),
            encoding="utf-8",
        )
        self.cwd = os.getcwd()
        os.chdir(self.root)

    def tearDown(self):
        review_server.REVIEW_SYNC_TRANSPORT = REVIEW_SYNC_TRANSPORT
        os.chdir(self.cwd)
        self.active_conn.close()
        self.env_conn.close()
        self.temp.cleanup()

    @contextmanager
    def server(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(str(self.active_db_path)))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{httpd.server_address[1]}"
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()

    def fetch_json(self, url: str, method: str = "GET") -> dict:
        request = urllib.request.Request(
            url,
            data=b"{}" if method == "POST" else None,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_sync_status_and_run_use_active_review_server_database(self):
        review_server.REVIEW_SYNC_TRANSPORT = FakeTransport(
            [
                {
                    "ok": True,
                    "record_type": "sync_response",
                    "rows": [raw_row("snap-new")],
                    "next_cursor": "2026-06-23T01:06:00.000Z",
                    "has_more": False,
                }
            ]
        )

        with self.server() as base_url:
            status = self.fetch_json(f"{base_url}/api/sync/status")
            result = self.fetch_json(f"{base_url}/api/sync/run", method="POST")

        self.assertEqual(status["raw_snapshot_count"], 1)
        self.assertEqual(result["rows_imported"], 1)
        self.assertEqual(result["status"]["raw_snapshot_count"], 2)
        active_count = self.active_conn.execute(
            "SELECT COUNT(*) AS count FROM raw_calendar_snapshots"
        ).fetchone()["count"]
        env_count = self.env_conn.execute(
            "SELECT COUNT(*) AS count FROM raw_calendar_snapshots"
        ).fetchone()["count"]
        self.assertEqual(active_count, 2)
        self.assertEqual(env_count, 0)


if __name__ == "__main__":
    unittest.main()
