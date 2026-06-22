from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from typing import Any

from .backfill import backfill_phase2
from .calendar_preferences import upsert_calendar_preference
from .csv_reports import write_reports
from .db import init_db
from .rates import cents_to_dollars, dollars_to_cents, seed_rate_rule, suggest_rate
from .util import json_dumps, new_id, now_iso, text


REQUIRED_APPROVAL_FIELDS = {
    "participants",
    "billing_party_id",
    "approved_duration_minutes",
    "service_mode",
    "time_category",
    "approved_rate_cents",
    "payment_status",
}


def dashboard_status(conn: sqlite3.Connection) -> dict[str, Any]:
    init_db(conn)
    backfill_phase2(conn)
    apply_smart_prefill(conn)
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
                "needs_payment_status",
                "needs_review",
            )
        ),
        "ready_to_approve": counts.get("ready_for_approval", 0),
        "approved_this_month": counts.get("approved", 0),
        "personal_admin": int(personal_admin),
    }


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
    time_category: str = "",
    payment_status: str = "",
    calendar_filter: str = "",
    limit: int = 25,
    offset: int = 0,
) -> dict[str, Any]:
    init_db(conn)
    backfill_phase2(conn)
    apply_smart_prefill(conn)
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
    if service_mode:
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
    else:
        filters.append("COALESCE(s.hidden_from_review, 0) = 0")
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
    return {
        "session": dict(row),
        "participants": get_session_participants(conn, row["id"]),
        "account": get_account(conn, row["account_id"]),
        "account_members": get_account_members(conn, row["account_id"]),
        "billing_party": get_billing_party(conn, row["billing_party_id"]),
        "checklist": checklist_for(row, get_session_participants(conn, row["id"])),
        "audit": audit_history(conn, row["id"], row["candidate_id"]),
    }


def row_summary(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    participants = get_session_participants(conn, row["session_id"])
    participant_names = [p.get("display_name") or p.get("participant_name") for p in participants]
    candidate_people = parse_json(row["candidate_person_names"], [])
    suggested = "; ".join(participant_names or candidate_people)
    account_name = row["account_name"] or suggested
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
        "time_category": row["time_category"] or "standard",
        "payment_status": row["payment_status"] or "unresolved",
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
        "classification": row["classification"],
        "review_reasons": parse_json(row["review_reasons"], []),
    }


def list_candidate_only_rows(
    conn: sqlite3.Connection,
    query: str = "",
    review_status: str = "",
    calendar_filter: str = "",
) -> list[dict[str, Any]]:
    filters = [
        "c.id NOT IN (SELECT candidate_id FROM sessions)",
        "c.classification IN ('personal', 'administrative', 'nonbillable', 'cancelled', 'no_show', 'unresolved')",
    ]
    params: list[Any] = []
    if query:
        filters.append("(c.title LIKE ? OR c.possible_referenced_person LIKE ?)")
        like = f"%{query}%"
        params.extend([like, like])
    if review_status:
        filters.append("c.review_status = ?")
        params.append(review_status)
    if calendar_filter:
        add_calendar_filter(filters, params, calendar_filter, "c")
    else:
        filters.append("COALESCE(c.hidden_from_review, 0) = 0")
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
        "payment_status": "not_billable",
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
        "payment_status": "not_billable",
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
        "participants": [],
        "account": None,
        "account_members": [],
        "billing_party": None,
        "checklist": [
            {"label": "Classification confirmed", "resolved": row["review_status"] in {"excluded", "approved"}},
            {"label": "Reusable alias decision", "resolved": False},
        ],
        "audit": audit_history(conn, "", row["id"]),
    }


