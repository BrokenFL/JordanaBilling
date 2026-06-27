import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import (
    create_invoice_draft,
    finalize_invoice,
    get_business_profile,
    save_business_profile,
)
from jordana_invoice.review_server import make_handler
from jordana_invoice.review_services import (
    approve_candidate,
    create_billing_party,
    create_person,
    get_person_record,
    list_review_candidates,
    preview_copy_contact_details,
    apply_copy_contact_details,
    update_billing_party,
)


def raw_row(snapshot_key, title="Robin Rivers 6", start="2026-06-17T18:00:00-04:00"):
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
        "start_at": start,
        "end_at": "2026-06-17T19:00:00-04:00",
        "duration_minutes": "60",
        "calendar": "Jordana Calendar",
        "payload_version": "2",
        "raw_json": "{}",
    }


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]


class BillingSetupCreateTests(unittest.TestCase):
    """Tests for create_billing_party safety and field persistence."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "create.sqlite3")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_create_person_linked_billing_party_stores_correct_person_id(self):
        person = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        bp = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers",
            "billing_party_type": "person",
            "person_id": person["person_id"],
        })
        self.assertEqual(bp["person_id"], person["person_id"])

    def test_all_billing_contact_fields_persist(self):
        person = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        bp = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers",
            "billing_party_type": "person",
            "person_id": person["person_id"],
            "billing_email": "robin@example.test",
            "billing_phone": "555-1234",
            "billing_address_line_1": "123 Main St",
            "billing_address_line_2": "Apt 4B",
            "billing_city": "Anytown",
            "billing_state": "CA",
            "billing_postal_code": "90210",
            "preferred_delivery_method": "email",
        })
        row = self.conn.execute(
            "SELECT * FROM billing_parties WHERE billing_party_id = ?", (bp["billing_party_id"],)
        ).fetchone()
        self.assertEqual(row["billing_name"], "Robin Rivers")
        self.assertEqual(row["billing_email"], "robin@example.test")
        self.assertEqual(row["billing_phone"], "555-1234")
        self.assertEqual(row["billing_address_line_1"], "123 Main St")
        self.assertEqual(row["billing_address_line_2"], "Apt 4B")
        self.assertEqual(row["billing_city"], "Anytown")
        self.assertEqual(row["billing_state"], "CA")
        self.assertEqual(row["billing_postal_code"], "90210")
        self.assertEqual(row["preferred_delivery_method"], "email")

    def test_invalid_delivery_method_rejected_on_create(self):
        with self.assertRaises(ValueError):
            create_billing_party(self.conn, {
                "billing_name": "Test Payer",
                "billing_party_type": "person",
                "preferred_delivery_method": "carrier_pigeon",
            })

    def test_invalid_billing_party_type_rejected_on_create(self):
        with self.assertRaises(ValueError):
            create_billing_party(self.conn, {
                "billing_name": "Test Payer",
                "billing_party_type": "alien",
            })

    def test_create_billing_party_does_not_create_account_or_membership(self):
        person = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        before_accounts = count(self.conn, "client_accounts")
        before_members = count(self.conn, "account_members")
        create_billing_party(self.conn, {
            "billing_name": "Robin Rivers",
            "billing_party_type": "person",
            "person_id": person["person_id"],
        })
        self.assertEqual(count(self.conn, "client_accounts"), before_accounts)
        self.assertEqual(count(self.conn, "account_members"), before_members)

    def test_create_billing_party_records_audit(self):
        person = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        before = count(self.conn, "audit_log")
        bp = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers",
            "billing_party_type": "person",
            "person_id": person["person_id"],
        })
        after = count(self.conn, "audit_log")
        self.assertEqual(after, before + 1)
        entry = self.conn.execute(
            "SELECT * FROM audit_log WHERE entity_id = ? ORDER BY created_at DESC LIMIT 1",
            (bp["billing_party_id"],),
        ).fetchone()
        self.assertEqual(entry["entity_type"], "billing_party")
        self.assertEqual(entry["action"], "created_inline")


class InvoiceSettingsBusinessProfileTests(unittest.TestCase):
    """Tests for invoice settings persistence, including Zelle."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "settings.sqlite3")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_zelle_recipient_persists_in_business_profile(self):
        save_business_profile(self.conn, {
            "business_name": "Demo Practice",
            "payee_name": "Demo Payee",
            "payment_address_line_1": "100 Example Ave",
            "payment_city": "Example",
            "payment_state": "FL",
            "payment_postal_code": "00000",
            "zelle_recipient": "demo-zelle@example.test",
        })
        profile = get_business_profile(self.conn)
        self.assertEqual(profile["zelle_recipient"], "demo-zelle@example.test")

    def test_zelle_recipient_updates_without_creating_new_profile(self):
        first = save_business_profile(self.conn, {
            "business_name": "Demo Practice",
            "payee_name": "Demo Payee",
            "payment_address_line_1": "100 Example Ave",
            "payment_city": "Example",
            "payment_state": "FL",
            "payment_postal_code": "00000",
            "zelle_recipient": "demo-zelle@example.test",
        })
        second = save_business_profile(self.conn, {"zelle_recipient": "15551234567"})
        self.assertEqual(first["business_profile_id"], second["business_profile_id"])
        self.assertEqual(second["zelle_recipient"], "15551234567")


