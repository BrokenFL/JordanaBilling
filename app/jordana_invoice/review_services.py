from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .appointment_ledger import list_appointment_ledger_page
from .backfill import backfill_phase2
from .calendar_preferences import classify_calendar, upsert_calendar_preference
from .csv_reports import write_reports
from .db import DatabaseBusyError
from .rates import (
    cents_to_dollars,
    dollars_to_cents,
    normalize_custom_service_code,
    normalize_custom_service_description,
    seed_rate_rule,
    suggest_rate,
)
from .service_catalog import learn_service
from .session_types import (
    ALLOWED_BILLING_SESSION_TYPES,
    ALLOWED_DURATION_CHOICES,
    BILLING_SESSION_TYPE_OPTIONS,
    DURATION_CHOICE_OPTIONS,
    appointment_status_label,
    get_user_facing_session_label,
    normalize_attendance_outcome,
    normalize_billing_treatment_for_outcome,
    rate_rule_appointment_status_for_session,
    validate_rate_rule_appointment_status,
    validate_billing_session_type,
    validate_duration_choice,
    duration_choice_to_minutes,
)
from .importer import apply_calendar_signal, initial_billing_treatment, maybe_insert_session
from .parser import parse_event
from .review import review_status_for_parse
from .payment_services import _invoice_balance_summary, _invoice_paid_amount, client_account_summary
from .util import json_dumps, new_id, now_iso, normalize_payment_status, parse_int, text


def init_db(_conn: sqlite3.Connection) -> None:
    """No-op; schema migrations run explicitly at startup via migrate_database()."""
    pass


class BillingPartyNotFoundError(ValueError):
    """Raised when a billing party ID does not match any row."""


class BillingPartyTypeError(ValueError):
    """Raised when a billing party exists but is the wrong type for the requested endpoint."""


REQUIRED_APPROVAL_FIELDS = {
    "participants",
    "billing_party_id",
    "approved_duration_minutes",
    "service_mode",
    "time_category",
    "approved_rate_cents",
}

EASTERN_TZ = ZoneInfo("America/New_York")


def get_session_type_options() -> list[dict[str, str]]:
    """Return the exactly 5 allowed billing session type options for UI dropdowns."""
    return list(BILLING_SESSION_TYPE_OPTIONS)


def get_duration_options() -> list[dict[str, str]]:
    """Return the exactly 5 allowed duration choice options for UI dropdowns."""
    return list(DURATION_CHOICE_OPTIONS)


def _coerce_charge_for_attendance(
    appointment_status: str | None,
    billing_treatment: str | None,
    approved_rate_cents: int | None,
    suggested_rate_cents: int | None,
    session: sqlite3.Row | dict[str, Any],
) -> tuple[str, str, int | None, int | None]:
    outcome = normalize_attendance_outcome(appointment_status)
    treatment = normalize_billing_treatment_for_outcome(outcome, billing_treatment)
    current_scheduled = (
        session["scheduled_rate_cents"]
        if "scheduled_rate_cents" in session.keys()  # type: ignore[attr-defined]
        else None
    )
    scheduled_rate_cents = suggested_rate_cents
    if scheduled_rate_cents is None:
        scheduled_rate_cents = current_scheduled
    if scheduled_rate_cents is None:
        scheduled_rate_cents = session["suggested_rate_cents"]

    if outcome == "late_cancellation":
        if treatment == "waived":
            return outcome, treatment, 0, scheduled_rate_cents
        if treatment == "bill_full_fee":
            return outcome, treatment, approved_rate_cents if approved_rate_cents is not None else scheduled_rate_cents, scheduled_rate_cents
        if treatment == "custom_fee":
            return outcome, treatment, approved_rate_cents, scheduled_rate_cents
        return outcome, treatment, approved_rate_cents, scheduled_rate_cents

    return outcome, treatment, approved_rate_cents, scheduled_rate_cents


def dashboard_status(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT review_status, COUNT(*) AS count
        FROM sessions
        GROUP BY review_status
        """
    ).fetchall()
    counts = {row["review_status"]: int(row["count"]) for row in rows}
    personal_admin = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM calendar_event_candidates
        WHERE classification IN ('personal', 'administrative', 'nonbillable')
        """
    ).fetchone()["count"]
    last_sync = conn.execute(
        "SELECT last_success_at FROM sync_state WHERE source_name = 'google_calendar_snapshots'"
    ).fetchone()
    demo = conn.execute(
        "SELECT metadata_value FROM app_metadata WHERE metadata_key = 'demo_mode'"
    ).fetchone()
    return {
        "demo_mode": bool(demo and demo["metadata_value"].lower() == "true"),
        "last_sync": last_sync["last_success_at"] if last_sync else "",
        "needs_review": sum(
            counts.get(status, 0)
            for status in (
                "needs_classification",
                "needs_person_match",
                "needs_account",
                "needs_participants",
                "needs_billing_party",
                "needs_duration",
                "needs_service_mode",
                "needs_rate",
                "needs_review",
            )
        ),
        "ready_to_approve": counts.get("ready_for_approval", 0),
        "approved_this_month": counts.get("approved", 0),
        "personal_admin": int(personal_admin),
    }


def review_readiness(
    conn: sqlite3.Connection,
    row: sqlite3.Row | dict[str, Any],
    participants: list[dict[str, Any]],
) -> dict[str, Any]:
    values = dict(row)
    clients_ready = bool(participants) and all(
        participant.get("person_id") and not participant.get("is_proposed")
        for participant in participants
    )
    effective_billing_party_id, billing_party_source = effective_billing_party_lookup(
        conn,
        values.get("billing_party_id"),
        values.get("account_id"),
        participants,
    )
    billing_ready = bool(effective_billing_party_id)
    duration_known = bool(values.get("approved_duration_minutes") or values.get("duration_minutes"))
    billing_type = values.get("billing_session_type") or map_service_mode_to_billing_type(values.get("service_mode"))
    session_type_known = billing_type in ALLOWED_BILLING_SESSION_TYPES
    time_category_known = bool(values.get("time_category"))
    cancellation_needed = values.get("appointment_status") in {"cancelled", "no_show"}
    cancellation_ready = not cancellation_needed or values.get("billing_treatment") not in {"", None, "unresolved"}
    rate_ready = bool(values.get("approved_rate_cents")) or (
        values.get("suggested_rate_cents") is not None
        and values.get("rate_rule_id")
        and not values.get("rate_needs_review")
    )
    session_ready = all(
        [
            duration_known,
            session_type_known,
            time_category_known,
            cancellation_ready,
            rate_ready,
        ]
    )
    authority_score = 0
    authority_reasons: list[str] = []
    if clients_ready:
        authority_score += 30
        authority_reasons.append("Known client")
    if billing_ready:
        authority_score += 20
        authority_reasons.append("Saved payer")
    if duration_known:
        authority_score += 10
    if session_type_known:
        authority_score += 10
    if time_category_known:
        authority_score += 5
    if values.get("suggested_rate_cents") is not None and values.get("rate_rule_id") and not values.get("rate_needs_review"):
        authority_score += 15
        authority_reasons.append(f"Exact {time_label_for_reason(values.get('time_category'))} rate")
    if values.get("title_time_matches_calendar") == 0:
        authority_score = min(authority_score, 75)
    if values.get("review_status") == "approved":
        authority_score = 100
    return {
        "clients_ready": clients_ready,
        "billing_ready": billing_ready,
        "session_ready": session_ready,
        "all_ready": clients_ready and billing_ready and session_ready,
        "authority_score": authority_score,
        "authority_reasons": authority_reasons,
        "billing_party_source": billing_party_source,
        "effective_billing_party_id": effective_billing_party_id,
    }


def effective_billing_party_lookup(
    conn: sqlite3.Connection,
    session_billing_party_id: str | None,
    account_id: str | None,
    participants: list[dict[str, Any]],
) -> tuple[str | None, str]:
    if session_billing_party_id:
        return session_billing_party_id, "session"
    if account_id:
        account = conn.execute(
            """
            SELECT bp.billing_party_id
            FROM client_accounts ca
            JOIN billing_parties bp ON bp.billing_party_id = ca.default_billing_party_id
            WHERE ca.account_id = ? AND ca.active = 1 AND bp.active = 1
            """,
            (account_id,),
        ).fetchone()
        if account:
            return account["billing_party_id"], "account_default"
    person_ids = sorted({participant.get("person_id") for participant in participants if participant.get("person_id")})
    if len(person_ids) == 1:
        rows = conn.execute(
            """
            SELECT billing_party_id
            FROM billing_parties
            WHERE person_id = ? AND active = 1
            ORDER BY updated_at DESC
            """,
            (person_ids[0],),
        ).fetchall()
        if len(rows) == 1:
            return rows[0]["billing_party_id"], "person_default"
    return None, "unresolved"


def map_service_mode_to_billing_type(service_mode: str | None) -> str | None:
    normalized = normalize_service_mode(service_mode)
    if normalized == "unknown":
        return None
    return "psychotherapy_house_call" if normalized == "house_call" else "psychotherapy"


def time_label_for_reason(time_category: str | None) -> str:
    return {
        "evening": "evening",
        "weekend": "weekend",
        "weekend_evening": "weekend-evening",
    }.get(text(time_category), "standard")


def add_calendar_filter(filters: list[str], params: list[Any], calendar_filter: str, alias: str) -> None:
    if calendar_filter == "preferred_work":
        filters.append(f"COALESCE({alias}.calendar_is_preferred_work, 0) = 1")
    elif calendar_filter == "other":
        filters.append(f"COALESCE({alias}.calendar_is_preferred_work, 0) = 0")
        filters.append(f"COALESCE({alias}.hidden_from_review, 0) = 0")
    elif calendar_filter == "personal_admin":
        filters.append(f"{alias}.calendar_disposition = 'usually_personal_admin'")
    elif calendar_filter == "hidden":
        filters.append(f"COALESCE({alias}.hidden_from_review, 0) = 1")
    elif calendar_filter == "all":
        return
    else:
        filters.append(f"COALESCE({alias}.hidden_from_review, 0) = 0")


def list_review_candidates(
    conn: sqlite3.Connection,
    *,
    query: str = "",
    review_status: str = "",
    service_mode: str = "",
    billing_session_type: str = "",
    time_category: str = "",
    payment_status: str = "",
    calendar_filter: str = "",
    limit: int = 25,
    offset: int = 0,
) -> dict[str, Any]:
    filters = []
    params: list[Any] = []
    if query:
        filters.append(
            "(s.raw_calendar_title LIKE ? OR s.proposed_client_name LIKE ? OR c.title LIKE ?)"
        )
        like = f"%{query}%"
        params.extend([like, like, like])
    if review_status:
        filters.append("s.review_status = ?")
        params.append(review_status)
    else:
        filters.append("s.review_status NOT IN ('excluded', 'approved')")
    if billing_session_type:
        filters.append("s.billing_session_type = ?")
        params.append(billing_session_type)
    elif service_mode:
        filters.append("s.service_mode = ?")
        params.append(service_mode)
    if time_category:
        filters.append("s.time_category = ?")
        params.append(time_category)
    if payment_status:
        filters.append("s.payment_status = ?")
        params.append(payment_status)
    if calendar_filter:
        add_calendar_filter(filters, params, calendar_filter, "s")
    where = "WHERE " + " AND ".join(filters) if filters else ""
    session_total = conn.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM sessions s
        JOIN calendar_event_candidates c ON c.id = s.candidate_id
        {where}
        """,
        params,
    ).fetchone()["count"]
    rows = conn.execute(
        f"""
        SELECT
          s.id AS session_id,
          s.candidate_id,
          s.session_date,
          s.start_at,
          s.end_at,
          s.duration_minutes,
          s.service_mode,
          s.billing_session_type,
          s.custom_service_description,
          s.time_category,
          s.payment_status,
          s.appointment_status,
          s.billing_treatment,
          s.review_status,
          s.raw_calendar_title,
          s.suggested_rate_cents,
          s.approved_rate_cents,
          s.calendar_name,
          s.calendar_disposition,
          s.calendar_is_preferred_work,
          s.hidden_from_review,
          s.title_time_text,
          s.title_time_normalized,
          s.title_time_matches_calendar,
          c.classification,
          c.confidence,
          c.candidate_person_names,
          c.review_reasons,
          a.account_name,
          a.account_code
        FROM sessions s
        JOIN calendar_event_candidates c ON c.id = s.candidate_id
        LEFT JOIN client_accounts a ON a.account_id = s.account_id
        {where}
        ORDER BY s.start_at DESC, s.raw_calendar_title
        LIMIT ? OFFSET ?
        """,
        (*params, limit, offset),
    ).fetchall()
    items = [row_summary(conn, row) for row in rows]
    candidate_only = list_candidate_only_rows(conn, query=query, review_status=review_status, calendar_filter=calendar_filter)
    if offset == 0:
        items.extend(candidate_only[: max(0, limit - len(items))])
    return {
        "total": int(session_total) + len(candidate_only),
        "items": items,
        "status": dashboard_status(conn),
    }


def list_sessions_ledger(
    conn: sqlite3.Connection,
    *,
    date_range: str = "rolling_30",
    review_status: str = "",
    payment_status: str = "",
    limit: int = 30,
    offset: int = 0,
) -> dict[str, Any]:
    init_db(conn)
    backfill_phase2(conn)
    apply_smart_prefill(conn)
    return list_appointment_ledger_page(
        conn,
        date_range=date_range,
        review_status=review_status,
        payment_status=payment_status,
        limit=limit,
        offset=offset,
    )


def get_review_candidate(conn: sqlite3.Connection, candidate_id: str) -> dict[str, Any]:
    init_db(conn)
    backfill_phase2(conn)
    apply_smart_prefill(conn)
    row = conn.execute(
        """
        SELECT
          s.*,
          c.title,
          c.classification,
          c.confidence,
          c.confidence_label,
          c.explanation,
          c.fields_requiring_review,
          c.unresolved_fields,
          c.review_reasons,
          c.candidate_person_names,
          c.possible_referenced_person,
          c.parser_payload,
          r.calendar_name,
          r.captured_at,
          r.notes,
          r.raw_json
        FROM sessions s
        JOIN calendar_event_candidates c ON c.id = s.candidate_id
        JOIN raw_calendar_snapshots r ON r.id = s.source_raw_snapshot_id
        WHERE s.candidate_id = ?
        """,
        (candidate_id,),
    ).fetchone()
    if not row:
        return get_candidate_only(conn, candidate_id)
    participants = get_session_participants(conn, row["id"])
    display_participants = participants
    all_null = participants and all(p.get("person_id") is None for p in participants)
    if (not display_participants or all_null) and not participants_were_explicitly_saved(conn, row["id"]):
        display_participants = proposed_participants_from_candidate(conn, row)
    readiness = review_readiness(conn, row, display_participants)
    effective_billing_party = get_billing_party(conn, readiness["effective_billing_party_id"])
    saved_billing_party = get_billing_party(conn, row["billing_party_id"])
    return {
        "session": {**dict(row), "authority_score": readiness["authority_score"], "authority_reasons": readiness["authority_reasons"]},
        "participants": display_participants,
        "account": get_account(conn, row["account_id"]),
        "account_members": get_account_members(conn, row["account_id"]),
        "billing_party": saved_billing_party or effective_billing_party,
        "effective_billing_party": effective_billing_party,
        "checklist": checklist_for(row, display_participants, readiness),
        "readiness": readiness,
        "audit": audit_history(conn, row["id"], row["candidate_id"]),
    }


def row_summary(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    participants = get_session_participants(conn, row["session_id"])
    all_null_summary = participants and all(p.get("person_id") is None for p in participants)
    if all_null_summary and not participants_were_explicitly_saved(conn, row["session_id"]):
        proposed = proposed_participants_from_candidate(conn, row)
        participant_names = [p.get("display_name") or p.get("participant_name") for p in proposed]
    else:
        participant_names = [p.get("display_name") or p.get("participant_name") for p in participants]
    candidate_people = parse_json(row["candidate_person_names"], [])
    suggested = "; ".join(participant_names or candidate_people)
    account_name = row["account_name"] or suggested
    readiness = review_readiness(conn, row, participants if participants and not all_null_summary else proposed_participants_from_candidate(conn, row))
    return {
        "session_id": row["session_id"],
        "candidate_id": row["candidate_id"],
        "status": row["review_status"],
        "date": row["session_date"] or text(row["start_at"])[:10],
        "time": start_time(row["start_at"]),
        "raw_title": row["raw_calendar_title"] or "",
        "suggested_client": suggested,
        "account_name": account_name,
        "account_code": row["account_code"] or "",
        "duration_minutes": row["duration_minutes"],
        "service_mode": row["service_mode"] or "unknown",
        "billing_session_type": row["billing_session_type"] if "billing_session_type" in row.keys() else None,
        "custom_service_description": row["custom_service_description"] if "custom_service_description" in row.keys() else None,
        "time_category": row["time_category"] or "standard",
        "payment_status": normalize_payment_status(row["payment_status"]),
        "appointment_status": row["appointment_status"] or "unresolved",
        "billing_treatment": row["billing_treatment"] or "unresolved",
        "calendar_name": row["calendar_name"] or "",
        "calendar_disposition": row["calendar_disposition"] or "review_normally",
        "calendar_is_preferred_work": bool(row["calendar_is_preferred_work"]),
        "hidden_from_review": bool(row["hidden_from_review"]),
        "title_time_text": row["title_time_text"] or "",
        "title_time_normalized": row["title_time_normalized"] or "",
        "title_time_matches_calendar": row["title_time_matches_calendar"],
        "rate": cents_to_dollars(row["approved_rate_cents"] or row["suggested_rate_cents"]),
        "confidence": round(float(row["confidence"] or 0) * 100),
        "authority_score": readiness["authority_score"],
        "authority_reasons": readiness["authority_reasons"],
        "billing_party_source": readiness["billing_party_source"],
        "classification": row["classification"],
        "review_reasons": parse_json(row["review_reasons"], []),
    }


def list_candidate_only_rows(
    conn: sqlite3.Connection,
    query: str = "",
    review_status: str = "",
    calendar_filter: str = "",
) -> list[dict[str, Any]]:
    if calendar_filter in ("personal_admin", "all", "hidden"):
        class_sql = "c.classification IN ('personal', 'administrative', 'nonbillable', 'cancelled', 'no_show', 'unresolved')"
    else:
        class_sql = "c.classification IN ('unresolved', 'cancelled', 'no_show')"
    filters = [
        "c.id NOT IN (SELECT candidate_id FROM sessions)",
        class_sql,
    ]
    params: list[Any] = []
    if query:
        filters.append("(c.title LIKE ? OR c.possible_referenced_person LIKE ?)")
        like = f"%{query}%"
        params.extend([like, like])
    if review_status:
        filters.append("c.review_status = ?")
        params.append(review_status)
    else:
        filters.append("c.review_status NOT IN ('excluded', 'approved')")
    if calendar_filter:
        add_calendar_filter(filters, params, calendar_filter, "c")
    rows = conn.execute(
        f"""
        SELECT c.*, r.calendar_name
        FROM calendar_event_candidates c
        JOIN raw_calendar_snapshots r ON r.id = c.latest_raw_snapshot_id
        WHERE {" AND ".join(filters)}
        ORDER BY c.start_at DESC, c.title
        LIMIT 50
        """,
        params,
    ).fetchall()
    return [candidate_only_summary(row) for row in rows]


def candidate_only_summary(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "session_id": None,
        "candidate_id": row["id"],
        "status": row["review_status"],
        "date": text(row["start_at"])[:10],
        "time": start_time(row["start_at"]),
        "raw_title": row["title"] or "",
        "suggested_client": row["possible_referenced_person"] or row["classification"].replace("_", " ").title(),
        "account_name": "Personal/Admin" if row["classification"] in {"personal", "administrative", "nonbillable"} else "Unclassified",
        "account_code": "",
        "duration_minutes": row["proposed_duration_minutes"] or row["calendar_duration_minutes"] or "",
        "service_mode": row["service_mode"] or "unknown",
        "time_category": row["time_category"] or "standard",
        "payment_status": "unpaid",
        "appointment_status": row["appointment_status"] or "unresolved",
        "billing_treatment": row["billing_treatment"] or "not_billable",
        "calendar_name": row["calendar_name"] or "",
        "calendar_disposition": row["calendar_disposition"] or "review_normally",
        "calendar_is_preferred_work": bool(row["calendar_is_preferred_work"]),
        "hidden_from_review": bool(row["hidden_from_review"]),
        "title_time_text": row["title_time_text"] or "",
        "title_time_normalized": row["title_time_normalized"] or "",
        "title_time_matches_calendar": row["title_time_matches_calendar"],
        "rate": "",
        "confidence": round(float(row["confidence"] or 0) * 100),
        "classification": row["classification"],
        "review_reasons": parse_json(row["review_reasons"], []),
    }


def get_candidate_only(conn: sqlite3.Connection, candidate_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT c.*, r.calendar_name, r.captured_at, r.notes, r.raw_json
        FROM calendar_event_candidates c
        JOIN raw_calendar_snapshots r ON r.id = c.latest_raw_snapshot_id
        WHERE c.id = ?
        """,
        (candidate_id,),
    ).fetchone()
    if not row:
        raise ValueError("Review candidate not found.")
    session = {
        "id": None,
        "candidate_id": row["id"],
        "raw_calendar_title": row["title"],
        "title": row["title"],
        "session_date": text(row["start_at"])[:10],
        "start_at": row["start_at"],
        "end_at": row["end_at"],
        "duration_minutes": row["proposed_duration_minutes"] or row["calendar_duration_minutes"],
        "calendar_duration_minutes": row["calendar_duration_minutes"],
        "service_mode": row["service_mode"] or "unknown",
        "time_category": row["time_category"] or "standard",
        "payment_status": "unpaid",
        "appointment_status": row["appointment_status"] or "unresolved",
        "billing_treatment": row["billing_treatment"] or "not_billable",
        "title_time_text": row["title_time_text"] or "",
        "title_time_normalized": row["title_time_normalized"] or "",
        "title_time_matches_calendar": row["title_time_matches_calendar"],
        "calendar_disposition": row["calendar_disposition"] or "review_normally",
        "calendar_is_preferred_work": bool(row["calendar_is_preferred_work"]),
        "hidden_from_review": bool(row["hidden_from_review"]),
        "review_status": row["review_status"],
        "classification": row["classification"],
        "confidence": row["confidence"],
        "review_reasons": row["review_reasons"],
        "explanation": row["explanation"],
        "calendar_name": row["calendar_name"],
        "captured_at": row["captured_at"],
        "notes": row["notes"],
    }
    return {
        "session": session,
        "participants": proposed_participants_from_candidate(conn, row),
        "account": None,
        "account_members": [],
        "billing_party": None,
        "checklist": [
            {"label": "Classification confirmed", "resolved": row["review_status"] in {"excluded", "approved"}},
            {"label": "Reusable alias decision", "resolved": False},
        ],
        "audit": audit_history(conn, "", row["id"]),
    }


def _save_interpretation_locked(conn: sqlite3.Connection, candidate_id: str, payload: dict[str, Any], now: str) -> dict[str, Any]:
    session = session_for_candidate(conn, candidate_id)
    session_id = session["id"]
    before = dict(session)
    participants = payload.get("participants", [])
    account_id = payload.get("account_id") or None
    billing_party_id = payload.get("billing_party_id") or None
    approved_rate_cents = money_payload_to_cents(payload.get("approved_rate"))
    suggested_rate_cents = money_payload_to_cents(payload.get("suggested_rate"))
    duration = int(payload.get("approved_duration_minutes") or payload.get("duration_minutes") or session["duration_minutes"])
    service_mode = normalize_service_mode(payload.get("service_mode") or session["service_mode"])
    service = learn_service(conn, service_mode, increment_usage=False)
    time_category = normalize_time_category(payload.get("time_category") or session["time_category"])
    raw_billing_type = payload.get("billing_session_type") or (session["billing_session_type"] if "billing_session_type" in session.keys() else None) or "psychotherapy"
    billing_session_type = validate_billing_session_type(raw_billing_type)
    appointment_method = payload.get("appointment_method") or (session["appointment_method"] if "appointment_method" in session.keys() else None) or derive_appointment_method_from_service(service_mode)
    raw_duration_choice = payload.get("duration_choice") or (session["duration_choice"] if "duration_choice" in session.keys() else None) or derive_duration_choice_from_minutes(duration)
    raw_custom_minutes = int(payload["custom_duration_minutes"]) if payload.get("custom_duration_minutes") else None
    duration_choice, custom_duration_minutes = validate_duration_choice(raw_duration_choice, raw_custom_minutes)
    custom_service_description = payload.get("custom_service_description") or None
    custom_service_code = payload.get("custom_service_code") or None
    appointment_status = normalize_attendance_outcome(
        payload.get("appointment_status") or session["appointment_status"]
    )
    payment_status = normalize_payment_status(payload.get("payment_status") or session["payment_status"])
    billable_status = payload.get("billable_status") or session["billable_status"] or "proposed"
    billing_treatment = payload.get("billing_treatment") or session["billing_treatment"] or "unresolved"
    appointment_status, billing_treatment, approved_rate_cents, scheduled_rate_cents = _coerce_charge_for_attendance(
        appointment_status,
        billing_treatment,
        approved_rate_cents,
        suggested_rate_cents,
        session,
    )
    rate_override_reason = payload.get("rate_override_reason") or None
    rate_scope = payload.get("rate_scope") or "session_only"
    approved_source = approved_rate_source_for(session, approved_rate_cents, rate_scope)
    exact_existing_rate = bool(session["suggested_rate_cents"] is not None and session["rate_rule_id"] and not session["rate_needs_review"])

    conn.execute("DELETE FROM session_participants WHERE session_id = ?", (session_id,))
    for index, participant in enumerate(participants):
        participant_name = participant.get("display_name") or participant.get("participant_name")
        person_id = resolve_confirmed_participant_person(conn, participant.get("person_id"), participant_name)
        conn.execute(
            """
            INSERT INTO session_participants (
              session_participant_id, session_id, person_id, participant_name,
              participant_role, is_primary, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id(),
                session_id,
                person_id,
                participant_name,
                participant.get("participant_role") or ("primary" if index == 0 else "participant"),
                1 if participant.get("is_primary", index == 0) else 0,
                now,
                now,
            ),
        )
        if account_id and person_id:
            ensure_account_member(
                conn,
                account_id,
                person_id,
                participant.get("relationship_role") or ("primary" if index == 0 else "family_member"),
                bool(participant.get("is_primary", index == 0)),
            )
    if account_id and billing_party_id:
        conn.execute(
            """
            UPDATE client_accounts
            SET default_billing_party_id = COALESCE(default_billing_party_id, ?),
                updated_at = ?
            WHERE account_id = ?
            """,
            (billing_party_id, now, account_id),
        )
    unresolved = unresolved_from_values(
        conn=conn,
        participants=participants,
        billing_party_id=billing_party_id,
        account_id=account_id,
        duration=duration,
        billing_session_type=billing_session_type,
        service_mode=service_mode,
        time_category=time_category,
        approved_rate_cents=approved_rate_cents,
        suggested_rate_cents=suggested_rate_cents,
        rate_rule_id=session["rate_rule_id"],
        rate_needs_review=0 if approved_rate_cents is not None else session["rate_needs_review"],
        payment_status=payment_status,
        appointment_status=appointment_status,
        billing_treatment=billing_treatment,
        custom_service_description=custom_service_description,
    )
    review_status = "ready_for_approval" if not unresolved else status_from_unresolved(unresolved)
    conn.execute(
        """
        UPDATE sessions
        SET account_id = ?,
            billing_party_id = ?,
            approved_duration_minutes = ?,
            duration_minutes = ?,
            service_mode = ?,
            service_catalog_id = ?,
            rate_group = ?,
            time_category = ?,
            billing_session_type = ?,
            appointment_method = ?,
            duration_choice = ?,
            custom_duration_minutes = ?,
            custom_service_description = ?,
            custom_service_code = ?,
            billing_type_source = ?,
            suggested_rate_cents = COALESCE(?, suggested_rate_cents),
            scheduled_rate_cents = COALESCE(?, scheduled_rate_cents),
            approved_rate_cents = ?,
            approved_rate_source = ?,
            approved_rate_rule_id = CASE WHEN ? = 'manual_override' THEN NULL ELSE approved_rate_rule_id END,
            rate_needs_review = ?,
            rate_override_reason = ?,
            payment_status = ?,
            billable_status = ?,
            appointment_status = ?,
            billing_treatment = ?,
            review_status = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            account_id,
            billing_party_id,
            duration,
            duration,
            service_mode,
            service["service_catalog_id"],
            rate_group_for(service_mode),
            time_category,
            billing_session_type,
            appointment_method,
            duration_choice,
            custom_duration_minutes,
            custom_service_description,
            custom_service_code,
            "manual" if payload.get("billing_session_type") else (session["billing_type_source"] if "billing_type_source" in session.keys() else None) or "auto",
            suggested_rate_cents,
            scheduled_rate_cents,
            approved_rate_cents,
            approved_source,
            approved_source,
            0 if approved_rate_cents is not None or exact_existing_rate else 1,
            rate_override_reason,
            payment_status,
            billable_status,
            appointment_status,
            billing_treatment,
            review_status,
            now,
            session_id,
        ),
    )
    maybe_save_rate_scope(conn, session, payload, approved_rate_cents)
    conn.execute(
        "UPDATE calendar_event_candidates SET review_status = ?, updated_at = ? WHERE id = ?",
        (review_status, now, candidate_id),
    )
    record_audit(
        conn,
        "session",
        session_id,
        "interpretation_saved",
        {"before": scrub_session(before), "payload": payload, "unresolved_fields": unresolved},
    )
    add_review_item(conn, candidate_id, session_id, review_status, unresolved, ["Saved changes from review UI."])
    return get_review_candidate(conn, candidate_id)


