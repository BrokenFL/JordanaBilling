import sqlite3
import unittest
from datetime import date

from jordana_invoice.db import init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.month_close import get_month_close_report


def raw_row(snapshot_key, capture_window="next_2_days", payload_version="3"):
    return {
        "ingested_at": "2026-07-30T12:00:00Z",
        "snapshot_key": snapshot_key,
        "run_id": "run-july",
        "batch_name": "JORDANA_CALENDAR_TEST",
        "capture_window": capture_window,
        "captured_at": "2026-07-30T08:00:00-04:00",
        "source_device": "test-device",
        "timezone": "America/New_York",
        "calendar_event_id": "future-moved-event",
        "event_fingerprint": "future-moved-fingerprint",
        "event_title": "Fictional Client 5",
        "start_at": "2026-07-31T17:00:00-04:00",
        "end_at": "2026-07-31T18:00:00-04:00",
        "duration_minutes": "60",
        "location": "",
        "notes": "",
        "calendar": "Jordana Work",
        "payload_version": payload_version,
        "raw_json": "{}",
    }


class MonthCloseTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def past_row(self, key="past", **changes):
        row = raw_row(key, capture_window="past_3_days")
        row.update(captured_at="2026-08-01T10:00:00-04:00")
        row.update(changes)
        return row

    def evidence_check(self):
        report = get_month_close_report(self.conn, "2026-07", today=date(2026, 8, 4))
        return next(item for item in report["checks"] if item["id"] == "raw_to_session")

    def approved_month_session(self, key="invoice-gap", *, payment="unpaid", treatment="billable", payer="payer-1", appointment="completed"):
        row = self.past_row(key, event_title="Avery Stone | 60 | Office")
        row.update(start_at="2026-07-31T17:00:00-04:00", end_at="2026-07-31T18:00:00-04:00")
        import_rows(self.conn, [row], "test")
        session_id = self.conn.execute("SELECT id FROM sessions ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        self.conn.execute(
            "UPDATE sessions SET session_date='2026-07-31', review_status='approved', appointment_status=?, "
            "billing_treatment=?, billable_status='billable', payment_status=?, approved_rate_cents=15000, "
            "rate_cents_snapshot=15000, billing_party_id=? WHERE id=?",
            (appointment, treatment, payment, payer, session_id),
        )
        return session_id

    def active_payer(self, payer="payer-1", active=1):
        self.conn.execute(
            """INSERT INTO billing_parties
               (billing_party_id, billing_party_type, billing_name, preferred_delivery_method,
                active, created_at, updated_at)
               VALUES (?, 'person', 'Avery Stone', 'unresolved', ?, '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z')""",
            (payer, active),
        )

    def active_invoice_line(self, session_id, status="draft", payer="payer-1"):
        self.conn.execute(
            """INSERT INTO invoices
               (invoice_id, status, bill_to_party_id, billing_period_start, billing_period_end,
                invoice_date, billing_month, created_at, updated_at)
               VALUES (?, ?, ?, '2026-07-01', '2026-07-31', '2026-07-31', '2026-07',
                       '2026-07-31T00:00:00Z', '2026-07-31T00:00:00Z')""",
               (f"invoice-{status}-{session_id}", status, payer),
        )
        self.conn.execute(
            """INSERT INTO invoice_line_items
               (invoice_line_item_id, invoice_id, source_session_id, service_date,
                participants_snapshot, service_name_snapshot, description_snapshot,
                unit_amount_cents, line_amount_cents, created_at, updated_at)
               VALUES (?, ?, ?, '2026-07-31', 'Avery Stone', 'Therapy', 'Therapy',
                       15000, 15000, '2026-07-31T00:00:00Z', '2026-07-31T00:00:00Z')""",
            (f"line-{status}-{session_id}", f"invoice-{status}-{session_id}", session_id),
        )

    def staging_check(self):
        report = get_month_close_report(self.conn, "2026-07", today=date(2026, 8, 4))
        return next(item for item in report["checks"] if item["id"] == "invoice_staging")

    def test_approved_session_missing_active_invoice_is_staging_gap(self):
        self.active_payer()
        self.approved_month_session()
        self.assertEqual(self.staging_check()["count"], 1)
        self.assertIn("Not staged", self.staging_check()["items"][0]["reason"])

    def test_approved_session_on_draft_or_finalized_invoice_is_covered(self):
        for status in ("draft", "finalized"):
            with self.subTest(status=status):
                self.active_payer(payer=f"payer-{status}")
                session_id = self.approved_month_session(key=f"invoice-{status}", payer=f"payer-{status}")
                self.active_invoice_line(session_id, status=status, payer=f"payer-{status}")
                self.assertEqual(self.staging_check()["count"], 0)

    def test_paid_at_session_requires_invoice_staging(self):
        self.active_payer()
        self.approved_month_session(payment="paid_at_session")
        self.assertEqual(self.staging_check()["count"], 1)

    def test_waived_session_requires_invoice_staging(self):
        self.active_payer()
        self.approved_month_session(treatment="waived")
        self.assertEqual(self.staging_check()["count"], 1)

    def test_future_scheduled_session_is_not_an_invoice_staging_gap(self):
        self.active_payer()
        self.approved_month_session(appointment="scheduled")
        self.assertEqual(self.staging_check()["count"], 0)

    def test_missing_or_inactive_payer_remains_actionable_staging_gap(self):
        self.approved_month_session(payer=None)
        missing = self.staging_check()
        self.assertEqual(missing["count"], 1)
        self.assertEqual(missing["items"][0]["reason"], "Missing bill-to party")
        self.active_payer(active=0)
        self.conn.execute("UPDATE sessions SET billing_party_id='payer-1'")
        inactive = self.staging_check()
        self.assertEqual(inactive["count"], 1)
        self.assertEqual(inactive["items"][0]["reason"], "Bill-to party is inactive")

    def test_equivalent_capture_matches_existing_approved_record_without_writes(self):
        import_rows(self.conn, [self.past_row()], "test")
        self.conn.execute("UPDATE sessions SET review_status='approved'")
        self.conn.execute("UPDATE calendar_event_candidates SET review_status='approved'")
        import_rows(self.conn, [self.past_row("utc-copy", calendar_event_id="", event_fingerprint="",
            start_at="2026-07-31T21:00:00Z", end_at="2026-07-31T22:00:00Z")], "test")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM calendar_event_candidates").fetchone()[0], 1)
        before = list(self.conn.iterdump())
        self.assertEqual(self.evidence_check()["count"], 0)
        self.assertEqual(before, list(self.conn.iterdump()))

    def test_alias_matches_when_original_candidate_key_differs(self):
        import_rows(self.conn, [self.past_row()], "test")
        self.conn.execute("UPDATE calendar_event_candidates SET candidate_key='retained-original-key'")
        self.assertEqual(self.evidence_check()["count"], 0)

    def test_real_unlinked_post_session_capture_is_still_reported(self):
        import_rows(self.conn, [raw_row("missing")], "test")
        self.conn.execute("UPDATE raw_calendar_snapshots SET capture_window='past_7_days', captured_at='2026-08-01T14:00:00Z'")
        self.assertEqual(self.evidence_check()["count"], 1)

    def test_pre_end_past_capture_is_not_a_missing_appointment(self):
        import_rows(self.conn, [raw_row("pre-end", capture_window="past_3_days")], "test")
        self.assertEqual(self.evidence_check()["count"], 0)

    def test_real_missing_count_is_not_truncated_to_fifty(self):
        rows = [raw_row(f"missing-{i}") for i in range(51)]
        for i, row in enumerate(rows):
            row.update(calendar_event_id=f"event-{i}", event_fingerprint=f"fp-{i}")
        import_rows(self.conn, rows, "test")
        self.conn.execute("UPDATE raw_calendar_snapshots SET capture_window='past_3_days', captured_at='2026-08-01T14:00:00Z'")
        self.assertEqual(self.evidence_check()["count"], 51)

    def test_candidate_only_review_cannot_produce_false_pass(self):
        import_rows(self.conn, [self.past_row(event_title="Avery Stone 5 unknown")], "test")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)
        report = get_month_close_report(self.conn, "2026-07", today=date(2026, 8, 4))
        review = next(item for item in report["checks"] if item["id"] == "review")
        self.assertEqual(review["count"], 1)
        self.conn.execute("UPDATE calendar_event_candidates SET calendar_review_state='absent'")
        report = get_month_close_report(self.conn, "2026-07", today=date(2026, 8, 4))
        review = next(item for item in report["checks"] if item["id"] == "review")
        self.assertEqual(review["count"], 0)

    def test_additive_capture_run_migration_is_installed(self):
        table = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'calendar_capture_runs'"
        ).fetchone()
        self.assertIsNotNone(table)

    def complete_closing_sweep(self):
        self.conn.execute(
            """INSERT INTO calendar_capture_runs (
                 run_id, started_at, completed_at, past_found, past_received,
                 future_found, future_received, status, synced_at
               ) VALUES ('close-july', '2026-08-02T08:00:00-04:00',
                 '2026-08-02T08:01:00-04:00', 3, 3, 2, 2, 'complete',
                 '2026-08-02T12:01:00Z')"""
        )

    def test_future_only_snapshot_does_not_create_missing_session_warning(self):
        self.complete_closing_sweep()
        import_rows(self.conn, [raw_row("future-only")], "test")
        report = get_month_close_report(self.conn, "2026-07", today=date(2026, 8, 4))
        raw_check = next(item for item in report["checks"] if item["id"] == "raw_to_session")
        self.assertEqual(raw_check["status"], "passed")
        self.assertEqual(raw_check["count"], 0)

    def test_incomplete_capture_run_blocks_close(self):
        self.conn.execute(
            """INSERT INTO calendar_capture_runs (
                 run_id, started_at, completed_at, past_found, past_received,
                 future_found, future_received, status, synced_at
               ) VALUES ('partial-july', '2026-07-15T08:00:00-04:00',
                 '2026-07-15T08:01:00-04:00', 4, 3, 2, 2, 'partial',
                 '2026-07-15T12:01:00Z')"""
        )
        report = get_month_close_report(self.conn, "2026-07", today=date(2026, 8, 4))
        capture = next(item for item in report["checks"] if item["id"] == "capture_integrity")
        self.assertEqual(report["status"], "action_needed")
        self.assertEqual(capture["status"], "action_needed")
        self.assertEqual(capture["count"], 1)

    def test_legacy_future_only_candidate_does_not_create_review_warning(self):
        self.complete_closing_sweep()
        import_rows(self.conn, [raw_row("legacy-future", payload_version="2")], "test")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 1)
        report = get_month_close_report(self.conn, "2026-07", today=date(2026, 8, 4))
        review = next(item for item in report["checks"] if item["id"] == "review")
        self.assertEqual(review["status"], "passed")

    def test_clean_post_month_sweep_is_ready_and_unpaid_is_not_a_blocker(self):
        self.complete_closing_sweep()
        report = get_month_close_report(self.conn, "2026-07", today=date(2026, 8, 4))
        self.assertEqual(report["status"], "ready")
        payments = next(item for item in report["checks"] if item["id"] == "payments")
        self.assertEqual(payments["status"], "informational")

    def test_later_complete_run_clears_recovered_partial_warning(self):
        self.conn.execute(
            """INSERT INTO calendar_capture_runs (
                 run_id, started_at, completed_at, past_found, past_received,
                 future_found, future_received, status, synced_at
               ) VALUES ('partial', '2026-07-15T08:00:00-04:00',
                 '2026-07-15T08:01:00-04:00', 4, 3, 2, 2, 'partial',
                 '2026-07-15T12:01:00Z')"""
        )
        self.conn.execute(
            """INSERT INTO calendar_capture_runs (
                 run_id, started_at, completed_at, past_found, past_received,
                 future_found, future_received, status, synced_at
               ) VALUES ('recovery', '2026-07-16T08:00:00-04:00',
                 '2026-07-16T08:01:00-04:00', 4, 4, 2, 2, 'complete',
                 '2026-07-16T12:01:00Z')"""
        )
        self.complete_closing_sweep()
        report = get_month_close_report(self.conn, "2026-07", today=date(2026, 8, 4))
        capture = next(item for item in report["checks"] if item["id"] == "capture_integrity")
        self.assertEqual(capture["status"], "passed")


if __name__ == "__main__":
    unittest.main()
