from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .calendar_identity import (
    canonical_event_slot_parts,
    canonical_structural_parts,
    has_complete_event_slot_identity,
    has_complete_structural_identity,
    utc_datetime,
)
from .calendar_warnings import resolve_calendar_warning, upsert_calendar_warning
from .capture_windows import is_past_capture_window
from .db import _get_db_path_from_conn, is_operational_db_path
from .invoice_services import _recalculate, calendar_source_identity_for_session
from .util import json_dumps, new_id, now_iso


LEGACY_SUPPRESSION_STATUS = "removed_from_newest_covering_snapshot"
RESTORED_SUPPRESSION_STATUS = "restored_from_legacy_calendar_suppression"
QUARANTINED_DUPLICATE_STATUS = "reconciled_legacy_calendar_duplicate"
RESTORED_WARNING = "legacy_calendar_suppression_restored"
CONFLICT_WARNING = "legacy_calendar_duration_conflict"

RESTORE_ACTION = "restore_legacy_calendar_suppression"
QUARANTINE_ACTION = "quarantine_legacy_calendar_duplicate"
CONFLICT_ACTION = "flag_legacy_calendar_duration_conflict"
DRAFT_LINE_ACTION = "remove_exact_duplicate_draft_line"

BackupFactory = Callable[[Path], Path]


@dataclass(frozen=True)
class CalendarRecoveryAction:
    action_key: str
    action_type: str
    reason: str
    candidate_id: str | None = None
    session_id: str | None = None
    invoice_id: str | None = None
    invoice_line_item_id: str | None = None
    original_state: dict[str, Any] | None = None


def calendar_recovery_plan(
    conn: sqlite3.Connection,
    *,
    apply: bool = False,
    confirm: bool = False,
    month: str | None = None,
    backup_factory: BackupFactory | None = None,
) -> dict[str, Any]:
    """Plan or apply the narrowly-scoped legacy calendar correction.

    A record is restored only if preserved raw evidence contains an
    offset-aware past-capture observation made after the appointment ended.
    Legacy future-only records remain untouched because their later absence
    may legitimately represent a reschedule or cancellation. Nothing is
    approved or invoiced by this recovery.
    """

    month = _validate_month(month)
    if apply and not month:
        raise ValueError("Apply requires a YYYY-MM billing-month scope.")
    actions, skipped = _build_actions(conn, month=month)
    summary = _summary(actions, skipped)
    summary["month"] = month or "all"
    if not apply:
        return {"summary": summary, "actions": actions, "backup_created": False}
    if not confirm:
        raise ValueError("Apply requires explicit confirmation.")
    if not _action_table_exists(conn):
        raise ValueError("Calendar recovery storage is unavailable; run the additive database migration first.")

    backup_created = _backup_if_operational(conn, backup_factory=backup_factory)
    applied = Counter()
    conn.execute("BEGIN IMMEDIATE")
    try:
        for action in actions:
            if _existing_action_status(conn, action.action_key) == "applied":
                applied["already_applied"] += 1
                continue
            applied_state = _apply_action(conn, action)
            _write_action(conn, action, applied_state)
            applied[action.action_type] += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    summary.update({
        "actions_applied": sum(value for key, value in applied.items() if key != "already_applied"),
        "already_applied": applied["already_applied"],
        "candidates_restored": applied[RESTORE_ACTION],
        "duplicate_candidates_quarantined": applied[QUARANTINE_ACTION],
        "ambiguous_groups_flagged": applied[CONFLICT_ACTION],
        "draft_duplicate_lines_removed": applied[DRAFT_LINE_ACTION],
    })
    return {"summary": summary, "actions": actions, "backup_created": backup_created}