def save_interpretation(conn: sqlite3.Connection, candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    init_db(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        res = _save_interpretation_locked(conn, candidate_id, payload, now_iso())
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    try:
        write_reports(conn)
    except Exception:
        pass

    return res


def approve_candidate(conn: sqlite3.Connection, candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    init_db(conn)

    # 1. Idempotency/recovery check for already approved session
    candidate_row = conn.execute("SELECT review_status FROM calendar_event_candidates WHERE id = ?", (candidate_id,)).fetchone()
    if candidate_row and candidate_row["review_status"] == "approved":
        session = session_for_candidate(conn, candidate_id)
        if session["payment_status"] == "paid_at_session":
            from .payment_services import record_or_validate_paid_at_session_payment_locked
            
            try:
                existing_payment = conn.execute(
                    "SELECT * FROM payments WHERE source_type = 'paid_at_session_backfill' AND source_session_id = ?",
                    (session["id"],),
                ).fetchone()

                if existing_payment is None:
                    # Case A: no payment exists. Must have confirmed details in payload. Do not invent.
                    amount_received_str = payload.get("amount_received")
                    payment_date = payload.get("payment_date")
                    payment_method = payload.get("payment_method")

                    if amount_received_str is None or str(amount_received_str).strip() == "":
                        raise ValueError("Amount received is required for paid-at-session recovery.")
                    if not payment_date or not str(payment_date).strip():
                        raise ValueError("Payment date is required for paid-at-session recovery.")
                    if not payment_method or not str(payment_method).strip():
                        raise ValueError("Payment method is required for paid-at-session recovery.")

                    amount_cents = money_payload_to_cents(amount_received_str)
                    charge_cents = (
                        session["rate_cents_snapshot"]
                        if session["rate_cents_snapshot"] is not None
                        else session["approved_rate_cents"]
                    )
                    if amount_cents is None or amount_cents <= 0:
                        raise ValueError("Payment amount must be greater than zero.")
                    if charge_cents is not None and amount_cents != charge_cents:
                        raise ValueError("Amount received must exactly equal the approved session charge.")

                    method_val = str(payment_method).strip().lower()
                else:
                    # Case B/C: payment exists. Validate against session charge.
                    charge_cents = (
                        session["rate_cents_snapshot"]
                        if session["rate_cents_snapshot"] is not None
                        else session["approved_rate_cents"]
                    )
                    if charge_cents is not None and existing_payment["amount_cents"] != charge_cents:
                        raise ValueError(
                            f"Existing paid-at-session payment amount ({existing_payment['amount_cents']}) "
                            f"does not match session charge ({charge_cents})."
                        )
                    amount_cents = existing_payment["amount_cents"]
                    payment_date = existing_payment["received_at"]
                    method_val = existing_payment["method"]

                billing_party_id = session["billing_party_id"]
                if billing_party_id is None:
                    raise ValueError("Session has no billing party.")

                conn.execute("BEGIN IMMEDIATE")
                try:
                    outcome_data = record_or_validate_paid_at_session_payment_locked(
                        conn,
                        session_id=session["id"],
                        billing_party_id=billing_party_id,
                        amount_cents=amount_cents,
                        payment_date=payment_date,
                        payment_method=method_val,
                        reference_number=payload.get("reference_number") or (existing_payment["reference_number"] if existing_payment else None),
                        administrative_note=payload.get("administrative_note") or (existing_payment["administrative_note"] if existing_payment else None),
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

                res = get_review_candidate(conn, candidate_id)
                res["paid_at_session_outcome"] = outcome_data["outcome"]
                return res
            except ValueError:
                raise
            except Exception as err:
                raise ValueError(f"Recoverable inconsistency: {err}") from err
        else:
            return get_review_candidate(conn, candidate_id)

    # 1b. Future-appointment gate: cannot approve until the session end time has passed
    # Query end_at directly to avoid side effects from session_for_candidate
    gate_row = conn.execute(
        """
        SELECT s.end_at AS session_end_at, c.end_at AS candidate_end_at
        FROM calendar_event_candidates c
        LEFT JOIN sessions s ON s.candidate_id = c.id
        WHERE c.id = ?
        """,
        (candidate_id,),
    ).fetchone()
    end_at_raw = ""
    if gate_row:
        end_at_raw = text(gate_row["session_end_at"]) or text(gate_row["candidate_end_at"])
    if end_at_raw:
        try:
            end_dt = datetime.fromisoformat(end_at_raw.replace("Z", "+00:00"))
            eastern = ZoneInfo("America/New_York")
            now_eastern = datetime.now(eastern)
            if end_dt > now_eastern:
                end_local = end_dt.astimezone(eastern)
                formatted = end_local.strftime("%B %-d at %-I:%M %p")
                raise ValueError(
                    f"This appointment is scheduled for {formatted}. "
                    f"It can be approved after the session ends."
                )
        except ValueError:
            raise
        except Exception:
            pass

    # 2. Start a single immediate transaction for first-time approval
    conn.execute("BEGIN IMMEDIATE")
    try:
        _save_interpretation_locked(conn, candidate_id, payload, now_iso())
        
        session = session_for_candidate(conn, candidate_id)
        participants = get_session_participants(conn, session["id"])
        
        unresolved = unresolved_from_values(
            conn=conn,
            participants=participants,
            billing_party_id=session["billing_party_id"],
            account_id=session["account_id"],
            duration=session["approved_duration_minutes"] or session["duration_minutes"],
            billing_session_type=session["billing_session_type"],
            service_mode=session["service_mode"],
            time_category=session["time_category"],
            approved_rate_cents=session["approved_rate_cents"],
            suggested_rate_cents=session["suggested_rate_cents"],
            rate_rule_id=session["rate_rule_id"],
            rate_needs_review=session["rate_needs_review"],
            payment_status=session["payment_status"],
            appointment_status=session["appointment_status"],
            billing_treatment=session["billing_treatment"],
            custom_service_description=session["custom_service_description"] if "custom_service_description" in session.keys() else None,
        )
        if unresolved:
            raise ValueError("Cannot approve until required fields are complete: " + ", ".join(unresolved))
            
        now = now_iso()
        service = learn_service(conn, session["service_mode"] or "other", increment_usage=True)
        conn.execute(
            """
            UPDATE sessions
            SET review_status = 'approved',
                billable_status = 'approved',
                service_catalog_id = ?,
                rate_cents_snapshot = approved_rate_cents,
                scheduled_rate_cents_snapshot = COALESCE(scheduled_rate_cents, suggested_rate_cents, approved_rate_cents),
                approved_rate_rule_id = COALESCE(approved_rate_rule_id, rate_rule_id),
                approved_rate_source = COALESCE(approved_rate_source, rate_source, 'manual_override'),
                updated_at = ?
            WHERE id = ?
            """,
            (service["service_catalog_id"], now, session["id"]),
        )
        conn.execute(
            "UPDATE calendar_event_candidates SET review_status = 'approved', updated_at = ? WHERE id = ?",
            (now, candidate_id),
        )
        save_alias_after_approval(conn, session, participants)
        record_audit(conn, "session", session["id"], "approved", {"candidate_id": candidate_id})
        add_review_item(conn, candidate_id, session["id"], "approved", [], ["Approved in review UI."])

        paid_at_session_outcome = None
        if session["payment_status"] == "paid_at_session":
            amount_received_str = payload.get("amount_received")
            payment_date = payload.get("payment_date")
            payment_method = payload.get("payment_method")
            
            if not session["billing_party_id"]:
                raise ValueError("Bill-to party is not confirmed.")
            if payload.get("billing_party_id") and payload.get("billing_party_id") != session["billing_party_id"]:
                raise ValueError("Payment Bill To party does not match the session billing party.")
            if amount_received_str is None or str(amount_received_str).strip() == "":
                raise ValueError("Amount received is required for paid-at-session sessions.")
            if not payment_date or not str(payment_date).strip():
                raise ValueError("Payment date is required for paid-at-session sessions.")
            if not payment_method or not str(payment_method).strip():
                raise ValueError("Payment method is required for paid-at-session sessions.")
                
            amount_cents = money_payload_to_cents(amount_received_str)
            charge_cents = (
                session["rate_cents_snapshot"]
                if session["rate_cents_snapshot"] is not None
                else session["approved_rate_cents"]
            )
            if amount_cents is None or amount_cents <= 0:
                raise ValueError("Payment amount must be greater than zero.")
            if amount_cents > charge_cents:
                raise ValueError("Amount received cannot exceed the approved session charge.")
            if amount_cents < charge_cents:
                raise ValueError("Amount received must exactly equal the approved session charge.")
                
            method_val = str(payment_method).strip().lower()
            
            from .payment_services import record_or_validate_paid_at_session_payment_locked
            outcome_data = record_or_validate_paid_at_session_payment_locked(
                conn,
                session_id=session["id"],
                billing_party_id=session["billing_party_id"],
                amount_cents=amount_cents,
                payment_date=payment_date,
                payment_method=method_val,
                reference_number=payload.get("reference_number"),
                administrative_note=payload.get("administrative_note"),
            )
            paid_at_session_outcome = outcome_data["outcome"]
            
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    report_warning = None
    try:
        write_reports(conn)
    except Exception as err:
        report_warning = f"Report generation warning: {err}"
        
    res = get_review_candidate(conn, candidate_id)
    if report_warning:
        res["report_warning"] = report_warning
    if paid_at_session_outcome:
        res["paid_at_session_outcome"] = paid_at_session_outcome
        
    return res


def save_person_section(conn: sqlite3.Connection, candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    init_db(conn)
    person_data = payload.get("person") or payload
    person_id = person_data.get("person_id")
    if person_id:
        update_person(conn, person_id, person_data)
    elif person_data.get("display_name"):
        created = create_person(conn, person_data["display_name"])
        person_id = created["person_id"]
    if person_id:
        session = session_for_candidate(conn, candidate_id)
        participants = get_session_participants(conn, session["id"])
        if not participants:
            add_session_participant(conn, session["id"], person_id, person_data.get("display_name") or "", True)
    refresh_candidate_suggestions(conn, candidate_id)
    record_audit(conn, "calendar_event_candidate", candidate_id, "person_section_saved", {"payload": safe_payload(payload)})
    conn.commit()
    return get_review_candidate(conn, candidate_id)


def save_relationship_section(conn: sqlite3.Connection, candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    init_db(conn)
    session = session_for_candidate(conn, candidate_id)
    now = now_iso()
    account_id = payload.get("account_id") or None
    participants = payload.get("participants", [])
    primary_person_id = payload.get("primary_person_id")
    if account_id:
        conn.execute("UPDATE sessions SET account_id = ?, updated_at = ? WHERE id = ?", (account_id, now, session["id"]))
    if "participants" in payload:
        conn.execute("DELETE FROM session_participants WHERE session_id = ?", (session["id"],))
        for index, participant in enumerate(participants):
            participant_name = participant.get("display_name") or participant.get("participant_name") or ""
            person_id = resolve_confirmed_participant_person(conn, participant.get("person_id"), participant_name, participant)
            is_primary = bool(participant.get("is_primary")) or (primary_person_id and person_id == primary_person_id) or (index == 0 and len(participants) == 1)
            add_session_participant(
                conn,
                session["id"],
                person_id,
                participant_name,
                is_primary,
                participant.get("participant_role") or ("primary" if is_primary else "participant"),
            )
            if account_id and person_id:
                ensure_account_member(
                    conn,
                    account_id,
                    person_id,
                    participant.get("relationship_role") or ("primary" if is_primary else "family_member"),
                    is_primary,
                )
    if payload.get("default_billing_party_id") and account_id:
        conn.execute(
            "UPDATE client_accounts SET default_billing_party_id = ?, updated_at = ? WHERE account_id = ?",
            (payload["default_billing_party_id"], now, account_id),
        )
    if payload.get("billing_party_id"):
        conn.execute(
            "UPDATE sessions SET billing_party_id = ?, updated_at = ? WHERE id = ?",
            (payload["billing_party_id"], now, session["id"]),
        )
    if "participants" in payload:
        saved_participants = get_session_participants(conn, session["id"])
        _save_aliases_for_participant_save(conn, session, saved_participants)
    refresh_candidate_suggestions(conn, candidate_id)
    record_audit(conn, "session", session["id"], "relationship_section_saved", {"payload": safe_payload(payload)})
    conn.commit()
    return get_review_candidate(conn, candidate_id)


def save_billing_section(conn: sqlite3.Connection, candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    init_db(conn)
    session = session_for_candidate(conn, candidate_id)
    billing_party_id = payload.get("billing_party_id")
    if payload.get("bill_to_person_id"):
        billing_party_id = billing_party_for_person(conn, payload["bill_to_person_id"])
    if payload.get("billing_party"):
        billing = payload["billing_party"]
        if billing.get("billing_party_id"):
            updated = update_billing_party(conn, billing["billing_party_id"], billing)
            billing_party_id = updated["billing_party_id"]
        elif billing.get("person_id") and billing.get("billing_party_type", "person") == "person":
            billing_party_id = _canonical_billing_party_for_person(conn, billing["person_id"])
            bp_update = {k: v for k, v in billing.items() if k != "person_id"}
            bp_update["billing_party_id"] = billing_party_id
            update_billing_party(conn, billing_party_id, bp_update)
        else:
            created = create_billing_party(conn, billing)
            billing_party_id = created["billing_party_id"]
    if billing_party_id:
        now = now_iso()
        conn.execute(
            "UPDATE sessions SET billing_party_id = ?, updated_at = ? WHERE id = ?",
            (billing_party_id, now, session["id"]),
        )
        if session["account_id"]:
            conn.execute(
                "UPDATE client_accounts SET default_billing_party_id = COALESCE(default_billing_party_id, ?), updated_at = ? WHERE account_id = ?",
                (billing_party_id, now, session["account_id"]),
            )
    refresh_candidate_suggestions(conn, candidate_id)
    record_audit(conn, "session", session["id"], "billing_section_saved", {"payload": safe_payload(payload)})
    conn.commit()
    return get_review_candidate(conn, candidate_id)


def save_session_draft(conn: sqlite3.Connection, candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    init_db(conn)
    session = session_for_candidate(conn, candidate_id)
    now = now_iso()
    approved_rate_cents = money_payload_to_cents(payload.get("approved_rate"))
    suggested_rate_cents = money_payload_to_cents(payload.get("suggested_rate"))
    duration = int(payload.get("approved_duration_minutes") or payload.get("duration_minutes") or session["duration_minutes"])
    service_mode = normalize_service_mode(payload.get("service_mode") or session["service_mode"])
    service = learn_service(conn, service_mode, increment_usage=False)
    billing_session_type = validate_billing_session_type(
        payload.get("billing_session_type")
        or (session["billing_session_type"] if "billing_session_type" in session.keys() else None)
        or "psychotherapy"
    )
    raw_duration_choice = payload.get("duration_choice") or (session["duration_choice"] if "duration_choice" in session.keys() else None) or derive_duration_choice_from_minutes(duration)
    raw_custom_minutes = int(payload["custom_duration_minutes"]) if payload.get("custom_duration_minutes") else None
    duration_choice, custom_duration_minutes = validate_duration_choice(raw_duration_choice, raw_custom_minutes)
    custom_service_description = text(payload.get("custom_service_description") or session["custom_service_description"] or "").strip() or None
    custom_service_code = text(payload.get("custom_service_code") or session["custom_service_code"] or "").strip() or None
    time_category = normalize_time_category(payload.get("time_category") or session["time_category"])
    appointment_status = normalize_attendance_outcome(
        payload.get("appointment_status") or session["appointment_status"]
    )
    payment_status = normalize_payment_status(payload.get("payment_status") or session["payment_status"])
    billable_status = payload.get("billable_status") or session["billable_status"] or "proposed"
    billing_treatment = payload.get("billing_treatment") or session["billing_treatment"] or "unresolved"
    appointment_status, billing_treatment, approved_rate_cents, scheduled_rate_cents = _coerce_charge_for_attendance(
        appointment_status,
        billing_treatment,
        approved_rate_cents,
        suggested_rate_cents,
        session,
    )
    rate_scope = payload.get("rate_scope") or "session_only"
    approved_source = approved_rate_source_for(session, approved_rate_cents, rate_scope)
    conn.execute(
        """
        UPDATE sessions
        SET approved_duration_minutes = ?,
            duration_minutes = ?,
            service_mode = ?,
            service_catalog_id = ?,
            rate_group = ?,
            time_category = ?,
            billing_session_type = ?,
            duration_choice = ?,
            custom_duration_minutes = ?,
            custom_service_description = ?,
            custom_service_code = ?,
            suggested_rate_cents = COALESCE(?, suggested_rate_cents),
            scheduled_rate_cents = COALESCE(?, scheduled_rate_cents),
            approved_rate_cents = ?,
            approved_rate_source = ?,
            approved_rate_rule_id = CASE WHEN ? = 'manual_override' THEN NULL ELSE approved_rate_rule_id END,
            rate_needs_review = ?,
            rate_override_reason = ?,
            payment_status = ?,
            billable_status = ?,
            appointment_status = ?,
            billing_treatment = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            duration,
            duration,
            service_mode,
            service["service_catalog_id"],
            rate_group_for(service_mode),
            time_category,
            billing_session_type,
            duration_choice,
            custom_duration_minutes,
            custom_service_description,
            custom_service_code,
            suggested_rate_cents,
            scheduled_rate_cents,
            approved_rate_cents,
            approved_source,
            approved_source,
            0 if approved_rate_cents is not None else 1,
            payload.get("rate_override_reason") or None,
            payment_status,
            billable_status,
            appointment_status,
            billing_treatment,
            now,
            session["id"],
        ),
    )
    maybe_save_rate_scope(conn, session, payload, approved_rate_cents)
    refresh_candidate_suggestions(conn, candidate_id, preserve_approved_rate=True)
    record_audit(conn, "session", session["id"], "session_draft_saved", {"payload": safe_payload(payload)})
    conn.commit()
    return get_review_candidate(conn, candidate_id)


def mark_candidate(
    conn: sqlite3.Connection,
    candidate_id: str,
    *,
    classification: str,
    reason: str = "",
) -> dict[str, Any]:
    init_db(conn)
    now = now_iso()
    session = conn.execute("SELECT * FROM sessions WHERE candidate_id = ?", (candidate_id,)).fetchone()
    review_status = "excluded" if classification in {"personal", "administrative", "nonbillable", "duplicate"} else "needs_classification"
    conn.execute(
        """
        UPDATE calendar_event_candidates
        SET classification = ?, review_status = ?, updated_at = ?
        WHERE id = ?
        """,
        (classification, review_status, now, candidate_id),
    )
    session_id = session["id"] if session else None
    if session:
        conn.execute(
            """
            UPDATE sessions
            SET review_status = ?, billable_status = ?, payment_status = ?, updated_at = ?
            WHERE id = ?
            """,
            (review_status, "excluded", "unpaid", now, session["id"]),
        )
    add_review_item(conn, candidate_id, session_id, review_status, [], [reason or f"Marked {classification}."])
    if classification in {"personal", "administrative", "nonbillable"}:
        candidate = conn.execute("SELECT title FROM calendar_event_candidates WHERE id = ?", (candidate_id,)).fetchone()
        if candidate:
            upsert_calendar_alias(
                conn,
                raw_alias=candidate["title"],
                classification=classification,
                approved=True,
            )
    record_audit(conn, "calendar_event_candidate", candidate_id, f"marked_{classification}", {"reason": reason})
    conn.commit()
    return get_review_candidate(conn, candidate_id)


def restore_candidate(
    conn: sqlite3.Connection,
    candidate_id: str,
    *,
    reason: str = "",
) -> dict[str, Any]:
    init_db(conn)
    now = now_iso()
    session = conn.execute(
        "SELECT * FROM sessions WHERE candidate_id = ?", (candidate_id,)
    ).fetchone()
    if not session:
        raise ValueError("No session found for this candidate; only session-backed candidates can be restored.")
    conn.execute(
        """
        UPDATE calendar_event_candidates
        SET classification = 'client_session', review_status = 'needs_classification', updated_at = ?
        WHERE id = ?
        """,
        (now, candidate_id),
    )
    conn.execute(
        """
        UPDATE sessions
        SET review_status = 'needs_classification',
            billable_status = 'proposed',
            payment_status = 'unpaid',
            billing_treatment = 'unresolved',
            updated_at = ?
        WHERE id = ?
        """,
        (now, session["id"]),
    )
    record_audit(
        conn,
        "calendar_event_candidate",
        candidate_id,
        "restored_to_review_queue",
        {"reason": reason, "prior_review_status": "excluded"},
    )
    add_review_item(
        conn,
        candidate_id,
        session["id"],
        "needs_classification",
        [],
        [reason or "Restored to review queue."],
    )
    conn.commit()
    warning = None
    try:
        refresh_candidate_suggestions(conn, candidate_id)
        conn.commit()
    except Exception:
        conn.rollback()
        warning = "Candidate was restored, but suggestions could not be refreshed."
    result = get_review_candidate(conn, candidate_id)
    if warning:
        result["warning"] = warning
    return result


def _ensure_review_session_for_candidate(
    conn: sqlite3.Connection,
    candidate_id: str,
    *,
    reason: str = "",
) -> sqlite3.Row:
    now = now_iso()

    existing_session = conn.execute(
        "SELECT * FROM sessions WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    if existing_session:
        return existing_session

    candidate = conn.execute(
        "SELECT * FROM calendar_event_candidates WHERE id = ?",
        (candidate_id,),
    ).fetchone()
    if not candidate:
        raise ValueError("Review candidate not found.")
    if candidate["review_status"] in {"approved", "excluded"}:
        raise ValueError(
            f"Candidate is {candidate['review_status']}; cannot promote to review."
        )

    snap = conn.execute(
        "SELECT * FROM raw_calendar_snapshots WHERE id = ?",
        (candidate["latest_raw_snapshot_id"],),
    ).fetchone()
    if not snap:
        raise ValueError("Raw snapshot not found for candidate.")

    parse_row = {
        "event_title": snap["event_title"],
        "start_at": snap["start_at"],
        "end_at": snap["end_at"],
        "duration_minutes": snap["duration_minutes"],
        "location": snap["location"],
    }
    result = parse_event(parse_row)
    disposition = classify_calendar(conn, snap["calendar_name"])
    result = apply_calendar_signal(result, disposition)

    result.classification = "client_session"
    if not result.proposed_start_at:
        result.proposed_start_at = snap["start_at"]
    if not result.proposed_duration_minutes:
        result.proposed_duration_minutes = (
            parse_int(snap["duration_minutes"]) or 60
        )
        result.proposed_end_at = snap["end_at"]
        result.duration_source = result.duration_source or "calendar"
    if not result.proposed_end_at and result.proposed_start_at and result.proposed_duration_minutes:
        from datetime import datetime as _dt
        try:
            start = _dt.fromisoformat(result.proposed_start_at)
            result.proposed_end_at = (start + timedelta(minutes=result.proposed_duration_minutes)).isoformat()
        except (ValueError, TypeError):
            pass
    result.confidence = max(result.confidence, 0.5)
    result.confidence_label = "review"
    result.explanation = f"{result.explanation} Manually promoted to review."
    if "classification" in result.fields_requiring_review:
        result.fields_requiring_review = [
            f for f in result.fields_requiring_review if f != "classification"
        ]
    if "classification" in result.unresolved_fields:
        result.unresolved_fields = [
            f for f in result.unresolved_fields if f != "classification"
        ]

    new_review_status = review_status_for_parse(result)

    conn.execute(
        """
        UPDATE calendar_event_candidates
        SET classification           = ?,
            confidence               = ?,
            confidence_label         = ?,
            explanation              = ?,
            fields_requiring_review  = ?,
            unresolved_fields        = ?,
            review_reasons           = ?,
            parser_payload           = ?,
            proposed_client_name     = ?,
            candidate_person_names   = ?,
            possible_referenced_person = ?,
            proposed_start_at        = ?,
            proposed_duration_minutes = ?,
            proposed_end_at          = ?,
            time_shorthand           = ?,
            duration_source          = ?,
            service_mode             = ?,
            rate_group               = ?,
            time_category            = ?,
            is_evening               = ?,
            is_weekend               = ?,
            appointment_status       = ?,
            billing_treatment        = ?,
            title_time_text          = ?,
            title_time_normalized    = ?,
            title_time_matches_calendar = ?,
            billing_session_type     = ?,
            appointment_method       = ?,
            duration_choice          = ?,
            house_call_suggested     = ?,
            billing_type_source      = ?,
            location_text            = ?,
            review_status            = ?,
            updated_at               = ?
        WHERE id = ?
          AND review_status NOT IN ('approved', 'excluded')
        """,
        (
            result.classification,
            result.confidence,
            result.confidence_label,
            result.explanation,
            json_dumps(result.fields_requiring_review),
            json_dumps(result.unresolved_fields),
            json_dumps(result.review_reasons),
            json_dumps(result.as_dict()),
            result.proposed_client_name,
            json_dumps(result.candidate_person_names),
            result.possible_referenced_person,
            result.proposed_start_at,
            result.proposed_duration_minutes,
            result.proposed_end_at,
            result.time_shorthand,
            result.duration_source,
            result.service_mode,
            result.rate_group,
            result.time_category,
            1 if result.is_evening else 0,
            1 if result.is_weekend else 0,
            result.appointment_status,
            initial_billing_treatment(result),
            result.title_time_text,
            result.title_time_normalized,
            (1 if result.title_time_matches_calendar else (0 if result.title_time_matches_calendar is False else None)),
            result.billing_session_type,
            result.appointment_method,
            result.duration_choice,
            1 if result.house_call_suggested else 0,
            result.billing_type_source,
            result.location_text,
            new_review_status,
            now,
            candidate_id,
        ),
    )

    created = maybe_insert_session(conn, candidate_id, snap, result)
    if not created:
        raise ValueError(
            "Could not create session from candidate; missing required fields."
        )

    record_audit(
        conn,
        "calendar_event_candidate",
        candidate_id,
        "promoted_to_review",
        {"reason": reason or "Manually promoted to review queue."},
    )
    session = conn.execute(
        "SELECT * FROM sessions WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    if not session:
        raise ValueError("Could not create session from candidate; missing required fields.")
    return session


def promote_candidate_to_review(
    conn: sqlite3.Connection,
    candidate_id: str,
    *,
    reason: str = "",
) -> dict[str, Any]:
    """
    Manually promote a candidate-only calendar record (no session) into the review queue.
    Re-parses the preserved raw snapshot, forces classification to client_session,
    and creates one reviewable session.  Skips candidates that already have a session
    or are approved/excluded.  Never modifies raw evidence.
    """
    init_db(conn)
    existing_session = conn.execute(
        "SELECT id FROM sessions WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    if existing_session:
        raise ValueError("A session already exists for this candidate.")

    _ensure_review_session_for_candidate(conn, candidate_id, reason=reason)
    conn.commit()
    return get_review_candidate(conn, candidate_id)


def search_people(conn: sqlite3.Connection, query: str = "") -> list[dict[str, Any]]:
    rows = search_table(conn, "people", "person_id", "display_name", query)
    similar = similar_people(conn, query)
    seen = {row["person_id"] for row in rows}
    for row in similar:
        if row["person_id"] not in seen:
            row["similar_match"] = True
            rows.append(row)
    return rows


def list_people_records(conn: sqlite3.Connection, query: str = "") -> list[dict[str, Any]]:
    init_db(conn)
    like = f"%{query}%"
    rows = conn.execute(
        """
        SELECT
          p.*,
          GROUP_CONCAT(DISTINCT ca.account_name) AS accounts,
          GROUP_CONCAT(DISTINCT bp.billing_name) AS billing_for,
          MAX(s.session_date) AS last_session
        FROM people p
        LEFT JOIN account_members am ON am.person_id = p.person_id
        LEFT JOIN client_accounts ca ON ca.account_id = am.account_id
        LEFT JOIN billing_parties bp ON bp.person_id = p.person_id
        LEFT JOIN session_participants sp ON sp.person_id = p.person_id
        LEFT JOIN sessions s ON s.id = sp.session_id
        WHERE p.display_name LIKE ? OR p.first_name LIKE ? OR p.last_name LIKE ? OR COALESCE(p.person_code, '') LIKE ?
        GROUP BY p.person_id
        ORDER BY COALESCE(NULLIF(p.last_name, ''), p.display_name), p.first_name
        LIMIT 250
        """,
        (like, like, like, like),
    ).fetchall()
    return [dict(row) for row in rows]


def _person_billing_setup(conn: sqlite3.Connection, person_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT billing_party_id, billing_party_type, person_id, organization_name,
               billing_name, billing_email, billing_phone, billing_address_line_1,
               billing_address_line_2, billing_city, billing_state, billing_postal_code,
               preferred_delivery_method, active
        FROM billing_parties
        WHERE person_id = ?
        ORDER BY active DESC, billing_name
        """,
        (person_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _payers_for_client(conn: sqlite3.Connection, person_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT DISTINCT
          bp.billing_party_id,
          bp.billing_name,
          bp.person_id AS payer_person_id,
          p.display_name AS payer_display_name,
          bp.active AS billing_party_active,
          COUNT(s.id) AS session_count,
          MAX(s.session_date) AS most_recent_session_date
        FROM sessions s
        JOIN session_participants sp ON sp.session_id = s.id AND sp.person_id = ?
        LEFT JOIN billing_parties bp ON bp.billing_party_id = s.billing_party_id
        LEFT JOIN people p ON p.person_id = bp.person_id
        WHERE s.billing_party_id IS NOT NULL
        GROUP BY bp.billing_party_id, bp.billing_name, bp.person_id, p.display_name, bp.active
        ORDER BY most_recent_session_date DESC
        """,
        (person_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _people_billed_for(conn: sqlite3.Connection, person_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT DISTINCT
          sp.person_id AS participant_person_id,
          COALESCE(p.display_name, sp.participant_name) AS participant_display_name,
          COUNT(DISTINCT s.id) AS session_count,
          MAX(s.session_date) AS latest_session_date
        FROM sessions s
        JOIN billing_parties bp ON bp.billing_party_id = s.billing_party_id AND bp.person_id = ?
        JOIN session_participants sp ON sp.session_id = s.id
        LEFT JOIN people p ON p.person_id = sp.person_id
        GROUP BY sp.person_id, participant_display_name
        ORDER BY latest_session_date DESC
        """,
        (person_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _invoices_for_person(conn: sqlite3.Connection, person_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
          i.invoice_id,
          i.invoice_number,
          i.status,
          i.invoice_date,
          i.billing_period_start,
          i.billing_period_end,
          i.subtotal_cents,
          i.adjustment_cents,
          i.total_cents,
          i.finalized_at,
          i.voided_at,
          i.void_reason,
          i.bill_to_party_id,
          bp.billing_name AS bill_to_name,
          (SELECT COALESCE(SUM(line_amount_cents), 0) FROM invoice_line_items li WHERE li.invoice_id = i.invoice_id) AS line_total_cents
        FROM invoices i
        JOIN billing_parties bp ON bp.billing_party_id = i.bill_to_party_id AND bp.person_id = ?
        ORDER BY i.invoice_date DESC, i.created_at DESC
        """,
        (person_id,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        if item["status"] == "void":
            item["paid_cents"] = 0
            item["balance_cents"] = 0
            item["payment_status"] = "void"
        elif item["status"] == "draft":
            item["paid_cents"] = 0
            item["balance_cents"] = item["total_cents"]
            item["payment_status"] = "unpaid"
        else:
            summary = _invoice_balance_summary(conn, item["invoice_id"])
            item["paid_cents"] = summary["paid_cents"]
            item["balance_cents"] = summary["balance_cents"]
            item["payment_status"] = summary["payment_status"]
        result.append(item)
    return result


def _billing_summary(conn: sqlite3.Connection, person_id: str) -> dict[str, Any]:
    active_bp_count = conn.execute(
        "SELECT COUNT(*) FROM billing_parties WHERE person_id = ? AND active = 1",
        (person_id,),
    ).fetchone()[0]
    invoice_rows = conn.execute(
        """
        SELECT i.status, i.total_cents
        FROM invoices i
        JOIN billing_parties bp ON bp.billing_party_id = i.bill_to_party_id AND bp.person_id = ?
        """,
        (person_id,),
    ).fetchall()
    invoice_count = len(invoice_rows)
    total_invoiced = sum(r["total_cents"] for r in invoice_rows if r["status"] != "void")
    finalized_invoice_total = sum(r["total_cents"] for r in invoice_rows if r["status"] == "finalized")
    approved_uninvoiced_count = conn.execute(
        """
        SELECT COUNT(DISTINCT s.id)
        FROM sessions s
        JOIN billing_parties bp ON bp.billing_party_id = s.billing_party_id AND bp.person_id = ?
        WHERE s.review_status = 'approved'
          AND s.billable_status NOT IN ('excluded', 'nonbillable')
          AND (s.appointment_status IS NULL OR s.appointment_status NOT IN ('scheduled'))
          AND NOT EXISTS (
            SELECT 1 FROM invoice_line_items li
            JOIN invoices i ON i.invoice_id = li.invoice_id
            WHERE li.source_session_id = s.id AND i.status IN ('draft', 'finalized')
          )
        """,
        (person_id,),
    ).fetchone()[0]
    account = client_account_summary(conn, person_id)
    return {
        "active_billing_parties": active_bp_count,
        "invoice_count": invoice_count,
        "total_invoiced_cents": total_invoiced,
        "finalized_invoice_total_cents": finalized_invoice_total,
        "approved_uninvoiced_sessions": approved_uninvoiced_count,
        "total_paid_cents": account["total_paid_cents"],
        "current_balance_cents": account["current_balance_cents"],
        "account_status": account["account_status"],
        "total_finalized_invoices": account["total_finalized_invoices"],
    }


def get_person_record(conn: sqlite3.Connection, person_id: str) -> dict[str, Any]:
    init_db(conn)
    person = conn.execute("SELECT * FROM people WHERE person_id = ?", (person_id,)).fetchone()
    if not person:
        raise ValueError("Person not found.")
    accounts = conn.execute(
        """
        SELECT ca.*, am.relationship_role, am.is_primary, am.effective_from, am.effective_through
        FROM account_members am
        JOIN client_accounts ca ON ca.account_id = am.account_id
        WHERE am.person_id = ?
        ORDER BY ca.account_name
        """,
        (person_id,),
    ).fetchall()
    sessions = conn.execute(
        """
        SELECT
          s.id AS session_id,
          s.candidate_id,
          s.session_date,
          s.start_at,
          s.duration_minutes,
          s.custom_duration_minutes,
          s.service_mode,
          s.billing_session_type,
          s.custom_service_description,
          s.time_category,
          s.approved_rate_cents,
          s.approved_rate_source,
          s.rate_source,
          s.payment_status,
          s.review_status,
          s.raw_calendar_title,
          (
            SELECT GROUP_CONCAT(COALESCE(p2.display_name, sp2.participant_name), ', ')
            FROM session_participants sp2
            LEFT JOIN people p2 ON p2.person_id = sp2.person_id
            WHERE sp2.session_id = s.id
              AND COALESCE(sp2.person_id, '') != ?
          ) AS other_participant_names
        FROM session_participants sp
        JOIN sessions s ON s.id = sp.session_id
        WHERE sp.person_id = ?
        ORDER BY s.start_at DESC
        LIMIT 50
        """,
        (person_id, person_id),
    ).fetchall()
    aliases = conn.execute(
        "SELECT * FROM calendar_aliases WHERE person_id = ? ORDER BY updated_at DESC LIMIT 50",
        (person_id,),
    ).fetchall()
    billing = conn.execute(
        "SELECT * FROM billing_parties WHERE person_id = ? ORDER BY billing_name",
        (person_id,),
    ).fetchall()
    person_rates = conn.execute(
        """
        SELECT *
        FROM rate_rules
        WHERE active = 1
          AND person_id = ?
          AND rate_rule_id NOT IN (SELECT rate_rule_id FROM rate_rule_participants)
        ORDER BY effective_from DESC, priority ASC
        """,
        (person_id,),
    ).fetchall()
    joint_rates = conn.execute(
        """
        SELECT rr.*, GROUP_CONCAT(p.display_name, ' + ') AS participant_names
        FROM rate_rules rr
        JOIN rate_rule_participants rrp ON rrp.rate_rule_id = rr.rate_rule_id
        JOIN people p ON p.person_id = rrp.person_id
        WHERE rr.active = 1
          AND rr.rate_rule_id IN (
            SELECT rate_rule_id FROM rate_rule_participants WHERE person_id = ?
          )
        GROUP BY rr.rate_rule_id
        HAVING COUNT(*) > 1
        ORDER BY rr.effective_from DESC, rr.priority ASC
        """,
        (person_id,),
    ).fetchall()
    return {
        "person": dict(person),
        "accounts": [dict(row) for row in accounts],
        "sessions": [dict(row) for row in sessions],
        "aliases": [dict(row) for row in aliases],
        "billing_parties": [dict(row) for row in billing],
        "active_rate_exceptions": [dict(row) for row in person_rates],
        "joint_rate_exceptions": [dict(row) for row in joint_rates],
        "audit": audit_history_for_entity(conn, "person", person_id),
        "billing_setup": _person_billing_setup(conn, person_id),
        "payers_for_client": _payers_for_client(conn, person_id),
        "people_billed_for": _people_billed_for(conn, person_id),
        "invoices": _invoices_for_person(conn, person_id),
        "billing_summary": _billing_summary(conn, person_id),
    }


def create_person(conn: sqlite3.Connection, display_name: str | dict[str, Any]) -> dict[str, Any]:
    init_db(conn)
    data = display_name if isinstance(display_name, dict) else {"display_name": display_name}
    display_name = text(
        data.get("display_name")
        or " ".join(part for part in [data.get("first_name"), data.get("last_name")] if part)
        or data.get("preferred_name")
    )
    if not display_name:
        raise ValueError("Display name is required.")
    existing = conn.execute(
        "SELECT * FROM people WHERE lower(display_name) = lower(?) AND active = 1 LIMIT 1",
        (display_name,),
    ).fetchone()
    if existing:
        result = dict(existing)
        result["created"] = False
        result["existing"] = True
        return result
    now = now_iso()
    person_id = new_id()
    first = text(data.get("first_name") or split_name(display_name)[0])
    last = text(data.get("last_name") or split_name(display_name)[1])
    preferred = text(data.get("preferred_name") or first)
    person_code = generate_person_code(conn, first, last) if first and last else None
    conn.execute(
        """
        INSERT INTO people (
          person_id, display_name, first_name, last_name, preferred_name,
          person_code, billing_email, billing_phone, administrative_notes,
          created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            person_id,
            display_name,
            first,
            last,
            preferred,
            person_code,
            data.get("billing_email") or data.get("email"),
            data.get("billing_phone") or data.get("phone"),
            data.get("administrative_notes"),
            now,
            now,
        ),
    )
    record_audit(conn, "person", person_id, "created_inline", {"display_name": display_name, "person_code": person_code})
    conn.commit()
    result = dict(conn.execute("SELECT * FROM people WHERE person_id = ?", (person_id,)).fetchone())
    result["created"] = True
    result["existing"] = False
    return result


def update_person(conn: sqlite3.Connection, person_id: str, data: dict[str, Any]) -> dict[str, Any]:
    init_db(conn)
    existing = conn.execute("SELECT * FROM people WHERE person_id = ?", (person_id,)).fetchone()
    if not existing:
        raise ValueError("Person not found.")
    old_name = existing["display_name"]
    display_name = text(data.get("display_name") or old_name)
    first_name = text(data.get("first_name") or split_name(display_name)[0])
    last_name = text(data.get("last_name") or split_name(display_name)[1])
    preferred_name = text(data.get("preferred_name") or first_name)
    old_code = existing["person_code"]
    person_code = data.get("person_code") or old_code
    if not person_code and first_name and last_name:
        person_code = generate_person_code(conn, first_name, last_name)
    now = now_iso()
    conn.execute(
        """
        UPDATE people
        SET display_name = ?,
            first_name = ?,
            last_name = ?,
            preferred_name = ?,
            person_code = COALESCE(?, person_code),
            billing_email = ?,
            billing_phone = ?,
            administrative_notes = ?,
            active_status = ?,
            active = ?,
            updated_at = ?
        WHERE person_id = ?
        """,
        (
            display_name,
            first_name,
            last_name,
            preferred_name,
            person_code,
            data.get("billing_email"),
            data.get("billing_phone"),
            data.get("administrative_notes") if "administrative_notes" in data else existing["administrative_notes"],
            data.get("active_status", "active"),
            1 if data.get("active", True) else 0,
            now,
            person_id,
        ),
    )
    if old_name != display_name:
        upsert_calendar_alias(
            conn,
            raw_alias=old_name,
            person_id=person_id,
            account_id=data.get("account_id"),
            classification="client_session",
            approved=True,
        )
    record_audit(
        conn,
        "person",
        person_id,
        "identity_corrected",
        {
            "old_value": old_name,
            "new_value": display_name,
            "old_code": old_code,
            "new_code": person_code,
            "source": "review_ui",
        },
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM people WHERE person_id = ?", (person_id,)).fetchone())


def similar_people(conn: sqlite3.Connection, name: str) -> list[dict[str, Any]]:
    init_db(conn)
    tokens = [token for token in text(name).lower().split() if token]
    if not tokens:
        return []
    first = tokens[0]
    rows = conn.execute(
        """
        SELECT person_id, display_name, first_name, last_name, person_code
        FROM people
        WHERE active = 1
          AND (
            lower(display_name) = ?
            OR lower(first_name) = ?
            OR lower(display_name) LIKE ?
            OR person_id IN (
              SELECT person_id FROM calendar_aliases
              WHERE normalized_alias LIKE ?
            )
          )
        ORDER BY CASE WHEN lower(display_name) = ? THEN 0 ELSE 1 END, display_name
        LIMIT 10
        """,
        (text(name).lower(), first, f"{first}%", f"{first}%", text(name).lower()),
    ).fetchall()
    return [dict(row) for row in rows]


def merge_people(
    conn: sqlite3.Connection,
    survivor_person_id: str,
    duplicate_person_id: str,
    reason: str = "",
) -> dict[str, Any]:
    init_db(conn)
    if survivor_person_id == duplicate_person_id:
        raise ValueError("Cannot merge a person into itself.")
    survivor = conn.execute("SELECT * FROM people WHERE person_id = ?", (survivor_person_id,)).fetchone()
    duplicate = conn.execute("SELECT * FROM people WHERE person_id = ?", (duplicate_person_id,)).fetchone()
    if not survivor or not duplicate:
        raise ValueError("Both people must exist before merging.")
    now = now_iso()
    conn.execute(
        "UPDATE session_participants SET person_id = ? WHERE person_id = ?",
        (survivor_person_id, duplicate_person_id),
    )
    conn.execute(
        """
        UPDATE account_members
        SET person_id = ?, updated_at = ?
        WHERE person_id = ?
        """,
        (survivor_person_id, now, duplicate_person_id),
    )
    conn.execute(
        "UPDATE billing_parties SET person_id = ?, updated_at = ? WHERE person_id = ?",
        (survivor_person_id, now, duplicate_person_id),
    )
    conn.execute(
        "UPDATE calendar_aliases SET person_id = ?, updated_at = ? WHERE person_id = ?",
        (survivor_person_id, now, duplicate_person_id),
    )
    upsert_calendar_alias(
        conn,
        raw_alias=duplicate["display_name"],
        person_id=survivor_person_id,
        classification="client_session",
        approved=True,
    )
    conn.execute(
        """
        UPDATE people
        SET active = 0,
            active_status = 'merged',
            merged_into_person_id = ?,
            merge_note = ?,
            updated_at = ?
        WHERE person_id = ?
        """,
        (survivor_person_id, reason, now, duplicate_person_id),
    )
    record_audit(
        conn,
        "person",
        survivor_person_id,
        "merged_duplicate_person",
        {
            "survivor_person_id": survivor_person_id,
            "duplicate_person_id": duplicate_person_id,
            "duplicate_display_name": duplicate["display_name"],
            "reason": reason,
        },
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM people WHERE person_id = ?", (survivor_person_id,)).fetchone())


def search_accounts(conn: sqlite3.Connection, query: str = "") -> list[dict[str, Any]]:
    return search_table(conn, "client_accounts", "account_id", "account_name", query, code_column="account_code")


def list_account_records(conn: sqlite3.Connection, query: str = "") -> list[dict[str, Any]]:
    init_db(conn)
    like = f"%{query}%"
    rows = conn.execute(
        """
        SELECT
          ca.*,
          bp.billing_name AS billing_party_name,
          GROUP_CONCAT(DISTINCT p.display_name) AS members,
          (
            SELECT MAX(s2.session_date)
            FROM sessions s2
            WHERE s2.account_id = ca.account_id
          ) AS last_session,
          (
            SELECT COALESCE(SUM(i.total_cents), 0)
            FROM invoices i
            WHERE i.bill_to_party_id = ca.default_billing_party_id
              AND i.status = 'finalized'
          ) AS outstanding_cents
        FROM client_accounts ca
        LEFT JOIN billing_parties bp ON bp.billing_party_id = ca.default_billing_party_id
        LEFT JOIN account_members am ON am.account_id = ca.account_id
        LEFT JOIN people p ON p.person_id = am.person_id
        WHERE ca.account_name LIKE ? OR COALESCE(ca.account_code, '') LIKE ?
        GROUP BY ca.account_id
        ORDER BY ca.account_name
        LIMIT 250
        """,
        (like, like),
    ).fetchall()
    output = []
    for row in rows:
        item = dict(row)
        primary = conn.execute(
            """
            SELECT p.display_name
            FROM account_members am
            JOIN people p ON p.person_id = am.person_id
            WHERE am.account_id = ? AND am.is_primary = 1
            ORDER BY p.display_name
            LIMIT 1
            """,
            (row["account_id"],),
        ).fetchone()
        rate = conn.execute(
            """
            SELECT amount_cents
            FROM rate_rules
            WHERE active = 1 AND client_account_id = ?
            ORDER BY priority ASC, effective_from DESC
            LIMIT 1
            """,
            (row["account_id"],),
        ).fetchone()
        item["primary_person"] = primary["display_name"] if primary else ""
        item["current_default_rate"] = cents_to_dollars(rate["amount_cents"]) if rate else ""
        item["finalized_invoice_total"] = cents_to_dollars(item.pop("outstanding_cents") or 0)
        output.append(item)
    return output


def get_account_record(conn: sqlite3.Connection, account_id: str) -> dict[str, Any]:
    init_db(conn)
    account = get_account(conn, account_id)
    if not account:
        raise ValueError("Account not found.")
    members = get_account_members(conn, account_id)
    billing_party = get_billing_party(conn, account.get("default_billing_party_id"))
    rates = conn.execute(
        """
        SELECT rr.*, p.display_name
        FROM rate_rules rr
        LEFT JOIN people p ON p.person_id = rr.person_id
        WHERE rr.client_account_id = ?
        ORDER BY rr.active DESC, rr.effective_from DESC
        """,
        (account_id,),
    ).fetchall()
    sessions = conn.execute(
        """
        SELECT session_date, start_at, duration_minutes, service_mode, time_category,
               approved_rate_cents, payment_status, review_status, raw_calendar_title
        FROM sessions
        WHERE account_id = ?
        ORDER BY start_at DESC
        LIMIT 75
        """,
        (account_id,),
    ).fetchall()
    aliases = conn.execute(
        "SELECT * FROM calendar_aliases WHERE account_id = ? ORDER BY updated_at DESC LIMIT 50",
        (account_id,),
    ).fetchall()
    return {
        "account": account,
        "members": members,
        "billing_party": billing_party,
        "rates": [dict(row) for row in rates],
        "sessions": [dict(row) for row in sessions],
        "aliases": [dict(row) for row in aliases],
        "audit": audit_history_for_entity(conn, "client_account", account_id),
    }


def find_equivalent_account(
    conn: sqlite3.Connection,
    person_id: str,
    account_type: str = "individual",
) -> dict[str, Any] | None:
    """Find an active account that is equivalent to a single-client relationship for this person.

    A match exists when any of the following is true for an active account:
    - The person is the sole account member.
    - The person is the primary account member.
    - The account's default billing party belongs to this person.

    This is conservative: a shared relationship where the person is a non-primary
    member is NOT treated as equivalent.

    Returns the account dict if found, None otherwise.
    """
    init_db(conn)

    # 1. Check accounts where this person is a member
    rows = conn.execute(
        """
        SELECT DISTINCT a.* FROM client_accounts a
        JOIN account_members m ON m.account_id = a.account_id
        WHERE m.person_id = ? AND a.active = 1
        """,
        (person_id,),
    ).fetchall()
    for row in rows:
        account = dict(row)
        members = conn.execute(
            "SELECT * FROM account_members WHERE account_id = ?",
            (account["account_id"],),
        ).fetchall()
        if len(members) == 1:
            return account
        primary = [m for m in members if m["is_primary"]]
        if primary and primary[0]["person_id"] == person_id:
            return account

    # 2. Check accounts whose default billing party belongs to this person
    bp_rows = conn.execute(
        """
        SELECT DISTINCT a.* FROM client_accounts a
        JOIN billing_parties bp ON bp.billing_party_id = a.default_billing_party_id
        WHERE bp.person_id = ? AND a.active = 1
        """,
        (person_id,),
    ).fetchall()
    for row in bp_rows:
        account = dict(row)
        members = conn.execute(
            "SELECT * FROM account_members WHERE account_id = ?",
            (account["account_id"],),
        ).fetchall()
        if len(members) <= 1:
            return account
        primary = [m for m in members if m["is_primary"]]
        if primary and primary[0]["person_id"] == person_id:
            return account
        # If no primary flag but sole billing-party owner, still treat as equivalent
        if not primary and len(members) == 0:
            return account

    return None


def create_account_or_return_existing(
    conn: sqlite3.Connection,
    person_id: str,
    account_name: str,
    account_type: str = "individual",
) -> dict[str, Any]:
    """Create a billing relationship for a client, or return an existing equivalent.

    If an active equivalent account already exists for this person (primary or sole
    member of an active individual account), return it with an ``existing`` flag
    instead of creating a duplicate.
    """
    init_db(conn)
    equivalent = find_equivalent_account(conn, person_id, account_type)
    if equivalent:
        return {"existing": True, "account": equivalent}
    account = create_account(conn, account_name, account_type)
    member_id = add_account_member(conn, account["account_id"], person_id, "primary", True)
    return {"existing": False, "account": account, "account_member_id": member_id}


def create_account(conn: sqlite3.Connection, account_name: str, account_type: str = "individual", *, commit: bool = True) -> dict[str, Any]:
    if commit:
        init_db(conn)
    existing = conn.execute(
        "SELECT * FROM client_accounts WHERE lower(account_name) = lower(?) AND active = 1 LIMIT 1",
        (account_name,),
    ).fetchone()
    if existing:
        return dict(existing)
    now = now_iso()
    account_id = new_id()
    account_code = generate_account_code(conn)
    conn.execute(
        """
        INSERT INTO client_accounts (
          account_id, account_code, account_name, account_type, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (account_id, account_code, account_name, account_type, now, now),
    )
    record_audit(conn, "client_account", account_id, "created_inline", {"account_name": account_name, "account_code": account_code})
    if commit:
        conn.commit()
    return dict(conn.execute("SELECT * FROM client_accounts WHERE account_id = ?", (account_id,)).fetchone())


def update_account(conn: sqlite3.Connection, account_id: str, data: dict[str, Any]) -> dict[str, Any]:
    init_db(conn)
    existing = conn.execute("SELECT * FROM client_accounts WHERE account_id = ?", (account_id,)).fetchone()
    if not existing:
        raise ValueError("Account not found.")
    now = now_iso()
    conn.execute(
        """
        UPDATE client_accounts
        SET account_name = ?,
            account_type = ?,
            default_billing_party_id = ?,
            administrative_notes = ?,
            active = ?,
            updated_at = ?
        WHERE account_id = ?
        """,
        (
            data.get("account_name") or existing["account_name"],
            data.get("account_type") or existing["account_type"],
            data.get("default_billing_party_id") or existing["default_billing_party_id"],
            data.get("administrative_notes") if "administrative_notes" in data else existing["administrative_notes"],
            1 if data.get("active", True) else 0,
            now,
            account_id,
        ),
    )
    record_audit(conn, "client_account", account_id, "updated_inline", data)
    conn.commit()
    return dict(conn.execute("SELECT * FROM client_accounts WHERE account_id = ?", (account_id,)).fetchone())


def deactivate_account(conn: sqlite3.Connection, account_id: str) -> dict[str, Any]:
    """Set a billing relationship (client_account) to inactive.

    Idempotent: if the account is already inactive, no audit entry is written.
    Preserves all historical records, members, billing parties, sessions, invoices, and payments.
    """
    init_db(conn)
    existing = conn.execute("SELECT * FROM client_accounts WHERE account_id = ?", (account_id,)).fetchone()
    if not existing:
        raise ValueError("Account not found.")
    if existing["active"] == 0:
        return dict(existing)
    _begin_immediate(conn)
    try:
        now = now_iso()
        _delete_billing_relationship_key(conn, account_id)
        conn.execute(
            "UPDATE client_accounts SET active = 0, updated_at = ? WHERE account_id = ?",
            (now, account_id),
        )
        record_audit(conn, "client_account", account_id, "deactivated", {"account_name": existing["account_name"]})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return dict(conn.execute("SELECT * FROM client_accounts WHERE account_id = ?", (account_id,)).fetchone())


def reactivate_account(conn: sqlite3.Connection, account_id: str) -> dict[str, Any]:
    """Set a billing relationship (client_account) back to active.

    Idempotent: if the account is already active, no audit entry is written.
    """
    init_db(conn)
    existing = conn.execute("SELECT * FROM client_accounts WHERE account_id = ?", (account_id,)).fetchone()
    if not existing:
        raise ValueError("Account not found.")
    if existing["active"] == 1:
        return dict(existing)
    duplicate_probe = _account_duplicate_probe(conn, account_id)
    _begin_immediate(conn)
    try:
        if duplicate_probe:
            payer_kind, payer_person_id, organization_billing_party_id, covered_client_ids = duplicate_probe
            duplicate = find_duplicate_billing_relationship(
                conn,
                payer_kind,
                payer_person_id,
                organization_billing_party_id,
                covered_client_ids,
                exclude_account_id=account_id,
            )
            if duplicate:
                raise ValueError("This billing relationship already exists.")
            _upsert_billing_relationship_key(
                conn,
                account_id=account_id,
                payer_kind=payer_kind,
                payer_person_id=payer_person_id,
                organization_billing_party_id=organization_billing_party_id,
                covered_client_ids=covered_client_ids,
            )
        now = now_iso()
        conn.execute(
            "UPDATE client_accounts SET active = 1, updated_at = ? WHERE account_id = ?",
            (now, account_id),
        )
        record_audit(conn, "client_account", account_id, "reactivated", {"account_name": existing["account_name"]})
        conn.commit()
    except sqlite3.IntegrityError as error:
        conn.rollback()
        raise ValueError("This billing relationship already exists.") from error
    except Exception:
        conn.rollback()
        raise
    return dict(conn.execute("SELECT * FROM client_accounts WHERE account_id = ?", (account_id,)).fetchone())


def update_billing_relationship(
    conn: sqlite3.Connection,
    account_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Transactionally update a billing relationship's payer, covered clients, and delivery fields.

    Preserves account UUID, account code, and all historical sessions/invoices/payments.
    Prevents active exact duplicates. Writes audit entries.
    """
    init_db(conn)
    account = conn.execute("SELECT * FROM client_accounts WHERE account_id = ?", (account_id,)).fetchone()
    if not account:
        raise ValueError("Account not found.")
    if account["active"] == 0:
        raise ValueError("Cannot edit an inactive billing relationship. Reactivate it first.")

    payer_kind = (payload.get("payer_kind") or "").strip().lower()
    if payer_kind not in ("client", "person", "organization"):
        raise ValueError("payer_kind must be one of: client, person, organization.")

    raw_covered = payload.get("covered_client_ids") or []
    if not isinstance(raw_covered, list) or not raw_covered:
        raise ValueError("At least one covered client is required for an active relationship.")
    covered_client_ids = []
    seen = set()
    for cid in raw_covered:
        cid = str(cid).strip()
        if not cid:
            raise ValueError("Covered client IDs must be non-empty strings.")
        if cid in seen:
            raise ValueError("Duplicate covered client IDs are not allowed.")
        seen.add(cid)
        covered_client_ids.append(cid)

    for cid in covered_client_ids:
        row = conn.execute("SELECT person_id FROM people WHERE person_id = ? AND active = 1", (cid,)).fetchone()
        if not row:
            raise ValueError(f"Covered client {cid} does not exist or is not active.")

    payer_person_id = None
    organization_billing_party_id = None
    if payer_kind in ("client", "person"):
        payer_person_id = str(payload.get("payer_person_id") or "").strip()
        if not payer_person_id:
            raise ValueError("payer_person_id is required for client or person payer kind.")
        payer_row = conn.execute("SELECT * FROM people WHERE person_id = ? AND active = 1", (payer_person_id,)).fetchone()
        if not payer_row:
            raise ValueError("Payer person does not exist or is not active.")
    elif payer_kind == "organization":
        organization_billing_party_id = str(payload.get("organization_billing_party_id") or "").strip()
        if not organization_billing_party_id:
            raise ValueError("organization_billing_party_id is required for organization payer kind.")
        org_row = conn.execute(
            "SELECT * FROM billing_parties WHERE billing_party_id = ? AND active = 1 AND billing_party_type = 'organization'",
            (organization_billing_party_id,),
        ).fetchone()
        if not org_row:
            raise ValueError("Organization billing party does not exist, is not active, or is not an organization.")

    duplicate = find_duplicate_billing_relationship(
        conn, payer_kind, payer_person_id, organization_billing_party_id, covered_client_ids
    )
    if duplicate and duplicate["account_id"] != account_id:
        raise ValueError("This billing relationship already exists.")

    _begin_immediate(conn)
    try:
        if payer_kind in ("client", "person"):
            billing_party_id = _canonical_billing_party_for_person(
                conn,
                payer_person_id,
                preferred_account_id=account_id,
            )
        else:
            billing_party_id = organization_billing_party_id

        current_members = {
            r["person_id"]: r for r in conn.execute(
                "SELECT * FROM account_members WHERE account_id = ?", (account_id,)
            ).fetchall()
        }
        new_set = set(covered_client_ids)

        for old_pid in current_members:
            if old_pid not in new_set:
                conn.execute(
                    "DELETE FROM account_members WHERE account_member_id = ?",
                    (current_members[old_pid]["account_member_id"],),
                )
                record_audit(conn, "account_member", current_members[old_pid]["account_member_id"], "removed", {
                    "account_id": account_id, "person_id": old_pid,
                })

        primary_person_id = None
        if payer_kind == "client" and payer_person_id in covered_client_ids:
            primary_person_id = payer_person_id
        elif covered_client_ids:
            primary_person_id = covered_client_ids[0]
        requested_filing_owner = payload.get("default_filing_owner_person_id", account["default_filing_owner_person_id"])
        requested_filing_owner = str(requested_filing_owner or "").strip() or None
        if requested_filing_owner and requested_filing_owner not in new_set:
            raise ValueError("Default filing client must be one of the covered clients.")
        if not requested_filing_owner and len(covered_client_ids) == 1:
            requested_filing_owner = covered_client_ids[0]

        for cid in covered_client_ids:
            if cid in current_members:
                is_primary = (cid == primary_person_id)
                role = "primary" if is_primary else "family_member"
                conn.execute(
                    "UPDATE account_members SET relationship_role = ?, is_primary = ?, updated_at = ? WHERE account_member_id = ?",
                    (role, 1 if is_primary else 0, now_iso(), current_members[cid]["account_member_id"]),
                )
            else:
                is_primary = (cid == primary_person_id)
                role = "primary" if is_primary else "family_member"
                add_account_member(conn, account_id, cid, role, is_primary, commit=False)

        now = now_iso()
        conn.execute(
            "UPDATE client_accounts SET default_billing_party_id = ?, default_filing_owner_person_id = ?, updated_at = ? WHERE account_id = ?",
            (billing_party_id, requested_filing_owner, now, account_id),
        )

        conn.execute(
            """
            UPDATE sessions
            SET billing_party_id = ?, updated_at = ?
            WHERE account_id = ? AND review_status != 'approved'
            """,
            (billing_party_id, now, account_id),
        )

        billing_delivery = payload.get("billing_delivery") or {}
        if billing_delivery:
            bp_update = {}
            for field in ("billing_email", "billing_phone", "billing_name", "billing_address_line_1",
                          "billing_address_line_2", "billing_city", "billing_state", "billing_postal_code",
                          "preferred_delivery_method", "administrative_notes", "organization_name"):
                if field in billing_delivery:
                    bp_update[field] = billing_delivery[field]
            if bp_update:
                bp_update["billing_party_id"] = billing_party_id
                update_billing_party(conn, billing_party_id, bp_update)

        admin_notes = payload.get("administrative_notes")
        if admin_notes is not None:
            conn.execute(
                "UPDATE client_accounts SET administrative_notes = ?, updated_at = ? WHERE account_id = ?",
                (admin_notes, now, account_id),
            )

        record_audit(conn, "client_account", account_id, "updated_billing_relationship", {
            "payer_kind": payer_kind,
            "covered_client_count": len(covered_client_ids),
        })
        _upsert_billing_relationship_key(
            conn,
            account_id=account_id,
            payer_kind=payer_kind,
            payer_person_id=payer_person_id,
            organization_billing_party_id=organization_billing_party_id,
            covered_client_ids=covered_client_ids,
        )

        conn.commit()
    except sqlite3.IntegrityError as error:
        conn.rollback()
        raise ValueError("This billing relationship already exists.") from error
    except Exception:
        conn.rollback()
        raise

    return get_account_record(conn, account_id)


def search_billing_parties(conn: sqlite3.Connection, query: str = "") -> list[dict[str, Any]]:
    init_db(conn)
    like = f"%{query}%"
    rows = conn.execute(
        """
        SELECT billing_party_id, billing_name, preferred_delivery_method
        FROM billing_parties
        WHERE billing_name LIKE ?
        ORDER BY billing_name
        LIMIT 20
        """,
        (like,),
    ).fetchall()
    return [dict(row) for row in rows]


def search_organization_billing_parties(conn: sqlite3.Connection, query: str = "") -> list[dict[str, Any]]:
    """Search active organization billing parties only."""
    init_db(conn)
    like = f"%{query}%"
    rows = conn.execute(
        """
        SELECT billing_party_id, billing_name, organization_name
        FROM billing_parties
        WHERE billing_name LIKE ?
          AND billing_party_type = 'organization'
          AND active = 1
        ORDER BY billing_name
        LIMIT 20
        """,
        (like,),
    ).fetchall()
    return [dict(row) for row in rows]


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return text(value).strip() or None


def _begin_immediate(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as error:
        if "locked" in str(error).lower():
            raise DatabaseBusyError("Database is currently locked.") from error
        raise


def _normalized_covered_client_ids(covered_client_ids: list[str]) -> list[str]:
    return sorted({str(cid).strip() for cid in covered_client_ids if str(cid).strip()})


def _covered_client_key(covered_client_ids: list[str]) -> str:
    return ",".join(_normalized_covered_client_ids(covered_client_ids))


def _payer_identity_key(
    payer_kind: str,
    payer_person_id: str | None,
    organization_billing_party_id: str | None,
) -> str:
    if payer_kind == "organization":
        return f"organization:{organization_billing_party_id or ''}"
    return f"person:{payer_person_id or ''}"


def _canonical_billing_party_for_person(
    conn: sqlite3.Connection,
    person_id: str,
    *,
    preferred_account_id: str | None = None,
) -> str:
    if preferred_account_id:
        current = conn.execute(
            """
            SELECT bp.billing_party_id
            FROM client_accounts ca
            JOIN billing_parties bp ON bp.billing_party_id = ca.default_billing_party_id
            WHERE ca.account_id = ?
              AND bp.person_id = ?
              AND bp.active = 1
            LIMIT 1
            """,
            (preferred_account_id, person_id),
        ).fetchone()
        if current:
            return current["billing_party_id"]

    active_relationship_party = conn.execute(
        """
        SELECT
          ca.default_billing_party_id AS billing_party_id,
          COUNT(*) AS relationship_count,
          MAX(bp.updated_at) AS latest_bp_update
        FROM client_accounts ca
        JOIN billing_parties bp ON bp.billing_party_id = ca.default_billing_party_id
        WHERE ca.active = 1
          AND bp.person_id = ?
          AND bp.active = 1
        GROUP BY ca.default_billing_party_id
        ORDER BY relationship_count DESC, latest_bp_update DESC, ca.default_billing_party_id
        LIMIT 1
        """,
        (person_id,),
    ).fetchone()
    if active_relationship_party:
        return active_relationship_party["billing_party_id"]
    return billing_party_for_person(conn, person_id, commit=False)


def _upsert_billing_relationship_key(
    conn: sqlite3.Connection,
    *,
    account_id: str,
    payer_kind: str,
    payer_person_id: str | None,
    organization_billing_party_id: str | None,
    covered_client_ids: list[str],
) -> None:
    conn.execute(
        """
        DELETE FROM billing_relationship_keys
        WHERE account_id IN (
          SELECT brk.account_id
          FROM billing_relationship_keys brk
          LEFT JOIN client_accounts ca ON ca.account_id = brk.account_id
          WHERE ca.account_id IS NULL OR ca.active = 0
        )
        """
    )
    payer_identity = _payer_identity_key(payer_kind, payer_person_id, organization_billing_party_id)
    payer_billing_party_id = (
        organization_billing_party_id if payer_kind == "organization" else None
    )
    now = now_iso()
    conn.execute("DELETE FROM billing_relationship_keys WHERE account_id = ?", (account_id,))
    conn.execute(
        """
        INSERT INTO billing_relationship_keys (
          account_id,
          payer_identity_key,
          payer_kind,
          payer_person_id,
          payer_billing_party_id,
          covered_client_key,
          active,
          created_at,
          updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            account_id,
            payer_identity,
            payer_kind,
            payer_person_id,
            payer_billing_party_id,
            _covered_client_key(covered_client_ids),
            now,
            now,
        ),
    )


def _delete_billing_relationship_key(conn: sqlite3.Connection, account_id: str) -> None:
    conn.execute("DELETE FROM billing_relationship_keys WHERE account_id = ?", (account_id,))


def _account_duplicate_probe(
    conn: sqlite3.Connection,
    account_id: str,
) -> tuple[str, str | None, str | None, list[str]] | None:
    row = conn.execute(
        """
        SELECT
          bp.billing_party_type,
          bp.person_id AS payer_person_id,
          bp.billing_party_id AS billing_party_id
        FROM client_accounts ca
        LEFT JOIN billing_parties bp ON bp.billing_party_id = ca.default_billing_party_id
        WHERE ca.account_id = ?
        """,
        (account_id,),
    ).fetchone()
    if not row or not row["billing_party_id"]:
        return None
    covered_client_ids = [
        member["person_id"]
        for member in conn.execute(
            "SELECT person_id FROM account_members WHERE account_id = ?",
            (account_id,),
        ).fetchall()
        if member["person_id"]
    ]
    payer_kind = "organization" if row["billing_party_type"] == "organization" else "person"
    organization_billing_party_id = row["billing_party_id"] if payer_kind == "organization" else None
    return payer_kind, row["payer_person_id"], organization_billing_party_id, covered_client_ids


def create_billing_party(conn: sqlite3.Connection, data: dict[str, Any], *, commit: bool = True) -> dict[str, Any]:
    if commit:
        init_db(conn)
    now = now_iso()
    billing_name = text(data.get("billing_name") or data.get("display_name") or data.get("name") or "").strip()
    if not billing_name:
        raise ValueError("Billing name is required.")
    delivery_method = data.get("preferred_delivery_method") or "unresolved"
    if delivery_method not in {"email", "mail", "both", "unresolved"}:
        raise ValueError("Invalid preferred delivery method.")
    billing_party_type = data.get("billing_party_type") or "person"
    if billing_party_type not in {"person", "organization"}:
        raise ValueError("Invalid billing party type.")
    person_id = data.get("person_id") or None
    if person_id and billing_party_type == "person":
        person = conn.execute(
            "SELECT person_id FROM people WHERE person_id = ? AND active = 1", (person_id,)
        ).fetchone()
        if not person:
            raise ValueError("Referenced person does not exist or is not active.")
    org_name = _normalize_optional_text(data.get("organization_name"))
    if billing_party_type == "organization" and org_name:
        existing_org = conn.execute(
            """
            SELECT * FROM billing_parties
            WHERE lower(organization_name) = lower(?)
              AND billing_party_type = 'organization'
              AND active = 1
            LIMIT 1
            """,
            (org_name,),
        ).fetchone()
        if existing_org:
            result = dict(existing_org)
            result["created"] = False
            result["existing"] = True
            return result
    billing_party_id = new_id()
    conn.execute(
        """
        INSERT INTO billing_parties (
          billing_party_id, billing_party_type, person_id, organization_name,
          billing_name, billing_email, billing_address_line_1, billing_address_line_2,
          billing_city, billing_state, billing_postal_code, billing_phone,
          preferred_delivery_method, administrative_notes,
          created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            billing_party_id,
            billing_party_type,
            person_id,
            org_name,
            billing_name,
            _normalize_optional_text(data.get("billing_email")),
            _normalize_optional_text(data.get("billing_address_line_1")),
            _normalize_optional_text(data.get("billing_address_line_2")),
            _normalize_optional_text(data.get("billing_city")),
            _normalize_optional_text(data.get("billing_state")),
            _normalize_optional_text(data.get("billing_postal_code")),
            _normalize_optional_text(data.get("billing_phone")),
            delivery_method,
            _normalize_optional_text(data.get("administrative_notes")),
            now,
            now,
        ),
    )
    record_audit(conn, "billing_party", billing_party_id, "created_inline", {"billing_name": billing_name, "billing_party_type": billing_party_type})
    if commit:
        conn.commit()
    result = dict(conn.execute("SELECT * FROM billing_parties WHERE billing_party_id = ?", (billing_party_id,)).fetchone())
    result["created"] = True
    result["existing"] = False
    return result


def billing_party_for_person(conn: sqlite3.Connection, person_id: str, *, commit: bool = True) -> str:
    if commit:
        init_db(conn)
    person = conn.execute("SELECT * FROM people WHERE person_id = ? AND active = 1", (person_id,)).fetchone()
    if not person:
        raise ValueError("Bill-to client must be a confirmed active person.")
    existing = conn.execute(
        """
        SELECT billing_party_id
        FROM billing_parties
        WHERE person_id = ? AND active = 1
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (person_id,),
    ).fetchone()
    if existing:
        return existing["billing_party_id"]
    created = create_billing_party(
        conn,
        {
            "billing_party_type": "person",
            "person_id": person_id,
            "billing_name": person["display_name"],
            "billing_email": person["billing_email"],
            "billing_phone": person["billing_phone"],
        },
        commit=commit,
    )
    return created["billing_party_id"]


def update_billing_party(conn: sqlite3.Connection, billing_party_id: str, data: dict[str, Any]) -> dict[str, Any]:
    init_db(conn)
    existing = conn.execute("SELECT * FROM billing_parties WHERE billing_party_id = ?", (billing_party_id,)).fetchone()
    if not existing:
        raise ValueError("Billing party not found.")
    now = now_iso()

    # --- Required fields: key-presence check, reject blank ---
    if "billing_name" in data:
        billing_name = text(data.get("billing_name") or "").strip()
        if not billing_name:
            raise ValueError("billing_name must not be blank.")
    else:
        billing_name = existing["billing_name"]

    if "preferred_delivery_method" in data:
        delivery_method = data.get("preferred_delivery_method") or "unresolved"
        if delivery_method not in {"email", "mail", "both", "unresolved"}:
            raise ValueError("Invalid preferred delivery method.")
    else:
        delivery_method = existing["preferred_delivery_method"]

    if "billing_party_type" in data:
        billing_party_type = data.get("billing_party_type") or "person"
        if billing_party_type not in {"person", "organization"}:
            raise ValueError("Invalid billing party type.")
    else:
        billing_party_type = existing["billing_party_type"]

    # --- person_id: reject reassignment ---
    if "person_id" in data:
        new_person_id = data.get("person_id") or None
        if new_person_id and new_person_id != existing["person_id"]:
            raise ValueError("Cannot reassign billing party to a different person through this operation.")
        person_id = new_person_id or existing["person_id"]
    else:
        person_id = existing["person_id"]

    # --- Optional clearable fields: key-presence → clear to None, omit → preserve ---
    optional_text_fields = [
        "billing_email",
        "billing_phone",
        "billing_address_line_1",
        "billing_address_line_2",
        "billing_city",
        "billing_state",
        "billing_postal_code",
        "administrative_notes",
    ]
    updates: dict[str, str | None] = {}
    for field in optional_text_fields:
        if field in data:
            updates[field] = _normalize_optional_text(data.get(field))
        else:
            updates[field] = existing[field]

    # --- organization_name: same partial-update semantics ---
    if "organization_name" in data:
        organization_name = _normalize_optional_text(data.get("organization_name"))
    else:
        organization_name = existing["organization_name"]

    # --- active: key-presence check, omit preserves existing ---
    if "active" in data:
        active = 1 if data.get("active") else 0
    else:
        active = existing["active"]

    conn.execute(
        """
        UPDATE billing_parties
        SET billing_party_type = ?,
            person_id = ?,
            organization_name = ?,
            billing_name = ?,
            billing_email = ?,
            billing_address_line_1 = ?,
            billing_address_line_2 = ?,
            billing_city = ?,
            billing_state = ?,
            billing_postal_code = ?,
            billing_phone = ?,
            preferred_delivery_method = ?,
            administrative_notes = ?,
            active = ?,
            updated_at = ?
        WHERE billing_party_id = ?
        """,
        (
            billing_party_type,
            person_id,
            organization_name,
            billing_name,
            updates["billing_email"],
            updates["billing_address_line_1"],
            updates["billing_address_line_2"],
            updates["billing_city"],
            updates["billing_state"],
            updates["billing_postal_code"],
            updates["billing_phone"],
            delivery_method,
            updates["administrative_notes"],
            active,
            now,
            billing_party_id,
        ),
    )

    # --- Audit: record which fields changed, without exposing values ---
    changed_fields: list[str] = []
    all_fields = (
        optional_text_fields
        + ["billing_name", "preferred_delivery_method", "billing_party_type", "person_id", "organization_name", "active"]
    )
    new_values: dict[str, Any] = {
        "billing_name": billing_name,
        "preferred_delivery_method": delivery_method,
        "billing_party_type": billing_party_type,
        "person_id": person_id,
        "organization_name": organization_name,
        "active": active,
        "billing_email": updates["billing_email"],
        "billing_phone": updates["billing_phone"],
        "billing_address_line_1": updates["billing_address_line_1"],
        "billing_address_line_2": updates["billing_address_line_2"],
        "billing_city": updates["billing_city"],
        "billing_state": updates["billing_state"],
        "billing_postal_code": updates["billing_postal_code"],
        "administrative_notes": updates["administrative_notes"],
    }
    for field in all_fields:
        if field in data:
            old_val = existing[field]
            new_val = new_values[field]
            if old_val != new_val:
                changed_fields.append(field)
    audit_detail: dict[str, Any] = {"changed_fields": changed_fields}
    if "active" in data:
        audit_detail["active_changed_to"] = active
    record_audit(conn, "billing_party", billing_party_id, "updated_inline", audit_detail)
    conn.commit()
    return dict(conn.execute("SELECT * FROM billing_parties WHERE billing_party_id = ?", (billing_party_id,)).fetchone())


_COPYABLE_CONTACT_FIELDS = [
    "billing_email",
    "billing_address_line_1",
    "billing_address_line_2",
    "billing_city",
    "billing_state",
    "billing_postal_code",
]


def preview_copy_contact_details(
    conn: sqlite3.Connection,
    target_billing_party_id: str,
    source_billing_party_id: str,
) -> dict[str, Any]:
    """Preview which contact fields would be copied from an inactive billing setup to an active one.

    Only fields that are empty on the target and populated on the source are included.
    Does not modify any data.
    """
    init_db(conn)
    target = conn.execute(
        "SELECT * FROM billing_parties WHERE billing_party_id = ?", (target_billing_party_id,)
    ).fetchone()
    if not target:
        raise ValueError("Target billing party not found.")
    if not target["active"]:
        raise ValueError("Target billing party must be active.")

    source = conn.execute(
        "SELECT * FROM billing_parties WHERE billing_party_id = ?", (source_billing_party_id,)
    ).fetchone()
    if not source:
        raise ValueError("Source billing party not found.")
    if source["active"]:
        raise ValueError("Source billing party must be inactive.")

    if target["person_id"] != source["person_id"]:
        raise ValueError("Source and target billing parties must belong to the same person.")

    fields_to_copy: list[dict[str, str]] = []
    for field in _COPYABLE_CONTACT_FIELDS:
        source_val = _normalize_optional_text(source[field])
        target_val = _normalize_optional_text(target[field])
        if source_val and not target_val:
            fields_to_copy.append({"field": field, "value": source_val})

    delivery_method = None
    source_delivery = str(source["preferred_delivery_method"] or "unresolved").strip()
    target_delivery = str(target["preferred_delivery_method"] or "unresolved").strip()
    if source_delivery in ("email", "mail", "both") and target_delivery == "unresolved":
        delivery_method = source_delivery

    return {
        "target_billing_party_id": target_billing_party_id,
        "source_billing_party_id": source_billing_party_id,
        "fields_to_copy": fields_to_copy,
        "delivery_method_to_copy": delivery_method,
        "target_billing_name": target["billing_name"],
        "source_billing_name": source["billing_name"],
    }


def apply_copy_contact_details(
    conn: sqlite3.Connection,
    target_billing_party_id: str,
    source_billing_party_id: str,
    *,
    confirmed_fields: list[str] | None = None,
    copy_delivery_method: bool = False,
) -> dict[str, Any]:
    """Copy contact details from an inactive billing setup to an active one.

    Only copies fields that are empty on the target and populated on the source.
    Does not overwrite existing active values.
    Does not reactivate the inactive setup.
    Does not modify approved sessions or finalized invoices.
    """
    init_db(conn)
    preview = preview_copy_contact_details(conn, target_billing_party_id, source_billing_party_id)

    allowed = set(confirmed_fields) if confirmed_fields is not None else {f["field"] for f in preview["fields_to_copy"]}
    update_data: dict[str, Any] = {}
    copied_fields: list[str] = []
    for item in preview["fields_to_copy"]:
        if item["field"] in allowed:
            update_data[item["field"]] = item["value"]
            copied_fields.append(item["field"])

    if copy_delivery_method and preview["delivery_method_to_copy"]:
        update_data["preferred_delivery_method"] = preview["delivery_method_to_copy"]
        copied_fields.append("preferred_delivery_method")

    if not update_data:
        return {
            "target_billing_party_id": target_billing_party_id,
            "copied_fields": [],
            "message": "No fields were copied.",
        }

    result = update_billing_party(conn, target_billing_party_id, update_data)

    record_audit(
        conn,
        "billing_party",
        target_billing_party_id,
        "copied_contact_from_inactive",
        {
            "source_billing_party_id": source_billing_party_id,
            "copied_fields": copied_fields,
        },
    )
    conn.commit()

    return {
        "target_billing_party_id": target_billing_party_id,
        "copied_fields": copied_fields,
        "billing_party": result,
        "message": f"Copied {', '.join(copied_fields)} from inactive setup to active setup.",
    }


def add_account_member(
    conn: sqlite3.Connection,
    account_id: str,
    person_id: str,
    relationship_role: str = "primary",
    is_primary: bool = False,
    *,
    commit: bool = True,
) -> str:
    if commit:
        init_db(conn)
    existing = conn.execute(
        """
        SELECT account_member_id FROM account_members
        WHERE account_id = ? AND person_id = ?
        LIMIT 1
        """,
        (account_id, person_id),
    ).fetchone()
    if existing:
        raise ValueError("This client is already included in this billing relationship.")
    now = now_iso()
    member_id = new_id()
    conn.execute(
        """
        INSERT INTO account_members (
          account_member_id, account_id, person_id, relationship_role,
          is_primary, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (member_id, account_id, person_id, relationship_role, 1 if is_primary else 0, now, now),
    )
    record_audit(conn, "account_member", member_id, "created_inline", {"account_id": account_id, "person_id": person_id})
    if commit:
        conn.commit()
    return member_id


def ensure_account_member(
    conn: sqlite3.Connection,
    account_id: str,
    person_id: str,
    relationship_role: str = "primary",
    is_primary: bool = False,
) -> str:
    existing = conn.execute(
        """
        SELECT account_member_id FROM account_members
        WHERE account_id = ? AND person_id = ?
        LIMIT 1
        """,
        (account_id, person_id),
    ).fetchone()
    now = now_iso()
    if existing:
        conn.execute(
            """
            UPDATE account_members
            SET relationship_role = ?,
                is_primary = CASE WHEN ? THEN 1 ELSE is_primary END,
                updated_at = ?
            WHERE account_member_id = ?
            """,
            (relationship_role, 1 if is_primary else 0, now, existing["account_member_id"]),
        )
        return existing["account_member_id"]
    member_id = new_id()
    conn.execute(
        """
        INSERT INTO account_members (
          account_member_id, account_id, person_id, relationship_role,
          is_primary, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (member_id, account_id, person_id, relationship_role, 1 if is_primary else 0, now, now),
    )
    return member_id


def remove_account_member(
    conn: sqlite3.Connection,
    account_id: str,
    person_id: str,
    *,
    commit: bool = True,
) -> None:
    """Remove a covered client from a billing relationship.

    Does NOT delete the person record. Does NOT alter historical sessions.
    Writes an audit entry.
    """
    if commit:
        init_db(conn)
    existing = conn.execute(
        "SELECT account_member_id FROM account_members WHERE account_id = ? AND person_id = ?",
        (account_id, person_id),
    ).fetchone()
    if not existing:
        raise ValueError("This client is not a member of this billing relationship.")
    conn.execute(
        "DELETE FROM account_members WHERE account_member_id = ?",
        (existing["account_member_id"],),
    )
    record_audit(conn, "account_member", existing["account_member_id"], "removed", {
        "account_id": account_id, "person_id": person_id,
    })
    if commit:
        conn.commit()


def add_session_participant(
    conn: sqlite3.Connection,
    session_id: str,
    person_id: str | None,
    participant_name: str,
    is_primary: bool = False,
    participant_role: str = "participant",
) -> str:
    now = now_iso()
    participant_id = new_id()
    display_name = participant_name
    if person_id and not display_name:
        person = conn.execute("SELECT display_name FROM people WHERE person_id = ?", (person_id,)).fetchone()
        display_name = person["display_name"] if person else ""
    conn.execute(
        """
        INSERT INTO session_participants (
          session_participant_id, session_id, person_id, participant_name,
          participant_role, is_primary, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (participant_id, session_id, person_id, display_name, participant_role, 1 if is_primary else 0, now, now),
    )
    return participant_id


def proposed_participants_from_candidate(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
) -> list[dict[str, Any]]:
    names = parse_json(row["candidate_person_names"] if "candidate_person_names" in row.keys() else None, [])
    if not isinstance(names, list) or not names:
        proposed = text(row["proposed_client_name"] if "proposed_client_name" in row.keys() else "")
        names = [proposed] if proposed else []
    participants = []
    for index, name in enumerate(names):
        display_name = text(name)
        if not display_name:
            continue
        matches = find_active_people_by_exact_identity_match(conn, display_name)
        participant = {
            "session_participant_id": None,
            "session_id": row["id"] if "id" in row.keys() else None,
            "person_id": None,
            "participant_name": display_name,
            "display_name": display_name,
            "participant_role": "primary" if index == 0 else "participant",
            "is_primary": index == 0,
            "is_proposed": True,
            "source": "parser_candidate",
        }
        if len(matches) == 1:
            person = matches[0]
            participant.update(
                {
                    "person_id": person["person_id"],
                    "display_name": person["display_name"],
                    "first_name": person["first_name"],
                    "last_name": person["last_name"],
                    "billing_email": person["billing_email"],
                    "billing_phone": person["billing_phone"],
                    "is_proposed": False,
                    "source": "exact_person_match",
                }
            )
        participants.append(participant)
    return participants


def participants_were_explicitly_saved(conn: sqlite3.Connection, session_id: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM audit_log
        WHERE entity_type = 'session'
          AND entity_id = ?
          AND action IN ('relationship_section_saved', 'interpretation_saved')
          AND details LIKE '%"participants"%'
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()
    return bool(row)


def resolve_confirmed_participant_person(
    conn: sqlite3.Connection,
    person_id: str | None,
    participant_name: str,
    participant: dict[str, Any] | None = None,
) -> str | None:
    if person_id:
        return person_id
    display_name = text(participant_name)
    if not display_name:
        return None
    matches = find_active_people_by_exact_identity_match(conn, display_name)
    if len(matches) == 1:
        return matches[0]["person_id"]
    if len(matches) > 1:
        return None
    if not is_usable_new_person_name(display_name):
        return None
    participant = participant or {}
    created = create_person(
        conn,
        {
            "display_name": display_name,
            "first_name": participant.get("first_name"),
            "last_name": participant.get("last_name"),
            "billing_email": participant.get("billing_email"),
            "billing_phone": participant.get("billing_phone"),
        },
    )
    return created["person_id"]


def normalize_exact_participant_name(value: str) -> str:
    return normalize_alias(value)


def find_active_people_by_exact_normalized_name(
    conn: sqlite3.Connection,
    display_name: str,
) -> list[sqlite3.Row]:
    normalized = normalize_exact_participant_name(display_name)
    if not normalized:
        return []
    rows = conn.execute(
        """
        SELECT person_id, display_name, first_name, last_name, billing_email, billing_phone
        FROM people
        WHERE active = 1
        """
    ).fetchall()
    return [
        row
        for row in rows
        if normalize_exact_participant_name(row["display_name"]) == normalized
    ]


def find_active_people_by_exact_approved_alias(
    conn: sqlite3.Connection,
    raw_alias: str,
) -> list[sqlite3.Row]:
    normalized = normalize_exact_participant_name(raw_alias)
    if not normalized:
        return []
    rows = conn.execute(
        """
        SELECT DISTINCT p.person_id, p.display_name, p.first_name, p.last_name, p.billing_email, p.billing_phone
        FROM calendar_aliases ca
        JOIN people p ON p.person_id = ca.person_id
        WHERE ca.approved_by_user = 1
          AND ca.normalized_alias = ?
          AND p.active = 1
        ORDER BY p.display_name
        """,
        (normalized,),
    ).fetchall()
    return list(rows)


def find_active_people_by_exact_identity_match(
    conn: sqlite3.Connection,
    display_name: str,
) -> list[sqlite3.Row]:
    name_matches = find_active_people_by_exact_normalized_name(conn, display_name)
    if name_matches:
        return name_matches
    return find_active_people_by_exact_approved_alias(conn, display_name)


def is_usable_new_person_name(display_name: str) -> bool:
    name = text(display_name)
    if not name:
        return False
    lowered = f" {name.lower()} "
    ambiguous_tokens = (" + ", " & ", " and ", ",", "/", "\\", ";", " for ")
    if any(token in lowered for token in ambiguous_tokens):
        return False
    first, last = split_name(name)
    return bool(first and last)


def refresh_candidate_suggestions(
    conn: sqlite3.Connection,
    candidate_id: str,
    *,
    preserve_approved_rate: bool = False,
) -> dict[str, Any]:
    session = session_for_candidate(conn, candidate_id)
    participants = get_session_participants(conn, session["id"])
    primary_person_id = next((p["person_id"] for p in participants if p.get("is_primary") and p.get("person_id")), None)
    now = now_iso()
    account_id = session["account_id"]
    billing_party_id = session["billing_party_id"]

    if not primary_person_id and len(participants) == 1 and participants[0].get("person_id"):
        primary_person_id = participants[0]["person_id"]
        conn.execute(
            "UPDATE session_participants SET is_primary = 1, participant_role = 'primary', updated_at = ? WHERE session_participant_id = ?",
            (now, participants[0]["session_participant_id"]),
        )

    duration = session["approved_duration_minutes"] or session["duration_minutes"]
    service_mode = normalize_service_mode(session["service_mode"])
    billing_session_type = session["billing_session_type"] or None
    custom_service_description = session["custom_service_description"] if "custom_service_description" in session.keys() else None
    custom_service_code = session["custom_service_code"] if "custom_service_code" in session.keys() else None
    time_category = normalize_time_category(session["time_category"])
    participant_person_ids = [p["person_id"] for p in participants if p.get("person_id")]
    suggestion = suggest_rate(
        conn,
        session_date=session["session_date"] or text(session["start_at"])[:10],
        duration_minutes=duration,
        billing_session_type=billing_session_type,
        appointment_status=session["appointment_status"],
        custom_service_description=custom_service_description,
        custom_service_code=custom_service_code,
        service_mode=service_mode,
        rate_group=session["rate_group"] or rate_group_for(service_mode),
        time_category=time_category,
        account_id=account_id,
        person_id=primary_person_id,
        participant_person_ids=participant_person_ids,
    )
    approved_rate = session["approved_rate_cents"] if preserve_approved_rate else session["approved_rate_cents"]
    if suggestion.suggested_rate_cents is not None:
        conn.execute(
            """
            UPDATE sessions
            SET suggested_rate_cents = ?,
                scheduled_rate_cents = COALESCE(scheduled_rate_cents, ?),
                rate_rule_id = ?,
                rate_source = ?,
                rate_needs_review = ?,
                rate_override_reason = COALESCE(rate_override_reason, ?),
                updated_at = ?
            WHERE id = ?
            """,
            (
                suggestion.suggested_rate_cents,
                suggestion.suggested_rate_cents,
                suggestion.rate_rule_id,
                suggestion.rate_source,
                1 if suggestion.rate_needs_review and approved_rate is None else 0,
                suggestion.explanation,
                now,
                session["id"],
            ),
        )
    else:
        should_clear_rule_suggestion = text(session["rate_source"]) in {
            "default",
            "person_exception",
            "billing_relationship",
            "account",
            "participant_combination_exception",
            "none",
        }
        if should_clear_rule_suggestion:
            conn.execute(
                """
                UPDATE sessions
                SET suggested_rate_cents = NULL,
                    rate_rule_id = NULL,
                    rate_source = 'none',
                    rate_needs_review = CASE WHEN approved_rate_cents IS NULL THEN 1 ELSE 0 END,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, session["id"]),
            )

    refreshed = session_for_candidate(conn, candidate_id)
    unresolved = unresolved_from_values(
        conn=conn,
        participants=get_session_participants(conn, refreshed["id"]),
        billing_party_id=refreshed["billing_party_id"],
        account_id=refreshed["account_id"],
        duration=refreshed["approved_duration_minutes"] or refreshed["duration_minutes"],
        billing_session_type=refreshed["billing_session_type"],
        service_mode=refreshed["service_mode"],
        time_category=refreshed["time_category"],
        approved_rate_cents=refreshed["approved_rate_cents"],
        suggested_rate_cents=refreshed["suggested_rate_cents"],
        rate_rule_id=refreshed["rate_rule_id"],
        rate_needs_review=refreshed["rate_needs_review"],
        payment_status=refreshed["payment_status"],
        appointment_status=refreshed["appointment_status"],
        billing_treatment=refreshed["billing_treatment"],
        custom_service_description=refreshed["custom_service_description"] if "custom_service_description" in refreshed.keys() else None,
    )
    review_status = "ready_for_approval" if not unresolved else status_from_unresolved(unresolved)
    conn.execute(
        "UPDATE sessions SET review_status = ?, updated_at = ? WHERE id = ?",
        (review_status, now, refreshed["id"]),
    )
    conn.execute(
        "UPDATE calendar_event_candidates SET review_status = ?, unresolved_fields = ?, review_reasons = ?, updated_at = ? WHERE id = ?",
        (review_status, json_dumps(unresolved), json_dumps([suggestion.explanation]), now, candidate_id),
    )
    add_review_item(conn, candidate_id, refreshed["id"], review_status, unresolved, [suggestion.explanation])
    return {"review_status": review_status, "unresolved_fields": unresolved, "rate_explanation": suggestion.explanation}


def get_account_members(conn: sqlite3.Connection, account_id: str | None) -> list[dict[str, Any]]:
    if not account_id:
        return []
    rows = conn.execute(
        """
        SELECT am.*, p.display_name, p.person_code
        FROM account_members am
        JOIN people p ON p.person_id = am.person_id
        WHERE am.account_id = ?
        ORDER BY am.is_primary DESC, p.display_name
        """,
        (account_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def apply_smart_prefill(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT s.*, c.confidence, c.candidate_person_names
        FROM sessions s
        JOIN calendar_event_candidates c ON c.id = s.candidate_id
        WHERE s.review_status != 'approved'
          AND (
            s.account_id IS NULL
            OR s.billing_party_id IS NULL
            OR s.id NOT IN (SELECT session_id FROM session_participants WHERE person_id IS NOT NULL)
          )
        """
    ).fetchall()
    updated = 0
    for session in rows:
        aliases = aliases_for_session(conn, session)
        if not aliases:
            continue
        alias = aliases[0]
        now = now_iso()
        if not session["account_id"] and alias["account_id"]:
            conn.execute(
                "UPDATE sessions SET account_id = ?, updated_at = ? WHERE id = ?",
                (alias["account_id"], now, session["id"]),
            )
            updated += 1
        if alias["person_id"]:
            participant_exists = conn.execute(
                """
                SELECT 1 FROM session_participants
                WHERE session_id = ? AND person_id = ?
                LIMIT 1
                """,
                (session["id"], alias["person_id"]),
            ).fetchone()
            if not participant_exists:
                person = conn.execute(
                    "SELECT display_name FROM people WHERE person_id = ?",
                    (alias["person_id"],),
                ).fetchone()
                conn.execute(
                    """
                    DELETE FROM session_participants
                    WHERE session_id = ? AND person_id IS NULL
                    """,
                    (session["id"],),
                )
                conn.execute(
                    """
                    INSERT INTO session_participants (
                      session_participant_id, session_id, person_id, participant_name,
                      participant_role, is_primary, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'primary', 1, ?, ?)
                    """,
                    (
                        new_id(),
                        session["id"],
                        alias["person_id"],
                        person["display_name"] if person else "",
                        now,
                        now,
                    ),
                )
                updated += 1
        account_id = alias["account_id"] or session["account_id"]
        if account_id and not session["billing_party_id"]:
            account = conn.execute(
                "SELECT default_billing_party_id FROM client_accounts WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            if account and account["default_billing_party_id"]:
                conn.execute(
                    "UPDATE sessions SET billing_party_id = ?, updated_at = ? WHERE id = ?",
                    (account["default_billing_party_id"], now, session["id"]),
                )
                updated += 1

    updated += _auto_link_exact_name_participants(conn)
    if updated:
        conn.commit()
    return updated


def _auto_link_exact_name_participants(conn: sqlite3.Connection) -> int:
    null_participants = conn.execute(
        """
        SELECT sp.*, s.review_status, s.account_id, s.billing_party_id
        FROM session_participants sp
        JOIN sessions s ON s.id = sp.session_id
        WHERE sp.person_id IS NULL
          AND s.review_status != 'approved'
        """
    ).fetchall()
    linked = 0
    for sp in null_participants:
        name = text(sp["participant_name"])
        if not name:
            continue
        matches = find_active_people_by_exact_identity_match(conn, name)
        if len(matches) != 1:
            continue
        person = matches[0]
        now = now_iso()
        conn.execute(
            "UPDATE session_participants SET person_id = ?, updated_at = ? WHERE session_participant_id = ?",
            (person["person_id"], now, sp["session_participant_id"]),
        )
        record_audit(
            conn,
            "session",
            sp["session_id"],
            "automatic_exact_name_match",
            {
                "participant_name": name,
                "matched_person_id": person["person_id"],
                "matched_display_name": person["display_name"],
            },
        )
        linked += 1

        session_billing_party_id = sp["billing_party_id"]
        if session_billing_party_id:
            continue

        account_id = sp["account_id"]
        assigned_bp_id = None
        bp_source = None
        if account_id:
            account = conn.execute(
                "SELECT default_billing_party_id FROM client_accounts WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            if account and account["default_billing_party_id"]:
                assigned_bp_id = account["default_billing_party_id"]
                bp_source = "account_default"

        if not assigned_bp_id:
            bp_rows = conn.execute(
                """
                SELECT billing_party_id FROM billing_parties
                WHERE person_id = ? AND active = 1
                ORDER BY updated_at DESC
                """,
                (person["person_id"],),
            ).fetchall()
            if len(bp_rows) == 1:
                assigned_bp_id = bp_rows[0]["billing_party_id"]
                bp_source = "person_default"

        if assigned_bp_id:
            conn.execute(
                "UPDATE sessions SET billing_party_id = ?, updated_at = ? WHERE id = ?",
                (assigned_bp_id, now, sp["session_id"]),
            )
            record_audit(
                conn,
                "session",
                sp["session_id"],
                "automatic_billing_party_assigned",
                {
                    "billing_party_id": assigned_bp_id,
                    "source": bp_source,
                    "person_id": person["person_id"],
                },
            )
    return linked


def aliases_for_session(conn: sqlite3.Connection, session: sqlite3.Row) -> list[sqlite3.Row]:
    candidates = {
        normalize_alias(session["raw_calendar_title"]),
        normalize_alias(session["proposed_client_name"]),
    }
    stripped = strip_calendar_shorthand(text(session["raw_calendar_title"]))
    if stripped:
        candidates.add(normalize_alias(stripped))
    placeholders = ",".join("?" for _ in candidates)
    if not placeholders:
        return []
    return conn.execute(
        f"""
        SELECT *
        FROM calendar_aliases
        WHERE approved_by_user = 1
          AND normalized_alias IN ({placeholders})
        ORDER BY confidence DESC, updated_at DESC
        """,
        tuple(candidates),
    ).fetchall()


def search_table(
    conn: sqlite3.Connection,
    table: str,
    id_column: str,
    label_column: str,
    query: str,
    code_column: str | None = None,
) -> list[dict[str, Any]]:
    init_db(conn)
    like = f"%{query}%"
    fields = f"{id_column}, {label_column}" + (f", {code_column}" if code_column else "")
    rows = conn.execute(
        f"""
        SELECT {fields}
        FROM {table}
        WHERE {label_column} LIKE ?
        ORDER BY {label_column}
        LIMIT 20
        """,
        (like,),
    ).fetchall()
    return [dict(row) for row in rows]


def session_for_candidate(conn: sqlite3.Connection, candidate_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM sessions WHERE candidate_id = ?", (candidate_id,)).fetchone()
    if not row:
        row = _ensure_review_session_for_candidate(
            conn,
            candidate_id,
            reason="Created session while saving review candidate.",
        )
    return row


def get_session_participants(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT sp.*, p.display_name, p.first_name, p.last_name, p.billing_email, p.billing_phone
        FROM session_participants sp
        LEFT JOIN people p ON p.person_id = sp.person_id
        WHERE sp.session_id = ?
        ORDER BY sp.is_primary DESC, sp.participant_name
        """,
        (session_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_account(conn: sqlite3.Connection, account_id: str | None) -> dict[str, Any] | None:
    if not account_id:
        return None
    row = conn.execute("SELECT * FROM client_accounts WHERE account_id = ?", (account_id,)).fetchone()
    return dict(row) if row else None


def get_billing_party(conn: sqlite3.Connection, billing_party_id: str | None) -> dict[str, Any] | None:
    if not billing_party_id:
        return None
    row = conn.execute("SELECT * FROM billing_parties WHERE billing_party_id = ?", (billing_party_id,)).fetchone()
    return dict(row) if row else None


def get_organization_billing_record(conn: sqlite3.Connection, billing_party_id: str) -> dict[str, Any]:
    """Return a complete read-only organization billing-party record.

    Raises ValueError if the billing party does not exist or is not an organization.
    Performs no writes.
    """
    init_db(conn)
    row = conn.execute(
        "SELECT * FROM billing_parties WHERE billing_party_id = ?",
        (billing_party_id,),
    ).fetchone()
    if not row:
        raise BillingPartyNotFoundError("Billing party not found.")
    if row["billing_party_type"] != "organization":
        raise BillingPartyTypeError(
            "Person-linked billing parties use the client endpoint at /api/people/{person_id}."
        )

    bp = dict(row)

    # --- Covered clients ---
    covered_rows = conn.execute(
        """
        SELECT DISTINCT
          p.person_id,
          p.display_name,
          p.person_code,
          COUNT(DISTINCT s.id) AS session_count,
          MAX(s.session_date) AS latest_session_date
        FROM sessions s
        JOIN session_participants sp ON sp.session_id = s.id AND sp.person_id IS NOT NULL
        JOIN people p ON p.person_id = sp.person_id
        WHERE s.billing_party_id = ?
        GROUP BY p.person_id, p.display_name, p.person_code
        ORDER BY p.display_name
        """,
        (billing_party_id,),
    ).fetchall()
    covered_clients = [dict(r) for r in covered_rows]

    # --- Sessions ---
    session_rows = conn.execute(
        """
        SELECT
          s.id AS session_id,
          s.candidate_id,
          s.session_date,
          s.start_at,
          s.duration_minutes,
          s.approved_duration_minutes,
          s.billing_session_type,
          s.custom_service_description,
          s.time_category,
          s.approved_rate_cents,
          s.review_status,
          s.payment_status,
          s.appointment_status,
          (
            SELECT GROUP_CONCAT(COALESCE(p2.display_name, sp2.participant_name), ', ')
            FROM session_participants sp2
            LEFT JOIN people p2 ON p2.person_id = sp2.person_id
            WHERE sp2.session_id = s.id
          ) AS participant_names,
          (
            SELECT i.invoice_number
            FROM invoice_line_items li
            JOIN invoices i ON i.invoice_id = li.invoice_id
            WHERE li.source_session_id = s.id AND i.status IN ('draft', 'finalized')
            LIMIT 1
          ) AS invoice_number,
          (
            SELECT i.invoice_id
            FROM invoice_line_items li
            JOIN invoices i ON i.invoice_id = li.invoice_id
            WHERE li.source_session_id = s.id AND i.status IN ('draft', 'finalized')
            LIMIT 1
          ) AS invoice_id
        FROM sessions s
        WHERE s.billing_party_id = ?
        ORDER BY s.start_at DESC
        """,
        (billing_party_id,),
    ).fetchall()
    sessions = [dict(r) for r in session_rows]

    # --- Invoices ---
    invoice_rows = conn.execute(
        """
        SELECT
          i.invoice_id,
          i.invoice_number,
          i.billing_period_start,
          i.billing_period_end,
          i.invoice_date,
          i.status,
          i.total_cents,
          i.finalized_at
        FROM invoices i
        WHERE i.bill_to_party_id = ?
        ORDER BY i.invoice_date DESC, i.created_at DESC
        """,
        (billing_party_id,),
    ).fetchall()
    invoices: list[dict[str, Any]] = []
    for r in invoice_rows:
        item = dict(r)
        item["balance_cents"] = 0 if item["status"] == "void" else item["total_cents"]
        invoices.append(item)

    # --- Billing summary ---
    total_sessions = conn.execute(
        "SELECT COUNT(DISTINCT s.id) FROM sessions s WHERE s.billing_party_id = ?",
        (billing_party_id,),
    ).fetchone()[0]
    approved_uninvoiced_count = conn.execute(
        """
        SELECT COUNT(DISTINCT s.id)
        FROM sessions s
        WHERE s.billing_party_id = ?
          AND s.review_status = 'approved'
          AND s.billable_status NOT IN ('excluded', 'nonbillable')
          AND (s.appointment_status IS NULL OR s.appointment_status NOT IN ('scheduled'))
          AND NOT EXISTS (
            SELECT 1 FROM invoice_line_items li
            JOIN invoices i ON i.invoice_id = li.invoice_id
            WHERE li.source_session_id = s.id AND i.status IN ('draft', 'finalized')
          )
        """,
        (billing_party_id,),
    ).fetchone()[0]
    invoice_count = len(invoices)
    total_invoiced_cents = sum(r["total_cents"] for r in invoices if r["status"] != "void")
    finalized_invoice_total_cents = sum(r["total_cents"] for r in invoices if r["status"] == "finalized")
    billing_summary = {
        "total_sessions": total_sessions,
        "approved_uninvoiced_sessions": approved_uninvoiced_count,
        "invoice_count": invoice_count,
        "total_invoiced_cents": total_invoiced_cents,
        "finalized_invoice_total_cents": finalized_invoice_total_cents,
        "active": bool(bp["active"]),
    }

    # --- Linked account information ---
    account_rows = conn.execute(
        """
        SELECT
          ca.account_id,
          ca.account_code,
          ca.account_name,
          ca.account_type,
          ca.active AS account_active
        FROM client_accounts ca
        WHERE ca.default_billing_party_id = ?
        ORDER BY ca.account_name
        """,
        (billing_party_id,),
    ).fetchall()
    linked_accounts: list[dict[str, Any]] = []
    for ar in account_rows:
        acct = dict(ar)
        members = get_account_members(conn, ar["account_id"])
        acct["members"] = members
        acct["active"] = bool(ar["account_active"])
        linked_accounts.append(acct)

    # --- Audit history ---
    audit = audit_history_for_entity(conn, "billing_party", billing_party_id)

    return {
        "billing_party": {
            "billing_party_id": bp["billing_party_id"],
            "billing_party_type": bp["billing_party_type"],
            "organization_name": bp["organization_name"],
            "billing_name": bp["billing_name"],
            "billing_email": bp["billing_email"],
            "billing_phone": bp["billing_phone"],
            "billing_address_line_1": bp["billing_address_line_1"],
            "billing_address_line_2": bp["billing_address_line_2"],
            "billing_city": bp["billing_city"],
            "billing_state": bp["billing_state"],
            "billing_postal_code": bp["billing_postal_code"],
            "preferred_delivery_method": bp["preferred_delivery_method"],
            "administrative_notes": bp["administrative_notes"],
            "active": bool(bp["active"]),
            "created_at": bp["created_at"],
            "updated_at": bp["updated_at"],
        },
        "covered_clients": covered_clients,
        "sessions": sessions,
        "invoices": invoices,
        "billing_summary": billing_summary,
        "linked_accounts": linked_accounts,
        "audit": audit,
    }


def default_billing_party_for_participants(
    conn: sqlite3.Connection,
    participants: list[dict[str, Any]],
    *,
    account_id: str | None = None,
) -> str | None:
    return effective_billing_party_lookup(conn, None, account_id, participants)[0]


def checklist_for(row: sqlite3.Row, participants: list[dict[str, Any]], readiness: dict[str, Any]) -> list[dict[str, Any]]:
    checks = [
        ("Clients confirmed", readiness["clients_ready"]),
        ("Bill to confirmed", readiness["billing_ready"]),
        ("Session details confirmed", readiness["session_ready"]),
        ("Final approval required", dict(row).get("review_status") == "approved"),
    ]
    return [{"label": label, "resolved": resolved} for label, resolved in checks]


def unresolved_from_values(**values: Any) -> list[str]:
    unresolved = []
    participants = values["participants"]
    if not participants or any(not participant.get("person_id") or participant.get("is_proposed") for participant in participants):
        unresolved.append("participants")
    effective_billing_party_id, _ = effective_billing_party_lookup(
        values["conn"],
        values.get("billing_party_id"),
        values.get("account_id"),
        participants,
    )
    if not effective_billing_party_id:
        unresolved.append("billing_party_id")
    if not values["duration"]:
        unresolved.append("approved_duration_minutes")
    billing_type = values.get("billing_session_type") or map_service_mode_to_billing_type(values.get("service_mode"))
    if billing_type not in ALLOWED_BILLING_SESSION_TYPES:
        unresolved.append("service_mode")
    if billing_type == "custom" and not text(values.get("custom_service_description")).strip():
        unresolved.append("custom_service_description")
    if not values["time_category"]:
        unresolved.append("time_category")
    appointment_status = normalize_attendance_outcome(values.get("appointment_status"))
    billing_treatment = normalize_billing_treatment_for_outcome(
        appointment_status,
        values.get("billing_treatment"),
    )
    if values["approved_rate_cents"] is None and not (
        values.get("suggested_rate_cents") is not None
        and values.get("rate_rule_id")
        and not values.get("rate_needs_review")
    ):
        unresolved.append("approved_rate_cents")
    if appointment_status == "late_cancellation":
        if billing_treatment in {"", None, "unresolved"}:
            unresolved.append("billing_treatment")
        elif billing_treatment == "custom_fee" and values["approved_rate_cents"] is None:
            unresolved.append("approved_rate_cents")
    elif appointment_status in {"cancelled", "no_show", "timely_cancellation"} and billing_treatment in {"", None, "unresolved"}:
        unresolved.append("billing_treatment")
    return unresolved


def status_from_unresolved(unresolved: list[str]) -> str:
    if "participants" in unresolved:
        return "needs_participants"
    if "billing_party_id" in unresolved:
        return "needs_billing_party"
    if "service_mode" in unresolved:
        return "needs_service_mode"
    if "custom_service_description" in unresolved:
        return "needs_service_mode"
    if "approved_rate_cents" in unresolved:
        return "needs_rate"
    if "billing_treatment" in unresolved:
        return "needs_billing_treatment"
    return "needs_review"


def add_review_item(
    conn: sqlite3.Connection,
    candidate_id: str,
    session_id: str,
    review_status: str,
    unresolved_fields: list[str],
    review_reasons: list[str],
) -> None:
    now = now_iso()
    conn.execute(
        """
        INSERT INTO review_items (
          review_item_id, candidate_id, session_id, review_status,
          unresolved_fields, review_reasons, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (new_id(), candidate_id, session_id, review_status, json_dumps(unresolved_fields), json_dumps(review_reasons), now, now),
    )


_AMBIGUOUS_TITLE_TOKENS = (" + ", " & ", " and ", ",", "/", "\\", ";", " for ")


def _is_ambiguous_multi_person_title(value: str) -> bool:
    lowered = f" {text(value).lower()} "
    return any(token in lowered for token in _AMBIGUOUS_TITLE_TOKENS)


def save_alias_after_approval(conn: sqlite3.Connection, session: sqlite3.Row, participants: list[dict[str, Any]]) -> None:
    person_id = next((p.get("person_id") for p in participants if p.get("person_id")), None)
    for raw_alias in {
        text(session["raw_calendar_title"]),
        text(session["proposed_client_name"]),
        strip_calendar_shorthand(text(session["raw_calendar_title"])),
    }:
        if raw_alias:
            upsert_calendar_alias(
                conn,
                raw_alias=raw_alias,
                person_id=person_id,
                account_id=session["account_id"],
                classification="client_session",
                service_mode=session["service_mode"],
                approved=True,
            )


def _save_aliases_for_participant_save(
    conn: sqlite3.Connection,
    session: sqlite3.Row,
    participants: list[dict[str, Any]],
) -> None:
    confirmed = [p for p in participants if p.get("person_id")]
    if len(confirmed) != 1:
        return
    person_id = confirmed[0]["person_id"]
    for raw_alias in {
        text(session["raw_calendar_title"]),
        text(session["proposed_client_name"]),
        strip_calendar_shorthand(text(session["raw_calendar_title"])),
    }:
        if not raw_alias or _is_ambiguous_multi_person_title(raw_alias):
            continue
        normalized = normalize_alias(raw_alias)
        if not normalized:
            continue
        existing = conn.execute(
            """
            SELECT ca.person_id, ca.approved_by_user, p.active
            FROM calendar_aliases ca
            LEFT JOIN people p ON p.person_id = ca.person_id
            WHERE ca.normalized_alias = ?
            """,
            (normalized,),
        ).fetchone()
        if (
            existing
            and existing["person_id"]
            and existing["person_id"] != person_id
            and existing["approved_by_user"]
            and existing["active"]
        ):
            continue
        upsert_calendar_alias(
            conn,
            raw_alias=raw_alias,
            person_id=person_id,
            account_id=session["account_id"],
            classification="client_session",
            service_mode=session["service_mode"],
            approved=True,
        )


def upsert_calendar_alias(
    conn: sqlite3.Connection,
    *,
    raw_alias: str,
    person_id: str | None = None,
    account_id: str | None = None,
    classification: str | None = None,
    service_mode: str | None = None,
    approved: bool = False,
) -> None:
    normalized_alias = normalize_alias(raw_alias)
    if not normalized_alias:
        return
    now = now_iso()
    conn.execute(
        """
        INSERT INTO calendar_aliases (
          alias_id, raw_alias, normalized_alias, account_id, person_id,
          classification, service_mode, confidence, approved_by_user,
          created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(normalized_alias) DO UPDATE SET
          account_id = COALESCE(excluded.account_id, calendar_aliases.account_id),
          person_id = COALESCE(excluded.person_id, calendar_aliases.person_id),
          classification = COALESCE(excluded.classification, calendar_aliases.classification),
          service_mode = COALESCE(excluded.service_mode, calendar_aliases.service_mode),
          confidence = MAX(calendar_aliases.confidence, excluded.confidence),
          approved_by_user = MAX(calendar_aliases.approved_by_user, excluded.approved_by_user),
          updated_at = excluded.updated_at
        """,
        (
            new_id(),
            raw_alias,
            normalized_alias,
            account_id,
            person_id,
            classification,
            service_mode,
            1.0 if approved else 0.75,
            1 if approved else 0,
            now,
            now,
        ),
    )


def audit_history(conn: sqlite3.Connection, session_id: str, candidate_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM audit_log
        WHERE (entity_type = 'session' AND entity_id = ?)
           OR (entity_type = 'calendar_event_candidate' AND entity_id = ?)
        ORDER BY created_at DESC
        LIMIT 30
        """,
        (session_id, candidate_id),
    ).fetchall()
    return [dict(row) for row in rows]


def audit_history_for_entity(conn: sqlite3.Connection, entity_type: str, entity_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM audit_log
        WHERE entity_type = ? AND entity_id = ?
        ORDER BY created_at DESC
        LIMIT 50
        """,
        (entity_type, entity_id),
    ).fetchall()
    return [dict(row) for row in rows]


def record_audit(conn: sqlite3.Connection, entity_type: str, entity_id: str, action: str, details: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO audit_log (id, entity_type, entity_id, action, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (new_id(), entity_type, entity_id, action, json_dumps(details), now_iso()),
    )


def safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    blocked = {"api_key", "password", "secret", "token"}
    return {key: value for key, value in payload.items() if key.lower() not in blocked}


def parse_json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def scrub_session(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def start_time(value: str | None) -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return dt.astimezone(EASTERN_TZ).strftime("%-I:%M %p")


def split_name(name: str) -> tuple[str, str]:
    parts = text(name).split()
    if not parts:
        return "", ""
    return parts[0], parts[-1] if len(parts) > 1 else ""


def generate_person_code(conn: sqlite3.Connection, first_name: str, last_name: str) -> str:
    prefix = person_code_prefix(first_name, last_name)
    for suffix in range(1, 1000):
        code = f"{prefix}-{suffix:03d}"
        exists = conn.execute("SELECT 1 FROM people WHERE person_code = ?", (code,)).fetchone()
        if not exists:
            return code
    raise ValueError(f"No available person code for prefix {prefix}.")


def person_code_prefix(first_name: str, last_name: str) -> str:
    first = normalize_code_text(first_name)
    last = normalize_code_text(last_name)
    if not first or not last:
        raise ValueError("First and last name are required before assigning a person code.")
    usable_last = (last + "XXX")[:3]
    return f"{first[0]}{usable_last}"


def generate_account_code(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        """
        SELECT account_code
        FROM client_accounts
        WHERE account_code LIKE 'ACCT-%'
        ORDER BY account_code DESC
        LIMIT 1
        """
    ).fetchone()
    next_number = 1
    if row and row["account_code"]:
        match = re.search(r"ACCT-(\d+)$", row["account_code"])
        if match:
            next_number = int(match.group(1)) + 1
    while True:
        code = f"ACCT-{next_number:04d}"
        exists = conn.execute("SELECT 1 FROM client_accounts WHERE account_code = ?", (code,)).fetchone()
        if not exists:
            return code
        next_number += 1


def normalize_code_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", text(value))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9]", "", ascii_text).upper()


def normalize_alias(value: str) -> str:
    return " ".join(text(value).lower().split())


def strip_calendar_shorthand(value: str) -> str:
    tokens = text(value).split()
    if len(tokens) <= 1:
        return text(value)
    duration_tokens = {"15", "20", "30", "45", "50", "60", "75", "90", "120"}
    if tokens and tokens[-1] in duration_tokens:
        tokens = tokens[:-1]
    if tokens and (tokens[-1].isdigit() or tokens[-1].upper() in {"AM", "PM"}):
        tokens = tokens[:-1]
    if tokens and tokens[-1].isdigit():
        tokens = tokens[:-1]
    return " ".join(tokens).strip()


def normalize_service_mode(value: str) -> str:
    normalized = text(value).lower().replace(" ", "_")
    return {
        "phone": "phone",
        "call": "phone",
        "facetime": "facetime",
        "face_time": "facetime",
        "office": "office",
        "office_visit": "office",
        "house": "house_call",
        "house_call": "house_call",
        "home_visit": "house_call",
    }.get(normalized, normalized or "unknown")


def normalize_time_category(value: str) -> str:
    normalized = text(value).lower().replace(" + ", "_").replace(" ", "_")
    return {
        "weekend_evening": "weekend",
        "weekend_+_evening": "weekend",
    }.get(normalized, normalized or "standard")


def rate_group_for(service_mode: str) -> str:
    return {"phone": "remote", "facetime": "remote", "office": "office", "house_call": "house_call"}.get(service_mode, "")


def derive_appointment_method_from_service(service_mode: str) -> str:
    if service_mode in {"phone", "facetime", "office"}:
        return service_mode
    if service_mode == "house_call":
        return "office"
    return "unknown"


def derive_duration_choice_from_minutes(duration_minutes: int) -> str:
    if duration_minutes in {30, 60, 90, 120}:
        return str(duration_minutes)
    return "custom"


def money_payload_to_cents(value: Any) -> int | None:
    if value is None or text(value) == "":
        return None
    if isinstance(value, int):
        return value
    return dollars_to_cents(text(value))


def approved_rate_source_for(
    session: sqlite3.Row,
    approved_rate_cents: int | None,
    rate_scope: str,
) -> str | None:
    if approved_rate_cents is None:
        return None
    if rate_scope in {"future_person", "future_joint"}:
        return "person_exception" if rate_scope == "future_person" else "participant_combination_exception"
    suggested = session["suggested_rate_cents"]
    if suggested is not None and int(suggested) == approved_rate_cents:
        return session["rate_source"] or "default"
    return "manual_override"


def maybe_save_rate_scope(
    conn: sqlite3.Connection,
    session: sqlite3.Row,
    payload: dict[str, Any],
    approved_rate_cents: int | None,
) -> None:
    if approved_rate_cents is None:
        return
    scope = payload.get("rate_scope") or "session_only"
    if scope == "session_only":
        record_audit(
            conn,
            "session",
            session["id"],
            "rate_override_session_only",
            {"approved_rate_cents": approved_rate_cents},
        )
        return

    participants = get_session_participants(conn, session["id"])
    person_ids = [p["person_id"] for p in participants if p.get("person_id")]
    duration = int(payload.get("approved_duration_minutes") or payload.get("duration_minutes") or session["duration_minutes"])
    service_mode = normalize_service_mode(payload.get("service_mode") or session["service_mode"])
    billing_session_type = validate_billing_session_type(
        payload.get("billing_session_type")
        or (session["billing_session_type"] if "billing_session_type" in session.keys() else None)
        or "psychotherapy"
    )
    custom_service_description = text(payload.get("custom_service_description") or session["custom_service_description"] or "").strip() or None
    custom_service_code = text(payload.get("custom_service_code") or session["custom_service_code"] or "").strip() or None
    time_category = normalize_time_category(payload.get("time_category") or session["time_category"])
    effective_from = session["session_date"] or text(session["start_at"])[:10]

    if scope == "future_person":
        person_id = payload.get("rate_scope_person_id")
        if not person_id and len(person_ids) == 1:
            person_id = person_ids[0]
        if not person_id:
            raise ValueError("Select which participant should receive this future rate.")
        rule_id = upsert_person_rate_exception(
            conn,
            person_id,
            approved_rate_cents,
            effective_from,
            duration,
            billing_session_type,
            rate_rule_appointment_status_for_session(session["appointment_status"]),
            custom_service_description,
            custom_service_code,
            service_mode,
            time_category,
        )
        source = "person_exception"
    elif scope == "future_joint":
        joint_ids = sorted(set(person_ids))
        if len(joint_ids) < 2:
            raise ValueError("Joint rate exceptions require at least two confirmed participants.")
        rule_id = upsert_joint_rate_exception(
            conn,
            joint_ids,
            approved_rate_cents,
            effective_from,
            duration,
            billing_session_type,
            rate_rule_appointment_status_for_session(session["appointment_status"]),
            custom_service_description,
            custom_service_code,
            service_mode,
            time_category,
        )
        source = "participant_combination_exception"
    else:
        return

    conn.execute(
        """
        UPDATE sessions
        SET approved_rate_rule_id = ?,
            approved_rate_source = ?,
            rate_override_reason = COALESCE(rate_override_reason, ?),
            updated_at = ?
        WHERE id = ?
        """,
        (rule_id, source, payload.get("rate_override_reason") or "Saved future rate memory.", now_iso(), session["id"]),
    )
    record_audit(
        conn,
        "session",
        session["id"],
        "rate_scope_saved",
        {"scope": scope, "rate_rule_id": rule_id, "approved_rate_cents": approved_rate_cents},
    )
    record_audit(
        conn,
        "rate_rule",
        rule_id,
        "rate_memory_saved",
        {"scope": scope, "session_id": session["id"], "amount_cents": approved_rate_cents},
    )


def upsert_person_rate_exception(
    conn: sqlite3.Connection,
    person_id: str,
    amount_cents: int,
    effective_from: str,
    duration_minutes: int,
    billing_session_type: str,
    appointment_status: str,
    custom_service_description: str | None,
    custom_service_code: str | None,
    service_mode: str,
    time_category: str,
) -> str:
    row = conn.execute(
        """
        SELECT rate_rule_id
        FROM rate_rules
        WHERE active = 1
          AND person_id = ?
          AND client_account_id IS NULL
          AND COALESCE(duration_minutes, -1) = ?
          AND COALESCE(billing_session_type, '') = ?
          AND appointment_status = ?
          AND COALESCE(custom_service_description, '') = ?
          AND COALESCE(custom_service_code, '') = ?
          AND time_category = ?
          AND rate_rule_id NOT IN (SELECT rate_rule_id FROM rate_rule_participants)
        ORDER BY effective_from DESC
        LIMIT 1
        """,
        (
            person_id,
            duration_minutes,
            billing_session_type,
            appointment_status,
            text(custom_service_description).strip(),
            text(custom_service_code).strip(),
            time_category,
        ),
    ).fetchone()
    if row:
        conn.execute(
            """
            UPDATE rate_rules
            SET amount_cents = ?, effective_from = ?, custom_service_description = ?, custom_service_code = ?, updated_at = ?
            WHERE rate_rule_id = ?
            """,
            (
                amount_cents,
                effective_from,
                text(custom_service_description).strip() or None,
                text(custom_service_code).strip() or None,
                now_iso(),
                row["rate_rule_id"],
            ),
        )
        return row["rate_rule_id"]
    return seed_rate_rule(
        conn,
        amount_cents=amount_cents,
        effective_from=effective_from,
        duration_minutes=duration_minutes,
        billing_session_type=billing_session_type,
        appointment_status=appointment_status,
        custom_service_description=custom_service_description,
        custom_service_code=custom_service_code,
        time_category=time_category,
        person_id=person_id,
        priority=10,
    )


def upsert_joint_rate_exception(
    conn: sqlite3.Connection,
    person_ids: list[str],
    amount_cents: int,
    effective_from: str,
    duration_minutes: int,
    billing_session_type: str,
    appointment_status: str,
    custom_service_description: str | None,
    custom_service_code: str | None,
    service_mode: str,
    time_category: str,
) -> str:
    placeholders = ",".join("?" for _ in person_ids)
    row = conn.execute(
        f"""
        SELECT rr.rate_rule_id
        FROM rate_rules rr
        JOIN rate_rule_participants rrp ON rrp.rate_rule_id = rr.rate_rule_id
        WHERE rr.active = 1
          AND COALESCE(rr.duration_minutes, -1) = ?
          AND COALESCE(rr.billing_session_type, '') = ?
          AND rr.appointment_status = ?
          AND COALESCE(rr.custom_service_description, '') = ?
          AND COALESCE(rr.custom_service_code, '') = ?
          AND rr.time_category = ?
          AND rrp.person_id IN ({placeholders})
        GROUP BY rr.rate_rule_id
        HAVING COUNT(DISTINCT rrp.person_id) = ?
           AND (
             SELECT COUNT(*) FROM rate_rule_participants exact
             WHERE exact.rate_rule_id = rr.rate_rule_id
           ) = ?
        ORDER BY rr.effective_from DESC
        LIMIT 1
        """,
        (
            duration_minutes,
            billing_session_type,
            appointment_status,
            text(custom_service_description).strip(),
            text(custom_service_code).strip(),
            time_category,
            *person_ids,
            len(person_ids),
            len(person_ids),
        ),
    ).fetchone()
    if row:
        conn.execute(
            """
            UPDATE rate_rules
            SET amount_cents = ?, effective_from = ?, custom_service_description = ?, custom_service_code = ?, updated_at = ?
            WHERE rate_rule_id = ?
            """,
            (
                amount_cents,
                effective_from,
                text(custom_service_description).strip() or None,
                text(custom_service_code).strip() or None,
                now_iso(),
                row["rate_rule_id"],
            ),
        )
        return row["rate_rule_id"]
    return seed_rate_rule(
        conn,
        amount_cents=amount_cents,
        effective_from=effective_from,
        duration_minutes=duration_minutes,
        billing_session_type=billing_session_type,
        appointment_status=appointment_status,
        custom_service_description=custom_service_description,
        custom_service_code=custom_service_code,
        time_category=time_category,
        participant_person_ids=person_ids,
        priority=5,
    )


def list_rate_rules(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    init_db(conn)
    rows = conn.execute(
        """
        SELECT rr.*, ca.account_name, p.display_name
        FROM rate_rules rr
        LEFT JOIN client_accounts ca ON ca.account_id = rr.client_account_id
        LEFT JOIN people p ON p.person_id = rr.person_id
        ORDER BY
          CASE
            WHEN rr.effective_through IS NOT NULL AND rr.effective_through != '' AND rr.effective_through < date('now', 'localtime') THEN 1
            ELSE 0
          END ASC,
          rr.effective_from DESC,
          rr.priority ASC,
          rr.duration_minutes
        """
    ).fetchall()
    return [serialize_rate_rule(conn, row) for row in rows]


def preview_rate_suggestion(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    init_db(conn)
    duration_minutes = parse_rate_rule_duration_minutes(data)
    billing_session_type = validate_billing_session_type(text(data.get("billing_session_type")) or "psychotherapy")
    custom_service_description = normalize_optional_custom_description(
        data.get("custom_service_description")
    )
    custom_service_code = normalize_optional_custom_code(data.get("custom_service_code"))
    if billing_session_type == "custom" and not custom_service_description:
        raise ValueError("Custom session type requires a description.")
    time_category = normalize_time_category(data.get("time_category") or "standard")
    participant_person_ids = normalize_participant_ids(data.get("participant_person_ids") or [])
    person_id = text(data.get("person_id")) or None
    account_id = text(data.get("client_account_id") or data.get("account_id")) or None
    session_date = text(data.get("session_date")) or date.today().isoformat()
    appointment_status = rate_rule_appointment_status_for_session(
        text(data.get("appointment_status")) or "scheduled"
    )
    service_mode = normalize_service_mode(data.get("service_mode") or "office")
    suggestion = suggest_rate(
        conn,
        session_date=session_date,
        duration_minutes=duration_minutes,
        billing_session_type=billing_session_type,
        appointment_status=appointment_status,
        custom_service_description=custom_service_description,
        custom_service_code=custom_service_code,
        service_mode=service_mode,
        rate_group=rate_group_for(service_mode),
        time_category=time_category,
        account_id=account_id,
        person_id=person_id,
        participant_person_ids=participant_person_ids,
    )
    return {
        "amount": cents_to_dollars(suggestion.suggested_rate_cents),
        "amount_cents": suggestion.suggested_rate_cents,
        "rate_rule_id": suggestion.rate_rule_id,
        "rate_source": suggestion.rate_source,
        "rate_needs_review": suggestion.rate_needs_review,
        "explanation": suggestion.explanation,
    }


def create_rate_rule_from_payload(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    init_db(conn)
    payload = normalized_rate_rule_payload(data)

    duplicate = _find_duplicate_active_rate_rule(
        conn,
        client_account_id=payload["client_account_id"],
        person_id=payload["person_id"],
        participant_person_ids=payload["participant_person_ids"],
        duration_minutes=payload["duration_minutes"],
        billing_session_type=payload["billing_session_type"],
        appointment_status=payload["appointment_status"],
        custom_service_description=payload["custom_service_description"],
        custom_service_code=payload["custom_service_code"],
        service_mode=payload["service_mode"],
        rate_group=payload["rate_group"],
        time_category=payload["time_category"],
        effective_from=payload["effective_from"],
    )
    if duplicate:
        raise ValueError("An active rate rule with the same scope and dimensions already exists.")

    rule_id = seed_rate_rule(
        conn,
        amount_cents=payload["amount_cents"],
        effective_from=payload["effective_from"],
        duration_minutes=payload["duration_minutes"],
        billing_session_type=payload["billing_session_type"],
        appointment_status=payload["appointment_status"],
        custom_service_description=payload["custom_service_description"],
        custom_service_code=payload["custom_service_code"],
        service_mode=payload["service_mode"],
        rate_group=payload["rate_group"],
        time_category=payload["time_category"],
        client_account_id=payload["client_account_id"],
        person_id=payload["person_id"],
        participant_person_ids=payload["participant_person_ids"],
        priority=payload["priority"],
    )
    record_audit(conn, "rate_rule", rule_id, "created_inline", payload)
    _recalc_unapproved_session_rates(conn)
    write_reports(conn)
    conn.commit()
    return serialize_rate_rule(conn, get_rate_rule_row(conn, rule_id))


def replace_rate_rule_from_payload(conn: sqlite3.Connection, rule_id: str, data: dict[str, Any]) -> dict[str, Any]:
    init_db(conn)
    existing = get_rate_rule_row(conn, rule_id)
    payload = normalized_rate_rule_payload(data, scope_override=rate_rule_scope_ids(conn, existing))
    if payload["effective_from"] <= text(existing["effective_from"]):
        raise ValueError("Replacement effective date must be after the existing rule start date.")
    duplicate = _find_duplicate_active_rate_rule(
        conn,
        client_account_id=payload["client_account_id"],
        person_id=payload["person_id"],
        participant_person_ids=payload["participant_person_ids"],
        duration_minutes=payload["duration_minutes"],
        billing_session_type=payload["billing_session_type"],
        appointment_status=payload["appointment_status"],
        custom_service_description=payload["custom_service_description"],
        custom_service_code=payload["custom_service_code"],
        service_mode=payload["service_mode"],
        rate_group=payload["rate_group"],
        time_category=payload["time_category"],
        effective_from=payload["effective_from"],
        exclude_rate_rule_id=rule_id,
    )
    if duplicate:
        raise ValueError("An active rate rule with the same scope and dimensions already exists.")
    replacement_start = datetime.strptime(payload["effective_from"], "%Y-%m-%d").date()
    prior_end = (replacement_start - timedelta(days=1)).isoformat()
    conn.execute(
        "UPDATE rate_rules SET effective_through = ?, updated_at = ? WHERE rate_rule_id = ?",
        (prior_end, now_iso(), rule_id),
    )
    new_rule_id = seed_rate_rule(
        conn,
        amount_cents=payload["amount_cents"],
        effective_from=payload["effective_from"],
        duration_minutes=payload["duration_minutes"],
        billing_session_type=payload["billing_session_type"],
        appointment_status=payload["appointment_status"],
        custom_service_description=payload["custom_service_description"],
        custom_service_code=payload["custom_service_code"],
        service_mode=payload["service_mode"],
        rate_group=payload["rate_group"],
        time_category=payload["time_category"],
        client_account_id=payload["client_account_id"],
        person_id=payload["person_id"],
        participant_person_ids=payload["participant_person_ids"],
        priority=payload["priority"],
    )
    record_audit(
        conn,
        "rate_rule",
        rule_id,
        "replaced",
        {"replacement_rate_rule_id": new_rule_id, "effective_through": prior_end},
    )
    record_audit(
        conn,
        "rate_rule",
        new_rule_id,
        "replacement_created",
        {"replaced_rate_rule_id": rule_id, "payload": payload},
    )
    _recalc_unapproved_session_rates(conn)
    write_reports(conn)
    conn.commit()
    return serialize_rate_rule(conn, get_rate_rule_row(conn, new_rule_id))


def end_rate_rule(conn: sqlite3.Connection, rule_id: str, effective_through: str) -> dict[str, Any]:
    init_db(conn)
    existing = get_rate_rule_row(conn, rule_id)
    validate_effective_date(effective_through)
    if effective_through < text(existing["effective_from"]):
        raise ValueError("End date cannot be before the rule start date.")
    conn.execute(
        "UPDATE rate_rules SET effective_through = ?, updated_at = ? WHERE rate_rule_id = ?",
        (effective_through, now_iso(), rule_id),
    )
    record_audit(
        conn,
        "rate_rule",
        rule_id,
        "ended",
        {"effective_through": effective_through},
    )
    _recalc_unapproved_session_rates(conn)
    write_reports(conn)
    conn.commit()
    return serialize_rate_rule(conn, get_rate_rule_row(conn, rule_id))


def serialize_rate_rule(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    participant_rows = conn.execute(
        """
        SELECT p.person_id, p.display_name
        FROM rate_rule_participants rrp
        JOIN people p ON p.person_id = rrp.person_id
        WHERE rrp.rate_rule_id = ?
        ORDER BY p.display_name
        """,
        (row["rate_rule_id"],),
    ).fetchall()
    participant_names = [text(p["display_name"]) for p in participant_rows if text(p["display_name"])]
    participant_ids = [p["person_id"] for p in participant_rows if p["person_id"]]
    item["participant_names"] = " + ".join(participant_names)
    item["participant_person_ids"] = participant_ids
    item["amount"] = cents_to_dollars(row["amount_cents"])
    item["scope_type"] = rate_rule_scope_type(item, participant_ids)
    item["scope_label"] = rate_rule_scope_label(item)
    item["appointment_status_label"] = appointment_status_label(item.get("appointment_status"))
    item["duration_label"] = (
        f"{item['duration_minutes']} minutes" if item.get("duration_minutes") else "Unknown duration"
    )
    item["session_type_label"] = rate_rule_session_type_label(item)
    item["ended"] = bool(
        text(item.get("effective_through"))
        and text(item["effective_through"]) < date.today().isoformat()
    )
    return item


def rate_rule_scope_type(item: dict[str, Any], participant_ids: list[str]) -> str:
    if participant_ids:
        return "participants"
    if item.get("person_id"):
        return "person"
    if item.get("client_account_id"):
        return "account"
    return "everyone"


def rate_rule_scope_label(item: dict[str, Any]) -> str:
    scope_type = rate_rule_scope_type(item, item.get("participant_person_ids") or [])
    if scope_type == "participants":
        return item.get("participant_names") or "Clients Together"
    if scope_type == "person":
        return text(item.get("display_name")) or "One Client"
    if scope_type == "account":
        return text(item.get("account_name")) or "Billing Relationship"
    return "Everyone"


def rate_rule_session_type_label(item: dict[str, Any]) -> str:
    return get_user_facing_session_label(
        text(item.get("billing_session_type")) or None,
        text(item.get("appointment_status")) or None,
        text(item.get("custom_service_description")) or None,
    )


def normalized_rate_rule_payload(
    data: dict[str, Any],
    *,
    scope_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    amount_cents = money_payload_to_cents(data.get("amount"))
    if amount_cents is None or amount_cents <= 0:
        raise ValueError("Amount is required and must be greater than 0.")
    duration_minutes = parse_rate_rule_duration_minutes(data)
    billing_session_type = validate_billing_session_type(text(data.get("billing_session_type")) or "")
    custom_service_description = normalize_optional_custom_description(data.get("custom_service_description"))
    custom_service_code = normalize_optional_custom_code(data.get("custom_service_code"))
    if billing_session_type == "custom" and not custom_service_description:
        raise ValueError("Custom session type requires a description.")
    time_category = normalize_time_category(data.get("time_category") or "")
    effective_from = text(data.get("effective_from")) or date.today().isoformat()
    validate_effective_date(effective_from)
    scope = infer_rate_scope(data, scope_override=scope_override)
    client_account_id = text(scope.get("client_account_id")) or None
    person_id = text(scope.get("person_id")) or None
    participant_person_ids = normalize_participant_ids(scope.get("participant_person_ids") or [])
    appointment_status = validate_rate_rule_appointment_status(
        text(data.get("appointment_status")) or "scheduled"
    )
    return {
        "amount_cents": amount_cents,
        "duration_minutes": duration_minutes,
        "billing_session_type": billing_session_type,
        "appointment_status": appointment_status,
        "custom_service_description": custom_service_description,
        "custom_service_code": custom_service_code,
        "service_mode": None,
        "rate_group": None,
        "time_category": time_category,
        "effective_from": effective_from,
        "client_account_id": client_account_id,
        "person_id": person_id,
        "participant_person_ids": participant_person_ids,
        "priority": int(data.get("priority") or 100),
        "applies_to": scope["applies_to"],
    }


def parse_rate_rule_duration_minutes(data: dict[str, Any]) -> int:
    duration_choice = text(data.get("duration_choice"))
    if duration_choice == "custom":
        minutes = parse_positive_int(data.get("custom_duration_minutes"))
        if minutes is None:
            raise ValueError("Custom duration requires actual minutes.")
        return minutes
    if duration_choice:
        minutes = parse_positive_int(duration_choice)
        if minutes is None:
            raise ValueError("Duration is required.")
        return minutes
    minutes = parse_positive_int(data.get("duration_minutes"))
    if minutes is None:
        raise ValueError("Duration is required.")
    return minutes


def parse_positive_int(value: object) -> int | None:
    parsed = parse_int(text(value)) if text(value) else None
    if parsed is None or parsed <= 0:
        return None
    return parsed


def normalize_optional_custom_description(value: object) -> str | None:
    normalized = text(value).strip()
    return normalized or None


def normalize_optional_custom_code(value: object) -> str | None:
    normalized = text(value).strip()
    return normalized or None


def infer_rate_scope(
    data: dict[str, Any],
    *,
    scope_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if scope_override:
        return scope_override
    applies_to = text(data.get("applies_to"))
    client_account_id = text(data.get("client_account_id") or data.get("account_id")) or None
    person_id = text(data.get("person_id")) or None
    participant_person_ids = normalize_participant_ids(data.get("participant_person_ids") or [])
    if not applies_to:
        if participant_person_ids:
            applies_to = "participants"
        elif person_id:
            applies_to = "person"
        elif client_account_id:
            applies_to = "account"
        else:
            applies_to = "everyone"
    if applies_to == "everyone":
        return {"applies_to": applies_to, "client_account_id": None, "person_id": None, "participant_person_ids": []}
    if applies_to == "person":
        if not person_id:
            raise ValueError("Select one resolved client for a One Client rule.")
        return {"applies_to": applies_to, "client_account_id": None, "person_id": person_id, "participant_person_ids": []}
    if applies_to == "participants":
        if len(participant_person_ids) < 2:
            raise ValueError("Select at least two resolved clients for a Clients Together rule.")
        return {"applies_to": applies_to, "client_account_id": None, "person_id": None, "participant_person_ids": participant_person_ids}
    if applies_to in {"account", "billing_relationship"}:
        if not client_account_id:
            raise ValueError("Select one resolved billing relationship for this rule.")
        return {"applies_to": "account", "client_account_id": client_account_id, "person_id": None, "participant_person_ids": []}
    raise ValueError("Choose a valid rate scope.")


def normalize_participant_ids(values: list[Any]) -> list[str]:
    return sorted({text(value) for value in values if text(value)})


def validate_effective_date(value: str) -> None:
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        raise ValueError("Effective date must be in YYYY-MM-DD format.")


def get_rate_rule_row(conn: sqlite3.Connection, rule_id: str) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT rr.*, ca.account_name, p.display_name
        FROM rate_rules rr
        LEFT JOIN client_accounts ca ON ca.account_id = rr.client_account_id
        LEFT JOIN people p ON p.person_id = rr.person_id
        WHERE rr.rate_rule_id = ?
        """,
        (rule_id,),
    ).fetchone()
    if not row:
        raise ValueError("Rate rule not found.")
    return row


def rate_rule_scope_ids(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    participant_ids = [
        participant["person_id"]
        for participant in conn.execute(
            "SELECT person_id FROM rate_rule_participants WHERE rate_rule_id = ? ORDER BY person_id",
            (row["rate_rule_id"],),
        ).fetchall()
    ]
    if participant_ids:
        return {"applies_to": "participants", "participant_person_ids": participant_ids}
    if row["person_id"]:
        return {"applies_to": "person", "person_id": row["person_id"]}
    if row["client_account_id"]:
        return {"applies_to": "account", "client_account_id": row["client_account_id"]}
    return {"applies_to": "everyone"}


def save_person_alias(
    conn: sqlite3.Connection,
    person_id: str,
    *,
    raw_alias: str,
    approved_by_user: bool = True,
    alias_id: str | None = None,
) -> dict[str, Any]:
    init_db(conn)
    person = conn.execute(
        "SELECT person_id FROM people WHERE person_id = ? AND active = 1",
        (person_id,),
    ).fetchone()
    if not person:
        raise ValueError("Person not found.")
    normalized_alias = normalize_alias(raw_alias)
    if not normalized_alias:
        raise ValueError("Alias is required.")
    now = now_iso()
    existing = conn.execute(
        """
        SELECT ca.alias_id, ca.person_id, ca.approved_by_user, p.active
        FROM calendar_aliases ca
        LEFT JOIN people p ON p.person_id = ca.person_id
        WHERE ca.normalized_alias = ?
        """,
        (normalized_alias,),
    ).fetchone()
    if alias_id:
        owned = conn.execute(
            "SELECT * FROM calendar_aliases WHERE alias_id = ? AND person_id = ?",
            (alias_id, person_id),
        ).fetchone()
        if not owned:
            raise ValueError("Alias not found for this person.")
        conn.execute(
            """
            UPDATE calendar_aliases
            SET approved_by_user = ?, updated_at = ?
            WHERE alias_id = ?
            """,
            (1 if approved_by_user else 0, now, alias_id),
        )
        record_audit(
            conn,
            "person",
            person_id,
            "alias_updated",
            {"alias_id": alias_id, "raw_alias": owned["raw_alias"], "approved_by_user": 1 if approved_by_user else 0},
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM calendar_aliases WHERE alias_id = ?", (alias_id,)).fetchone())
    if existing and existing["person_id"] != person_id and existing["approved_by_user"] and existing["active"]:
        raise ValueError("Alias is already approved for another active client.")
    upsert_calendar_alias(
        conn,
        raw_alias=raw_alias,
        person_id=person_id,
        classification="client_session",
        approved=approved_by_user,
    )
    row = conn.execute(
        "SELECT * FROM calendar_aliases WHERE normalized_alias = ?",
        (normalized_alias,),
    ).fetchone()
    record_audit(
        conn,
        "person",
        person_id,
        "alias_saved",
        {"alias_id": row["alias_id"], "raw_alias": row["raw_alias"], "approved_by_user": row["approved_by_user"]},
    )
    conn.commit()
    return dict(row)


def _find_duplicate_active_rate_rule(
    conn: sqlite3.Connection,
    *,
    client_account_id: str | None,
    person_id: str | None,
    participant_person_ids: list[str] | None,
    duration_minutes: int | None,
    billing_session_type: str | None,
    appointment_status: str,
    custom_service_description: str | None,
    custom_service_code: str | None,
    service_mode: str | None,
    rate_group: str | None,
    time_category: str,
    effective_from: str,
    exclude_rate_rule_id: str | None = None,
) -> str | None:
    if participant_person_ids:
        ids = sorted(set(p for p in participant_person_ids if p))
        placeholders = ",".join("?" for _ in ids)
        row = conn.execute(
            f"""
            SELECT rr.rate_rule_id
            FROM rate_rules rr
            JOIN rate_rule_participants rrp ON rrp.rate_rule_id = rr.rate_rule_id
            WHERE rr.active = 1
              AND rr.client_account_id IS NULL
              AND rr.person_id IS NULL
              AND COALESCE(rr.duration_minutes, -1) = ?
              AND COALESCE(rr.billing_session_type, '') = ?
              AND rr.appointment_status = ?
              AND COALESCE(rr.custom_service_description, '') = ?
              AND COALESCE(rr.custom_service_code, '') = ?
              AND COALESCE(rr.service_mode, '') = ?
              AND COALESCE(rr.rate_group, '') = ?
              AND rr.time_category = ?
              AND rr.effective_from = ?
              AND (? IS NULL OR rr.rate_rule_id != ?)
              AND rrp.person_id IN ({placeholders})
            GROUP BY rr.rate_rule_id
            HAVING COUNT(DISTINCT rrp.person_id) = ?
               AND (
                 SELECT COUNT(*) FROM rate_rule_participants exact
                 WHERE exact.rate_rule_id = rr.rate_rule_id
               ) = ?
            LIMIT 1
            """,
            (
                duration_minutes if duration_minutes is not None else -1,
                billing_session_type or "",
                appointment_status,
                custom_service_description or "",
                custom_service_code or "",
                service_mode or "",
                rate_group or "",
                time_category,
                effective_from,
                exclude_rate_rule_id,
                exclude_rate_rule_id,
                *ids,
                len(ids),
                len(ids),
            ),
        ).fetchone()
        return row["rate_rule_id"] if row else None

    row = conn.execute(
        """
        SELECT rate_rule_id
        FROM rate_rules
        WHERE active = 1
          AND COALESCE(client_account_id, '') = ?
          AND COALESCE(person_id, '') = ?
          AND COALESCE(duration_minutes, -1) = ?
          AND COALESCE(billing_session_type, '') = ?
          AND appointment_status = ?
          AND COALESCE(custom_service_description, '') = ?
          AND COALESCE(custom_service_code, '') = ?
          AND COALESCE(service_mode, '') = ?
          AND COALESCE(rate_group, '') = ?
          AND time_category = ?
          AND effective_from = ?
          AND (? IS NULL OR rate_rule_id != ?)
          AND rate_rule_id NOT IN (SELECT rate_rule_id FROM rate_rule_participants)
        LIMIT 1
        """,
        (
            client_account_id or "",
            person_id or "",
            duration_minutes if duration_minutes is not None else -1,
            billing_session_type or "",
            appointment_status,
            custom_service_description or "",
            custom_service_code or "",
            service_mode or "",
            rate_group or "",
            time_category,
            effective_from,
            exclude_rate_rule_id,
            exclude_rate_rule_id,
        ),
    ).fetchone()
    return row["rate_rule_id"] if row else None


def _recalc_unapproved_session_rates(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT id, candidate_id
        FROM sessions
        WHERE review_status NOT IN ('approved', 'excluded')
        """
    ).fetchall()
    for row in rows:
        refresh_candidate_suggestions(conn, row["candidate_id"], preserve_approved_rate=True)
    return len(rows)


def recalc_unapproved_session_rates(conn: sqlite3.Connection) -> int:
    init_db(conn)
    count = _recalc_unapproved_session_rates(conn)
    conn.commit()
    return count


def reparse_unapproved_candidates(
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    """
    Reparse every unapproved, non-excluded candidate using the current parser.
    Preserves raw calendar evidence and audit history.
    Creates sessions for newly-classified client_session candidates
    (e.g., historical 'Sarah 5 cancelled' → client_session / cancelled / unresolved billing).
    Skips any candidate or session already approved or explicitly excluded.
    """
    init_db(conn)
    now = now_iso()
    rows = conn.execute(
        """
        SELECT
          c.id, c.latest_raw_snapshot_id, c.calendar_name, c.review_status AS cand_review_status,
          s.id AS session_id, s.review_status AS sess_review_status
        FROM calendar_event_candidates c
        LEFT JOIN sessions s ON s.candidate_id = c.id
        WHERE COALESCE(s.review_status, c.review_status) NOT IN ('approved', 'excluded')
        """
    ).fetchall()

    reparsed = 0
    sessions_created = 0
    skipped = 0

    for row in rows:
        snap = conn.execute(
            "SELECT * FROM raw_calendar_snapshots WHERE id = ?",
            (row["latest_raw_snapshot_id"],),
        ).fetchone()
        if not snap:
            skipped += 1
            continue

        parse_row = {
            "event_title": snap["event_title"],
            "start_at": snap["start_at"],
            "end_at": snap["end_at"],
            "duration_minutes": snap["duration_minutes"],
            "location": snap["location"],
        }
        result = parse_event(parse_row)
        disposition = classify_calendar(conn, row["calendar_name"])
        result = apply_calendar_signal(result, disposition)
        new_review_status = review_status_for_parse(result)

        conn.execute(
            """
            UPDATE calendar_event_candidates
            SET classification           = ?,
                confidence               = ?,
                confidence_label         = ?,
                explanation              = ?,
                fields_requiring_review  = ?,
                unresolved_fields        = ?,
                review_reasons           = ?,
                parser_payload           = ?,
                proposed_client_name     = ?,
                candidate_person_names   = ?,
                possible_referenced_person = ?,
                proposed_start_at        = ?,
                proposed_duration_minutes = ?,
                proposed_end_at          = ?,
                time_shorthand           = ?,
                duration_source          = ?,
                service_mode             = ?,
                rate_group               = ?,
                time_category            = ?,
                is_evening               = ?,
                is_weekend               = ?,
                appointment_status       = ?,
                billing_treatment        = ?,
                title_time_text          = ?,
                title_time_normalized    = ?,
                title_time_matches_calendar = ?,
                billing_session_type     = ?,
                appointment_method       = ?,
                duration_choice          = ?,
                house_call_suggested     = ?,
                billing_type_source      = ?,
                location_text            = ?,
                review_status            = ?,
                updated_at               = ?
            WHERE id = ?
              AND review_status NOT IN ('approved', 'excluded')
            """,
            (
                result.classification,
                result.confidence,
                result.confidence_label,
                result.explanation,
                json_dumps(result.fields_requiring_review),
                json_dumps(result.unresolved_fields),
                json_dumps(result.review_reasons),
                json_dumps(result.as_dict()),
                result.proposed_client_name,
                json_dumps(result.candidate_person_names),
                result.possible_referenced_person,
                result.proposed_start_at,
                result.proposed_duration_minutes,
                result.proposed_end_at,
                result.time_shorthand,
                result.duration_source,
                result.service_mode,
                result.rate_group,
                result.time_category,
                1 if result.is_evening else 0,
                1 if result.is_weekend else 0,
                result.appointment_status,
                initial_billing_treatment(result),
                result.title_time_text,
                result.title_time_normalized,
                (1 if result.title_time_matches_calendar else (0 if result.title_time_matches_calendar is False else None)),
                result.billing_session_type,
                result.appointment_method,
                result.duration_choice,
                1 if result.house_call_suggested else 0,
                result.billing_type_source,
                result.location_text,
                new_review_status,
                now,
                row["id"],
            ),
        )
        record_audit(
            conn,
            "calendar_event_candidate",
            row["id"],
            "reparsed",
            {"classification": result.classification, "appointment_status": result.appointment_status},
        )
        reparsed += 1

        if result.classification == "client_session" and not row["session_id"]:
            created = maybe_insert_session(conn, row["id"], snap, result)
            if created:
                sessions_created += 1

    conn.commit()
    return {"reparsed": reparsed, "sessions_created": sessions_created, "skipped": skipped}


def analyze_billing_relationship_duplicates(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return a read-only administrative summary of duplicate billing relationships."""
    init_db(conn)

    grouped_relationships: dict[tuple[str, str], dict[str, Any]] = {}
    active_accounts = conn.execute(
        """
        SELECT
          ca.account_id,
          ca.default_billing_party_id,
          bp.billing_party_type,
          bp.person_id AS payer_person_id
        FROM client_accounts ca
        LEFT JOIN billing_parties bp ON bp.billing_party_id = ca.default_billing_party_id
        WHERE ca.active = 1
          AND ca.default_billing_party_id IS NOT NULL
        ORDER BY ca.account_id
        """
    ).fetchall()
    for account in active_accounts:
        payer_kind = "organization" if account["billing_party_type"] == "organization" else "person"
        payer_identity_key = _payer_identity_key(
            payer_kind,
            account["payer_person_id"],
            account["default_billing_party_id"] if payer_kind == "organization" else None,
        )
        covered_client_ids = _normalized_covered_client_ids(
            [
                member["person_id"]
                for member in conn.execute(
                    "SELECT person_id FROM account_members WHERE account_id = ?",
                    (account["account_id"],),
                ).fetchall()
                if member["person_id"]
            ]
        )
        if not covered_client_ids:
            continue
        group_key = (payer_identity_key, ",".join(covered_client_ids))
        group = grouped_relationships.setdefault(
            group_key,
            {
                "payer_identity_key": payer_identity_key,
                "payer_kind": payer_kind,
                "payer_person_id": account["payer_person_id"],
                "covered_client_ids": covered_client_ids,
                "account_ids": [],
                "billing_party_ids": [],
            },
        )
        group["account_ids"].append(account["account_id"])
        group["billing_party_ids"].append(account["default_billing_party_id"])

    exact_duplicates: list[dict[str, Any]] = []
    for group in grouped_relationships.values():
        if len(group["account_ids"]) <= 1:
            continue
        is_self_pay = bool(
            group["payer_kind"] != "organization"
            and group["payer_person_id"]
            and len(group["covered_client_ids"]) == 1
            and group["covered_client_ids"][0] == group["payer_person_id"]
        )
        exact_duplicates.append(
            {
                **group,
                "duplicate_count": len(group["account_ids"]),
                "is_self_pay": is_self_pay,
            }
        )
    exact_duplicates.sort(
        key=lambda item: (
            -item["duplicate_count"],
            item["payer_identity_key"],
            ",".join(item["covered_client_ids"]),
        )
    )

    payer_record_conflicts: list[dict[str, Any]] = []
    for row in conn.execute(
        """
        SELECT
          bp.person_id AS payer_person_id,
          COUNT(DISTINCT ca.default_billing_party_id) AS active_billing_party_count,
          GROUP_CONCAT(DISTINCT ca.default_billing_party_id) AS billing_party_ids,
          GROUP_CONCAT(DISTINCT ca.account_id) AS account_ids
        FROM client_accounts ca
        JOIN billing_parties bp ON bp.billing_party_id = ca.default_billing_party_id
        WHERE ca.active = 1
          AND bp.active = 1
          AND bp.person_id IS NOT NULL
        GROUP BY bp.person_id
        HAVING COUNT(DISTINCT ca.default_billing_party_id) > 1
        ORDER BY active_billing_party_count DESC, bp.person_id
        """
    ).fetchall():
        payer_record_conflicts.append(
            {
                "payer_person_id": row["payer_person_id"],
                "active_billing_party_count": int(row["active_billing_party_count"]),
                "billing_party_ids": [bid for bid in (row["billing_party_ids"] or "").split(",") if bid],
                "account_ids": [aid for aid in (row["account_ids"] or "").split(",") if aid],
            }
        )

    duplicate_account_ids = {
        account_id
        for item in exact_duplicates
        for account_id in item["account_ids"]
    }
    payer_conflict_account_ids = {
        account_id
        for item in payer_record_conflicts
        for account_id in item["account_ids"]
    }

    return {
        "summary": {
            "exact_active_duplicate_group_count": len(exact_duplicates),
            "exact_active_duplicate_relationship_count": len(duplicate_account_ids),
            "duplicate_self_pay_group_count": sum(1 for item in exact_duplicates if item["is_self_pay"]),
            "payer_record_conflict_count": len(payer_record_conflicts),
        },
        "exact_active_duplicates": exact_duplicates,
        "duplicate_self_pay_relationships": [
            item for item in exact_duplicates if item["is_self_pay"]
        ],
        "payer_record_conflicts": payer_record_conflicts,
        "duplicate_account_ids": sorted(duplicate_account_ids),
        "payer_conflict_account_ids": sorted(payer_conflict_account_ids),
        "recommended_resolution": (
            "Existing duplicates should be resolved through an explicit audited "
            "deactivation or merge workflow. This analyzer is read-only and does not modify records."
        ),
    }


def normalize_duplicate_payer_billing_parties(
    conn: sqlite3.Connection,
    person_id: str,
    *,
    canonical_billing_party_id: str | None = None,
) -> dict[str, Any]:
    """Audited normalization of duplicate active person-linked billing parties.

    Selects or establishes one canonical active billing-party record for the
    given payer person, copies missing contact/delivery fields from redundant
    records (never overwriting non-empty canonical fields), deactivates
    redundant records, repoints safe mutable references (account defaults,
    draft-only invoice/session references), and leaves finalized invoices,
    snapshots, PDF paths, and payment ownership unchanged.

    Returns a structured summary of the merge operation.
    """
    init_db(conn)

    person = conn.execute(
        "SELECT * FROM people WHERE person_id = ? AND active = 1", (person_id,)
    ).fetchone()
    if not person:
        raise ValueError("Person does not exist or is not active.")

    active_parties = conn.execute(
        """
        SELECT * FROM billing_parties
        WHERE person_id = ? AND active = 1 AND billing_party_type = 'person'
        ORDER BY updated_at DESC
        """,
        (person_id,),
    ).fetchall()

    if len(active_parties) <= 1:
        return {
            "person_id": person_id,
            "canonical_billing_party_id": active_parties[0]["billing_party_id"] if active_parties else None,
            "deactivated_count": 0,
            "fields_copied": [],
            "conflicts": [],
            "repointed_accounts": [],
            "repointed_drafts": [],
            "repointed_sessions": [],
            "skipped": "No duplicate active billing parties found.",
        }

    # --- Select canonical record ---
    if canonical_billing_party_id:
        canonical = next(
            (p for p in active_parties if p["billing_party_id"] == canonical_billing_party_id),
            None,
        )
        if not canonical:
            raise ValueError("Specified canonical billing party is not an active record for this person.")
    else:
        # Prefer the one referenced by the most active accounts
        account_counts = {}
        for p in active_parties:
            count = conn.execute(
                "SELECT COUNT(*) FROM client_accounts WHERE default_billing_party_id = ? AND active = 1",
                (p["billing_party_id"],),
            ).fetchone()[0]
            account_counts[p["billing_party_id"]] = count
        canonical = max(active_parties, key=lambda p: (
            account_counts.get(p["billing_party_id"], 0),
            p["updated_at"],
        ))

    canonical_id = canonical["billing_party_id"]
    redundant = [p for p in active_parties if p["billing_party_id"] != canonical_id]

    # --- Copy missing fields from redundant records (field-level safe rules) ---
    contact_fields = [
        "billing_email", "billing_phone", "billing_address_line_1",
        "billing_address_line_2", "billing_city", "billing_state",
        "billing_postal_code", "preferred_delivery_method", "administrative_notes",
    ]
    fields_copied: list[str] = []
    conflicts: list[dict[str, str]] = []

    canonical_updates: dict[str, Any] = {}
    for field in contact_fields:
        canonical_val = str(canonical[field] or "").strip() if canonical[field] is not None else ""
        if not canonical_val:
            for r in redundant:
                redundant_val = str(r[field] or "").strip() if r[field] is not None else ""
                if redundant_val:
                    if field not in canonical_updates:
                        canonical_updates[field] = redundant_val
                        fields_copied.append(field)
                    elif canonical_updates[field] != redundant_val:
                        conflicts.append({
                            "field": field,
                            "canonical_value": "",
                            "conflicting_values": f"{canonical_updates[field]} vs {redundant_val}",
                        })
                    break  # only check first redundant with a value for this field

    # Detect conflicts: canonical has a value and a redundant has a different value
    for field in contact_fields:
        canonical_val = str(canonical[field] or "").strip() if canonical[field] is not None else ""
        if canonical_val:
            for r in redundant:
                redundant_val = str(r[field] or "").strip() if r[field] is not None else ""
                if redundant_val and redundant_val != canonical_val:
                    conflicts.append({
                        "field": field,
                        "canonical_value": canonical_val,
                        "conflicting_values": redundant_val,
                    })

    # Apply canonical updates
    if canonical_updates:
        now = now_iso()
        set_clauses = ", ".join(f"{k} = ?" for k in canonical_updates)
        params = list(canonical_updates.values()) + [now, canonical_id]
        conn.execute(
            f"UPDATE billing_parties SET {set_clauses}, updated_at = ? WHERE billing_party_id = ?",
            params,
        )

    # --- Begin transaction for structural changes ---
    _begin_immediate(conn)
    try:
        now = now_iso()

        # Repoint active account defaults
        repointed_accounts: list[str] = []
        for r in redundant:
            r_id = r["billing_party_id"]
            accounts = conn.execute(
                "SELECT account_id FROM client_accounts WHERE default_billing_party_id = ? AND active = 1",
                (r_id,),
            ).fetchall()
            for acct in accounts:
                conn.execute(
                    "UPDATE client_accounts SET default_billing_party_id = ?, updated_at = ? WHERE account_id = ?",
                    (canonical_id, now, acct["account_id"]),
                )
                repointed_accounts.append(acct["account_id"])

        # Repoint draft-only invoice references
        repointed_drafts: list[str] = []
        for r in redundant:
            r_id = r["billing_party_id"]
            draft_invoices = conn.execute(
                "SELECT invoice_id FROM invoices WHERE bill_to_party_id = ? AND status = 'draft'",
                (r_id,),
            ).fetchall()
            for inv in draft_invoices:
                # Check if canonical already has a draft for the same billing month
                existing_month = conn.execute(
                    "SELECT billing_month FROM invoices WHERE invoice_id = ?",
                    (inv["invoice_id"],),
                ).fetchone()
                bm = existing_month["billing_month"] if existing_month else None
                if bm:
                    target = conn.execute(
                        "SELECT invoice_id FROM invoices WHERE bill_to_party_id = ? AND billing_month = ? AND status = 'draft' AND invoice_id != ?",
                        (canonical_id, bm, inv["invoice_id"]),
                    ).fetchone()
                    if target:
                        # Move lines to the target draft instead of repointing
                        conn.execute(
                            "UPDATE invoice_line_items SET invoice_id = ? WHERE invoice_id = ?",
                            (target["invoice_id"], inv["invoice_id"]),
                        )
                        conn.execute(
                            "DELETE FROM invoices WHERE invoice_id = ?",
                            (inv["invoice_id"],),
                        )
                        repointed_drafts.append(inv["invoice_id"])
                        continue
                conn.execute(
                    "UPDATE invoices SET bill_to_party_id = ?, updated_at = ? WHERE invoice_id = ?",
                    (canonical_id, now, inv["invoice_id"]),
                )
                repointed_drafts.append(inv["invoice_id"])

        # Repoint non-approved session references
        repointed_sessions: list[str] = []
        for r in redundant:
            r_id = r["billing_party_id"]
            sessions = conn.execute(
                "SELECT id FROM sessions WHERE billing_party_id = ? AND review_status != 'approved'",
                (r_id,),
            ).fetchall()
            for sess in sessions:
                conn.execute(
                    "UPDATE sessions SET billing_party_id = ?, updated_at = ? WHERE id = ?",
                    (canonical_id, now, sess["id"]),
                )
                repointed_sessions.append(sess["id"])

        # Deactivate redundant records
        for r in redundant:
            r_id = r["billing_party_id"]
            conn.execute(
                "UPDATE billing_parties SET active = 0, updated_at = ? WHERE billing_party_id = ?",
                (now, r_id),
            )
            record_audit(conn, "billing_party", r_id, "deactivated_by_payer_normalization", {
                "canonical_billing_party_id": canonical_id,
                "person_id": person_id,
            })

        record_audit(conn, "billing_party", canonical_id, "canonical_payer_normalization", {
            "person_id": person_id,
            "deactivated_count": len(redundant),
            "fields_copied": fields_copied,
            "conflict_count": len(conflicts),
        })

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {
        "person_id": person_id,
        "canonical_billing_party_id": canonical_id,
        "deactivated_count": len(redundant),
        "fields_copied": fields_copied,
        "conflicts": conflicts,
        "repointed_accounts": repointed_accounts,
        "repointed_drafts": repointed_drafts,
        "repointed_sessions": repointed_sessions,
        "skipped": None,
    }


def list_billing_relationship_records(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return a unified billing directory of all payer relationships and account groupings.

    Produces one consolidated record per person payer (grouping all their
    active person-linked billing parties and accounts), plus separate records
    for organization billing parties and genuine non-person-linked account groupings.

    No accounts, people, billing parties, sessions, or invoices are created or modified.
    """
    init_db(conn)

    people_map: dict[str, str] = {
        row["person_id"]: row["display_name"]
        for row in conn.execute("SELECT person_id, display_name FROM people").fetchall()
    }

    account_links: dict[str, dict[str, Any]] = {}
    duplicate_analysis = analyze_billing_relationship_duplicates(conn)
    duplicate_account_ids = set(duplicate_analysis["duplicate_account_ids"])
    payer_conflict_account_ids = set(duplicate_analysis["payer_conflict_account_ids"])
    person_linked_account_ids: set[str] = set()
    for row in conn.execute(
        """
        SELECT ca.account_id, ca.account_code, ca.account_name, ca.account_type,
               ca.default_billing_party_id, ca.active,
               bp.person_id AS payer_person_id
        FROM client_accounts ca
        LEFT JOIN billing_parties bp ON bp.billing_party_id = ca.default_billing_party_id
        WHERE ca.default_billing_party_id IS NOT NULL
        """
    ).fetchall():
        link = {
            "account_id": row["account_id"],
            "account_code": row["account_code"],
            "account_name": row["account_name"],
            "account_type": row["account_type"],
        }
        account_links[row["default_billing_party_id"]] = link
        if row["payer_person_id"]:
            person_linked_account_ids.add(row["account_id"])

    bp_rows = conn.execute(
        """
        SELECT
          bp.billing_party_id,
          bp.billing_party_type,
          bp.person_id AS payer_person_id,
          bp.organization_name,
          bp.billing_name,
          bp.billing_email,
          bp.billing_phone,
          bp.preferred_delivery_method,
          bp.active AS billing_party_active,
          COUNT(DISTINCT s.id) AS session_count,
          GROUP_CONCAT(DISTINCT s.id) AS session_ids_raw,
          MAX(s.session_date) AS latest_session_date,
          GROUP_CONCAT(DISTINCT sp.person_id) AS covered_person_ids_raw
        FROM billing_parties bp
        LEFT JOIN sessions s ON s.billing_party_id = bp.billing_party_id
        LEFT JOIN session_participants sp ON sp.session_id = s.id AND sp.person_id IS NOT NULL
        WHERE bp.person_id IS NOT NULL
           OR s.id IS NOT NULL
           OR bp.billing_party_id IN (
             SELECT default_billing_party_id FROM client_accounts
             WHERE default_billing_party_id IS NOT NULL
           )
        GROUP BY bp.billing_party_id
        """
    ).fetchall()

    # --- Consolidate person-linked billing parties by person_id ---
    person_groups: dict[str, list[sqlite3.Row]] = {}
    org_rows: list[sqlite3.Row] = []
    for row in bp_rows:
        if row["billing_party_type"] == "person" and row["payer_person_id"]:
            pid = row["payer_person_id"]
            person_groups.setdefault(pid, []).append(row)
        else:
            org_rows.append(row)

    # Build account-member map for merging covered clients
    account_member_map: dict[str, list[str]] = {}
    for acct_row in conn.execute(
        "SELECT account_id, GROUP_CONCAT(person_id) AS member_ids FROM account_members GROUP BY account_id"
    ).fetchall():
        account_member_map[acct_row["account_id"]] = [
            mid for mid in (acct_row["member_ids"] or "").split(",") if mid
        ]

    account_session_map: dict[str, dict[str, Any]] = {}
    for acct_session_row in conn.execute(
        """
        SELECT
          account_id,
          COUNT(DISTINCT id) AS session_count,
          GROUP_CONCAT(DISTINCT id) AS session_ids_raw,
          MAX(session_date) AS latest_session_date,
          GROUP_CONCAT(DISTINCT sp.person_id) AS covered_person_ids_raw
        FROM sessions s
        LEFT JOIN session_participants sp ON sp.session_id = s.id AND sp.person_id IS NOT NULL
        WHERE account_id IS NOT NULL
        GROUP BY account_id
        """
    ).fetchall():
        account_session_map[acct_session_row["account_id"]] = {
            "session_count": acct_session_row["session_count"],
            "session_ids_raw": acct_session_row["session_ids_raw"],
            "latest_session_date": acct_session_row["latest_session_date"],
            "covered_person_ids_raw": acct_session_row["covered_person_ids_raw"],
        }

    records: list[dict[str, Any]] = []

    for pid, parties in person_groups.items():
        # Pick canonical: most account references, then active, then ID
        acct_counts = {}
        for p in parties:
            acct_counts[p["billing_party_id"]] = conn.execute(
                "SELECT COUNT(*) FROM client_accounts WHERE default_billing_party_id = ? AND active = 1",
                (p["billing_party_id"],),
            ).fetchone()[0]
        canonical = max(parties, key=lambda p: (
            acct_counts.get(p["billing_party_id"], 0),
            int(p["billing_party_active"]),
            p["billing_party_id"],
        ))

        canonical_id = canonical["billing_party_id"]

        # Merge covered people from all billing parties and account members
        all_covered: set[str] = set()
        all_bp_ids: list[str] = []
        all_account_ids: list[str] = []
        all_session_ids: set[str] = set()
        latest_date = None
        for p in parties:
            all_bp_ids.append(p["billing_party_id"])
            for cid in (p["covered_person_ids_raw"] or "").split(","):
                if cid:
                    all_covered.add(cid)
            for sid in (p["session_ids_raw"] or "").split(","):
                if sid:
                    all_session_ids.add(sid)
            d = p["latest_session_date"]
            if d and (latest_date is None or d > latest_date):
                latest_date = d
            link = account_links.get(p["billing_party_id"])
            if link:
                all_account_ids.append(link["account_id"])
                for mid in account_member_map.get(link["account_id"], []):
                    all_covered.add(mid)

        for account_id in list(all_account_ids):
            session_info = account_session_map.get(account_id)
            if not session_info:
                continue
            for sid in (session_info["session_ids_raw"] or "").split(","):
                if sid:
                    all_session_ids.add(sid)
            d = session_info["latest_session_date"]
            if d and (latest_date is None or d > latest_date):
                latest_date = d
            for cid in (session_info["covered_person_ids_raw"] or "").split(","):
                if cid:
                    all_covered.add(cid)

        covered_people = [
            {"person_id": cid, "display_name": people_map.get(cid, "")}
            for cid in sorted(all_covered)
        ]

        if all_covered and all(cid == pid for cid in all_covered):
            record_type = "self_pay"
        elif all_covered:
            record_type = "third_party"
        else:
            record_type = "self_pay"

        link = account_links.get(canonical_id)

        records.append(
            {
                "record_type": record_type,
                "record_id": canonical_id,
                "billing_party_id": canonical_id,
                "payer_person_id": pid,
                "payer_display_name": people_map.get(pid, ""),
                "organization_name": None,
                "billing_name": canonical["billing_name"],
                "billing_party_type": "person",
                "billing_email": canonical["billing_email"],
                "billing_phone": canonical["billing_phone"],
                "preferred_delivery_method": canonical["preferred_delivery_method"],
                "active": bool(canonical["billing_party_active"]),
                "covered_people": covered_people,
                "session_count": len(all_session_ids),
                "latest_session_date": latest_date,
                "account_id": link["account_id"] if link else None,
                "account_code": link["account_code"] if link else None,
                "account_name": link["account_name"] if link else None,
                "account_type": link["account_type"] if link else None,
                "consolidated_billing_party_ids": all_bp_ids,
                "consolidated_account_ids": all_account_ids,
                "has_payer_record_conflict": len(parties) > 1,
                "has_exact_active_duplicate": any(
                    aid in duplicate_account_ids for aid in all_account_ids
                ),
            }
        )

    # --- Organization billing parties (not consolidated) ---
    for row in org_rows:
        bp_id = row["billing_party_id"]
        bp_type = row["billing_party_type"]
        active = bool(row["billing_party_active"])

        covered_ids = [
            pid for pid in (row["covered_person_ids_raw"] or "").split(",") if pid
        ]
        covered_people = [
            {"person_id": pid, "display_name": people_map.get(pid, "")}
            for pid in covered_ids
        ]

        record_type = "organization"
        link = account_links.get(bp_id)

        records.append(
            {
                "record_type": record_type,
                "record_id": bp_id,
                "billing_party_id": bp_id,
                "payer_person_id": None,
                "payer_display_name": None,
                "organization_name": row["organization_name"],
                "billing_name": row["billing_name"],
                "billing_party_type": bp_type,
                "billing_email": row["billing_email"],
                "billing_phone": row["billing_phone"],
                "preferred_delivery_method": row["preferred_delivery_method"],
                "active": active,
                "covered_people": covered_people,
                "session_count": row["session_count"],
                "latest_session_date": row["latest_session_date"],
                "account_id": link["account_id"] if link else None,
                "account_code": link["account_code"] if link else None,
                "account_name": link["account_name"] if link else None,
                "account_type": link["account_type"] if link else None,
                "consolidated_billing_party_ids": [bp_id],
                "consolidated_account_ids": [link["account_id"]] if link else [],
                "has_exact_active_duplicate": bool(link and link["account_id"] in duplicate_account_ids),
                "has_payer_record_conflict": False,
            }
        )

    # --- Account rows: keep genuine billing relationships visible even when their Bill To is person-linked. ---
    acct_rows = conn.execute(
        """
        SELECT
          ca.account_id,
          ca.account_code,
          ca.account_name,
          ca.account_type,
          ca.active AS account_active,
          ca.default_billing_party_id,
          bp.billing_name AS default_billing_party_name,
          bp.person_id AS payer_person_id,
          GROUP_CONCAT(DISTINCT am.person_id) AS member_ids_raw,
          COUNT(DISTINCT s.id) AS session_count,
          MAX(s.session_date) AS latest_session_date
        FROM client_accounts ca
        LEFT JOIN billing_parties bp ON bp.billing_party_id = ca.default_billing_party_id
        LEFT JOIN account_members am ON am.account_id = ca.account_id
        LEFT JOIN sessions s ON s.account_id = ca.account_id
        GROUP BY ca.account_id
        """
    ).fetchall()

    for row in acct_rows:
        if row["account_id"] in person_linked_account_ids and row["account_id"] in account_session_map:
            continue
        member_ids = [
            mid for mid in (row["member_ids_raw"] or "").split(",") if mid
        ]
        members = [
            {"person_id": mid, "display_name": people_map.get(mid, "")}
            for mid in member_ids
        ]

        records.append(
            {
                "record_type": "account",
                "record_id": row["account_id"],
                "billing_party_id": row["default_billing_party_id"],
                "payer_person_id": None,
                "payer_display_name": None,
                "organization_name": None,
                "billing_name": row["default_billing_party_name"],
                "billing_party_type": None,
                "billing_email": None,
                "billing_phone": None,
                "preferred_delivery_method": None,
                "active": bool(row["account_active"]),
                "covered_people": members,
                "session_count": row["session_count"],
                "latest_session_date": row["latest_session_date"],
                "account_id": row["account_id"],
                "account_code": row["account_code"],
                "account_name": row["account_name"],
                "account_type": row["account_type"],
                "consolidated_billing_party_ids": [row["default_billing_party_id"]] if row["default_billing_party_id"] else [],
                "consolidated_account_ids": [row["account_id"]],
                "has_exact_active_duplicate": row["account_id"] in duplicate_account_ids,
                "has_payer_record_conflict": row["account_id"] in payer_conflict_account_ids,
            }
        )

    def sort_key(r: dict[str, Any]) -> tuple:
        display = (
            r.get("payer_display_name")
            or r.get("organization_name")
            or r.get("billing_name")
            or r.get("account_name")
            or ""
        )
        return (0 if r["active"] else 1, display.lower(), r["record_id"])

    records.sort(key=sort_key)
    return records


def find_duplicate_billing_relationship(
    conn: sqlite3.Connection,
    payer_kind: str,
    payer_person_id: str | None,
    organization_billing_party_id: str | None,
    covered_client_ids: list[str],
    *,
    exclude_account_id: str | None = None,
) -> dict[str, Any] | None:
    """Find an active billing relationship that is an exact duplicate of the requested setup.

    A duplicate exists when:
    1. The account's default billing party represents the same payer
    2. The active account-member person-ID set exactly equals the requested covered-client set
    3. The account is active

    Returns a dict with account_id and billing_party_id if found, None otherwise.
    """
    init_db(conn)
    requested_set = frozenset(_normalized_covered_client_ids(covered_client_ids))

    if payer_kind in ("client", "person"):
        if not payer_person_id:
            return None
        accounts = conn.execute(
            """
            SELECT DISTINCT a.account_id, a.default_billing_party_id
            FROM client_accounts a
            JOIN billing_parties bp ON bp.billing_party_id = a.default_billing_party_id
            WHERE bp.person_id = ? AND a.active = 1
            """,
            (payer_person_id,),
        ).fetchall()
    elif payer_kind == "organization":
        if not organization_billing_party_id:
            return None
        accounts = conn.execute(
            """
            SELECT DISTINCT a.account_id, a.default_billing_party_id
            FROM client_accounts a
            WHERE a.default_billing_party_id = ? AND a.active = 1
            """,
            (organization_billing_party_id,),
        ).fetchall()
    else:
        return None

    for row in accounts:
        if exclude_account_id and row["account_id"] == exclude_account_id:
            continue
        member_ids = frozenset(
            r["person_id"]
            for r in conn.execute(
                "SELECT person_id FROM account_members WHERE account_id = ?",
                (row["account_id"],),
            ).fetchall()
        )
        if member_ids == requested_set:
            return {
                "account_id": row["account_id"],
                "billing_party_id": row["default_billing_party_id"],
            }
    return None


def _derive_account_type(payer_kind: str, covered_client_ids: list[str], payer_person_id: str | None) -> str:
    """Derive a backend account_type from the payer kind and covered-client set."""
    if payer_kind == "client" and len(covered_client_ids) == 1 and covered_client_ids[0] == payer_person_id:
        return "individual"
    return "family"


def _derive_account_name(
    conn: sqlite3.Connection,
    payer_kind: str,
    payer_person_id: str | None,
    organization_billing_party_id: str | None,
    covered_client_ids: list[str],
) -> str:
    """Generate a human-readable account name for a new billing relationship."""
    if payer_kind in ("client", "person") and payer_person_id:
        row = conn.execute(
            "SELECT display_name FROM people WHERE person_id = ?", (payer_person_id,)
        ).fetchone()
        payer_name = row["display_name"] if row else "Unknown"
    elif payer_kind == "organization" and organization_billing_party_id:
        row = conn.execute(
            "SELECT organization_name, billing_name FROM billing_parties WHERE billing_party_id = ?",
            (organization_billing_party_id,),
        ).fetchone()
        payer_name = (row["organization_name"] if row else None) or (row["billing_name"] if row else "Unknown")
    else:
        payer_name = "Unknown"

    if payer_kind == "client" and len(covered_client_ids) == 1 and covered_client_ids[0] == payer_person_id:
        return payer_name

    covered_names = []
    for pid in covered_client_ids:
        row = conn.execute(
            "SELECT display_name FROM people WHERE person_id = ?", (pid,)
        ).fetchone()
        if row:
            covered_names.append(row["display_name"])

    if len(covered_names) == 0:
        return payer_name
    if len(covered_names) == 1:
        return f"{payer_name} — pays for {covered_names[0]}"
    if len(covered_names) == 2:
        return f"{payer_name} — pays for {covered_names[0]} & {covered_names[1]}"
    return f"{payer_name} — pays for {covered_names[0]} & {len(covered_names) - 1} others"


def setup_billing_relationship(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Transactionally set up a billing relationship from existing records.

    Supported payer kinds: client, person, organization.
    All payer and covered-client records must already exist and be active.

    Returns a dict with:
        account_id, billing_party_id, account_name, account_type,
        covered_client_ids, created, duplicate
    """
    init_db(conn)

    # --- Validate payer_kind ---
    payer_kind = (payload.get("payer_kind") or "").strip().lower()
    if payer_kind not in ("client", "person", "organization"):
        raise ValueError("payer_kind must be one of: client, person, organization.")

    # --- Validate covered_client_ids ---
    raw_covered = payload.get("covered_client_ids") or []
    if not isinstance(raw_covered, list) or not raw_covered:
        raise ValueError("At least one covered client is required.")
    covered_client_ids = []
    seen = set()
    for cid in raw_covered:
        cid = str(cid).strip()
        if not cid:
            raise ValueError("Covered client IDs must be non-empty strings.")
        if cid in seen:
            raise ValueError("Duplicate covered client IDs are not allowed.")
        seen.add(cid)
        covered_client_ids.append(cid)

    # --- Validate covered clients exist and are active ---
    for cid in covered_client_ids:
        row = conn.execute(
            "SELECT person_id FROM people WHERE person_id = ? AND active = 1", (cid,)
        ).fetchone()
        if not row:
            raise ValueError(f"Covered client {cid} does not exist or is not active.")

    # --- Validate payer ---
    payer_person_id = None
    organization_billing_party_id = None

    if payer_kind in ("client", "person"):
        payer_person_id = str(payload.get("payer_person_id") or "").strip()
        if not payer_person_id:
            raise ValueError("payer_person_id is required for client or person payer kind.")
        payer_row = conn.execute(
            "SELECT * FROM people WHERE person_id = ? AND active = 1", (payer_person_id,)
        ).fetchone()
        if not payer_row:
            raise ValueError("Payer person does not exist or is not active.")
    elif payer_kind == "organization":
        organization_billing_party_id = str(payload.get("organization_billing_party_id") or "").strip()
        if not organization_billing_party_id:
            raise ValueError("organization_billing_party_id is required for organization payer kind.")
        org_row = conn.execute(
            "SELECT * FROM billing_parties WHERE billing_party_id = ? AND active = 1 AND billing_party_type = 'organization'",
            (organization_billing_party_id,),
        ).fetchone()
        if not org_row:
            raise ValueError("Organization billing party does not exist, is not active, or is not an organization.")

    # --- Check for exact duplicate ---
    duplicate = find_duplicate_billing_relationship(
        conn, payer_kind, payer_person_id, organization_billing_party_id, covered_client_ids
    )
    if duplicate:
        account = dict(conn.execute(
            "SELECT * FROM client_accounts WHERE account_id = ?", (duplicate["account_id"],)
        ).fetchone())
        record_audit(
            conn, "client_account", duplicate["account_id"],
            "duplicate_relationship_reused",
            {"payer_kind": payer_kind, "covered_client_count": len(covered_client_ids)},
        )
        conn.commit()
        return {
            "account_id": duplicate["account_id"],
            "billing_party_id": duplicate["billing_party_id"],
            "account_name": account["account_name"],
            "account_type": account["account_type"],
            "covered_client_ids": covered_client_ids,
            "created": False,
            "duplicate": True,
        }

    # --- Transactional creation: all writes commit once or roll back on failure ---
    _begin_immediate(conn)
    try:
        # Resolve or create billing party
        if payer_kind in ("client", "person"):
            billing_party_id = _canonical_billing_party_for_person(conn, payer_person_id)
        else:
            billing_party_id = organization_billing_party_id

        # Derive account type and name
        account_type = _derive_account_type(payer_kind, covered_client_ids, payer_person_id)
        account_name = _derive_account_name(
            conn, payer_kind, payer_person_id, organization_billing_party_id, covered_client_ids
        )

        # Create account (no commit)
        account = create_account(conn, account_name, account_type, commit=False)

        # Add covered clients as members (no commit)
        primary_person_id = None
        if payer_kind == "client" and payer_person_id in covered_client_ids:
            primary_person_id = payer_person_id
        elif covered_client_ids:
            primary_person_id = covered_client_ids[0]

        for cid in covered_client_ids:
            is_primary = (cid == primary_person_id)
            role = "primary" if is_primary else "family_member"
            add_account_member(conn, account["account_id"], cid, role, is_primary, commit=False)

        # Set default billing party on account
        now = now_iso()
        conn.execute(
            "UPDATE client_accounts SET default_billing_party_id = ?, updated_at = ? WHERE account_id = ?",
            (billing_party_id, now, account["account_id"]),
        )
        _upsert_billing_relationship_key(
            conn,
            account_id=account["account_id"],
            payer_kind=payer_kind,
            payer_person_id=payer_person_id,
            organization_billing_party_id=organization_billing_party_id,
            covered_client_ids=covered_client_ids,
        )

        # Audit
        record_audit(
            conn, "client_account", account["account_id"],
            "created_via_setup_service",
            {"payer_kind": payer_kind, "covered_client_count": len(covered_client_ids)},
        )
        record_audit(
            conn, "billing_party", billing_party_id,
            "reused_in_setup_service" if payer_kind == "organization" else "reused_or_created_in_setup_service",
            {"account_id": account["account_id"], "payer_kind": payer_kind},
        )

        # Commit once
        conn.commit()
    except sqlite3.IntegrityError as error:
        conn.rollback()
        raise ValueError("This billing relationship already exists.") from error
    except Exception:
        conn.rollback()
        raise

    return {
        "account_id": account["account_id"],
        "billing_party_id": billing_party_id,
        "account_name": account["account_name"],
        "account_type": account["account_type"],
        "covered_client_ids": covered_client_ids,
        "created": True,
        "duplicate": False,
    }
