"""Characterization and contract tests for write endpoints (Round 4A.1).

These tests lock down the current request/response behavior of write endpoints
without changing production behavior. They use temporary databases and sanitized
fixtures only.
"""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, migrate_database
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import save_business_profile, finalize_invoice
from jordana_invoice.review_server import make_handler
from jordana_invoice.review_services import (
    approve_candidate,
    create_billing_party,
    create_person,
)
from jordana_invoice.util import stable_hash


def raw_row(key, title, start):
    return {
        "ingested_at": "2026-08-20T12:00:00Z",
        "snapshot_key": key,
        "run_id": f"run-{key}",
        "batch_name": "contract-test",
        "capture_window": "past_7_days",
        "captured_at": "2026-08-20T12:00:00Z",
        "source_device": "test",
        "timezone": "America/New_York",
        "calendar_event_id": f"event-{key}",
        "event_fingerprint": f"fp-{key}",
        "event_title": title,
        "start_at": start,
        "end_at": start[:11] + "11:00:00-04:00",
        "duration_minutes": "60",
        "calendar": "Jordana Work",
        "payload_version": "2",
        "raw_json": "{}",
    }


class WriteEndpointContractTestBase(unittest.TestCase):
    """Base class with shared setup for DB-backed write endpoint contract tests."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = str(self.root / "server.sqlite3")
        migrate_database(self.db_path)
        self.conn = connect(self.db_path)
        self.person = create_person(self.conn, {
            "first_name": "Avery", "last_name": "Stone", "display_name": "Avery Stone",
        })
        self.party = create_billing_party(self.conn, {
            "billing_name": "Avery Stone",
            "person_id": self.person["person_id"],
            "billing_email": "avery@example.test",
            "billing_address_line_1": "10 Sample Street",
            "billing_city": "Example", "billing_state": "FL",
            "billing_postal_code": "00000",
            "preferred_delivery_method": "both",
        })
        save_business_profile(self.conn, {
            "business_name": "Demo Practice",
            "provider_display_name": "Demo Provider",
            "address_line_1": "100 Example Avenue",
            "city": "Example", "state": "FL", "postal_code": "00000",
            "phone": "555-0100", "email": "billing@example.test",
            "payee_name": "Demo Payee",
            "payment_address_line_1": "100 Example Avenue",
            "payment_city": "Example", "payment_state": "FL",
            "payment_postal_code": "00000",
            "zelle_recipient": "demo-zelle@example.test",
            "invoice_total_label": "TOTAL DUE",
            "invoice_number_format": "YYYY-NNNN",
        })
        self.handler_cls = make_handler(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _handler(self, path, body=b"{}"):
        handler = object.__new__(self.handler_cls)
        handler.path = path
        handler.headers = {
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
            self.handler_cls.write_token_header: self.handler_cls.write_token,
        }
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler._database_connection = self.conn
        captured = {}
        handler.send_json = lambda payload, status=200: captured.update({
            "payload": payload, "status": status,
        })
        handler.send_error = lambda code: captured.update({
            "payload": None, "status": code,
        })
        handler.finish = lambda: None
        handler.log_message = lambda *a: None
        return handler, captured

    def _post(self, path, body_dict):
        body = json.dumps(body_dict).encode("utf-8")
        handler, captured = self._handler(path, body)
        handler.do_POST()
        return handler, captured

    def _approved_session(self, key="one", day=15):
        import_rows(self.conn, [
            raw_row(key, "Avery Stone | 60 | Office",
                    f"2026-05-{day:02d}T10:00:00-04:00"),
        ], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": [{
                "person_id": self.person["person_id"],
                "display_name": "Avery Stone",
            }],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "150.00",
            "payment_status": "unpaid",
            "billing_treatment": "billable",
        })
        return detail["session"]["id"], candidate_id

    def _import_candidate(self, key, title, start):
        import_rows(self.conn, [raw_row(key, title, start)], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        return candidate_id


# ---------------------------------------------------------------------------
# 1. Session Review and Approval
# ---------------------------------------------------------------------------

class TestSaveInterpretationContract(WriteEndpointContractTestBase):
    def test_save_returns_200_with_candidate_detail(self):
        cid = self._import_candidate("si-1", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/save",
            {
                "participants": [{
                    "person_id": self.person["person_id"],
                    "display_name": "Avery Stone",
                }],
                "billing_party_id": self.party["billing_party_id"],
                "approved_duration_minutes": 60,
                "service_mode": "office",
                "time_category": "standard",
                "approved_rate": "150.00",
                "payment_status": "unpaid",
            },
        )
        self.assertEqual(captured["status"], 200)
        self.assertIn("session", captured["payload"])
        self.assertIn("participants", captured["payload"])

    def test_save_is_idempotent(self):
        cid = self._import_candidate("si-2", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        body = {
            "participants": [{
                "person_id": self.person["person_id"],
                "display_name": "Avery Stone",
            }],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "150.00",
            "payment_status": "unpaid",
        }
        _, c1 = self._post(f"/api/review/candidates/{cid}/save", body)
        _, c2 = self._post(f"/api/review/candidates/{cid}/save", body)
        self.assertEqual(c1["status"], 200)
        self.assertEqual(c2["status"], 200)
        self.assertEqual(
            c1["payload"]["session"]["id"],
            c2["payload"]["session"]["id"],
        )


class TestSaveRelationshipContract(WriteEndpointContractTestBase):
    def test_save_relationship_returns_200(self):
        cid = self._import_candidate("sr-1", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/save-relationship",
            {
                "participants": [{
                    "person_id": self.person["person_id"],
                    "display_name": "Avery Stone",
                    "is_primary": True,
                }],
            },
        )
        self.assertEqual(captured["status"], 200)
        self.assertIn("participants", captured["payload"])

    def test_save_relationship_creates_person_from_proposed(self):
        cid = self._import_candidate("sr-2", "Leah Grossman 630 30", "2026-05-15T10:00:00-04:00")
        detail_resp, _ = self._handler(f"/api/review/candidates/{cid}")
        # Get proposed participants via GET
        handler, captured = self._handler(f"/api/review/candidates/{cid}")
        handler.do_GET()
        participants = captured["payload"]["participants"]
        _, captured_save = self._post(
            f"/api/review/candidates/{cid}/save-relationship",
            {"participants": participants},
        )
        self.assertEqual(captured_save["status"], 200)
        saved_p = captured_save["payload"]["participants"]
        self.assertIsNotNone(saved_p[0]["person_id"])


class TestSaveBillingContract(WriteEndpointContractTestBase):
    def test_save_billing_returns_200(self):
        cid = self._import_candidate("sb-1", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/save-billing",
            {"billing_party_id": self.party["billing_party_id"]},
        )
        self.assertEqual(captured["status"], 200)
        self.assertIn("session", captured["payload"])


class TestSaveSessionDraftContract(WriteEndpointContractTestBase):
    def test_save_session_draft_returns_200(self):
        cid = self._import_candidate("ss-1", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/save-session",
            {
                "approved_duration_minutes": 60,
                "service_mode": "office",
                "approved_rate": "150.00",
                "payment_status": "unpaid",
            },
        )
        self.assertEqual(captured["status"], 200)
        self.assertIn("session", captured["payload"])


class TestApproveSessionContract(WriteEndpointContractTestBase):
    def test_approve_returns_200_with_session_and_staging(self):
        cid = self._import_candidate("ap-1", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/approve",
            {
                "participants": [{
                    "person_id": self.person["person_id"],
                    "display_name": "Avery Stone",
                }],
                "billing_party_id": self.party["billing_party_id"],
                "approved_duration_minutes": 60,
                "service_mode": "office",
                "time_category": "standard",
                "approved_rate": "150.00",
                "payment_status": "unpaid",
                "billing_treatment": "billable",
            },
        )
        self.assertEqual(captured["status"], 200)
        payload = captured["payload"]
        self.assertIn("session", payload)
        self.assertEqual(payload["session"]["review_status"], "approved")
        self.assertIn("invoice_staging", payload)
        self.assertIn(payload["invoice_staging"]["status"],
                      {"success", "warning", "not_required", "unavailable", "error"})

    def test_approve_missing_required_fields_returns_400(self):
        cid = self._import_candidate("ap-2", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/approve",
            {"participants": []},
        )
        self.assertEqual(captured["status"], 400)
        self.assertFalse(captured["payload"]["ok"])
        self.assertIn("error", captured["payload"])

    def test_approve_is_idempotent_for_re_approval(self):
        cid = self._import_candidate("ap-3", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        body = {
            "participants": [{
                "person_id": self.person["person_id"],
                "display_name": "Avery Stone",
            }],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "150.00",
            "payment_status": "unpaid",
            "billing_treatment": "billable",
        }
        _, c1 = self._post(f"/api/review/candidates/{cid}/approve", body)
        _, c2 = self._post(f"/api/review/candidates/{cid}/approve", body)
        self.assertEqual(c1["status"], 200)
        self.assertEqual(c2["status"], 200)
        self.assertEqual(c1["payload"]["session"]["id"], c2["payload"]["session"]["id"])

    def test_approve_unsafe_exception_sanitized(self):
        cid = self._import_candidate("ap-4", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        with patch("jordana_invoice.review_server.approve_candidate",
                    side_effect=RuntimeError("internal SQL detail /path/to/db")):
            _, captured = self._post(
                f"/api/review/candidates/{cid}/approve",
                {"participants": []},
            )
        self.assertEqual(captured["status"], 400)
        self.assertEqual(captured["payload"]["error"], "An unexpected error occurred.")


class TestMarkCandidateContract(WriteEndpointContractTestBase):
    def test_mark_personal_returns_200(self):
        cid = self._import_candidate("mk-1", "Lunch break", "2026-05-15T12:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/mark",
            {"classification": "personal", "reason": "Lunch"},
        )
        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"]["session"]["review_status"], "excluded")

    def test_mark_with_default_classification(self):
        cid = self._import_candidate("mk-2", "Lunch break", "2026-05-15T12:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/mark",
            {},
        )
        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"]["session"]["classification"], "personal")


class TestRestoreCandidateContract(WriteEndpointContractTestBase):
    def test_restore_returns_200(self):
        cid = self._import_candidate("rs-1", "Avery Stone 6pm", "2026-05-15T12:00:00-04:00")
        self._post(f"/api/review/candidates/{cid}/mark", {"classification": "personal"})
        _, captured = self._post(
            f"/api/review/candidates/{cid}/restore",
            {"reason": "Restoring"},
        )
        self.assertEqual(captured["status"], 200)
        self.assertIn("session", captured["payload"])

    def test_restore_no_session_returns_400(self):
        cid = self._import_candidate("rs-2", "Lunch break", "2026-05-15T12:00:00-04:00")
        self.conn.execute("DELETE FROM sessions WHERE candidate_id = ?", (cid,))
        self.conn.commit()
        _, captured = self._post(
            f"/api/review/candidates/{cid}/restore",
            {"reason": "Restoring"},
        )
        self.assertEqual(captured["status"], 400)
        self.assertEqual(
            captured["payload"]["error"],
            "No session found for this candidate; only session-backed candidates can be restored.",
        )



class TestSendToReviewContract(WriteEndpointContractTestBase):
    def test_send_to_review_returns_200(self):
        # Import a candidate and mark it excluded first
        cid = self._import_candidate("tr-1", "Some event", "2026-05-15T12:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/send-to-review",
            {"reason": "Needs review"},
        )
        self.assertEqual(captured["status"], 200)
        self.assertIn("session", captured["payload"])


class TestRecalcRatesContract(WriteEndpointContractTestBase):
    def test_recalc_rates_returns_200_with_count(self):
        _, captured = self._post("/api/review/recalc-rates", {})
        self.assertEqual(captured["status"], 200)
        self.assertTrue(captured["payload"]["ok"])
        self.assertIn("sessions_updated", captured["payload"])
        self.assertIsInstance(captured["payload"]["sessions_updated"], int)


class TestReparseCandidatesContract(WriteEndpointContractTestBase):
    def test_reparse_returns_200(self):
        _, captured = self._post("/api/review/reparse-candidates", {})
        self.assertEqual(captured["status"], 200)
        self.assertTrue(captured["payload"]["ok"])


# ---------------------------------------------------------------------------
# 2. People and Identity
# ---------------------------------------------------------------------------

class TestCreatePersonContract(WriteEndpointContractTestBase):
    def test_create_person_returns_200(self):
        _, captured = self._post("/api/people", {
            "first_name": "Jordan",
            "last_name": "Test",
            "display_name": "Jordan Test",
        })
        self.assertEqual(captured["status"], 200)
        self.assertIn("person_id", captured["payload"])

    def test_create_person_missing_display_name_returns_400(self):
        _, captured = self._post("/api/people", {})
        self.assertEqual(captured["status"], 400)
        self.assertEqual(captured["payload"]["error"], "Display name is required.")


class TestUpdatePersonContract(WriteEndpointContractTestBase):
    def test_update_person_returns_200(self):
        _, captured = self._post(
            f"/api/people/{self.person['person_id']}",
            {"display_name": "Avery Q. Stone"},
        )
        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"]["display_name"], "Avery Q. Stone")

    def test_update_person_not_found_returns_400(self):
        _, captured = self._post(
            "/api/people/nonexistent-id",
            {"display_name": "Test"},
        )
        self.assertEqual(captured["status"], 400)
        self.assertEqual(captured["payload"]["error"], "Person not found.")


class TestMergePeopleContract(WriteEndpointContractTestBase):
    def test_merge_returns_200(self):
        dup = create_person(self.conn, {"first_name": "Dup", "last_name": "Person",
                                         "display_name": "Dup Person"})
        _, captured = self._post(
            f"/api/people/{self.person['person_id']}/merge",
            {"duplicate_person_id": dup["person_id"], "reason": "test merge"},
        )
        self.assertEqual(captured["status"], 200)

    def test_merge_self_returns_400(self):
        _, captured = self._post(
            f"/api/people/{self.person['person_id']}/merge",
            {"duplicate_person_id": self.person["person_id"]},
        )
        self.assertEqual(captured["status"], 400)
        self.assertEqual(captured["payload"]["error"], "Cannot merge a person into itself.")

    def test_merge_missing_duplicate_id_returns_400_sanitized(self):
        _, captured = self._post(
            f"/api/people/{self.person['person_id']}/merge",
            {"reason": "no id"},
        )
        self.assertEqual(captured["status"], 400)
        self.assertEqual(captured["payload"]["error"], "duplicate_person_id is required.")


# ---------------------------------------------------------------------------
# 3. Billing Relationships and Accounts
# ---------------------------------------------------------------------------

class TestCreateAccountContract(WriteEndpointContractTestBase):
    def test_create_account_returns_200(self):
        _, captured = self._post("/api/accounts", {
            "account_name": "Test Household",
            "account_type": "household",
        })
        self.assertEqual(captured["status"], 200)
        self.assertIn("account_id", captured["payload"])

    def test_create_account_missing_name_returns_400_sanitized(self):
        _, captured = self._post("/api/accounts", {"account_type": "household"})
        self.assertEqual(captured["status"], 400)
        self.assertEqual(captured["payload"]["error"], "account_name is required.")


class TestDeactivateAccountContract(WriteEndpointContractTestBase):
    def test_deactivate_returns_200(self):
        create_resp, _ = self._post("/api/accounts", {
            "account_name": "Test HH", "account_type": "household",
        })
        # Get account_id from the create response
        create_resp2, create_captured = self._post("/api/accounts", {
            "account_name": "Test HH 2", "account_type": "household",
        })
        account_id = create_captured["payload"]["account_id"]
        _, captured = self._post(f"/api/accounts/{account_id}/deactivate", {})
        self.assertEqual(captured["status"], 200)

    def test_deactivate_not_found_returns_404(self):
        _, captured = self._post("/api/accounts/nonexistent/deactivate", {})
        self.assertEqual(captured["status"], 404)
        self.assertEqual(captured["payload"]["error"], "Account not found.")


class TestCreateBillingPartyContract(WriteEndpointContractTestBase):
    def test_create_billing_party_returns_200(self):
        _, captured = self._post("/api/billing-parties", {
            "billing_name": "Test Org",
            "billing_party_type": "organization",
        })
        self.assertEqual(captured["status"], 200)
        self.assertIn("billing_party_id", captured["payload"])

    def test_create_billing_party_missing_name_returns_400(self):
        _, captured = self._post("/api/billing-parties", {
            "billing_party_type": "person",
        })
        self.assertEqual(captured["status"], 400)
        self.assertEqual(captured["payload"]["error"], "Billing name is required.")


class TestUpdateBillingPartyContract(WriteEndpointContractTestBase):
    def test_update_billing_party_returns_200(self):
        _, captured = self._post(
            f"/api/billing-parties/{self.party['billing_party_id']}",
            {"billing_email": "updated@example.test"},
        )
        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"]["billing_email"], "updated@example.test")

    def test_update_billing_party_not_found_returns_400(self):
        _, captured = self._post(
            "/api/billing-parties/nonexistent-id",
            {"billing_email": "test@example.test"},
        )
        self.assertEqual(captured["status"], 400)
        self.assertEqual(captured["payload"]["error"], "Billing party not found.")


# ---------------------------------------------------------------------------
# 4. Rate Rules
# ---------------------------------------------------------------------------

class TestRateRulesContract(WriteEndpointContractTestBase):
    def test_create_rate_rule_returns_200(self):
        _, captured = self._post("/api/rate-rules", {
            "applies_to": "everyone",
            "amount": "150.00",
            "duration_minutes": 60,
            "billing_session_type": "psychotherapy",
            "effective_from": "2026-01-01",
            "service_mode": "office",
        })
        self.assertEqual(captured["status"], 200)
        self.assertIn("rate_rule_id", captured["payload"])

    def test_rate_rule_preview_returns_200(self):
        _, captured = self._post("/api/rate-rules/preview", {
            "applies_to": "everyone",
            "amount": "150.00",
            "duration_minutes": 60,
            "billing_session_type": "psychotherapy",
            "service_mode": "office",
        })
        self.assertEqual(captured["status"], 200)


# ---------------------------------------------------------------------------
# 5. Business Profile
# ---------------------------------------------------------------------------

class TestBusinessProfileContract(WriteEndpointContractTestBase):
    def test_save_business_profile_returns_200(self):
        _, captured = self._post("/api/business-profile", {
            "business_name": "Updated Practice",
        })
        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"]["business_name"], "Updated Practice")

    def test_save_business_profile_missing_name_returns_400(self):
        _, captured = self._post("/api/business-profile", {"business_name": ""})
        self.assertEqual(captured["status"], 400)
        self.assertEqual(captured["payload"]["error"], "Business name is required.")


# ---------------------------------------------------------------------------
# 6. Invoices
# ---------------------------------------------------------------------------

class TestCreateInvoiceDraftContract(WriteEndpointContractTestBase):
    def test_create_invoice_returns_200(self):
        _, captured = self._post("/api/invoices", {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_month": "2026-05",
        })
        self.assertEqual(captured["status"], 200)
        self.assertIn("invoice", captured["payload"])

    def test_create_invoice_missing_party_returns_400(self):
        _, captured = self._post("/api/invoices", {"billing_month": "2026-05"})
        self.assertEqual(captured["status"], 400)
        self.assertEqual(captured["payload"]["error"], "Select an active bill-to party.")


class TestStageInvoicesContract(WriteEndpointContractTestBase):
    def test_stage_all_returns_200(self):
        self._approved_session("st-1", day=10)
        _, captured = self._post("/api/invoices/stage", {})
        self.assertEqual(captured["status"], 200)
        expected_keys = {
            "drafts_created", "drafts_reused", "sessions_staged",
            "sessions_already_staged", "sessions_moved",
            "sessions_removed_ineligible", "sessions_skipped", "errors",
        }
        self.assertTrue(expected_keys <= set(captured["payload"].keys()))

    def test_stage_idempotent(self):
        self._approved_session("st-2", day=10)
        self._approved_session("st-3", day=20)
        _, c1 = self._post("/api/invoices/stage", {})
        _, c2 = self._post("/api/invoices/stage", {})
        self.assertEqual(c1["status"], 200)
        self.assertEqual(c2["status"], 200)
        self.assertGreater(c1["payload"]["sessions_staged"], 0)
        self.assertEqual(c2["payload"]["sessions_staged"], 0)
        self.assertEqual(c2["payload"]["sessions_already_staged"],
                         c1["payload"]["sessions_staged"])

    def test_stage_non_list_session_ids_returns_400(self):
        _, captured = self._post("/api/invoices/stage", {"session_ids": "not-a-list"})
        self.assertEqual(captured["status"], 400)

    def test_stage_empty_string_session_id_returns_400(self):
        _, captured = self._post("/api/invoices/stage", {"session_ids": [""]})
        self.assertEqual(captured["status"], 400)


class TestFinalizeInvoiceContract(WriteEndpointContractTestBase):
    def test_preview_finalize_endpoint_is_side_effect_free(self):
        self._approved_session("pf-1", day=10)
        self._post("/api/invoices/stage", {})
        invoice = self.conn.execute(
            "SELECT invoice_id, delivery_method, revision FROM invoices WHERE status = 'draft' LIMIT 1"
        ).fetchone()
        _, captured = self._post(
            f"/api/invoices/{invoice['invoice_id']}/preview-finalize",
            {
                "delivery_method": "mail",
                "insurance_coding_included": True,
                "insurance_diagnosis_code": "Z00.0",
            },
        )
        after = self.conn.execute(
            "SELECT delivery_method, revision, status, invoice_number, pdf_path FROM invoices WHERE invoice_id = ?",
            (invoice["invoice_id"],),
        ).fetchone()
        self.assertEqual(captured["status"], 200)
        self.assertEqual(after["delivery_method"], invoice["delivery_method"])
        self.assertEqual(after["revision"], invoice["revision"])
        self.assertEqual(after["status"], "draft")
        self.assertIsNone(after["invoice_number"])
        self.assertIsNone(after["pdf_path"])

    def test_finalize_without_confirmed_returns_400(self):
        sid, _ = self._approved_session("fn-1", day=10)
        # Stage first to create a draft
        self._post("/api/invoices/stage", {})
        invoice = self.conn.execute(
            "SELECT invoice_id FROM invoices WHERE status = 'draft' LIMIT 1"
        ).fetchone()
        _, captured = self._post(
            f"/api/invoices/{invoice['invoice_id']}/finalize",
            {"confirmed": False},
        )
        self.assertEqual(captured["status"], 400)
        self.assertEqual(captured["payload"]["error"],
                         "Explicit finalization confirmation is required.")


class TestVoidInvoiceContract(WriteEndpointContractTestBase):
    def test_void_without_reason_returns_400(self):
        sid, _ = self._approved_session("vd-1", day=10)
        self._post("/api/invoices/stage", {})
        invoice = self.conn.execute(
            "SELECT invoice_id FROM invoices WHERE status = 'draft' LIMIT 1"
        ).fetchone()
        _, captured = self._post(
            f"/api/invoices/{invoice['invoice_id']}/void",
            {"reason": ""},
        )
        self.assertEqual(captured["status"], 400)
        self.assertEqual(captured["payload"]["error"], "A void reason is required.")


# ---------------------------------------------------------------------------
# 7. Payments
# ---------------------------------------------------------------------------

class TestRecordPaymentContract(WriteEndpointContractTestBase):
    def _finalized_invoice(self, key="pay-1", day=10):
        self._approved_session(key, day=day)
        self._post("/api/invoices/stage", {})
        invoice_id = self.conn.execute(
            "SELECT invoice_id FROM invoices WHERE status = 'draft' LIMIT 1"
        ).fetchone()[0]
        # Set filing owner so finalize can succeed
        self._post(f"/api/invoices/{invoice_id}/filing-owner",
                     {"person_id": self.person["person_id"]})
        # Finalize at service level to control pdf_root
        result = finalize_invoice(self.conn, invoice_id,
                                   pdf_root=self.root / "Invoices")
        return result["invoice"]["invoice_id"]

    def test_record_payment_returns_200(self):
        inv_id = self._finalized_invoice("pay-1", day=10)
        _, captured = self._post(
            f"/api/invoices/{inv_id}/payments",
            {
                "payment_date": "2026-05-20",
                "amount_cents": 15000,
                "payment_method": "check",
            },
        )
        self.assertEqual(captured["status"], 200)
        self.assertIn("payment", captured["payload"])
        self.assertIn("allocations", captured["payload"])

    def test_record_payment_missing_date_returns_400(self):
        inv_id = self._finalized_invoice("pay-2", day=10)
        _, captured = self._post(
            f"/api/invoices/{inv_id}/payments",
            {"amount_cents": 15000, "payment_method": "check"},
        )
        self.assertEqual(captured["status"], 400)
        self.assertEqual(captured["payload"]["error"], "Payment date is required.")

    def test_record_payment_zero_amount_returns_400(self):
        inv_id = self._finalized_invoice("pay-3", day=10)
        _, captured = self._post(
            f"/api/invoices/{inv_id}/payments",
            {"payment_date": "2026-05-20", "amount_cents": 0, "payment_method": "check"},
        )
        self.assertEqual(captured["status"], 400)
        self.assertEqual(captured["payload"]["error"],
                         "Payment amount must be greater than zero.")

    def test_record_payment_on_draft_returns_400(self):
        sid, _ = self._approved_session("pay-4", day=10)
        self._post("/api/invoices/stage", {})
        invoice_id = self.conn.execute(
            "SELECT invoice_id FROM invoices WHERE status = 'draft' LIMIT 1"
        ).fetchone()[0]
        _, captured = self._post(
            f"/api/invoices/{invoice_id}/payments",
            {"payment_date": "2026-05-20", "amount_cents": 15000, "payment_method": "check"},
        )
        self.assertEqual(captured["status"], 400)
        self.assertEqual(captured["payload"]["error"],
                         "Cannot record a payment for a draft invoice.")

    def test_record_payment_duplicate_returns_200_with_flag(self):
        inv_id = self._finalized_invoice("pay-5", day=10)
        body = {
            "payment_date": "2026-05-20",
            "amount_cents": 15000,
            "payment_method": "check",
        }
        _, c1 = self._post(f"/api/invoices/{inv_id}/payments", body)
        _, c2 = self._post(f"/api/invoices/{inv_id}/payments", body)
        self.assertEqual(c1["status"], 200)
        # Duplicate payment submission: current behavior returns 200 with
        # duplicate_submission_ignored flag, or 400 if duplicate detection
        # raises a safe validation error.
        self.assertIn(c2["status"], (200, 400))
        if c2["status"] == 200:
            self.assertTrue(c2["payload"].get("duplicate_submission_ignored"))


class TestVoidPaymentContract(WriteEndpointContractTestBase):
    def test_void_without_reason_returns_400(self):
        sid, _ = self._approved_session("vp-1", day=10)
        self._post("/api/invoices/stage", {})
        invoice_id = self.conn.execute(
            "SELECT invoice_id FROM invoices WHERE status = 'draft' LIMIT 1"
        ).fetchone()[0]
        self._post(f"/api/invoices/{invoice_id}/filing-owner",
                     {"person_id": self.person["person_id"]})
        finalize_invoice(self.conn, invoice_id, pdf_root=self.root / "Invoices")
        _, pay_cap = self._post(
            f"/api/invoices/{invoice_id}/payments",
            {"payment_date": "2026-05-20", "amount_cents": 15000, "payment_method": "check"},
        )
        payment_id = pay_cap["payload"]["payment"]["payment_id"]
        _, captured = self._post(f"/api/payments/{payment_id}/void", {"reason": ""})
        self.assertEqual(captured["status"], 400)
        self.assertEqual(captured["payload"]["error"], "A void reason is required.")


# ---------------------------------------------------------------------------
# 8. Sync Endpoints
# ---------------------------------------------------------------------------

class TestSyncRunContract(WriteEndpointContractTestBase):
    def test_sync_run_returns_200(self):
        with patch("jordana_invoice.review_server.sync_calendar_automatically") as mock_sync, \
             patch("jordana_invoice.review_server.review_sync_config",
                   return_value={"reports_dir": "Reports"}), \
             patch("jordana_invoice.review_server.sync_status_for_connection",
                   return_value={"last_success": "2026-06-23T00:00:00"}), \
             patch("jordana_invoice.review_server.public_sync_status",
                   side_effect=lambda p: p):
            class Result:
                rows_fetched = 0
                rows_imported = 0
                duplicate_rows_skipped = 0
                review_items_changed = 0
                mode = "incremental"
            mock_sync.return_value = Result()
            _, captured = self._post("/api/sync/run", {})
            self.assertEqual(captured["status"], 200)
            self.assertIn("rows_fetched", captured["payload"])
            self.assertIn("mode", captured["payload"])
            self.assertIn("status", captured["payload"])


class TestSyncRebuildContract(WriteEndpointContractTestBase):
    def test_rebuild_without_confirmed_returns_400(self):
        _, captured = self._post("/api/sync/rebuild", {"confirmed": False})
        self.assertEqual(captured["status"], 400)
        self.assertEqual(captured["payload"]["error"],
                         "Explicit rebuild confirmation is required.")


# ---------------------------------------------------------------------------
# 9. Write Token Enforcement (cross-cutting)
# ---------------------------------------------------------------------------

class TestWriteTokenEnforcementContract(WriteEndpointContractTestBase):
    def _handler_no_token(self, path, body=b"{}"):
        handler = object.__new__(self.handler_cls)
        handler.path = path
        handler.headers = {
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
        }
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler._database_connection = self.conn
        captured = {}
        handler.send_json = lambda payload, status=200: captured.update({
            "payload": payload, "status": status,
        })
        handler.finish = lambda: None
        handler.log_message = lambda *a: None
        return handler, captured

    def test_missing_write_token_returns_403(self):
        handler, captured = self._handler_no_token(
            "/api/people", json.dumps({"display_name": "Test"}).encode("utf-8"),
        )
        handler.do_POST()
        self.assertEqual(captured["status"], 403)
        self.assertEqual(captured["payload"], {"ok": False, "error": "Forbidden."})

    def test_incorrect_write_token_returns_403(self):
        handler = object.__new__(self.handler_cls)
        handler.path = "/api/people"
        body = json.dumps({"display_name": "Test"}).encode("utf-8")
        handler.headers = {
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
            self.handler_cls.write_token_header: "wrong-token",
        }
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler._database_connection = self.conn
        captured = {}
        handler.send_json = lambda payload, status=200: captured.update({
            "payload": payload, "status": status,
        })
        handler.finish = lambda: None
        handler.log_message = lambda *a: None
        handler.do_POST()
        self.assertEqual(captured["status"], 403)
        self.assertEqual(captured["payload"], {"ok": False, "error": "Forbidden."})


# ---------------------------------------------------------------------------
# 10. Malformed JSON (cross-cutting)
# ---------------------------------------------------------------------------

class TestMalformedJsonContract(WriteEndpointContractTestBase):
    def test_malformed_json_returns_400(self):
        handler, captured = self._handler(
            "/api/people", b"{not valid json",
        )
        handler.do_POST()
        self.assertEqual(captured["status"], 400)
        self.assertEqual(captured["payload"]["error"], "Malformed JSON in request body.")


# ---------------------------------------------------------------------------
# 11. Service Catalog
# ---------------------------------------------------------------------------

class TestServiceCatalogContract(WriteEndpointContractTestBase):
    def test_activate_service_returns_200(self):
        service = self.conn.execute(
            "SELECT service_catalog_id FROM service_catalog LIMIT 1"
        ).fetchone()
        if service:
            _, captured = self._post(
                f"/api/service-catalog/{service['service_catalog_id']}/activate",
                {},
            )
            self.assertEqual(captured["status"], 200)


# ---------------------------------------------------------------------------
# 12. Unsafe Exception Sanitization (cross-cutting)
# ---------------------------------------------------------------------------

class TestUnsafeExceptionSanitizationContract(WriteEndpointContractTestBase):
    def test_unsafe_get_exception_returns_500(self):
        with patch("jordana_invoice.review_server.dashboard_status",
                    side_effect=RuntimeError("disk I/O error /path/to/db")):
            handler, captured = self._handler("/api/status")
            handler.conn = lambda: self.conn
            handler.do_GET()
            self.assertEqual(captured["status"], 500)
            self.assertEqual(captured["payload"],
                             {"ok": False, "error": "An unexpected error occurred."})

    def test_safe_validation_error_preserved_on_post(self):
        with patch("jordana_invoice.review_server.create_person",
                    side_effect=ValueError("Display name is required.")):
            _, captured = self._post("/api/people", {"name": ""})
            self.assertEqual(captured["status"], 400)
            self.assertEqual(captured["payload"]["error"], "Display name is required.")

    def test_unsafe_value_error_sanitized_on_post(self):
        with patch("jordana_invoice.review_server.create_person",
                    side_effect=ValueError("internal SQL: SELECT * /path/to/db")):
            _, captured = self._post("/api/people", {"name": "test"})
            self.assertEqual(captured["status"], 400)
            self.assertEqual(captured["payload"]["error"],
                             "An unexpected error occurred.")


if __name__ == "__main__":
    unittest.main()
