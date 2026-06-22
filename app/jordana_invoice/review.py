from __future__ import annotations

import sqlite3

from .parser import ParseResult
from .util import json_dumps, new_id, now_iso, text


def review_status_for_parse(result: ParseResult, rate_needs_review: bool = True) -> str:
    fields = set(result.unresolved_fields or result.fields_requiring_review)
    if result.classification in {"personal", "administrative", "cancelled", "nonbillable"}:
        return "excluded" if result.confidence_label == "excluded" and not fields else "needs_classification"
    if result.classification == "unresolved":
        return "needs_classification"
    if "participants" in fields:
        return "needs_participants"
    if "client_full_name" in fields:
        return "needs_person_match"
    if "billing_party" in fields:
        return "needs_billing_party"
    if "duration_discrepancy" in fields:
        return "needs_duration"
    if "service_mode" in fields:
        return "needs_service_mode"
    if rate_needs_review:
        return "needs_rate"
    if "payment_status" in fields:
        return "needs_payment_status"
    return "ready_for_approval"


def unresolved_fields_for_session(result: ParseResult, rate_needs_review: bool = True) -> list[str]:
    fields = set(result.unresolved_fields or result.fields_requiring_review)
    fields.discard("client_account")
    if rate_needs_review and result.classification == "client_session":
        fields.add("rate")
    fields.add("payment_status")
    return sorted(fields)


def record_review_decision(
    conn: sqlite3.Connection,
    *,
    candidate_id: str | None = None,
    session_id: str | None = None,
    review_status: str,
    decision_payload: dict[str, object],
    decision_source: str = "developer_cli",
    reason: str = "",
) -> str:
    now = now_iso()
    review_item_id = new_id()
    conn.execute(
        """
        INSERT INTO review_items (
          review_item_id, candidate_id, session_id, review_status,
          unresolved_fields, review_reasons, decision_payload,
          reviewed_at, decision_source, reason, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            review_item_id,
            candidate_id,
            session_id,
            review_status,
            json_dumps(decision_payload.get("unresolved_fields", [])),
            json_dumps(decision_payload.get("review_reasons", [])),
            json_dumps(decision_payload),
            now,
            decision_source,
            reason,
            now,
            now,
        ),
    )
    if candidate_id:
        conn.execute(
            "UPDATE calendar_event_candidates SET review_status = ?, updated_at = ? WHERE id = ?",
            (review_status, now, candidate_id),
        )
    if session_id:
        conn.execute(
            "UPDATE sessions SET review_status = ?, updated_at = ? WHERE id = ?",
            (review_status, now, session_id),
        )
    conn.execute(
        """
        INSERT INTO audit_log (id, entity_type, entity_id, action, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            new_id(),
            "review_item",
            review_item_id,
            "decision_recorded",
            json_dumps({"candidate_id": candidate_id, "session_id": session_id, "review_status": review_status}),
            now,
        ),
    )
    return review_item_id
