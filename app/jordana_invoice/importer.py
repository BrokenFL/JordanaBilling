from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from .calendar_preferences import CalendarDisposition, classify_calendar
from .db import OperationalImportAuthorization, assert_csv_import_safe
from .parser import ParseResult, parse_event
from .rates import suggest_rate
from .review import review_status_for_parse, unresolved_fields_for_session
from .util import json_dumps, new_id, now_iso, parse_int, stable_hash, text


def init_db(_conn: sqlite3.Connection) -> None:
    """No-op; schema migrations run explicitly at startup via migrate_database()."""
    pass


RAW_HEADER_MAP = {
    "ingested_at": "ingested_at",
    "snapshot_key": "snapshot_key",
    "run_id": "run_id",
    "batch_name": "batch_name",
    "capture_window": "capture_window",
    "captured_at": "captured_at",
    "window_start": "window_start",
    "window_end": "window_end",
    "source_device": "source_device",
    "timezone": "timezone",
    "calendar_event_id": "calendar_event_id",
    "event_fingerprint": "event_fingerprint",
    "event_title": "event_title",
    "title": "event_title",
    "start_at": "start_at",
    "start_date": "start_at",
    "end_at": "end_at",
    "end_date": "end_at",
    "duration_minutes": "duration_minutes",
    "location": "location",
    "notes": "notes",
    "calendar": "calendar_name",
    "calendar_name": "calendar_name",
    "payload_version": "payload_version",
    "raw_json": "raw_json",
}


def import_csv(
    conn: sqlite3.Connection,
    csv_path: str | Path,
    source_name: str | None = None,
    operational_authorization: OperationalImportAuthorization | None = None,
) -> str:
    assert_csv_import_safe(conn, authorization=operational_authorization)
    path = Path(csv_path)
    rows = list(read_csv_rows(path))
    return import_rows(
        conn,
        rows,
        source_name or path.name,
        source_path=str(path),
        commit=True,
    )


def import_rows(
    conn: sqlite3.Connection,
    rows: list[dict[str, object]],
    source_name: str,
    source_path: str | None = None,
    commit: bool = True,
) -> str:
    if commit:
        init_db(conn)
    imported_at = now_iso()
    import_run_id = new_id()
    normalized_rows = [normalize_raw_row(row) for row in rows]

    conn.execute(
        """
        INSERT INTO import_runs (
          id, source_name, source_path, imported_at, source_row_count,
          completed_run_count, status, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            import_run_id,
            source_name,
            source_path,
            imported_at,
            len(normalized_rows),
            count_completed_runs(normalized_rows),
            "imported",
            "Raw calendar snapshots imported; candidates parsed locally.",
        ),
    )

    inserted_count = 0
    for row_number, row in enumerate(normalized_rows, start=2):
        if insert_raw_snapshot(conn, import_run_id, row_number, row):
            inserted_count += 1

    if inserted_count:
        collapse_candidates(conn, import_run_id)
    else:
        conn.execute(
            "UPDATE import_runs SET status = ?, notes = ? WHERE id = ?",
            ("no_new_rows", "No new raw snapshots were inserted.", import_run_id),
        )

    if commit:
        conn.commit()
    return import_run_id


def read_csv_rows(path: Path) -> Iterable[dict[str, object]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            yield {RAW_HEADER_MAP.get(k.strip(), k.strip()): v for k, v in row.items()}


def count_completed_runs(rows: list[dict[str, object]]) -> int:
    run_windows: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        run_id = text(row.get("run_id"))
        window = text(row.get("capture_window"))
        if run_id and window:
            run_windows[run_id].add(window)
    return sum(
        1
        for windows in run_windows.values()
        if {"next_2_days", "past_7_days"}.issubset(windows)
    )


def insert_raw_snapshot(
    conn: sqlite3.Connection,
    import_run_id: str,
    source_row_number: int,
    row: dict[str, object],
) -> str | None:
    raw_id = new_id()
    normalized = normalize_raw_row(row)
    snapshot_key = text(normalized.get("snapshot_key"))
    if snapshot_key and snapshot_exists(conn, snapshot_key):
        return None
    source_hash = stable_hash(json_dumps(normalized))
    now = now_iso()

    conn.execute(
        """
        INSERT INTO raw_calendar_snapshots (
          id, import_run_id, source_row_number, source_hash, snapshot_key, run_id,
          batch_name, capture_window, captured_at, ingested_at,
          source_device, timezone, calendar_event_id, event_fingerprint,
          event_title, start_at, end_at, duration_minutes, location,
          notes, calendar_name, payload_version, raw_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            raw_id,
            import_run_id,
            source_row_number,
            source_hash,
            snapshot_key,
            normalized.get("run_id"),
            normalized.get("batch_name"),
            normalized.get("capture_window"),
            normalized.get("captured_at"),
            normalized.get("ingested_at"),
            normalized.get("source_device"),
            normalized.get("timezone"),
            normalized.get("calendar_event_id"),
            normalized.get("event_fingerprint"),
            normalized.get("event_title"),
            normalized.get("start_at"),
            normalized.get("end_at"),
            parse_int(normalized.get("duration_minutes")),
            normalized.get("location"),
            normalized.get("notes"),
            normalized.get("calendar_name"),
            parse_int(normalized.get("payload_version")),
            json_dumps(normalized),
            now,
        ),
    )

    return raw_id


