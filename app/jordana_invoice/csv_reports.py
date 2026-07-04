from __future__ import annotations

import csv
import io
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .appointment_ledger import (
    APPOINTMENT_LEDGER_COLUMNS,
    build_appointment_ledger_csv_rows,
)
from .rates import cents_to_dollars
from .util import csv_safe, normalize_payment_status, text


SESSION_COLUMNS = [
    "session_date",
    "start_time",
    "raw_calendar_title",
    "classification",
    "confidence",
    "candidate_person_names",
    "candidate_account_code",
    "candidate_account_name",
    "participant_names",
    "billing_party_name",
    "duration_minutes",
    "calendar_duration_minutes",
    "service_mode",
    "rate_group",
    "time_category",
    "is_evening",
    "is_weekend",
    "suggested_rate",
    "approved_rate",
    "rate_source",
    "payment_status",
    "appointment_status",
    "review_status",
    "review_reasons",
    "invoice_number",
]

SUMMARY_COLUMNS = [
    "account_code",
    "account_name",
    "participant_names",
    "session_count",
    "billed_amount",
    "paid_at_session_amount",
    "unpaid_amount",
    "last_session_date",
]


def write_reports(
    conn: sqlite3.Connection,
    reports_dir: str | Path | None = None,
    year: int | None = None,
) -> tuple[Path, Path, Path, Path]:
    target_dir = resolve_reports_dir(reports_dir)
    report_year = current_eastern_year() if year is None else int(year)
    target_dir.mkdir(parents=True, exist_ok=True)
    session_path = target_dir / f"Jordana_Client_Sessions_{report_year}.csv"
    summary_path = target_dir / f"Jordana_Client_Summary_{report_year}.csv"
    simple_path = target_dir / f"Jordana_Session_Log_{report_year}.csv"
    appointment_path = target_dir / "Jordana_All_Appointments.csv"

    session_rows = build_session_rows(conn, report_year)
    summary_rows = build_summary_rows(session_rows)
    appointment_rows = build_appointment_ledger_csv_rows(conn)

    atomic_write_csv(session_path, SESSION_COLUMNS, session_rows)
    atomic_write_csv(summary_path, SUMMARY_COLUMNS, summary_rows)
    atomic_write_csv(simple_path, SIMPLE_COLUMNS, build_simple_rows(conn, session_rows))
    atomic_write_csv(appointment_path, APPOINTMENT_LEDGER_COLUMNS, appointment_rows)
    return session_path, summary_path, simple_path, appointment_path


def resolve_reports_dir(reports_dir: str | Path | None = None) -> Path:
    if reports_dir is not None:
        return Path(reports_dir)
    return Path(os.environ.get("JORDANA_REPORTS_DIR", "Reports"))


def refresh_reports_after_commit(conn: sqlite3.Connection) -> str | None:
    try:
        write_reports(conn)
    except Exception:
        return "Report generation warning: reports could not be refreshed. They can be regenerated later."
    return None


def current_eastern_year(now: datetime | None = None) -> int:
    eastern = ZoneInfo("America/New_York")
    moment = now or datetime.now(eastern)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=eastern)
    return moment.astimezone(eastern).year


SIMPLE_COLUMNS = [
    "Date",
    "Participants",
    "Bill To",
    "Duration",
    "Time Category",
    "Outcome",
    "Rate",
    "Review Status",
    "Invoice Status",
    "Payment Status",
]

REPORT_EXCLUDED_REVIEW_STATUSES = (
    "excluded",
    "needs_review",
    "needs_classification",
    "needs_person_match",
    "needs_rate",
    "needs_billing_treatment",
)


