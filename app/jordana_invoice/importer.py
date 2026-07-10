from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .calendar_preferences import CalendarDisposition, classify_calendar
from .capture_windows import completed_run_windows
from .db import (
    OperationalImportAuthorization,
    _create_backup,
    _get_db_path_from_conn,
    _verify_backup,
    assert_csv_import_safe,
)
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


@dataclass(frozen=True)
class CandidateIdentityResolution:
    candidate_id: str | None = None
    reason: str = "new_candidate"
    ambiguous: bool = False


@dataclass(frozen=True)
class RawSnapshotReplayResult:
    raw_snapshots_seen: int
    candidates_before: int
    candidates_after: int
    sessions_before: int
    sessions_after: int
    review_items_before: int
    review_items_after: int
    excluded_pending_sessions: int
    approved_sessions_protected: int
    dry_run: bool
    import_run_id: str | None = None
    backup_path: str | None = None

    @property
    def candidates_created(self) -> int:
        return max(self.candidates_after - self.candidates_before, 0)

    @property
    def sessions_created(self) -> int:
        return max(self.sessions_after - self.sessions_before, 0)

    @property
    def review_items_changed(self) -> int:
        return abs(self.review_items_after - self.review_items_before)

    def as_dict(self) -> dict[str, object]:
        return {
            "raw_snapshots_seen": self.raw_snapshots_seen,
            "candidates_created": self.candidates_created,
            "sessions_created": self.sessions_created,
            "review_items_changed": self.review_items_changed,
            "excluded_pending_sessions": self.excluded_pending_sessions,
            "approved_sessions_protected": self.approved_sessions_protected,
            "dry_run": self.dry_run,
            "import_run_id": self.import_run_id,
            "backup_path": self.backup_path,
        }


def _validate_reconciliation_month(month: str | None) -> str | None:
    raw = text(month)
    if not raw:
        return None
    if len(raw) != 7 or raw[4] != "-" or not raw[:4].isdigit() or not raw[5:].isdigit():
        raise ValueError("Month must use YYYY-MM format.")
    month_number = int(raw[5:])
    if month_number < 1 or month_number > 12:
        raise ValueError("Month must use YYYY-MM format.")
    return raw


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
        # A release upgrade can add reconciliation logic after the raw evidence
        # has already arrived. Recheck pending derived records even on an empty
        # incremental sync; raw evidence remains read-only.
        suppress_pending_events_missing_from_newest_covering_snapshot(conn)

    if commit:
        conn.commit()
    return import_run_id


def replay_existing_raw_snapshots(
    conn: sqlite3.Connection,
    *,
    apply: bool = False,
    backup_operational: bool = True,
    month: str | None = None,
) -> RawSnapshotReplayResult:
    """Replay preserved raw evidence through candidate/session collapse.

    Raw snapshots are never inserted, updated, or deleted here. Dry-run uses a
    savepoint and rolls back every derived write after collecting the exact
    reconciliation summary.
    """
    month = _validate_reconciliation_month(month)

    if apply and backup_operational:
        db_path = _get_db_path_from_conn(conn)
        backup_path: str | None = None
        if db_path is not None and db_path.exists():
            created_backup = _create_backup(db_path)
            _verify_backup(created_backup)
            backup_path = str(created_backup)
    else:
        backup_path = None

    if apply:
        result = _replay_existing_raw_snapshots_inner(conn, dry_run=False, backup_path=backup_path, month=month)
        conn.commit()
        return result

    conn.execute("SAVEPOINT raw_snapshot_replay_dry_run")
    try:
        result = _replay_existing_raw_snapshots_inner(conn, dry_run=True, backup_path=None, month=month)
    finally:
        conn.execute("ROLLBACK TO raw_snapshot_replay_dry_run")
        conn.execute("RELEASE raw_snapshot_replay_dry_run")
    return result


def calendar_reconciliation_report(
    conn: sqlite3.Connection,
    *,
    month: str | None = None,
    apply: bool = False,
) -> dict[str, object]:
    month = _validate_reconciliation_month(month)
    result = replay_existing_raw_snapshots(conn, apply=apply, month=month)
    return {
        "ok": True,
        "month": month,
        "mode": "apply" if apply else "dry_run",
        "summary": result.as_dict(),
        "buckets": calendar_reconciliation_buckets(conn, month=month),
    }


def calendar_reconciliation_buckets(conn: sqlite3.Connection, *, month: str | None = None) -> dict[str, list[dict[str, object]]]:
    month = _validate_reconciliation_month(month)
    return {
        "missing_sessions": _missing_reconciliation_rows(conn, month),
        "extra_sessions": _extra_reconciliation_sessions(conn, month),
        "possible_duplicates": _possible_duplicate_sessions(conn, month),
        "newer_edited_event_versions": _edited_event_versions(conn, month),
        "excluded_non_client_items_affecting_billing": _excluded_items_affecting_billing(conn, month),
        "approved_records_require_manual_review": _approved_records_requiring_manual_review(conn, month),
    }