def snapshot_exists(conn: sqlite3.Connection, snapshot_key: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM raw_calendar_snapshots WHERE snapshot_key = ? LIMIT 1",
            (snapshot_key,),
        ).fetchone()
        is not None
    )


def normalize_raw_row(row: dict[str, object]) -> dict[str, str]:
    normalized = {RAW_HEADER_MAP.get(key, key): text(value) for key, value in row.items()}
    raw_json = normalized.get("raw_json")
    if raw_json:
        try:
            embedded = json.loads(raw_json)
        except json.JSONDecodeError:
            embedded = {}
        for key in (
            "event_title",
            "start_at",
            "end_at",
            "duration_minutes",
            "location",
            "notes",
            "calendar",
        ):
            mapped = RAW_HEADER_MAP.get(key, key)
            if not normalized.get(mapped) and embedded.get(key) is not None:
                normalized[mapped] = text(embedded.get(key))
    return normalized


def collapse_candidates(conn: sqlite3.Connection, import_run_id: str) -> None:
    rows = conn.execute(
        """
        SELECT * FROM raw_calendar_snapshots
        WHERE import_run_id = ?
        ORDER BY start_at, captured_at, source_row_number
        """,
        (import_run_id,),
    ).fetchall()

    keys: set[str] = set()
    for row in rows:
        keys.add(candidate_key(row))

    for key in sorted(keys):
        group = raw_snapshots_for_candidate_key(conn, key)
        latest = sorted(
            group,
            key=lambda r: (
                text(r["captured_at"]),
                text(r["ingested_at"]),
                r["source_row_number"],
            ),
        )[-1]
        parse_result = parse_event(dict(latest))
        calendar_disposition = classify_calendar(conn, latest["calendar_name"])
        parse_result = apply_calendar_signal(parse_result, calendar_disposition)
        candidate_id = insert_candidate(
            conn,
            import_run_id,
            key,
            latest,
            group,
            parse_result,
            calendar_disposition,
        )
        inserted_session = maybe_insert_session(conn, candidate_id, latest, parse_result)
        if not inserted_session:
            maybe_insert_review_item(conn, candidate_id, None, parse_result)


def candidate_key(row: sqlite3.Row) -> str:
    calendar_event_id = text(row["calendar_event_id"])
    if calendar_event_id:
        return stable_hash(f"calendar_event_id:{calendar_event_id}")
    event_fingerprint = text(row["event_fingerprint"])
    if event_fingerprint:
        return stable_hash(f"event_fingerprint:{event_fingerprint}")
    stable_parts = [
        text(row["event_title"]).lower(),
        text(row["start_at"]),
        text(row["end_at"]),
        text(row["calendar_name"]).lower(),
    ]
    return stable_hash("|".join(part for part in stable_parts if part))


