import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import (
    add_sessions_to_draft,
    create_invoice_draft,
    finalize_invoice,
    save_business_profile,
    void_invoice,
)
from jordana_invoice.review_server import make_handler
from jordana_invoice.review_services import (
    approve_candidate,
    create_account,
    add_account_member,
    create_billing_party,
    create_person,
    get_account_record,
    get_organization_billing_record,
    get_person_record,
    list_billing_relationship_records,
    list_review_candidates,
)


def raw_row(snapshot_key, title, start):
    return {
        "ingested_at": "2026-06-22T02:00:00.000Z",
        "snapshot_key": snapshot_key,
        "run_id": "run-org",
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


class OrganizationBillingRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "org.sqlite3")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _import_and_approve(self, snapshot_key, title, participant_ids, billing_party_id, start="2026-06-17T18:00:00-04:00", rate="200.00"):
        row = raw_row(snapshot_key, title=title, start=start)
        import_rows(self.conn, [row], "test")
        candidate_id = next(
            r["candidate_id"]
            for r in list_review_candidates(self.conn)["items"]
            if r["raw_title"] == title
        )
        approve_candidate(self.conn, candidate_id, {
            "participants": [
                {"person_id": pid, "display_name": pid, "is_primary": idx == 0}
                for idx, pid in enumerate(participant_ids)
            ],
            "billing_party_id": billing_party_id,
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": rate,
            "payment_status": "unpaid",
        })
        return candidate_id

    def _make_org(self, **kwargs):
        defaults = {
            "billing_name": "Cedar Family Trust",
            "billing_party_type": "organization",
            "organization_name": "Cedar Family Trust",
            "billing_email": "billing@cedartrust.example",
            "billing_phone": "555-0200",
            "billing_address_line_1": "100 Cedar Lane",
            "billing_city": "Cedarville",
            "billing_state": "FL",
            "billing_postal_code": "00001",
            "preferred_delivery_method": "email",
            "administrative_notes": "Pay net 30",
        }
        defaults.update(kwargs)
        return create_billing_party(self.conn, defaults)

    def _save_profile(self):
        save_business_profile(self.conn, {
            "business_name": "Demo Practice",
            "provider_display_name": "Demo Provider",
            "address_line_1": "100 Example Ave",
            "city": "Example",
            "state": "FL",
            "postal_code": "00000",
            "phone": "555-0100",
            "email": "billing@example.test",
            "payee_name": "Demo Payee",
            "invoice_total_label": "TOTAL DUE",
            "invoice_number_format": "YYYY-NNNN",
        })

    # ---- Organization details ----

    def test_complete_organization_fields_are_returned(self):
        org = self._make_org()
        rec = get_organization_billing_record(self.conn, org["billing_party_id"])
        bp = rec["billing_party"]
        self.assertEqual(bp["billing_party_id"], org["billing_party_id"])
        self.assertEqual(bp["billing_party_type"], "organization")
        self.assertEqual(bp["organization_name"], "Cedar Family Trust")
        self.assertEqual(bp["billing_name"], "Cedar Family Trust")
        self.assertEqual(bp["billing_email"], "billing@cedartrust.example")
        self.assertEqual(bp["billing_phone"], "555-0200")
        self.assertEqual(bp["billing_address_line_1"], "100 Cedar Lane")
        self.assertEqual(bp["billing_city"], "Cedarville")
        self.assertEqual(bp["billing_state"], "FL")
        self.assertEqual(bp["billing_postal_code"], "00001")
        self.assertEqual(bp["preferred_delivery_method"], "email")
        self.assertEqual(bp["administrative_notes"], "Pay net 30")
        self.assertTrue(bp["active"])
        self.assertIsNotNone(bp["created_at"])
        self.assertIsNotNone(bp["updated_at"])

    def test_missing_id_raises_clear_error(self):
        with self.assertRaises(ValueError) as ctx:
            get_organization_billing_record(self.conn, "nonexistent-id")
        self.assertIn("not found", str(ctx.exception).lower())

    def test_person_billing_party_is_rejected(self):
        person = create_person(self.conn, {"display_name": "Taylor Reed", "first_name": "Taylor", "last_name": "Reed"})
        person_bp = create_billing_party(self.conn, {
            "billing_name": "Taylor Reed",
            "billing_party_type": "person",
            "person_id": person["person_id"],
        })
        with self.assertRaises(ValueError) as ctx:
            get_organization_billing_record(self.conn, person_bp["billing_party_id"])
        self.assertIn("client endpoint", str(ctx.exception).lower())

    # ---- Covered clients ----

    def test_covered_clients_are_distinct(self):
        taylor = create_person(self.conn, {"display_name": "Taylor Reed", "first_name": "Taylor", "last_name": "Reed"})
        org = self._make_org()
        self._import_and_approve("snap-1", "Taylor Reed 6", [taylor["person_id"]], org["billing_party_id"])
        self._import_and_approve("snap-2", "Taylor Reed 6", [taylor["person_id"]], org["billing_party_id"], start="2026-06-18T18:00:00-04:00")
        rec = get_organization_billing_record(self.conn, org["billing_party_id"])
        clients = rec["covered_clients"]
        self.assertEqual(len(clients), 1)
        self.assertEqual(clients[0]["person_id"], taylor["person_id"])
        self.assertEqual(clients[0]["session_count"], 2)

    def test_per_client_session_counts_are_correct(self):
        taylor = create_person(self.conn, {"display_name": "Taylor Reed", "first_name": "Taylor", "last_name": "Reed"})
        morgan = create_person(self.conn, {"display_name": "Morgan Lee", "first_name": "Morgan", "last_name": "Lee"})
        org = self._make_org()
        self._import_and_approve("snap-t1", "Taylor Reed 6", [taylor["person_id"]], org["billing_party_id"])
        self._import_and_approve("snap-t2", "Taylor Reed 6", [taylor["person_id"]], org["billing_party_id"], start="2026-06-18T18:00:00-04:00")
        self._import_and_approve("snap-m1", "Morgan Lee 6", [morgan["person_id"]], org["billing_party_id"], start="2026-06-19T18:00:00-04:00")
        rec = get_organization_billing_record(self.conn, org["billing_party_id"])
        clients = {c["person_id"]: c for c in rec["covered_clients"]}
        self.assertEqual(clients[taylor["person_id"]]["session_count"], 2)
        self.assertEqual(clients[morgan["person_id"]]["session_count"], 1)

    def test_one_organization_covering_multiple_clients_returns_all(self):
        taylor = create_person(self.conn, {"display_name": "Taylor Reed", "first_name": "Taylor", "last_name": "Reed"})
        morgan = create_person(self.conn, {"display_name": "Morgan Lee", "first_name": "Morgan", "last_name": "Lee"})
        avery = create_person(self.conn, {"display_name": "Avery Stone", "first_name": "Avery", "last_name": "Stone"})
        org = self._make_org()
        self._import_and_approve("snap-t", "Taylor Reed 6", [taylor["person_id"]], org["billing_party_id"])
        self._import_and_approve("snap-m", "Morgan Lee 6", [morgan["person_id"]], org["billing_party_id"], start="2026-06-18T18:00:00-04:00")
        self._import_and_approve("snap-a", "Avery Stone 6", [avery["person_id"]], org["billing_party_id"], start="2026-06-19T18:00:00-04:00")
        rec = get_organization_billing_record(self.conn, org["billing_party_id"])
        names = [c["display_name"] for c in rec["covered_clients"]]
        self.assertEqual(names, sorted(names))
        self.assertEqual(len(rec["covered_clients"]), 3)

    # ---- Sessions ----

    def test_sessions_include_participants_and_stored_historical_rates(self):
        taylor = create_person(self.conn, {"display_name": "Taylor Reed", "first_name": "Taylor", "last_name": "Reed"})
        org = self._make_org()
        self._import_and_approve("snap-r", "Taylor Reed 6", [taylor["person_id"]], org["billing_party_id"], rate="350.00")
        rec = get_organization_billing_record(self.conn, org["billing_party_id"])
        sessions = rec["sessions"]
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s["approved_rate_cents"], 35000)
        self.assertIn("Taylor Reed", s["participant_names"])
        self.assertEqual(s["review_status"], "approved")

    def test_sessions_are_newest_first(self):
        taylor = create_person(self.conn, {"display_name": "Taylor Reed", "first_name": "Taylor", "last_name": "Reed"})
        org = self._make_org()
        self._import_and_approve("snap-old", "Taylor Reed 6", [taylor["person_id"]], org["billing_party_id"], start="2026-06-10T18:00:00-04:00")
        self._import_and_approve("snap-new", "Taylor Reed 6", [taylor["person_id"]], org["billing_party_id"], start="2026-06-20T18:00:00-04:00")
        rec = get_organization_billing_record(self.conn, org["billing_party_id"])
        dates = [s["session_date"] for s in rec["sessions"]]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_session_invoice_linkage_when_safely_joinable(self):
        taylor = create_person(self.conn, {"display_name": "Taylor Reed", "first_name": "Taylor", "last_name": "Reed"})
        org = self._make_org()
        self._import_and_approve("snap-inv", "Taylor Reed 6", [taylor["person_id"]], org["billing_party_id"])
        self._save_profile()
        session = self.conn.execute("SELECT id FROM sessions WHERE billing_party_id = ?", (org["billing_party_id"],)).fetchone()
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": org["billing_party_id"],
            "billing_period_start": "2026-06-01",
            "billing_period_end": "2026-06-30",
            "invoice_date": "2026-06-30",
            "session_ids": [session["id"]],
        })
        rec = get_organization_billing_record(self.conn, org["billing_party_id"])
        s = rec["sessions"][0]
        self.assertEqual(s["invoice_id"], draft["invoice"]["invoice_id"])
        self.assertIsNone(s["invoice_number"])

    # ---- Invoices ----

    def test_invoices_addressed_to_organization_are_returned(self):
        taylor = create_person(self.conn, {"display_name": "Taylor Reed", "first_name": "Taylor", "last_name": "Reed"})
        org = self._make_org()
        self._import_and_approve("snap-i1", "Taylor Reed 6", [taylor["person_id"]], org["billing_party_id"])
        self._save_profile()
        session = self.conn.execute("SELECT id FROM sessions WHERE billing_party_id = ?", (org["billing_party_id"],)).fetchone()
        with patch("jordana_invoice.invoice_services.generate_invoice_pdf", return_value="a" * 64):
            draft = create_invoice_draft(self.conn, {
                "bill_to_party_id": org["billing_party_id"],
                "billing_period_start": "2026-06-01",
                "billing_period_end": "2026-06-30",
                "invoice_date": "2026-06-30",
                "session_ids": [session["id"]],
            })
            finalized = finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "pdf")
        rec = get_organization_billing_record(self.conn, org["billing_party_id"])
        invoices = rec["invoices"]
        self.assertEqual(len(invoices), 1)
        inv = invoices[0]
        self.assertEqual(inv["invoice_id"], finalized["invoice"]["invoice_id"])
        self.assertEqual(inv["invoice_number"], finalized["invoice"]["invoice_number"])
        self.assertEqual(inv["status"], "finalized")
        self.assertEqual(inv["balance_cents"], inv["total_cents"])
        self.assertIsNotNone(inv["finalized_at"])

    def test_void_invoices_have_zero_balance(self):
        taylor = create_person(self.conn, {"display_name": "Taylor Reed", "first_name": "Taylor", "last_name": "Reed"})
        org = self._make_org()
        self._import_and_approve("snap-v1", "Taylor Reed 6", [taylor["person_id"]], org["billing_party_id"])
        self._save_profile()
        session = self.conn.execute("SELECT id FROM sessions WHERE billing_party_id = ?", (org["billing_party_id"],)).fetchone()
        with patch("jordana_invoice.invoice_services.generate_invoice_pdf", return_value="b" * 64):
            draft = create_invoice_draft(self.conn, {
                "bill_to_party_id": org["billing_party_id"],
                "billing_period_start": "2026-06-01",
                "billing_period_end": "2026-06-30",
                "invoice_date": "2026-06-30",
                "session_ids": [session["id"]],
            })
            finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "pdf")
            void_invoice(self.conn, draft["invoice"]["invoice_id"], "Incorrect billing period")
        rec = get_organization_billing_record(self.conn, org["billing_party_id"])
        inv = rec["invoices"][0]
        self.assertEqual(inv["status"], "void")
        self.assertEqual(inv["balance_cents"], 0)

    def test_invoice_totals_exclude_void_amounts_in_summary(self):
        taylor = create_person(self.conn, {"display_name": "Taylor Reed", "first_name": "Taylor", "last_name": "Reed"})
        org = self._make_org()
        self._import_and_approve("snap-s1", "Taylor Reed 6", [taylor["person_id"]], org["billing_party_id"])
        self._import_and_approve("snap-s2", "Taylor Reed 6", [taylor["person_id"]], org["billing_party_id"], start="2026-06-18T18:00:00-04:00")
        self._save_profile()
        sessions = self.conn.execute("SELECT id FROM sessions WHERE billing_party_id = ? ORDER BY session_date", (org["billing_party_id"],)).fetchall()
        with patch("jordana_invoice.invoice_services.generate_invoice_pdf", return_value="c" * 64):
            draft1 = create_invoice_draft(self.conn, {
                "bill_to_party_id": org["billing_party_id"],
                "billing_period_start": "2026-06-01",
                "billing_period_end": "2026-06-30",
                "invoice_date": "2026-06-30",
                "session_ids": [sessions[0]["id"]],
            })
            finalize_invoice(self.conn, draft1["invoice"]["invoice_id"], pdf_root=self.root / "pdf")
            draft2 = create_invoice_draft(self.conn, {
                "bill_to_party_id": org["billing_party_id"],
                "billing_period_start": "2026-06-01",
                "billing_period_end": "2026-06-30",
                "invoice_date": "2026-06-30",
                "session_ids": [sessions[1]["id"]],
            })
            finalize_invoice(self.conn, draft2["invoice"]["invoice_id"], pdf_root=self.root / "pdf")
            void_invoice(self.conn, draft2["invoice"]["invoice_id"], "Duplicate")
        rec = get_organization_billing_record(self.conn, org["billing_party_id"])
        summary = rec["billing_summary"]
        self.assertEqual(summary["invoice_count"], 2)
        voided_total = next(inv["total_cents"] for inv in rec["invoices"] if inv["status"] == "void")
        non_void_total = next(inv["total_cents"] for inv in rec["invoices"] if inv["status"] != "void")
        self.assertEqual(summary["total_invoiced_cents"], non_void_total)
        self.assertNotEqual(summary["total_invoiced_cents"], non_void_total + voided_total)

    # ---- Billing summary ----

    def test_approved_uninvoiced_count_excludes_sessions_on_non_void_invoice(self):
        taylor = create_person(self.conn, {"display_name": "Taylor Reed", "first_name": "Taylor", "last_name": "Reed"})
        org = self._make_org()
        self._import_and_approve("snap-u1", "Taylor Reed 6", [taylor["person_id"]], org["billing_party_id"])
        self._import_and_approve("snap-u2", "Taylor Reed 6", [taylor["person_id"]], org["billing_party_id"], start="2026-06-18T18:00:00-04:00")
        self._save_profile()
        sessions = self.conn.execute("SELECT id FROM sessions WHERE billing_party_id = ? ORDER BY session_date", (org["billing_party_id"],)).fetchall()
        with patch("jordana_invoice.invoice_services.generate_invoice_pdf", return_value="d" * 64):
            draft = create_invoice_draft(self.conn, {
                "bill_to_party_id": org["billing_party_id"],
                "billing_period_start": "2026-06-01",
                "billing_period_end": "2026-06-30",
                "invoice_date": "2026-06-30",
                "session_ids": [sessions[0]["id"]],
            })
            finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "pdf")
        rec = get_organization_billing_record(self.conn, org["billing_party_id"])
        self.assertEqual(rec["billing_summary"]["approved_uninvoiced_sessions"], 1)
        self.assertEqual(rec["billing_summary"]["total_sessions"], 2)

    # ---- Linked account information ----

    def test_linked_genuine_account_information_is_returned(self):
        org = self._make_org()
        account = create_account(self.conn, "Cedar Household", "household")
        self.conn.execute(
            "UPDATE client_accounts SET default_billing_party_id = ? WHERE account_id = ?",
            (org["billing_party_id"], account["account_id"]),
        )
        self.conn.commit()
        rec = get_organization_billing_record(self.conn, org["billing_party_id"])
        linked = rec["linked_accounts"]
        self.assertEqual(len(linked), 1)
        self.assertEqual(linked[0]["account_id"], account["account_id"])
        self.assertEqual(linked[0]["account_name"], "Cedar Household")
        self.assertEqual(linked[0]["account_type"], "household")
        self.assertTrue(linked[0]["active"])
        self.assertEqual(linked[0]["members"], [])

    def test_no_account_membership_inferred_from_session_evidence(self):
        taylor = create_person(self.conn, {"display_name": "Taylor Reed", "first_name": "Taylor", "last_name": "Reed"})
        org = self._make_org()
        self._import_and_approve("snap-no-acct", "Taylor Reed 6", [taylor["person_id"]], org["billing_party_id"])
        rec = get_organization_billing_record(self.conn, org["billing_party_id"])
        self.assertEqual(rec["linked_accounts"], [])
        member_count = self.conn.execute("SELECT COUNT(*) FROM account_members").fetchone()[0]
        self.assertEqual(member_count, 0)

    # ---- Audit history ----

    def test_audit_history_is_returned(self):
        org = self._make_org()
        rec = get_organization_billing_record(self.conn, org["billing_party_id"])
        audit = rec["audit"]
        self.assertGreaterEqual(len(audit), 1)
        self.assertEqual(audit[0]["entity_type"], "billing_party")
        self.assertEqual(audit[0]["entity_id"], org["billing_party_id"])

    # ---- Empty collections ----

    def test_empty_related_collections_return_empty_arrays(self):
        org = self._make_org()
        rec = get_organization_billing_record(self.conn, org["billing_party_id"])
        self.assertEqual(rec["covered_clients"], [])
        self.assertEqual(rec["sessions"], [])
        self.assertEqual(rec["invoices"], [])
        self.assertEqual(rec["linked_accounts"], [])
        self.assertEqual(len(rec["audit"]), 1)
        self.assertEqual(rec["audit"][0]["action"], "created_inline")

    # ---- No writes ----

    def test_service_creates_or_modifies_no_rows(self):
        taylor = create_person(self.conn, {"display_name": "Taylor Reed", "first_name": "Taylor", "last_name": "Reed"})
        org = self._make_org()
        self._import_and_approve("snap-nw", "Taylor Reed 6", [taylor["person_id"]], org["billing_party_id"])
        before = {
            t: count(self.conn, t)
            for t in ("billing_parties", "sessions", "session_participants", "invoices",
                      "invoice_line_items", "client_accounts", "account_members", "audit_log", "people")
        }
        get_organization_billing_record(self.conn, org["billing_party_id"])
        after = {
            t: count(self.conn, t)
            for t in ("billing_parties", "sessions", "session_participants", "invoices",
                      "invoice_line_items", "client_accounts", "account_members", "audit_log", "people")
        }
        self.assertEqual(before, after)

    # ---- Existing endpoints unchanged ----

    def test_billing_relationships_directory_remains_unchanged(self):
        taylor = create_person(self.conn, {"display_name": "Taylor Reed", "first_name": "Taylor", "last_name": "Reed"})
        org = self._make_org()
        self._import_and_approve("snap-dir", "Taylor Reed 6", [taylor["person_id"]], org["billing_party_id"])
        records = list_billing_relationship_records(self.conn)
        org_rec = next(r for r in records if r["billing_party_id"] == org["billing_party_id"])
        self.assertEqual(org_rec["record_type"], "organization")
        self.assertEqual(org_rec["billing_party_type"], "organization")

    def test_person_endpoint_remains_unchanged(self):
        person = create_person(self.conn, {"display_name": "Taylor Reed", "first_name": "Taylor", "last_name": "Reed"})
        person_bp = create_billing_party(self.conn, {
            "billing_name": "Taylor Reed",
            "billing_party_type": "person",
            "person_id": person["person_id"],
        })
        rec = get_person_record(self.conn, person["person_id"])
        self.assertEqual(rec["person"]["person_id"], person["person_id"])
        self.assertEqual(len(rec["billing_parties"]), 1)
        self.assertEqual(rec["billing_parties"][0]["billing_party_id"], person_bp["billing_party_id"])

    def test_account_endpoint_remains_unchanged(self):
        account = create_account(self.conn, "Test Household", "household")
        rec = get_account_record(self.conn, account["account_id"])
        self.assertEqual(rec["account"]["account_id"], account["account_id"])
        self.assertEqual(rec["account"]["account_name"], "Test Household")


