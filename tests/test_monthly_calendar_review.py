"""Fictional monthly export checks; the operational database is never loaded."""
import csv
import io
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.monthly_calendar_review import CALENDAR_COLUMNS, compare_monthly_calendar, parse_calendar_csv
from jordana_invoice.request_validation import RequestValidationError
from jordana_invoice.review_server import make_handler


def calendar_row(title="Alex Example 2", start="2026-09-09T14:00:00-04:00", end="2026-09-09T15:00:00-04:00", **extra):
    return {"Month": "2026-09", "Exported At": "2026-10-01T09:00:00-04:00", "Calendar": "Work",
            "Title": title, "Start": start, "End": end, "Duration Minutes": "60", "All Day": "false", **extra}


def calendar_csv(*rows):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=CALENDAR_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


class MonthlyCalendarReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "fictional.sqlite3"
        self.conn = connect(self.path)
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def approve(self, title="Alex Example 2", start="2026-09-09T14:00:00-04:00", end="2026-09-09T15:00:00-04:00", key="one"):
        import_rows(self.conn, [{"snapshot_key": key, "run_id": key, "batch_name": key, "event_title": title,
            "event_fingerprint": key, "start_at": start, "end_at": end, "duration_minutes": "60",
            "captured_at": "2026-10-02T12:00:00-04:00", "ingested_at": "2026-10-02T12:00:00-04:00",
            "calendar": "Work", "capture_window": "past_3_days", "payload_version": "3"}], "fictional")
        session = self.conn.execute("SELECT s.id FROM sessions s JOIN calendar_event_candidates c ON c.id=s.candidate_id WHERE c.title=?", (title,)).fetchone()
        self.assertIsNotNone(session)
        sid = session[0]
        self.conn.execute("UPDATE sessions SET review_status='approved',billable_status='approved',approved_rate_cents=23500 WHERE id=?", (sid,))
        self.conn.execute("UPDATE calendar_event_candidates SET classification='unresolved' WHERE id=(SELECT candidate_id FROM sessions WHERE id=?)", (sid,))
        self.conn.commit()
        return sid

    def compare(self, *rows):
        return compare_monthly_calendar(self.conn, {"month": "2026-09", "csv_text": calendar_csv(*rows)})

    def test_approved_unresolved_source_matches_without_any_mutation(self):
        self.approve()
        before = list(self.conn.iterdump())
        result = self.compare(calendar_row())
        self.assertEqual(result["counts"], {"matched": 1})
        self.assertEqual(result["approved_count"], 1)
        self.assertEqual(result["rows"][0]["session"]["rate"], "235.00")
        self.assertEqual(list(self.conn.iterdump()), before)
        self.conn.execute("PRAGMA query_only=ON")
        self.assertEqual(self.compare(calendar_row())["counts"], {"matched": 1})

    def test_unique_time_change_is_only_a_possible_match(self):
        self.approve()
        result = self.compare(calendar_row(start="2026-09-09T14:30:00-04:00", end="2026-09-09T15:30:00-04:00"))
        self.assertEqual(result["counts"], {"possible_match": 1})
        self.assertIn("Start time differs", result["rows"][0]["differences"])
        self.assertEqual(result["rows"][0]["session"]["start_at"], "2026-09-09T14:00:00-04:00")

    def test_comparison_does_not_end_a_callers_transaction(self):
        sid = self.approve()
        self.conn.execute("UPDATE sessions SET duration_minutes=90 WHERE id=?", (sid,))
        self.assertTrue(self.conn.in_transaction)
        self.assertEqual(self.compare(calendar_row())["counts"], {"difference": 1})
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual(self.conn.execute("SELECT duration_minutes FROM sessions WHERE id=?", (sid,)).fetchone()[0], 60)

    def test_title_change_is_not_a_confirmed_match(self):
        self.approve()
        self.assertEqual(self.compare(calendar_row(title="Alex Example | 60 | Phone"))["counts"], {"possible_match": 1})

    def test_approved_duration_and_rate_are_not_reconstructed_from_calendar(self):
        sid = self.approve()
        self.conn.execute("UPDATE sessions SET duration_minutes=90 WHERE id=?", (sid,))
        self.conn.commit()
        result = self.compare(calendar_row())
        self.assertEqual(result["counts"], {"difference": 1})
        self.assertEqual(result["rows"][0]["session"]["duration_minutes"], 90)
        self.assertEqual(result["rows"][0]["session"]["rate"], "235.00")

    def test_ambiguous_group_preserves_every_calendar_and_session_record(self):
        self.approve()
        result = self.compare(calendar_row(), calendar_row())
        self.assertEqual(result["counts"], {"ambiguous": 3})
        self.assertEqual(result["calendar_count"], 2)
        self.assertEqual(result["approved_count"], 1)
        self.assertEqual(sum(bool(row["calendar"]) for row in result["rows"]), 2)
        self.assertEqual(sum(bool(row["session"]) for row in result["rows"]), 1)

    def test_calendar_duplicates_without_approval_remain_two_rows(self):
        self.assertEqual(self.compare(calendar_row(), calendar_row())["counts"], {"duplicate": 2})

    def test_recurring_title_on_different_dates_is_not_a_duplicate(self):
        rows = [calendar_row(), calendar_row(start="2026-09-16T14:00:00-04:00", end="2026-09-16T15:00:00-04:00")]
        result = self.compare(*rows)
        self.assertEqual(result["counts"], {"calendar_only": 2})

    def test_both_unmatched_directions_are_visible(self):
        self.approve()
        result = self.compare(calendar_row(title="Administrative reminder", start="2026-09-12T14:00:00-04:00", end="2026-09-12T15:00:00-04:00"))
        self.assertEqual(result["counts"], {"session_only": 1, "calendar_only": 1})

    def test_pending_and_intentionally_excluded_sessions_are_not_approved_rows(self):
        sid = self.approve()
        for fields in ("review_status='needs_review'", "review_status='approved',billable_status='excluded'"):
            self.conn.execute(f"UPDATE sessions SET {fields} WHERE id=?", (sid,))
            self.conn.commit()
            self.assertEqual(self.compare(calendar_row())["approved_count"], 0)

    def test_timezone_offsets_match_the_same_instant(self):
        self.approve()
        row = calendar_row(start="2026-09-09T18:00:00Z", end="2026-09-09T19:00:00Z")
        self.assertEqual(self.compare(row)["counts"], {"matched": 1})

    def test_eastern_month_boundary_and_all_day_exclusion(self):
        inside = calendar_row(start="2026-10-01T02:00:00Z", end="2026-10-01T03:00:00Z")
        outside = calendar_row(start="2026-10-01T05:00:00Z", end="2026-10-01T06:00:00Z")
        all_day = calendar_row(**{"All Day": "true", "Start": "2026-09-01", "End": "2026-09-02"})
        events, metadata = parse_calendar_csv(calendar_csv(inside, outside, all_day), "2026-09")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["date"], "2026-09-30")
        self.assertEqual(metadata["excluded_all_day"], 1)
        self.assertEqual(metadata["outside_month"], 1)

    def test_daylight_saving_duration_uses_actual_instants(self):
        row = calendar_row(start="2026-11-01T01:30:00-04:00", end="2026-11-01T01:30:00-05:00", **{"Month": "2026-11"})
        events, _ = parse_calendar_csv(calendar_csv(row), "2026-11")
        self.assertEqual(events[0]["duration_minutes"], 60)

    def test_bom_quotes_unicode_and_multiline_title_are_preserved(self):
        title = 'Example, "quoted" café\nsecond line'
        events, _ = parse_calendar_csv("\ufeff" + calendar_csv(calendar_row(title=title)), "2026-09")
        self.assertEqual(events[0]["title"], title)

    def test_downloads_use_csv_formula_protection(self):
        result = self.compare(calendar_row(title="=1+1"))
        row = next(csv.DictReader(io.StringIO(result["comparison_csv"])))
        self.assertEqual(row["Calendar Title"], "'=1+1")

    def test_zero_event_export_keeps_all_approved_sessions_visible(self):
        self.approve()
        result = self.compare()
        self.assertEqual(result["calendar_count"], 0)
        self.assertEqual(result["counts"], {"session_only": 1})

    def test_invalid_input_is_rejected_without_private_text_in_errors(self):
        secret_title = "Fictional private calendar title"
        cases = [calendar_row(title=secret_title, **{"All Day": "maybe"}),
                 calendar_row(title=secret_title, **{"Duration Minutes": "nan"}),
                 calendar_row(title=secret_title, start="2026-09-09T14:00:00"),
                 calendar_row(title=secret_title, end="2026-09-09T13:00:00-04:00"),
                 calendar_row(title=secret_title, **{"Month": "2026-08"})]
        for row in cases:
            with self.subTest(row=row):
                with self.assertRaises(RequestValidationError) as error:
                    self.compare(row)
                self.assertNotIn(secret_title, str(error.exception))
        for payload in ([], {}, {"month": "2026-13", "csv_text": ""}, {"month": "2026-09", "csv_text": []}):
            with self.assertRaises(RequestValidationError):
                compare_monthly_calendar(self.conn, payload)

    def test_combined_exports_are_rejected(self):
        with self.assertRaisesRegex(RequestValidationError, "more than one export"):
            self.compare(calendar_row(), calendar_row(**{"Exported At": "2026-10-02T09:00:00-04:00"}))

    def test_wrong_headers_and_truncated_records_fail_closed(self):
        for text in ("Title,Start\nExample,2026-09-09\n", calendar_csv(calendar_row()).rsplit(",", 1)[0], calendar_csv(calendar_row()) + '"unterminated'):
            with self.assertRaises(RequestValidationError):
                compare_monthly_calendar(self.conn, {"month": "2026-09", "csv_text": text})


class MonthlyCalendarReviewHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        path = Path(self.temp.name) / "fictional.sqlite3"
        conn = connect(path)
        init_db(conn)
        conn.close()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(str(path), write_token="fictional-test-token"))
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/api/reports/calendar-review"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()
        self.temp.cleanup()

    def request(self, payload, token="fictional-test-token"):
        request = Request(self.url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "X-Jordana-Write-Token": token})
        return urlopen(request, timeout=5)

    def test_read_only_comparison_route(self):
        with self.request({"month": "2026-09", "csv_text": calendar_csv(calendar_row())}) as response:
            result = json.load(response)
        self.assertEqual(result["counts"], {"calendar_only": 1})
        self.assertEqual(result["calendar_count"], 1)

    def test_token_guard_and_sanitized_validation(self):
        with self.assertRaises(HTTPError) as error:
            self.request({"month": "2026-09", "csv_text": calendar_csv()}, token="wrong")
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        with self.assertRaises(HTTPError) as error:
            self.request({"month": "2026-09", "csv_text": "invalid"})
        self.assertEqual(error.exception.code, 400)
        self.assertIn("column headers", json.load(error.exception)["error"])
        error.exception.close()
