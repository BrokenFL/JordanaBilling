"""Focused tests for request validation helpers (Round 4A.2).

Tests cover the four scoped write workflows:
1. Approve reviewed session
2. Save session draft or section-level session values
3. Confirm duplicate / duplicate resolution (mark endpoint)
4. Restore candidate

Each workflow is tested for:
- valid current payload
- missing required field (where applicable)
- wrong top-level JSON type
- wrong field type
- empty identifier
- invalid enum-like value
- documented legacy alias
- boolean incorrectly supplied where integer is required
- unknown field behavior
- sanitized error behavior
- no persistence call when validation fails
- unchanged success response contract
- unchanged failure status code and response shape
- write-token enforcement remains before validation

Uses temporary databases and synthetic fixtures only.
"""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from jordana_invoice.db import connect, migrate_database
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import save_business_profile
from jordana_invoice.review_server import make_handler, is_safe_validation_error
from jordana_invoice.review_services import (
    approve_candidate,
    create_billing_party,
    create_person,
)
from jordana_invoice.request_validation import (
    RequestValidationError,
    parse_approve_session_request,
    parse_save_interpretation_request,
    parse_save_person_section_request,
    parse_save_relationship_section_request,
    parse_save_billing_section_request,
    parse_save_session_draft_request,
    parse_mark_candidate_request,
    parse_restore_candidate_request,
)
from jordana_invoice.util import stable_hash