def raw_snapshots_for_candidate_key(conn: sqlite3.Connection, key: str) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT * FROM raw_calendar_snapshots
        ORDER BY start_at, captured_at, source_row_number
        """
    ).fetchall()
    return [row for row in rows if candidate_key(row) == key]


def apply_calendar_signal(result: ParseResult, disposition: CalendarDisposition) -> ParseResult:
    if disposition.disposition == "preferred_work" and result.classification == "client_session":
        result.confidence = min(0.96, result.confidence + 0.04)
        result.explanation = f"{result.explanation} Source calendar is the preferred work calendar."
    elif disposition.disposition in {"usually_personal_admin", "hidden"}:
        if result.classification == "unresolved":
            result.classification = "personal"
            result.confidence = max(result.confidence, 0.58)
            result.explanation = "Source calendar is usually personal/admin; preserved for review."
            result.fields_requiring_review = sorted(set(result.fields_requiring_review + ["classification"]))
        elif result.classification == "client_session":
            result.confidence = max(0.2, result.confidence - 0.12)
            result.fields_requiring_review = sorted(set(result.fields_requiring_review + ["calendar_source"]))
            result.explanation = f"{result.explanation} Source calendar is usually personal/admin."
    return result


def insert_candidate(
    conn: sqlite3.Connection,
    import_run_id: str,
    key: str,
    latest: sqlite3.Row,
    group: list[sqlite3.Row],
    result: ParseResult,
    calendar_disposition: CalendarDisposition,
) -> str:
    now = now_iso()
    windows = sorted({text(row["capture_window"]) for row in group if text(row["capture_window"])})
    review_status = review_status_for_parse(result)
    existing = conn.execute(
        """
        SELECT id, review_status
        FROM calendar_event_candidates
        WHERE candidate_key = ?
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (key,),
    ).fetchone()
    if existing:
        candidate_id = existing["id"]
        conn.execute(
            """
            UPDATE calendar_event_candidates
            SET import_run_id = ?,
                latest_raw_snapshot_id = ?,
                raw_snapshot_count = ?,
                title = ?,
                start_at = ?,
                end_at = ?,
                calendar_duration_minutes = ?,
                calendar_name = ?,
                capture_windows = ?,
                classification = ?,
                confidence = ?,
                explanation = ?,
                fields_requiring_review = ?,
                proposed_client_name = ?,
                proposed_start_at = ?,
                proposed_duration_minutes = ?,
                proposed_end_at = ?,
                time_shorthand = ?,
                duration_source = ?,
                parser_payload = ?,
                review_status = CASE WHEN review_status = 'approved' THEN review_status ELSE ? END,
                confidence_label = ?,
                unresolved_fields = ?,
                review_reasons = ?,
                candidate_person_names = ?,
                possible_referenced_person = ?,
                service_mode = ?,
                rate_group = ?,
                time_category = ?,
                is_evening = ?,
                is_weekend = ?,
                appointment_status = ?,
                billing_treatment = ?,
                title_time_text = ?,
                title_time_normalized = ?,
                title_time_matches_calendar = ?,
                calendar_disposition = ?,
                calendar_is_preferred_work = ?,
                hidden_from_review = ?,
                reconciliation_status = ?,
                billing_session_type = ?,
                appointment_method = ?,
                duration_choice = ?,
                house_call_suggested = ?,
                billing_type_source = ?,
                location_text = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                import_run_id,
                latest["id"],
                len(group),
                latest["event_title"],
                latest["start_at"],
                latest["end_at"],
                result.calendar_duration_minutes,
                latest["calendar_name"],
                json_dumps(windows),
                result.classification,
                result.confidence,
                result.explanation,
                json_dumps(result.fields_requiring_review),
                result.proposed_client_name,
                result.proposed_start_at,
                result.proposed_duration_minutes,
                result.proposed_end_at,
                result.time_shorthand,
                result.duration_source,
                json_dumps(result.as_dict()),
                review_status,
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
                result.appointment_status,
                initial_billing_treatment(result),
                result.title_time_text,
                result.title_time_normalized,
                bool_to_db(result.title_time_matches_calendar),
                calendar_disposition.disposition,
                1 if calendar_disposition.is_preferred_work else 0,
                1 if calendar_disposition.hidden_from_review else 0,
                reconciliation_status(conn, latest, result),
                result.billing_session_type,
                result.appointment_method,
                result.duration_choice,
                1 if result.house_call_suggested else 0,
                result.billing_type_source,
                result.location_text,
                now,
                candidate_id,
            ),
        )
        audit(conn, "calendar_event_candidate", candidate_id, "updated_from_calendar_snapshot", result.as_dict())
        return candidate_id

    candidate_id = new_id()

    conn.execute(
        """
        INSERT INTO calendar_event_candidates (
          id, import_run_id, candidate_key, latest_raw_snapshot_id,
          raw_snapshot_count, title, start_at, end_at,
          calendar_duration_minutes, calendar_name, capture_windows,
          classification, confidence, explanation, fields_requiring_review,
          proposed_client_name, proposed_start_at, proposed_duration_minutes,
          proposed_end_at, time_shorthand, duration_source, parser_payload,
          review_status, confidence_label, unresolved_fields, review_reasons,
          candidate_person_names, possible_referenced_person, service_mode,
          rate_group, time_category, is_evening, is_weekend,
          appointment_status, billing_treatment, title_time_text,
          title_time_normalized, title_time_matches_calendar,
          calendar_disposition, calendar_is_preferred_work, hidden_from_review,
          reconciliation_status, billing_session_type, appointment_method,
          duration_choice, house_call_suggested, billing_type_source,
          location_text, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        candidate_values(
            candidate_id,
            import_run_id,
            key,
            latest["id"],
            len(group),
            latest["event_title"],
            latest["start_at"],
            latest["end_at"],
            result.calendar_duration_minutes,
            latest["calendar_name"],
            json_dumps(windows),
            result.classification,
            result.confidence,
            result.explanation,
            json_dumps(result.fields_requiring_review),
            result.proposed_client_name,
            result.proposed_start_at,
            result.proposed_duration_minutes,
            result.proposed_end_at,
            result.time_shorthand,
            result.duration_source,
            json_dumps(result.as_dict()),
            review_status,
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
            result.appointment_status,
            initial_billing_treatment(result),
            result.title_time_text,
            result.title_time_normalized,
            bool_to_db(result.title_time_matches_calendar),
            calendar_disposition.disposition,
            1 if calendar_disposition.is_preferred_work else 0,
            1 if calendar_disposition.hidden_from_review else 0,
            reconciliation_status(conn, latest, result),
            result.billing_session_type,
            result.appointment_method,
            result.duration_choice,
            1 if result.house_call_suggested else 0,
            result.billing_type_source,
            result.location_text,
            now,
            now,
        ),
    )
    audit(conn, "calendar_event_candidate", candidate_id, "parsed", result.as_dict())
    return candidate_id


def candidate_values(
    candidate_id: str,
    import_run_id: str,
    key: str,
    latest_id: str,
    group_count: int,
    title: str,
    start_at: str,
    end_at: str,
    calendar_duration_minutes: int | None,
    calendar_name: str,
    capture_windows: str,
    classification: str,
    confidence: float,
    explanation: str,
    fields_requiring_review: str,
    proposed_client_name: str | None,
    proposed_start_at: str | None,
    proposed_duration_minutes: int | None,
    proposed_end_at: str | None,
    time_shorthand: str | None,
    duration_source: str | None,
    parser_payload: str,
    review_status: str,
    confidence_label: str,
    unresolved_fields: str,
    review_reasons: str,
    candidate_person_names: str,
    possible_referenced_person: str | None,
    service_mode: str,
    rate_group: str | None,
    time_category: str,
    is_evening: int,
    is_weekend: int,
    appointment_status: str,
    billing_treatment: str,
    title_time_text: str | None,
    title_time_normalized: str | None,
    title_time_matches_calendar: int | None,
    calendar_disposition: str,
    calendar_is_preferred_work: int,
    hidden_from_review: int,
    reconciliation_status_value: str,
    billing_session_type: str,
    appointment_method: str,
    duration_choice: str,
    house_call_suggested: int,
    billing_type_source: str,
    location_text: str | None,
    created_at: str,
    updated_at: str,
) -> tuple:
    return (
        candidate_id,
        import_run_id,
        key,
        latest_id,
        group_count,
        title,
        start_at,
        end_at,
        calendar_duration_minutes,
        calendar_name,
        capture_windows,
        classification,
        confidence,
        explanation,
        fields_requiring_review,
        proposed_client_name,
        proposed_start_at,
        proposed_duration_minutes,
        proposed_end_at,
        time_shorthand,
        duration_source,
        parser_payload,
        review_status,
        confidence_label,
        unresolved_fields,
        review_reasons,
        candidate_person_names,
        possible_referenced_person,
        service_mode,
        rate_group,
        time_category,
        is_evening,
        is_weekend,
        appointment_status,
        billing_treatment,
        title_time_text,
        title_time_normalized,
        title_time_matches_calendar,
        calendar_disposition,
        calendar_is_preferred_work,
        hidden_from_review,
        reconciliation_status_value,
        billing_session_type,
        appointment_method,
        duration_choice,
        house_call_suggested,
        billing_type_source,
        location_text,
        created_at,
        updated_at,
    )


def bool_to_db(value: bool | None) -> int | None:
    if value is None:
        return None
    return 1 if value else 0


def initial_billing_treatment(result: ParseResult) -> str:
    if result.appointment_status in {"cancelled", "no_show"}:
        return "unresolved"
    if result.classification == "client_session":
        return "billable"
    return "not_billable"


def maybe_insert_session(
    conn: sqlite3.Connection,
    candidate_id: str,
    latest: sqlite3.Row,
    result: ParseResult,
) -> bool:
    if result.classification != "client_session":
        return False
    if not result.proposed_start_at or not result.proposed_duration_minutes:
        return False
    now = now_iso()
    session_date = result.proposed_start_at[:10]
    rate = suggest_rate(
        conn,
        session_date=session_date,
        duration_minutes=result.proposed_duration_minutes,
        billing_session_type=result.billing_session_type,
        appointment_status=result.appointment_status,
        service_mode=result.service_mode,
        rate_group=result.rate_group,
        time_category=result.time_category,
    )
    unresolved_fields = unresolved_fields_for_session(result, rate.rate_needs_review)
    review_status = review_status_for_parse(result, rate.rate_needs_review)
    existing = conn.execute(
        "SELECT * FROM sessions WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    candidate = conn.execute(
        """
        SELECT calendar_disposition, calendar_is_preferred_work, hidden_from_review
        FROM calendar_event_candidates
        WHERE id = ?
        """,
        (candidate_id,),
    ).fetchone()
    billing_treatment = initial_billing_treatment(result)
    if existing:
        conn.execute(
            """
            UPDATE sessions
            SET proposed_client_name = ?,
                session_date = ?,
                start_at = ?,
                end_at = ?,
                calendar_duration_minutes = ?,
                parsed_duration_minutes = ?,
                duration_minutes = CASE WHEN review_status = 'approved' THEN duration_minutes ELSE ? END,
                service_mode = CASE WHEN review_status = 'approved' THEN service_mode ELSE ? END,
                rate_group = CASE WHEN review_status = 'approved' THEN rate_group ELSE ? END,
                time_category = CASE WHEN review_status = 'approved' THEN time_category ELSE ? END,
                is_evening = ?,
                is_weekend = ?,
                suggested_rate_cents = CASE WHEN review_status = 'approved' THEN suggested_rate_cents ELSE ? END,
                rate_rule_id = CASE WHEN review_status = 'approved' THEN rate_rule_id ELSE ? END,
                rate_source = CASE WHEN review_status = 'approved' THEN rate_source ELSE ? END,
                rate_needs_review = CASE WHEN review_status = 'approved' THEN rate_needs_review ELSE ? END,
                source_raw_snapshot_id = ?,
                raw_calendar_title = ?,
                review_status = CASE WHEN review_status = 'approved' THEN review_status ELSE ? END,
                appointment_status = ?,
                billing_treatment = CASE
                  WHEN review_status = 'approved' THEN billing_treatment
                  WHEN ? IN ('cancelled', 'no_show') THEN ?
                  WHEN billing_treatment != 'unresolved' THEN billing_treatment
                  ELSE ?
                END,
                title_time_text = ?,
                title_time_normalized = ?,
                title_time_matches_calendar = ?,
                calendar_name = ?,
                calendar_disposition = ?,
                calendar_is_preferred_work = ?,
                hidden_from_review = ?,
                billing_session_type = CASE WHEN review_status = 'approved' THEN billing_session_type ELSE ? END,
                appointment_method = CASE WHEN review_status = 'approved' THEN appointment_method ELSE ? END,
                duration_choice = CASE WHEN review_status = 'approved' THEN duration_choice ELSE ? END,
                custom_duration_minutes = CASE WHEN review_status = 'approved' THEN custom_duration_minutes ELSE ? END,
                house_call_suggested = ?,
                billing_type_source = CASE WHEN review_status = 'approved' THEN billing_type_source ELSE ? END,
                location_text = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                result.proposed_client_name,
                session_date,
                result.proposed_start_at,
                result.proposed_end_at,
                result.calendar_duration_minutes,
                result.proposed_duration_minutes,
                result.proposed_duration_minutes,
                result.service_mode,
                result.rate_group,
                result.time_category,
                1 if result.is_evening else 0,
                1 if result.is_weekend else 0,
                rate.suggested_rate_cents,
                rate.rate_rule_id,
                rate.rate_source,
                1 if rate.rate_needs_review else 0,
                latest["id"],
                latest["event_title"],
                review_status,
                result.appointment_status,
                result.appointment_status,
                billing_treatment,
                billing_treatment,
                result.title_time_text,
                result.title_time_normalized,
                bool_to_db(result.title_time_matches_calendar),
                latest["calendar_name"],
                candidate["calendar_disposition"] if candidate else "review_normally",
                candidate["calendar_is_preferred_work"] if candidate else 0,
                candidate["hidden_from_review"] if candidate else 0,
                result.billing_session_type,
                result.appointment_method,
                result.duration_choice,
                result.custom_duration_minutes,
                1 if result.house_call_suggested else 0,
                result.billing_type_source,
                result.location_text,
                now,
                existing["id"],
            ),
        )
        if existing["review_status"] != "approved":
            conn.execute("DELETE FROM session_participants WHERE session_id = ?", (existing["id"],))
            insert_session_participants(conn, existing["id"], result)
        maybe_insert_review_item(conn, candidate_id, existing["id"], result, unresolved_fields, review_status)
        audit(conn, "session", existing["id"], "updated_from_calendar_snapshot", result.as_dict())
        return True

    session_id = new_id()
    conn.execute(
        """
        INSERT INTO sessions (
          id, candidate_id, source_event_candidate_id, proposed_client_name,
          session_date, start_at, end_at, calendar_duration_minutes,
          parsed_duration_minutes, duration_minutes, service_mode, rate_group,
          time_category, is_evening, is_weekend, suggested_rate_cents,
          rate_rule_id, rate_source, rate_needs_review, source_raw_snapshot_id,
          raw_calendar_title, review_status, billable_status, payment_status,
          appointment_status, billing_treatment, title_time_text,
          title_time_normalized, title_time_matches_calendar, calendar_name,
          calendar_disposition, calendar_is_preferred_work, hidden_from_review,
          billing_session_type, appointment_method, duration_choice,
          custom_duration_minutes, house_call_suggested, billing_type_source,
          location_text, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            candidate_id,
            candidate_id,
            result.proposed_client_name,
            session_date,
            result.proposed_start_at,
            result.proposed_end_at,
            result.calendar_duration_minutes,
            result.proposed_duration_minutes,
            result.proposed_duration_minutes,
            result.service_mode,
            result.rate_group,
            result.time_category,
            1 if result.is_evening else 0,
            1 if result.is_weekend else 0,
            rate.suggested_rate_cents,
            rate.rate_rule_id,
            rate.rate_source,
            1 if rate.rate_needs_review else 0,
            latest["id"],
            latest["event_title"],
            review_status,
            "proposed",
            "unpaid",
            result.appointment_status,
            billing_treatment,
            result.title_time_text,
            result.title_time_normalized,
            bool_to_db(result.title_time_matches_calendar),
            latest["calendar_name"],
            candidate["calendar_disposition"] if candidate else "review_normally",
            candidate["calendar_is_preferred_work"] if candidate else 0,
            candidate["hidden_from_review"] if candidate else 0,
            result.billing_session_type,
            result.appointment_method,
            result.duration_choice,
            result.custom_duration_minutes,
            1 if result.house_call_suggested else 0,
            result.billing_type_source,
            result.location_text,
            now,
            now,
        ),
    )
    insert_session_participants(conn, session_id, result)
    maybe_insert_review_item(conn, candidate_id, session_id, result, unresolved_fields, review_status)
    audit(conn, "session", session_id, "proposed_from_calendar", result.as_dict())
    return True


def maybe_insert_review_item(
    conn: sqlite3.Connection,
    candidate_id: str,
    session_id: str | None,
    result: ParseResult,
    unresolved_fields: list[str] | None = None,
    review_status: str | None = None,
) -> None:
    unresolved_fields = unresolved_fields or result.unresolved_fields
    review_status = review_status or review_status_for_parse(result)
    if not unresolved_fields and result.confidence >= 0.8:
        return
    review_type = (
        "client_session"
        if result.classification == "client_session"
        else "classification"
    )
    priority = 1 if result.classification in {"unresolved", "client_session"} else 2
    now = now_iso()
    review_id = new_id()
    conn.execute(
        """
        INSERT INTO review_queue (
          id, candidate_id, review_type, priority, reason, fields,
          created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            review_id,
            candidate_id,
            review_type,
            priority,
            result.explanation,
            json_dumps(unresolved_fields),
            now,
            now,
        ),
    )
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
            json_dumps(result.review_reasons or [result.explanation]),
            now,
            now,
        ),
    )
    audit(conn, "review_queue", review_id, "opened", result.as_dict())


def insert_session_participants(
    conn: sqlite3.Connection,
    session_id: str,
    result: ParseResult,
) -> None:
    names = result.candidate_person_names or ([result.proposed_client_name] if result.proposed_client_name else [])
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
                name,
                "primary" if index == 0 else "participant",
                1 if index == 0 else 0,
                now,
                now,
            ),
        )


def reconciliation_status(
    conn: sqlite3.Connection,
    latest: sqlite3.Row,
    result: ParseResult,
) -> str:
    if result.classification != "client_session":
        return "not_applicable"
    row = conn.execute(
        """
        SELECT c.title, c.start_at
        FROM calendar_event_candidates c
        WHERE c.id != ?
          AND substr(c.start_at, 1, 10) = substr(?, 1, 10)
          AND c.calendar_name = ?
          AND c.proposed_client_name = ?
        LIMIT 1
        """,
        ("", latest["start_at"], latest["calendar_name"], result.proposed_client_name),
    ).fetchone()
    return "possible_edited_or_rescheduled_event" if row else "new_or_current"


def audit(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    action: str,
    details: object,
) -> None:
    conn.execute(
        """
        INSERT INTO audit_log (id, entity_type, entity_id, action, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (new_id(), entity_type, entity_id, action, json_dumps(details), now_iso()),
    )