def build_session_rows(conn: sqlite3.Connection, year: int) -> list[dict[str, object]]:
    placeholders = ", ".join("?" for _ in REPORT_EXCLUDED_REVIEW_STATUSES)
    rows = conn.execute(
        f"""
        SELECT
          s.id,
          s.session_date,
          s.start_at,
          s.duration_minutes,
          s.calendar_duration_minutes,
          s.service_mode,
          s.rate_group,
          s.time_category,
          s.is_evening,
          s.is_weekend,
          s.suggested_rate_cents,
          s.approved_rate_cents,
          s.approved_rate_source,
          s.rate_source,
          s.payment_status,
          s.appointment_status,
          s.billing_treatment,
          s.review_status,
          s.raw_calendar_title,
          s.billing_session_type,
          s.custom_service_description,
          c.classification,
          c.confidence,
          c.candidate_person_names,
          c.candidate_account_code,
          c.candidate_account_name,
          c.review_reasons,
          a.account_code,
          a.account_name,
          b.billing_name
        FROM sessions s
        JOIN calendar_event_candidates c ON c.id = s.candidate_id
        LEFT JOIN client_accounts a ON a.account_id = s.account_id
        LEFT JOIN billing_parties b ON b.billing_party_id = s.billing_party_id
        WHERE substr(s.start_at, 1, 4) = ?
          AND COALESCE(c.classification, '') = 'client_session'
          AND COALESCE(s.review_status, '') NOT IN ({placeholders})
          AND COALESCE(s.billable_status, '') NOT IN ('excluded', 'nonbillable')
        ORDER BY s.start_at, s.proposed_client_name, c.title
        """,
        (str(year), *REPORT_EXCLUDED_REVIEW_STATUSES),
    ).fetchall()

    output: list[dict[str, object]] = []
    for row in rows:
        participants = participant_names(conn, row["id"])
        account_name = row["account_name"] or row["candidate_account_name"] or ""
        account_code = row["account_code"] or row["candidate_account_code"] or make_account_code(account_name or participants)
        output.append(
            {
                "id": row["id"],
                "session_date": row["session_date"] or (row["start_at"] or "")[:10],
                "start_time": start_time(row["start_at"]),
                "raw_calendar_title": row["raw_calendar_title"] or "",
                "classification": row["classification"] or "",
                "confidence": row["confidence"] or "",
                "candidate_person_names": jsonish_to_list_text(row["candidate_person_names"]),
                "candidate_account_code": account_code,
                "candidate_account_name": account_name,
                "participant_names": participants,
                "billing_party_name": row["billing_name"] or "",
                "duration_minutes": row["duration_minutes"] or "",
                "calendar_duration_minutes": row["calendar_duration_minutes"] or "",
                "service_mode": row["service_mode"] or "unknown",
                "rate_group": row["rate_group"] or "",
                "time_category": row["time_category"] or "standard",
                "is_evening": "yes" if row["is_evening"] else "no",
                "is_weekend": "yes" if row["is_weekend"] else "no",
                "suggested_rate": cents_to_dollars(row["suggested_rate_cents"]),
                "approved_rate": cents_to_dollars(row["approved_rate_cents"]),
                "rate_source": row["approved_rate_source"] or row["rate_source"] or "",
                "payment_status": normalize_payment_status(row["payment_status"]),
                "appointment_status": row["appointment_status"] or "unresolved",
                "billing_treatment": row["billing_treatment"] or "unresolved",
                "review_status": row["review_status"] or "needs_review",
                "review_reasons": jsonish_to_list_text(row["review_reasons"]),
                "invoice_number": "",
            }
        )
    return output


