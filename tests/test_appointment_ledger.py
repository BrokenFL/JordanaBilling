import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path

from jordana_invoice.appointment_ledger import list_appointment_ledger_page
from jordana_invoice.csv_reports import write_reports
from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows


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


class AppointmentLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "ledger.sqlite3")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_candidate_only_rows_are_written_to_all_appointments_csv(self):
        import_rows(
            self.conn,
            [
                raw_row("snap-session", "Bonnie 5", "2026-06-23T17:00:00-04:00"),
                raw_row("snap-unresolved", "Raisin??", "2026-06-24T11:00:00-04:00"),
            ],
            "test",
        )

        reports_dir = self.root / "Reports"
        write_reports(self.conn, reports_dir=reports_dir)

        with (reports_dir / "Jordana_All_Appointments.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

        unresolved = next(row for row in rows if row["Calendar Title"] == "Raisin??")
        self.assertEqual(unresolved["Classification"], "unresolved")
        self.assertEqual(unresolved["Rate"], "")
        self.assertEqual(unresolved["Client / Participants"], "")

    def test_all_appointments_csv_stays_cumulative_and_deduplicated(self):
        import_rows(
            self.conn,
            [raw_row("snap-1", "Bonnie 5", "2026-06-23T17:00:00-04:00", event_fingerprint="fp-bonnie")],
            "test",
        )
        write_reports(self.conn, reports_dir=self.root / "Reports")

        import_rows(
            self.conn,
            [raw_row("snap-2", "Bonnie 5", "2026-06-23T17:00:00-04:00", event_fingerprint="fp-bonnie")],
            "test",
        )
        write_reports(self.conn, reports_dir=self.root / "Reports")

        with (self.root / "Reports" / "Jordana_All_Appointments.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

        bonnie_rows = [row for row in rows if row["Calendar Title"] == "Bonnie 5"]
        self.assertEqual(len(bonnie_rows), 1)

    def test_sessions_filters_and_pagination_use_shared_ledger_query(self):
        rows = []
        for day in range(1, 36):
            rows.append(
                raw_row(
                    f"snap-{day}",
                    f"Bonnie {day}",
                    f"2026-06-{day:02d}T17:00:00-04:00" if day <= 30 else f"2026-05-{day - 30:02d}T17:00:00-04:00",
                )
            )
        import_rows(self.conn, rows, "test")

        sessions = self.conn.execute(
            "SELECT id, session_date FROM sessions ORDER BY start_at DESC"
        ).fetchall()
        for index, session in enumerate(sessions):
            payment_status = "paid" if index % 2 == 0 else "unpaid"
            review_status = "approved" if index % 3 == 0 else "needs_review"
            self.conn.execute(
                "UPDATE sessions SET payment_status = ?, review_status = ? WHERE id = ?",
                (payment_status, review_status, session["id"]),
            )
        self.conn.commit()

        first_page = list_appointment_ledger_page(
            self.conn,
            date_range="rolling_30",
            limit=30,
            offset=0,
            today=date(2026, 6, 30),
        )
        filtered = list_appointment_ledger_page(
            self.conn,
            date_range="this_month",
            review_status="approved",
            payment_status="paid",
            limit=30,
            offset=0,
            today=date(2026, 6, 30),
        )
        second_page = list_appointment_ledger_page(
            self.conn,
            date_range="all",
            limit=30,
            offset=30,
            today=date(2026, 6, 30),
        )

        self.assertEqual(first_page["total"], 30)
        self.assertEqual(len(first_page["items"]), 30)
        self.assertEqual(first_page["items"][0]["date"], "2026-06-30")
        self.assertEqual(second_page["total"], 35)
        self.assertEqual(len(second_page["items"]), 5)
        self.assertTrue(filtered["items"])
        self.assertTrue(all(item["review_status"] == "approved" for item in filtered["items"]))
        self.assertTrue(all(item["payment_status"] == "paid" for item in filtered["items"]))

    def test_needs_classification_filter_returns_candidate_only_send_to_review_rows(self):
        import_rows(
            self.conn,
            [
                raw_row("snap-session", "Bonnie 5", "2026-06-23T17:00:00-04:00"),
                raw_row("snap-unresolved", "Raisin??", "2026-06-24T11:00:00-04:00"),
            ],
            "test",
        )

        result = list_appointment_ledger_page(
            self.conn,
            date_range="all",
            review_status="needs_classification",
            limit=30,
            offset=0,
            today=date(2026, 6, 30),
        )

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["calendar_title"], "Raisin??")
        self.assertIsNone(result["items"][0]["session_id"])
        self.assertEqual(result["items"][0]["review_status"], "needs_classification")


if __name__ == "__main__":
    unittest.main()
