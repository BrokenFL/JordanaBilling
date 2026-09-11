"""Service-month financial totals shared by Invoices, Payments, and Month Close."""
from __future__ import annotations

import re
import sqlite3
from datetime import date
from typing import Any


_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _month_bounds(month: str | None, *, today: date | None = None) -> tuple[str, str, str]:
    if month is None:
        current = today or date.today()
        month = f"{current.year:04d}-{current.month:02d}"
    if not _MONTH_RE.fullmatch(month):
        raise ValueError("billing_month must be in YYYY-MM format.")
    year, month_number = (int(part) for part in month.split("-", 1))
    start = f"{year:04d}-{month_number:02d}-01"
    end = f"{year + (month_number == 12):04d}-{1 if month_number == 12 else month_number + 1:02d}-01"
    return month, start, end


def _invoice_month_expression(alias: str = "i") -> str:
    return f"COALESCE(NULLIF({alias}.billing_month, ''), substr({alias}.billing_period_start, 1, 7))"


def get_financial_summary(
    conn: sqlite3.Connection,
    month: str | None = None,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """Return totals attributed to one service month, always as integer cents."""
    selected_month, month_start, month_end = _month_bounds(month, today=today)
    invoice_month = _invoice_month_expression()

    total_billable_cents = int(conn.execute(
        """SELECT COALESCE(SUM(COALESCE(s.rate_cents_snapshot, s.approved_rate_cents, 0)), 0)
           FROM sessions s
           WHERE s.review_status = 'approved' AND substr(s.session_date, 1, 7) = ?
             AND s.billable_status NOT IN ('excluded', 'nonbillable')
             AND s.appointment_status != 'scheduled'
             AND NOT (s.appointment_status IN ('cancelled', 'no_show') AND s.billing_treatment != 'billable')""",
        (selected_month,),
    ).fetchone()[0] or 0)
    total_invoiced_cents = int(conn.execute(
        f"""SELECT COALESCE(SUM(i.total_cents), 0) FROM invoices i
            WHERE i.status = 'finalized' AND {invoice_month} = ?""",
        (selected_month,),
    ).fetchone()[0] or 0)
    payments_applied_cents = int(conn.execute(
        f"""SELECT COALESCE(SUM(pa.amount_cents), 0)
            FROM payment_allocations pa
            JOIN payments p ON p.payment_id = pa.payment_id
            JOIN sessions s ON s.id = pa.session_id
            LEFT JOIN invoice_line_items li ON li.invoice_line_item_id = pa.invoice_line_item_id
            LEFT JOIN invoices i ON i.invoice_id = li.invoice_id
            WHERE pa.status = 'active' AND p.status = 'posted'
              AND ((li.invoice_line_item_id IS NOT NULL AND i.status = 'finalized' AND {invoice_month} = ?)
                OR (li.invoice_line_item_id IS NULL AND substr(s.session_date, 1, 7) = ?))""",
        (selected_month, selected_month),
    ).fetchone()[0] or 0)
    outstanding_cents = int(conn.execute(
        f"""SELECT COALESCE(SUM(CASE WHEN i.total_cents > COALESCE(paid.paid_cents, 0)
                  THEN i.total_cents - COALESCE(paid.paid_cents, 0) ELSE 0 END), 0)
            FROM invoices i
            LEFT JOIN (
              SELECT li.invoice_id, SUM(pa.amount_cents) AS paid_cents
              FROM payment_allocations pa
              JOIN payments p ON p.payment_id = pa.payment_id
              JOIN invoice_line_items li ON li.invoice_line_item_id = pa.invoice_line_item_id
              WHERE pa.status = 'active' AND p.status = 'posted'
              GROUP BY li.invoice_id
            ) paid ON paid.invoice_id = i.invoice_id
            WHERE i.status = 'finalized' AND {invoice_month} = ?""",
        (selected_month,),
    ).fetchone()[0] or 0)

    cash_received_cents = int(conn.execute(
        """SELECT COALESCE(SUM(amount_cents), 0) FROM payments
           WHERE status = 'posted' AND received_at >= ? AND received_at < ?""",
        (month_start, month_end),
    ).fetchone()[0] or 0)
    draft_value_cents = int(conn.execute(
        f"""SELECT COALESCE(SUM(total_cents), 0) FROM invoices i
            WHERE status = 'draft' AND {invoice_month} = ?""",
        (selected_month,),
    ).fetchone()[0] or 0)

    return {
        "month": selected_month,
        "month_start": month_start,
        "month_end_exclusive": month_end,
        "total_billable_cents": total_billable_cents,
        "total_invoiced_cents": total_invoiced_cents,
        "payments_applied_cents": payments_applied_cents,
        "outstanding_cents": outstanding_cents,
        # Transitional aliases preserve old API consumers while Test.35 ships.
        "draft_invoice_value_cents": draft_value_cents,
        "finalized_invoice_value_for_month_cents": total_invoiced_cents,
        "payments_received_for_month_cents": cash_received_cents,
        "outstanding_balance_cents": outstanding_cents,
    }