class BillingSetupUpdateTests(unittest.TestCase):
    """Tests for update_billing_party isolation, validation, and audit."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "update.sqlite3")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_update_one_billing_party_does_not_change_another(self):
        person = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        bp1 = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers",
            "billing_party_type": "person",
            "person_id": person["person_id"],
            "billing_email": "robin@example.test",
        })
        bp2 = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers Work",
            "billing_party_type": "person",
            "person_id": person["person_id"],
            "billing_email": "robin.work@example.test",
        })
        update_billing_party(self.conn, bp1["billing_party_id"], {
            "billing_email": "robin.new@example.test",
        })
        row2 = self.conn.execute(
            "SELECT billing_email FROM billing_parties WHERE billing_party_id = ?", (bp2["billing_party_id"],)
        ).fetchone()
        self.assertEqual(row2["billing_email"], "robin.work@example.test")

    def test_update_billing_party_records_audit(self):
        person = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        bp = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers",
            "billing_party_type": "person",
            "person_id": person["person_id"],
        })
        before = count(self.conn, "audit_log")
        update_billing_party(self.conn, bp["billing_party_id"], {
            "billing_email": "robin.updated@example.test",
        })
        after = count(self.conn, "audit_log")
        self.assertEqual(after, before + 1)
        entry = self.conn.execute(
            "SELECT * FROM audit_log WHERE entity_id = ? ORDER BY created_at DESC LIMIT 1",
            (bp["billing_party_id"],),
        ).fetchone()
        self.assertEqual(entry["entity_type"], "billing_party")
        self.assertEqual(entry["action"], "updated_inline")

    def test_invalid_delivery_method_rejected_on_update(self):
        person = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        bp = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers",
            "billing_party_type": "person",
            "person_id": person["person_id"],
        })
        with self.assertRaises(ValueError):
            update_billing_party(self.conn, bp["billing_party_id"], {
                "preferred_delivery_method": "fax",
            })

    def test_invalid_billing_party_type_rejected_on_update(self):
        person = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        bp = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers",
            "billing_party_type": "person",
            "person_id": person["person_id"],
        })
        with self.assertRaises(ValueError):
            update_billing_party(self.conn, bp["billing_party_id"], {
                "billing_party_type": "syndicate",
            })

    def test_update_does_not_create_account_or_membership(self):
        person = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        bp = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers",
            "billing_party_type": "person",
            "person_id": person["person_id"],
        })
        before_accounts = count(self.conn, "client_accounts")
        before_members = count(self.conn, "account_members")
        update_billing_party(self.conn, bp["billing_party_id"], {
            "billing_email": "robin.new@example.test",
        })
        self.assertEqual(count(self.conn, "client_accounts"), before_accounts)
        self.assertEqual(count(self.conn, "account_members"), before_members)

    def test_unrelated_client_cannot_be_silently_reassigned(self):
        person_a = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        person_b = create_person(self.conn, {"display_name": "Taylor Reed", "first_name": "Taylor", "last_name": "Reed"})
        bp = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers",
            "billing_party_type": "person",
            "person_id": person_a["person_id"],
        })
        with self.assertRaises(ValueError):
            update_billing_party(self.conn, bp["billing_party_id"], {
                "person_id": person_b["person_id"],
            })

    def test_update_same_person_id_is_allowed(self):
        person = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        bp = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers",
            "billing_party_type": "person",
            "person_id": person["person_id"],
        })
        result = update_billing_party(self.conn, bp["billing_party_id"], {
            "person_id": person["person_id"],
            "billing_email": "robin.same@example.test",
        })
        self.assertEqual(result["person_id"], person["person_id"])
        self.assertEqual(result["billing_email"], "robin.same@example.test")


class BillingSetupDeactivationTests(unittest.TestCase):
    """Tests that deactivation sets active=0 and preserves historical references."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "deact.sqlite3")
        init_db(self.conn)
        save_business_profile(self.conn, {
            "business_name": "Test Practice", "provider_display_name": "Test Provider",
            "address_line_1": "100 Test Ave", "city": "Test", "state": "FL", "postal_code": "00000",
            "phone": "555-0100", "email": "test@example.test", "payee_name": "Test Payee",
            "payment_address_line_1": "100 Test Ave", "payment_city": "Test", "payment_state": "FL",
            "payment_postal_code": "00000", "zelle_recipient": "test-zelle@example.test",
        })
        self.conn.commit()
        import_rows(self.conn, [raw_row("snap-d1")], "test")
        self.candidate_id = list_review_candidates(self.conn)["items"][0]["candidate_id"]
        self.person = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        self.payer = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers",
            "billing_party_type": "person",
            "person_id": self.person["person_id"],
            "billing_email": "robin@example.test",
            "billing_address_line_1": "123 Main St",
            "billing_city": "Anytown",
            "billing_state": "CA",
            "billing_postal_code": "90210",
            "preferred_delivery_method": "email",
        })

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _approve_session(self):
        approve_candidate(self.conn, self.candidate_id, {
            "participants": [
                {"person_id": self.person["person_id"], "display_name": "Robin Rivers", "is_primary": True},
            ],
            "billing_party_id": self.payer["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "unpaid",
        })

    def test_deactivation_sets_active_zero(self):
        result = update_billing_party(self.conn, self.payer["billing_party_id"], {"active": False})
        self.assertEqual(result["active"], 0)

    def test_deactivation_does_not_change_session_billing_party_id(self):
        self._approve_session()
        session = self.conn.execute("SELECT billing_party_id FROM sessions WHERE candidate_id = ?", (self.candidate_id,)).fetchone()
        original_bp_id = session["billing_party_id"]
        update_billing_party(self.conn, self.payer["billing_party_id"], {"active": False})
        session_after = self.conn.execute("SELECT billing_party_id FROM sessions WHERE candidate_id = ?", (self.candidate_id,)).fetchone()
        self.assertEqual(session_after["billing_party_id"], original_bp_id)

    def test_deactivation_does_not_change_invoice_bill_to_party_id(self):
        self._approve_session()
        session = self.conn.execute("SELECT id FROM sessions WHERE candidate_id = ?", (self.candidate_id,)).fetchone()
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.payer["billing_party_id"],
            "billing_period_start": "2026-06-01",
            "billing_period_end": "2026-06-30",
            "invoice_date": "2026-06-30",
            "session_ids": [session["id"]],
        })
        with patch("jordana_invoice.invoice_services.generate_invoice_pdf", return_value="a" * 64):
            finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "pdf")
        original_bill_to = self.conn.execute(
            "SELECT bill_to_party_id FROM invoices WHERE invoice_id = ?", (draft["invoice"]["invoice_id"],)
        ).fetchone()["bill_to_party_id"]
        update_billing_party(self.conn, self.payer["billing_party_id"], {"active": False})
        after_bill_to = self.conn.execute(
            "SELECT bill_to_party_id FROM invoices WHERE invoice_id = ?", (draft["invoice"]["invoice_id"],)
        ).fetchone()["bill_to_party_id"]
        self.assertEqual(after_bill_to, original_bill_to)