def save_interpretation(conn: sqlite3.Connection, candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    init_db(conn)
    now = now_iso()
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
    time_category = normalize_time_category(payload.get("time_category") or session["time_category"])
    payment_status = payload.get("payment_status") or session["payment_status"] or "unresolved"
    billable_status = payload.get("billable_status") or session["billable_status"] or "proposed"
    billing_treatment = payload.get("billing_treatment") or session["billing_treatment"] or "unresolved"
    rate_override_reason = payload.get("rate_override_reason") or None
    rate_scope = payload.get("rate_scope") or "session_only"
    approved_source = approved_rate_source_for(session, approved_rate_cents, rate_scope)

    conn.execute("DELETE FROM session_participants WHERE session_id = ?", (session_id,))
    for index, participant in enumerate(participants):
        person_id = participant.get("person_id")
        participant_name = participant.get("display_name") or participant.get("participant_name")
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
        participants=participants,
        billing_party_id=billing_party_id,
        duration=duration,
        service_mode=service_mode,
        time_category=time_category,
        approved_rate_cents=approved_rate_cents,
        payment_status=payment_status,
        appointment_status=session["appointment_status"],
        billing_treatment=billing_treatment,
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
            rate_group = ?,
            time_category = ?,
            suggested_rate_cents = COALESCE(?, suggested_rate_cents),
            approved_rate_cents = ?,
            approved_rate_source = ?,
            approved_rate_rule_id = CASE WHEN ? = 'manual_override' THEN NULL ELSE approved_rate_rule_id END,
            rate_needs_review = ?,
            rate_override_reason = ?,
            payment_status = ?,
            billable_status = ?,
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
            rate_group_for(service_mode),
            time_category,
            suggested_rate_cents,
            approved_rate_cents,
            approved_source,
            approved_source,
            0 if approved_rate_cents is not None else 1,
            rate_override_reason,
            payment_status,
            billable_status,
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
    conn.commit()
    return get_review_candidate(conn, candidate_id)


def approve_candidate(conn: sqlite3.Connection, candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    saved = save_interpretation(conn, candidate_id, payload)
    session = session_for_candidate(conn, candidate_id)
    participants = get_session_participants(conn, session["id"])
    unresolved = unresolved_from_values(
        participants=participants,
        billing_party_id=session["billing_party_id"],
        duration=session["approved_duration_minutes"] or session["duration_minutes"],
        service_mode=session["service_mode"],
        time_category=session["time_category"],
        approved_rate_cents=session["approved_rate_cents"],
        payment_status=session["payment_status"],
        appointment_status=session["appointment_status"],
        billing_treatment=session["billing_treatment"],
    )
    if unresolved:
        raise ValueError("Cannot approve until required fields are complete: " + ", ".join(unresolved))
    now = now_iso()
    conn.execute(
        """
        UPDATE sessions
        SET review_status = 'approved',
            billable_status = 'approved',
            rate_cents_snapshot = approved_rate_cents,
            approved_rate_rule_id = COALESCE(approved_rate_rule_id, rate_rule_id),
            approved_rate_source = COALESCE(approved_rate_source, rate_source, 'manual_override'),
            updated_at = ?
        WHERE id = ?
        """,
        (now, session["id"]),
    )
    conn.execute(
        "UPDATE calendar_event_candidates SET review_status = 'approved', updated_at = ? WHERE id = ?",
        (now, candidate_id),
    )
    save_alias_after_approval(conn, session, participants)
    record_audit(conn, "session", session["id"], "approved", {"candidate_id": candidate_id})
    add_review_item(conn, candidate_id, session["id"], "approved", [], ["Approved in review UI."])
    write_reports(conn)
    conn.commit()
    return get_review_candidate(conn, candidate_id)


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
    if participants:
        conn.execute("DELETE FROM session_participants WHERE session_id = ?", (session["id"],))
        for index, participant in enumerate(participants):
            person_id = participant.get("person_id")
            is_primary = bool(participant.get("is_primary")) or (primary_person_id and person_id == primary_person_id) or (index == 0 and len(participants) == 1)
            add_session_participant(
                conn,
                session["id"],
                person_id,
                participant.get("display_name") or participant.get("participant_name") or "",
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
    refresh_candidate_suggestions(conn, candidate_id)
    record_audit(conn, "session", session["id"], "relationship_section_saved", {"payload": safe_payload(payload)})
    conn.commit()
    return get_review_candidate(conn, candidate_id)


def save_billing_section(conn: sqlite3.Connection, candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    init_db(conn)
    session = session_for_candidate(conn, candidate_id)
    billing_party_id = payload.get("billing_party_id")
    if payload.get("billing_party"):
        billing = payload["billing_party"]
        if billing.get("billing_party_id"):
            updated = update_billing_party(conn, billing["billing_party_id"], billing)
            billing_party_id = updated["billing_party_id"]
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
    time_category = normalize_time_category(payload.get("time_category") or session["time_category"])
    payment_status = payload.get("payment_status") or session["payment_status"] or "unresolved"
    billable_status = payload.get("billable_status") or session["billable_status"] or "proposed"
    billing_treatment = payload.get("billing_treatment") or session["billing_treatment"] or "unresolved"
    rate_scope = payload.get("rate_scope") or "session_only"
    approved_source = approved_rate_source_for(session, approved_rate_cents, rate_scope)
    conn.execute(
        """
        UPDATE sessions
        SET approved_duration_minutes = ?,
            duration_minutes = ?,
            service_mode = ?,
            rate_group = ?,
            time_category = ?,
            suggested_rate_cents = COALESCE(?, suggested_rate_cents),
            approved_rate_cents = ?,
            approved_rate_source = ?,
            approved_rate_rule_id = CASE WHEN ? = 'manual_override' THEN NULL ELSE approved_rate_rule_id END,
            rate_needs_review = ?,
            rate_override_reason = ?,
            payment_status = ?,
            billable_status = ?,
            billing_treatment = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            duration,
            duration,
            service_mode,
            rate_group_for(service_mode),
            time_category,
            suggested_rate_cents,
            approved_rate_cents,
            approved_source,
            approved_source,
            0 if approved_rate_cents is not None else 1,
            payload.get("rate_override_reason") or None,
            payment_status,
            billable_status,
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
            (review_status, "excluded", "not_billable", now, session["id"]),
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
        SELECT s.session_date, s.start_at, s.duration_minutes, s.service_mode, s.time_category,
               s.approved_rate_cents, s.approved_rate_source, s.rate_source,
               s.payment_status, s.review_status, s.raw_calendar_title
        FROM session_participants sp
        JOIN sessions s ON s.id = sp.session_id
        WHERE sp.person_id = ?
        ORDER BY s.start_at DESC
        LIMIT 50
        """,
        (person_id,),
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
        return dict(existing)
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
    return dict(conn.execute("SELECT * FROM people WHERE person_id = ?", (person_id,)).fetchone())


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
            SELECT SUM(CASE WHEN s3.payment_status != 'paid' THEN COALESCE(s3.approved_rate_cents, 0) ELSE 0 END)
            FROM sessions s3
            WHERE s3.account_id = ca.account_id
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
        item["outstanding_balance"] = cents_to_dollars(item.pop("outstanding_cents") or 0)
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


def create_account(conn: sqlite3.Connection, account_name: str, account_type: str = "individual") -> dict[str, Any]:
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


def search_billing_parties(conn: sqlite3.Connection, query: str = "") -> list[dict[str, Any]]:
    return search_table(conn, "billing_parties", "billing_party_id", "billing_name", query)


def create_billing_party(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    init_db(conn)
    now = now_iso()
    billing_party_id = new_id()
    billing_name = data.get("billing_name") or data.get("display_name") or data.get("name")
    conn.execute(
        """
        INSERT INTO billing_parties (
          billing_party_id, billing_party_type, person_id, organization_name,
          billing_name, billing_email, billing_address_line_1, billing_address_line_2,
          billing_city, billing_state, billing_postal_code, billing_phone,
          administrative_notes,
          created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            billing_party_id,
            data.get("billing_party_type") or "person",
            data.get("person_id"),
            data.get("organization_name"),
            billing_name,
            data.get("billing_email"),
            data.get("billing_address_line_1"),
            data.get("billing_address_line_2"),
            data.get("billing_city"),
            data.get("billing_state"),
            data.get("billing_postal_code"),
            data.get("billing_phone"),
            data.get("administrative_notes"),
            now,
            now,
        ),
    )
    record_audit(conn, "billing_party", billing_party_id, "created_inline", {"billing_name": billing_name})
    conn.commit()
    return dict(conn.execute("SELECT * FROM billing_parties WHERE billing_party_id = ?", (billing_party_id,)).fetchone())


def update_billing_party(conn: sqlite3.Connection, billing_party_id: str, data: dict[str, Any]) -> dict[str, Any]:
    init_db(conn)
    existing = conn.execute("SELECT * FROM billing_parties WHERE billing_party_id = ?", (billing_party_id,)).fetchone()
    if not existing:
        raise ValueError("Billing party not found.")
    now = now_iso()
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
            administrative_notes = ?,
            active = ?,
            updated_at = ?
        WHERE billing_party_id = ?
        """,
        (
            data.get("billing_party_type") or existing["billing_party_type"],
            data.get("person_id") or existing["person_id"],
            data.get("organization_name") or existing["organization_name"],
            data.get("billing_name") or existing["billing_name"],
            data.get("billing_email") or existing["billing_email"],
            data.get("billing_address_line_1") or existing["billing_address_line_1"],
            data.get("billing_address_line_2") or existing["billing_address_line_2"],
            data.get("billing_city") or existing["billing_city"],
            data.get("billing_state") or existing["billing_state"],
            data.get("billing_postal_code") or existing["billing_postal_code"],
            data.get("billing_phone") or existing["billing_phone"],
            data.get("administrative_notes") if "administrative_notes" in data else existing["administrative_notes"],
            1 if data.get("active", True) else 0,
            now,
            billing_party_id,
        ),
    )
    record_audit(conn, "billing_party", billing_party_id, "updated_inline", data)
    conn.commit()
    return dict(conn.execute("SELECT * FROM billing_parties WHERE billing_party_id = ?", (billing_party_id,)).fetchone())


def add_account_member(
    conn: sqlite3.Connection,
    account_id: str,
    person_id: str,
    relationship_role: str = "primary",
    is_primary: bool = False,
) -> str:
    init_db(conn)
    now = now_iso()
    member_id = new_id()
    conn.execute(
        """
        INSERT OR IGNORE INTO account_members (
          account_member_id, account_id, person_id, relationship_role,
          is_primary, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (member_id, account_id, person_id, relationship_role, 1 if is_primary else 0, now, now),
    )
    record_audit(conn, "account_member", member_id, "created_inline", {"account_id": account_id, "person_id": person_id})
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

    if account_id and not billing_party_id:
        account = conn.execute(
            "SELECT default_billing_party_id FROM client_accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if account and account["default_billing_party_id"]:
            billing_party_id = account["default_billing_party_id"]
            conn.execute(
                "UPDATE sessions SET billing_party_id = ?, updated_at = ? WHERE id = ?",
                (billing_party_id, now, session["id"]),
            )
    if not billing_party_id:
        billing_party_id = default_billing_party_for_participants(conn, participants)
        if billing_party_id:
            conn.execute(
                "UPDATE sessions SET billing_party_id = ?, updated_at = ? WHERE id = ?",
                (billing_party_id, now, session["id"]),
            )

    duration = session["approved_duration_minutes"] or session["duration_minutes"]
    service_mode = normalize_service_mode(session["service_mode"])
    time_category = normalize_time_category(session["time_category"])
    participant_person_ids = [p["person_id"] for p in participants if p.get("person_id")]
    suggestion = suggest_rate(
        conn,
        session_date=session["session_date"] or text(session["start_at"])[:10],
        duration_minutes=duration,
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
                rate_rule_id = ?,
                rate_source = ?,
                rate_needs_review = ?,
                rate_override_reason = COALESCE(rate_override_reason, ?),
                updated_at = ?
            WHERE id = ?
            """,
            (
                suggestion.suggested_rate_cents,
                suggestion.rate_rule_id,
                suggestion.rate_source,
                1 if suggestion.rate_needs_review and approved_rate is None else 0,
                suggestion.explanation,
                now,
                session["id"],
            ),
        )

    refreshed = session_for_candidate(conn, candidate_id)
    unresolved = unresolved_from_values(
        participants=get_session_participants(conn, refreshed["id"]),
        billing_party_id=refreshed["billing_party_id"],
        duration=refreshed["approved_duration_minutes"] or refreshed["duration_minutes"],
        service_mode=refreshed["service_mode"],
        time_category=refreshed["time_category"],
        approved_rate_cents=refreshed["approved_rate_cents"],
        payment_status=refreshed["payment_status"],
        appointment_status=refreshed["appointment_status"],
        billing_treatment=refreshed["billing_treatment"],
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
    if updated:
        conn.commit()
    return updated


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
        raise ValueError("Session not found for candidate.")
    return row


def get_session_participants(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT sp.*, p.display_name
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


def default_billing_party_for_participants(
    conn: sqlite3.Connection,
    participants: list[dict[str, Any]],
) -> str | None:
    person_ids = [p["person_id"] for p in participants if p.get("person_id")]
    if not person_ids:
        return None
    placeholders = ",".join("?" for _ in person_ids)
    prior = conn.execute(
        f"""
        SELECT s.billing_party_id
        FROM sessions s
        JOIN session_participants sp ON sp.session_id = s.id
        WHERE sp.person_id IN ({placeholders})
          AND s.billing_party_id IS NOT NULL
          AND s.review_status = 'approved'
        ORDER BY s.start_at DESC
        LIMIT 1
        """,
        tuple(person_ids),
    ).fetchone()
    if prior and prior["billing_party_id"]:
        return prior["billing_party_id"]
    if len(person_ids) == 1:
        person = conn.execute(
            "SELECT display_name FROM people WHERE person_id = ?",
            (person_ids[0],),
        ).fetchone()
        if not person:
            return None
        existing = conn.execute(
            """
            SELECT billing_party_id
            FROM billing_parties
            WHERE person_id = ? AND active = 1
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (person_ids[0],),
        ).fetchone()
        if existing:
            return existing["billing_party_id"]
        created = create_billing_party(
            conn,
            {
                "billing_party_type": "person",
                "person_id": person_ids[0],
                "billing_name": person["display_name"],
            },
        )
        return created["billing_party_id"]
    return None


def checklist_for(row: sqlite3.Row, participants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks = [
        ("Participants identified", bool(participants)),
        ("Bill to selected", bool(row["billing_party_id"])),
        ("Duration confirmed", bool(row["approved_duration_minutes"] or row["duration_minutes"])),
        ("Service mode confirmed", bool(row["service_mode"] and row["service_mode"] != "unknown")),
        ("Time category confirmed", bool(row["time_category"])),
        ("Rate confirmed", bool(row["approved_rate_cents"])),
        ("Payment status confirmed", bool(row["payment_status"] and row["payment_status"] != "unresolved")),
    ]
    return [{"label": label, "resolved": resolved} for label, resolved in checks]


def unresolved_from_values(**values: Any) -> list[str]:
    unresolved = []
    if not values["participants"]:
        unresolved.append("participants")
    if not values["billing_party_id"]:
        unresolved.append("billing_party_id")
    if not values["duration"]:
        unresolved.append("approved_duration_minutes")
    if not values["service_mode"] or values["service_mode"] == "unknown":
        unresolved.append("service_mode")
    if not values["time_category"]:
        unresolved.append("time_category")
    if values["approved_rate_cents"] is None:
        unresolved.append("approved_rate_cents")
    if values.get("appointment_status") in {"cancelled", "no_show"} and values.get("billing_treatment") in {"", None, "unresolved"}:
        unresolved.append("billing_treatment")
    if not values["payment_status"] or values["payment_status"] == "unresolved":
        unresolved.append("payment_status")
    return unresolved


def status_from_unresolved(unresolved: list[str]) -> str:
    if "participants" in unresolved:
        return "needs_participants"
    if "billing_party_id" in unresolved:
        return "needs_billing_party"
    if "service_mode" in unresolved:
        return "needs_service_mode"
    if "approved_rate_cents" in unresolved:
        return "needs_rate"
    if "billing_treatment" in unresolved:
        return "needs_billing_treatment"
    if "payment_status" in unresolved:
        return "needs_payment_status"
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
    if not value or "T" not in value:
        return ""
    return value.split("T", 1)[1][:5]


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
        "weekend_evening": "weekend_evening",
        "weekend_+_evening": "weekend_evening",
    }.get(normalized, normalized or "standard")


def rate_group_for(service_mode: str) -> str:
    return {"phone": "remote", "facetime": "remote", "office": "office", "house_call": "house_call"}.get(service_mode, "")


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
          AND COALESCE(service_mode, '') = ?
          AND time_category = ?
          AND rate_rule_id NOT IN (SELECT rate_rule_id FROM rate_rule_participants)
        ORDER BY effective_from DESC
        LIMIT 1
        """,
        (person_id, duration_minutes, service_mode, time_category),
    ).fetchone()
    if row:
        conn.execute(
            """
            UPDATE rate_rules
            SET amount_cents = ?, effective_from = ?, updated_at = ?
            WHERE rate_rule_id = ?
            """,
            (amount_cents, effective_from, now_iso(), row["rate_rule_id"]),
        )
        return row["rate_rule_id"]
    return seed_rate_rule(
        conn,
        amount_cents=amount_cents,
        effective_from=effective_from,
        duration_minutes=duration_minutes,
        service_mode=service_mode,
        rate_group=rate_group_for(service_mode),
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
          AND COALESCE(rr.service_mode, '') = ?
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
        (duration_minutes, service_mode, time_category, *person_ids, len(person_ids), len(person_ids)),
    ).fetchone()
    if row:
        conn.execute(
            """
            UPDATE rate_rules
            SET amount_cents = ?, effective_from = ?, updated_at = ?
            WHERE rate_rule_id = ?
            """,
            (amount_cents, effective_from, now_iso(), row["rate_rule_id"]),
        )
        return row["rate_rule_id"]
    return seed_rate_rule(
        conn,
        amount_cents=amount_cents,
        effective_from=effective_from,
        duration_minutes=duration_minutes,
        service_mode=service_mode,
        rate_group=rate_group_for(service_mode),
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
        WHERE rr.active = 1
        ORDER BY rr.effective_from DESC, rr.priority ASC, rr.duration_minutes
        """
    ).fetchall()
    output = []
    for row in rows:
        item = dict(row)
        item["amount"] = cents_to_dollars(row["amount_cents"])
        participants = conn.execute(
            """
            SELECT p.display_name
            FROM rate_rule_participants rrp
            JOIN people p ON p.person_id = rrp.person_id
            WHERE rrp.rate_rule_id = ?
            ORDER BY p.display_name
            """,
            (row["rate_rule_id"],),
        ).fetchall()
        item["participant_names"] = " + ".join(p["display_name"] for p in participants)
        output.append(item)
    return output


def create_rate_rule_from_payload(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    init_db(conn)
    rule_id = seed_rate_rule(
        conn,
        amount_cents=money_payload_to_cents(data.get("amount")) or 0,
        effective_from=data.get("effective_from") or "2026-01-01",
        duration_minutes=int(data["duration_minutes"]) if text(data.get("duration_minutes")) else None,
        service_mode=normalize_service_mode(data.get("service_mode")) if data.get("service_mode") else None,
        rate_group=data.get("rate_group"),
        time_category=normalize_time_category(data.get("time_category") or "standard"),
        client_account_id=data.get("client_account_id"),
        person_id=data.get("person_id"),
        participant_person_ids=data.get("participant_person_ids") or None,
        priority=int(data.get("priority") or 100),
    )
    record_audit(conn, "rate_rule", rule_id, "created_inline", data)
    conn.commit()
    return dict(conn.execute("SELECT * FROM rate_rules WHERE rate_rule_id = ?", (rule_id,)).fetchone())