def reverse_calendar_recovery(
    conn: sqlite3.Connection,
    *,
    confirm: bool = False,
    backup_factory: BackupFactory | None = None,
) -> dict[str, int | bool]:
    """Reverse only unchanged actions previously applied by this recovery."""

    if not confirm:
        raise ValueError("Reversal requires explicit confirmation.")
    if not _action_table_exists(conn):
        return {
            "applied_actions_found": 0,
            "actions_reversed": 0,
            "unsafe_reversals_refused": 0,
            "backup_created": False,
        }
    rows = conn.execute(
        """
        SELECT rowid, *
        FROM calendar_recovery_actions
        WHERE status = 'applied'
        ORDER BY rowid DESC
        """
    ).fetchall()
    if not rows:
        return {
            "applied_actions_found": 0,
            "actions_reversed": 0,
            "unsafe_reversals_refused": 0,
            "backup_created": False,
        }

    backup_created = _backup_if_operational(conn, backup_factory=backup_factory)
    summary = {
        "applied_actions_found": len(rows),
        "actions_reversed": 0,
        "unsafe_reversals_refused": 0,
        "backup_created": backup_created,
    }
    conn.execute("BEGIN IMMEDIATE")
    try:
        for row in rows:
            if _reverse_action(conn, row):
                summary["actions_reversed"] += 1
            else:
                summary["unsafe_reversals_refused"] += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return summary


def _build_actions(
    conn: sqlite3.Connection,
    *,
    month: str | None,
) -> tuple[list[CalendarRecoveryAction], Counter[str]]:
    skipped: Counter[str] = Counter()
    actions = _legacy_suppression_actions(conn, skipped, month=month)
    actions.extend(_draft_duplicate_line_actions(conn, skipped, month=month))
    available: list[CalendarRecoveryAction] = []
    for action in actions:
        if _existing_action_status(conn, action.action_key) == "applied":
            skipped["already_applied"] += 1
            continue
        available.append(action)
    return available, skipped


