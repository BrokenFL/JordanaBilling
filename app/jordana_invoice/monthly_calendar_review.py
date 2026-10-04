"""Read-only comparison of a phone Calendar CSV with approved session records."""
from __future__ import annotations

import csv
import io
import math
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from .calendar_identity import calendar_text, canonical_datetime, utc_datetime
from .csv_reports import outcome_label, participant_names, session_ledger_status
from .rates import cents_to_dollars
from .request_validation import RequestValidationError
from .util import csv_safe

EASTERN = ZoneInfo("America/New_York")
CALENDAR_COLUMNS = ["Month", "Exported At", "Calendar", "Title", "Start", "End", "Duration Minutes", "All Day"]
MAX_CSV_BYTES = 800_000
MAX_EVENTS = 5_000


def validate_month(month: object) -> str:
    if not isinstance(month, str) or not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", month):
        raise RequestValidationError("Choose a month between January 2000 and December 2099.")
    return month


def _csv_error(row: int, message: str) -> RequestValidationError:
    # Never include a calendar title or uploaded text in an error/diagnostic.
    return RequestValidationError(f"Calendar CSV record {row}: {message}")


def parse_calendar_csv(csv_text: object, month: str) -> tuple[list[dict], dict]:
    validate_month(month)
    if not isinstance(csv_text, str):
        raise RequestValidationError("Choose the CSV created by Jordana Monthly Calendar Export.")
    if len(csv_text.encode("utf-8")) > MAX_CSV_BYTES:
        raise RequestValidationError("Calendar CSV is too large. Export one month at a time.")
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")), strict=True)
    try:
        if reader.fieldnames != CALENDAR_COLUMNS:
            raise RequestValidationError("Use the CSV created by Jordana Monthly Calendar Export. Its column headers must be unchanged.")
        events, excluded_all_day, outside_month = [], 0, 0
        exported_at = set()
        for number, row in enumerate(reader, 1):
            if number > MAX_EVENTS:
                raise RequestValidationError("Calendar CSV contains too many records. Export one month at a time.")
            if None in row or any(value is None for value in row.values()):
                raise _csv_error(number, "the number of columns does not match the header.")
            if row["Month"] != month:
                raise _csv_error(number, "the export month does not match the selected month.")
            captured = utc_datetime(row["Exported At"])
            if captured is None:
                raise _csv_error(number, "the export timestamp must include a time zone.")
            exported_at.add(captured.isoformat())
            flag = row["All Day"].strip().casefold()
            if flag not in {"true", "false", "yes", "no", "1", "0"}:
                raise _csv_error(number, "All Day must be true or false.")
            if flag in {"true", "yes", "1"}:
                excluded_all_day += 1
                continue
            start, end = utc_datetime(row["Start"]), utc_datetime(row["End"])
            if start is None or end is None or end < start:
                raise _csv_error(number, "Start and End must be valid timestamps with time zones and End must not precede Start.")
            minutes = (end - start).total_seconds() / 60
            try:
                declared = float(row["Duration Minutes"])
            except ValueError:
                raise _csv_error(number, "Duration Minutes must be a number.") from None
            if not math.isfinite(declared) or abs(declared - minutes) > 0.02:
                raise _csv_error(number, "Duration Minutes does not agree with Start and End.")
            if start.astimezone(EASTERN).strftime("%Y-%m") != month:
                outside_month += 1
                continue
            events.append({
                "record_number": number, "title": row["Title"], "calendar": row["Calendar"],
                "start_at": row["Start"], "end_at": row["End"], "duration_minutes": round(minutes, 4),
                "date": start.astimezone(EASTERN).date().isoformat(),
                "start_time": start.astimezone(EASTERN).strftime("%I:%M %p").lstrip("0"),
            })
        if len(exported_at) > 1:
            raise RequestValidationError("Calendar CSV combines more than one export. Choose one monthly export file.")
    except csv.Error:
        raise RequestValidationError("Calendar CSV could not be read. Export the month again without editing the file.") from None
    return events, {"exported_at": next(iter(exported_at), ""), "excluded_all_day": excluded_all_day, "outside_month": outside_month}