def _replay_existing_raw_snapshots_inner(
    conn: sqlite3.Connection,
    *,
    dry_run: bool,
    backup_path: str | None,
    month: str | None,
) -> RawSnapshotReplayResult:
    if month:
        rows = conn.execute(
            """
            SELECT *
            FROM raw_calendar_snapshots
            WHERE substr(start_at, 1, 7) = ?
            ORDER BY start_at, captured_at, ingested_at, source_row_number
            """,
            (month,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT *
            FROM raw_calendar_snapshots
            ORDER BY start_at, captured_at, ingested_at, source_row_number
            """
        ).fetchall()
    before = _replay_counts(conn)
    import_run_id = new_id()
    now = now_iso()
    conn.execute(
        """
        INSERT INTO import_runs (
          id, source_name, source_path, imported_at, source_row_count,
          completed_run_count, status, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            import_run_id,
            "raw_snapshot_replay",
            None,
            now,
            len(rows),
            count_completed_runs([dict(row) for row in rows]),
            "dry_run" if dry_run else "imported",
            "Existing raw calendar snapshots replayed without duplicating raw evidence.",
        ),
    )
    excluded_before = _audit_action_count(conn, "session", "excluded_from_latest_calendar_snapshot")
    protected_before = _audit_action_count(conn, "session", "approved_session_protected_from_calendar_replay")
    if rows:
        collapse_raw_snapshot_rows(conn, import_run_id, rows)
    after = _replay_counts(conn)
    excluded_after = _audit_action_count(conn, "session", "excluded_from_latest_calendar_snapshot")
    protected_after = _audit_action_count(conn, "session", "approved_session_protected_from_calendar_replay")
    return RawSnapshotReplayResult(
        raw_snapshots_seen=len(rows),
        candidates_before=before["calendar_event_candidates"],
        candidates_after=after["calendar_event_candidates"],
        sessions_before=before["sessions"],
        sessions_after=after["sessions"],
        review_items_before=before["review_items"],
        review_items_after=after["review_items"],
        excluded_pending_sessions=max(excluded_after - excluded_before, 0),
        approved_sessions_protected=max(protected_after - protected_before, 0),
        dry_run=dry_run,
        import_run_id=None if dry_run else import_run_id,
        backup_path=backup_path,
    )


def _month_clause(alias: str, month: str | None, column: str = "start_at") -> tuple[str, tuple[str, ...]]:
    if not month:
        return "", ()
    return f" AND substr({alias}.{column}, 1, 7) = ?", (month,)


def _snapshot_summary(row: sqlite3.Row | dict[str, object]) -> dict[str, object]:
    return {
        "raw_snapshot_id": row["id"],
        "calendar_event_id": text(row["calendar_event_id"]),
        "title": text(row["event_title"]),
        "start_at": text(row["start_at"]),
        "end_at": text(row["end_at"]),
        "calendar_name": text(row["calendar_name"]),
    }


def _session_summary(row: sqlite3.Row | dict[str, object]) -> dict[str, object]:
    return {
        "session_id": row["id"],
        "candidate_id": row["candidate_id"],
        "title": text(row["raw_calendar_title"]),
        "date": text(row["session_date"]),
        "start_at": text(row["start_at"]),
        "end_at": text(row["end_at"]),
        "participants": text(row["participants"]),
        "review_status": text(row["review_status"]),
        "billing_treatment": text(row["billing_treatment"]),
        "billable_status": text(row["billable_status"]),
    }


def _missing_reconciliation_rows(conn: sqlite3.Connection, month: str | None) -> list[dict[str, object]]:
    where, params = _month_clause("r", month)
    rows = conn.execute(
        f"""
        SELECT *
        FROM raw_calendar_snapshots r
        WHERE 1 = 1 {where}
        ORDER BY r.start_at, r.captured_at, r.ingested_at
        LIMIT 250
        """,
        params,
    ).fetchall()
    missing: list[dict[str, object]] = []
    for row in rows:
        key = candidate_key(row)
        found = conn.execute(
            "SELECT 1 FROM calendar_event_candidates WHERE candidate_key = ? LIMIT 1",
            (key,),
        ).fetchone()
        if not found:
            missing.append(_snapshot_summary(row))
    return missing[:50]


def _extra_reconciliation_sessions(conn: sqlite3.Connection, month: str | None) -> list[dict[str, object]]:
    where, params = _month_clause("s", month)
    return [
        _session_summary(row)
        for row in conn.execute(
            f"""
            SELECT s.*, COALESCE(group_concat(NULLIF(sp.participant_name, ''), ', '), '') AS participants
            FROM sessions s
            LEFT JOIN raw_calendar_snapshots r ON r.id = s.source_raw_snapshot_id
            LEFT JOIN session_participants sp ON sp.session_id = s.id
            WHERE r.id IS NULL {where}
            GROUP BY s.id
            ORDER BY s.start_at
            LIMIT 50
            """,
            params,
        ).fetchall()
    ]


def _possible_duplicate_sessions(conn: sqlite3.Connection, month: str | None) -> list[dict[str, object]]:
    where, params = _month_clause("s", month)
    rows = conn.execute(
        f"""
        SELECT s.*, COALESCE(group_concat(NULLIF(sp.participant_name, ''), ', '), '') AS participants
        FROM sessions s
        LEFT JOIN session_participants sp ON sp.session_id = s.id
        WHERE s.review_status != 'excluded' {where}
        GROUP BY s.id
        ORDER BY s.session_date, s.start_at
        LIMIT 500
        """,
        params,
    ).fetchall()
    groups: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        groups[(text(row["session_date"]), text(row["start_at"]), text(row["billing_party_id"]))].append(_session_summary(row))
    return [
        {"date": date, "start_at": start_at, "billing_party_id": bill_to, "sessions": sessions}
        for (date, start_at, bill_to), sessions in groups.items()
        if date and start_at and len(sessions) > 1
    ][:50]


def _edited_event_versions(conn: sqlite3.Connection, month: str | None) -> list[dict[str, object]]:
    where, params = _month_clause("r", month)
    rows = conn.execute(
        f"""
        SELECT calendar_event_id,
               COUNT(*) AS snapshot_count,
               COUNT(DISTINCT COALESCE(event_title, '') || '|' || COALESCE(start_at, '') || '|' || COALESCE(end_at, '')) AS version_count,
               MIN(start_at) AS first_start_at,
               MAX(start_at) AS latest_start_at,
               MAX(captured_at) AS latest_captured_at
        FROM raw_calendar_snapshots r
        WHERE calendar_event_id IS NOT NULL AND calendar_event_id != '' {where}
        GROUP BY calendar_event_id
        HAVING snapshot_count > 1 AND version_count > 1
        ORDER BY latest_captured_at DESC
        LIMIT 50
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def _excluded_items_affecting_billing(conn: sqlite3.Connection, month: str | None) -> list[dict[str, object]]:
    where, params = _month_clause("s", month)
    return [
        {
            **_session_summary(row),
            "invoice_id": row["invoice_id"],
            "invoice_status": row["invoice_status"],
        }
        for row in conn.execute(
            f"""
            SELECT s.*, COALESCE(group_concat(NULLIF(sp.participant_name, ''), ', '), '') AS participants,
                   li.invoice_id, i.status AS invoice_status
            FROM sessions s
            JOIN invoice_line_items li ON li.source_session_id = s.id
            JOIN invoices i ON i.invoice_id = li.invoice_id
            LEFT JOIN session_participants sp ON sp.session_id = s.id
            WHERE (
                s.review_status = 'excluded'
                OR s.billable_status IN ('excluded', 'nonbillable')
                OR s.billing_treatment != 'billable'
              ) {where}
            GROUP BY s.id, li.invoice_id
            ORDER BY s.start_at
            LIMIT 50
            """,
            params,
        ).fetchall()
    ]


def _approved_records_requiring_manual_review(conn: sqlite3.Connection, month: str | None) -> list[dict[str, object]]:
    where, params = _month_clause("s", month)
    return [
        {
            **_session_summary(row),
            "old_value": text(row["old_value"]),
            "new_value": text(row["new_value"]),
            "reason": text(row["reason"]),
        }
        for row in conn.execute(
            f"""
            SELECT s.*, COALESCE(group_concat(NULLIF(sp.participant_name, ''), ', '), '') AS participants,
                   ri.old_value, ri.new_value, ri.reason
            FROM review_items ri
            JOIN sessions s ON s.id = ri.session_id
            LEFT JOIN session_participants sp ON sp.session_id = s.id
            WHERE s.review_status = 'approved'
              AND ri.review_status IN ('source_change_warning', 'needs_review')
              {where}
            GROUP BY ri.review_item_id
            ORDER BY s.start_at
            LIMIT 50
            """,
            params,
        ).fetchall()
    ]


def _replay_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        table: int(conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"])
        for table in ("calendar_event_candidates", "sessions", "review_items")
    }


def _audit_action_count(conn: sqlite3.Connection, entity_type: str, action: str) -> int:
    return int(
        conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM audit_log
            WHERE entity_type = ? AND action = ?
            """,
            (entity_type, action),
        ).fetchone()["c"]
    )


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
    return sum(1 for windows in run_windows.values() if completed_run_windows(windows))


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
    collapse_raw_snapshot_rows(conn, import_run_id, rows)


