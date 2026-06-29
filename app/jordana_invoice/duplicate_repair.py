from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .db import _backup_sqlite_database, is_operational_db_path
from .util import json_dumps, new_id, now_iso


APPLIED_REVIEW_SOURCE = "duplicate_repair"
APPLIED_REVIEW_REASON = "Reconciled as duplicate of canonical candidate."


@dataclass(frozen=True)
class DuplicateRecord:
    candidate_id: str
    session_id: str | None
    candidate_created_at: str
    session_created_at: str | None
    review_status: str
    has_invoice: bool
    has_payment: bool

    @property
    def is_approved(self) -> bool:
        return self.review_status == "approved"

    @property
    def is_protected(self) -> bool:
        return self.is_approved or self.has_invoice or self.has_payment


@dataclass
class DuplicateGroupPlan:
    canonical: DuplicateRecord
    duplicates: list[DuplicateRecord] = field(default_factory=list)
    manual_review: list[DuplicateRecord] = field(default_factory=list)


BackupFactory = Callable[[Path], Path]


def dry_run_duplicate_repair(conn: sqlite3.Connection) -> dict[str, int]:
    return duplicate_repair_plan(conn, apply=False)["summary"]


def duplicate_repair_plan(
    conn: sqlite3.Connection,
    *,
    apply: bool = False,
    confirm: bool = False,
    backup_factory: BackupFactory | None = None,
) -> dict[str, Any]:
    groups = _duplicate_groups(conn)
    plans = [_plan_group(group) for group in groups]
    summary = _summary(plans)
    if apply:
        if not confirm:
            raise ValueError("Apply requires explicit confirmation.")
        _backup_if_operational(conn, backup_factory=backup_factory)
        applied_summary = _apply_plans(conn, plans)
        conn.commit()
        summary.update(applied_summary)
    return {"summary": summary, "groups": plans}


def reverse_duplicate_repair(
    conn: sqlite3.Connection,
    *,
    confirm: bool = False,
    backup_factory: BackupFactory | None = None,
) -> dict[str, int]:
    if not confirm:
        raise ValueError("Reversal requires explicit confirmation.")
    _backup_if_operational(conn, backup_factory=backup_factory)
    summary = _reverse_applied(conn)
    conn.commit()
    return summary


def _duplicate_groups(conn: sqlite3.Connection) -> list[list[DuplicateRecord]]:
    duplicate_keys = conn.execute(
        """
        WITH keyed AS (
          SELECT
            c.id AS candidate_id,
            s.id AS session_id,
            c.created_at AS candidate_created_at,
            s.created_at AS session_created_at,
            COALESCE(s.review_status, c.review_status) AS review_status,
            lower(trim(COALESCE(c.title, ''))) AS norm_title,
            c.start_at,
            c.end_at,
            COALESCE(c.calendar_duration_minutes, -1) AS duration_minutes,
            lower(trim(COALESCE(c.calendar_name, ''))) AS norm_calendar
          FROM calendar_event_candidates c
          LEFT JOIN sessions s ON s.candidate_id = c.id
          WHERE c.start_at IS NOT NULL
            AND c.end_at IS NOT NULL
            AND c.calendar_duration_minutes IS NOT NULL
            AND COALESCE(c.title, '') != ''
            AND COALESCE(c.calendar_name, '') != ''
            AND NOT EXISTS (
              SELECT 1 FROM candidate_duplicate_reconciliations r
              WHERE r.duplicate_candidate_id = c.id
                AND r.status = 'applied'
            )
        )
        SELECT norm_title, start_at, end_at, duration_minutes, norm_calendar
        FROM keyed
        GROUP BY norm_title, start_at, end_at, duration_minutes, norm_calendar
        HAVING COUNT(DISTINCT candidate_id) > 1
        """
    ).fetchall()
    groups: list[list[DuplicateRecord]] = []
    for key in duplicate_keys:
        rows = conn.execute(
            """
            SELECT
              c.id AS candidate_id,
              s.id AS session_id,
              c.created_at AS candidate_created_at,
              s.created_at AS session_created_at,
              COALESCE(s.review_status, c.review_status) AS review_status,
              EXISTS (
                SELECT 1 FROM invoice_line_items li
                WHERE li.source_session_id = s.id
              ) AS has_invoice,
              EXISTS (
                SELECT 1 FROM payment_allocations pa
                WHERE pa.session_id = s.id
              ) AS has_payment
            FROM calendar_event_candidates c
            LEFT JOIN sessions s ON s.candidate_id = c.id
            WHERE lower(trim(COALESCE(c.title, ''))) = ?
              AND c.start_at = ?
              AND c.end_at = ?
              AND COALESCE(c.calendar_duration_minutes, -1) = ?
              AND lower(trim(COALESCE(c.calendar_name, ''))) = ?
              AND NOT EXISTS (
                SELECT 1 FROM candidate_duplicate_reconciliations r
                WHERE r.duplicate_candidate_id = c.id
                  AND r.status = 'applied'
              )
            ORDER BY c.created_at, c.id
            """,
            (
                key["norm_title"],
                key["start_at"],
                key["end_at"],
                key["duration_minutes"],
                key["norm_calendar"],
            ),
        ).fetchall()
        groups.append(
            [
                DuplicateRecord(
                    candidate_id=row["candidate_id"],
                    session_id=row["session_id"],
                    candidate_created_at=row["candidate_created_at"],
                    session_created_at=row["session_created_at"],
                    review_status=row["review_status"],
                    has_invoice=bool(row["has_invoice"]),
                    has_payment=bool(row["has_payment"]),
                )
                for row in rows
            ]
        )
    return groups


