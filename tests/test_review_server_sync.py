import json
import os
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.review_server import CalendarSyncRuntime, REVIEW_SYNC_TRANSPORT, make_handler
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
        self.calls = []

    def __call__(self, url, payload, timeout_seconds):
        self.calls.append(payload)
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
    def server(self, runtime=None):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(str(self.active_db_path), sync_runtime=runtime))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{httpd.server_address[1]}"
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()

    def fetch_json(self, url: str, method: str = "GET") -> dict:
        headers = {"Content-Type": "application/json"}
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            parts = urlsplit(url)
            base_url = f"{parts.scheme}://{parts.netloc}"
            headers["X-Jordana-Write-Token"] = self.fetch_write_token(base_url)
        request = urllib.request.Request(
            url,
            data=b"{}" if method == "POST" else None,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))

    def fetch_write_token(self, base_url: str) -> str:
        with urllib.request.urlopen(f"{base_url}/review") as response:
            html = response.read().decode("utf-8")
        match = re.search(r'window\.__JORDANA_BOOTSTRAP__=\{"writeToken":\s*"([^"]+)"\};', html)
        self.assertIsNotNone(match, "Review page bootstrap token was not found")
        return match.group(1)

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
        self.assertEqual(result["mode"], "initial_full")
        self.assertIn("duplicate_snapshots_skipped", result)
        self.assertIn("review_items_changed", result)

    def test_rebuild_requires_confirmation(self):
        with self.server() as base_url:
            request = urllib.request.Request(
                f"{base_url}/api/sync/rebuild",
                data=b"{}",
                headers={
                    "Content-Type": "application/json",
                    "X-Jordana-Write-Token": self.fetch_write_token(base_url),
                },
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(request)
        err = ctx.exception
        self.assertEqual(err.code, 400)
        self.assertEqual(
            json.loads(err.read().decode("utf-8")),
            {"ok": False, "error": "Explicit rebuild confirmation is required."},
        )
        err.close()

    def test_runtime_startup_runs_exactly_one_intelligent_sync(self):
        transport = FakeTransport(
            [
                {
                    "ok": True,
                    "record_type": "sync_response",
                    "rows": [raw_row("startup-sync")],
                    "next_cursor": {
                        "ingested_at": "2026-06-22T02:00:00.000Z",
                        "snapshot_key": "startup-sync",
                    },
                    "has_more": False,
                }
            ]
        )
        runtime = CalendarSyncRuntime(str(self.active_db_path), transport=transport, interval_minutes=60)
        runtime.start()
        try:
            for _ in range(50):
                if len(transport.calls) == 1:
                    break
                __import__("time").sleep(0.02)
            self.assertEqual(len(transport.calls), 1)
            self.assertEqual(transport.calls[0]["after_ingested_at"], "1970-01-01T00:00:00.000Z")
        finally:
            runtime.stop()

    def test_scheduled_runtime_run_uses_incremental_cursor(self):
        self.active_conn.execute(
            """
            INSERT INTO sync_state (source_name, cursor_value, last_success_at)
            VALUES (?, ?, ?)
            """,
            ("google_calendar_snapshots", "2026-06-22T01:00:00.000Z", "2026-06-22T01:01:00.000Z"),
        )
        self.active_conn.commit()
        transport = FakeTransport(
            [
                {
                    "ok": True,
                    "record_type": "sync_response",
                    "rows": [],
                    "next_cursor": "2026-06-22T01:00:00.000Z",
                    "has_more": False,
                }
            ]
        )
        runtime = CalendarSyncRuntime(str(self.active_db_path), transport=transport, interval_minutes=60)
        runtime._run_once(startup=False)
        self.assertEqual(transport.calls[0]["after_ingested_at"], "2026-06-22T01:00:00.000Z")

    def test_get_requests_do_not_require_write_token(self):
        with self.server() as base_url:
            status = self.fetch_json(f"{base_url}/api/status")
        self.assertIn("needs_review", status)

    def test_missing_write_token_returns_403(self):
        with self.server() as base_url:
            request = urllib.request.Request(
                f"{base_url}/api/sync/run",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(request)
        err = ctx.exception
        self.assertEqual(err.code, 403)
        self.assertEqual(
            json.loads(err.read().decode("utf-8")),
            {"ok": False, "error": "Write access expired. Refresh Jordana Billing and try again."},
        )
        err.close()

    def test_incorrect_write_token_returns_403(self):
        with self.server() as base_url:
            request = urllib.request.Request(
                f"{base_url}/api/sync/run",
                data=b"{}",
                headers={
                    "Content-Type": "application/json",
                    "X-Jordana-Write-Token": "wrong-token",
                },
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(request)
        err = ctx.exception
        self.assertEqual(err.code, 403)
        self.assertEqual(
            json.loads(err.read().decode("utf-8")),
            {"ok": False, "error": "Write access expired. Refresh Jordana Billing and try again."},
        )
        err.close()

    def test_write_token_changes_between_server_launches(self):
        with self.server() as first_base_url:
            first_token = self.fetch_write_token(first_base_url)
        with self.server() as second_base_url:
            second_token = self.fetch_write_token(second_base_url)
        self.assertNotEqual(first_token, second_token)


if __name__ == "__main__":
    unittest.main()