def collapse_raw_snapshot_rows(
    conn: sqlite3.Connection,
    import_run_id: str,
    rows: list[sqlite3.Row],
) -> None:
    same_batch_structural_keys: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if text(row["calendar_event_id"]) or text(row["event_fingerprint"]):
            same_batch_structural_keys[structural_identity_value(row)].add(candidate_key(row))

    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        key = candidate_key(row)
        resolution = resolve_candidate_identity(conn, row, key)
        if resolution.ambiguous:
            key = stable_hash(f"ambiguous_identity:{row['id']}")
        if not resolution.candidate_id and not resolution.ambiguous and not (
            text(row["calendar_event_id"]) or text(row["event_fingerprint"])
        ):
            batch_keys = same_batch_structural_keys.get(structural_identity_value(row), set())
            if len(batch_keys) == 1:
                key = next(iter(batch_keys))
                resolution = CandidateIdentityResolution(None, "exact_structural_same_batch")
        group_token = f"candidate:{resolution.candidate_id}" if resolution.candidate_id else f"key:{key}"
        if group_token not in grouped:
            grouped[group_token] = {
                "key": key,
                "resolution": resolution,
                "rows": [],
            }
        grouped[group_token]["rows"].append(row)

    for group_info in sorted(grouped.values(), key=lambda item: str(item["key"])):
        key = str(group_info["key"])
        resolution = group_info["resolution"]
        incoming_group = list(group_info["rows"])
        if isinstance(resolution, CandidateIdentityResolution) and resolution.candidate_id:
            candidate = conn.execute(
                "SELECT candidate_key FROM calendar_event_candidates WHERE id = ?",
                (resolution.candidate_id,),
            ).fetchone()
            if candidate:
                key = candidate["candidate_key"]
        group = raw_snapshots_for_identity_group(conn, key, incoming_group)
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
        if isinstance(resolution, CandidateIdentityResolution) and resolution.ambiguous:
            parse_result.fields_requiring_review = sorted(
                set(parse_result.fields_requiring_review + ["identity_resolution"])
            )
            parse_result.unresolved_fields = sorted(
                set(parse_result.unresolved_fields + ["identity_resolution"])
            )
            parse_result.review_reasons = sorted(
                set(parse_result.review_reasons + ["Ambiguous calendar identity; manual review required."])
            )
        candidate_id = insert_candidate(
            conn,
            import_run_id,
            key,
            latest,
            group,
            parse_result,
            calendar_disposition,
        )
        record_candidate_identity_aliases(
            conn,
            candidate_id,
            group,
            resolution if isinstance(resolution, CandidateIdentityResolution) else None,
        )
        inserted_session = maybe_insert_session(conn, candidate_id, latest, parse_result)
        if parse_result.classification != "client_session":
            maybe_exclude_pending_session(conn, candidate_id, latest, parse_result)
        if not inserted_session:
            maybe_insert_review_item(conn, candidate_id, None, parse_result)

    suppress_pending_events_missing_from_newest_covering_snapshot(conn)