class BillingSetupInvoiceSnapshotTests(unittest.TestCase):
    """Tests that updating contact details does not change invoice snapshots."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "inv.sqlite3")
        init_db(self.conn)
        save_business_profile(self.conn, {
            "business_name": "Test Practice", "provider_display_name": "Test Provider",
            "address_line_1": "100 Test Ave", "city": "Test", "state": "FL", "postal_code": "00000",
            "phone": "555-0100", "email": "test@example.test", "payee_name": "Test Payee",
            "payment_address_line_1": "100 Test Ave", "payment_city": "Test", "payment_state": "FL",
            "payment_postal_code": "00000", "zelle_recipient": "test-zelle@example.test",
        })
        self.conn.commit()
        import_rows(self.conn, [raw_row("snap-inv")], "test")
        self.candidate_id = list_review_candidates(self.conn)["items"][0]["candidate_id"]
        self.person = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        self.payer = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers",
            "billing_party_type": "person",
            "person_id": self.person["person_id"],
            "billing_email": "robin@example.test",
            "billing_phone": "555-1234",
            "billing_address_line_1": "123 Main St",
            "billing_city": "Anytown",
            "billing_state": "CA",
            "billing_postal_code": "90210",
            "preferred_delivery_method": "email",
        })

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_updating_contact_details_does_not_change_finalized_invoice_snapshots(self):
        approve_candidate(self.conn, self.candidate_id, {
            "participants": [
                {"person_id": self.person["person_id"], "display_name": "Robin Rivers", "is_primary": True},
            ],
            "billing_party_id": self.payer["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "unpaid",
        })
        session = self.conn.execute("SELECT id FROM sessions WHERE candidate_id = ?", (self.candidate_id,)).fetchone()
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.payer["billing_party_id"],
            "billing_period_start": "2026-06-01",
            "billing_period_end": "2026-06-30",
            "invoice_date": "2026-06-30",
            "session_ids": [session["id"]],
        })
        with patch("jordana_invoice.invoice_services.generate_invoice_pdf", return_value="a" * 64):
            finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "pdf")

        inv_before = self.conn.execute(
            "SELECT bill_to_name_snapshot, bill_to_email_snapshot, bill_to_phone_snapshot, bill_to_address_snapshot, total_cents FROM invoices WHERE invoice_id = ?",
            (draft["invoice"]["invoice_id"],),
        ).fetchone()

        update_billing_party(self.conn, self.payer["billing_party_id"], {
            "billing_name": "Robin Rivers Updated",
            "billing_email": "robin.new@example.test",
            "billing_phone": "555-9999",
            "billing_address_line_1": "456 Oak Ave",
            "billing_city": "Newtown",
            "billing_state": "NY",
            "billing_postal_code": "10001",
        })

        inv_after = self.conn.execute(
            "SELECT bill_to_name_snapshot, bill_to_email_snapshot, bill_to_phone_snapshot, bill_to_address_snapshot, total_cents FROM invoices WHERE invoice_id = ?",
            (draft["invoice"]["invoice_id"],),
        ).fetchone()

        self.assertEqual(inv_after["bill_to_name_snapshot"], inv_before["bill_to_name_snapshot"])
        self.assertEqual(inv_after["bill_to_email_snapshot"], inv_before["bill_to_email_snapshot"])
        self.assertEqual(inv_after["bill_to_phone_snapshot"], inv_before["bill_to_phone_snapshot"])
        self.assertEqual(inv_after["bill_to_address_snapshot"], inv_before["bill_to_address_snapshot"])
        self.assertEqual(inv_after["total_cents"], inv_before["total_cents"])


class BillingSetupReadConsistencyTests(unittest.TestCase):
    """Tests that reading the client record returns active and inactive billing setup records."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "read.sqlite3")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_person_record_returns_active_and_inactive_billing_setup(self):
        person = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        active_bp = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers",
            "billing_party_type": "person",
            "person_id": person["person_id"],
            "billing_email": "robin@example.test",
        })
        inactive_bp = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers Old",
            "billing_party_type": "person",
            "person_id": person["person_id"],
            "billing_email": "robin.old@example.test",
        })
        update_billing_party(self.conn, inactive_bp["billing_party_id"], {"active": False})

        record = get_person_record(self.conn, person["person_id"])
        setup = record["billing_setup"]
        self.assertEqual(len(setup), 2)
        active_ids = [s["billing_party_id"] for s in setup if s["active"] == 1]
        inactive_ids = [s["billing_party_id"] for s in setup if s["active"] == 0]
        self.assertIn(active_bp["billing_party_id"], active_ids)
        self.assertIn(inactive_bp["billing_party_id"], inactive_ids)

    def test_billing_parties_list_returns_both_active_and_inactive(self):
        person = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        active_bp = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers",
            "billing_party_type": "person",
            "person_id": person["person_id"],
        })
        inactive_bp = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers Old",
            "billing_party_type": "person",
            "person_id": person["person_id"],
        })
        update_billing_party(self.conn, inactive_bp["billing_party_id"], {"active": False})

        record = get_person_record(self.conn, person["person_id"])
        bp_ids = [bp["billing_party_id"] for bp in record["billing_parties"]]
        self.assertIn(active_bp["billing_party_id"], bp_ids)
        self.assertIn(inactive_bp["billing_party_id"], bp_ids)


