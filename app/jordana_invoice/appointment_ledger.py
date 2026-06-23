from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from .rates import cents_to_dollars
from .util import text


APPOINTMENT_LEDGER_COLUMNS = [
    "Date",
    "Time",
    "Calendar Title",
    "Client / Participants",
    "Session Length",
    "Session Type",
    "Rate",
    "Payment Status",
    "Review Status",
    "Appointment Status",
    "Classification",
    "Calendar",
]


@dataclass(frozen=True)
class LedgerFilters:
    date_range: str = "all"
    review_status: str = ""
    payment_status: str = ""
    limit: int = 30
    offset: int = 0
    today: date | None = None


def build_appointment_ledger_csv_rows(
    conn: sqlite3.Connection,
    *,
    today: date | None = None,
) -> list[dict[str, object]]:
    rows = list_appointment_ledger_rows(
        conn,
        LedgerFilters(date_range="all", review_status="", payment_status="", limit=1000000, offset=0, today=today),
    )
    return [row_to_csv(row) for row in rows]


def list_appointment_ledger_page(
    conn: sqlite3.Connection,
    *,
    date_range: str = "rolling_30",
    review_status: str = "",
    payment_status: str = "",
    limit: int = 30,
    offset: int = 0,
    today: date | None = None,
) -> dict[str, Any]:
    filters = LedgerFilters(
        date_range=date_range,
        review_status=review_status,
        payment_status=payment_status,
        limit=limit,
        offset=offset,
        today=today,
    )
    total = count_appointment_ledger_rows(conn, filters)
    items = list_appointment_ledger_rows(conn, filters)
    return {"total": total, "items": items}


def list_appointment_ledger_rows(
    conn: sqlite3.Connection,
    filters: LedgerFilters,
) -> list[dict[str, Any]]:
    where_sql, params = ledger_where(filters)
    rows = conn.execute(
        f"""
        {ledger_participant_cte()}
        SELECT
          c.id AS candidate_id,
          s.id AS session_id,
          COALESCE(s.session_date, substr(COALESCE(s.start_at, c.start_at), 1, 10)) AS appointment_date,
          COALESCE(s.start_at, c.start_at) AS appointment_start,
          COALESCE(s.raw_calendar_title, c.title, r.event_title, '') AS calendar_title,
          COALESCE(s.approved_duration_minutes, s.duration_minutes, c.proposed_duration_minutes, c.calendar_duration_minutes) AS session_length,
          s.billing_session_type,
          s.custom_service_description,
          COALESCE(s.service_mode, c.service_mode, '') AS service_mode,
          s.approved_rate_cents,
          s.suggested_rate_cents,
          {payment_status_sql()} AS payment_status,
          COALESCE(s.review_status, c.review_status, 'needs_review') AS review_status,
          COALESCE(s.appointment_status, c.appointment_status, 'unresolved') AS appointment_status,
          c.classification,
          COALESCE(s.calendar_name, c.calendar_name, r.calendar_name, '') AS calendar_name,
          pa.participant_names,
          c.candidate_person_names,
          c.possible_referenced_person
        FROM calendar_event_candidates c
        LEFT JOIN sessions s ON s.candidate_id = c.id
        LEFT JOIN raw_calendar_snapshots r ON r.id = c.latest_raw_snapshot_id
        LEFT JOIN participant_agg pa ON pa.session_id = s.id
        {where_sql}
        ORDER BY COALESCE(s.start_at, c.start_at) DESC, c.updated_at DESC, c.id DESC
        LIMIT ? OFFSET ?
        """,
        (*params, filters.limit, filters.offset),
    ).fetchall()
    return [ledger_row_to_dict(row) for row in rows]