def _plan_group(records: list[DuplicateRecord]) -> DuplicateGroupPlan:
    canonical = sorted(records, key=_canonical_sort_key)[0]
    plan = DuplicateGroupPlan(canonical=canonical)
    for record in records:
        if record.candidate_id == canonical.candidate_id:
            continue
        if record.is_protected:
            plan.manual_review.append(record)
        else:
            plan.duplicates.append(record)
    return plan


def _canonical_sort_key(record: DuplicateRecord) -> tuple[int, int, str, str]:
    protected_rank = 0 if record.has_invoice else 1 if record.is_approved else 2
    created = record.session_created_at or record.candidate_created_at or ""
    return (protected_rank, 0 if record.session_id else 1, created, record.candidate_id)


def _summary(plans: list[DuplicateGroupPlan]) -> dict[str, int]:
    duplicate_records = [record for plan in plans for record in plan.duplicates]
    manual_records = [record for plan in plans for record in plan.manual_review]
    protected_records = [plan.canonical for plan in plans if plan.canonical.is_protected] + [
        record for record in manual_records if record.is_protected
    ]
    return {
        "groups_detected": len(plans),
        "canonical_records_selected": len(plans),
        "unapproved_duplicate_candidates_proposed": len(duplicate_records),
        "unapproved_duplicate_sessions_proposed": sum(1 for record in duplicate_records if record.session_id),
        "review_items_proposed_for_closure": len(duplicate_records),
        "protected_approved_records": sum(1 for record in protected_records if record.is_approved),
        "protected_invoiced_records": sum(1 for record in protected_records if record.has_invoice),
        "protected_paid_records": sum(1 for record in protected_records if record.has_payment),
        "ambiguous_groups_requiring_manual_review": sum(1 for plan in plans if plan.manual_review),
    }