class BillingSetupAPITests(unittest.TestCase):
    """Tests that creating and editing through the API records audit entries."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "api.sqlite3")
        self.handler_cls = make_handler(self.db_path, write_token="test-write-token")
        self.conn = connect(Path(self.temp.name) / "api.sqlite3")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _post_handler(self, path, body):
        handler = object.__new__(self.handler_cls)
        handler.path = path
        handler.headers = {
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
            self.handler_cls.write_token_header: self.handler_cls.write_token,
        }
        handler.rfile = io.BytesIO(body.encode("utf-8"))
        handler.wfile = io.BytesIO()
        handler.send_error = lambda code: (_ for _ in ()).throw(AssertionError(f"unexpected error {code}"))
        captured = {}
        handler.send_json = lambda payload, status=200: captured.setdefault("payload", payload)
        handler.finish = lambda: None
        return handler, captured

    def test_api_create_billing_party_records_audit(self):
        person = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        before_audit = count(self.conn, "audit_log")
        body = json.dumps({
            "billing_name": "Robin Rivers",
            "billing_party_type": "person",
            "person_id": person["person_id"],
        })
        handler, captured = self._post_handler("/api/billing-parties", body)
        handler.conn = lambda: self.conn
        handler.do_POST()
        after_audit = count(self.conn, "audit_log")
        self.assertEqual(after_audit, before_audit + 1)
        self.assertIn("billing_party_id", captured["payload"])

    def test_api_update_billing_party_records_audit(self):
        person = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        bp = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers",
            "billing_party_type": "person",
            "person_id": person["person_id"],
        })
        before_audit = count(self.conn, "audit_log")
        body = json.dumps({"billing_email": "robin.api@example.test"})
        handler, captured = self._post_handler(f"/api/billing-parties/{bp['billing_party_id']}", body)
        handler.conn = lambda: self.conn
        handler.do_POST()
        after_audit = count(self.conn, "audit_log")
        self.assertEqual(after_audit, before_audit + 1)
        self.assertEqual(captured["payload"]["billing_email"], "robin.api@example.test")

    def test_api_create_does_not_create_account(self):
        person = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        before_accounts = count(self.conn, "client_accounts")
        before_members = count(self.conn, "account_members")
        body = json.dumps({
            "billing_name": "Robin Rivers",
            "billing_party_type": "person",
            "person_id": person["person_id"],
        })
        handler, _ = self._post_handler("/api/billing-parties", body)
        handler.conn = lambda: self.conn
        handler.do_POST()
        self.assertEqual(count(self.conn, "client_accounts"), before_accounts)
        self.assertEqual(count(self.conn, "account_members"), before_members)


class BillingSetupPartialUpdateTests(unittest.TestCase):
    """Tests for partial-update semantics: omit preserves, empty/null clears, blank required rejected."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "partial.sqlite3")
        init_db(self.conn)
        self.person = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        self.bp = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers",
            "billing_party_type": "person",
            "person_id": self.person["person_id"],
            "billing_email": "robin@example.test",
            "billing_phone": "555-1234",
            "billing_address_line_1": "123 Main St",
            "billing_address_line_2": "Apt 4B",
            "billing_city": "Anytown",
            "billing_state": "CA",
            "billing_postal_code": "90210",
            "preferred_delivery_method": "email",
            "administrative_notes": "Original notes",
        })

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_omitted_email_preserves_existing_email(self):
        update_billing_party(self.conn, self.bp["billing_party_id"], {"billing_phone": "555-9999"})
        row = self.conn.execute("SELECT billing_email FROM billing_parties WHERE billing_party_id = ?", (self.bp["billing_party_id"],)).fetchone()
        self.assertEqual(row["billing_email"], "robin@example.test")

    def test_empty_email_clears_existing_email(self):
        update_billing_party(self.conn, self.bp["billing_party_id"], {"billing_email": ""})
        row = self.conn.execute("SELECT billing_email FROM billing_parties WHERE billing_party_id = ?", (self.bp["billing_party_id"],)).fetchone()
        self.assertIsNone(row["billing_email"])

    def test_null_email_clears_existing_email(self):
        update_billing_party(self.conn, self.bp["billing_party_id"], {"billing_email": None})
        row = self.conn.execute("SELECT billing_email FROM billing_parties WHERE billing_party_id = ?", (self.bp["billing_party_id"],)).fetchone()
        self.assertIsNone(row["billing_email"])

    def test_empty_phone_clears_phone(self):
        update_billing_party(self.conn, self.bp["billing_party_id"], {"billing_phone": ""})
        row = self.conn.execute("SELECT billing_phone FROM billing_parties WHERE billing_party_id = ?", (self.bp["billing_party_id"],)).fetchone()
        self.assertIsNone(row["billing_phone"])

    def test_empty_address_line_1_clears(self):
        update_billing_party(self.conn, self.bp["billing_party_id"], {"billing_address_line_1": ""})
        row = self.conn.execute("SELECT billing_address_line_1 FROM billing_parties WHERE billing_party_id = ?", (self.bp["billing_party_id"],)).fetchone()
        self.assertIsNone(row["billing_address_line_1"])

    def test_empty_address_line_2_clears(self):
        update_billing_party(self.conn, self.bp["billing_party_id"], {"billing_address_line_2": ""})
        row = self.conn.execute("SELECT billing_address_line_2 FROM billing_parties WHERE billing_party_id = ?", (self.bp["billing_party_id"],)).fetchone()
        self.assertIsNone(row["billing_address_line_2"])

    def test_empty_city_clears(self):
        update_billing_party(self.conn, self.bp["billing_party_id"], {"billing_city": ""})
        row = self.conn.execute("SELECT billing_city FROM billing_parties WHERE billing_party_id = ?", (self.bp["billing_party_id"],)).fetchone()
        self.assertIsNone(row["billing_city"])

    def test_empty_state_clears(self):
        update_billing_party(self.conn, self.bp["billing_party_id"], {"billing_state": ""})
        row = self.conn.execute("SELECT billing_state FROM billing_parties WHERE billing_party_id = ?", (self.bp["billing_party_id"],)).fetchone()
        self.assertIsNone(row["billing_state"])

    def test_empty_postal_code_clears(self):
        update_billing_party(self.conn, self.bp["billing_party_id"], {"billing_postal_code": ""})
        row = self.conn.execute("SELECT billing_postal_code FROM billing_parties WHERE billing_party_id = ?", (self.bp["billing_party_id"],)).fetchone()
        self.assertIsNone(row["billing_postal_code"])

    def test_empty_administrative_notes_clears(self):
        update_billing_party(self.conn, self.bp["billing_party_id"], {"administrative_notes": ""})
        row = self.conn.execute("SELECT administrative_notes FROM billing_parties WHERE billing_party_id = ?", (self.bp["billing_party_id"],)).fetchone()
        self.assertIsNone(row["administrative_notes"])

    def test_blank_billing_name_rejected(self):
        with self.assertRaises(ValueError):
            update_billing_party(self.conn, self.bp["billing_party_id"], {"billing_name": ""})

    def test_omitted_billing_name_preserves_existing_name(self):
        update_billing_party(self.conn, self.bp["billing_party_id"], {"billing_email": "new@example.test"})
        row = self.conn.execute("SELECT billing_name FROM billing_parties WHERE billing_party_id = ?", (self.bp["billing_party_id"],)).fetchone()
        self.assertEqual(row["billing_name"], "Robin Rivers")

    def test_omitted_delivery_method_preserves_existing_method(self):
        update_billing_party(self.conn, self.bp["billing_party_id"], {"billing_email": "new@example.test"})
        row = self.conn.execute("SELECT preferred_delivery_method FROM billing_parties WHERE billing_party_id = ?", (self.bp["billing_party_id"],)).fetchone()
        self.assertEqual(row["preferred_delivery_method"], "email")

    def test_whitespace_only_optional_field_clears_to_none(self):
        update_billing_party(self.conn, self.bp["billing_party_id"], {"billing_email": "   "})
        row = self.conn.execute("SELECT billing_email FROM billing_parties WHERE billing_party_id = ?", (self.bp["billing_party_id"],)).fetchone()
        self.assertIsNone(row["billing_email"])

    def test_omitted_active_preserves_existing_active(self):
        update_billing_party(self.conn, self.bp["billing_party_id"], {"billing_email": "new@example.test"})
        row = self.conn.execute("SELECT active FROM billing_parties WHERE billing_party_id = ?", (self.bp["billing_party_id"],)).fetchone()
        self.assertEqual(row["active"], 1)

    def test_omitted_active_preserves_existing_inactive(self):
        update_billing_party(self.conn, self.bp["billing_party_id"], {"active": False})
        update_billing_party(self.conn, self.bp["billing_party_id"], {"billing_email": "new@example.test"})
        row = self.conn.execute("SELECT active FROM billing_parties WHERE billing_party_id = ?", (self.bp["billing_party_id"],)).fetchone()
        self.assertEqual(row["active"], 0)

    def test_active_false_stores_zero(self):
        update_billing_party(self.conn, self.bp["billing_party_id"], {"active": False})
        row = self.conn.execute("SELECT active FROM billing_parties WHERE billing_party_id = ?", (self.bp["billing_party_id"],)).fetchone()
        self.assertEqual(row["active"], 0)

    def test_active_true_stores_one(self):
        update_billing_party(self.conn, self.bp["billing_party_id"], {"active": False})
        update_billing_party(self.conn, self.bp["billing_party_id"], {"active": True})
        row = self.conn.execute("SELECT active FROM billing_parties WHERE billing_party_id = ?", (self.bp["billing_party_id"],)).fetchone()
        self.assertEqual(row["active"], 1)

    def test_multiple_fields_updated_simultaneously(self):
        result = update_billing_party(self.conn, self.bp["billing_party_id"], {
            "billing_email": "new@example.test",
            "billing_phone": "555-9999",
            "billing_city": "Newtown",
        })
        self.assertEqual(result["billing_email"], "new@example.test")
        self.assertEqual(result["billing_phone"], "555-9999")
        self.assertEqual(result["billing_city"], "Newtown")
        self.assertEqual(result["billing_state"], "CA")