def _legacy_suppression_actions(
    conn: sqlite3.Connection,
    skipped: Counter[str],
    *,
    month: str | None,
) -> list[CalendarRecoveryAction]:
    positive_evidence = _post_end_past_evidence_counts(conn)
    month_clause = ""
    params: list[Any] = [LEGACY_SUPPRESSION_STATUS]
    if month:
        month_clause = " AND substr(COALESCE(c.proposed_start_at, c.start_at), 1, 7) = ?"
        params.append(month)
    candidates = conn.execute(
        f"""
        SELECT c.id, c.title, c.start_at, c.end_at, c.calendar_duration_minutes,
               c.calendar_name, c.classification, c.review_status,
               c.hidden_from_review, c.reconciliation_status, c.updated_at,
               s.id AS session_id, s.review_status AS session_review_status,
               s.billable_status, s.payment_status, s.billing_treatment,
               s.hidden_from_review AS session_hidden_from_review,
               s.updated_at AS session_updated_at, c.created_at
        FROM calendar_event_candidates c
        JOIN sessions s ON s.candidate_id = c.id
        WHERE c.classification = 'client_session'
          AND c.reconciliation_status = ?
          {month_clause}
          AND c.review_status = 'excluded'
          AND COALESCE(c.hidden_from_review, 0) = 1
          AND s.review_status = 'excluded'
          AND s.billable_status = 'excluded'
          AND COALESCE(s.hidden_from_review, 0) = 1
          AND COALESCE(s.payment_status, 'unpaid') = 'unpaid'
          AND NOT EXISTS (
            SELECT 1 FROM invoice_line_items li WHERE li.source_session_id = s.id
          )
          AND NOT EXISTS (
            SELECT 1 FROM payment_allocations pa WHERE pa.session_id = s.id
          )
        ORDER BY c.created_at, c.id
        """,
        tuple(params),
    ).fetchall()

    eligible = []
    for row in candidates:
        if not has_complete_structural_identity(row):
            skipped["legacy_candidates_without_complete_identity"] += 1
            continue
        structural_key = "|".join(canonical_structural_parts(row))
        if not positive_evidence.get(structural_key):
            skipped["legacy_candidates_without_post_end_past_evidence"] += 1
            continue
        eligible.append(row)

    aliases = _calendar_event_id_aliases(conn, [row["id"] for row in eligible])
    by_slot: dict[tuple[str, str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in eligible:
        if not has_complete_event_slot_identity(row):
            # This cannot happen after the complete structural guard, but
            # retain the defensive branch so a malformed legacy row is never
            # silently restored.
            skipped["legacy_candidates_without_complete_slot_identity"] += 1
            continue
        by_slot[canonical_event_slot_parts(row)].append(row)

    actions: list[CalendarRecoveryAction] = []
    for group in by_slot.values():
        if len(group) == 1:
            actions.append(_restore_action(group[0]))
            continue
        source_ids = set().union(*(aliases.get(row["id"], set()) for row in group))
        structural_keys = {"|".join(canonical_structural_parts(row)) for row in group}
        if len(source_ids) > 1:
            # Distinct stable IDs prove distinct events even if their slots
            # coincide, so each remains independently reviewable.
            actions.extend(_restore_action(row) for row in group)
            continue
        if len(structural_keys) > 1:
            # No stable ID and conflicting end/duration: do not choose a
            # session or make it billable. The Review queue gets a focused
            # warning while legacy exclusion remains in place.
            actions.extend(_conflict_action(row) for row in group)
            continue
        canonical = min(group, key=lambda row: (row["created_at"] or "", row["id"]))
        actions.append(_restore_action(canonical))
        actions.extend(_quarantine_action(row) for row in group if row["id"] != canonical["id"])
    return actions


def _post_end_past_evidence_counts(conn: sqlite3.Connection) -> Counter[str]:
    rows = conn.execute(
        """
        SELECT event_title, start_at, end_at, duration_minutes, calendar_name,
               capture_window, captured_at
        FROM raw_calendar_snapshots
        """
    ).fetchall()
    counts: Counter[str] = Counter()
    for row in rows:
        captured_at = utc_datetime(row["captured_at"])
        end_at = utc_datetime(row["end_at"])
        if not (
            is_past_capture_window(row["capture_window"])
            and captured_at
            and end_at
            and captured_at >= end_at
            and has_complete_structural_identity(row)
        ):
            continue
        counts["|".join(canonical_structural_parts(row))] += 1
    return counts


def _calendar_event_id_aliases(
    conn: sqlite3.Connection,
    candidate_ids: list[str],
) -> dict[str, set[str]]:
    values: dict[str, set[str]] = defaultdict(set)
    if not candidate_ids:
        return values
    placeholders = ", ".join("?" for _ in candidate_ids)
    rows = conn.execute(
        f"""
        SELECT candidate_id, alias_value
        FROM candidate_identity_aliases
        WHERE alias_type = 'calendar_event_id'
          AND candidate_id IN ({placeholders})
        """,
        tuple(candidate_ids),
    ).fetchall()
    for row in rows:
        values[row["candidate_id"]].add(row["alias_value"])
    return values


def _restore_action(row: sqlite3.Row) -> CalendarRecoveryAction:
    return CalendarRecoveryAction(
        action_key=f"restore:{row['id']}",
        action_type=RESTORE_ACTION,
        reason="Post-end past-calendar evidence survived legacy absence suppression.",
        candidate_id=row["id"],
        session_id=row["session_id"],
        original_state=_candidate_state_from_row(row),
    )


def _quarantine_action(row: sqlite3.Row) -> CalendarRecoveryAction:
    return CalendarRecoveryAction(
        action_key=f"quarantine:{row['id']}",
        action_type=QUARANTINE_ACTION,
        reason="Exact legacy offset duplicate of the restored calendar candidate.",
        candidate_id=row["id"],
        session_id=row["session_id"],
        original_state=_candidate_state_from_row(row),
    )


def _conflict_action(row: sqlite3.Row) -> CalendarRecoveryAction:
    return CalendarRecoveryAction(
        action_key=f"duration-conflict:{row['id']}",
        action_type=CONFLICT_ACTION,
        reason="Legacy source observations disagree about duration or end time.",
        candidate_id=row["id"],
        session_id=row["session_id"],
        original_state={},
    )


def _draft_duplicate_line_actions(
    conn: sqlite3.Connection,
    skipped: Counter[str],
    *,
    month: str | None,
) -> list[CalendarRecoveryAction]:
    month_clause = ""
    params: tuple[str, ...] = ()
    if month:
        month_clause = " AND substr(li.service_date, 1, 7) = ?"
        params = (month,)
    rows = conn.execute(
        f"""
        SELECT li.*,
               i.status AS invoice_status, i.invoice_number, i.finalized_at,
               i.voided_at, i.pdf_path, i.pdf_sha256,
               i.subtotal_cents AS invoice_subtotal_cents,
               i.adjustment_cents AS invoice_adjustment_cents,
               i.total_cents AS invoice_total_cents,
               i.revision AS invoice_revision,
               i.updated_at AS invoice_updated_at,
               s.id AS session_id, s.candidate_id AS session_candidate_id,
               s.raw_calendar_title, s.calendar_name,
               s.start_at AS session_start_at, s.end_at AS session_end_at,
               s.duration_minutes AS session_duration_minutes,
               EXISTS (
                 SELECT 1 FROM payment_allocations pa
                 WHERE pa.invoice_line_item_id = li.invoice_line_item_id
                    OR pa.session_id = li.source_session_id
               ) AS has_payment
        FROM invoice_line_items li
        JOIN invoices i ON i.invoice_id = li.invoice_id
        JOIN sessions s ON s.id = li.source_session_id
        WHERE i.status = 'draft'
        {month_clause}
        ORDER BY li.invoice_id, li.sort_order, li.created_at, li.invoice_line_item_id
        """
        , params
    ).fetchall()
    groups: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        session = {
            "id": row["session_id"],
            "candidate_id": row["session_candidate_id"],
            "raw_calendar_title": row["raw_calendar_title"],
            "calendar_name": row["calendar_name"],
            "start_at": row["session_start_at"],
            "end_at": row["session_end_at"],
            "duration_minutes": row["session_duration_minutes"],
        }
        source_identity = calendar_source_identity_for_session(conn, session)
        if source_identity:
            groups[(row["invoice_id"], source_identity)].append(row)

    actions: list[CalendarRecoveryAction] = []
    for group in groups.values():
        if len(group) < 2:
            continue
        if not all(_draft_line_is_safe(row) for row in group):
            skipped["draft_duplicate_lines_with_protected_invoice_or_payment"] += len(group) - 1
            continue
        snapshots = {_line_snapshot_key(row) for row in group}
        if len(snapshots) != 1:
            skipped["draft_duplicate_lines_with_conflicting_snapshots"] += len(group) - 1
            continue
        canonical = min(
            group,
            key=lambda row: (row["sort_order"], row["created_at"] or "", row["invoice_line_item_id"]),
        )
        for row in group:
            if row["invoice_line_item_id"] == canonical["invoice_line_item_id"]:
                continue
            actions.append(
                CalendarRecoveryAction(
                    action_key=f"draft-line:{row['invoice_line_item_id']}",
                    action_type=DRAFT_LINE_ACTION,
                    reason="Exact duplicate of a confirmed calendar event in an editable draft.",
                    session_id=row["session_id"],
                    invoice_id=row["invoice_id"],
                    invoice_line_item_id=row["invoice_line_item_id"],
                    original_state={
                        "line": _invoice_line_state(conn, row["invoice_line_item_id"]),
                        "invoice": _invoice_state_from_row(row),
                    },
                )
            )
    return actions


def _draft_line_is_safe(row: sqlite3.Row) -> bool:
    return not any(
        (
            row["invoice_status"] != "draft",
            row["invoice_number"],
            row["finalized_at"],
            row["voided_at"],
            row["pdf_path"],
            row["pdf_sha256"],
            row["has_payment"],
        )
    )


def _line_snapshot_key(row: sqlite3.Row) -> tuple[object, ...]:
    fields = (
        "service_date",
        "participants_snapshot",
        "service_catalog_id",
        "service_name_snapshot",
        "billing_session_type_snapshot",
        "time_category_snapshot",
        "appointment_status_snapshot",
        "billing_treatment_snapshot",
        "scheduled_rate_cents_snapshot",
        "duration_minutes",
        "description_snapshot",
        "custom_service_description_snapshot",
        "custom_service_code_snapshot",
        "quantity",
        "unit_amount_cents",
        "line_amount_cents",
    )
    return tuple(row[field] for field in fields)


def _summary(actions: list[CalendarRecoveryAction], skipped: Counter[str]) -> dict[str, Any]:
    planned = Counter(action.action_type for action in actions)
    return {
        "legacy_candidates_restorable": planned[RESTORE_ACTION],
        "legacy_duplicate_candidates_to_quarantine": planned[QUARANTINE_ACTION],
        "legacy_duration_conflicts_to_flag": planned[CONFLICT_ACTION],
        "draft_duplicate_lines_to_remove": planned[DRAFT_LINE_ACTION],
        "actions_planned": len(actions),
        **{key: int(value) for key, value in sorted(skipped.items())},
    }


def _validate_month(value: str | None) -> str | None:
    month = str(value or "").strip()
    if not month:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError("Month must use YYYY-MM format.")
    return month


def _apply_action(conn: sqlite3.Connection, action: CalendarRecoveryAction) -> dict[str, Any]:
    if action.action_type == RESTORE_ACTION:
        return _apply_restore(conn, action)
    if action.action_type == QUARANTINE_ACTION:
        return _apply_quarantine(conn, action)
    if action.action_type == CONFLICT_ACTION:
        return _apply_conflict_warning(conn, action)
    if action.action_type == DRAFT_LINE_ACTION:
        return _apply_draft_line_removal(conn, action)
    raise ValueError("Unknown calendar recovery action.")


def _apply_restore(conn: sqlite3.Connection, action: CalendarRecoveryAction) -> dict[str, Any]:
    _require_candidate_state(conn, action)
    now = now_iso()
    conn.execute(
        """
        UPDATE calendar_event_candidates
        SET review_status = 'needs_review', hidden_from_review = 0,
            reconciliation_status = ?, updated_at = ?
        WHERE id = ?
        """,
        (RESTORED_SUPPRESSION_STATUS, now, action.candidate_id),
    )
    conn.execute(
        """
        UPDATE sessions
        SET review_status = 'needs_review', billable_status = 'proposed',
            payment_status = 'unpaid', billing_treatment = 'unresolved',
            hidden_from_review = 0, updated_at = ?
        WHERE id = ?
        """,
        (now, action.session_id),
    )
    warning_preexisting = _has_open_warning(conn, action.candidate_id or "", RESTORED_WARNING)
    upsert_calendar_warning(
        conn,
        candidate_id=action.candidate_id or "",
        session_id=action.session_id,
        warning_code=RESTORED_WARNING,
        reason=(
            "A legacy absence rule hid this appointment even though a "
            "post-session calendar capture exists. It was restored to normal Review; "
            "no approval or invoice was created."
        ),
    )
    _record_audit(
        conn,
        action.candidate_id or "",
        "legacy_calendar_suppression_restored",
        {"source": "post_end_past_capture"},
    )
    return {
        "state": _candidate_state(conn, action.candidate_id or ""),
        "warning_created": not warning_preexisting,
    }


def _apply_quarantine(conn: sqlite3.Connection, action: CalendarRecoveryAction) -> dict[str, Any]:
    _require_candidate_state(conn, action)
    now = now_iso()
    conn.execute(
        """
        UPDATE calendar_event_candidates
        SET reconciliation_status = ?, updated_at = ?
        WHERE id = ?
        """,
        (QUARANTINED_DUPLICATE_STATUS, now, action.candidate_id),
    )
    _record_audit(
        conn,
        action.candidate_id or "",
        "legacy_calendar_offset_duplicate_quarantined",
        {"source": "exact_canonical_calendar_identity"},
    )
    return {"state": _candidate_state(conn, action.candidate_id or "")}


def _apply_conflict_warning(conn: sqlite3.Connection, action: CalendarRecoveryAction) -> dict[str, Any]:
    candidate = _candidate_state(conn, action.candidate_id or "")
    if candidate is None:
        raise ValueError("Calendar recovery candidate no longer exists.")
    warning_preexisting = _has_open_warning(conn, action.candidate_id or "", CONFLICT_WARNING)
    upsert_calendar_warning(
        conn,
        candidate_id=action.candidate_id or "",
        session_id=action.session_id,
        warning_code=CONFLICT_WARNING,
        reason=(
            "Preserved calendar observations disagree about this appointment's duration "
            "or end time. No session was restored or billed automatically."
        ),
    )
    _record_audit(
        conn,
        action.candidate_id or "",
        "legacy_calendar_duration_conflict_flagged",
        {},
    )
    return {"warning_created": not warning_preexisting}


def _apply_draft_line_removal(conn: sqlite3.Connection, action: CalendarRecoveryAction) -> dict[str, Any]:
    original = action.original_state or {}
    invoice = _invoice_state(conn, action.invoice_id or "")
    line = _invoice_line_state(conn, action.invoice_line_item_id or "")
    if invoice != original.get("invoice") or line != original.get("line"):
        raise ValueError("Refused to alter a draft line that changed after the recovery plan was created.")
    if not _invoice_is_safe_draft(conn, action.invoice_id or ""):
        raise ValueError("Refused to alter a protected or paid draft invoice.")
    if _invoice_line_has_payment(conn, action.invoice_line_item_id or "", action.session_id or ""):
        raise ValueError("Refused to alter a draft line with payment activity.")
    cursor = conn.execute(
        "DELETE FROM invoice_line_items WHERE invoice_line_item_id = ? AND invoice_id = ?",
        (action.invoice_line_item_id, action.invoice_id),
    )
    if not cursor.rowcount:
        raise ValueError("Draft line no longer exists.")
    _recalculate(conn, action.invoice_id or "")
    conn.execute(
        "UPDATE invoices SET revision = revision + 1, updated_at = ? WHERE invoice_id = ? AND status = 'draft'",
        (now_iso(), action.invoice_id),
    )
    _record_audit(
        conn,
        action.invoice_id or "",
        "exact_duplicate_calendar_draft_line_removed",
        {"invoice_line_item_id": action.invoice_line_item_id},
        entity_type="invoice",
    )
    return {"invoice": _invoice_state(conn, action.invoice_id or ""), "line_removed": True}


def _reverse_action(conn: sqlite3.Connection, row: sqlite3.Row) -> bool:
    try:
        original = json.loads(row["original_state_json"])
        applied = json.loads(row["applied_state_json"])
    except json.JSONDecodeError:
        return False
    action_type = row["action_type"]
    if action_type in {RESTORE_ACTION, QUARANTINE_ACTION}:
        current = _candidate_state(conn, row["candidate_id"] or "")
        if current != applied.get("state"):
            return False
        if action_type == RESTORE_ACTION and applied.get("warning_created"):
            resolve_calendar_warning(
                conn,
                candidate_id=row["candidate_id"],
                warning_code=RESTORED_WARNING,
                resolution="Legacy suppression restoration was reversed safely.",
            )
        _restore_candidate_state(conn, original)
    elif action_type == CONFLICT_ACTION:
        if applied.get("warning_created"):
            resolve_calendar_warning(
                conn,
                candidate_id=row["candidate_id"],
                warning_code=CONFLICT_WARNING,
                resolution="Legacy duration-conflict flag was reversed safely.",
            )
    elif action_type == DRAFT_LINE_ACTION:
        if not _reverse_draft_line_removal(conn, row, original, applied):
            return False
    else:
        return False
    now = now_iso()
    conn.execute(
        """
        UPDATE calendar_recovery_actions
        SET status = 'reversed', reversed_at = ?, updated_at = ?
        WHERE recovery_action_id = ? AND status = 'applied'
        """,
        (now, now, row["recovery_action_id"]),
    )
    _record_audit(
        conn,
        row["candidate_id"] or row["invoice_id"] or "",
        "calendar_recovery_action_reversed",
        {"action_type": action_type},
        entity_type="invoice" if action_type == DRAFT_LINE_ACTION else "calendar_event_candidate",
    )
    return True


def _reverse_draft_line_removal(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    original: dict[str, Any],
    applied: dict[str, Any],
) -> bool:
    invoice_id = row["invoice_id"] or ""
    line_id = row["invoice_line_item_id"] or ""
    if _invoice_state(conn, invoice_id) != applied.get("invoice"):
        return False
    if _invoice_line_state(conn, line_id) is not None:
        return False
    if not _invoice_is_safe_draft(conn, invoice_id):
        return False
    line = original.get("line")
    invoice = original.get("invoice")
    if not isinstance(line, dict) or not isinstance(invoice, dict):
        return False
    columns = tuple(line.keys())
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO invoice_line_items ({', '.join(columns)}) VALUES ({placeholders})",
        tuple(line[column] for column in columns),
    )
    conn.execute(
        """
        UPDATE invoices
        SET subtotal_cents = ?, adjustment_cents = ?, total_cents = ?,
            revision = ?, updated_at = ?
        WHERE invoice_id = ? AND status = 'draft'
        """,
        (
            invoice["subtotal_cents"],
            invoice["adjustment_cents"],
            invoice["total_cents"],
            invoice["revision"],
            invoice["updated_at"],
            invoice_id,
        ),
    )
    return True


def _candidate_state_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "candidate": {
            "id": row["id"],
            "classification": row["classification"],
            "review_status": row["review_status"],
            "hidden_from_review": row["hidden_from_review"],
            "reconciliation_status": row["reconciliation_status"],
            "updated_at": row["updated_at"],
        },
        "session": {
            "id": row["session_id"],
            "review_status": row["session_review_status"],
            "billable_status": row["billable_status"],
            "payment_status": row["payment_status"],
            "billing_treatment": row["billing_treatment"],
            "hidden_from_review": row["session_hidden_from_review"],
            "updated_at": row["session_updated_at"],
        },
    }