def _approved_sessions(conn: sqlite3.Connection, month: str) -> list[dict]:
    # Approval is authoritative even when the imported candidate was reclassified.
    rows = conn.execute("""
        SELECT s.id, s.start_at, s.end_at, s.session_date, s.raw_calendar_title,
               s.calendar_name, s.duration_minutes, s.approved_rate_cents,
               s.appointment_status, s.billing_treatment, b.billing_name
        FROM sessions s LEFT JOIN billing_parties b ON b.billing_party_id = s.billing_party_id
        WHERE s.review_status = 'approved'
          AND COALESCE(s.billable_status, '') NOT IN ('excluded', 'nonbillable')
        ORDER BY s.start_at, s.id
    """).fetchall()
    sessions = []
    for row in rows:
        start = utc_datetime(row["start_at"])
        service_date = start.astimezone(EASTERN).date().isoformat() if start else (row["session_date"] or "")
        if not service_date.startswith(month + "-"):
            continue
        ledger = session_ledger_status(conn, row["id"], row["approved_rate_cents"] or 0)
        sessions.append({
            "id": row["id"], "title": row["raw_calendar_title"] or "", "calendar": row["calendar_name"] or "",
            "date": service_date, "start_at": row["start_at"] or "", "end_at": row["end_at"] or "",
            "start_time": start.astimezone(EASTERN).strftime("%I:%M %p").lstrip("0") if start else "Unknown",
            "duration_minutes": row["duration_minutes"], "participants": participant_names(conn, row["id"]),
            "bill_to": row["billing_name"] or "", "rate": cents_to_dollars(row["approved_rate_cents"]),
            "outcome": outcome_label(row["appointment_status"], row["billing_treatment"]),
            "invoice_status": ledger["invoice_status"], "payment_status": ledger["payment_status"],
        })
    return sessions


def _key(item: dict, mode: str) -> tuple | None:
    start = utc_datetime(item["start_at"])
    if start is None:
        return None
    calendar = calendar_text(item["calendar"])
    title = calendar_text(item["title"])
    if mode == "exact":
        return calendar, canonical_datetime(item["start_at"]), title
    if mode == "title_day":
        return (calendar, title, item["date"]) if title else None
    return calendar, canonical_datetime(item["start_at"])


def _differences(event: dict, session: dict) -> list[str]:
    result = []
    if calendar_text(event["title"]) != calendar_text(session["title"]):
        result.append("Calendar title differs from the approved record")
    if canonical_datetime(event["start_at"]) != canonical_datetime(session["start_at"]):
        result.append("Start time differs")
    if canonical_datetime(event["end_at"]) != canonical_datetime(session["end_at"]):
        result.append("End time differs")
    if event["duration_minutes"] != session["duration_minutes"]:
        result.append("Calendar duration differs from the approved duration")
    return result


def _csv(columns: list[str], rows: list[dict]) -> str:
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: csv_safe(row.get(key, "")) for key in columns})
    return stream.getvalue()