def suppress_pending_events_missing_from_newest_covering_snapshot(
    conn: sqlite3.Connection,
) -> int:
    """Exclude ended pending events omitted by the newest complete capture run.

    Raw snapshot rows remain append-only evidence.  A missing event is only
    operationally meaningful only after its scheduled end, when the newest
    complete capture run omits it from every batch that covers that date.
    Partial, failed, malformed, non-covering, future, and approved records
    therefore cannot suppress anything.
    """
    newest_covering_batches = newest_complete_covering_batches(conn)
    if not newest_covering_batches:
        return 0

    candidates = conn.execute(
        """
        SELECT c.id, c.candidate_key, c.start_at, c.proposed_start_at,
               c.latest_raw_snapshot_id,
               c.end_at AS candidate_end_at,
               s.end_at AS session_end_at
        FROM calendar_event_candidates c
        LEFT JOIN sessions s ON s.candidate_id = c.id
        WHERE c.classification = 'client_session'
          AND c.review_status NOT IN ('approved', 'excluded')
          AND COALESCE(s.review_status, '') != 'approved'
        """
    ).fetchall()
    suppressed = 0
    for candidate in candidates:
        appointment_date = snapshot_date(candidate["proposed_start_at"] or candidate["start_at"])
        if not appointment_date:
            continue
        if not appointment_has_ended(candidate["session_end_at"] or candidate["candidate_end_at"]):
            continue
        batches = newest_covering_batches.get(appointment_date)
        if not batches:
            continue
        if candidate_is_present_in_snapshot_batches(conn, candidate, batches):
            continue
        suppress_pending_candidate_for_missing_snapshot(conn, candidate["id"], batches)
        suppressed += 1
    return suppressed


