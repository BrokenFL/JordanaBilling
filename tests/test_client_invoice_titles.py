import os
import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import (
    MIGRATION_023_CLIENT_INVOICE_TITLE,
    connect,
    migrate_database,
)
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_names import format_invoice_person_name
from jordana_invoice.invoice_services import get_invoice, stage_approved_sessions_to_monthly_drafts
from jordana_invoice.review_services import (
    approve_candidate,
    create_billing_party,
    create_person,
    update_person,
)
from jordana_invoice.util import stable_hash


def raw_row(key: str) -> dict[str, str]:
    return {
        "ingested_at": "2026-08-16T12:00:00Z",
        "snapshot_key": key,
        "run_id": f"run-{key}",
        "batch_name": "invoice-title-test",
        "capture_window": "past_7_days",
        "captured_at": "2026-08-16T12:00:00Z",
        "source_device": "test",
        "timezone": "America/New_York",
        "calendar_event_id": f"event-{key}",
        "event_fingerprint": f"fingerprint-{key}",
        "event_title": "Avery Stone | 60 | Office",
        "start_at": "2026-08-15T10:00:00-04:00",
        "end_at": "2026-08-15T11:00:00-04:00",
        "duration_minutes": "60",
        "calendar": "Jordana Work",
        "payload_version": "2",
        "raw_json": "{}",
    }


class ClientInvoiceTitleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_backup_dir = os.environ.get("JORDANA_BACKUP_DIR")
        os.environ["JORDANA_BACKUP_DIR"] = str(self.root)
        self.db_path = self.root / "test.sqlite3"
        migrate_database(self.db_path)
        self.conn = connect(self.db_path)

    def tearDown(self):
        self.conn.close()
        if self.old_backup_dir is None:
            os.environ.pop("JORDANA_BACKUP_DIR", None)
        else:
            os.environ["JORDANA_BACKUP_DIR"] = self.old_backup_dir
        self.temp.cleanup()

    def _stage_draft(self, *, billing_name: str = "Avery Stone") -> tuple[dict, dict]:
        person = create_person(
            self.conn,
            {"first_name": "Avery", "last_name": "Stone", "display_name": "Avery Stone"},
        )
        party = create_billing_party(
            self.conn,
            {
                "billing_name": billing_name,
                "person_id": person["person_id"],
                "billing_email": "avery@example.test",
                "preferred_delivery_method": "email",
            },
        )
        import_rows(self.conn, [raw_row("title-test")], "invoice-title-test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash("calendar_event_id:event-title-test"),),
        ).fetchone()[0]
        approve_candidate(
            self.conn,
            candidate_id,
            {
                "participants": [
                    {
                        "person_id": person["person_id"],
                        "display_name": person["display_name"],
                        "is_primary": True,
                    }
                ],
                "billing_party_id": party["billing_party_id"],
                "approved_duration_minutes": 60,
                "service_mode": "office",
                "time_category": "standard",
                "approved_rate": "150.00",
                "payment_status": "unpaid",
                "billing_treatment": "billable",
            },
        )
        staged = stage_approved_sessions_to_monthly_drafts(self.conn)
        self.assertEqual(staged["sessions_staged"], 1)
        invoice = self.conn.execute(
            "SELECT invoice_id FROM invoices WHERE status = 'draft'"
        ).fetchone()
        return person, get_invoice(self.conn, invoice["invoice_id"])

    def test_migration_adds_disabled_preference_by_default(self):
        column = self.conn.execute(
            "SELECT name FROM pragma_table_info('people') WHERE name = 'use_dr_on_invoices'"
        ).fetchone()
        migration = self.conn.execute(
            "SELECT migration_id FROM schema_migrations WHERE migration_id = ?",
            (MIGRATION_023_CLIENT_INVOICE_TITLE,),
        ).fetchone()
        self.assertIsNotNone(column)
        self.assertIsNotNone(migration)
        person = create_person(self.conn, "Avery Stone")
        self.assertEqual(person["use_dr_on_invoices"], 0)

    def test_formatter_adds_title_once_only_when_selected(self):
        self.assertEqual(format_invoice_person_name("Avery Stone", False), "Avery Stone")
        self.assertEqual(format_invoice_person_name("Avery Stone", True), "Dr. Avery Stone")
        self.assertEqual(format_invoice_person_name("Dr. Avery Stone", True), "Dr. Avery Stone")

    def test_checkbox_refreshes_editable_draft_without_changing_calendar_name(self):
        person, draft = self._stage_draft()
        invoice_id = draft["invoice"]["invoice_id"]
        self.assertEqual(draft["render_model"]["bill_to_lines"][0], "Avery Stone")
        self.assertEqual(draft["lines"][0]["participants_snapshot"], "Avery Stone")
        aliases_before = self.conn.execute(
            "SELECT COUNT(*) FROM calendar_aliases WHERE person_id = ?",
            (person["person_id"],),
        ).fetchone()[0]

        updated = update_person(
            self.conn,
            person["person_id"],
            {"use_dr_on_invoices": True},
        )
        refreshed = get_invoice(self.conn, invoice_id)
        self.assertEqual(updated["display_name"], "Avery Stone")
        self.assertEqual(updated["use_dr_on_invoices"], 1)
        self.assertEqual(refreshed["render_model"]["bill_to_lines"][0], "Dr. Avery Stone")
        self.assertEqual(refreshed["lines"][0]["participants_snapshot"], "Dr. Avery Stone")
        aliases = self.conn.execute(
            "SELECT COUNT(*) FROM calendar_aliases WHERE person_id = ?",
            (person["person_id"],),
        ).fetchone()[0]
        self.assertEqual(aliases, aliases_before)

    def test_finalized_title_snapshots_stay_frozen_when_checkbox_changes(self):
        person, draft = self._stage_draft()
        invoice_id = draft["invoice"]["invoice_id"]
        update_person(self.conn, person["person_id"], {"use_dr_on_invoices": True})
        self.conn.execute(
            "UPDATE invoices SET status = 'finalized' WHERE invoice_id = ?",
            (invoice_id,),
        )
        self.conn.commit()

        update_person(self.conn, person["person_id"], {"use_dr_on_invoices": False})
        invoice = self.conn.execute(
            "SELECT bill_to_name_snapshot FROM invoices WHERE invoice_id = ?",
            (invoice_id,),
        ).fetchone()
        line = self.conn.execute(
            "SELECT participants_snapshot FROM invoice_line_items WHERE invoice_id = ?",
            (invoice_id,),
        ).fetchone()
        self.assertEqual(invoice["bill_to_name_snapshot"], "Dr. Avery Stone")
        self.assertEqual(line["participants_snapshot"], "Dr. Avery Stone")

    def test_custom_billing_contact_name_is_not_prefixed(self):
        person, draft = self._stage_draft(billing_name="Office Manager")
        update_person(self.conn, person["person_id"], {"use_dr_on_invoices": True})
        refreshed = get_invoice(self.conn, draft["invoice"]["invoice_id"])
        self.assertEqual(refreshed["render_model"]["bill_to_lines"][0], "Office Manager")
        self.assertEqual(refreshed["lines"][0]["participants_snapshot"], "Dr. Avery Stone")

    def test_client_workspace_exposes_and_saves_invoice_title_checkbox(self):
        javascript = (Path(__file__).resolve().parents[1] / "app/jordana_invoice/static/review.js").read_text()
        self.assertIn('id="recordUseDrOnInvoices" type="checkbox"', javascript)
        self.assertIn('use_dr_on_invoices: $("recordUseDrOnInvoices").checked', javascript)
        self.assertIn("Calendar matching keeps the normal display name.", javascript)


if __name__ == "__main__":
    unittest.main()