def _candidate_state(conn: sqlite3.Connection, candidate_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT c.id, c.classification, c.review_status, c.hidden_from_review,
               c.reconciliation_status, c.updated_at,
               s.id AS session_id, s.review_status AS session_review_status,
               s.billable_status, s.payment_status, s.billing_treatment,
               s.hidden_from_review AS session_hidden_from_review,
               s.updated_at AS session_updated_at
        FROM calendar_event_candidates c
        JOIN sessions s ON s.candidate_id = c.id
        WHERE c.id = ?
        """,
        (candidate_id,),
    ).fetchone()
    return _candidate_state_from_row(row) if row else None


def _require_candidate_state(conn: sqlite3.Connection, action: CalendarRecoveryAction) -> None:
    if _candidate_state(conn, action.candidate_id or "") != action.original_state:
        raise ValueError("Refused to change a candidate that changed after the recovery plan was created.")


def _restore_candidate_state(conn: sqlite3.Connection, state: dict[str, Any]) -> None:
    candidate = state.get("candidate") or {}
    session = state.get("session") or {}
    conn.execute(
        """
        UPDATE calendar_event_candidates
        SET classification = ?, review_status = ?, hidden_from_review = ?,
            reconciliation_status = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            candidate["classification"],
            candidate["review_status"],
            candidate["hidden_from_review"],
            candidate["reconciliation_status"],
            candidate["updated_at"],
            candidate["id"],
        ),
    )
    conn.execute(
        """
        UPDATE sessions
        SET review_status = ?, billable_status = ?, payment_status = ?,
            billing_treatment = ?, hidden_from_review = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            session["review_status"],
            session["billable_status"],
            session["payment_status"],
            session["billing_treatment"],
            session["hidden_from_review"],
            session["updated_at"],
            session["id"],
        ),
    )


def _invoice_line_state(conn: sqlite3.Connection, line_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM invoice_line_items WHERE invoice_line_item_id = ?",
        (line_id,),
    ).fetchone()
    return dict(row) if row else None


def _invoice_state_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "invoice_id": row["invoice_id"],
        "status": row["invoice_status"],
        "subtotal_cents": row["invoice_subtotal_cents"],
        "adjustment_cents": row["invoice_adjustment_cents"],
        "total_cents": row["invoice_total_cents"],
        "revision": row["invoice_revision"],
        "updated_at": row["invoice_updated_at"],
    }


def _invoice_state(conn: sqlite3.Connection, invoice_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT invoice_id, status, subtotal_cents, adjustment_cents, total_cents,
               revision, updated_at
        FROM invoices WHERE invoice_id = ?
        """,
        (invoice_id,),
    ).fetchone()
    return dict(row) if row else None


