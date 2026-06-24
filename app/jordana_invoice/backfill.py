from __future__ import annotations

import json
import sqlite3

from .parser import parse_event
from .rates import suggest_rate
from .review import review_status_for_parse, unresolved_fields_for_session
from .util import json_dumps, new_id, now_iso, text


def backfill_phase2(conn: sqlite3.Connection) -> int:
    updated = 0
    candidates = conn.execute(
        """
        SELECT c.*, r.event_title, r.start_at AS raw_start_at, r.end_at AS raw_end_at,
               r.duration_minutes AS raw_duration_minutes
        FROM calendar_event_candidates c
        JOIN raw_calendar_snapshots r ON r.id = c.latest_raw_snapshot_id
        WHERE c.confidence_label IS NULL
           OR c.unresolved_fields IS NULL
           OR c.service_mode IS NULL
        """
    ).fetchall()
    for candidate in candidates:
        result = parse_event(
            {
                "event_title": candidate["event_title"] or candidate["title"],
                "start_at": candidate["raw_start_at"] or candidate["start_at"],
                "end_at": candidate["raw_end_at"] or candidate["end_at"],
                "duration_minutes": candidate["raw_duration_minutes"] or candidate["calendar_duration_minutes"],
            }
        )
        review_status = review_status_for_parse(result)
        now = now_iso()
        conn.execute(
            """
            UPDATE calendar_event_candidates
            SET confidence_label = ?,
                unresolved_fields = ?,
                review_reasons = ?,
                candidate_person_names = ?,
                possible_referenced_person = ?,
                service_mode = ?,
                rate_group = ?,
                time_category = ?,
                is_evening = ?,
                is_weekend = ?,
                parser_payload = ?,
                review_status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                result.confidence_label,
                json_dumps(result.unresolved_fields),
                json_dumps(result.review_reasons),
                json_dumps(result.candidate_person_names),
                result.possible_referenced_person,
                result.service_mode,
                result.rate_group,
                result.time_category,
                1 if result.is_evening else 0,
                1 if result.is_weekend else 0,
                json_dumps(result.as_dict()),
                review_status,
                now,
                candidate["id"],
            ),
        )
        updated += 1

    sessions = conn.execute(
        """
        SELECT s.*, c.parser_payload, c.review_reasons, c.candidate_person_names
        FROM sessions s
        JOIN calendar_event_candidates c ON c.id = s.candidate_id
        WHERE s.service_mode IS NULL
           OR s.session_date IS NULL
           OR s.raw_calendar_title IS NULL
        """
    ).fetchall()
    for session in sessions:
        payload = parse_payload(session["parser_payload"])
        session_date = (session["start_at"] or "")[:10]
        rate = suggest_rate(
            conn,
            session_date=session_date,
            duration_minutes=session["duration_minutes"],
            appointment_status=session["appointment_status"],
            service_mode=payload.get("service_mode") or "unknown",
            rate_group=payload.get("rate_group"),
            time_category=payload.get("time_category") or "standard",
        )
        unresolved_fields = sorted(set(payload.get("unresolved_fields") or []) | {"payment_status"} | ({"rate"} if rate.rate_needs_review else set()))
        review_status = "needs_rate" if rate.rate_needs_review else "ready_for_approval"
        now = now_iso()
        conn.execute(
            """
            UPDATE sessions
            SET source_event_candidate_id = candidate_id,
                session_date = ?,
                calendar_duration_minutes = ?,
                parsed_duration_minutes = ?,
                service_mode = ?,
                rate_group = ?,
                time_category = ?,
                is_evening = ?,
                is_weekend = ?,
                suggested_rate_cents = ?,
                rate_rule_id = ?,
                rate_source = ?,
                rate_needs_review = ?,
                payment_status = COALESCE(payment_status, 'unresolved'),
                raw_calendar_title = ?,
                review_status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                session_date,
                payload.get("calendar_duration_minutes"),
                session["duration_minutes"],
                payload.get("service_mode") or "unknown",
                payload.get("rate_group"),
                payload.get("time_category") or "standard",
                1 if payload.get("is_evening") else 0,
                1 if payload.get("is_weekend") else 0,
                rate.suggested_rate_cents,
                rate.rate_rule_id,
                rate.rate_source,
                1 if rate.rate_needs_review else 0,
                session["raw_calendar_title"] or session["proposed_client_name"] or "",
                review_status,
                now,
                session["id"],
            ),
        )
        ensure_participants(conn, session["id"], payload)
        ensure_review_item(conn, session["candidate_id"], session["id"], review_status, unresolved_fields, payload.get("review_reasons") or [])
        updated += 1
    return updated


def parse_payload(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def ensure_participants(
    conn: sqlite3.Connection,
    session_id: str,
    payload: dict[str, object],
) -> None:
    existing = conn.execute(
        "SELECT 1 FROM session_participants WHERE session_id = ? LIMIT 1",
        (session_id,),
    ).fetchone()
    if existing:
        return
    names = payload.get("candidate_person_names")
    if not isinstance(names, list) or not names:
        proposed = text(payload.get("proposed_client_name"))
        names = [proposed] if proposed else []
    now = now_iso()
    for index, name in enumerate(names):
        conn.execute(
            """
            INSERT INTO session_participants (
              session_participant_id, session_id, participant_name,
              participant_role, is_primary, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id(),
                session_id,
                text(name),
                "primary" if index == 0 else "participant",
                1 if index == 0 else 0,
                now,
                now,
            ),
        )


def ensure_review_item(
    conn: sqlite3.Connection,
    candidate_id: str,
    session_id: str,
    review_status: str,
    unresolved_fields: list[str],
    review_reasons: list[str],
) -> None:
    existing = conn.execute(
        "SELECT 1 FROM review_items WHERE session_id = ? LIMIT 1",
        (session_id,),
    ).fetchone()
    if existing:
        return
    now = now_iso()
    conn.execute(
        """
        INSERT INTO review_items (
          review_item_id, candidate_id, session_id, review_status,
          unresolved_fields, review_reasons, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id(),
            candidate_id,
            session_id,
            review_status,
            json_dumps(unresolved_fields),
            json_dumps(review_reasons),
            now,
            now,
        ),
    )