def raw_row(key, title, start):
    return {
        "ingested_at": "2026-08-20T12:00:00Z",
        "snapshot_key": key,
        "run_id": f"run-{key}",
        "batch_name": "rv-test",
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


class RequestValidationTestBase(unittest.TestCase):
    """Base class with shared setup for DB-backed request validation tests."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = str(self.root / "rv.sqlite3")
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

    def _import_candidate(self, key, title, start):
        import_rows(self.conn, [raw_row(key, title, start)], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        return candidate_id


# ---------------------------------------------------------------------------
# 1. Approve Session Request Parser
# ---------------------------------------------------------------------------

class TestParseApproveSessionRequest(unittest.TestCase):
    """Unit tests for parse_approve_session_request."""

    def test_valid_payload(self):
        payload = {
            "participants": [{"person_id": "p1", "display_name": "Avery Stone"}],
            "billing_party_id": "bp1",
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "150.00",
            "payment_status": "unpaid",
            "billing_treatment": "billable",
        }
        req = parse_approve_session_request(payload)
        self.assertEqual(req.to_payload(), payload)

    def test_wrong_top_level_type(self):
        with self.assertRaises(RequestValidationError):
            parse_approve_session_request([])
        with self.assertRaises(RequestValidationError):
            parse_approve_session_request("not a dict")
        with self.assertRaises(RequestValidationError):
            parse_approve_session_request(42)

    def test_wrong_field_type_participants(self):
        with self.assertRaises(RequestValidationError):
            parse_approve_session_request({"participants": "not-a-list"})

    def test_wrong_field_type_participants_item(self):
        with self.assertRaises(RequestValidationError):
            parse_approve_session_request({"participants": ["not-a-dict"]})

    def test_wrong_field_type_billing_party_id(self):
        with self.assertRaises(RequestValidationError):
            parse_approve_session_request({"billing_party_id": 123})

    def test_empty_billing_party_id(self):
        with self.assertRaises(RequestValidationError):
            parse_approve_session_request({"billing_party_id": "  "})

    def test_wrong_field_type_service_mode(self):
        with self.assertRaises(RequestValidationError):
            parse_approve_session_request({"service_mode": 123})

    def test_wrong_field_type_time_category(self):
        with self.assertRaises(RequestValidationError):
            parse_approve_session_request({"time_category": 123})

    def test_wrong_field_type_approved_rate(self):
        with self.assertRaises(RequestValidationError):
            parse_approve_session_request({"approved_rate": []})

    def test_boolean_where_integer_expected(self):
        with self.assertRaises(RequestValidationError):
            parse_approve_session_request({"approved_duration_minutes": True})
        with self.assertRaises(RequestValidationError):
            parse_approve_session_request({"approved_duration_minutes": False})

    def test_boolean_where_string_or_integer_expected(self):
        with self.assertRaises(RequestValidationError):
            parse_approve_session_request({"approved_rate": True})

    def test_legacy_alias_duration_minutes(self):
        payload = {"duration_minutes": 90}
        req = parse_approve_session_request(payload)
        self.assertEqual(req.to_payload()["duration_minutes"], 90)

    def test_unknown_field_passed_through(self):
        payload = {"participants": [], "unknown_field": "value"}
        req = parse_approve_session_request(payload)
        self.assertEqual(req.to_payload()["unknown_field"], "value")

    def test_empty_payload_accepted(self):
        """No fields are required at the HTTP layer; service validates."""
        req = parse_approve_session_request({})
        self.assertEqual(req.to_payload(), {})

    def test_string_duration_accepted(self):
        """Current behavior accepts string integers for duration."""
        req = parse_approve_session_request({"approved_duration_minutes": "60"})
        self.assertEqual(req.to_payload()["approved_duration_minutes"], "60")

    def test_error_message_is_safe(self):
        """Error messages must not expose backend details."""
        try:
            parse_approve_session_request({"participants": "bad"})
        except RequestValidationError as e:
            msg = str(e)
            self.assertNotIn("SELECT", msg)
            self.assertNotIn("path", msg.lower())
            self.assertNotIn("db", msg.lower())

    # -- Regression: custom_duration_minutes empty-string bug --

    def test_custom_duration_null_accepted(self):
        """Standard 60-minute approval: null custom_duration_minutes must pass."""
        req = parse_approve_session_request({
            "approved_duration_minutes": "60",
            "duration_choice": "60",
            "custom_duration_minutes": None,
        })
        self.assertIsNone(req.to_payload()["custom_duration_minutes"])

    def test_custom_duration_omitted_accepted(self):
        """Standard approval: missing custom_duration_minutes must pass."""
        req = parse_approve_session_request({
            "approved_duration_minutes": "60",
            "duration_choice": "60",
        })
        self.assertNotIn("custom_duration_minutes", req.to_payload())

    def test_custom_duration_empty_string_rejected(self):
        """Empty string must be rejected — it is not a valid integer."""
        with self.assertRaises(RequestValidationError) as ctx:
            parse_approve_session_request({"custom_duration_minutes": ""})
        self.assertIn("custom_duration_minutes must be an integer", str(ctx.exception))

    def test_custom_duration_valid_integer_accepted(self):
        """Valid custom duration integer must pass."""
        req = parse_approve_session_request({"custom_duration_minutes": 45})
        self.assertEqual(req.to_payload()["custom_duration_minutes"], 45)

    def test_custom_duration_valid_string_integer_accepted(self):
        """String representation of a valid integer must pass (frontend sends strings)."""
        req = parse_approve_session_request({"custom_duration_minutes": "45"})
        self.assertEqual(req.to_payload()["custom_duration_minutes"], "45")

    def test_custom_duration_non_integer_string_rejected(self):
        """Non-integer string must still be rejected — no silent conversion."""
        with self.assertRaises(RequestValidationError) as ctx:
            parse_approve_session_request({"custom_duration_minutes": "abc"})
        self.assertIn("custom_duration_minutes must be an integer", str(ctx.exception))


# ---------------------------------------------------------------------------
# 2. Save Section Request Parsers
# ---------------------------------------------------------------------------

class TestParseSaveInterpretationRequest(unittest.TestCase):

    def test_valid_payload(self):
        payload = {
            "participants": [{"person_id": "p1", "display_name": "Test"}],
            "billing_party_id": "bp1",
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "150.00",
            "payment_status": "unpaid",
        }
        req = parse_save_interpretation_request(payload)
        self.assertEqual(req.to_payload(), payload)

    def test_wrong_top_level_type(self):
        with self.assertRaises(RequestValidationError):
            parse_save_interpretation_request("not a dict")

    def test_wrong_field_type_participants(self):
        with self.assertRaises(RequestValidationError):
            parse_save_interpretation_request({"participants": 123})

    def test_boolean_where_integer_expected(self):
        with self.assertRaises(RequestValidationError):
            parse_save_interpretation_request({"approved_duration_minutes": True})

    def test_unknown_field_passed_through(self):
        req = parse_save_interpretation_request({"unknown": "ok"})
        self.assertEqual(req.to_payload()["unknown"], "ok")

    def test_empty_payload_accepted(self):
        req = parse_save_interpretation_request({})
        self.assertEqual(req.to_payload(), {})


class TestParseSavePersonSectionRequest(unittest.TestCase):

    def test_valid_payload_with_person_dict(self):
        payload = {"person": {"person_id": "p1", "display_name": "Test"}}
        req = parse_save_person_section_request(payload)
        self.assertEqual(req.to_payload(), payload)

    def test_valid_payload_top_level_fields(self):
        payload = {"person_id": "p1", "first_name": "Test", "last_name": "User"}
        req = parse_save_person_section_request(payload)
        self.assertEqual(req.to_payload(), payload)

    def test_wrong_top_level_type(self):
        with self.assertRaises(RequestValidationError):
            parse_save_person_section_request([])

    def test_wrong_field_type_person(self):
        with self.assertRaises(RequestValidationError):
            parse_save_person_section_request({"person": "not-a-dict"})

    def test_wrong_field_type_person_id(self):
        with self.assertRaises(RequestValidationError):
            parse_save_person_section_request({"person_id": 123})

    def test_empty_person_id_rejected(self):
        with self.assertRaises(RequestValidationError):
            parse_save_person_section_request({"person_id": "  "})

    def test_unknown_field_passed_through(self):
        req = parse_save_person_section_request({"extra": "ok"})
        self.assertEqual(req.to_payload()["extra"], "ok")


class TestParseSaveRelationshipSectionRequest(unittest.TestCase):

    def test_valid_payload(self):
        payload = {
            "participants": [{"person_id": "p1", "display_name": "Test", "is_primary": True}],
            "account_id": "acc1",
        }
        req = parse_save_relationship_section_request(payload)
        self.assertEqual(req.to_payload(), payload)

    def test_wrong_top_level_type(self):
        with self.assertRaises(RequestValidationError):
            parse_save_relationship_section_request(42)

    def test_wrong_field_type_participants(self):
        with self.assertRaises(RequestValidationError):
            parse_save_relationship_section_request({"participants": "bad"})

    def test_wrong_field_type_participants_item(self):
        with self.assertRaises(RequestValidationError):
            parse_save_relationship_section_request({"participants": [123]})

    def test_empty_account_id_rejected(self):
        with self.assertRaises(RequestValidationError):
            parse_save_relationship_section_request({"account_id": ""})

    def test_unknown_field_passed_through(self):
        req = parse_save_relationship_section_request({"extra": "ok"})
        self.assertEqual(req.to_payload()["extra"], "ok")

    def test_empty_payload_accepted(self):
        req = parse_save_relationship_section_request({})
        self.assertEqual(req.to_payload(), {})


class TestParseSaveBillingSectionRequest(unittest.TestCase):

    def test_valid_payload(self):
        payload = {"billing_party_id": "bp1"}
        req = parse_save_billing_section_request(payload)
        self.assertEqual(req.to_payload(), payload)

    def test_valid_payload_with_billing_party_dict(self):
        payload = {"billing_party": {"billing_party_id": "bp1", "billing_name": "Test"}}
        req = parse_save_billing_section_request(payload)
        self.assertEqual(req.to_payload(), payload)

    def test_wrong_top_level_type(self):
        with self.assertRaises(RequestValidationError):
            parse_save_billing_section_request("bad")

    def test_wrong_field_type_billing_party_id(self):
        with self.assertRaises(RequestValidationError):
            parse_save_billing_section_request({"billing_party_id": 123})

    def test_empty_billing_party_id_rejected(self):
        with self.assertRaises(RequestValidationError):
            parse_save_billing_section_request({"billing_party_id": "  "})

    def test_wrong_field_type_billing_party_dict(self):
        with self.assertRaises(RequestValidationError):
            parse_save_billing_section_request({"billing_party": "not-a-dict"})

    def test_unknown_field_passed_through(self):
        req = parse_save_billing_section_request({"extra": "ok"})
        self.assertEqual(req.to_payload()["extra"], "ok")

    def test_empty_payload_accepted(self):
        req = parse_save_billing_section_request({})
        self.assertEqual(req.to_payload(), {})


class TestParseSaveSessionDraftRequest(unittest.TestCase):

    def test_valid_payload(self):
        payload = {
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "approved_rate": "150.00",
            "payment_status": "unpaid",
        }
        req = parse_save_session_draft_request(payload)
        self.assertEqual(req.to_payload(), payload)

    def test_wrong_top_level_type(self):
        with self.assertRaises(RequestValidationError):
            parse_save_session_draft_request([])

    def test_wrong_field_type_duration(self):
        with self.assertRaises(RequestValidationError):
            parse_save_session_draft_request({"approved_duration_minutes": "not-a-number"})

    def test_boolean_where_integer_expected(self):
        with self.assertRaises(RequestValidationError):
            parse_save_session_draft_request({"approved_duration_minutes": True})
        with self.assertRaises(RequestValidationError):
            parse_save_session_draft_request({"custom_duration_minutes": False})

    def test_wrong_field_type_payment_status(self):
        with self.assertRaises(RequestValidationError):
            parse_save_session_draft_request({"payment_status": 123})

    def test_wrong_field_type_billing_treatment(self):
        with self.assertRaises(RequestValidationError):
            parse_save_session_draft_request({"billing_treatment": []})

    def test_legacy_alias_duration_minutes(self):
        req = parse_save_session_draft_request({"duration_minutes": 90})
        self.assertEqual(req.to_payload()["duration_minutes"], 90)

    def test_unknown_field_passed_through(self):
        req = parse_save_session_draft_request({"extra": "ok"})
        self.assertEqual(req.to_payload()["extra"], "ok")

    def test_empty_payload_accepted(self):
        req = parse_save_session_draft_request({})
        self.assertEqual(req.to_payload(), {})


# ---------------------------------------------------------------------------
# 3. Mark Candidate Request Parser (Duplicate Resolution)
# ---------------------------------------------------------------------------

class TestParseMarkCandidateRequest(unittest.TestCase):

    def test_valid_duplicate_classification(self):
        req = parse_mark_candidate_request({"classification": "duplicate", "reason": "dup"})
        self.assertEqual(req.classification, "duplicate")
        self.assertEqual(req.reason, "dup")

    def test_valid_personal_classification(self):
        req = parse_mark_candidate_request({"classification": "personal"})
        self.assertEqual(req.classification, "personal")

    def test_default_classification(self):
        req = parse_mark_candidate_request({})
        self.assertEqual(req.classification, "personal")
        self.assertEqual(req.reason, "")

    def test_default_reason(self):
        req = parse_mark_candidate_request({"classification": "administrative"})
        self.assertEqual(req.reason, "")

    def test_wrong_top_level_type(self):
        with self.assertRaises(RequestValidationError):
            parse_mark_candidate_request("bad")

    def test_wrong_field_type_classification(self):
        with self.assertRaises(RequestValidationError):
            parse_mark_candidate_request({"classification": 123})

    def test_wrong_field_type_reason(self):
        with self.assertRaises(RequestValidationError):
            parse_mark_candidate_request({"reason": 123})

    def test_invalid_enum_classification(self):
        with self.assertRaises(RequestValidationError):
            parse_mark_candidate_request({"classification": "invalid_value"})

    def test_unknown_field_passed_through(self):
        req = parse_mark_candidate_request({"extra": "ok"})
        self.assertEqual(req.to_payload()["extra"], "ok")

    def test_all_allowed_classifications(self):
        for cls in ("personal", "administrative", "nonbillable", "duplicate", "client_session"):
            req = parse_mark_candidate_request({"classification": cls})
            self.assertEqual(req.classification, cls)


# ---------------------------------------------------------------------------
# 4. Restore Candidate Request Parser
# ---------------------------------------------------------------------------

class TestParseRestoreCandidateRequest(unittest.TestCase):

    def test_valid_payload_with_reason(self):
        req = parse_restore_candidate_request({"reason": "Restoring"})
        self.assertEqual(req.reason, "Restoring")

    def test_valid_payload_empty(self):
        req = parse_restore_candidate_request({})
        self.assertEqual(req.reason, "")

    def test_wrong_top_level_type(self):
        with self.assertRaises(RequestValidationError):
            parse_restore_candidate_request([])

    def test_wrong_field_type_reason(self):
        with self.assertRaises(RequestValidationError):
            parse_restore_candidate_request({"reason": 123})

    def test_unknown_field_passed_through(self):
        req = parse_restore_candidate_request({"extra": "ok"})
        self.assertEqual(req.to_payload()["extra"], "ok")


# ---------------------------------------------------------------------------
# 5. RequestValidationError Safety
# ---------------------------------------------------------------------------

class TestRequestValidationErrorSafety(unittest.TestCase):

    def test_is_safe_validation_error(self):
        err = RequestValidationError("participants must be a list.")
        self.assertTrue(is_safe_validation_error(err))

    def test_is_safe_validation_error_subclass_of_value_error(self):
        err = RequestValidationError("test")
        self.assertIsInstance(err, ValueError)

    def test_error_messages_do_not_expose_internals(self):
        cases = [
            ("participants must be a list.",),
            ("billing_party_id must be a string.",),
            ("approved_duration_minutes must be an integer, not a boolean.",),
            ("Request body must be a JSON object.",),
            ("classification must be one of: administrative, client_session, duplicate, nonbillable, personal.",),
        ]
        for (msg,) in cases:
            self.assertNotIn("SELECT", msg)
            self.assertNotIn("INSERT", msg)
            self.assertNotIn("UPDATE", msg)
            self.assertNotIn("DELETE", msg)
            self.assertNotIn("sqlite", msg.lower())
            self.assertNotIn("/path/", msg)


# ---------------------------------------------------------------------------
# 6. Handler-Level Integration: No Persistence on Validation Failure
# ---------------------------------------------------------------------------

class TestNoPersistenceOnValidationFailure(RequestValidationTestBase):

    def test_approve_no_persistence_on_bad_participants_type(self):
        cid = self._import_candidate("np-1", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        with patch("jordana_invoice.review_server.approve_candidate") as mock_approve:
            _, captured = self._post(
                f"/api/review/candidates/{cid}/approve",
                {"participants": "not-a-list"},
            )
        mock_approve.assert_not_called()
        self.assertEqual(captured["status"], 400)
        self.assertFalse(captured["payload"]["ok"])

    def test_save_no_persistence_on_bad_duration_type(self):
        cid = self._import_candidate("np-2", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        with patch("jordana_invoice.review_server.save_interpretation") as mock_save:
            _, captured = self._post(
                f"/api/review/candidates/{cid}/save",
                {"approved_duration_minutes": True},
            )
        mock_save.assert_not_called()
        self.assertEqual(captured["status"], 400)

    def test_mark_no_persistence_on_invalid_classification(self):
        cid = self._import_candidate("np-3", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        with patch("jordana_invoice.review_server.mark_candidate") as mock_mark:
            _, captured = self._post(
                f"/api/review/candidates/{cid}/mark",
                {"classification": "invalid_class"},
            )
        mock_mark.assert_not_called()
        self.assertEqual(captured["status"], 400)

    def test_restore_no_persistence_on_bad_reason_type(self):
        cid = self._import_candidate("np-4", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        with patch("jordana_invoice.review_server.restore_candidate") as mock_restore:
            _, captured = self._post(
                f"/api/review/candidates/{cid}/restore",
                {"reason": 123},
            )
        mock_restore.assert_not_called()
        self.assertEqual(captured["status"], 400)

    def test_save_billing_no_persistence_on_bad_billing_party_id_type(self):
        cid = self._import_candidate("np-5", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        with patch("jordana_invoice.review_server.save_billing_section") as mock_save:
            _, captured = self._post(
                f"/api/review/candidates/{cid}/save-billing",
                {"billing_party_id": 123},
            )
        mock_save.assert_not_called()
        self.assertEqual(captured["status"], 400)

    def test_save_session_no_persistence_on_boolean_duration(self):
        cid = self._import_candidate("np-6", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        with patch("jordana_invoice.review_server.save_session_draft") as mock_save:
            _, captured = self._post(
                f"/api/review/candidates/{cid}/save-session",
                {"approved_duration_minutes": True},
            )
        mock_save.assert_not_called()
        self.assertEqual(captured["status"], 400)


# ---------------------------------------------------------------------------
# 7. Handler-Level Integration: Unchanged Success Contracts
# ---------------------------------------------------------------------------

class TestUnchangedSuccessContracts(RequestValidationTestBase):

    def test_approve_success_unchanged(self):
        cid = self._import_candidate("sc-1", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/approve",
            {
                "participants": [{"person_id": self.person["person_id"], "display_name": "Avery Stone"}],
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
        self.assertIn("session", captured["payload"])
        self.assertEqual(captured["payload"]["session"]["review_status"], "approved")
        self.assertIn("invoice_staging", captured["payload"])

    def test_save_success_unchanged(self):
        cid = self._import_candidate("sc-2", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/save",
            {
                "participants": [{"person_id": self.person["person_id"], "display_name": "Avery Stone"}],
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

    def test_save_billing_success_unchanged(self):
        cid = self._import_candidate("sc-3", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/save-billing",
            {"billing_party_id": self.party["billing_party_id"]},
        )
        self.assertEqual(captured["status"], 200)
        self.assertIn("session", captured["payload"])

    def test_save_session_draft_success_unchanged(self):
        cid = self._import_candidate("sc-4", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
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

    def test_mark_duplicate_success_unchanged(self):
        cid = self._import_candidate("sc-5", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/mark",
            {"classification": "duplicate", "reason": "duplicate"},
        )
        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"]["session"]["review_status"], "excluded")

    def test_mark_personal_success_unchanged(self):
        cid = self._import_candidate("sc-6", "Lunch break", "2026-05-15T12:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/mark",
            {"classification": "personal", "reason": "Lunch"},
        )
        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"]["session"]["review_status"], "excluded")

    def test_mark_default_classification_unchanged(self):
        cid = self._import_candidate("sc-7", "Lunch break", "2026-05-15T12:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/mark",
            {},
        )
        self.assertEqual(captured["status"], 200)
        self.assertEqual(captured["payload"]["session"]["classification"], "personal")

    def test_restore_success_unchanged(self):
        cid = self._import_candidate("sc-8", "Avery Stone 6pm", "2026-05-15T12:00:00-04:00")
        self._post(f"/api/review/candidates/{cid}/mark", {"classification": "personal"})
        _, captured = self._post(
            f"/api/review/candidates/{cid}/restore",
            {"reason": "Restoring"},
        )
        self.assertEqual(captured["status"], 200)


# ---------------------------------------------------------------------------
# 8. Handler-Level Integration: Failure Status Codes and Response Shapes
# ---------------------------------------------------------------------------

class TestFailureStatusAndShape(RequestValidationTestBase):

    def test_approve_bad_type_returns_400_with_ok_false(self):
        cid = self._import_candidate("fs-1", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/approve",
            {"participants": "not-a-list"},
        )
        self.assertEqual(captured["status"], 400)
        self.assertFalse(captured["payload"]["ok"])
        self.assertIn("error", captured["payload"])

    def test_save_bad_type_returns_400_with_ok_false(self):
        cid = self._import_candidate("fs-2", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/save",
            {"participants": 123},
        )
        self.assertEqual(captured["status"], 400)
        self.assertFalse(captured["payload"]["ok"])

    def test_mark_invalid_enum_returns_400_with_ok_false(self):
        cid = self._import_candidate("fs-3", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/mark",
            {"classification": "invalid"},
        )
        self.assertEqual(captured["status"], 400)
        self.assertFalse(captured["payload"]["ok"])

    def test_restore_bad_type_returns_400_with_ok_false(self):
        cid = self._import_candidate("fs-4", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/restore",
            {"reason": 123},
        )
        self.assertEqual(captured["status"], 400)
        self.assertFalse(captured["payload"]["ok"])

    def test_save_billing_bad_type_returns_400(self):
        cid = self._import_candidate("fs-5", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/save-billing",
            {"billing_party_id": 123},
        )
        self.assertEqual(captured["status"], 400)

    def test_save_session_boolean_duration_returns_400(self):
        cid = self._import_candidate("fs-6", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/save-session",
            {"approved_duration_minutes": True},
        )
        self.assertEqual(captured["status"], 400)


# ---------------------------------------------------------------------------
# 9. Write-Token Enforcement Before Validation
# ---------------------------------------------------------------------------

class TestWriteTokenBeforeValidation(RequestValidationTestBase):

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

    def test_missing_token_returns_403_before_validation(self):
        """Write token is checked before request body parsing."""
        cid = self._import_candidate("wt-1", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        body = json.dumps({"participants": "bad-type"}).encode("utf-8")
        handler, captured = self._handler_no_token(
            f"/api/review/candidates/{cid}/approve", body,
        )
        handler.do_POST()
        self.assertEqual(captured["status"], 403)
        self.assertEqual(captured["payload"], {"ok": False, "error": "Forbidden."})

    def test_incorrect_token_returns_403_before_validation(self):
        cid = self._import_candidate("wt-2", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        handler = object.__new__(self.handler_cls)
        handler.path = f"/api/review/candidates/{cid}/approve"
        body = json.dumps({"participants": "bad-type"}).encode("utf-8")
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


# ---------------------------------------------------------------------------
# 10. Sanitized Error Behavior
# ---------------------------------------------------------------------------

class TestSanitizedErrorBehavior(RequestValidationTestBase):

    def test_validation_error_not_sanitized_to_unexpected(self):
        """RequestValidationError messages should be preserved, not sanitized."""
        cid = self._import_candidate("se-1", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/approve",
            {"participants": "not-a-list"},
        )
        self.assertEqual(captured["status"], 400)
        error = captured["payload"]["error"]
        self.assertNotEqual(error, "An unexpected error occurred.")
        self.assertIn("participants", error)

    def test_validation_error_for_boolean_duration(self):
        cid = self._import_candidate("se-2", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/approve",
            {"approved_duration_minutes": True},
        )
        self.assertEqual(captured["status"], 400)
        error = captured["payload"]["error"]
        self.assertNotEqual(error, "An unexpected error occurred.")
        self.assertIn("approved_duration_minutes", error)

    def test_validation_error_for_invalid_classification(self):
        cid = self._import_candidate("se-3", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/mark",
            {"classification": "totally_invalid"},
        )
        self.assertEqual(captured["status"], 400)
        error = captured["payload"]["error"]
        self.assertNotEqual(error, "An unexpected error occurred.")
        self.assertIn("classification", error)


# ---------------------------------------------------------------------------
# 11. Unknown Field Behavior
# ---------------------------------------------------------------------------

class TestUnknownFieldBehavior(RequestValidationTestBase):

    def test_approve_unknown_field_accepted(self):
        """Unknown fields are passed through silently, preserving current behavior."""
        cid = self._import_candidate("uf-1", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/approve",
            {
                "participants": [{"person_id": self.person["person_id"], "display_name": "Avery Stone"}],
                "billing_party_id": self.party["billing_party_id"],
                "approved_duration_minutes": 60,
                "service_mode": "office",
                "time_category": "standard",
                "approved_rate": "150.00",
                "payment_status": "unpaid",
                "billing_treatment": "billable",
                "unknown_future_field": "some value",
            },
        )
        self.assertEqual(captured["status"], 200)

    def test_save_unknown_field_accepted(self):
        cid = self._import_candidate("uf-2", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/save",
            {"unknown_field": "value"},
        )
        self.assertEqual(captured["status"], 200)

    def test_mark_unknown_field_accepted(self):
        cid = self._import_candidate("uf-3", "Avery Stone 60", "2026-05-15T10:00:00-04:00")
        _, captured = self._post(
            f"/api/review/candidates/{cid}/mark",
            {"classification": "personal", "unknown_field": "value"},
        )
        self.assertEqual(captured["status"], 200)

    def test_restore_unknown_field_accepted(self):
        cid = self._import_candidate("uf-4", "Avery Stone 6pm", "2026-05-15T12:00:00-04:00")
        self._post(f"/api/review/candidates/{cid}/mark", {"classification": "personal"})
        _, captured = self._post(
            f"/api/review/candidates/{cid}/restore",
            {"reason": "ok", "unknown_field": "value"},
        )
        self.assertEqual(captured["status"], 200)


# ---------------------------------------------------------------------------
# 12. Restore Endpoint Regression Tests
# ---------------------------------------------------------------------------

class TestRestoreEndpointRegression(RequestValidationTestBase):
    """Regression tests for the restore success-with-warning fix.

    restore_candidate commits the restore, then calls refresh_candidate_suggestions
    as a secondary operation.  If the refresh raises, the endpoint must still
    return 200 with an additive ``warning`` field rather than a false 400.
    """

    def test_restore_after_mark_returns_200(self):
        cid = self._import_candidate("rf-1", "Avery Stone 6pm", "2026-05-15T12:00:00-04:00")
        self._post(f"/api/review/candidates/{cid}/mark", {"classification": "personal"})
        _, captured = self._post(
            f"/api/review/candidates/{cid}/restore",
            {"reason": "Restoring"},
        )
        self.assertEqual(captured["status"], 200)

    def test_restore_no_session_returns_400(self):
        """Restore on a candidate without a session returns 400."""
        cid = self._import_candidate("rf-2", "Test event", "2026-05-15T12:00:00-04:00")
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

    def test_restore_succeeds_and_refresh_succeeds_returns_200_no_warning(self):
        cid = self._import_candidate("rf-3", "Avery Stone 6pm", "2026-05-15T12:00:00-04:00")
        self._post(f"/api/review/candidates/{cid}/mark", {"classification": "personal"})
        _, captured = self._post(
            f"/api/review/candidates/{cid}/restore",
            {"reason": "Restoring"},
        )
        self.assertEqual(captured["status"], 200)
        self.assertNotIn("warning", captured["payload"])

    def test_restore_succeeds_and_refresh_raises_returns_200_with_warning(self):
        from unittest.mock import patch
        cid = self._import_candidate("rf-4", "Avery Stone 6pm", "2026-05-15T12:00:00-04:00")
        self._post(f"/api/review/candidates/{cid}/mark", {"classification": "personal"})
        with patch("jordana_invoice.review_services.refresh_candidate_suggestions") as mock_refresh:
            mock_refresh.side_effect = RuntimeError("internal boom")
            _, captured = self._post(
                f"/api/review/candidates/{cid}/restore",
                {"reason": "Restoring"},
            )
        self.assertEqual(captured["status"], 200)
        self.assertIn("warning", captured["payload"])
        self.assertEqual(
            captured["payload"]["warning"],
            "Candidate was restored, but suggestions could not be refreshed.",
        )

    def test_restore_warning_is_sanitized_no_raw_exception(self):
        from unittest.mock import patch
        cid = self._import_candidate("rf-5", "Avery Stone 6pm", "2026-05-15T12:00:00-04:00")
        self._post(f"/api/review/candidates/{cid}/mark", {"classification": "personal"})
        with patch("jordana_invoice.review_services.refresh_candidate_suggestions") as mock_refresh:
            mock_refresh.side_effect = RuntimeError("SECRET_INTERNAL_DETAIL_xyz")
            _, captured = self._post(
                f"/api/review/candidates/{cid}/restore",
                {"reason": "Restoring"},
            )
        self.assertEqual(captured["status"], 200)
        warning = captured["payload"].get("warning", "")
        self.assertNotIn("SECRET_INTERNAL_DETAIL_xyz", warning)

    def test_restore_db_state_present_despite_refresh_failure(self):
        from unittest.mock import patch
        cid = self._import_candidate("rf-6", "Avery Stone 6pm", "2026-05-15T12:00:00-04:00")
        self._post(f"/api/review/candidates/{cid}/mark", {"classification": "personal"})
        with patch("jordana_invoice.review_services.refresh_candidate_suggestions") as mock_refresh:
            mock_refresh.side_effect = RuntimeError("boom")
            self._post(
                f"/api/review/candidates/{cid}/restore",
                {"reason": "Restoring"},
            )
        row = self.conn.execute(
            "SELECT review_status FROM calendar_event_candidates WHERE id = ?", (cid,)
        ).fetchone()
        self.assertEqual(row["review_status"], "needs_classification")

    def test_restore_no_duplicate_side_effects_on_repeat(self):
        from unittest.mock import patch
        cid = self._import_candidate("rf-7", "Avery Stone 6pm", "2026-05-15T12:00:00-04:00")
        self._post(f"/api/review/candidates/{cid}/mark", {"classification": "personal"})
        with patch("jordana_invoice.review_services.refresh_candidate_suggestions") as mock_refresh:
            mock_refresh.side_effect = RuntimeError("boom")
            self._post(f"/api/review/candidates/{cid}/restore", {"reason": "first"})
            mock_refresh.side_effect = RuntimeError("boom")
            self._post(f"/api/review/candidates/{cid}/restore", {"reason": "second"})
        session_count = self.conn.execute(
            "SELECT COUNT(*) AS c FROM sessions WHERE candidate_id = ?", (cid,)
        ).fetchone()["c"]
        self.assertEqual(session_count, 1)
        audit_count = self.conn.execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE entity_id = ? AND action = 'restored_to_review_queue'",
            (cid,),
        ).fetchone()["c"]
        self.assertEqual(audit_count, 2)

    def test_genuine_restore_failure_still_returns_400(self):
        cid = self._import_candidate("rf-8", "Test event", "2026-05-15T12:00:00-04:00")
        self.conn.execute("DELETE FROM sessions WHERE candidate_id = ?", (cid,))
        self.conn.commit()
        _, captured = self._post(
            f"/api/review/candidates/{cid}/restore",
            {"reason": "Restoring"},
        )
        self.assertEqual(captured["status"], 400)

    def test_refresh_not_attempted_if_restore_fails(self):
        from unittest.mock import patch
        cid = self._import_candidate("rf-9", "Test event", "2026-05-15T12:00:00-04:00")
        self.conn.execute("DELETE FROM sessions WHERE candidate_id = ?", (cid,))
        self.conn.commit()
        with patch("jordana_invoice.review_services.refresh_candidate_suggestions") as mock_refresh:
            _, captured = self._post(
                f"/api/review/candidates/{cid}/restore",
                {"reason": "Restoring"},
            )
        mock_refresh.assert_not_called()
        self.assertEqual(captured["status"], 400)


if __name__ == "__main__":
    unittest.main()
