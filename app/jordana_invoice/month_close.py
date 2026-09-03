"""Read-only month-close reconciliation for calendar and billing evidence."""
from __future__ import annotations

import calendar
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .financial_summary import _month_bounds, get_financial_summary
from .importer import calendar_reconciliation_buckets, candidate_key
from .capture_windows import is_past_capture_window
from .util import now_iso, text


EASTERN = ZoneInfo("America/New_York")


def _as_local_date(value: object) -> date | None:
    raw = text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(EASTERN)
        return parsed.date()
    except ValueError:
        return None


def _check(check_id: str, label: str, status: str, summary: str, *, count: int = 0,
           items: list[dict[str, Any]] | None = None, action: str = "") -> dict[str, Any]:
    return {"id": check_id, "label": label, "status": status, "summary": summary,
            "count": int(count), "items": items or [], "action": action}


def _money(cents: object) -> str:
    return f"${int(cents or 0) / 100:,.2f}"


def _capture_check(conn: sqlite3.Connection, month: str, today: date) -> dict[str, Any]:
    _, start_text, end_text = _month_bounds(month)
    month_start = date.fromisoformat(start_text)
    month_end = date.fromisoformat(end_text)
    deadline = month_end + timedelta(days=3)
    rows = conn.execute(
        """SELECT * FROM calendar_capture_runs
           WHERE COALESCE(completed_at, started_at, source_updated_at) >= ?
             AND COALESCE(completed_at, started_at, source_updated_at) < ?
           ORDER BY COALESCE(completed_at, started_at, source_updated_at)""",
        (month_start.isoformat(), (deadline + timedelta(days=1)).isoformat()),
    ).fetchall()
    complete_dates = [
        run_date for row in rows
        if text(row["status"]) == "complete"
        and (run_date := _as_local_date(row["completed_at"] or row["started_at"] or row["source_updated_at"]))
    ]
    partial = []
    for row in rows:
        if text(row["status"]) == "complete":
            continue
        partial_date = _as_local_date(row["completed_at"] or row["started_at"] or row["source_updated_at"])
        recovered = partial_date and any(
            partial_date <= completed <= partial_date + timedelta(days=3)
            for completed in complete_dates
        )
        if not recovered:
            partial.append(dict(row))
    closing = []
    for row in rows:
        run_date = _as_local_date(row["completed_at"] or row["started_at"] or row["source_updated_at"])
        if text(row["status"]) == "complete" and run_date and month_end <= run_date <= deadline:
            closing.append(dict(row))
    if partial:
        return _check("capture_integrity", "Calendar capture", "action_needed",
            f"{len(partial)} calendar capture run(s) were incomplete.", count=len(partial),
            items=[{"run_id": text(row.get("run_id")), "started_at": text(row.get("started_at")),
                    "past": f"{row.get('past_received', 0)}/{row.get('past_found', 0)}",
                    "future": f"{row.get('future_received', 0)}/{row.get('future_found', 0)}"}
                   for row in partial[:20]], action="calendar_import")
    if today < month_end:
        complete_count = sum(text(row["status"]) == "complete" for row in rows)
        return _check("capture_integrity", "Calendar capture", "in_progress",
            f"Month is still open; {complete_count} complete capture run(s) are recorded.", count=complete_count)
    if not rows:
        return _check("capture_integrity", "Calendar capture", "action_needed",
            "No capture-run proof is available for this month. Sync after the Apps Script update.",
            action="calendar_import")
    if today > deadline and not closing:
        return _check("capture_integrity", "Calendar capture", "action_needed",
            "No complete past-3-days closing sweep was recorded within three days after month end.",
            action="calendar_import")
    if not closing:
        return _check("capture_integrity", "Calendar capture", "in_progress",
            "Run the normal Shortcut once after month end to complete the closing sweep.", action="calendar_import")
    latest = closing[-1]
    return _check("capture_integrity", "Calendar capture", "passed",
        "Capture counts matched and a post-month-end past-3-days sweep completed.", count=len(rows),
        items=[{"run_id": latest["run_id"], "completed_at": latest["completed_at"]}])