def _invoice_is_safe_draft(conn: sqlite3.Connection, invoice_id: str) -> bool:
    row = conn.execute(
        """
        SELECT status, invoice_number, finalized_at, voided_at, pdf_path, pdf_sha256
        FROM invoices WHERE invoice_id = ?
        """,
        (invoice_id,),
    ).fetchone()
    return bool(
        row
        and row["status"] == "draft"
        and not row["invoice_number"]
        and not row["finalized_at"]
        and not row["voided_at"]
        and not row["pdf_path"]
        and not row["pdf_sha256"]
    )


def _invoice_line_has_payment(conn: sqlite3.Connection, line_id: str, session_id: str) -> bool:
    return bool(
        conn.execute(
            """
            SELECT 1 FROM payment_allocations
            WHERE invoice_line_item_id = ? OR session_id = ?
            LIMIT 1
            """,
            (line_id, session_id),
        ).fetchone()
    )


def _has_open_warning(conn: sqlite3.Connection, candidate_id: str, warning_code: str) -> bool:
    rows = conn.execute(
        """
        SELECT unresolved_fields FROM review_items
        WHERE candidate_id = ?
          AND review_status = 'source_change_warning'
          AND reviewed_at IS NULL
        """,
        (candidate_id,),
    ).fetchall()
    for row in rows:
        try:
            fields = json.loads(row["unresolved_fields"] or "[]")
        except json.JSONDecodeError:
            continue
        if isinstance(fields, list) and warning_code in fields:
            return True
    return False


