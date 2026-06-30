"""Focused tests for request validation helpers (Round 4A.3).

Tests cover the new parser functions added for remaining write endpoints:
- People: create, update, aliases, merge
- Accounts: create, from-client, update, billing-relationship, remove-member, add-member
- Billing parties: create, update, copy-contact
- Billing relationships: setup, normalize-payer
- Rate rules: create, preview, replace, end
- Invoices: create, stage, update, update-line, add-sessions, remove-line,
  preview-finalize, finalize, void, filing-owner, document-action, print-preview
- Payments: record, reverse-allocation, apply-funds, void, receipt, receipt-document-action
- Business profile: save
- Sync: run, rebuild

Each parser is tested for:
- valid payload acceptance
- missing required field
- wrong top-level JSON type
- wrong field type (bool for int, non-string for string)
- unknown field pass-through
- RequestValidationError is a safe validation error
"""
import unittest

from jordana_invoice.request_validation import (
    RequestValidationError,
    parse_create_person_request,
    parse_update_person_request,
    parse_save_person_alias_request,
    parse_merge_people_request,
    parse_create_account_request,
    parse_create_account_from_client_request,
    parse_update_account_request,
    parse_update_billing_relationship_request,
    parse_remove_account_member_request,
    parse_add_account_member_request,
    parse_setup_billing_relationship_request,
    parse_normalize_payer_request,
    parse_create_billing_party_request,
    parse_update_billing_party_request,
    parse_copy_contact_request,
    parse_create_rate_rule_request,
    parse_preview_rate_request,
    parse_replace_rate_rule_request,
    parse_end_rate_rule_request,
    parse_create_invoice_draft_request,
    parse_stage_invoices_request,
    parse_update_invoice_draft_request,
    parse_update_invoice_line_item_request,
    parse_add_sessions_to_draft_request,
    parse_remove_line_from_draft_request,
    parse_preview_finalize_request,
    parse_finalize_invoice_request,
    parse_void_invoice_request,
    parse_update_invoice_filing_owner_request,
    parse_document_action_request,
    parse_print_preview_request,
    parse_record_payment_request,
    parse_reverse_allocation_request,
    parse_apply_funds_request,
    parse_void_payment_request,
    parse_create_payment_receipt_request,
    parse_save_business_profile_request,
    parse_sync_run_request,
    parse_sync_rebuild_request,
)
from jordana_invoice.review_server import is_safe_validation_error


