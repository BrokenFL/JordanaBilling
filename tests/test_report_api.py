import csv
import io
import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from jordana_invoice.csv_reports import (
    SESSION_COLUMNS,
    SUMMARY_COLUMNS,
    SIMPLE_COLUMNS,
    available_report_types,
    available_years,
    current_eastern_year,
    default_report_year,
    generate_report_csv,
    report_filename,
    write_reports,
)
from jordana_invoice.appointment_ledger import APPOINTMENT_LEDGER_COLUMNS
from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.review_server import make_handler


def raw_row(
    snapshot_key: str,
    title: str,
    start_at: str,
    *,
    end_at: str | None = None,
    event_fingerprint: str | None = None,
    capture_window: str = "next_2_days",
    calendar: str = "Jordana Work",
) -> dict[str, str]:
    return {
        "ingested_at": "2026-06-22T02:00:00.000Z",
        "snapshot_key": snapshot_key,
        "run_id": "run-1",
        "batch_name": "test",
        "capture_window": capture_window,
        "captured_at": "2026-06-22T01:00:00.000Z",
        "source_device": "test",
        "timezone": "America/New_York",
        "calendar_event_id": "",
        "event_fingerprint": event_fingerprint or f"fp-{snapshot_key}",
        "event_title": title,
        "start_at": start_at,
        "end_at": end_at or start_at.replace("17:00:00", "18:00:00"),
        "duration_minutes": "60",
        "location": "",
        "notes": "",
        "calendar": calendar,
        "payload_version": "2",
        "raw_json": "{}",
    }


class ReportMetadataTests(unittest.TestCase):
    def test_metadata_contains_exactly_four_report_types(self):
        types = available_report_types()
        self.assertEqual(len(types), 4)
        self.assertEqual(
            {entry["type"] for entry in types},
            {"sessions", "summary", "simple", "appointments"},
        )

    def test_each_metadata_entry_has_required_fields(self):
        for entry in available_report_types():
            self.assertIn("type", entry)
            self.assertIn("display_name", entry)
            self.assertIn("description", entry)
            self.assertIn("year_required", entry)
            self.assertTrue(entry["year_required"])

    def test_metadata_returns_copies(self):
        first = available_report_types()
        first[0]["type"] = "mutated"
        second = available_report_types()
        self.assertNotEqual(second[0]["type"], "mutated")


class AvailableYearsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "test.sqlite3")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_years_are_distinct_and_newest_first(self):
        import_rows(
            self.conn,
            [
                raw_row("snap-1", "Bonnie 1", "2026-06-23T17:00:00-04:00"),
                raw_row("snap-2", "Bonnie 2", "2025-03-15T17:00:00-04:00"),
                raw_row("snap-3", "Bonnie 3", "2026-01-10T17:00:00-04:00"),
            ],
            "test",
        )
        years = available_years(self.conn)
        self.assertEqual(years, sorted(years, reverse=True))
        self.assertEqual(len(years), len(set(years)))
        self.assertIn(2026, years)
        self.assertIn(2025, years)

    def test_empty_database_returns_empty_years(self):
        years = available_years(self.conn)
        self.assertEqual(years, [])


class DefaultYearTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "test.sqlite3")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_default_year_is_current_eastern_year_when_present(self):
        import_rows(
            self.conn,
            [raw_row("snap-1", "Bonnie 1", "2026-06-23T17:00:00-04:00")],
            "test",
        )
        eastern = ZoneInfo("America/New_York")
        current_year = date.today().year
        # Ensure the current Eastern year is in the data
        import_rows(
            self.conn,
            [raw_row("snap-current", "Bonnie Current", f"{current_year}-06-23T17:00:00-04:00")],
            "test",
        )
        self.assertEqual(default_report_year(self.conn), current_year)

    def test_default_year_falls_back_to_newest_available(self):
        import_rows(
            self.conn,
            [
                raw_row("snap-1", "Bonnie 1", "2024-06-23T17:00:00-04:00"),
                raw_row("snap-2", "Bonnie 2", "2025-03-15T17:00:00-04:00"),
            ],
            "test",
        )
        eastern = ZoneInfo("America/New_York")
        current_year = date.today().year
        if current_year not in (2024, 2025):
            self.assertEqual(default_report_year(self.conn), 2025)

    def test_default_year_returns_current_year_on_empty_database(self):
        eastern = ZoneInfo("America/New_York")
        current_year = date.today().year
        self.assertEqual(default_report_year(self.conn), current_year)


class GenerateReportCsvTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "test.sqlite3")
        init_db(self.conn)
        self.reports_dir = self.root / "Reports"

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _import_sample_data(self):
        import_rows(
            self.conn,
            [
                raw_row("snap-1", "Bonnie 5", "2026-06-23T17:00:00-04:00"),
                raw_row("snap-2", "Amber 3", "2026-06-24T11:00:00-04:00"),
                raw_row("snap-3", "Bonnie 5", "2025-12-15T17:00:00-04:00"),
            ],
            "test",
        )

    def _parse_csv(self, csv_text: str) -> list[dict[str, str]]:
        reader = csv.DictReader(io.StringIO(csv_text))
        return list(reader)

    def test_sessions_returns_valid_csv_with_existing_headers(self):
        self._import_sample_data()
        csv_text = generate_report_csv(self.conn, "sessions", 2026)
        rows = self._parse_csv(csv_text)
        self.assertTrue(len(rows) > 0)
        self.assertEqual(list(rows[0].keys()), SESSION_COLUMNS)

    def test_summary_returns_valid_csv_with_existing_headers(self):
        self._import_sample_data()
        csv_text = generate_report_csv(self.conn, "summary", 2026)
        rows = self._parse_csv(csv_text)
        self.assertEqual(list(rows[0].keys()) if rows else [], SUMMARY_COLUMNS if rows else [])

    def test_simple_returns_valid_csv_with_existing_headers(self):
        self._import_sample_data()
        csv_text = generate_report_csv(self.conn, "simple", 2026)
        rows = self._parse_csv(csv_text)
        self.assertTrue(len(rows) > 0)
        self.assertEqual(list(rows[0].keys()), SIMPLE_COLUMNS)

    def test_appointments_returns_valid_csv_with_existing_headers(self):
        self._import_sample_data()
        csv_text = generate_report_csv(self.conn, "appointments", 2026)
        rows = self._parse_csv(csv_text)
        self.assertTrue(len(rows) > 0)
        self.assertEqual(list(rows[0].keys()), APPOINTMENT_LEDGER_COLUMNS)

    def test_requested_year_limits_rows(self):
        self._import_sample_data()
        sessions_2026 = self._parse_csv(generate_report_csv(self.conn, "sessions", 2026))
        sessions_2025 = self._parse_csv(generate_report_csv(self.conn, "sessions", 2025))
        self.assertTrue(all("2026" in row["session_date"] for row in sessions_2026))
        self.assertTrue(all("2025" in row["session_date"] for row in sessions_2025))
        self.assertTrue(len(sessions_2026) > 0)
        self.assertTrue(len(sessions_2025) > 0)

    def test_appointments_year_filter_excludes_other_years(self):
        self._import_sample_data()
        rows_2026 = self._parse_csv(generate_report_csv(self.conn, "appointments", 2026))
        rows_2025 = self._parse_csv(generate_report_csv(self.conn, "appointments", 2025))
        self.assertTrue(all(row["Date"].startswith("2026") for row in rows_2026))
        self.assertTrue(all(row["Date"].startswith("2025") for row in rows_2025))

    def test_valid_empty_year_returns_headers_only(self):
        self._import_sample_data()
        csv_text = generate_report_csv(self.conn, "sessions", 2099)
        rows = self._parse_csv(csv_text)
        self.assertEqual(len(rows), 0)
        reader = csv.reader(io.StringIO(csv_text))
        header = next(reader)
        self.assertEqual(header, SESSION_COLUMNS)

    def test_invalid_type_raises_value_error(self):
        self._import_sample_data()
        with self.assertRaises(ValueError):
            generate_report_csv(self.conn, "bogus", 2026)

    def test_malformed_year_raises_value_error(self):
        self._import_sample_data()
        with self.assertRaises(ValueError):
            generate_report_csv(self.conn, "sessions", "abc")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            generate_report_csv(self.conn, "sessions", 1999)
        with self.assertRaises(ValueError):
            generate_report_csv(self.conn, "sessions", 2101)

    def test_no_on_demand_call_creates_or_modifies_files_under_reports(self):
        self._import_sample_data()
        generate_report_csv(self.conn, "sessions", 2026)
        generate_report_csv(self.conn, "appointments", 2026)
        self.assertFalse(self.reports_dir.exists())


class ReportFilenameTests(unittest.TestCase):
    def test_sessions_filename(self):
        self.assertEqual(report_filename("sessions", 2026), "Jordana_Client_Sessions_2026.csv")

    def test_summary_filename(self):
        self.assertEqual(report_filename("summary", 2026), "Jordana_Client_Summary_2026.csv")

    def test_simple_filename(self):
        self.assertEqual(report_filename("simple", 2026), "Jordana_Session_Log_2026.csv")

    def test_appointments_filename_has_no_year(self):
        self.assertEqual(report_filename("appointments", 2026), "Jordana_All_Appointments.csv")

    def test_invalid_type_raises_for_filename(self):
        with self.assertRaises(ValueError):
            report_filename("bogus", 2026)


class ReportDownloadEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "server.sqlite3")
        self.handler_cls = make_handler(self.db_path)
        self.conn = connect(self.db_path)
        init_db(self.conn)
        import_rows(
            self.conn,
            [
                raw_row("snap-1", "Bonnie 5", "2026-06-23T17:00:00-04:00"),
                raw_row("snap-2", "Amber 3", "2026-06-24T11:00:00-04:00"),
            ],
            "test",
        )

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _handler(self, path: str):
        handler = object.__new__(self.handler_cls)
        handler.path = path
        handler.headers = {"Content-Length": "0"}
        handler.rfile = io.BytesIO(b"")
        handler.wfile = io.BytesIO()
        handler.send_error = lambda code: (_ for _ in ()).throw(AssertionError(f"unexpected error {code}"))
        handler.finish = lambda: None
        return handler

    def test_download_response_has_correct_content_type_and_filename(self):
        handler = self._handler("/api/reports/download?type=sessions&year=2026")
        handler.conn = lambda: self.conn

        captured = {}

        def mock_send_response(status):
            captured["status"] = status

        def mock_send_header(key, value):
            captured.setdefault("headers", {})[key] = value

        def mock_end_headers():
            pass

        def mock_wfile_write(body):
            captured["body"] = body

        handler.send_response = mock_send_response
        handler.send_header = mock_send_header
        handler.end_headers = mock_end_headers
        handler.wfile.write = mock_wfile_write

        handler.do_GET()

        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["headers"]["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("Jordana_Client_Sessions_2026.csv", captured["headers"]["Content-Disposition"])
        self.assertIn("attachment", captured["headers"]["Content-Disposition"])

    def test_reports_endpoint_returns_metadata_and_years(self):
        handler = self._handler("/api/reports")
        handler.conn = lambda: self.conn

        captured = {}
        handler.send_json = lambda payload, status=200: captured.setdefault("payload", payload)

        handler.do_GET()

        self.assertIn("reports", captured["payload"])
        self.assertIn("years", captured["payload"])
        self.assertIn("default_year", captured["payload"])
        self.assertEqual(len(captured["payload"]["reports"]), 4)
        self.assertIn(2026, captured["payload"]["years"])

    def test_download_invalid_type_returns_error(self):
        handler = self._handler("/api/reports/download?type=bogus&year=2026")
        handler.conn = lambda: self.conn

        captured = {}
        handler.send_json = lambda payload, status=200: captured.setdefault("payload", payload)
        handler.send_response = lambda status: None
        handler.send_header = lambda k, v: None
        handler.end_headers = lambda: None
        handler.wfile.write = lambda b: None

        handler.do_GET()

        self.assertFalse(captured.get("payload", {}).get("ok", True))


class WriteReportsUnchangedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "test.sqlite3")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_write_reports_still_produces_four_files(self):
        import_rows(
            self.conn,
            [raw_row("snap-1", "Bonnie 5", "2026-06-23T17:00:00-04:00")],
            "test",
        )
        reports_dir = self.root / "Reports"
        paths = write_reports(self.conn, reports_dir=reports_dir)

        self.assertEqual(len(paths), 4)
        for path in paths:
            self.assertTrue(path.exists())

    def test_write_reports_default_year_uses_current_eastern_year(self):
        import_rows(
            self.conn,
            [
                raw_row("snap-2026", "Bonnie 5", "2026-06-23T17:00:00-04:00"),
                raw_row("snap-2027", "Bonnie 6", "2027-06-23T17:00:00-04:00"),
            ],
            "test",
        )
        reports_dir = self.root / "Reports"
        with patch("jordana_invoice.csv_reports.current_eastern_year", return_value=2027):
            paths = write_reports(self.conn, reports_dir=reports_dir)

        self.assertEqual(paths[0].name, "Jordana_Client_Sessions_2027.csv")
        self.assertTrue((reports_dir / "Jordana_All_Appointments.csv").exists())
        with (reports_dir / "Jordana_Client_Sessions_2027.csv").open() as handle:
            rows = list(csv.DictReader(handle))
        self.assertTrue(rows)
        self.assertTrue(all(row["session_date"].startswith("2027") for row in rows))

    def test_current_eastern_year_handles_december_to_january_boundary(self):
        self.assertEqual(
            current_eastern_year(datetime(2027, 1, 1, 4, 59, tzinfo=timezone.utc)),
            2026,
        )
        self.assertEqual(
            current_eastern_year(datetime(2027, 1, 1, 5, 0, tzinfo=timezone.utc)),
            2027,
        )


if __name__ == "__main__":
    unittest.main()
