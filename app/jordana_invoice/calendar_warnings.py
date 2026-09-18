from __future__ import annotations

import json
import sqlite3
from typing import Any

from .util import json_dumps, new_id, now_iso


def _warning_fields(value: Any) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def upsert_calendar_warning(
    conn: sqlite3.Connection,
    *,
    candidate_id: str,
    session_id: str | None,
    warning_code: str,
    reason: str,
    old_value: str = "",
    new_value: str = "",
    priority: int = 1,
) -> str:
    """Add one reversible, review-queue warning for a calendar concern.

    Warnings never change approval, billability, payment state, or raw
    evidence.  Each warning code is idempotent per candidate while it remains
    open, so repeated syncs do not create a noisy queue.
    """

    now = now_iso()
    items = conn.execute(
        """
        SELECT review_item_id, unresolved_fields
        FROM review_items
        WHERE candidate_id = ?
          AND review_status = 'source_change_warning'
          AND reviewed_at IS NULL
        ORDER BY created_at DESC
        """,
        (candidate_id,),
    ).fetchall()
    existing = next((item for item in items if warning_code in _warning_fields(item["unresolved_fields"])), None)

    if existing:
        review_item_id = existing["review_item_id"]
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
                json_dumps([warning_code]),
                json_dumps([reason]),
                old_value,
                new_value,
                reason,
                now,
                review_item_id,
            ),
        )
    else:
        review_item_id = new_id()
        conn.execute(
            """
            INSERT INTO review_items (
              review_item_id, candidate_id, session_id, review_status,
              unresolved_fields, review_reasons, old_value, new_value,
              reason, created_at, updated_at
            ) VALUES (?, ?, ?, 'source_change_warning', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_item_id,
                candidate_id,
                session_id,
                json_dumps([warning_code]),
                json_dumps([reason]),
                old_value,
                new_value,
                reason,
                now,
                now,
            ),
        )

    queue = conn.execute(
        """
        SELECT id
        FROM review_queue
        WHERE candidate_id = ?
          AND review_type = ?
          AND status = 'open'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (candidate_id, warning_code),
    ).fetchone()
    if queue:
        conn.execute(
            """
            UPDATE review_queue
            SET priority = ?, reason = ?, fields = ?, updated_at = ?
            WHERE id = ?
            """,
            (priority, reason, json_dumps([warning_code]), now, queue["id"]),
        )
    else:
        conn.execute(
            """
            INSERT INTO review_queue (
              id, candidate_id, review_type, priority, reason, fields,
              status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)
            """,
            (
                new_id(),
                candidate_id,
                warning_code,
                priority,
                reason,
                json_dumps([warning_code]),
                now,
                now,
            ),
        )

    conn.execute(
        """
        INSERT INTO audit_log (id, entity_type, entity_id, action, details, created_at)
        VALUES (?, 'calendar_event_candidate', ?, 'calendar_warning_opened', ?, ?)
        """,
        (new_id(), candidate_id, json_dumps({"warning_code": warning_code}), now),
    )
    return review_item_id


def resolve_calendar_warning(
    conn: sqlite3.Connection,
    *,
    candidate_id: str,
    warning_code: str,
    resolution: str,
) -> int:
    """Resolve only the matching open warning after positive source evidence."""

    rows = conn.execute(
        """
        SELECT review_item_id, unresolved_fields
        FROM review_items
        WHERE candidate_id = ?
          AND review_status = 'source_change_warning'
          AND reviewed_at IS NULL
        """,
        (candidate_id,),
    ).fetchall()
    matched = [row for row in rows if warning_code in _warning_fields(row["unresolved_fields"])]
    if not matched:
        return 0

    now = now_iso()
    for row in matched:
        conn.execute(
            """
            UPDATE review_items
            SET reviewed_at = ?, decision_source = 'calendar_reconciliation',
                decision_payload = ?, updated_at = ?
            WHERE review_item_id = ?
            """,
            (now, json_dumps({"resolution": resolution}), now, row["review_item_id"]),
        )
    conn.execute(
        """
        UPDATE review_queue
        SET status = 'resolved', decision_payload = ?, updated_at = ?
        WHERE candidate_id = ?
          AND review_type = ?
          AND status = 'open'
        """,
        (json_dumps({"resolution": resolution}), now, candidate_id, warning_code),
    )
    conn.execute(
        """
        INSERT INTO audit_log (id, entity_type, entity_id, action, details, created_at)
        VALUES (?, 'calendar_event_candidate', ?, 'calendar_warning_resolved', ?, ?)
        """,
        (new_id(), candidate_id, json_dumps({"warning_code": warning_code}), now),
    )
    return len(matched)
