from __future__ import annotations

import json
import sqlite3
from typing import Any

from .backfill import backfill_phase2
from .csv_reports import write_reports
from .db import init_db
from .rates import cents_to_dollars, dollars_to_cents, seed_rate_rule
from .util import json_dumps, new_id, now_iso, text


REQUIRED_APPROVAL_FIELDS = {
    "participants",
    "account_id",
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
    return {
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


def list_review_candidates(
    conn: sqlite3.Connection,
    *,
    query: str = "",
    review_status: str = "",
    service_mode: str = "",
    time_category: str = "",
    payment_status: str = "",
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
          s.review_status,
          s.raw_calendar_title,
          s.suggested_rate_cents,
          s.approved_rate_cents,
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
    candidate_only = list_candidate_only_rows(conn, query=query, review_status=review_status)
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
        "rate": cents_to_dollars(row["approved_rate_cents"] or row["suggested_rate_cents"]),
        "confidence": round(float(row["confidence"] or 0) * 100),
        "classification": row["classification"],
        "review_reasons": parse_json(row["review_reasons"], []),
    }


def list_candidate_only_rows(
    conn: sqlite3.Connection,
    query: str = "",
    review_status: str = "",
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
    rate_override_reason = payload.get("rate_override_reason") or None

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
        account_id=account_id,
        billing_party_id=billing_party_id,
        duration=duration,
        service_mode=service_mode,
        time_category=time_category,
        approved_rate_cents=approved_rate_cents,
        payment_status=payment_status,
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
            rate_needs_review = ?,
            rate_override_reason = ?,
            payment_status = ?,
            billable_status = ?,
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
            0 if approved_rate_cents is not None else 1,
            rate_override_reason,
            payment_status,
            billable_status,
            review_status,
            now,
            session_id,
        ),
    )
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
        account_id=session["account_id"],
        billing_party_id=session["billing_party_id"],
        duration=session["approved_duration_minutes"] or session["duration_minutes"],
        service_mode=session["service_mode"],
        time_category=session["time_category"],
        approved_rate_cents=session["approved_rate_cents"],
        payment_status=session["payment_status"],
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


def create_person(conn: sqlite3.Connection, display_name: str) -> dict[str, Any]:
    init_db(conn)
    existing = conn.execute(
        "SELECT * FROM people WHERE lower(display_name) = lower(?) AND active = 1 LIMIT 1",
        (display_name,),
    ).fetchone()
    if existing:
        return dict(existing)
    now = now_iso()
    person_id = new_id()
    first, last = split_name(display_name)
    conn.execute(
        """
        INSERT INTO people (
          person_id, display_name, first_name, last_name, preferred_name,
          person_code, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (person_id, display_name, first, last, first, make_code("P", display_name), now, now),
    )
    record_audit(conn, "person", person_id, "created_inline", {"display_name": display_name})
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
            data.get("person_code"),
            data.get("billing_email"),
            data.get("billing_phone"),
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
        {"old_value": old_name, "new_value": display_name, "source": "review_ui"},
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


def create_account(conn: sqlite3.Connection, account_name: str, account_type: str = "individual") -> dict[str, Any]:
    init_db(conn)
    now = now_iso()
    account_id = new_id()
    conn.execute(
        """
        INSERT INTO client_accounts (
          account_id, account_code, account_name, account_type, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (account_id, make_code("A", account_name), account_name, account_type, now, now),
    )
    record_audit(conn, "client_account", account_id, "created_inline", {"account_name": account_name})
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
            active = ?,
            updated_at = ?
        WHERE account_id = ?
        """,
        (
            data.get("account_name") or existing["account_name"],
            data.get("account_type") or existing["account_type"],
            data.get("default_billing_party_id") or existing["default_billing_party_id"],
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
          created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def checklist_for(row: sqlite3.Row, participants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks = [
        ("Participants identified", bool(participants)),
        ("Account connected", bool(row["account_id"])),
        ("Billing party selected", bool(row["billing_party_id"])),
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
    if not values["account_id"]:
        unresolved.append("account_id")
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
    if not values["payment_status"] or values["payment_status"] == "unresolved":
        unresolved.append("payment_status")
    return unresolved


def status_from_unresolved(unresolved: list[str]) -> str:
    if "participants" in unresolved:
        return "needs_participants"
    if "account_id" in unresolved:
        return "needs_account"
    if "billing_party_id" in unresolved:
        return "needs_billing_party"
    if "service_mode" in unresolved:
        return "needs_service_mode"
    if "approved_rate_cents" in unresolved:
        return "needs_rate"
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


def record_audit(conn: sqlite3.Connection, entity_type: str, entity_id: str, action: str, details: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO audit_log (id, entity_type, entity_id, action, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (new_id(), entity_type, entity_id, action, json_dumps(details), now_iso()),
    )


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


def make_code(prefix: str, name: str) -> str:
    stem = "".join(ch for ch in text(name).upper() if ch.isalnum())[:4] or prefix
    return f"{stem}-{new_id()[:4].upper()}"


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
        priority=int(data.get("priority") or 100),
    )
    record_audit(conn, "rate_rule", rule_id, "created_inline", data)
    conn.commit()
    return dict(conn.execute("SELECT * FROM rate_rules WHERE rate_rule_id = ?", (rule_id,)).fetchone())
