"""Explicit request-parsing and validation helpers for high-risk review write endpoints.

This module provides lightweight, typed validation for the four scoped write workflows
in Round 4A.2:

1. Approve reviewed session
2. Save session draft or section-level session values
3. Confirm duplicate / duplicate resolution (mark endpoint)
4. Restore candidate

The parsers validate request shape and supported primitive values only.
Business validation (database-backed and billing-domain decisions) remains in
the service layer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class RequestValidationError(ValueError):
    """Raised when request shape validation fails.

    Messages are safe for user display and do not expose backend
    implementation details.
    """


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _require_object(payload: Any) -> dict[str, Any]:
    """Ensure the payload is a JSON object (dict)."""
    if not isinstance(payload, dict):
        raise RequestValidationError("Request body must be a JSON object.")
    return payload


def _optional_str(data: dict[str, Any], key: str) -> str | None:
    """Return a string value if present, or None. Reject non-string types."""
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise RequestValidationError(f"{key} must be a string.")
    return value


def _optional_nonempty_str(data: dict[str, Any], key: str) -> str | None:
    """Return a non-empty string if present, or None. Reject non-string types."""
    value = _optional_str(data, key)
    if value is not None and not value.strip():
        raise RequestValidationError(f"{key} must not be empty.")
    return value


def _optional_int_not_bool(data: dict[str, Any], key: str) -> int | None:
    """Return an integer if present, or None. Reject booleans and non-numeric types."""
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        raise RequestValidationError(f"{key} must be an integer, not a boolean.")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            raise RequestValidationError(f"{key} must be an integer.")
    raise RequestValidationError(f"{key} must be an integer.")


def _optional_str_or_int(data: dict[str, Any], key: str) -> str | int | None:
    """Return a string or integer if present, or None. Reject booleans and other types."""
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        raise RequestValidationError(f"{key} must be a string or integer, not a boolean.")
    if isinstance(value, (str, int)):
        return value
    raise RequestValidationError(f"{key} must be a string or integer.")


def _optional_list_of_dicts(
    data: dict[str, Any], key: str
) -> list[dict[str, Any]] | None:
    """Return a list of dicts if present, or None. Reject non-list or non-dict items."""
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, list):
        raise RequestValidationError(f"{key} must be a list.")
    for item in value:
        if not isinstance(item, dict):
            raise RequestValidationError(f"Each item in {key} must be an object.")
    return value


def _optional_dict(data: dict[str, Any], key: str) -> dict[str, Any] | None:
    """Return a dict if present, or None. Reject non-dict types."""
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RequestValidationError(f"{key} must be an object.")
    return value


def _optional_str_choice(
    data: dict[str, Any], key: str, allowed: frozenset[str]
) -> str | None:
    """Return a string from the allowed set if present, or None."""
    value = _optional_str(data, key)
    if value is not None and value not in allowed:
        raise RequestValidationError(
            f"{key} must be one of: {', '.join(sorted(allowed))}."
        )
    return value


def _required_str(data: dict[str, Any], key: str) -> str:
    """Return a required string value. Reject missing, non-string, or empty."""
    if key not in data:
        raise RequestValidationError(f"{key} is required.")
    value = data[key]
    if not isinstance(value, str):
        raise RequestValidationError(f"{key} must be a string.")
    if not value.strip():
        raise RequestValidationError(f"{key} must not be empty.")
    return value


def _required_int_not_bool(data: dict[str, Any], key: str) -> int:
    """Return a required integer. Reject missing, booleans, and non-numeric types."""
    if key not in data:
        raise RequestValidationError(f"{key} is required.")
    value = data[key]
    if isinstance(value, bool):
        raise RequestValidationError(f"{key} must be an integer, not a boolean.")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            raise RequestValidationError(f"{key} must be an integer.")
    raise RequestValidationError(f"{key} must be an integer.")


def _optional_bool(data: dict[str, Any], key: str) -> bool | None:
    """Return a boolean if present, or None. Reject non-boolean types."""
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise RequestValidationError(f"{key} must be a boolean.")
    return value


def _optional_list_of_strs(
    data: dict[str, Any], key: str
) -> list[str] | None:
    """Return a list of strings if present, or None. Reject non-list or non-string items."""
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, list):
        raise RequestValidationError(f"{key} must be a list.")
    for item in value:
        if not isinstance(item, str):
            raise RequestValidationError(f"Each item in {key} must be a string.")
    return value


def _reject_bool_if_present(data: dict[str, Any], key: str) -> None:
    """Reject boolean values where a non-boolean type is expected.

    Use for fields where the service does its own type checking but
    booleans would silently pass isinstance(x, int) in Python.
    """
    value = data.get(key)
    if isinstance(value, bool):
        raise RequestValidationError(f"{key} must not be a boolean.")


# ---------------------------------------------------------------------------
# Validated request types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ApproveSessionRequest:
    """Validated request for POST /api/review/candidates/{id}/approve.

    The parser validates shape and primitive types only. Business validation
    (confirmed participants, Bill To, duration, session type, rate, payment
    handling, etc.) remains in ``approve_candidate`` and related service code.

    Accepted legacy aliases:
    - ``duration_minutes`` as fallback for ``approved_duration_minutes``
    - ``approved_rate`` (string dollars or integer cents) for rate

    Unknown fields are passed through silently to preserve current behavior.
    """

    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class SaveSectionRequest:
    """Validated request for section-level save endpoints.

    Covers:
    - POST /api/review/candidates/{id}/save
    - POST /api/review/candidates/{id}/save-person
    - POST /api/review/candidates/{id}/save-relationship
    - POST /api/review/candidates/{id}/save-billing
    - POST /api/review/candidates/{id}/save-session

    Each endpoint-specific parser validates the fields relevant to that section.
    No section-level save approves a session, creates an invoice, or posts a payment.
    """

    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class MarkCandidateRequest:
    """Validated request for POST /api/review/candidates/{id}/mark.

    Used for duplicate resolution (classification="duplicate") and other
    classification actions (personal, administrative, nonbillable, client_session).

    The parser validates shape only. The service determines review_status
    from the classification value.
    """

    classification: str
    reason: str
    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class RestoreCandidateRequest:
    """Validated request for POST /api/review/candidates/{id}/restore.

    The parser validates shape only. The service checks for session existence
    and handles the restore logic.  Post-restore suggestion refresh failures
    are caught by the service and returned as an additive ``warning`` field
    on the success response, following the same convention used for
    invoice-staging warnings on the approve endpoint.
    """

    reason: str
    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


# ---------------------------------------------------------------------------
# Parser functions
# ---------------------------------------------------------------------------

def parse_approve_session_request(payload: Any) -> ApproveSessionRequest:
    """Parse and validate an approval request payload.

    Validates:
    - payload is a JSON object
    - participants (if present) is a list of objects
    - billing_party_id (if present) is a non-empty string
    - approved_duration_minutes (if present) is an integer (not boolean)
    - service_mode (if present) is a string
    - time_category (if present) is a string
    - approved_rate (if present) is a string or integer (not boolean)
    - payment_status (if present) is a string
    - billing_treatment (if present) is a string
    - amount_received (if present) is a string or integer (not boolean)
    - payment_date (if present) is a string
    - payment_method (if present) is a string
    - reference_number (if present) is a string
    - administrative_note (if present) is a string

    Legacy aliases:
    - duration_minutes is accepted as fallback for approved_duration_minutes
      (the service reads both)

    Unknown fields are passed through silently.

    Business validation remains in approve_candidate and the service layer.
    """
    data = _require_object(payload)

    _optional_list_of_dicts(data, "participants")
    _optional_nonempty_str(data, "billing_party_id")
    _optional_int_not_bool(data, "approved_duration_minutes")
    _optional_int_not_bool(data, "duration_minutes")
    _optional_str(data, "service_mode")
    _optional_str(data, "time_category")
    _optional_str_or_int(data, "approved_rate")
    _optional_str(data, "payment_status")
    _optional_str(data, "billing_treatment")
    _optional_str_or_int(data, "amount_received")
    _optional_str(data, "payment_date")
    _optional_str(data, "payment_method")
    _optional_str(data, "reference_number")
    _optional_str(data, "administrative_note")
    _optional_str(data, "billing_session_type")
    _optional_str(data, "rate_scope")
    _optional_str(data, "rate_override_reason")
    _optional_str(data, "account_id")
    _optional_str(data, "billable_status")
    _optional_str(data, "duration_choice")
    _optional_int_not_bool(data, "custom_duration_minutes")
    _optional_str(data, "custom_service_description")
    _optional_str(data, "custom_service_code")
    _optional_str_or_int(data, "suggested_rate")
    _optional_str(data, "appointment_method")

    return ApproveSessionRequest(payload=data)


def parse_save_interpretation_request(payload: Any) -> SaveSectionRequest:
    """Parse and validate a save-interpretation (full save) request payload.

    This is the general save endpoint that accepts all session fields.
    Shape validation mirrors what the service reads.
    """
    data = _require_object(payload)

    _optional_list_of_dicts(data, "participants")
    _optional_nonempty_str(data, "billing_party_id")
    _optional_nonempty_str(data, "account_id")
    _optional_int_not_bool(data, "approved_duration_minutes")
    _optional_int_not_bool(data, "duration_minutes")
    _optional_str(data, "service_mode")
    _optional_str(data, "time_category")
    _optional_str_or_int(data, "approved_rate")
    _optional_str(data, "payment_status")
    _optional_str(data, "billing_treatment")
    _optional_str(data, "billing_session_type")
    _optional_str(data, "rate_scope")
    _optional_str(data, "rate_override_reason")
    _optional_str(data, "billable_status")
    _optional_str(data, "duration_choice")
    _optional_int_not_bool(data, "custom_duration_minutes")
    _optional_str(data, "custom_service_description")
    _optional_str(data, "custom_service_code")
    _optional_str_or_int(data, "suggested_rate")
    _optional_str(data, "appointment_method")

    return SaveSectionRequest(payload=data)


def parse_save_person_section_request(payload: Any) -> SaveSectionRequest:
    """Parse and validate a save-person section request payload.

    Accepted fields: ``person`` (dict) or top-level person fields
    (person_id, first_name, last_name, display_name).
    """
    data = _require_object(payload)

    _optional_dict(data, "person")
    _optional_nonempty_str(data, "person_id")
    _optional_str(data, "first_name")
    _optional_str(data, "last_name")
    _optional_str(data, "display_name")

    return SaveSectionRequest(payload=data)


def parse_save_relationship_section_request(payload: Any) -> SaveSectionRequest:
    """Parse and validate a save-relationship section request payload.

    Accepted fields: participants (list of dicts), account_id,
    primary_person_id, default_billing_party_id, billing_party_id.
    """
    data = _require_object(payload)

    _optional_list_of_dicts(data, "participants")
    _optional_nonempty_str(data, "account_id")
    _optional_nonempty_str(data, "primary_person_id")
    _optional_nonempty_str(data, "default_billing_party_id")
    _optional_nonempty_str(data, "billing_party_id")

    return SaveSectionRequest(payload=data)


def parse_save_billing_section_request(payload: Any) -> SaveSectionRequest:
    """Parse and validate a save-billing section request payload.

    Accepted fields: billing_party_id, bill_to_person_id, billing_party (dict).
    """
    data = _require_object(payload)

    _optional_nonempty_str(data, "billing_party_id")
    _optional_nonempty_str(data, "bill_to_person_id")
    _optional_dict(data, "billing_party")

    return SaveSectionRequest(payload=data)


def parse_save_session_draft_request(payload: Any) -> SaveSectionRequest:
    """Parse and validate a save-session-draft request payload.

    Accepted fields: approved_duration_minutes, duration_minutes, duration_choice,
    custom_duration_minutes, billing_session_type, custom_service_description,
    custom_service_code, approved_rate, suggested_rate, rate_scope,
    rate_override_reason, payment_status, billing_treatment, billable_status,
    amount_received, payment_date, payment_method, reference_number,
    administrative_note.

    Time category is not independently editable here; the service derives it
    from the authoritative calendar date and start time.
    """
    data = _require_object(payload)

    _optional_int_not_bool(data, "approved_duration_minutes")
    _optional_int_not_bool(data, "duration_minutes")
    _optional_str(data, "duration_choice")
    _optional_int_not_bool(data, "custom_duration_minutes")
    _optional_str(data, "billing_session_type")
    _optional_str(data, "custom_service_description")
    _optional_str(data, "custom_service_code")
    _optional_str_or_int(data, "approved_rate")
    _optional_str_or_int(data, "suggested_rate")
    _optional_str(data, "rate_scope")
    _optional_str(data, "rate_override_reason")
    _optional_str(data, "payment_status")
    _optional_str(data, "billing_treatment")
    _optional_str(data, "billable_status")
    _optional_str_or_int(data, "amount_received")
    _optional_str(data, "payment_date")
    _optional_str(data, "payment_method")
    _optional_str(data, "reference_number")
    _optional_str(data, "administrative_note")
    _optional_str(data, "service_mode")

    return SaveSectionRequest(payload=data)


_MARK_CLASSIFICATIONS = frozenset({
    "personal",
    "administrative",
    "nonbillable",
    "duplicate",
    "client_session",
})


def parse_mark_candidate_request(payload: Any) -> MarkCandidateRequest:
    """Parse and validate a mark-candidate request payload.

    Accepted fields: classification (default "personal"), reason (default "").

    The classification value is validated against the known set of allowed
    values. The service determines review_status from the classification.
    """
    data = _require_object(payload)

    classification = _optional_str_choice(data, "classification", _MARK_CLASSIFICATIONS)
    if classification is None:
        classification = "personal"
    reason = _optional_str(data, "reason")
    if reason is None:
        reason = ""

    return MarkCandidateRequest(
        classification=classification,
        reason=reason,
        payload=data,
    )


def parse_restore_candidate_request(payload: Any) -> RestoreCandidateRequest:
    """Parse and validate a restore-candidate request payload.

    Accepted fields: reason (default "").

    The parser validates shape only. The service checks for session existence
    and handles the restore logic.
    """
    data = _require_object(payload)

    reason = _optional_str(data, "reason")
    if reason is None:
        reason = ""

    return RestoreCandidateRequest(reason=reason, payload=data)


# ===========================================================================
# Round 4A.3: Remaining write-endpoint request types and parsers
# ===========================================================================

# ---------------------------------------------------------------------------
# People, clients, aliases, merges
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CreatePersonRequest:
    """Validated request for POST /api/people."""

    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class UpdatePersonRequest:
    """Validated request for POST /api/people/{id}."""

    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class SavePersonAliasRequest:
    """Validated request for POST /api/people/{id}/aliases."""

    raw_alias: str
    approved_by_user: bool
    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class MergePeopleRequest:
    """Validated request for POST /api/people/{id}/merge."""

    duplicate_person_id: str
    reason: str
    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


def parse_create_person_request(payload: Any) -> CreatePersonRequest:
    """Parse POST /api/people.

    Accepted fields: display_name, first_name, last_name, preferred_name,
    billing_email, email, billing_phone, phone, administrative_notes.
    """
    data = _require_object(payload)
    _optional_str(data, "display_name")
    _optional_str(data, "first_name")
    _optional_str(data, "last_name")
    _optional_str(data, "preferred_name")
    _optional_str(data, "billing_email")
    _optional_str(data, "email")
    _optional_str(data, "billing_phone")
    _optional_str(data, "phone")
    _optional_str(data, "administrative_notes")
    return CreatePersonRequest(payload=data)


def parse_update_person_request(payload: Any) -> UpdatePersonRequest:
    """Parse POST /api/people/{id}.

    Accepted fields: display_name, first_name, last_name, preferred_name,
    person_code, billing_email, billing_phone, administrative_notes,
    active_status, active, account_id.
    """
    data = _require_object(payload)
    _optional_str(data, "display_name")
    _optional_str(data, "first_name")
    _optional_str(data, "last_name")
    _optional_str(data, "preferred_name")
    _optional_str(data, "person_code")
    _optional_str(data, "billing_email")
    _optional_str(data, "billing_phone")
    _optional_str(data, "administrative_notes")
    _optional_str(data, "active_status")
    _optional_bool(data, "active")
    _optional_str(data, "account_id")
    return UpdatePersonRequest(payload=data)


def parse_save_person_alias_request(payload: Any) -> SavePersonAliasRequest:
    """Parse POST /api/people/{id}/aliases.

    Accepted fields: raw_alias (required), approved_by_user (optional bool),
    alias_id (optional).
    """
    data = _require_object(payload)
    raw_alias = _required_str(data, "raw_alias")
    approved = _optional_bool(data, "approved_by_user")
    if approved is None:
        approved = True
    _optional_str(data, "alias_id")
    return SavePersonAliasRequest(
        raw_alias=raw_alias, approved_by_user=approved, payload=data,
    )


def parse_merge_people_request(payload: Any) -> MergePeopleRequest:
    """Parse POST /api/people/{id}/merge.

    Accepted fields: duplicate_person_id (required), reason (optional).
    """
    data = _require_object(payload)
    duplicate_person_id = _required_str(data, "duplicate_person_id")
    reason = _optional_str(data, "reason")
    if reason is None:
        reason = ""
    return MergePeopleRequest(
        duplicate_person_id=duplicate_person_id, reason=reason, payload=data,
    )


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CreateAccountRequest:
    """Validated request for POST /api/accounts."""

    account_name: str
    person_id: str
    account_type: str
    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class CreateAccountFromClientRequest:
    """Validated request for POST /api/accounts/from-client."""

    person_id: str
    account_name: str
    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class UpdateAccountRequest:
    """Validated request for POST /api/accounts/{id}."""

    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class UpdateBillingRelationshipRequest:
    """Validated request for POST /api/accounts/{id}/update-billing-relationship."""

    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class RemoveAccountMemberRequest:
    """Validated request for POST /api/accounts/{id}/remove-member."""

    person_id: str
    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class AddAccountMemberRequest:
    """Validated request for POST /api/account-members."""

    account_id: str
    person_id: str
    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


def parse_create_account_request(payload: Any) -> CreateAccountRequest:
    """Parse POST /api/accounts.

    Required: account_name.
    Optional: account_type (default "individual"), person_id.
    """
    data = _require_object(payload)
    account_name = _required_str(data, "account_name")
    _optional_str(data, "person_id")
    account_type = _optional_str(data, "account_type")
    if account_type is None:
        account_type = "individual"
    return CreateAccountRequest(
        account_name=account_name, person_id=data.get("person_id") or "",
        account_type=account_type, payload=data,
    )


def parse_create_account_from_client_request(payload: Any) -> CreateAccountFromClientRequest:
    """Parse POST /api/accounts/from-client.

    Required: person_id, account_name.
    """
    data = _require_object(payload)
    person_id = _required_str(data, "person_id")
    account_name = _required_str(data, "account_name")
    return CreateAccountFromClientRequest(
        person_id=person_id, account_name=account_name, payload=data,
    )


def parse_update_account_request(payload: Any) -> UpdateAccountRequest:
    """Parse POST /api/accounts/{id}.

    Accepted fields: account_name, account_type, default_billing_party_id,
    administrative_notes, active.
    """
    data = _require_object(payload)
    _optional_str(data, "account_name")
    _optional_str(data, "account_type")
    _optional_str(data, "default_billing_party_id")
    _optional_str(data, "administrative_notes")
    _optional_bool(data, "active")
    return UpdateAccountRequest(payload=data)


_PAYER_KINDS = frozenset({"client", "person", "organization"})


def parse_update_billing_relationship_request(payload: Any) -> UpdateBillingRelationshipRequest:
    """Parse POST /api/accounts/{id}/update-billing-relationship.

    Accepted fields: payer_kind, covered_client_ids, payer_person_id,
    organization_billing_party_id, delivery_method, billing_notes.
    """
    data = _require_object(payload)
    _optional_str_choice(data, "payer_kind", _PAYER_KINDS)
    _optional_list_of_strs(data, "covered_client_ids")
    _optional_str(data, "payer_person_id")
    _optional_str(data, "organization_billing_party_id")
    _optional_str(data, "delivery_method")
    _optional_str(data, "billing_notes")
    return UpdateBillingRelationshipRequest(payload=data)


def parse_remove_account_member_request(payload: Any) -> RemoveAccountMemberRequest:
    """Parse POST /api/accounts/{id}/remove-member.

    Required: person_id.
    """
    data = _require_object(payload)
    person_id = _required_str(data, "person_id")
    return RemoveAccountMemberRequest(person_id=person_id, payload=data)


def parse_add_account_member_request(payload: Any) -> AddAccountMemberRequest:
    """Parse POST /api/account-members.

    Required: account_id, person_id.
    Optional: relationship_role, is_primary.
    """
    data = _require_object(payload)
    account_id = _required_str(data, "account_id")
    person_id = _required_str(data, "person_id")
    _optional_str(data, "relationship_role")
    _optional_bool(data, "is_primary")
    return AddAccountMemberRequest(
        account_id=account_id, person_id=person_id, payload=data,
    )


# ---------------------------------------------------------------------------
# Billing relationships and parties
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SetupBillingRelationshipRequest:
    """Validated request for POST /api/billing-relationships/setup."""

    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class NormalizePayerRequest:
    """Validated request for POST /api/billing-relationships/normalize-payer."""

    person_id: str
    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class CreateBillingPartyRequest:
    """Validated request for POST /api/billing-parties."""

    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class UpdateBillingPartyRequest:
    """Validated request for POST /api/billing-parties/{id}."""

    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class CopyContactRequest:
    """Validated request for POST /api/billing-parties/{id}/copy-contact."""

    source_billing_party_id: str
    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


_DELIVERY_METHODS = frozenset({"email", "mail", "both", "unresolved"})
_BILLING_PARTY_TYPES = frozenset({"person", "organization"})


def parse_setup_billing_relationship_request(payload: Any) -> SetupBillingRelationshipRequest:
    """Parse POST /api/billing-relationships/setup.

    Accepted fields: payer_kind, covered_client_ids, payer_person_id,
    organization_billing_party_id.
    """
    data = _require_object(payload)
    _optional_str_choice(data, "payer_kind", _PAYER_KINDS)
    _optional_list_of_strs(data, "covered_client_ids")
    _optional_str(data, "payer_person_id")
    _optional_str(data, "organization_billing_party_id")
    return SetupBillingRelationshipRequest(payload=data)


def parse_normalize_payer_request(payload: Any) -> NormalizePayerRequest:
    """Parse POST /api/billing-relationships/normalize-payer.

    Required: person_id.
    Optional: canonical_billing_party_id.
    """
    data = _require_object(payload)
    person_id = _required_str(data, "person_id")
    _optional_str(data, "canonical_billing_party_id")
    return NormalizePayerRequest(person_id=person_id, payload=data)


def parse_create_billing_party_request(payload: Any) -> CreateBillingPartyRequest:
    """Parse POST /api/billing-parties.

    Accepted fields: billing_name, display_name, name, person_id,
    billing_party_type, preferred_delivery_method, organization_name,
    billing_email, billing_address_line_1, billing_address_line_2,
    billing_city, billing_state, billing_postal_code, billing_phone,
    administrative_notes.
    """
    data = _require_object(payload)
    _optional_str(data, "billing_name")
    _optional_str(data, "display_name")
    _optional_str(data, "name")
    _optional_str(data, "person_id")
    _optional_str_choice(data, "billing_party_type", _BILLING_PARTY_TYPES)
    _optional_str_choice(data, "preferred_delivery_method", _DELIVERY_METHODS)
    _optional_str(data, "organization_name")
    _optional_str(data, "billing_email")
    _optional_str(data, "billing_address_line_1")
    _optional_str(data, "billing_address_line_2")
    _optional_str(data, "billing_city")
    _optional_str(data, "billing_state")
    _optional_str(data, "billing_postal_code")
    _optional_str(data, "billing_phone")
    _optional_str(data, "administrative_notes")
    return CreateBillingPartyRequest(payload=data)


def parse_update_billing_party_request(payload: Any) -> UpdateBillingPartyRequest:
    """Parse POST /api/billing-parties/{id}.

    Same fields as create (all optional for partial update).
    """
    data = _require_object(payload)
    _optional_str(data, "billing_name")
    _optional_str_choice(data, "billing_party_type", _BILLING_PARTY_TYPES)
    _optional_str(data, "person_id")
    _optional_str_choice(data, "preferred_delivery_method", _DELIVERY_METHODS)
    _optional_str(data, "organization_name")
    _optional_str(data, "billing_email")
    _optional_str(data, "billing_address_line_1")
    _optional_str(data, "billing_address_line_2")
    _optional_str(data, "billing_city")
    _optional_str(data, "billing_state")
    _optional_str(data, "billing_postal_code")
    _optional_str(data, "billing_phone")
    _optional_str(data, "administrative_notes")
    _optional_bool(data, "active")
    return UpdateBillingPartyRequest(payload=data)


def parse_copy_contact_request(payload: Any) -> CopyContactRequest:
    """Parse POST /api/billing-parties/{id}/copy-contact.

    Required: source_billing_party_id.
    """
    data = _require_object(payload)
    source_billing_party_id = _required_str(data, "source_billing_party_id")
    return CopyContactRequest(
        source_billing_party_id=source_billing_party_id, payload=data,
    )


# ---------------------------------------------------------------------------
# Rate rules and previews
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CreateRateRuleRequest:
    """Validated request for POST /api/rate-rules."""

    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class PreviewRateRequest:
    """Validated request for POST /api/rate-rules/preview."""

    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class ReplaceRateRuleRequest:
    """Validated request for POST /api/rate-rules/{id}/replace."""

    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class EndRateRuleRequest:
    """Validated request for POST /api/rate-rules/{id}/end."""

    effective_through: str
    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


def parse_create_rate_rule_request(payload: Any) -> CreateRateRuleRequest:
    """Parse POST /api/rate-rules.

    Accepted fields: amount, duration_choice, custom_duration_minutes,
    duration_minutes, billing_session_type, custom_service_description,
    custom_service_code, time_category, effective_from, applies_to,
    client_account_id, account_id, person_id, participant_person_ids,
    appointment_status, priority.
    """
    data = _require_object(payload)
    _optional_str_or_int(data, "amount")
    _optional_str(data, "duration_choice")
    _optional_int_not_bool(data, "custom_duration_minutes")
    _optional_int_not_bool(data, "duration_minutes")
    _optional_str(data, "billing_session_type")
    _optional_str(data, "custom_service_description")
    _optional_str(data, "custom_service_code")
    _optional_str(data, "time_category")
    _optional_str(data, "effective_from")
    _optional_str(data, "applies_to")
    _optional_str(data, "client_account_id")
    _optional_str(data, "account_id")
    _optional_str(data, "person_id")
    _optional_list_of_strs(data, "participant_person_ids")
    _optional_str(data, "appointment_status")
    _optional_int_not_bool(data, "priority")
    return CreateRateRuleRequest(payload=data)


def parse_preview_rate_request(payload: Any) -> PreviewRateRequest:
    """Parse POST /api/rate-rules/preview.

    Same fields as create (read-only preview).
    """
    data = _require_object(payload)
    _optional_str_or_int(data, "amount")
    _optional_str(data, "duration_choice")
    _optional_int_not_bool(data, "custom_duration_minutes")
    _optional_int_not_bool(data, "duration_minutes")
    _optional_str(data, "billing_session_type")
    _optional_str(data, "custom_service_description")
    _optional_str(data, "custom_service_code")
    _optional_str(data, "time_category")
    _optional_str(data, "effective_from")
    _optional_str(data, "applies_to")
    _optional_str(data, "client_account_id")
    _optional_str(data, "account_id")
    _optional_str(data, "person_id")
    _optional_list_of_strs(data, "participant_person_ids")
    _optional_str(data, "appointment_status")
    return PreviewRateRequest(payload=data)


def parse_replace_rate_rule_request(payload: Any) -> ReplaceRateRuleRequest:
    """Parse POST /api/rate-rules/{id}/replace.

    Same fields as create.
    """
    data = _require_object(payload)
    _optional_str_or_int(data, "amount")
    _optional_str(data, "duration_choice")
    _optional_int_not_bool(data, "custom_duration_minutes")
    _optional_int_not_bool(data, "duration_minutes")
    _optional_str(data, "billing_session_type")
    _optional_str(data, "custom_service_description")
    _optional_str(data, "custom_service_code")
    _optional_str(data, "time_category")
    _optional_str(data, "effective_from")
    _optional_str(data, "applies_to")
    _optional_str(data, "client_account_id")
    _optional_str(data, "account_id")
    _optional_str(data, "person_id")
    _optional_list_of_strs(data, "participant_person_ids")
    _optional_str(data, "appointment_status")
    _optional_int_not_bool(data, "priority")
    return ReplaceRateRuleRequest(payload=data)


def parse_end_rate_rule_request(payload: Any) -> EndRateRuleRequest:
    """Parse POST /api/rate-rules/{id}/end.

    Required: effective_through.
    """
    data = _require_object(payload)
    effective_through = _required_str(data, "effective_through")
    return EndRateRuleRequest(
        effective_through=effective_through, payload=data,
    )


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CreateInvoiceDraftRequest:
    """Validated request for POST /api/invoices."""

    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class StageInvoicesRequest:
    """Validated request for POST /api/invoices/stage."""

    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class UpdateInvoiceDraftRequest:
    """Validated request for POST /api/invoices/{id}."""

    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class UpdateInvoiceLineItemRequest:
    """Validated request for POST /api/invoices/{id}/update-line."""

    invoice_line_item_id: str
    description: str
    amount_cents: int
    amount_scope: str
    reason: str
    expected_revision: int
    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class AddSessionsToDraftRequest:
    """Validated request for POST /api/invoices/{id}/add-sessions."""

    session_ids: list[str]
    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class RemoveLineFromDraftRequest:
    """Validated request for POST /api/invoices/{id}/remove-line."""

    invoice_line_item_id: str
    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class PreviewFinalizeRequest:
    """Validated request for POST /api/invoices/{id}/preview-finalize."""

    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class FinalizeInvoiceRequest:
    """Validated request for POST /api/invoices/{id}/finalize."""

    confirmed: bool
    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class VoidInvoiceRequest:
    """Validated request for POST /api/invoices/{id}/void."""

    reason: str
    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class UpdateInvoiceFilingOwnerRequest:
    """Validated request for POST /api/invoices/{id}/filing-owner."""

    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class DocumentActionRequest:
    """Validated request for POST /api/invoices/{id}/document-action
    and POST /api/payments/{id}/receipt-document-action.
    """

    action: str
    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class PrintPreviewRequest:
    """Validated request for POST /api/invoices/{id}/print-preview
    and POST /api/invoices/{id}/draft-pdf.
    """

    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


_AMOUNT_SCOPES = frozenset({"invoice_line_only", "invoice_line_and_session"})


def parse_create_invoice_draft_request(payload: Any) -> CreateInvoiceDraftRequest:
    """Parse POST /api/invoices.

    Accepted fields: bill_to_party_id, billing_month, billing_period_start,
    billing_period_end, delivery_method, supplement_sequence, invoice_date,
    notes, session_ids.
    """
    data = _require_object(payload)
    _optional_str(data, "bill_to_party_id")
    _optional_str(data, "billing_month")
    _optional_str(data, "billing_period_start")
    _optional_str(data, "billing_period_end")
    _optional_str_choice(data, "delivery_method", _DELIVERY_METHODS)
    _optional_int_not_bool(data, "supplement_sequence")
    _optional_str(data, "invoice_date")
    _optional_str(data, "notes")
    _optional_list_of_strs(data, "session_ids")
    return CreateInvoiceDraftRequest(payload=data)


def parse_stage_invoices_request(payload: Any) -> StageInvoicesRequest:
    """Parse POST /api/invoices/stage.

    Accepted fields: session_ids (optional list of non-empty strings).
    """
    data = _require_object(payload)
    session_ids = _optional_list_of_strs(data, "session_ids")
    if session_ids is not None:
        for sid in session_ids:
            if not sid.strip():
                raise RequestValidationError("Each session_id must be a non-empty string.")
    return StageInvoicesRequest(payload=data)


def parse_update_invoice_draft_request(payload: Any) -> UpdateInvoiceDraftRequest:
    """Parse POST /api/invoices/{id}.

    Accepted fields: billing_period_start, billing_period_end, billing_month,
    delivery_method, notes, supplement_sequence.
    """
    data = _require_object(payload)
    _optional_str(data, "billing_period_start")
    _optional_str(data, "billing_period_end")
    _optional_str(data, "billing_month")
    _optional_str_choice(data, "delivery_method", _DELIVERY_METHODS)
    _optional_str(data, "notes")
    _optional_int_not_bool(data, "supplement_sequence")
    return UpdateInvoiceDraftRequest(payload=data)


def parse_update_invoice_line_item_request(payload: Any) -> UpdateInvoiceLineItemRequest:
    """Parse POST /api/invoices/{id}/update-line.

    Required: invoice_line_item_id, description, amount_cents, amount_scope,
    reason, expected_revision.
    """
    data = _require_object(payload)
    invoice_line_item_id = _required_str(data, "invoice_line_item_id")
    description = _required_str(data, "description")
    amount_cents = _required_int_not_bool(data, "amount_cents")
    amount_scope = _required_str(data, "amount_scope")
    reason = _required_str(data, "reason")
    expected_revision = _required_int_not_bool(data, "expected_revision")
    return UpdateInvoiceLineItemRequest(
        invoice_line_item_id=invoice_line_item_id,
        description=description,
        amount_cents=amount_cents,
        amount_scope=amount_scope,
        reason=reason,
        expected_revision=expected_revision,
        payload=data,
    )


def parse_add_sessions_to_draft_request(payload: Any) -> AddSessionsToDraftRequest:
    """Parse POST /api/invoices/{id}/add-sessions.

    Required: session_ids (list of strings).
    """
    data = _require_object(payload)
    session_ids = _optional_list_of_strs(data, "session_ids")
    if session_ids is None:
        session_ids = []
    return AddSessionsToDraftRequest(session_ids=session_ids, payload=data)


def parse_remove_line_from_draft_request(payload: Any) -> RemoveLineFromDraftRequest:
    """Parse POST /api/invoices/{id}/remove-line.

    Required: invoice_line_item_id.
    """
    data = _require_object(payload)
    invoice_line_item_id = _required_str(data, "invoice_line_item_id")
    return RemoveLineFromDraftRequest(
        invoice_line_item_id=invoice_line_item_id, payload=data,
    )


def parse_preview_finalize_request(payload: Any) -> PreviewFinalizeRequest:
    """Parse POST /api/invoices/{id}/preview-finalize.

    Accepted fields: billing_period_start, billing_period_end, billing_month,
    delivery_method, notes, supplement_sequence.
    """
    data = _require_object(payload)
    _optional_str(data, "billing_period_start")
    _optional_str(data, "billing_period_end")
    _optional_str(data, "billing_month")
    _optional_str_choice(data, "delivery_method", _DELIVERY_METHODS)
    _optional_str(data, "notes")
    _optional_int_not_bool(data, "supplement_sequence")
    return PreviewFinalizeRequest(payload=data)


def parse_finalize_invoice_request(payload: Any) -> FinalizeInvoiceRequest:
    """Parse POST /api/invoices/{id}/finalize.

    Required: confirmed (must be true).
    Optional: expected_revision, insurance_coding_included, insurance_diagnosis_code.
    """
    data = _require_object(payload)
    confirmed = _optional_bool(data, "confirmed")
    if confirmed is None:
        confirmed = False
    _optional_int_not_bool(data, "expected_revision")
    _optional_bool(data, "insurance_coding_included")
    _optional_str(data, "insurance_diagnosis_code")
    return FinalizeInvoiceRequest(confirmed=confirmed, payload=data)


def parse_void_invoice_request(payload: Any) -> VoidInvoiceRequest:
    """Parse POST /api/invoices/{id}/void.

    Accepted fields: reason (string, defaults to empty — service validates non-empty).
    """
    data = _require_object(payload)
    reason = _optional_str(data, "reason")
    if reason is None:
        reason = ""
    return VoidInvoiceRequest(reason=reason, payload=data)


def parse_update_invoice_filing_owner_request(payload: Any) -> UpdateInvoiceFilingOwnerRequest:
    """Parse POST /api/invoices/{id}/filing-owner.

    Accepted fields: person_id (optional, can be None to clear).
    """
    data = _require_object(payload)
    _optional_str(data, "person_id")
    return UpdateInvoiceFilingOwnerRequest(payload=data)


def parse_document_action_request(payload: Any) -> DocumentActionRequest:
    """Parse POST /api/invoices/{id}/document-action and
    POST /api/payments/{id}/receipt-document-action.

    Accepted fields: action (string).
    """
    data = _require_object(payload)
    action = _optional_str(data, "action")
    if action is None:
        action = ""
    return DocumentActionRequest(action=action, payload=data)


def parse_print_preview_request(payload: Any) -> PrintPreviewRequest:
    """Parse POST /api/invoices/{id}/print-preview and
    POST /api/invoices/{id}/draft-pdf.

    Accepted fields: insurance_coding_included, insurance_diagnosis_code.
    """
    data = _require_object(payload)
    _optional_bool(data, "insurance_coding_included")
    _optional_str(data, "insurance_diagnosis_code")
    return PrintPreviewRequest(payload=data)


# ---------------------------------------------------------------------------
# Payments, allocations, receipts
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RecordPaymentRequest:
    """Validated request for POST /api/invoices/{id}/payments."""

    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class ReverseAllocationRequest:
    """Validated request for POST /api/payments/allocations/{id}/reverse."""

    reason: str
    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class ApplyFundsRequest:
    """Validated request for POST /api/payments/{id}/apply-funds."""

    invoice_id: str
    amount_cents: int
    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class VoidPaymentRequest:
    """Validated request for POST /api/payments/{id}/void."""

    reason: str
    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class CreatePaymentReceiptRequest:
    """Validated request for POST /api/payments/{id}/receipt."""

    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


def parse_record_payment_request(payload: Any) -> RecordPaymentRequest:
    """Parse POST /api/invoices/{id}/payments.

    Accepted fields: payment_date (string), amount_cents (integer),
    payment_method (string), reference_number, received_from_name,
    administrative_note.

    The parser validates types only; the service checks required fields
    and business rules.
    """
    data = _require_object(payload)
    _optional_str(data, "payment_date")
    _optional_int_not_bool(data, "amount_cents")
    _optional_str(data, "payment_method")
    _optional_str(data, "reference_number")
    _optional_str(data, "received_from_name")
    _optional_str(data, "administrative_note")
    return RecordPaymentRequest(payload=data)


def parse_reverse_allocation_request(payload: Any) -> ReverseAllocationRequest:
    """Parse POST /api/payments/allocations/{id}/reverse.

    Accepted fields: reason (string, defaults to empty — service validates non-empty).
    Optional: idempotency_key.
    """
    data = _require_object(payload)
    reason = _optional_str(data, "reason")
    if reason is None:
        reason = ""
    _optional_str(data, "idempotency_key")
    return ReverseAllocationRequest(reason=reason, payload=data)


def parse_apply_funds_request(payload: Any) -> ApplyFundsRequest:
    """Parse POST /api/payments/{id}/apply-funds.

    Required: invoice_id, amount_cents.
    Optional: idempotency_key.
    """
    data = _require_object(payload)
    invoice_id = _required_str(data, "invoice_id")
    amount_cents = _required_int_not_bool(data, "amount_cents")
    _optional_str(data, "idempotency_key")
    return ApplyFundsRequest(
        invoice_id=invoice_id, amount_cents=amount_cents, payload=data,
    )


def parse_void_payment_request(payload: Any) -> VoidPaymentRequest:
    """Parse POST /api/payments/{id}/void.

    Accepted fields: reason (string, defaults to empty — service validates non-empty).
    Optional: idempotency_key.
    """
    data = _require_object(payload)
    reason = _optional_str(data, "reason")
    if reason is None:
        reason = ""
    _optional_str(data, "idempotency_key")
    return VoidPaymentRequest(reason=reason, payload=data)


def parse_create_payment_receipt_request(payload: Any) -> CreatePaymentReceiptRequest:
    """Parse POST /api/payments/{id}/receipt.

    Optional: filing_owner_person_id.
    """
    data = _require_object(payload)
    _optional_str(data, "filing_owner_person_id")
    return CreatePaymentReceiptRequest(payload=data)


# ---------------------------------------------------------------------------
# Business profile
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SaveBusinessProfileRequest:
    """Validated request for POST /api/business-profile."""

    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


def parse_save_business_profile_request(payload: Any) -> SaveBusinessProfileRequest:
    """Parse POST /api/business-profile.

    Accepted fields: business_name, provider_display_name, credentials_display,
    address_line_1, address_line_2, city, state, postal_code, phone, email,
    payee_name, payment_address_line_1, payment_address_line_2, payment_city,
    payment_state, payment_postal_code, zelle_recipient, logo_path,
    logo_contains_business_details, show_email_below_logo,
    invoice_total_label, invoice_number_format, insurance_ein, insurance_npi,
    insurance_sw.
    """
    data = _require_object(payload)
    _optional_str(data, "business_name")
    _optional_str(data, "provider_display_name")
    _optional_str(data, "credentials_display")
    _optional_str(data, "address_line_1")
    _optional_str(data, "address_line_2")
    _optional_str(data, "city")
    _optional_str(data, "state")
    _optional_str(data, "postal_code")
    _optional_str(data, "phone")
    _optional_str(data, "email")
    _optional_str(data, "payee_name")
    _optional_str(data, "payment_address_line_1")
    _optional_str(data, "payment_address_line_2")
    _optional_str(data, "payment_city")
    _optional_str(data, "payment_state")
    _optional_str(data, "payment_postal_code")
    _optional_str(data, "zelle_recipient")
    _optional_str(data, "logo_path")
    _optional_bool(data, "logo_contains_business_details")
    _optional_bool(data, "show_email_below_logo")
    _optional_str(data, "invoice_total_label")
    _optional_str(data, "invoice_number_format")
    _optional_str(data, "insurance_ein")
    _optional_str(data, "insurance_npi")
    _optional_str(data, "insurance_sw")
    return SaveBusinessProfileRequest(payload=data)


# ---------------------------------------------------------------------------
# Calendar sync and import
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SyncRunRequest:
    """Validated request for POST /api/sync/run."""

    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


@dataclass(frozen=True)
class SyncRebuildRequest:
    """Validated request for POST /api/sync/rebuild."""

    confirmed: bool
    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return self.payload


def parse_sync_run_request(payload: Any) -> SyncRunRequest:
    """Parse POST /api/sync/run.

    No accepted fields (body is read but not used).
    """
    data = _require_object(payload)
    return SyncRunRequest(payload=data)


def parse_sync_rebuild_request(payload: Any) -> SyncRebuildRequest:
    """Parse POST /api/sync/rebuild.

    Required: confirmed (must be true).
    """
    data = _require_object(payload)
    confirmed = _optional_bool(data, "confirmed")
    if confirmed is None:
        confirmed = False
    return SyncRebuildRequest(confirmed=confirmed, payload=data)