class OrganizationBillingEndpointTests(unittest.TestCase):
    """HTTP-level tests for GET /api/billing-parties/{billing_party_id}."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "ep.sqlite3")
        self.handler_cls = make_handler(self.db_path)
        self.conn = connect(Path(self.temp.name) / "ep.sqlite3")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _handler(self, path):
        handler = object.__new__(self.handler_cls)
        handler.path = path
        handler.headers = {}
        handler.rfile = io.BytesIO(b"")
        handler.wfile = io.BytesIO()
        handler.send_error = lambda code: (_ for _ in ()).throw(AssertionError(f"unexpected error {code}"))
        captured = {}
        handler.send_json = lambda payload, status=200: captured.update({"payload": payload, "status": status})
        handler.finish = lambda: None
        return handler, captured

    def _make_org(self, **kwargs):
        defaults = {
            "billing_name": "Cedar Family Trust",
            "billing_party_type": "organization",
            "organization_name": "Cedar Family Trust",
            "billing_email": "billing@cedartrust.example",
            "billing_phone": "555-0200",
            "billing_address_line_1": "100 Cedar Lane",
            "billing_city": "Cedarville",
            "billing_state": "FL",
            "billing_postal_code": "00001",
            "preferred_delivery_method": "email",
            "administrative_notes": "Pay net 30",
        }
        defaults.update(kwargs)
        return create_billing_party(self.conn, defaults)

    def _import_and_approve(self, snapshot_key, title, participant_ids, billing_party_id, start="2026-06-17T18:00:00-04:00", rate="200.00"):
        row = raw_row(snapshot_key, title=title, start=start)
        import_rows(self.conn, [row], "test")
        candidate_id = next(
            r["candidate_id"]
            for r in list_review_candidates(self.conn)["items"]
            if r["raw_title"] == title
        )
        approve_candidate(self.conn, candidate_id, {
            "participants": [
                {"person_id": pid, "display_name": pid, "is_primary": idx == 0}
                for idx, pid in enumerate(participant_ids)
            ],
            "billing_party_id": billing_party_id,
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": rate,
            "payment_status": "unpaid",
        })
        return candidate_id

    def test_valid_organization_returns_200(self):
        org = self._make_org()
        handler, captured = self._handler(f"/api/billing-parties/{org['billing_party_id']}")
        handler.conn = lambda: self.conn
        handler.do_GET()
        self.assertEqual(captured["status"], 200)

    def test_valid_response_contains_all_sections(self):
        taylor = create_person(self.conn, {"display_name": "Taylor Reed", "first_name": "Taylor", "last_name": "Reed"})
        org = self._make_org()
        self._import_and_approve("snap-ep", "Taylor Reed 6", [taylor["person_id"]], org["billing_party_id"])
        handler, captured = self._handler(f"/api/billing-parties/{org['billing_party_id']}")
        handler.conn = lambda: self.conn
        handler.do_GET()
        payload = captured["payload"]
        self.assertIn("billing_party", payload)
        self.assertIn("covered_clients", payload)
        self.assertIn("sessions", payload)
        self.assertIn("invoices", payload)
        self.assertIn("billing_summary", payload)
        self.assertIn("linked_accounts", payload)
        self.assertIn("audit", payload)
        self.assertEqual(payload["billing_party"]["billing_party_id"], org["billing_party_id"])
        self.assertEqual(payload["billing_party"]["organization_name"], "Cedar Family Trust")
        self.assertEqual(len(payload["covered_clients"]), 1)
        self.assertEqual(len(payload["sessions"]), 1)

    def test_missing_billing_party_id_returns_404(self):
        handler, captured = self._handler("/api/billing-parties/nonexistent-id")
        handler.conn = lambda: self.conn
        handler.do_GET()
        self.assertEqual(captured["status"], 404)
        self.assertFalse(captured["payload"]["ok"])
        self.assertIn("not found", captured["payload"]["error"].lower())

    def test_person_billing_party_returns_400(self):
        person = create_person(self.conn, {"display_name": "Taylor Reed", "first_name": "Taylor", "last_name": "Reed"})
        person_bp = create_billing_party(self.conn, {
            "billing_name": "Taylor Reed",
            "billing_party_type": "person",
            "person_id": person["person_id"],
        })
        handler, captured = self._handler(f"/api/billing-parties/{person_bp['billing_party_id']}")
        handler.conn = lambda: self.conn
        handler.do_GET()
        self.assertEqual(captured["status"], 400)
        self.assertFalse(captured["payload"]["ok"])
        self.assertIn("client endpoint", captured["payload"]["error"].lower())

    def test_all_responses_use_valid_json(self):
        org = self._make_org()
        for path, expected_status in [
            (f"/api/billing-parties/{org['billing_party_id']}", 200),
            ("/api/billing-parties/nonexistent-id", 404),
        ]:
            handler, captured = self._handler(path)
            handler.conn = lambda: self.conn
            handler.do_GET()
            json.dumps(captured["payload"])
            self.assertEqual(captured["status"], expected_status)

    def test_error_payloads_contain_ok_false(self):
        handler, captured = self._handler("/api/billing-parties/nonexistent-id")
        handler.conn = lambda: self.conn
        handler.do_GET()
        self.assertIn("ok", captured["payload"])
        self.assertFalse(captured["payload"]["ok"])

    def test_unexpected_internal_exception_returns_500(self):
        org = self._make_org()
        handler, captured = self._handler(f"/api/billing-parties/{org['billing_party_id']}")
        handler.conn = lambda: self.conn
        with patch("jordana_invoice.review_server.get_organization_billing_record", side_effect=RuntimeError("unexpected")):
            handler.do_GET()
        self.assertEqual(captured["status"], 500)
        self.assertFalse(captured["payload"]["ok"])

    def test_get_requests_create_or_modify_no_rows(self):
        taylor = create_person(self.conn, {"display_name": "Taylor Reed", "first_name": "Taylor", "last_name": "Reed"})
        org = self._make_org()
        self._import_and_approve("snap-nw-ep", "Taylor Reed 6", [taylor["person_id"]], org["billing_party_id"])
        self.conn.commit()
        before = {
            t: count(self.conn, t)
            for t in ("billing_parties", "sessions", "session_participants", "invoices",
                      "invoice_line_items", "client_accounts", "account_members", "audit_log", "people")
        }
        for path in [
            f"/api/billing-parties/{org['billing_party_id']}",
            "/api/billing-parties/nonexistent-id",
        ]:
            handler, _ = self._handler(path)
            handler.conn = lambda: self.conn
            handler.do_GET()
        after = {
            t: count(self.conn, t)
            for t in ("billing_parties", "sessions", "session_participants", "invoices",
                      "invoice_line_items", "client_accounts", "account_members", "audit_log", "people")
        }
        self.assertEqual(before, after)