def _apply_plans(conn: sqlite3.Connection, plans: list[DuplicateGroupPlan]) -> dict[str, int]:
    summary = {"reconciliations_applied": 0, "already_applied": 0}
    now = now_iso()
    for plan in plans:
        for duplicate in plan.duplicates:
            if duplicate.is_protected:
                raise ValueError("Refused to modify a protected duplicate record.")
            existing = conn.execute(
                """
                SELECT status
                FROM candidate_duplicate_reconciliations
                WHERE duplicate_candidate_id = ?
                """,
                (duplicate.candidate_id,),
            ).fetchone()
            if existing and existing["status"] == "applied":
                summary["already_applied"] += 1
                continue
            if existing and existing["status"] != "reversed":
                raise ValueError("Refused to apply over an unresolved reconciliation record.")
            original_state = _capture_state(conn, duplicate.candidate_id)
            applied_state = _applied_state(original_state)
            if existing and existing["status"] == "reversed":
                conn.execute(
                    """
                    UPDATE candidate_duplicate_reconciliations
                    SET canonical_candidate_id = ?,
                        canonical_session_id = ?,
                        duplicate_session_id = ?,
                        status = 'applied',
                        reason = ?,
                        original_state_json = ?,
                        applied_state_json = ?,
                        applied_at = ?,
                        reversed_at = NULL,
                        updated_at = ?
                    WHERE duplicate_candidate_id = ? AND status = 'reversed'
                    """,
                    (
                        plan.canonical.candidate_id,
                        plan.canonical.session_id,
                        duplicate.session_id,
                        "exact_structural_duplicate",
                        json_dumps(original_state),
                        json_dumps(applied_state),
                        now,
                        now,
                        duplicate.candidate_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO candidate_duplicate_reconciliations (
                      reconciliation_id, canonical_candidate_id, duplicate_candidate_id,
                      canonical_session_id, duplicate_session_id, status, reason,
                      original_state_json, applied_state_json, applied_at,
                      created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'applied', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id(),
                        plan.canonical.candidate_id,
                        duplicate.candidate_id,
                        plan.canonical.session_id,
                        duplicate.session_id,
                        "exact_structural_duplicate",
                        json_dumps(original_state),
                        json_dumps(applied_state),
                        now,
                        now,
                        now,
                    ),
                )
            _write_applied_state(conn, duplicate.candidate_id, now)
            _audit_once(
                conn,
                "calendar_event_candidate",
                duplicate.candidate_id,
                "duplicate_reconciliation_applied",
                {
                    "canonical_candidate_id": plan.canonical.candidate_id,
                    "canonical_session_id": plan.canonical.session_id,
                    "duplicate_session_id": duplicate.session_id,
                    "reason": "exact_structural_duplicate",
                },
            )
            summary["reconciliations_applied"] += 1
    return summary


def _write_applied_state(conn: sqlite3.Connection, duplicate_candidate_id: str, now: str) -> None:
    conn.execute(
        """
        UPDATE calendar_event_candidates
        SET reconciliation_status = 'reconciled_duplicate',
            review_status = 'excluded',
            updated_at = ?
        WHERE id = ? AND review_status != 'approved'
        """,
        (now, duplicate_candidate_id),
    )
    conn.execute(
        """
        UPDATE sessions
        SET review_status = 'excluded',
            billable_status = 'excluded',
            updated_at = ?
        WHERE candidate_id = ? AND review_status != 'approved'
        """,
        (now, duplicate_candidate_id),
    )
    conn.execute(
        """
        UPDATE review_items
        SET review_status = 'excluded',
            decision_source = COALESCE(decision_source, ?),
            reason = COALESCE(reason, ?),
            updated_at = ?
        WHERE candidate_id = ? AND review_status != 'approved'
        """,
        (APPLIED_REVIEW_SOURCE, APPLIED_REVIEW_REASON, now, duplicate_candidate_id),
    )


def _reverse_applied(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT *
        FROM candidate_duplicate_reconciliations
        WHERE status = 'applied'
        ORDER BY created_at, reconciliation_id
        """
    ).fetchall()
    summary = {
        "applied_reconciliations_found": len(rows),
        "reconciliations_reversed": 0,
        "already_reversed": 0,
        "unsafe_reversals_refused": 0,
    }
    now = now_iso()
    for row in rows:
        original_state = _loads_state(row["original_state_json"])
        applied_state = _loads_state(row["applied_state_json"])
        current_state = _capture_state(conn, row["duplicate_candidate_id"])
        if not _state_matches_applied(current_state, applied_state):
            summary["unsafe_reversals_refused"] += 1
            continue
        _restore_state(conn, original_state)
        conn.execute(
            """
            UPDATE candidate_duplicate_reconciliations
            SET status = 'reversed',
                reversed_at = ?,
                updated_at = ?
            WHERE reconciliation_id = ? AND status = 'applied'
            """,
            (now, now, row["reconciliation_id"]),
        )
        _audit_once(
            conn,
            "calendar_event_candidate",
            row["duplicate_candidate_id"],
            "duplicate_reconciliation_reversed",
            {
                "canonical_candidate_id": row["canonical_candidate_id"],
                "canonical_session_id": row["canonical_session_id"],
                "duplicate_session_id": row["duplicate_session_id"],
                "reason": "safe_reversal",
            },
        )
        summary["reconciliations_reversed"] += 1
    return summary


def _capture_state(conn: sqlite3.Connection, duplicate_candidate_id: str) -> dict[str, Any]:
    candidate = conn.execute(
        """
        SELECT id, review_status, reconciliation_status, updated_at
        FROM calendar_event_candidates
        WHERE id = ?
        """,
        (duplicate_candidate_id,),
    ).fetchone()
    if candidate is None:
        raise ValueError("Duplicate candidate not found.")
    session = conn.execute(
        """
        SELECT id, review_status, billable_status, updated_at
        FROM sessions
        WHERE candidate_id = ?
        """,
        (duplicate_candidate_id,),
    ).fetchone()
    review_items = conn.execute(
        """
        SELECT review_item_id, review_status, decision_source, reason, updated_at
        FROM review_items
        WHERE candidate_id = ?
        ORDER BY review_item_id
        """,
        (duplicate_candidate_id,),
    ).fetchall()
    return {
        "candidate": dict(candidate),
        "session": dict(session) if session else None,
        "review_items": [dict(row) for row in review_items],
    }


def _applied_state(original_state: dict[str, Any]) -> dict[str, Any]:
    candidate = dict(original_state["candidate"])
    candidate["review_status"] = "excluded"
    candidate["reconciliation_status"] = "reconciled_duplicate"
    candidate["updated_at"] = "__repair_timestamp__"
    session = None
    if original_state["session"]:
        session = dict(original_state["session"])
        session["review_status"] = "excluded"
        session["billable_status"] = "excluded"
        session["updated_at"] = "__repair_timestamp__"
    review_items = []
    for item in original_state["review_items"]:
        next_item = dict(item)
        next_item["review_status"] = "excluded"
        next_item["decision_source"] = next_item["decision_source"] or APPLIED_REVIEW_SOURCE
        next_item["reason"] = next_item["reason"] or APPLIED_REVIEW_REASON
        next_item["updated_at"] = "__repair_timestamp__"
        review_items.append(next_item)
    return {"candidate": candidate, "session": session, "review_items": review_items}


def _loads_state(value: str | None) -> dict[str, Any]:
    if not value:
        raise ValueError("Reconciliation is missing reversal state.")
    return json.loads(value)


def _state_matches_applied(current: dict[str, Any], applied: dict[str, Any]) -> bool:
    if not _candidate_matches(current["candidate"], applied["candidate"]):
        return False
    if bool(current["session"]) != bool(applied["session"]):
        return False
    if current["session"] and not _session_matches(current["session"], applied["session"]):
        return False
    current_items = {item["review_item_id"]: item for item in current["review_items"]}
    applied_items = {item["review_item_id"]: item for item in applied["review_items"]}
    if current_items.keys() != applied_items.keys():
        return False
    return all(_review_item_matches(current_items[key], applied_items[key]) for key in applied_items)


def _candidate_matches(current: dict[str, Any], applied: dict[str, Any]) -> bool:
    return (
        current["review_status"] == applied["review_status"]
        and current["reconciliation_status"] == applied["reconciliation_status"]
    )


def _session_matches(current: dict[str, Any], applied: dict[str, Any]) -> bool:
    return (
        current["review_status"] == applied["review_status"]
        and current["billable_status"] == applied["billable_status"]
    )


def _review_item_matches(current: dict[str, Any], applied: dict[str, Any]) -> bool:
    return (
        current["review_status"] == applied["review_status"]
        and current["decision_source"] == applied["decision_source"]
        and current["reason"] == applied["reason"]
    )


def _restore_state(conn: sqlite3.Connection, original_state: dict[str, Any]) -> None:
    candidate = original_state["candidate"]
    conn.execute(
        """
        UPDATE calendar_event_candidates
        SET review_status = ?,
            reconciliation_status = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (candidate["review_status"], candidate["reconciliation_status"], candidate["updated_at"], candidate["id"]),
    )
    if original_state["session"]:
        session = original_state["session"]
        conn.execute(
            """
            UPDATE sessions
            SET review_status = ?,
                billable_status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (session["review_status"], session["billable_status"], session["updated_at"], session["id"]),
        )
    for item in original_state["review_items"]:
        conn.execute(
            """
            UPDATE review_items
            SET review_status = ?,
                decision_source = ?,
                reason = ?,
                updated_at = ?
            WHERE review_item_id = ?
            """,
            (
                item["review_status"],
                item["decision_source"],
                item["reason"],
                item["updated_at"],
                item["review_item_id"],
            ),
        )


def _audit_once(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    action: str,
    details: object,
) -> None:
    existing = conn.execute(
        """
        SELECT 1 FROM audit_log
        WHERE entity_type = ? AND entity_id = ? AND action = ?
        LIMIT 1
        """,
        (entity_type, entity_id, action),
    ).fetchone()
    if existing:
        return
    conn.execute(
        """
        INSERT INTO audit_log (id, entity_type, entity_id, action, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (new_id(), entity_type, entity_id, action, json_dumps(details), now_iso()),
    )


def _backup_if_operational(conn: sqlite3.Connection, *, backup_factory: BackupFactory | None = None) -> None:
    db_path = _main_db_path(conn)
    if db_path is None or not is_operational_db_path(db_path):
        return
    backup_factory = backup_factory or _create_private_backup
    backup_path = backup_factory(db_path)
    _verify_backup(backup_path)


def _main_db_path(conn: sqlite3.Connection) -> Path | None:
    for row in conn.execute("PRAGMA database_list").fetchall():
        if row[1] == "main" and row[2]:
            return Path(row[2])
    return None


def _create_private_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "private" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"{db_path.stem}.duplicate-repair-backup-{timestamp}{db_path.suffix}"
    counter = 1
    while backup_path.exists():
        backup_path = backup_dir / f"{db_path.stem}.duplicate-repair-backup-{timestamp}-{counter}{db_path.suffix}"
        counter += 1
    _backup_sqlite_database(db_path, backup_path)
    return backup_path


def _verify_backup(backup_path: Path) -> None:
    test_conn = sqlite3.connect(str(backup_path))
    try:
        integrity = test_conn.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise ValueError("Backup integrity check failed.")
        fk_rows = test_conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_rows:
            raise ValueError("Backup foreign key check failed.")
    finally:
        test_conn.close()
