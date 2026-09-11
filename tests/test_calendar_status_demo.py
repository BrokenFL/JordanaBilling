import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from jordana_invoice.calendar_preferences import upsert_calendar_preference
from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.parser import parse_event
from jordana_invoice.review_services import dashboard_status, list_review_candidates
from jordana_invoice.review_services import calendar_freshness_status


def event(title, start="2026-06-18T20:30:00-04:00", end="2026-06-18T21:30:00-04:00", calendar="Jordana Work"):
    return {
        "event_title": title,
        "start_at": start,
        "end_at": end,
        "duration_minutes": 60,
        "calendar": calendar,
    }


def raw_row(snapshot_key, event_id, title, captured_at, calendar="Jordana Work", start="2026-06-18T20:30:00-04:00"):
    return {
        "ingested_at": captured_at,
        "snapshot_key": snapshot_key,
        "run_id": f"run-{snapshot_key}",
        "batch_name": "test",
        "capture_window": "past_7_days",
        "captured_at": captured_at,
        "source_device": "test",
        "timezone": "America/New_York",
        "calendar_event_id": event_id,
        "event_fingerprint": f"fp-{event_id}",
        "event_title": title,
        "start_at": start,
        "end_at": "2026-06-18T21:30:00-04:00",
        "duration_minutes": "60",
        "calendar": calendar,
        "payload_version": "2",
        "raw_json": "{}",
    }


class CalendarStatusDemoTests(unittest.TestCase):
    def test_structured_title_with_time_and_cancelled_status(self):
        result = parse_event(event("Avery Stone | 8:30 PM | 60 | Office | Cancelled"))
        self.assertEqual(result.classification, "client_session")
        self.assertEqual(result.proposed_client_name, "Avery Stone")
        self.assertEqual(result.title_time_normalized, "20:30")
        self.assertTrue(result.title_time_matches_calendar)
        self.assertEqual(result.appointment_status, "cancelled")
        self.assertIn("billing_treatment", result.unresolved_fields)

    def test_structured_title_time_mismatch_keeps_calendar_authoritative(self):
        result = parse_event(event("Taylor Reed | 8:30 PM | 60 | Office", start="2026-06-18T20:00:00-04:00"))
        self.assertFalse(result.title_time_matches_calendar)
        self.assertEqual(result.time_category, "evening")
        self.assertIn("time_discrepancy", result.unresolved_fields)

    def test_structured_title_status_case_and_unknown_status(self):
        no_show = parse_event(event("Morgan Blake | 6:30 PM | 30 | Phone | no show", start="2026-06-18T18:30:00-04:00", end="2026-06-18T19:00:00-04:00"))
        unknown = parse_event(event("Morgan Blake | 6:30 PM | 30 | Phone | Maybe", start="2026-06-18T18:30:00-04:00", end="2026-06-18T19:00:00-04:00"))
        self.assertEqual(no_show.appointment_status, "no_show")
        self.assertEqual(unknown.appointment_status, "unresolved")
        self.assertIn("appointment_status", unknown.unresolved_fields)

    def test_calendar_filtering_and_hidden_reveal(self):
        with tempfile.TemporaryDirectory() as temp:
            conn = connect(Path(temp) / "calendar.sqlite3")
            init_db(conn)
            upsert_calendar_preference(conn, "Uncategorized", "hidden", source="test")
            import_rows(
                conn,
                [
                    raw_row("snap-work", "event-work", "Avery Stone | 60 | Office", "2026-06-22T01:00:00Z", "Jordana Work"),
                    raw_row("snap-hidden", "event-hidden", "Unsorted reminder", "2026-06-22T01:00:00Z", "Uncategorized"),
                ],
                "test",
            )
            normal = list_review_candidates(conn)["items"]
            hidden = list_review_candidates(conn, calendar_filter="hidden")["items"]
            self.assertTrue(all(not item["hidden_from_review"] for item in normal))
            self.assertTrue(hidden)
            self.assertTrue(all(item["hidden_from_review"] for item in hidden))
            conn.close()

    def test_later_cancelled_snapshot_updates_one_candidate_and_one_session(self):
        with tempfile.TemporaryDirectory() as temp:
            conn = connect(Path(temp) / "versions.sqlite3")
            init_db(conn)
            import_rows(
                conn,
                [raw_row("snap-1", "event-1", "Avery Stone | 8:30 PM | 60 | Office", "2026-06-22T01:00:00Z")],
                "first",
            )
            import_rows(
                conn,
                [raw_row("snap-2", "event-1", "Avery Stone | 8:30 PM | 60 | Office | Cancelled", "2026-06-22T02:00:00Z")],
                "second",
            )
            self.assertEqual(count(conn, "raw_calendar_snapshots"), 2)
            self.assertEqual(count(conn, "calendar_event_candidates"), 1)
            self.assertEqual(count(conn, "sessions"), 1)
            session = conn.execute("SELECT * FROM sessions").fetchone()
            self.assertEqual(session["appointment_status"], "cancelled")
            self.assertEqual(session["billing_treatment"], "unresolved")
            conn.close()

    def test_demo_script_creates_isolated_demo_database(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path("data/demo/test_calendar_status_demo.sqlite3")
            try:
                result = subprocess.run(
                    ["scripts/create_demo_database.sh", str(db_path)],
                    cwd=Path(__file__).resolve().parents[1],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertIn("DEMO: SQLite integrity_check=ok", result.stdout)
                conn = connect(db_path)
                self.assertTrue(dashboard_status(conn)["demo_mode"])
                self.assertGreater(count(conn, "raw_calendar_snapshots"), 0)
                conn.close()
            finally:
                for suffix in ("", "-shm", "-wal"):
                    Path(f"{db_path}{suffix}").unlink(missing_ok=True)

    def test_calendar_freshness_warns_only_after_eighteen_hours(self):
        now = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)
        fresh = calendar_freshness_status("2026-07-11T08:00:00Z", now=now)
        stale = calendar_freshness_status("2026-07-10T20:00:00Z", now=now)

        self.assertFalse(fresh["calendar_sync_stale"])
        self.assertEqual(fresh["calendar_sync_warning"], "")
        self.assertTrue(stale["calendar_sync_stale"])
        self.assertEqual(stale["calendar_sync_age_hours"], 24.0)
        self.assertIn("24 hours", stale["calendar_sync_warning"])

    def test_calendar_freshness_warns_when_success_time_is_missing_or_invalid(self):
        self.assertTrue(calendar_freshness_status("")["calendar_sync_stale"])
        self.assertTrue(calendar_freshness_status("not-a-date")["calendar_sync_stale"])


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]


if __name__ == "__main__":
    unittest.main()