def compare_monthly_calendar(conn: sqlite3.Connection, payload: object) -> dict:
    if not isinstance(payload, dict):
        raise RequestValidationError("Choose a month and a calendar CSV file.")
    month = validate_month(payload.get("month"))
    events, metadata = parse_calendar_csv(payload.get("csv_text"), month)
    # One snapshot covers session, participant, invoice, and payment reads while
    # background sync runs on another connection. Never commit a caller's work.
    own_read_transaction = not conn.in_transaction
    if own_read_transaction:
        conn.execute("BEGIN")
    try:
        sessions = _approved_sessions(conn, month)
    finally:
        if own_read_transaction:
            conn.rollback()
    unused_events, unused_sessions = set(range(len(events))), set(range(len(sessions)))
    results = []

    # Exact slots first; weaker associations remain explicit possible matches.
    # A group with multiple choices is never paired arbitrarily.
    for mode in ("exact", "title_day", "start"):
        egroups, sgroups = defaultdict(list), defaultdict(list)
        for index in sorted(unused_events):
            key = _key(events[index], mode)
            if key is not None:
                egroups[key].append(index)
        for index in sorted(unused_sessions):
            key = _key(sessions[index], mode)
            if key is not None:
                sgroups[key].append(index)
        for key in sorted(egroups.keys() & sgroups.keys()):
            ei, si = egroups[key], sgroups[key]
            if len(ei) == len(si) == 1:
                event, session = events[ei[0]], sessions[si[0]]
                differences = _differences(event, session)
                status = "possible_match" if mode != "exact" else ("difference" if differences else "matched")
                results.append({"status": status, "calendar": event, "session": session, "differences": differences})
            else:
                for index in ei:
                    results.append({"status": "ambiguous", "calendar": events[index], "session": None,
                                    "differences": ["Multiple records could match; review individually"]})
                for index in si:
                    results.append({"status": "ambiguous", "calendar": None, "session": sessions[index],
                                    "differences": ["Multiple records could match; review individually"]})
            unused_events.difference_update(ei)
            unused_sessions.difference_update(si)

    # Preserve every duplicate row rather than silently reducing the counts.
    duplicate_keys = Counter((_key(event, "exact"), canonical_datetime(event["end_at"])) for event in events)
    for index in sorted(unused_events):
        event = events[index]
        duplicate = duplicate_keys[(_key(event, "exact"), canonical_datetime(event["end_at"]))] > 1
        results.append({"status": "duplicate" if duplicate else "calendar_only", "calendar": event, "session": None,
                        "differences": ["Repeated calendar record; review for duplicates"] if duplicate else ["No approved session match; may be personal, administrative, or awaiting review"]})
    for index in sorted(unused_sessions):
        session = sessions[index]
        results.append({"status": "session_only", "calendar": None, "session": session,
                        "differences": ["Approved session is not present in this calendar export"]})
    results.sort(key=lambda row: ((row["calendar"] or row["session"])["date"],
                                 canonical_datetime((row["calendar"] or row["session"])["start_at"]), row["status"]))
    counts = Counter(row["status"] for row in results)
    approved_columns = ["Date", "Start", "Participants", "Bill To", "Duration Minutes", "Outcome", "Rate", "Review Status", "Invoice Status", "Payment Status"]
    approved_csv = _csv(approved_columns, [{
        "Date": s["date"], "Start": s["start_time"], "Participants": s["participants"], "Bill To": s["bill_to"],
        "Duration Minutes": s["duration_minutes"], "Outcome": s["outcome"], "Rate": s["rate"],
        "Review Status": "Approved", "Invoice Status": s["invoice_status"], "Payment Status": s["payment_status"],
    } for s in sessions])
    comparison_columns = ["Result", "Date", "Calendar", "Calendar Title", "Calendar Start", "Calendar End", "Calendar Duration Minutes", "Approved Participants", "Approved Start", "Approved End", "Approved Duration Minutes", "Approved Rate", "Outcome", "Review Details"]
    comparison_rows = []
    for row in results:
        event, session = row["calendar"] or {}, row["session"] or {}
        comparison_rows.append({
            "Result": row["status"].replace("_", " ").title(), "Date": event.get("date") or session.get("date"),
            "Calendar": event.get("calendar") or session.get("calendar"), "Calendar Title": event.get("title"),
            "Calendar Start": event.get("start_at"), "Calendar End": event.get("end_at"), "Calendar Duration Minutes": event.get("duration_minutes"),
            "Approved Participants": session.get("participants"), "Approved Start": session.get("start_at"), "Approved End": session.get("end_at"),
            "Approved Duration Minutes": session.get("duration_minutes"), "Approved Rate": session.get("rate"), "Outcome": session.get("outcome"),
            "Review Details": "; ".join(row["differences"]),
        })
    return {"month": month, "metadata": metadata, "calendar_count": len(events), "approved_count": len(sessions),
            "counts": dict(counts), "review_count": len(results) - counts["matched"], "rows": results,
            "approved_csv": approved_csv, "comparison_csv": _csv(comparison_columns, comparison_rows)}