def _record_audit(
    conn: sqlite3.Connection,
    entity_id: str,
    action: str,
    details: dict[str, Any],
    *,
    entity_type: str = "calendar_event_candidate",
) -> None:
    conn.execute(
        """
        INSERT INTO audit_log (id, entity_type, entity_id, action, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (new_id(), entity_type, entity_id, action, json_dumps(details), now_iso()),
    )


def _action_table_exists(conn: sqlite3.Connection) -> bool:
    return bool(
        conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'calendar_recovery_actions'
            """
        ).fetchone()
    )


def _existing_action_status(conn: sqlite3.Connection, action_key: str) -> str | None:
    if not _action_table_exists(conn):
        return None
    row = conn.execute(
        "SELECT status FROM calendar_recovery_actions WHERE action_key = ?",
        (action_key,),
    ).fetchone()
    return row["status"] if row else None


def _write_action(
    conn: sqlite3.Connection,
    action: CalendarRecoveryAction,
    applied_state: dict[str, Any],
) -> None:
    now = now_iso()
    existing = conn.execute(
        "SELECT recovery_action_id, status FROM calendar_recovery_actions WHERE action_key = ?",
        (action.action_key,),
    ).fetchone()
    if existing and existing["status"] == "applied":
        raise ValueError("Calendar recovery action was already applied.")
    values = (
        action.action_type,
        action.candidate_id,
        action.session_id,
        action.invoice_id,
        action.invoice_line_item_id,
        action.reason,
        json_dumps(action.original_state or {}),
        json_dumps(applied_state),
        now,
        now,
    )
    if existing:
        conn.execute(
            """
            UPDATE calendar_recovery_actions
            SET action_type = ?, candidate_id = ?, session_id = ?, invoice_id = ?,
                invoice_line_item_id = ?, status = 'applied', reason = ?,
                original_state_json = ?, applied_state_json = ?, applied_at = ?,
                reversed_at = NULL, updated_at = ?
            WHERE recovery_action_id = ?
            """,
            (*values, existing["recovery_action_id"]),
        )
        return
    conn.execute(
        """
        INSERT INTO calendar_recovery_actions (
          recovery_action_id, action_key, action_type, candidate_id, session_id,
          invoice_id, invoice_line_item_id, status, reason, original_state_json,
          applied_state_json, applied_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'applied', ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id(),
            action.action_key,
            *values[:-2],
            now,
            now,
            now,
        ),
    )


def _backup_if_operational(
    conn: sqlite3.Connection,
    *,
    backup_factory: BackupFactory | None,
) -> bool:
    db_path = _get_db_path_from_conn(conn)
    if db_path is None or not db_path.exists() or not is_operational_db_path(db_path):
        return False
    if backup_factory:
        backup_path = backup_factory(db_path)
        from .backups import verify_sqlite_backup

        if verify_sqlite_backup(backup_path) != "ok":
            raise ValueError("Calendar recovery backup integrity check failed.")
        return True
    from .backups import create_verified_backup

    create_verified_backup(db_path, reason="calendar_recovery")
    return True