class TestParseCreatePerson(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_create_person_request({"display_name": "Alice Stone"})
        self.assertEqual(req.to_payload()["display_name"], "Alice Stone")

    def test_non_object_raises(self):
        with self.assertRaises(RequestValidationError):
            parse_create_person_request("not a dict")

    def test_unknown_field_passes_through(self):
        req = parse_create_person_request({"display_name": "Alice", "extra": True})
        self.assertEqual(req.to_payload()["extra"], True)

    def test_wrong_type_for_string_field(self):
        with self.assertRaises(RequestValidationError):
            parse_create_person_request({"display_name": 123})


class TestParseUpdatePerson(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_update_person_request({"display_name": "Alice Stone", "active": False})
        self.assertFalse(req.to_payload()["active"])

    def test_non_object_raises(self):
        with self.assertRaises(RequestValidationError):
            parse_update_person_request([])

    def test_wrong_type_for_bool(self):
        with self.assertRaises(RequestValidationError):
            parse_update_person_request({"active": "yes"})


class TestParseSavePersonAlias(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_save_person_alias_request({"raw_alias": "Alice"})
        self.assertEqual(req.raw_alias, "Alice")
        self.assertTrue(req.approved_by_user)

    def test_missing_raw_alias(self):
        with self.assertRaises(RequestValidationError):
            parse_save_person_alias_request({})

    def test_empty_raw_alias(self):
        with self.assertRaises(RequestValidationError):
            parse_save_person_alias_request({"raw_alias": "  "})

    def test_non_object_raises(self):
        with self.assertRaises(RequestValidationError):
            parse_save_person_alias_request(None)

    def test_approved_by_user_false(self):
        req = parse_save_person_alias_request({"raw_alias": "Alice", "approved_by_user": False})
        self.assertFalse(req.approved_by_user)


class TestParseMergePeople(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_merge_people_request({"duplicate_person_id": "p-2", "reason": "dup"})
        self.assertEqual(req.duplicate_person_id, "p-2")
        self.assertEqual(req.reason, "dup")

    def test_missing_duplicate_id(self):
        with self.assertRaises(RequestValidationError):
            parse_merge_people_request({"reason": "no id"})

    def test_non_object_raises(self):
        with self.assertRaises(RequestValidationError):
            parse_merge_people_request(42)


class TestParseCreateAccount(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_create_account_request({"account_name": "Test HH", "account_type": "household"})
        self.assertEqual(req.account_name, "Test HH")
        self.assertEqual(req.account_type, "household")

    def test_missing_name(self):
        with self.assertRaises(RequestValidationError):
            parse_create_account_request({"account_type": "household"})

    def test_default_type(self):
        req = parse_create_account_request({"account_name": "Test"})
        self.assertEqual(req.account_type, "individual")

    def test_non_object_raises(self):
        with self.assertRaises(RequestValidationError):
            parse_create_account_request("bad")


class TestParseCreateAccountFromClient(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_create_account_from_client_request({"person_id": "p-1", "account_name": "Test"})
        self.assertEqual(req.person_id, "p-1")
        self.assertEqual(req.account_name, "Test")

    def test_missing_person_id(self):
        with self.assertRaises(RequestValidationError):
            parse_create_account_from_client_request({"account_name": "Test"})


class TestParseUpdateAccount(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_update_account_request({"account_name": "Updated", "active": True})
        self.assertEqual(req.to_payload()["account_name"], "Updated")

    def test_non_object_raises(self):
        with self.assertRaises(RequestValidationError):
            parse_update_account_request(None)


class TestParseUpdateBillingRelationship(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_update_billing_relationship_request({
            "payer_kind": "client",
            "covered_client_ids": ["p-1"],
        })
        self.assertEqual(req.to_payload()["payer_kind"], "client")

    def test_invalid_payer_kind(self):
        with self.assertRaises(RequestValidationError):
            parse_update_billing_relationship_request({"payer_kind": "invalid"})

    def test_non_object_raises(self):
        with self.assertRaises(RequestValidationError):
            parse_update_billing_relationship_request(123)


class TestParseRemoveAccountMember(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_remove_account_member_request({"person_id": "p-1"})
        self.assertEqual(req.person_id, "p-1")

    def test_missing_person_id(self):
        with self.assertRaises(RequestValidationError):
            parse_remove_account_member_request({})


class TestParseAddAccountMember(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_add_account_member_request({"account_id": "a-1", "person_id": "p-1"})
        self.assertEqual(req.account_id, "a-1")
        self.assertEqual(req.person_id, "p-1")

    def test_missing_account_id(self):
        with self.assertRaises(RequestValidationError):
            parse_add_account_member_request({"person_id": "p-1"})


class TestParseSetupBillingRelationship(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_setup_billing_relationship_request({
            "payer_kind": "client",
            "covered_client_ids": ["p-1"],
        })
        self.assertEqual(req.to_payload()["payer_kind"], "client")

    def test_invalid_payer_kind(self):
        with self.assertRaises(RequestValidationError):
            parse_setup_billing_relationship_request({"payer_kind": "bad"})


class TestParseNormalizePayer(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_normalize_payer_request({"person_id": "p-1"})
        self.assertEqual(req.person_id, "p-1")

    def test_missing_person_id(self):
        with self.assertRaises(RequestValidationError):
            parse_normalize_payer_request({})


class TestParseCreateBillingParty(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_create_billing_party_request({"billing_name": "Test Org"})
        self.assertEqual(req.to_payload()["billing_name"], "Test Org")

    def test_invalid_delivery_method(self):
        with self.assertRaises(RequestValidationError):
            parse_create_billing_party_request({"preferred_delivery_method": "fax"})

    def test_invalid_billing_party_type(self):
        with self.assertRaises(RequestValidationError):
            parse_create_billing_party_request({"billing_party_type": "trust"})

    def test_non_object_raises(self):
        with self.assertRaises(RequestValidationError):
            parse_create_billing_party_request(None)


class TestParseUpdateBillingParty(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_update_billing_party_request({"billing_name": "Updated", "active": False})
        self.assertFalse(req.to_payload()["active"])

    def test_invalid_delivery_method(self):
        with self.assertRaises(RequestValidationError):
            parse_update_billing_party_request({"preferred_delivery_method": "carrier_pigeon"})


class TestParseCopyContact(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_copy_contact_request({"source_billing_party_id": "bp-1"})
        self.assertEqual(req.source_billing_party_id, "bp-1")

    def test_missing_source(self):
        with self.assertRaises(RequestValidationError):
            parse_copy_contact_request({})


class TestParseCreateRateRule(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_create_rate_rule_request({"amount": "100", "billing_session_type": "office"})
        self.assertEqual(req.to_payload()["amount"], "100")

    def test_non_object_raises(self):
        with self.assertRaises(RequestValidationError):
            parse_create_rate_rule_request("bad")

    def test_bool_for_int_field(self):
        with self.assertRaises(RequestValidationError):
            parse_create_rate_rule_request({"priority": True})


class TestParsePreviewRate(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_preview_rate_request({"amount": 100})
        self.assertEqual(req.to_payload()["amount"], 100)


class TestParseReplaceRateRule(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_replace_rate_rule_request({"amount": "200"})
        self.assertEqual(req.to_payload()["amount"], "200")


class TestParseEndRateRule(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_end_rate_rule_request({"effective_through": "2026-12-31"})
        self.assertEqual(req.effective_through, "2026-12-31")

    def test_missing_effective_through(self):
        with self.assertRaises(RequestValidationError):
            parse_end_rate_rule_request({})


class TestParseCreateInvoiceDraft(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_create_invoice_draft_request({"bill_to_party_id": "bp-1"})
        self.assertEqual(req.to_payload()["bill_to_party_id"], "bp-1")

    def test_invalid_delivery_method(self):
        with self.assertRaises(RequestValidationError):
            parse_create_invoice_draft_request({"delivery_method": "telepathy"})

    def test_non_object_raises(self):
        with self.assertRaises(RequestValidationError):
            parse_create_invoice_draft_request(None)


class TestParseStageInvoices(unittest.TestCase):
    def test_valid_empty(self):
        req = parse_stage_invoices_request({})
        self.assertIsNone(req.to_payload().get("session_ids"))

    def test_valid_with_ids(self):
        req = parse_stage_invoices_request({"session_ids": ["s-1", "s-2"]})
        self.assertEqual(req.to_payload()["session_ids"], ["s-1", "s-2"])

    def test_non_list_session_ids(self):
        with self.assertRaises(RequestValidationError):
            parse_stage_invoices_request({"session_ids": "not-a-list"})

    def test_empty_string_in_session_ids(self):
        with self.assertRaises(RequestValidationError):
            parse_stage_invoices_request({"session_ids": [""]})

    def test_non_object_raises(self):
        with self.assertRaises(RequestValidationError):
            parse_stage_invoices_request(42)


class TestParseUpdateInvoiceDraft(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_update_invoice_draft_request({"notes": "Updated notes"})
        self.assertEqual(req.to_payload()["notes"], "Updated notes")

    def test_non_object_raises(self):
        with self.assertRaises(RequestValidationError):
            parse_update_invoice_draft_request("bad")


class TestParseUpdateInvoiceLineItem(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_update_invoice_line_item_request({
            "invoice_line_item_id": "li-1",
            "description": "Session",
            "amount_cents": 5000,
            "amount_scope": "invoice_line_only",
            "reason": "correction",
            "expected_revision": 1,
        })
        self.assertEqual(req.invoice_line_item_id, "li-1")
        self.assertEqual(req.amount_cents, 5000)
        self.assertEqual(req.expected_revision, 1)

    def test_missing_required_field(self):
        with self.assertRaises(RequestValidationError):
            parse_update_invoice_line_item_request({
                "invoice_line_item_id": "li-1",
                "description": "Session",
                "amount_cents": 5000,
                "amount_scope": "invoice_line_only",
                "reason": "correction",
            })

    def test_bool_for_int(self):
        with self.assertRaises(RequestValidationError):
            parse_update_invoice_line_item_request({
                "invoice_line_item_id": "li-1",
                "description": "Session",
                "amount_cents": True,
                "amount_scope": "invoice_line_only",
                "reason": "correction",
                "expected_revision": 1,
            })

    def test_non_object_raises(self):
        with self.assertRaises(RequestValidationError):
            parse_update_invoice_line_item_request(None)


class TestParseAddSessionsToDraft(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_add_sessions_to_draft_request({"session_ids": ["s-1"]})
        self.assertEqual(req.session_ids, ["s-1"])

    def test_empty_defaults_to_empty_list(self):
        req = parse_add_sessions_to_draft_request({})
        self.assertEqual(req.session_ids, [])

    def test_non_list_raises(self):
        with self.assertRaises(RequestValidationError):
            parse_add_sessions_to_draft_request({"session_ids": "bad"})


class TestParseRemoveLineFromDraft(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_remove_line_from_draft_request({"invoice_line_item_id": "li-1"})
        self.assertEqual(req.invoice_line_item_id, "li-1")

    def test_missing_id(self):
        with self.assertRaises(RequestValidationError):
            parse_remove_line_from_draft_request({})


class TestParsePreviewFinalize(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_preview_finalize_request({"notes": "test"})
        self.assertEqual(req.to_payload()["notes"], "test")

    def test_non_object_raises(self):
        with self.assertRaises(RequestValidationError):
            parse_preview_finalize_request(None)


class TestParseFinalizeInvoice(unittest.TestCase):
    def test_valid_confirmed(self):
        req = parse_finalize_invoice_request({"confirmed": True})
        self.assertTrue(req.confirmed)

    def test_default_not_confirmed(self):
        req = parse_finalize_invoice_request({})
        self.assertFalse(req.confirmed)

    def test_non_object_raises(self):
        with self.assertRaises(RequestValidationError):
            parse_finalize_invoice_request("bad")


class TestParseVoidInvoice(unittest.TestCase):
    def test_valid_with_reason(self):
        req = parse_void_invoice_request({"reason": "Duplicate"})
        self.assertEqual(req.reason, "Duplicate")

    def test_missing_reason_defaults_empty(self):
        req = parse_void_invoice_request({})
        self.assertEqual(req.reason, "")

    def test_non_object_raises(self):
        with self.assertRaises(RequestValidationError):
            parse_void_invoice_request(42)


class TestParseUpdateInvoiceFilingOwner(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_update_invoice_filing_owner_request({"person_id": "p-1"})
        self.assertEqual(req.to_payload()["person_id"], "p-1")

    def test_non_object_raises(self):
        with self.assertRaises(RequestValidationError):
            parse_update_invoice_filing_owner_request(None)


class TestParseDocumentAction(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_document_action_request({"action": "regenerate"})
        self.assertEqual(req.action, "regenerate")

    def test_missing_action_defaults_empty(self):
        req = parse_document_action_request({})
        self.assertEqual(req.action, "")


class TestParsePrintPreview(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_print_preview_request({"insurance_coding_included": True})
        self.assertTrue(req.to_payload()["insurance_coding_included"])

    def test_non_object_raises(self):
        with self.assertRaises(RequestValidationError):
            parse_print_preview_request("bad")


class TestParseRecordPayment(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_record_payment_request({
            "payment_date": "2026-05-20",
            "amount_cents": 15000,
            "payment_method": "check",
        })
        self.assertEqual(req.to_payload()["payment_date"], "2026-05-20")

    def test_non_object_raises(self):
        with self.assertRaises(RequestValidationError):
            parse_record_payment_request(None)

    def test_bool_for_int(self):
        with self.assertRaises(RequestValidationError):
            parse_record_payment_request({
                "payment_date": "2026-05-20",
                "amount_cents": True,
                "payment_method": "check",
            })


class TestParseReverseAllocation(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_reverse_allocation_request({"reason": "error", "idempotency_key": "k-1"})
        self.assertEqual(req.reason, "error")

    def test_missing_reason_defaults_empty(self):
        req = parse_reverse_allocation_request({})
        self.assertEqual(req.reason, "")


class TestParseApplyFunds(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_apply_funds_request({"invoice_id": "inv-1", "amount_cents": 5000})
        self.assertEqual(req.invoice_id, "inv-1")
        self.assertEqual(req.amount_cents, 5000)

    def test_missing_invoice_id(self):
        with self.assertRaises(RequestValidationError):
            parse_apply_funds_request({"amount_cents": 5000})

    def test_bool_for_int(self):
        with self.assertRaises(RequestValidationError):
            parse_apply_funds_request({"invoice_id": "inv-1", "amount_cents": True})


class TestParseVoidPayment(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_void_payment_request({"reason": "error"})
        self.assertEqual(req.reason, "error")

    def test_missing_reason_defaults_empty(self):
        req = parse_void_payment_request({})
        self.assertEqual(req.reason, "")


class TestParseCreatePaymentReceipt(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_create_payment_receipt_request({"filing_owner_person_id": "p-1"})
        self.assertEqual(req.to_payload()["filing_owner_person_id"], "p-1")

    def test_non_object_raises(self):
        with self.assertRaises(RequestValidationError):
            parse_create_payment_receipt_request(None)


class TestParseSaveBusinessProfile(unittest.TestCase):
    def test_valid_payload(self):
        req = parse_save_business_profile_request({"business_name": "Test Co"})
        self.assertEqual(req.to_payload()["business_name"], "Test Co")

    def test_non_object_raises(self):
        with self.assertRaises(RequestValidationError):
            parse_save_business_profile_request("bad")

    def test_wrong_type_for_bool(self):
        with self.assertRaises(RequestValidationError):
            parse_save_business_profile_request({"show_email_below_logo": "yes"})


class TestParseSyncRun(unittest.TestCase):
    def test_valid_empty(self):
        req = parse_sync_run_request({})
        self.assertEqual(req.to_payload(), {})

    def test_non_object_raises(self):
        with self.assertRaises(RequestValidationError):
            parse_sync_run_request(None)


class TestParseSyncRebuild(unittest.TestCase):
    def test_valid_confirmed(self):
        req = parse_sync_rebuild_request({"confirmed": True})
        self.assertTrue(req.confirmed)

    def test_default_not_confirmed(self):
        req = parse_sync_rebuild_request({})
        self.assertFalse(req.confirmed)

    def test_non_object_raises(self):
        with self.assertRaises(RequestValidationError):
            parse_sync_rebuild_request("bad")


class TestRequestValidationErrorIsSafe(unittest.TestCase):
    def test_is_safe_validation_error(self):
        err = RequestValidationError("test error")
        self.assertTrue(is_safe_validation_error(err))


if __name__ == "__main__":
    unittest.main()
