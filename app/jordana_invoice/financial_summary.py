"""Shared read-only financial totals for invoices, payments, and future dashboards.

All monetary values are returned as integer cents. Finalized invoice totals come
from the immutable invoice snapshot, and outstanding balances use only posted
payments with active allocations.
"""
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
    if month_number == 12:
        end = f"{year + 1:04d}-01-01"
    else:
        end = f"{year:04d}-{month_number + 1:02d}-01"
    return month, start, end


def get_financial_summary(
    conn: sqlite3.Connection,
    month: str | None = None,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """Return the shared financial summary for one calendar month.

    ``draft_invoice_value_cents`` is global because drafts are work in progress,
    not finalized revenue. Monthly finalized totals use ``finalized_at`` rather
    than invoice date. Monthly payment receipts include posted payments received
    in the month; void payments are excluded. Reversing an allocation changes an
    invoice balance but does not erase a still-posted cash receipt.
    """
    selected_month, month_start, month_end = _month_bounds(month, today=today)

    draft_invoice_value_cents = int(
        conn.execute(
            "SELECT COALESCE(SUM(total_cents), 0) FROM invoices WHERE status = 'draft'"
        ).fetchone()[0]
        or 0
    )
    finalized_invoice_value_for_month_cents = int(
        conn.execute(
            """
            SELECT COALESCE(SUM(total_cents), 0)
            FROM invoices
            WHERE status = 'finalized'
              AND finalized_at >= ?
              AND finalized_at < ?
            """,
            (month_start, month_end),
        ).fetchone()[0]
        or 0
    )
    payments_received_for_month_cents = int(
        conn.execute(
            """
            SELECT COALESCE(SUM(amount_cents), 0)
            FROM payments
            WHERE status = 'posted'
              AND received_at >= ?
              AND received_at < ?
            """,
            (month_start, month_end),
        ).fetchone()[0]
        or 0
    )
    outstanding_balance_cents = int(
        conn.execute(
            """
            SELECT COALESCE(SUM(
              CASE
                WHEN i.total_cents > COALESCE(paid.paid_cents, 0)
                  THEN i.total_cents - COALESCE(paid.paid_cents, 0)
                ELSE 0
              END
            ), 0)
            FROM invoices i
            LEFT JOIN (
              SELECT li.invoice_id, SUM(pa.amount_cents) AS paid_cents
              FROM payment_allocations pa
              JOIN payments p ON p.payment_id = pa.payment_id
              JOIN invoice_line_items li
                ON li.invoice_line_item_id = pa.invoice_line_item_id
              WHERE pa.status = 'active'
                AND p.status = 'posted'
              GROUP BY li.invoice_id
            ) paid ON paid.invoice_id = i.invoice_id
            WHERE i.status = 'finalized'
            """
        ).fetchone()[0]
        or 0
    )

    return {
        "month": selected_month,
        "month_start": month_start,
        "month_end_exclusive": month_end,
        "draft_invoice_value_cents": draft_invoice_value_cents,
        "finalized_invoice_value_for_month_cents": finalized_invoice_value_for_month_cents,
        "payments_received_for_month_cents": payments_received_for_month_cents,
        "outstanding_balance_cents": outstanding_balance_cents,
    }
