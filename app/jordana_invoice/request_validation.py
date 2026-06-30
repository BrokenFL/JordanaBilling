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
    and handles the restore logic.

    Known inconsistency: ``restore_candidate`` commits the restore, then calls
    ``refresh_candidate_suggestions`` which may raise an unsafe exception. The
    handler sanitizes this to a 400 response even though the restore succeeded.
    This is documented as a deferred issue for a later round.
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