class BillingSetupPersonValidationTests(unittest.TestCase):
    """Tests for person-linked creation validation."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "perval.sqlite3")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_nonexistent_person_produces_clear_validation_error(self):
        with self.assertRaises(ValueError) as ctx:
            create_billing_party(self.conn, {
                "billing_name": "Ghost Person",
                "billing_party_type": "person",
                "person_id": "nonexistent-uuid",
            })
        self.assertIn("does not exist", str(ctx.exception))

    def test_inactive_person_rejected(self):
        person = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        self.conn.execute("UPDATE people SET active = 0 WHERE person_id = ?", (person["person_id"],))
        self.conn.commit()
        with self.assertRaises(ValueError) as ctx:
            create_billing_party(self.conn, {
                "billing_name": "Robin Rivers",
                "billing_party_type": "person",
                "person_id": person["person_id"],
            })
        self.assertIn("not active", str(ctx.exception))

    def test_organization_creation_still_works_without_person_id(self):
        bp = create_billing_party(self.conn, {
            "billing_name": "Charity Fund",
            "billing_party_type": "organization",
            "organization_name": "Charity Fund",
        })
        self.assertEqual(bp["billing_party_type"], "organization")
        self.assertIsNone(bp["person_id"])

    def test_person_type_without_person_id_still_allowed(self):
        bp = create_billing_party(self.conn, {
            "billing_name": "Standalone Payer",
            "billing_party_type": "person",
        })
        self.assertEqual(bp["billing_party_type"], "person")
        self.assertIsNone(bp["person_id"])


class BillingSetupAuditDetailTests(unittest.TestCase):
    """Tests that audit entries identify changed fields including cleared fields."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "audit.sqlite3")
        init_db(self.conn)
        self.person = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        self.bp = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers",
            "billing_party_type": "person",
            "person_id": self.person["person_id"],
            "billing_email": "robin@example.test",
            "billing_phone": "555-1234",
            "administrative_notes": "Original notes",
        })

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _latest_audit(self):
        return self.conn.execute(
            "SELECT * FROM audit_log WHERE entity_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.bp["billing_party_id"],),
        ).fetchone()

    def test_clearing_fields_creates_audit_entry(self):
        before = count(self.conn, "audit_log")
        update_billing_party(self.conn, self.bp["billing_party_id"], {
            "billing_email": "",
            "billing_phone": "",
        })
        after = count(self.conn, "audit_log")
        self.assertEqual(after, before + 1)

    def test_audit_indicates_which_fields_changed(self):
        update_billing_party(self.conn, self.bp["billing_party_id"], {
            "billing_email": "new@example.test",
            "billing_phone": "555-9999",
        })
        entry = self._latest_audit()
        import json as _json
        details = _json.loads(entry["details"])
        self.assertIn("changed_fields", details)
        self.assertIn("billing_email", details["changed_fields"])
        self.assertIn("billing_phone", details["changed_fields"])

    def test_audit_indicates_cleared_fields(self):
        update_billing_party(self.conn, self.bp["billing_party_id"], {
            "billing_email": "",
        })
        entry = self._latest_audit()
        import json as _json
        details = _json.loads(entry["details"])
        self.assertIn("billing_email", details["changed_fields"])

    def test_audit_indicates_active_status_change(self):
        update_billing_party(self.conn, self.bp["billing_party_id"], {"active": False})
        entry = self._latest_audit()
        import json as _json
        details = _json.loads(entry["details"])
        self.assertIn("active", details["changed_fields"])
        self.assertEqual(details["active_changed_to"], 0)

    def test_audit_does_not_expose_field_values(self):
        update_billing_party(self.conn, self.bp["billing_party_id"], {
            "billing_email": "secret@example.test",
            "billing_phone": "555-SECRET",
        })
        entry = self._latest_audit()
        import json as _json
        details = _json.loads(entry["details"])
        self.assertNotIn("secret@example.test", _json.dumps(details))
        self.assertNotIn("555-SECRET", _json.dumps(details))

    def test_audit_omitted_fields_not_listed_as_changed(self):
        update_billing_party(self.conn, self.bp["billing_party_id"], {
            "billing_email": "new@example.test",
        })
        entry = self._latest_audit()
        import json as _json
        details = _json.loads(entry["details"])
        self.assertNotIn("billing_phone", details["changed_fields"])
        self.assertNotIn("billing_name", details["changed_fields"])

    def test_audit_no_unchanged_fields_listed_as_changed(self):
        update_billing_party(self.conn, self.bp["billing_party_id"], {
            "billing_email": "robin@example.test",
        })
        entry = self._latest_audit()
        import json as _json
        details = _json.loads(entry["details"])
        self.assertNotIn("billing_email", details["changed_fields"])