def count_appointment_ledger_rows(
    conn: sqlite3.Connection,
    filters: LedgerFilters,
) -> int:
    where_sql, params = ledger_where(filters)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM calendar_event_candidates c
        LEFT JOIN sessions s ON s.candidate_id = c.id
        {where_sql}
        """,
        params,
    ).fetchone()
    return int(row["count"])


def ledger_participant_cte() -> str:
    return """
        WITH participant_agg AS (
          SELECT
            ordered.session_id,
            group_concat(ordered.display_name, '; ') AS participant_names
          FROM (
            SELECT
              sp.session_id,
              COALESCE(p.display_name, sp.participant_name) AS display_name,
              sp.is_primary
            FROM session_participants sp
            LEFT JOIN people p ON p.person_id = sp.person_id
            WHERE COALESCE(p.display_name, sp.participant_name) IS NOT NULL
              AND COALESCE(p.display_name, sp.participant_name) != ''
            ORDER BY sp.session_id, sp.is_primary DESC, display_name
          ) ordered
          GROUP BY ordered.session_id
        )
    """


def ledger_where(filters: LedgerFilters) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    appointment_date_sql = "date(COALESCE(s.start_at, c.start_at))"

    date_start, date_end = date_bounds(filters.date_range, filters.today)
    if date_start:
        clauses.append(f"{appointment_date_sql} >= date(?)")
        params.append(date_start.isoformat())
    if date_end:
        clauses.append(f"{appointment_date_sql} < date(?)")
        params.append(date_end.isoformat())
    if filters.review_status:
        clauses.append("COALESCE(s.review_status, c.review_status, 'needs_review') = ?")
        params.append(filters.review_status)
    if filters.payment_status:
        clauses.append(f"{payment_status_sql()} = ?")
        params.append(filters.payment_status)

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where_sql, params


def date_bounds(date_range: str, today: date | None) -> tuple[date | None, date | None]:
    today = today or date.today()
    if date_range == "rolling_30":
        return today - timedelta(days=29), today + timedelta(days=1)
    if date_range == "this_month":
        start = today.replace(day=1)
        return start, next_month(start)
    if date_range == "previous_month":
        this_month = today.replace(day=1)
        previous_end = this_month
        previous_start = (this_month - timedelta(days=1)).replace(day=1)
        return previous_start, previous_end
    if date_range == "all":
        return None, None
    raise ValueError(f"Unsupported date range: {date_range}")


def next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def payment_status_sql() -> str:
    return """
        CASE
          WHEN s.id IS NOT NULL THEN COALESCE(NULLIF(s.payment_status, ''), 'unresolved')
          WHEN c.classification IN ('personal', 'administrative', 'nonbillable', 'cancelled', 'no_show') THEN 'not_billable'
          ELSE 'unresolved'
        END
    """


def ledger_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "candidate_id": row["candidate_id"],
        "session_id": row["session_id"],
        "date": row["appointment_date"] or "",
        "time": start_time(row["appointment_start"]),
        "calendar_title": row["calendar_title"] or "",
        "client_participants": client_participants_text(row),
        "session_length": row["session_length"] or "",
        "session_type": session_type_text(
            row["billing_session_type"],
            row["service_mode"],
            row["custom_service_description"],
        ),
        "rate": cents_to_dollars(first_rate_cents(row)),
        "payment_status": row["payment_status"] or "unresolved",
        "review_status": row["review_status"] or "needs_review",
        "appointment_status": row["appointment_status"] or "unresolved",
        "classification": row["classification"] or "",
        "calendar": row["calendar_name"] or "",
    }


def row_to_csv(row: dict[str, Any]) -> dict[str, object]:
    return {
        "Date": row["date"],
        "Time": row["time"],
        "Calendar Title": row["calendar_title"],
        "Client / Participants": row["client_participants"],
        "Session Length": row["session_length"],
        "Session Type": row["session_type"],
        "Rate": row["rate"],
        "Payment Status": row["payment_status"],
        "Review Status": row["review_status"],
        "Appointment Status": row["appointment_status"],
        "Classification": row["classification"],
        "Calendar": row["calendar"],
    }


def client_participants_text(row: sqlite3.Row) -> str:
    if text(row["participant_names"]):
        return text(row["participant_names"])
    candidate_names = json_list_to_text(row["candidate_person_names"])
    if candidate_names:
        return candidate_names
    if text(row["possible_referenced_person"]):
        return text(row["possible_referenced_person"])
    return ""


def json_list_to_text(value: object) -> str:
    raw = text(value)
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(parsed, list):
        return "; ".join(text(item) for item in parsed if text(item))
    return raw


def first_rate_cents(row: sqlite3.Row) -> int | None:
    return row["approved_rate_cents"] or row["suggested_rate_cents"]


def session_type_text(
    billing_session_type: object,
    service_mode: object,
    custom_service_description: object = None,
) -> str:
    billing = text(billing_session_type)
    if billing:
        if billing == "custom" and text(custom_service_description):
            return text(custom_service_description)
        return {
            "psychotherapy": "Psychotherapy Session",
            "psychotherapy_house_call": "Psychotherapy Session / House Call",
            "psychotherapy_weekend": "Psychotherapy Session / Weekend",
            "psychotherapy_evening": "Psychotherapy Session / Evening",
            "custom": "Custom",
        }.get(billing, billing)
    service = text(service_mode)
    if not service:
        return ""
    return {
        "office": "Office",
        "phone": "Phone",
        "facetime": "FaceTime",
        "house_call": "House Call",
        "unknown": "Unknown",
    }.get(service, service)


def start_time(value: str | None) -> str:
    if not value or "T" not in value:
        return ""
    return value.split("T", 1)[1][:5]