def build_summary_rows(
    session_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    for row in session_rows:
        code = str(row["candidate_account_code"] or "UNRESOLVED")
        if code not in grouped:
            grouped[code] = {
                "account_code": code,
                "account_name": row["candidate_account_name"],
                "participant_names": row["participant_names"],
                "session_count": 0,
                "billed_amount": "0.00",
                "paid_at_session_amount": "0.00",
                "unpaid_amount": "0.00",
                "last_session_date": "",
            }
        grouped[code]["session_count"] = int(grouped[code]["session_count"]) + 1
        grouped[code]["last_session_date"] = max(
            str(grouped[code]["last_session_date"]),
            str(row["session_date"]),
        )
        approved_rate = money_to_float(row["approved_rate"])
        if approved_rate:
            grouped[code]["billed_amount"] = f"{money_to_float(grouped[code]['billed_amount']) + approved_rate:.2f}"
            ps = normalize_payment_status(row["payment_status"])
            if ps == "paid_at_session":
                grouped[code]["paid_at_session_amount"] = f"{money_to_float(grouped[code]['paid_at_session_amount']) + approved_rate:.2f}"
            else:
                grouped[code]["unpaid_amount"] = f"{money_to_float(grouped[code]['unpaid_amount']) + approved_rate:.2f}"
    return [grouped[key] for key in sorted(grouped)]


def build_simple_rows(conn: sqlite3.Connection, session_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    rows = []
    for row in session_rows:
        ledger = session_ledger_status(conn, text(row.get("id")), money_to_cents(row.get("approved_rate") or row.get("suggested_rate")))
        rows.append(
            {
                "Date": row["session_date"],
                "Participants": row["participant_names"] or row["candidate_person_names"],
                "Bill To": row["billing_party_name"],
                "Duration": duration_label(row["duration_minutes"]),
                "Time Category": time_category_label(row["time_category"]),
                "Outcome": outcome_label(row["appointment_status"], row["billing_treatment"]),
                "Rate": row["approved_rate"] or row["suggested_rate"],
                "Review Status": review_status_label(row["review_status"]),
                "Invoice Status": ledger["invoice_status"],
                "Payment Status": ledger["payment_status"],
            }
        )
    return rows


def session_ledger_status(conn: sqlite3.Connection, session_id: str, rate_cents: int) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT i.invoice_id, i.status, li.line_amount_cents, li.invoice_line_item_id
        FROM invoice_line_items li
        JOIN invoices i ON i.invoice_id = li.invoice_id
        WHERE li.source_session_id = ?
        ORDER BY
          CASE i.status WHEN 'draft' THEN 0 WHEN 'finalized' THEN 1 WHEN 'void' THEN 2 ELSE 3 END,
          i.updated_at DESC,
          i.created_at DESC
        """,
        (session_id,),
    ).fetchall()
    active = next((row for row in rows if row["status"] != "void"), None)
    if active is None:
        if rows:
            return {"invoice_status": "Voided", "payment_status": "Not Invoiced"}
        return {"invoice_status": "Not Invoiced", "payment_status": "Waived" if rate_cents == 0 else "Not Invoiced"}
    invoice_status = {"draft": "Draft", "finalized": "Finalized"}.get(active["status"], text(active["status"]).title())
    line_cents = int(active["line_amount_cents"] or 0)
    if line_cents == 0:
        payment_status = "Waived"
    elif active["status"] != "finalized":
        payment_status = "Unpaid"
    else:
        paid_cents = conn.execute(
            """
            SELECT COALESCE(SUM(pa.amount_cents), 0)
            FROM payment_allocations pa
            JOIN payments p ON p.payment_id = pa.payment_id
            WHERE pa.invoice_line_item_id = ?
              AND pa.status = 'active'
              AND p.status = 'posted'
            """,
            (active["invoice_line_item_id"],),
        ).fetchone()[0]
        if paid_cents <= 0:
            payment_status = "Unpaid"
        elif paid_cents >= line_cents:
            payment_status = "Paid"
        else:
            payment_status = "Partially Paid"
    return {"invoice_status": invoice_status, "payment_status": payment_status}


def outcome_label(appointment_status: object, billing_treatment: object) -> str:
    status = text(appointment_status).lower()
    treatment = text(billing_treatment).lower()
    if status in {"scheduled", "completed"}:
        return "Completed"
    if status == "late_cancellation":
        return {
            "bill_full_fee": "Late Cancellation — Full Fee",
            "billable": "Late Cancellation — Full Fee",
            "custom_fee": "Late Cancellation — Custom Fee",
            "waived": "Late Cancellation — Waived",
            "not_billable": "Late Cancellation — Waived",
        }.get(treatment, "—")
    if status in {"cancelled", "timely_cancellation"}:
        return "Cancelled — Not Billable"
    if status == "no_show":
        return "No Show"
    return "—"


def review_status_label(value: object) -> str:
    status = text(value)
    if status == "approved":
        return "Approved"
    if status in {"ready_for_approval", "reviewed"}:
        return "Reviewed"
    return "Needs Review"


def time_category_label(value: object) -> str:
    raw = text(value) or "standard"
    return {
        "standard": "Standard",
        "evening": "Evening",
        "weekend": "Weekend",
        "weekend_evening": "Weekend",
    }.get(raw, raw.replace("_", " ").title())


def duration_label(value: object) -> str:
    raw = text(value)
    return f"{raw} min" if raw else ""


def money_to_cents(value: object) -> int:
    try:
        return int(round(float(text(value) or "0") * 100))
    except ValueError:
        return 0


def participant_names(conn: sqlite3.Connection, session_id: str) -> str:
    rows = conn.execute(
        """
        SELECT COALESCE(p.display_name, sp.participant_name) AS display_name
        FROM session_participants sp
        LEFT JOIN people p ON p.person_id = sp.person_id
        WHERE sp.session_id = ?
        ORDER BY sp.is_primary DESC, display_name
        """,
        (session_id,),
    ).fetchall()
    return "; ".join(text(row["display_name"]) for row in rows if text(row["display_name"]))


def start_time(value: str | None) -> str:
    if not value or "T" not in value:
        return ""
    return value.split("T", 1)[1][:5]


def jsonish_to_list_text(value: object) -> str:
    raw = text(value)
    if not raw:
        return ""
    if raw.startswith("[") and raw.endswith("]"):
        return raw.strip("[]").replace('"', "").replace(",", "; ")
    return raw


def make_account_code(account_name: str) -> str:
    parts = [part for part in account_name.upper().split() if part]
    if not parts:
        return "UNRESOLVED"
    if len(parts) == 1:
        return parts[0][:8]
    return (parts[0][0] + parts[-1])[:12]


def money_to_float(value: object) -> float:
    try:
        return float(text(value) or "0")
    except ValueError:
        return 0.0


def atomic_write_csv(
    path: Path,
    columns: list[str],
    rows: list[dict[str, object]],
) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: csv_safe(row.get(column, "")) for column in columns})

    validate_csv(tmp_path, columns)
    os.replace(tmp_path, path)


def validate_csv(path: Path, columns: list[str]) -> None:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader)
    if header != columns:
        raise ValueError(f"Invalid report header for {path}")


# ---------------------------------------------------------------------------
# On-demand report API support
# ---------------------------------------------------------------------------

_VALID_REPORT_TYPES = ("sessions", "summary", "simple", "appointments")

_REPORT_TYPE_METADATA: list[dict[str, object]] = [
    {
        "type": "sessions",
        "display_name": "Client Sessions",
        "description": "Detailed session-level export with classification, rates, and review status.",
        "year_required": True,
    },
    {
        "type": "summary",
        "display_name": "Client Summary",
        "description": "Account-level summary with session counts, billed totals, and paid-at-session vs unpaid amounts. Finalized invoice totals are separate from session payment status.",
        "year_required": True,
    },
    {
        "type": "simple",
        "display_name": "Session Log",
        "description": "Simplified human-readable session log for quick review.",
        "year_required": True,
    },
    {
        "type": "appointments",
        "display_name": "All Appointments",
        "description": "Full appointment ledger across all calendars and review statuses.",
        "year_required": True,
    },
]


def available_report_types() -> list[dict[str, object]]:
    return [dict(entry) for entry in _REPORT_TYPE_METADATA]


def available_years(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute(
        """
        SELECT DISTINCT substr(start_at, 1, 4) AS year
        FROM (
            SELECT start_at FROM sessions
            WHERE start_at IS NOT NULL AND start_at != ''
            UNION
            SELECT start_at FROM calendar_event_candidates
            WHERE start_at IS NOT NULL AND start_at != ''
        )
        WHERE year GLOB '[0-9][0-9][0-9][0-9]'
        ORDER BY year DESC
        """,
    ).fetchall()
    return [int(row["year"]) for row in rows]


def default_report_year(conn: sqlite3.Connection) -> int:
    eastern = ZoneInfo("America/New_York")
    current_year = datetime.now(eastern).year
    years = available_years(conn)
    if current_year in years:
        return current_year
    if years:
        return years[0]
    return current_year


def validate_report_type(report_type: str) -> None:
    if report_type not in _VALID_REPORT_TYPES:
        raise ValueError(
            f"Unknown report type: {report_type!r}. "
            f"Expected one of: {', '.join(_VALID_REPORT_TYPES)}"
        )


def validate_year(year: int) -> None:
    if not isinstance(year, int) or isinstance(year, bool):
        raise ValueError(f"Year must be an integer, got {type(year).__name__}")
    if year < 2000 or year > 2100:
        raise ValueError(f"Year out of range: {year}")


def generate_report_csv(
    conn: sqlite3.Connection,
    report_type: str,
    year: int,
) -> str:
    validate_report_type(report_type)
    validate_year(year)

    if report_type == "sessions":
        rows = build_session_rows(conn, year)
        columns = SESSION_COLUMNS
    elif report_type == "summary":
        session_rows = build_session_rows(conn, year)
        rows = build_summary_rows(session_rows)
        columns = SUMMARY_COLUMNS
    elif report_type == "simple":
        session_rows = build_session_rows(conn, year)
        rows = build_simple_rows(conn, session_rows)
        columns = SIMPLE_COLUMNS
    else:
        all_rows = build_appointment_ledger_csv_rows(conn)
        rows = [
            row for row in all_rows
            if str(row.get("Date", ""))[:4] == str(year)
        ]
        columns = APPOINTMENT_LEDGER_COLUMNS

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({col: csv_safe(row.get(col, "")) for col in columns})
    return buf.getvalue()


def report_filename(report_type: str, year: int) -> str:
    validate_report_type(report_type)
    if report_type == "sessions":
        return f"Jordana_Client_Sessions_{year}.csv"
    if report_type == "summary":
        return f"Jordana_Client_Summary_{year}.csv"
    if report_type == "simple":
        return f"Jordana_Session_Log_{year}.csv"
    return "Jordana_All_Appointments.csv"