class BillingSetupHistoryPreservationTests(unittest.TestCase):
    """Tests that clearing or editing contact fields does not change historical records."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "hist.sqlite3")
        init_db(self.conn)
        save_business_profile(self.conn, {
            "business_name": "Test Practice", "provider_display_name": "Test Provider",
            "address_line_1": "100 Test Ave", "city": "Test", "state": "FL", "postal_code": "00000",
            "phone": "555-0100", "email": "test@example.test", "payee_name": "Test Payee",
            "payment_address_line_1": "100 Test Ave", "payment_city": "Test", "payment_state": "FL",
            "payment_postal_code": "00000", "zelle_recipient": "test-zelle@example.test",
        })
        self.conn.commit()
        import_rows(self.conn, [raw_row("snap-hist")], "test")
        self.candidate_id = list_review_candidates(self.conn)["items"][0]["candidate_id"]
        self.person = create_person(self.conn, {"display_name": "Robin Rivers", "first_name": "Robin", "last_name": "Rivers"})
        self.payer = create_billing_party(self.conn, {
            "billing_name": "Robin Rivers",
            "billing_party_type": "person",
            "person_id": self.person["person_id"],
            "billing_email": "robin@example.test",
            "billing_phone": "555-1234",
            "billing_address_line_1": "123 Main St",
            "billing_city": "Anytown",
            "billing_state": "CA",
            "billing_postal_code": "90210",
            "preferred_delivery_method": "email",
        })

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_clearing_fields_does_not_change_session_billing_party_id(self):
        approve_candidate(self.conn, self.candidate_id, {
            "participants": [
                {"person_id": self.person["person_id"], "display_name": "Robin Rivers", "is_primary": True},
            ],
            "billing_party_id": self.payer["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "unpaid",
        })
        session = self.conn.execute("SELECT billing_party_id, approved_rate_cents FROM sessions WHERE candidate_id = ?", (self.candidate_id,)).fetchone()
        original_bp = session["billing_party_id"]
        original_rate = session["approved_rate_cents"]

        update_billing_party(self.conn, self.payer["billing_party_id"], {
            "billing_email": "",
            "billing_phone": "",
            "billing_address_line_1": "",
        })

        session_after = self.conn.execute("SELECT billing_party_id, approved_rate_cents FROM sessions WHERE candidate_id = ?", (self.candidate_id,)).fetchone()
        self.assertEqual(session_after["billing_party_id"], original_bp)
        self.assertEqual(session_after["approved_rate_cents"], original_rate)

    def test_clearing_fields_does_not_change_finalized_invoice(self):
        approve_candidate(self.conn, self.candidate_id, {
            "participants": [
                {"person_id": self.person["person_id"], "display_name": "Robin Rivers", "is_primary": True},
            ],
            "billing_party_id": self.payer["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "unpaid",
        })
        session = self.conn.execute("SELECT id FROM sessions WHERE candidate_id = ?", (self.candidate_id,)).fetchone()
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.payer["billing_party_id"],
            "billing_period_start": "2026-06-01",
            "billing_period_end": "2026-06-30",
            "invoice_date": "2026-06-30",
            "session_ids": [session["id"]],
        })
        with patch("jordana_invoice.invoice_services.generate_invoice_pdf", return_value="a" * 64):
            finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "pdf")

        inv_before = self.conn.execute(
            "SELECT bill_to_party_id, bill_to_name_snapshot, bill_to_email_snapshot, total_cents FROM invoices WHERE invoice_id = ?",
            (draft["invoice"]["invoice_id"],),
        ).fetchone()

        update_billing_party(self.conn, self.payer["billing_party_id"], {
            "billing_name": "Robin Rivers Updated",
            "billing_email": "",
            "billing_phone": "",
            "billing_address_line_1": "",
        })

        inv_after = self.conn.execute(
            "SELECT bill_to_party_id, bill_to_name_snapshot, bill_to_email_snapshot, total_cents FROM invoices WHERE invoice_id = ?",
            (draft["invoice"]["invoice_id"],),
        ).fetchone()

        self.assertEqual(inv_after["bill_to_party_id"], inv_before["bill_to_party_id"])
        self.assertEqual(inv_after["bill_to_name_snapshot"], inv_before["bill_to_name_snapshot"])
        self.assertEqual(inv_after["bill_to_email_snapshot"], inv_before["bill_to_email_snapshot"])
        self.assertEqual(inv_after["total_cents"], inv_before["total_cents"])


class CopyContactDetailsTests(unittest.TestCase):
    """Tests for copying contact details from inactive to active billing setup."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "copy.sqlite3")
        init_db(self.conn)
        save_business_profile(self.conn, {
            "business_name": "Test Practice", "provider_display_name": "Test Provider",
            "address_line_1": "100 Test Ave", "city": "Test", "state": "FL", "postal_code": "00000",
            "phone": "555-0100", "email": "test@example.test", "payee_name": "Test Payee",
            "payment_address_line_1": "100 Test Ave", "payment_city": "Test", "payment_state": "FL",
            "payment_postal_code": "00000", "zelle_recipient": "test-zelle@example.test",
        })
        self.conn.commit()
        self.person = create_person(self.conn, {"display_name": "Fred Colin", "first_name": "Fred", "last_name": "Colin"})
        self.active_bp = create_billing_party(self.conn, {
            "billing_name": "Fred Colin",
            "billing_party_type": "person",
            "person_id": self.person["person_id"],
            "preferred_delivery_method": "email",
        })
        self.inactive_bp = create_billing_party(self.conn, {
            "billing_name": "Fred Colin",
            "billing_party_type": "person",
            "person_id": self.person["person_id"],
            "billing_email": "fred@example.test",
            "billing_address_line_1": "123 Main St",
            "billing_city": "Anytown",
            "billing_state": "CA",
            "billing_postal_code": "90210",
            "preferred_delivery_method": "email",
        })
        update_billing_party(self.conn, self.inactive_bp["billing_party_id"], {"active": False})

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_preview_shows_fields_to_copy(self):
        preview = preview_copy_contact_details(self.conn, self.active_bp["billing_party_id"], self.inactive_bp["billing_party_id"])
        fields = {f["field"] for f in preview["fields_to_copy"]}
        self.assertIn("billing_email", fields)
        self.assertIn("billing_address_line_1", fields)
        self.assertIn("billing_city", fields)
        self.assertIn("billing_state", fields)
        self.assertIn("billing_postal_code", fields)

    def test_preview_does_not_modify_data(self):
        before = self.conn.execute("SELECT billing_email FROM billing_parties WHERE billing_party_id = ?", (self.active_bp["billing_party_id"],)).fetchone()
        preview_copy_contact_details(self.conn, self.active_bp["billing_party_id"], self.inactive_bp["billing_party_id"])
        after = self.conn.execute("SELECT billing_email FROM billing_parties WHERE billing_party_id = ?", (self.active_bp["billing_party_id"],)).fetchone()
        self.assertEqual(after["billing_email"], before["billing_email"])

    def test_apply_copies_empty_fields_to_active(self):
        result = apply_copy_contact_details(self.conn, self.active_bp["billing_party_id"], self.inactive_bp["billing_party_id"])
        self.assertIn("billing_email", result["copied_fields"])
        row = self.conn.execute("SELECT billing_email, billing_address_line_1, billing_city FROM billing_parties WHERE billing_party_id = ?", (self.active_bp["billing_party_id"],)).fetchone()
        self.assertEqual(row["billing_email"], "fred@example.test")
        self.assertEqual(row["billing_address_line_1"], "123 Main St")
        self.assertEqual(row["billing_city"], "Anytown")

    def test_apply_does_not_reactivate_inactive(self):
        apply_copy_contact_details(self.conn, self.active_bp["billing_party_id"], self.inactive_bp["billing_party_id"])
        row = self.conn.execute("SELECT active FROM billing_parties WHERE billing_party_id = ?", (self.inactive_bp["billing_party_id"],)).fetchone()
        self.assertEqual(row["active"], 0)

    def test_apply_does_not_overwrite_existing_active_values(self):
        update_billing_party(self.conn, self.active_bp["billing_party_id"], {"billing_email": "existing@example.test"})
        result = apply_copy_contact_details(self.conn, self.active_bp["billing_party_id"], self.inactive_bp["billing_party_id"])
        self.assertNotIn("billing_email", result["copied_fields"])
        row = self.conn.execute("SELECT billing_email FROM billing_parties WHERE billing_party_id = ?", (self.active_bp["billing_party_id"],)).fetchone()
        self.assertEqual(row["billing_email"], "existing@example.test")

    def test_apply_with_confirmed_fields_only_copies_selected(self):
        result = apply_copy_contact_details(
            self.conn, self.active_bp["billing_party_id"], self.inactive_bp["billing_party_id"],
            confirmed_fields=["billing_email"],
        )
        self.assertEqual(result["copied_fields"], ["billing_email"])
        row = self.conn.execute("SELECT billing_email, billing_address_line_1 FROM billing_parties WHERE billing_party_id = ?", (self.active_bp["billing_party_id"],)).fetchone()
        self.assertEqual(row["billing_email"], "fred@example.test")
        self.assertIsNone(row["billing_address_line_1"])

    def test_apply_records_audit(self):
        before = count(self.conn, "audit_log")
        apply_copy_contact_details(self.conn, self.active_bp["billing_party_id"], self.inactive_bp["billing_party_id"])
        after = count(self.conn, "audit_log")
        self.assertEqual(after, before + 2)
        entry = self.conn.execute(
            "SELECT * FROM audit_log WHERE entity_id = ? AND action = 'copied_contact_from_inactive' ORDER BY created_at DESC LIMIT 1",
            (self.active_bp["billing_party_id"],),
        ).fetchone()
        self.assertEqual(entry["entity_type"], "billing_party")

    def test_preview_rejects_active_source(self):
        with self.assertRaises(ValueError):
            preview_copy_contact_details(self.conn, self.active_bp["billing_party_id"], self.active_bp["billing_party_id"])

    def test_preview_rejects_inactive_target(self):
        with self.assertRaises(ValueError):
            preview_copy_contact_details(self.conn, self.inactive_bp["billing_party_id"], self.inactive_bp["billing_party_id"])

    def test_preview_rejects_different_persons(self):
        person2 = create_person(self.conn, {"display_name": "Other Person"})
        bp_other = create_billing_party(self.conn, {
            "billing_name": "Other", "person_id": person2["person_id"],
            "billing_email": "other@example.test",
        })
        update_billing_party(self.conn, bp_other["billing_party_id"], {"active": False})
        with self.assertRaises(ValueError):
            preview_copy_contact_details(self.conn, self.active_bp["billing_party_id"], bp_other["billing_party_id"])

    def test_apply_does_not_change_finalized_invoice_snapshots(self):
        import_rows(self.conn, [raw_row("snap-copy")], "test")
        candidate_id = list_review_candidates(self.conn)["items"][0]["candidate_id"]
        approve_candidate(self.conn, candidate_id, {
            "participants": [
                {"person_id": self.person["person_id"], "display_name": "Fred Colin", "is_primary": True},
            ],
            "billing_party_id": self.active_bp["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "200.00",
            "payment_status": "unpaid",
        })
        session = self.conn.execute("SELECT id FROM sessions WHERE candidate_id = ?", (candidate_id,)).fetchone()
        update_billing_party(self.conn, self.active_bp["billing_party_id"], {"billing_email": "temp@example.test"})
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.active_bp["billing_party_id"],
            "billing_period_start": "2026-06-01",
            "billing_period_end": "2026-06-30",
            "invoice_date": "2026-06-30",
            "session_ids": [session["id"]],
        })
        with patch("jordana_invoice.invoice_services.generate_invoice_pdf", return_value="a" * 64):
            finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "pdf")
        inv_before = self.conn.execute(
            "SELECT bill_to_email_snapshot, bill_to_name_snapshot FROM invoices WHERE invoice_id = ?",
            (draft["invoice"]["invoice_id"],),
        ).fetchone()
        apply_copy_contact_details(self.conn, self.active_bp["billing_party_id"], self.inactive_bp["billing_party_id"])
        inv_after = self.conn.execute(
            "SELECT bill_to_email_snapshot, bill_to_name_snapshot FROM invoices WHERE invoice_id = ?",
            (draft["invoice"]["invoice_id"],),
        ).fetchone()
        self.assertEqual(inv_after["bill_to_email_snapshot"], inv_before["bill_to_email_snapshot"])
        self.assertEqual(inv_after["bill_to_name_snapshot"], inv_before["bill_to_name_snapshot"])

    def test_apply_with_no_copyable_fields_returns_empty(self):
        update_billing_party(self.conn, self.active_bp["billing_party_id"], {
            "billing_email": "already@set.test",
            "billing_address_line_1": "456 Oak Ave",
            "billing_city": "Newtown",
            "billing_state": "NY",
            "billing_postal_code": "10001",
        })
        result = apply_copy_contact_details(self.conn, self.active_bp["billing_party_id"], self.inactive_bp["billing_party_id"])
        self.assertEqual(result["copied_fields"], [])


if __name__ == "__main__":
    unittest.main()