def appointment_has_ended(value: object) -> bool:
    try:
        end_at = datetime.fromisoformat(text(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    if end_at.tzinfo is None:
        return False
    return end_at.astimezone(timezone.utc) <= datetime.now(timezone.utc)


def newest_complete_covering_batches(conn: sqlite3.Connection) -> dict[str, tuple[tuple[str, str], ...]]:
    """Return every newest-run batch that proves coverage for each date.

    Coverage boundaries are retained inside ``raw_json`` because the original
    Sheet columns are intentionally preserved verbatim.  A batch without both
    valid boundaries (or a canonical label/timestamp fallback) is not evidence
    of coverage. A current event in either overlapping batch keeps it active.
    """
    rows = conn.execute(
        """
        SELECT r.*, i.status AS import_status
        FROM raw_calendar_snapshots r
        JOIN import_runs i ON i.id = r.import_run_id
        WHERE trim(coalesce(r.run_id, '')) != ''
        """
    ).fetchall()
    run_windows: dict[str, set[str]] = defaultdict(set)
    run_is_successful: dict[str, bool] = {}
    batch_rows: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        run_id = text(row["run_id"])
        capture_window = text(row["capture_window"])
        run_windows[run_id].add(capture_window)
        run_is_successful[run_id] = run_is_successful.get(run_id, True) and row["import_status"] == "imported"
        batch_rows[(run_id, capture_window)].append(row)

    run_rank: dict[str, tuple[str, str, str]] = defaultdict(lambda: ("", "", ""))
    for row in rows:
        run_id = text(row["run_id"])
        run_rank[run_id] = max(
            run_rank[run_id],
            (text(row["captured_at"]), text(row["ingested_at"]), text(row["id"])),
        )

    covering_by_run_and_date: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for (run_id, capture_window), batch in batch_rows.items():
        if not run_is_successful.get(run_id) or not completed_run_windows(run_windows[run_id]):
            continue
        coverage = snapshot_batch_coverage(batch)
        if not coverage:
            continue
        for appointment_date in coverage:
            covering_by_run_and_date[(run_id, appointment_date)].append((run_id, capture_window))

    newest_covering_batches: dict[str, tuple[tuple[str, str, str], tuple[tuple[str, str], ...]]] = {}
    for (run_id, appointment_date), batches in covering_by_run_and_date.items():
        rank = run_rank[run_id]
        prior = newest_covering_batches.get(appointment_date)
        if prior is None or rank > prior[0]:
            newest_covering_batches[appointment_date] = (rank, tuple(batches))
    return {
        appointment_date: batches
        for appointment_date, (_, batches) in newest_covering_batches.items()
    }


def snapshot_batch_coverage(rows: list[sqlite3.Row]) -> set[str]:
    coverage: set[str] = set()
    for row in rows:
        try:
            raw = json.loads(text(row["raw_json"]))
        except json.JSONDecodeError:
            continue
        window_start = snapshot_date(raw.get("window_start"))
        window_end = snapshot_date(raw.get("window_end"))
        if not window_start or not window_end or window_end < window_start:
            continue
        current = window_start
        while current <= window_end:
            coverage.add(current)
            # ISO calendar arithmetic is deliberately local-date based; capture
            # windows are calendar-date boundaries, not elapsed-time intervals.
            current = (date.fromisoformat(current) + timedelta(days=1)).isoformat()
    if coverage:
        return coverage

    # The production Shortcut currently leaves explicit window boundaries blank
    # even though it supplies a canonical window label and capture timestamp.
    # Those labels have fixed inclusive date semantics, so they are still
    # definite coverage evidence. Unknown/legacy labels remain non-covering.
    capture_windows = {text(row["capture_window"]) for row in rows if text(row["capture_window"])}
    if len(capture_windows) != 1:
        return set()
    capture_window = next(iter(capture_windows))
    if capture_window == "backfill_2026_06_01_through_2026_06_14":
        return dates_inclusive("2026-06-01", "2026-06-14")

    capture_dates = {snapshot_date(row["captured_at"]) for row in rows}
    capture_dates.discard(None)
    if len(capture_dates) != 1:
        return set()
    captured_on = date.fromisoformat(next(iter(capture_dates)))
    offsets = {
        "past_3_days": (-3, 0),
        "past_7_days": (-7, 0),
        "next_7_days": (0, 7),
        "next_2_days": (0, 2),
    }.get(capture_window)
    if not offsets:
        return set()
    start_offset, end_offset = offsets
    return dates_inclusive(
        (captured_on + timedelta(days=start_offset)).isoformat(),
        (captured_on + timedelta(days=end_offset)).isoformat(),
    )


def dates_inclusive(start_date: str, end_date: str) -> set[str]:
    dates: set[str] = set()
    current = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    while current <= end:
        dates.add(current.isoformat())
        current += timedelta(days=1)
    return dates


def snapshot_date(value: object) -> str | None:
    candidate = text(value)[:10]
    if len(candidate) != 10 or candidate[4] != "-" or candidate[7] != "-":
        return None
    try:
        return date.fromisoformat(candidate).isoformat()
    except ValueError:
        return None


def candidate_is_present_in_snapshot_batches(
    conn: sqlite3.Connection,
    candidate: sqlite3.Row,
    batches: tuple[tuple[str, str], ...],
) -> bool:
    return any(
        candidate_is_present_in_snapshot_batch(conn, candidate, batch)
        for batch in batches
    )


def candidate_is_present_in_snapshot_batch(
    conn: sqlite3.Connection,
    candidate: sqlite3.Row,
    batch: tuple[str, str],
) -> bool:
    run_id, capture_window = batch
    row = conn.execute(
        """
        SELECT 1
        FROM candidate_identity_aliases a
        JOIN raw_calendar_snapshots r ON r.id = a.source_raw_snapshot_id
        WHERE a.candidate_id = ?
          AND r.run_id = ?
          AND r.capture_window = ?
        LIMIT 1
        """,
        (candidate["id"], run_id, capture_window),
    ).fetchone()
    if row:
        return True
    latest = conn.execute(
        """
        SELECT run_id, capture_window
        FROM raw_calendar_snapshots
        WHERE id = ?
        """,
        (candidate["latest_raw_snapshot_id"],),
    ).fetchone()
    return bool(latest and latest["run_id"] == run_id and latest["capture_window"] == capture_window)


def suppress_pending_candidate_for_missing_snapshot(
    conn: sqlite3.Connection,
    candidate_id: str,
    batches: tuple[tuple[str, str], ...],
) -> None:
    run_id = batches[0][0]
    capture_windows = sorted({capture_window for _, capture_window in batches})
    now = now_iso()
    reason = "Absent from every newest complete calendar snapshot batch that covers this completed appointment."
    conn.execute(
        """
        UPDATE calendar_event_candidates
        SET review_status = 'excluded',
            hidden_from_review = 1,
            reconciliation_status = 'removed_from_newest_covering_snapshot',
            updated_at = ?
        WHERE id = ?
          AND review_status NOT IN ('approved', 'excluded')
        """,
        (now, candidate_id),
    )
    conn.execute(
        """
        UPDATE sessions
        SET review_status = 'excluded',
            billable_status = 'excluded',
            hidden_from_review = 1,
            updated_at = ?
        WHERE candidate_id = ?
          AND review_status != 'approved'
        """,
        (now, candidate_id),
    )
    conn.execute(
        """
        UPDATE review_items
        SET review_status = 'excluded',
            reviewed_at = COALESCE(reviewed_at, ?),
            updated_at = ?
        WHERE candidate_id = ?
          AND reviewed_at IS NULL
        """,
        (now, now, candidate_id),
    )
    audit(
        conn,
        "calendar_event_candidate",
        candidate_id,
        "suppressed_by_newest_covering_calendar_snapshot",
        {
            "reason": reason,
            "newest_covering_run_id": run_id,
            "newest_covering_capture_windows": capture_windows,
        },
    )


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


def structural_identity_value(row: sqlite3.Row) -> str:
    stable_parts = [
        text(row["event_title"]).lower(),
        text(row["start_at"]),
        text(row["end_at"]),
        text(row["duration_minutes"]),
        text(row["calendar_name"]).lower(),
    ]
    return stable_hash("structural:" + "|".join(part for part in stable_parts if part))


def identity_aliases_for_row(row: sqlite3.Row) -> list[tuple[str, str]]:
    aliases: list[tuple[str, str]] = []
    calendar_event_id = text(row["calendar_event_id"])
    if calendar_event_id:
        aliases.append(("calendar_event_id", stable_hash(f"calendar_event_id:{calendar_event_id}")))
    event_fingerprint = text(row["event_fingerprint"])
    if event_fingerprint:
        aliases.append(("event_fingerprint", stable_hash(f"event_fingerprint:{event_fingerprint}")))
    if text(row["event_title"]) and text(row["start_at"]) and text(row["end_at"]) and text(row["calendar_name"]):
        aliases.append(("structural", structural_identity_value(row)))
    return aliases


def resolve_candidate_identity(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    fallback_key: str,
) -> CandidateIdentityResolution:
    event_id = text(row["calendar_event_id"])
    fingerprint = text(row["event_fingerprint"])
    resolved: list[CandidateIdentityResolution] = []
    if event_id:
        event_resolution = resolve_exact_identity(
            conn,
            "calendar_event_id",
            stable_hash(f"calendar_event_id:{event_id}"),
            fallback_key,
        )
        if event_resolution.ambiguous:
            return event_resolution
        if event_resolution.candidate_id:
            resolved.append(event_resolution)
    if fingerprint:
        fingerprint_resolution = resolve_exact_identity(
            conn,
            "event_fingerprint",
            stable_hash(f"event_fingerprint:{fingerprint}"),
            stable_hash(f"event_fingerprint:{fingerprint}"),
        )
        if fingerprint_resolution.ambiguous:
            return fingerprint_resolution
        if fingerprint_resolution.candidate_id:
            resolved.append(fingerprint_resolution)
    resolved_candidate_ids = {resolution.candidate_id for resolution in resolved}
    if len(resolved_candidate_ids) == 1:
        return resolved[0]
    if len(resolved_candidate_ids) > 1:
        return CandidateIdentityResolution(None, "ambiguous_identifier_conflict", True)

    if not (event_id and fingerprint):
        structural_resolution = resolve_structural_identity(
            conn,
            row,
            include_approved=not (event_id or fingerprint),
        )
        if structural_resolution.candidate_id:
            return structural_resolution
        if structural_resolution.ambiguous:
            return structural_resolution
    return CandidateIdentityResolution(None, "new_candidate")


def resolve_exact_identity(
    conn: sqlite3.Connection,
    alias_type: str,
    alias_value: str,
    candidate_key_value: str,
) -> CandidateIdentityResolution:
    candidate_ids = {
        row["candidate_id"]
        for row in conn.execute(
            """
            SELECT candidate_id
            FROM candidate_identity_aliases
            WHERE alias_type = ? AND alias_value = ?
            """,
            (alias_type, alias_value),
        ).fetchall()
    }
    candidate_ids.update(
        row["id"]
        for row in conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (candidate_key_value,),
        ).fetchall()
    )
    if len(candidate_ids) == 1:
        return CandidateIdentityResolution(next(iter(candidate_ids)), f"exact_{alias_type}")
    if len(candidate_ids) > 1:
        return CandidateIdentityResolution(None, f"ambiguous_{alias_type}", True)
    return CandidateIdentityResolution(None, "new_candidate")


def resolve_structural_identity(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    include_approved: bool = True,
) -> CandidateIdentityResolution:
    alias_value = structural_identity_value(row)
    candidate_ids = {
        alias_row["candidate_id"]
        for alias_row in conn.execute(
            """
            SELECT candidate_id
            FROM candidate_identity_aliases
            WHERE alias_type = 'structural' AND alias_value = ?
            """,
            (alias_value,),
        ).fetchall()
    }
    candidate_ids.update(
        candidate_row["id"]
        for candidate_row in conn.execute(
            """
            SELECT c.id
            FROM calendar_event_candidates c
            JOIN raw_calendar_snapshots r ON r.id = c.latest_raw_snapshot_id
            WHERE lower(trim(coalesce(c.title, ''))) = lower(trim(?))
              AND c.start_at = ?
              AND c.end_at = ?
              AND coalesce(c.calendar_duration_minutes, -1) = coalesce(?, -1)
              AND lower(trim(coalesce(c.calendar_name, ''))) = lower(trim(?))
              AND lower(trim(coalesce(r.event_title, ''))) = lower(trim(?))
              AND r.start_at = ?
              AND r.end_at = ?
              AND coalesce(r.duration_minutes, -1) = coalesce(?, -1)
              AND lower(trim(coalesce(r.calendar_name, ''))) = lower(trim(?))
            """,
            (
                text(row["event_title"]),
                text(row["start_at"]),
                text(row["end_at"]),
                parse_int(row["duration_minutes"]),
                text(row["calendar_name"]),
                text(row["event_title"]),
                text(row["start_at"]),
                text(row["end_at"]),
                parse_int(row["duration_minutes"]),
                text(row["calendar_name"]),
            ),
        ).fetchall()
    )
    if not include_approved and candidate_ids:
        candidate_ids = {
            candidate_id
            for candidate_id in candidate_ids
            if not conn.execute(
                """
                SELECT 1
                FROM sessions
                WHERE candidate_id = ?
                  AND review_status = 'approved'
                LIMIT 1
                """,
                (candidate_id,),
            ).fetchone()
        }
    if len(candidate_ids) == 1:
        return CandidateIdentityResolution(next(iter(candidate_ids)), "exact_structural")
    if len(candidate_ids) > 1:
        return CandidateIdentityResolution(None, "ambiguous_structural", True)
    return CandidateIdentityResolution(None, "new_candidate")


def raw_snapshots_for_candidate_key(conn: sqlite3.Connection, key: str) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT * FROM raw_calendar_snapshots
        ORDER BY start_at, captured_at, source_row_number
        """
    ).fetchall()
    return [row for row in rows if candidate_key(row) == key]


def raw_snapshots_for_identity_group(
    conn: sqlite3.Connection,
    key: str,
    incoming_rows: list[sqlite3.Row],
) -> list[sqlite3.Row]:
    by_id = {row["id"]: row for row in raw_snapshots_for_candidate_key(conn, key)}
    for row in incoming_rows:
        by_id[row["id"]] = row
    return list(by_id.values())


def record_candidate_identity_aliases(
    conn: sqlite3.Connection,
    candidate_id: str,
    group: list[sqlite3.Row],
    resolution: CandidateIdentityResolution | None,
) -> None:
    reason = resolution.reason if resolution else "new_candidate"
    now = now_iso()
    for row in group:
        for alias_type, alias_value in identity_aliases_for_row(row):
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO candidate_identity_aliases (
                  alias_id, candidate_id, alias_type, alias_value,
                  source_raw_snapshot_id, resolution_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (new_id(), candidate_id, alias_type, alias_value, row["id"], reason, now),
            )
            if cursor.rowcount:
                audit(
                    conn,
                    "calendar_event_candidate",
                    candidate_id,
                    "identity_alias_recorded",
                    {
                        "alias_type": alias_type,
                        "resolution_reason": reason,
                        "source_raw_snapshot_id": row["id"],
                    },
                )


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
    if result.appointment_status in {"cancelled", "no_show", "late_cancellation", "timely_cancellation"}:
        return "unresolved"
    if result.classification == "client_session":
        return "billable"
    return "not_billable"


def maybe_create_source_change_warning(
    conn: sqlite3.Connection,
    candidate_id: str,
    session_id: str,
    old_title: str,
    new_title: str,
) -> None:
    """Create a visible source-change warning when an approved session's
    raw calendar title has changed in a later snapshot.

    Does NOT un-approve the session or rewrite approved values.
    Creates a review_items entry and a review_queue entry so the change
    is visible in the review queue. Idempotent: if an unreviewed
    source_change_warning already exists for this candidate, it is
    updated rather than duplicated.
    """
    if not old_title or not new_title or old_title == new_title:
        return

    now = now_iso()
    reason = f"Source calendar title changed after approval: \"{old_title}\" -> \"{new_title}\""
    unresolved_fields = ["source_change_warning"]
    review_status = "source_change_warning"

    existing_warning = conn.execute(
        """
        SELECT review_item_id
        FROM review_items
        WHERE candidate_id = ?
          AND review_status = 'source_change_warning'
          AND reviewed_at IS NULL
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (candidate_id,),
    ).fetchone()

    if existing_warning:
        conn.execute(
            """
            UPDATE review_items
            SET session_id = ?,
                unresolved_fields = ?,
                review_reasons = ?,
                old_value = ?,
                new_value = ?,
                reason = ?,
                updated_at = ?
            WHERE review_item_id = ?
            """,
            (
                session_id,
                json_dumps(unresolved_fields),
                json_dumps([reason]),
                old_title,
                new_title,
                reason,
                now,
                existing_warning["review_item_id"],
            ),
        )
        audit(conn, "review_item", existing_warning["review_item_id"], "updated", {"reason": reason})
        return

    review_item_id = new_id()
    conn.execute(
        """
        INSERT INTO review_items (
          review_item_id, candidate_id, session_id, review_status,
          unresolved_fields, review_reasons, old_value, new_value,
          reason, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            review_item_id,
            candidate_id,
            session_id,
            review_status,
            json_dumps(unresolved_fields),
            json_dumps([reason]),
            old_title,
            new_title,
            reason,
            now,
            now,
        ),
    )

    existing_queue = conn.execute(
        """
        SELECT id
        FROM review_queue
        WHERE candidate_id = ?
          AND status = 'open'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (candidate_id,),
    ).fetchone()
    if existing_queue:
        conn.execute(
            """
            UPDATE review_queue
            SET review_type = 'source_change_warning',
                priority = 1,
                reason = ?,
                fields = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (reason, json_dumps(unresolved_fields), now, existing_queue["id"]),
        )
    else:
        conn.execute(
            """
            INSERT INTO review_queue (
              id, candidate_id, review_type, priority, reason, fields,
              status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id(),
                candidate_id,
                "source_change_warning",
                1,
                reason,
                json_dumps(unresolved_fields),
                "open",
                now,
                now,
            ),
        )
    audit(conn, "review_item", review_item_id, "source_change_warning_created", {"old_title": old_title, "new_title": new_title})


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
        if existing["review_status"] == "approved":
            maybe_create_source_change_warning(
                conn,
                candidate_id,
                existing["id"],
                text(existing["raw_calendar_title"]),
                text(latest["event_title"]),
            )
            audit(
                conn,
                "session",
                existing["id"],
                "approved_session_protected_from_calendar_replay",
                {
                    "source_raw_snapshot_id": latest["id"],
                    "calendar_event_title": latest["event_title"],
                },
            )
            return True
        conn.execute(
            """
            UPDATE sessions
            SET proposed_client_name = ?,
                session_date = ?,
                start_at = ?,
                end_at = ?,
                calendar_duration_minutes = ?,
                parsed_duration_minutes = ?,
                duration_minutes = ?,
                service_mode = ?,
                rate_group = ?,
                time_category = ?,
                is_evening = ?,
                is_weekend = ?,
                suggested_rate_cents = ?,
                rate_rule_id = ?,
                rate_source = ?,
                rate_needs_review = ?,
                source_raw_snapshot_id = ?,
                raw_calendar_title = ?,
                review_status = ?,
                appointment_status = ?,
                billing_treatment = CASE
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
                billing_session_type = ?,
                appointment_method = ?,
                duration_choice = ?,
                custom_duration_minutes = ?,
                house_call_suggested = ?,
                billing_type_source = ?,
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


def maybe_exclude_pending_session(
    conn: sqlite3.Connection,
    candidate_id: str,
    latest: sqlite3.Row,
    result: ParseResult,
) -> bool:
    existing = conn.execute(
        """
        SELECT *
        FROM sessions
        WHERE candidate_id = ?
        """,
        (candidate_id,),
    ).fetchone()
    if not existing:
        return False
    if existing["review_status"] == "approved":
        audit(
            conn,
            "session",
            existing["id"],
            "approved_session_protected_from_calendar_replay",
            {
                "source_raw_snapshot_id": latest["id"],
                "latest_classification": result.classification,
            },
        )
        return False
    now = now_iso()
    reason = (
        "Latest calendar evidence is not an eligible client session; "
        "pending operational session excluded from billing surfaces."
    )
    conn.execute(
        """
        UPDATE sessions
        SET review_status = 'excluded',
            billable_status = 'excluded',
            source_raw_snapshot_id = ?,
            raw_calendar_title = ?,
            calendar_name = ?,
            calendar_disposition = COALESCE(
              (SELECT calendar_disposition FROM calendar_event_candidates WHERE id = ?),
              calendar_disposition
            ),
            hidden_from_review = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            latest["id"],
            latest["event_title"],
            latest["calendar_name"],
            candidate_id,
            1,
            now,
            existing["id"],
        ),
    )
    conn.execute("DELETE FROM session_participants WHERE session_id = ?", (existing["id"],))
    maybe_insert_review_item(
        conn,
        candidate_id,
        existing["id"],
        result,
        result.unresolved_fields or ["classification"],
        "excluded",
    )
    audit(
        conn,
        "session",
        existing["id"],
        "excluded_from_latest_calendar_snapshot",
        {
            "reason": reason,
            "source_raw_snapshot_id": latest["id"],
            "latest_classification": result.classification,
        },
    )
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
    existing_review = conn.execute(
        """
        SELECT review_item_id
        FROM review_items
        WHERE candidate_id = ?
          AND reviewed_at IS NULL
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (candidate_id,),
    ).fetchone()
    if existing_review:
        conn.execute(
            """
            UPDATE review_items
            SET session_id = ?,
                review_status = ?,
                unresolved_fields = ?,
                review_reasons = ?,
                updated_at = ?
            WHERE review_item_id = ?
            """,
            (
                session_id,
                review_status,
                json_dumps(unresolved_fields),
                json_dumps(result.review_reasons or [result.explanation]),
                now,
                existing_review["review_item_id"],
            ),
        )
        audit(conn, "review_item", existing_review["review_item_id"], "updated", result.as_dict())
        return

    existing_queue = conn.execute(
        """
        SELECT id
        FROM review_queue
        WHERE candidate_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (candidate_id,),
    ).fetchone()
    if existing_queue:
        review_id = existing_queue["id"]
        conn.execute(
            """
            UPDATE review_queue
            SET review_type = ?,
                priority = ?,
                reason = ?,
                fields = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (review_type, priority, result.explanation, json_dumps(unresolved_fields), now, review_id),
        )
    else:
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