def _unmapped_past_rows(conn: sqlite3.Connection, month: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM raw_calendar_snapshots
           WHERE capture_window = 'past_3_days'
           ORDER BY start_at, captured_at, ingested_at""").fetchall()
    seen: set[str] = set()
    missing: list[dict[str, Any]] = []
    for row in rows:
        local_date = _as_local_date(row["start_at"])
        if not local_date or local_date.strftime("%Y-%m") != month:
            continue
        key = candidate_key(row)
        if key in seen:
            continue
        seen.add(key)
        if conn.execute("SELECT 1 FROM calendar_event_candidates WHERE candidate_key = ? LIMIT 1", (key,)).fetchone():
            continue
        missing.append({"raw_snapshot_id": row["id"], "title": text(row["event_title"]),
                        "start_at": text(row["start_at"]), "calendar_name": text(row["calendar_name"])})
    return missing[:50]


def _review_items(conn: sqlite3.Connection, month: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(
        """SELECT s.id AS session_id, s.session_date, s.start_at,
                  s.raw_calendar_title AS title, s.review_status
           FROM sessions s
           JOIN calendar_event_candidates c ON c.id = s.candidate_id
           WHERE substr(s.session_date, 1, 7) = ?
             AND s.review_status NOT IN ('approved', 'excluded')
             AND (c.capture_windows LIKE '%past_3_days%'
               OR c.capture_windows LIKE '%past_7_days%'
               OR c.capture_windows LIKE '%backfill_%')
           ORDER BY s.start_at LIMIT 50""", (month,)).fetchall()]


def _candidate_has_past_evidence(conn: sqlite3.Connection, candidate_id: str) -> bool:
    row = conn.execute(
        "SELECT capture_windows FROM calendar_event_candidates WHERE id = ?", (candidate_id,)
    ).fetchone()
    try:
        windows = json.loads(row["capture_windows"] or "[]") if row else []
    except (json.JSONDecodeError, TypeError):
        windows = []
    return any(is_past_capture_window(window) for window in windows)


def _uninvoiced_sessions(conn: sqlite3.Connection, month: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(
        """SELECT s.id AS session_id, s.session_date, s.start_at, s.raw_calendar_title AS title,
                  COALESCE(s.rate_cents_snapshot, s.approved_rate_cents, 0) AS amount_cents
           FROM sessions s WHERE substr(s.session_date, 1, 7) = ?
             AND s.review_status = 'approved' AND s.payment_status != 'paid_at_session'
             AND s.billable_status NOT IN ('excluded', 'nonbillable')
             AND s.appointment_status != 'scheduled'
             AND NOT (s.appointment_status IN ('cancelled', 'no_show') AND s.billing_treatment != 'billable')
             AND NOT EXISTS (
               SELECT 1 FROM invoice_line_items li JOIN invoices i ON i.invoice_id = li.invoice_id
               WHERE li.source_session_id = s.id AND i.status = 'finalized')
           ORDER BY s.start_at LIMIT 50""", (month,)).fetchall()]


def _duplicate_finalized_lines(conn: sqlite3.Connection, month: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(
        """SELECT li.source_session_id AS session_id, COUNT(DISTINCT i.invoice_id) AS invoice_count
           FROM invoice_line_items li JOIN invoices i ON i.invoice_id = li.invoice_id
           WHERE i.status = 'finalized'
             AND COALESCE(NULLIF(i.billing_month, ''), substr(i.billing_period_start, 1, 7)) = ?
             AND li.source_session_id IS NOT NULL
           GROUP BY li.source_session_id HAVING COUNT(DISTINCT i.invoice_id) > 1 LIMIT 50""", (month,)).fetchall()]


def _receipt_issues(conn: sqlite3.Connection, month: str) -> list[dict[str, Any]]:
    month_name = calendar.month_name[int(month[5:7])]
    issues: list[dict[str, Any]] = []
    rows = conn.execute(
        """SELECT pr.receipt_id, pr.receipt_number, pr.pdf_path, pr.snapshot_json,
                  MIN(COALESCE(NULLIF(i.billing_month, ''), substr(i.billing_period_start, 1, 7))) AS expected_month
           FROM payment_receipts pr
           JOIN payment_allocations pa ON pa.payment_id = pr.payment_id AND pa.status = 'active'
           JOIN payments p ON p.payment_id = pa.payment_id AND p.status = 'posted'
           JOIN invoice_line_items li ON li.invoice_line_item_id = pa.invoice_line_item_id
           JOIN invoices i ON i.invoice_id = li.invoice_id AND i.status = 'finalized'
           GROUP BY pr.receipt_id HAVING expected_month = ?""", (month,)).fetchall()
    for row in rows:
        try:
            snapshot_month = text(json.loads(row["snapshot_json"] or "{}").get("filing_month"))
        except (json.JSONDecodeError, TypeError):
            snapshot_month = ""
        parts = Path(text(row["pdf_path"])).parts
        expected_folder = f"{month_name} {month[:4]}"
        if snapshot_month not in ("", month) or expected_folder not in parts:
            issues.append({"receipt_id": row["receipt_id"], "receipt_number": row["receipt_number"],
                           "expected_month": month, "stored_month": snapshot_month})
    return issues[:50]


def get_month_close_report(
    conn: sqlite3.Connection, month: str | None = None, *, today: date | None = None
) -> dict[str, Any]:
    selected_month, _, end_text = _month_bounds(month, today=today)
    today = today or date.today()
    month_end = date.fromisoformat(end_text)
    financial = get_financial_summary(conn, selected_month, today=today)
    buckets = calendar_reconciliation_buckets(conn, month=selected_month)
    unmapped = _unmapped_past_rows(conn, selected_month)
    duplicates = []
    for group in buckets["possible_duplicates"]:
        evidenced = [
            item for item in group.get("sessions", [])
            if _candidate_has_past_evidence(conn, text(item.get("candidate_id")))
        ]
        if len(evidenced) > 1:
            duplicates.append({**group, "sessions": evidenced})
    review = _review_items(conn, selected_month)
    uninvoiced = _uninvoiced_sessions(conn, selected_month)
    duplicate_lines = _duplicate_finalized_lines(conn, selected_month)
    receipt_issues = _receipt_issues(conn, selected_month)

    checks = [_capture_check(conn, selected_month, today)]
    checks.append(_check("raw_to_session", "Past calendar evidence", "action_needed" if unmapped else "passed",
        f"{len(unmapped)} past calendar item(s) did not reach a candidate." if unmapped else "Every captured past item reached the candidate ledger.",
        count=len(unmapped), items=unmapped, action="reconciliation" if unmapped else ""))
    checks.append(_check("duplicates", "Duplicate protection", "action_needed" if duplicates else "passed",
        f"{len(duplicates)} UTC-equivalent session group(s) need review." if duplicates else "No duplicate session instants were found.",
        count=len(duplicates), items=duplicates, action="reconciliation" if duplicates else ""))
    checks.append(_check("review", "Session review", "action_needed" if review else "passed",
        f"{len(review)} past session(s) still need review." if review else "All past sessions are approved or intentionally excluded.",
        count=len(review), items=review, action="review" if review else ""))
    invoice_items = uninvoiced + duplicate_lines
    checks.append(_check("invoice_coverage", "Invoice coverage", "action_needed" if invoice_items else "passed",
        (f"{len(uninvoiced)} approved session(s) are not finalized; {len(duplicate_lines)} appear on multiple finalized invoices."
         if invoice_items else "Every invoice-eligible approved session is on one finalized invoice."),
        count=len(invoice_items), items=invoice_items, action="invoices" if invoice_items else ""))
    checks.append(_check("payments", "Payments", "informational",
        f"{_money(financial['payments_applied_cents'])} applied; {_money(financial['outstanding_cents'])} remains outstanding. Unpaid balances do not block month close."))
    checks.append(_check("receipts", "Receipt filing", "action_needed" if receipt_issues else "passed",
        f"{len(receipt_issues)} receipt(s) are filed outside the invoice service month." if receipt_issues else "Receipts are filed under their invoice service month.",
        count=len(receipt_issues), items=receipt_issues, action="payments" if receipt_issues else ""))
    blockers = [item for item in checks if item["status"] == "action_needed"]
    status = "action_needed" if blockers else ("in_progress" if today < month_end else "ready")
    return {"ok": True, "month": selected_month, "status": status, "checked_at": now_iso(),
            "blocker_count": len(blockers), "checks": checks, "financial_summary": financial,
            "edited_event_groups": len(buckets["newer_edited_event_versions"]),
            "note": "Edited history and future-only snapshots are informational and do not create missing-session warnings."}
