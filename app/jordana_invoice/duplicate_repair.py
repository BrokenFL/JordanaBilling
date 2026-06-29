from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .util import json_dumps, new_id, now_iso


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


def dry_run_duplicate_repair(conn: sqlite3.Connection) -> dict[str, int]:
    return duplicate_repair_plan(conn, apply=False)["summary"]


def duplicate_repair_plan(
    conn: sqlite3.Connection,
    *,
    apply: bool = False,
    confirm: bool = False,
) -> dict[str, Any]:
    groups = _duplicate_groups(conn)
    plans = [_plan_group(group) for group in groups]
    summary = _summary(plans)
    if apply:
        if not confirm:
            raise ValueError("Apply requires explicit confirmation.")
        _apply_plans(conn, plans)
        conn.commit()
    return {"summary": summary, "groups": plans}


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


def _apply_plans(conn: sqlite3.Connection, plans: list[DuplicateGroupPlan]) -> None:
    now = now_iso()
    for plan in plans:
        for duplicate in plan.duplicates:
            if duplicate.is_protected:
                raise ValueError("Refused to modify a protected duplicate record.")
            conn.execute(
                """
                INSERT OR IGNORE INTO candidate_duplicate_reconciliations (
                  reconciliation_id, canonical_candidate_id, duplicate_candidate_id,
                  canonical_session_id, duplicate_session_id, status, reason,
                  applied_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'applied', ?, ?, ?, ?)
                """,
                (
                    new_id(),
                    plan.canonical.candidate_id,
                    duplicate.candidate_id,
                    plan.canonical.session_id,
                    duplicate.session_id,
                    "exact_structural_duplicate",
                    now,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                UPDATE calendar_event_candidates
                SET reconciliation_status = 'reconciled_duplicate',
                    review_status = CASE WHEN review_status = 'approved' THEN review_status ELSE 'excluded' END,
                    updated_at = ?
                WHERE id = ? AND review_status != 'approved'
                """,
                (now, duplicate.candidate_id),
            )
            if duplicate.session_id:
                conn.execute(
                    """
                    UPDATE sessions
                    SET review_status = CASE WHEN review_status = 'approved' THEN review_status ELSE 'excluded' END,
                        billable_status = CASE WHEN review_status = 'approved' THEN billable_status ELSE 'excluded' END,
                        updated_at = ?
                    WHERE id = ? AND review_status != 'approved'
                    """,
                    (now, duplicate.session_id),
                )
            conn.execute(
                """
                UPDATE review_items
                SET review_status = 'excluded',
                    decision_source = COALESCE(decision_source, 'duplicate_repair'),
                    reason = COALESCE(reason, 'Reconciled as duplicate of canonical candidate.'),
                    updated_at = ?
                WHERE candidate_id = ? AND review_status != 'approved'
                """,
                (now, duplicate.candidate_id),
            )
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
