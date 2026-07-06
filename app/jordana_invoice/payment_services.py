"""Backend payment-ledger services and financial invariants.

This module provides narrowly scoped functions for creating payments,
allocating payments to session charges, linking allocations to invoice
lines after staging, reversing allocations, voiding payments, and
computing paid/unapplied amounts.

No HTTP behavior, API routes, UI, or invoice-total changes are included.
All monetary values are integer cents.  All write operations that
validate totals use ``BEGIN IMMEDIATE`` so concurrent requests cannot
over-allocate a payment or charge.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from .csv_reports import refresh_reports_after_commit
from .db import DatabaseBusyError
from .util import json_dumps, new_id, now_iso, text


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _begin_immediate(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as error:
        if "locked" in str(error).lower():
            raise DatabaseBusyError(
                "Cannot complete payment operation: database is locked by another operation. "
                "Please retry in a moment."
            ) from error
        raise


def _audit(conn: sqlite3.Connection, entity_type: str, entity_id: str, action: str, details: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO audit_log (id, entity_type, entity_id, action, details, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (new_id(), entity_type, entity_id, action, json_dumps(details), now_iso()),
    )


def _validate_received_at(value: str) -> str:
    raw = text(value)
    if not raw:
        raise ValueError("received_at is required.")
    return raw


PAYMENT_METHODS = {"zelle", "check", "cash", "ach", "card", "other"}
RECENT_DUPLICATE_WINDOW_SECONDS = 120


def _month_key(value: str | None) -> str:
    raw = text(value)
    return raw[:7] if len(raw) >= 7 else ""


def _month_label(month_key: str | None) -> str:
    raw = text(month_key)
    if len(raw) != 7:
        return raw
    try:
        return datetime.strptime(raw, "%Y-%m").strftime("%B %Y")
    except ValueError:
        return raw


def _invoice_period_key(row: sqlite3.Row | dict[str, Any]) -> str:
    getter = row.get if isinstance(row, dict) else row.__getitem__
    billing_month = text(getter("billing_month")) if "billing_month" in row.keys() else ""
    if billing_month:
        return billing_month
    start = text(getter("billing_period_start")) if "billing_period_start" in row.keys() else ""
    return _month_key(start)


def _invoice_period_display(row: sqlite3.Row | dict[str, Any]) -> str:
    getter = row.get if isinstance(row, dict) else row.__getitem__
    key = _invoice_period_key(row)
    start = text(getter("billing_period_start")) if "billing_period_start" in row.keys() else ""
    end = text(getter("billing_period_end")) if "billing_period_end" in row.keys() else ""
    if key:
        return _month_label(key)
    if start and end:
        return f"{start} - {end}"
    return start or end


def _first_name_sort_key(name: str | None) -> tuple[str, str]:
    display = text(name)
    parts = display.split()
    first = parts[0].lower() if parts else ""
    return first, display.lower()


def _sort_payment_rows(rows: list[dict[str, Any]], name_key: str) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: (*_first_name_sort_key(row.get(name_key)), text(row.get("invoice_number") or row.get("received_at"))))


def list_payment_service_period_options(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """Return every invoice/service month represented on the Payments screen."""
    months: set[str] = set()
    for row in conn.execute(
        """
        SELECT billing_month, billing_period_start
        FROM invoices
        WHERE status = 'finalized'
        """
    ).fetchall():
        key = _invoice_period_key(row)
        if key:
            months.add(key)
    for row in conn.execute(
        """
        SELECT COALESCE(s.session_date, substr(s.start_at, 1, 10), p.received_at) AS service_date
        FROM payments p
        JOIN sessions s ON s.id = p.source_session_id
        WHERE p.source_type = 'paid_at_session_backfill'
          AND p.status = 'posted'
        """
    ).fetchall():
        key = _month_key(row["service_date"])
        if key:
            months.add(key)
    return [{"value": month, "label": _month_label(month)} for month in sorted(months, reverse=True)]


def _validate_payment_method(value: str | None, *, required: bool = False) -> str:
    raw = text(value).lower()
    if not raw:
        if required:
            raise ValueError("Payment method is required.")
        return "other"
    if raw not in PAYMENT_METHODS:
        raise ValueError("Unsupported payment method.")
    return raw


def _session_charge_cents(session: sqlite3.Row | dict[str, Any]) -> int:
    snapshot = session.get("rate_cents_snapshot") if isinstance(session, dict) else session["rate_cents_snapshot"]
    if snapshot is not None and snapshot > 0:
        return int(snapshot)
    approved = session.get("approved_rate_cents") if isinstance(session, dict) else session["approved_rate_cents"]
    if approved is not None and approved > 0:
        return int(approved)
    raise ValueError("Session has no approved charge amount.")


def _payment_row(conn: sqlite3.Connection, payment_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM payments WHERE payment_id = ?", (payment_id,)).fetchone()
    if row is None:
        raise ValueError("Payment was not found.")
    return row


def _session_row(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise ValueError("Session was not found.")
    return row


def _active_allocations_for_payment(conn: sqlite3.Connection, payment_id: str) -> int:
    return conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM payment_allocations WHERE payment_id = ? AND status = 'active'",
        (payment_id,),
    ).fetchone()[0]


def _active_allocations_for_session(conn: sqlite3.Connection, session_id: str) -> int:
    return conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM payment_allocations WHERE session_id = ? AND status = 'active'",
        (session_id,),
    ).fetchone()[0]


def _invoice_line_rows_for_invoice(conn: sqlite3.Connection, invoice_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT li.*
        FROM invoice_line_items li
        WHERE li.invoice_id = ?
        ORDER BY li.service_date ASC, li.sort_order ASC, li.invoice_line_item_id ASC
        """,
        (invoice_id,),
    ).fetchall()


def _invoice_paid_amount(conn: sqlite3.Connection, invoice_id: str) -> int:
    return conn.execute(
        """
        SELECT COALESCE(SUM(pa.amount_cents), 0)
        FROM payment_allocations pa
        JOIN payments p ON p.payment_id = pa.payment_id
        JOIN invoice_line_items li ON li.invoice_line_item_id = pa.invoice_line_item_id
        WHERE li.invoice_id = ? AND pa.status = 'active' AND p.status = 'posted'
        """,
        (invoice_id,),
    ).fetchone()[0]


def _invoice_summary_row(conn: sqlite3.Connection, invoice_id: str) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT i.*, bp.billing_name AS bill_to_display_name
        FROM invoices i
        JOIN billing_parties bp ON bp.billing_party_id = i.bill_to_party_id
        WHERE i.invoice_id = ?
        """,
        (invoice_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Invoice was not found.")
    return row


def _invoice_balance_summary(conn: sqlite3.Connection, invoice_id: str) -> dict[str, Any]:
    invoice = _invoice_summary_row(conn, invoice_id)
    paid_cents = _invoice_paid_amount(conn, invoice_id)
    total_cents = int(invoice["total_cents"] or 0)
    balance_cents = max(total_cents - paid_cents, 0)
    if balance_cents == 0:
        payment_status = "paid"
    elif paid_cents == 0:
        payment_status = "unpaid"
    else:
        payment_status = "partially_paid"
    return {
        **dict(invoice),
        "paid_cents": paid_cents,
        "balance_cents": balance_cents,
        "payment_status": payment_status,
    }


def _find_recent_duplicate_invoice_payment(
    conn: sqlite3.Connection,
    *,
    invoice_id: str,
    billing_party_id: str,
    amount_cents: int,
    received_at: str,
    method: str,
    reference_number: str | None,
    received_from_name: str | None,
    administrative_note: str | None,
) -> sqlite3.Row | None:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=RECENT_DUPLICATE_WINDOW_SECONDS)).isoformat().replace("+00:00", "Z")
    rows = conn.execute(
        """
        SELECT p.*
        FROM payments p
        WHERE p.billing_party_id = ?
          AND p.amount_cents = ?
          AND p.received_at = ?
          AND p.method = ?
          AND COALESCE(p.reference_number, '') = COALESCE(?, '')
          AND COALESCE(p.received_from_name, '') = COALESCE(?, '')
          AND COALESCE(p.administrative_note, '') = COALESCE(?, '')
          AND p.status = 'posted'
          AND p.source_type = 'manual'
          AND p.created_at >= ?
        ORDER BY p.created_at DESC
        """,
        (
            billing_party_id,
            amount_cents,
            received_at,
            method,
            reference_number,
            received_from_name,
            administrative_note,
            cutoff,
        ),
    ).fetchall()
    for row in rows:
        detail = get_payment_detail(conn, row["payment_id"])
        if detail["allocated_cents"] != amount_cents:
            continue
        allocations = detail["allocations"]
        if not allocations:
            continue
        linked_invoice_ids = {
            alloc_invoice["invoice_id"]
            for alloc_invoice in (
                conn.execute(
                    """
                    SELECT li.invoice_id
                    FROM invoice_line_items li
                    WHERE li.invoice_line_item_id = ?
                    """,
                    (allocation["invoice_line_item_id"],),
                ).fetchone()
                for allocation in allocations
                if allocation.get("invoice_line_item_id")
            )
            if alloc_invoice is not None
        }
        if linked_invoice_ids == {invoice_id}:
            return row
    return None


def _insert_payment_record(
    conn: sqlite3.Connection,
    *,
    billing_party_id: str,
    amount_cents: int,
    received_at: str,
    method: str = "other",
    reference_number: str | None = None,
    received_from_name: str | None = None,
    administrative_note: str | None = None,
    source_type: str = "manual",
    source_session_id: str | None = None,
) -> dict[str, Any]:
    if not billing_party_id or not text(billing_party_id):
        raise ValueError("billing_party_id is required.")
    party = conn.execute(
        "SELECT billing_party_id FROM billing_parties WHERE billing_party_id = ?", (billing_party_id,)
    ).fetchone()
    if party is None:
        raise ValueError("Bill To party was not found.")
    if not isinstance(amount_cents, int) or amount_cents <= 0:
        raise ValueError("amount_cents must be a positive integer.")
    received = _validate_received_at(received_at)
    method_val = _validate_payment_method(method)
    ref = text(reference_number) if reference_number is not None else None
    from_name = text(received_from_name) if received_from_name is not None else None
    note = text(administrative_note) if administrative_note is not None else None

    if source_type not in ("manual", "paid_at_session_backfill"):
        raise ValueError("Unsupported source_type.")
    if source_type == "manual" and source_session_id is not None:
        raise ValueError("Manual payments must not have a source_session_id.")
    if source_type == "paid_at_session_backfill":
        if source_session_id is None:
            raise ValueError("paid_at_session_backfill requires a source_session_id.")
        src_session = conn.execute(
            "SELECT billing_party_id FROM sessions WHERE id = ?", (source_session_id,)
        ).fetchone()
        if src_session is None:
            raise ValueError("Source session was not found.")
        if src_session["billing_party_id"] != billing_party_id:
            raise ValueError("Payment Bill To party does not match the source session billing party.")

    payment_id = new_id()
    now = now_iso()
    conn.execute(
        """INSERT INTO payments
           (payment_id, billing_party_id, amount_cents, received_at, method,
            reference_number, received_from_name, administrative_note,
            status, source_type, source_session_id, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'posted', ?, ?, ?, ?)""",
        (payment_id, billing_party_id, amount_cents, received, method_val,
         ref, from_name, note, source_type, source_session_id, now, now),
    )
    _audit(conn, "payment", payment_id, "payment_created", {
        "amount_cents": amount_cents,
        "source_type": source_type,
    })
    row = conn.execute("SELECT * FROM payments WHERE payment_id = ?", (payment_id,)).fetchone()
    return dict(row)


def _allocate_payment_to_session_locked(
    conn: sqlite3.Connection,
    *,
    payment_id: str,
    session_id: str,
    amount_cents: int,
    invoice_line_item_id: str | None = None,
) -> dict[str, Any]:
    payment = _payment_row(conn, payment_id)
    if payment["status"] != "posted":
        raise ValueError("Payment is not posted.")
    session = _session_row(conn, session_id)
    if payment["billing_party_id"] != session["billing_party_id"]:
        raise ValueError("Payment Bill To party does not match the session billing party.")
    if not isinstance(amount_cents, int) or amount_cents <= 0:
        raise ValueError("amount_cents must be a positive integer.")

    current_payment_alloc = _active_allocations_for_payment(conn, payment_id)
    if current_payment_alloc + amount_cents > payment["amount_cents"]:
        raise ValueError("Allocation exceeds the remaining unapplied payment amount.")

    charge = _session_charge_cents(session)
    current_session_alloc = _active_allocations_for_session(conn, session_id)
    if current_session_alloc + amount_cents > charge:
        raise ValueError("Allocation exceeds the session charge amount.")

    if invoice_line_item_id is not None:
        line = conn.execute(
            "SELECT * FROM invoice_line_items WHERE invoice_line_item_id = ?", (invoice_line_item_id,)
        ).fetchone()
        if line is None:
            raise ValueError("Invoice line item was not found.")
        if line["source_session_id"] != session_id:
            raise ValueError("Invoice line does not belong to the specified session.")
        invoice = conn.execute(
            "SELECT bill_to_party_id FROM invoices WHERE invoice_id = ?", (line["invoice_id"],)
        ).fetchone()
        if invoice is None:
            raise ValueError("Invoice for the line item was not found.")
        if invoice["bill_to_party_id"] != payment["billing_party_id"]:
            raise ValueError("Invoice Bill To party does not match the payment Bill To party.")

    allocation_id = new_id()
    now = now_iso()
    conn.execute(
        """INSERT INTO payment_allocations
           (allocation_id, payment_id, session_id, invoice_line_item_id,
            amount_cents, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 'active', ?, ?)""",
        (allocation_id, payment_id, session_id, invoice_line_item_id, amount_cents, now, now),
    )
    _audit(conn, "payment_allocation", allocation_id, "allocation_created", {
        "payment_id": payment_id,
        "session_id": session_id,
        "amount_cents": amount_cents,
    })
    row = conn.execute(
        "SELECT * FROM payment_allocations WHERE allocation_id = ?", (allocation_id,)
    ).fetchone()
    return dict(row)


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------

def create_payment(
    conn: sqlite3.Connection,
    *,
    billing_party_id: str,
    amount_cents: int,
    received_at: str,
    method: str = "other",
    reference_number: str | None = None,
    received_from_name: str | None = None,
    administrative_note: str | None = None,
    source_type: str = "manual",
    source_session_id: str | None = None,
) -> dict[str, Any]:
    """Create a posted payment record.

    Returns the stored payment row as a dict.
    """
    row = _insert_payment_record(
        conn,
        billing_party_id=billing_party_id,
        amount_cents=amount_cents,
        received_at=received_at,
        method=method,
        reference_number=reference_number,
        received_from_name=received_from_name,
        administrative_note=administrative_note,
        source_type=source_type,
        source_session_id=source_session_id,
    )
    conn.commit()
    return row


def allocate_payment_to_session(
    conn: sqlite3.Connection,
    *,
    payment_id: str,
    session_id: str,
    amount_cents: int,
    invoice_line_item_id: str | None = None,
) -> dict[str, Any]:
    """Allocate part or all of a payment to a session charge.

    Uses ``BEGIN IMMEDIATE`` so validation and insertion are atomic.
    """
    _begin_immediate(conn)
    try:
        row = _allocate_payment_to_session_locked(
            conn,
            payment_id=payment_id,
            session_id=session_id,
            amount_cents=amount_cents,
            invoice_line_item_id=invoice_line_item_id,
        )
        conn.commit()
        warning = refresh_reports_after_commit(conn)
        if warning:
            row["report_warning"] = warning
        return row
    except Exception:
        conn.rollback()
        raise


def link_session_allocations_to_invoice_line(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    invoice_line_item_id: str,
) -> dict[str, Any]:
    """Link existing active session allocations to an invoice line.

    Updates only active allocations for the session whose
    ``invoice_line_item_id`` IS NULL.  Does not recreate rows or change
    amounts.  Idempotent.
    """
    _begin_immediate(conn)
    try:
        line = conn.execute(
            "SELECT * FROM invoice_line_items WHERE invoice_line_item_id = ?", (invoice_line_item_id,)
        ).fetchone()
        if line is None:
            raise ValueError("Invoice line item was not found.")
        if line["source_session_id"] != session_id:
            raise ValueError("Invoice line does not belong to the specified session.")

        invoice = conn.execute(
            "SELECT bill_to_party_id FROM invoices WHERE invoice_id = ?", (line["invoice_id"],)
        ).fetchone()
        if invoice is None:
            raise ValueError("Invoice was not found.")

        allocations = conn.execute(
            """SELECT pa.* FROM payment_allocations pa
               JOIN payments p ON p.payment_id = pa.payment_id
               WHERE pa.session_id = ? AND pa.invoice_line_item_id IS NULL
                 AND pa.status = 'active' AND p.status = 'posted'""",
            (session_id,),
        ).fetchall()

        linked_ids: list[str] = []
        now = now_iso()
        for alloc in allocations:
            payment = conn.execute(
                "SELECT billing_party_id FROM payments WHERE payment_id = ?", (alloc["payment_id"],)
            ).fetchone()
            if payment["billing_party_id"] != invoice["bill_to_party_id"]:
                raise ValueError(
                    "Payment Bill To party does not match the invoice Bill To party for allocation %s." % alloc["allocation_id"]
                )
            conn.execute(
                "UPDATE payment_allocations SET invoice_line_item_id = ?, updated_at = ? WHERE allocation_id = ?",
                (invoice_line_item_id, now, alloc["allocation_id"]),
            )
            linked_ids.append(alloc["allocation_id"])

        if linked_ids:
            _audit(conn, "payment_allocation", invoice_line_item_id, "allocations_linked", {
                "session_id": session_id,
                "count": len(linked_ids),
            })
        conn.commit()
        return {"linked_count": len(linked_ids), "linked_ids": linked_ids}
    except Exception:
        conn.rollback()
        raise


def _check_idempotency_key(
    conn: sqlite3.Connection,
    idempotency_key: str | None,
    entity_type: str,
    entity_id: str,
    action: str,
) -> bool:
    """Return True and record the key if it is new; return False if already used."""
    if idempotency_key is None:
        return True
    existing = conn.execute(
        "SELECT 1 FROM idempotency_keys WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()
    if existing is not None:
        return False
    conn.execute(
        "INSERT INTO idempotency_keys (idempotency_key, entity_type, entity_id, action, created_at) VALUES (?, ?, ?, ?, ?)",
        (idempotency_key, entity_type, entity_id, action, now_iso()),
    )
    return True


def reverse_allocation(
    conn: sqlite3.Connection,
    allocation_id: str,
    *,
    reason: str = "",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Reverse an active allocation, preserving the row.

    Requires a non-empty administrative reason.  Reversing a second
    time raises ``ValueError`` (not idempotent).  If an idempotency key
    is supplied and has already been used, the request is rejected.
    """
    reason_val = text(reason)
    if not reason_val:
        raise ValueError("A reversal reason is required.")
    _begin_immediate(conn)
    try:
        row = conn.execute(
            "SELECT * FROM payment_allocations WHERE allocation_id = ?", (allocation_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Allocation was not found.")
        if not _check_idempotency_key(conn, idempotency_key, "payment_allocation", allocation_id, "reverse"):
            raise ValueError("This request has already been processed.")
        if row["status"] != "active":
            raise ValueError("Allocation is already reversed.")
        now = now_iso()
        conn.execute(
            "UPDATE payment_allocations SET status = 'reversed', reversed_at = ?, reversal_reason = ?, updated_at = ? WHERE allocation_id = ?",
            (now, reason_val, now, allocation_id),
        )
        _audit(conn, "payment_allocation", allocation_id, "allocation_reversed", {
            "amount_cents": row["amount_cents"],
            "reason": reason_val,
        })
        conn.commit()
        warning = refresh_reports_after_commit(conn)
        updated = conn.execute(
            "SELECT * FROM payment_allocations WHERE allocation_id = ?", (allocation_id,)
        ).fetchone()
        result = dict(updated)
        if warning:
            result["report_warning"] = warning
        return result
    except Exception:
        conn.rollback()
        raise


def void_payment(
    conn: sqlite3.Connection,
    payment_id: str,
    *,
    reason: str = "",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Void a posted payment.

    Requires a non-empty administrative reason.  Rejects voiding if the
    payment has active allocations.  Re-voiding a void payment raises
    ``ValueError`` (not idempotent).  If an idempotency key is supplied
    and has already been used, the request is rejected.
    """
    reason_val = text(reason)
    if not reason_val:
        raise ValueError("A void reason is required.")
    _begin_immediate(conn)
    try:
        payment = _payment_row(conn, payment_id)
        if not _check_idempotency_key(conn, idempotency_key, "payment", payment_id, "void"):
            raise ValueError("This request has already been processed.")
        if payment["status"] != "posted":
            raise ValueError("Payment is already void.")
        active_count = conn.execute(
            "SELECT COUNT(*) FROM payment_allocations WHERE payment_id = ? AND status = 'active'",
            (payment_id,),
        ).fetchone()[0]
        if active_count > 0:
            raise ValueError("Cannot void a payment with active allocations. Reverse all allocations first.")
        now = now_iso()
        conn.execute(
            "UPDATE payments SET status = 'void', voided_at = ?, void_reason = ?, updated_at = ? WHERE payment_id = ?",
            (now, reason_val, now, payment_id),
        )
        _audit(conn, "payment", payment_id, "payment_voided", {
            "amount_cents": payment["amount_cents"],
            "reason": reason_val,
        })
        conn.commit()
        warning = refresh_reports_after_commit(conn)
        updated = conn.execute("SELECT * FROM payments WHERE payment_id = ?", (payment_id,)).fetchone()
        result = dict(updated)
        if warning:
            result["report_warning"] = warning
        return result
    except Exception:
        conn.rollback()
        raise


def apply_available_funds(
    conn: sqlite3.Connection,
    payment_id: str,
    *,
    invoice_id: str,
    amount_cents: int,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Apply available (unapplied) funds from a posted payment to a finalized invoice.

    Creates new allocation rows — never edits or overwrites reversed
    allocations.  Validates that the payment is posted, the invoice is
    finalized, the invoice Bill To matches the payment Bill To, the
    amount is positive and does not exceed available funds or the
    invoice balance.  Uses ``BEGIN IMMEDIATE`` for atomicity.
    """
    if not isinstance(amount_cents, int) or amount_cents <= 0:
        raise ValueError("Amount must be greater than zero.")
    _begin_immediate(conn)
    try:
        payment = _payment_row(conn, payment_id)
        if payment["status"] != "posted":
            raise ValueError("Payment is not posted.")
        invoice = _invoice_summary_row(conn, invoice_id)
        if invoice["status"] != "finalized":
            raise ValueError("Only a finalized invoice can accept a payment.")
        if invoice["bill_to_party_id"] != payment["billing_party_id"]:
            raise ValueError("Payment Bill To party does not match the invoice Bill To party.")
        if not _check_idempotency_key(conn, idempotency_key, "payment", payment_id, "apply_funds"):
            raise ValueError("This request has already been processed.")
        available = payment["amount_cents"] - _active_allocations_for_payment(conn, payment_id)
        if amount_cents > available:
            raise ValueError("Amount exceeds available unapplied funds.")
        summary = _invoice_balance_summary(conn, invoice_id)
        if summary["balance_cents"] <= 0:
            raise ValueError("Invoice is already fully paid.")
        if amount_cents > summary["balance_cents"]:
            raise ValueError("Amount exceeds the current invoice balance.")

        remaining = amount_cents
        allocations: list[dict[str, Any]] = []
        for line in _invoice_line_rows_for_invoice(conn, invoice_id):
            if remaining <= 0:
                break
            if not line["source_session_id"]:
                raise ValueError("Invoice line is missing a source session and cannot accept a payment.")
            unpaid = int(line["line_amount_cents"] or 0) - invoice_line_paid_amount(conn, line["invoice_line_item_id"])
            if unpaid <= 0:
                continue
            alloc_amount = min(unpaid, remaining)
            allocations.append(
                _allocate_payment_to_session_locked(
                    conn,
                    payment_id=payment_id,
                    session_id=line["source_session_id"],
                    amount_cents=alloc_amount,
                    invoice_line_item_id=line["invoice_line_item_id"],
                )
            )
            remaining -= alloc_amount
        if remaining != 0:
            raise RuntimeError("Fund application did not fully apply to the invoice.")
        _audit(conn, "payment", payment_id, "funds_applied", {
            "invoice_id": invoice_id,
            "amount_cents": amount_cents,
        })
        conn.commit()
        warning = refresh_reports_after_commit(conn)
        result = {
            "payment": dict(_payment_row(conn, payment_id)),
            "invoice": _invoice_balance_summary(conn, invoice_id),
            "allocations": allocations,
        }
        if warning:
            result["report_warning"] = warning
        return result
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------------------
# Read and calculation helpers
# ---------------------------------------------------------------------------

def payment_allocated_amount(conn: sqlite3.Connection, payment_id: str) -> int:
    """Sum of active allocation amounts for a posted payment."""
    return conn.execute(
        """SELECT COALESCE(SUM(pa.amount_cents), 0)
           FROM payment_allocations pa
           JOIN payments p ON p.payment_id = pa.payment_id
           WHERE pa.payment_id = ? AND pa.status = 'active' AND p.status = 'posted'""",
        (payment_id,),
    ).fetchone()[0]


def payment_unapplied_amount(conn: sqlite3.Connection, payment_id: str) -> int:
    """Payment amount minus active allocations."""
    payment = _payment_row(conn, payment_id)
    if payment["status"] != "posted":
        return 0
    return payment["amount_cents"] - payment_allocated_amount(conn, payment_id)


def session_paid_amount(conn: sqlite3.Connection, session_id: str) -> int:
    """Active allocated amount for a session."""
    return conn.execute(
        """SELECT COALESCE(SUM(pa.amount_cents), 0)
           FROM payment_allocations pa
           JOIN payments p ON p.payment_id = pa.payment_id
           WHERE pa.session_id = ? AND pa.status = 'active' AND p.status = 'posted'""",
        (session_id,),
    ).fetchone()[0]


def invoice_line_paid_amount(conn: sqlite3.Connection, invoice_line_item_id: str) -> int:
    """Active allocated amount for an invoice line."""
    return conn.execute(
        """SELECT COALESCE(SUM(pa.amount_cents), 0)
           FROM payment_allocations pa
           JOIN payments p ON p.payment_id = pa.payment_id
           WHERE pa.invoice_line_item_id = ? AND pa.status = 'active' AND p.status = 'posted'""",
        (invoice_line_item_id,),
    ).fetchone()[0]


def get_payment_detail(conn: sqlite3.Connection, payment_id: str) -> dict[str, Any]:
    """Return a payment with its allocations and computed amounts."""
    payment = _payment_row(conn, payment_id)
    allocations = conn.execute(
        """SELECT pa.* FROM payment_allocations pa
           WHERE pa.payment_id = ?
           ORDER BY pa.created_at""",
        (payment_id,),
    ).fetchall()
    allocated = payment_allocated_amount(conn, payment_id)
    return {
        "payment": dict(payment),
        "allocations": [dict(a) for a in allocations],
        "allocated_cents": allocated,
        "unapplied_cents": payment["amount_cents"] - allocated if payment["status"] == "posted" else 0,
    }


def list_outstanding_invoices(conn: sqlite3.Connection, *, billing_month: str | None = None) -> list[dict[str, Any]]:
    """List finalized invoices with a positive remaining balance."""
    period = text(billing_month)
    rows = conn.execute(
        """
        SELECT i.invoice_id
        FROM invoices i
        WHERE i.status = 'finalized'
        ORDER BY i.invoice_date DESC, i.finalized_at DESC, i.created_at DESC
        """
    ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        summary = _invoice_balance_summary(conn, row["invoice_id"])
        if period and _invoice_period_key(summary) != period:
            continue
        if summary["balance_cents"] <= 0:
            continue
        summary["invoice_period"] = _invoice_period_key(summary)
        summary["invoice_period_display"] = _invoice_period_display(summary)
        results.append(summary)
    return _sort_payment_rows(results, "bill_to_display_name")


def _invoice_paid_date_and_methods(conn: sqlite3.Connection, invoice_id: str) -> tuple[str | None, str | None]:
    """Return (paid_date, payment_method_label) for a fully-paid invoice.

    paid_date is the received_at of the final settling payment.
    payment_method_label is the method or 'Multiple' if more than one distinct method.
    """
    rows = conn.execute(
        """
        SELECT DISTINCT p.payment_id, p.received_at, p.method
        FROM payments p
        JOIN payment_allocations pa ON pa.payment_id = p.payment_id
        JOIN invoice_line_items li ON li.invoice_line_item_id = pa.invoice_line_item_id
        WHERE li.invoice_id = ? AND pa.status = 'active' AND p.status = 'posted'
        ORDER BY p.received_at DESC, p.created_at DESC
        """,
        (invoice_id,),
    ).fetchall()
    if not rows:
        return None, None
    paid_date = rows[0]["received_at"]
    methods = {row["method"] for row in rows}
    method_label = "Multiple" if len(methods) > 1 else rows[0]["method"]
    return paid_date, method_label


def list_paid_invoices(conn: sqlite3.Connection, *, billing_month: str | None = None) -> list[dict[str, Any]]:
    """List finalized, non-void invoices whose derived balance is zero."""
    period = text(billing_month)
    rows = conn.execute(
        """
        SELECT i.invoice_id
        FROM invoices i
        WHERE i.status = 'finalized'
        ORDER BY i.invoice_date DESC, i.finalized_at DESC, i.created_at DESC
        """
    ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        summary = _invoice_balance_summary(conn, row["invoice_id"])
        if period and _invoice_period_key(summary) != period:
            continue
        if summary["balance_cents"] > 0:
            continue
        paid_date, method_label = _invoice_paid_date_and_methods(conn, row["invoice_id"])
        summary["paid_date"] = paid_date
        summary["payment_method"] = method_label
        summary["invoice_period"] = _invoice_period_key(summary)
        summary["invoice_period_display"] = _invoice_period_display(summary)
        summary["row_type"] = "invoice"
        results.append(summary)
    for row in conn.execute(
        """
        SELECT
          p.payment_id,
          p.billing_party_id,
          p.amount_cents,
          p.received_at,
          p.method,
          p.reference_number,
          s.id AS session_id,
          COALESCE(s.session_date, substr(s.start_at, 1, 10), p.received_at) AS service_date,
          bp.billing_name AS bill_to_display_name
        FROM payments p
        JOIN sessions s ON s.id = p.source_session_id
        JOIN billing_parties bp ON bp.billing_party_id = p.billing_party_id
        WHERE p.source_type = 'paid_at_session_backfill'
          AND p.status = 'posted'
        """
    ).fetchall():
        row_period = _month_key(row["service_date"])
        if period and row_period != period:
            continue
        results.append({
            "row_type": "paid_at_session",
            "payment_id": row["payment_id"],
            "session_id": row["session_id"],
            "invoice_id": "",
            "invoice_number": "Paid at session",
            "bill_to_display_name": row["bill_to_display_name"],
            "invoice_period": row_period,
            "invoice_period_display": _month_label(row_period),
            "total_cents": row["amount_cents"],
            "paid_cents": row["amount_cents"],
            "balance_cents": 0,
            "paid_date": row["received_at"],
            "payment_method": row["method"],
            "payment_status": "paid",
        })
    return _sort_payment_rows(results, "bill_to_display_name")


def list_all_payments(conn: sqlite3.Connection, *, billing_month: str | None = None) -> list[dict[str, Any]]:
    """Return the recorded payment ledger for the Payments screen.

    Sorts by bill-to first name, then stable by payment date.
    Each row includes bill_to_name, invoice numbers, and applied amount.
    """
    period = text(billing_month)
    rows = conn.execute(
        """
        SELECT
          p.payment_id,
          p.billing_party_id,
          p.amount_cents,
          p.received_at,
          p.method,
          p.reference_number,
          p.received_from_name,
          p.administrative_note,
          p.status,
          p.source_type,
          p.created_at,
          COALESCE(
            (SELECT COALESCE(i.billing_month, substr(i.billing_period_start, 1, 7))
             FROM payment_allocations pa2
             JOIN invoice_line_items li ON li.invoice_line_item_id = pa2.invoice_line_item_id
             JOIN invoices i ON i.invoice_id = li.invoice_id
             WHERE pa2.payment_id = p.payment_id AND pa2.status = 'active'
             ORDER BY i.billing_period_start ASC
             LIMIT 1),
            (SELECT substr(COALESCE(s.session_date, s.start_at, p.received_at), 1, 7)
             FROM sessions s
             WHERE s.id = p.source_session_id)
          ) AS invoice_period,
          bp.billing_name AS bill_to_name,
          COALESCE((
            SELECT GROUP_CONCAT(DISTINCT i.invoice_number)
            FROM payment_allocations pa2
            JOIN invoice_line_items li ON li.invoice_line_item_id = pa2.invoice_line_item_id
            JOIN invoices i ON i.invoice_id = li.invoice_id
            WHERE pa2.payment_id = p.payment_id AND pa2.status = 'active' AND p.status = 'posted'
          ), '') AS invoice_numbers
        FROM payments p
        JOIN billing_parties bp ON bp.billing_party_id = p.billing_party_id
        ORDER BY p.received_at DESC, p.created_at DESC, p.payment_id DESC
        """
    ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        row_period = text(row["invoice_period"])
        if period and row_period != period:
            continue
        applied = conn.execute(
            """
            SELECT COALESCE(SUM(pa.amount_cents), 0)
            FROM payment_allocations pa
            WHERE pa.payment_id = ? AND pa.status = 'active'
            """,
            (row["payment_id"],),
        ).fetchone()[0]
        if row["status"] != "posted":
            applied = 0
        invoice_nums = row["invoice_numbers"] or ""
        if invoice_nums and "," in invoice_nums:
            invoice_display = "Multiple invoices"
        else:
            invoice_display = invoice_nums or ""
        results.append({
            "payment_id": row["payment_id"],
            "billing_party_id": row["billing_party_id"],
            "bill_to_name": row["bill_to_name"],
            "amount_cents": row["amount_cents"],
            "received_at": row["received_at"],
            "method": row["method"],
            "reference_number": row["reference_number"],
            "received_from_name": row["received_from_name"],
            "administrative_note": row["administrative_note"],
            "status": row["status"],
            "source_type": row["source_type"],
            "amount_applied_cents": applied,
            "invoice_numbers": invoice_display,
            "invoice_period": row_period,
            "invoice_period_display": _month_label(row_period),
        })
    return _sort_payment_rows(results, "bill_to_name")


def get_payment_correction_history(conn: sqlite3.Connection, payment_id: str) -> list[dict[str, Any]]:
    """Return correction history for a payment from the audit log.

    Includes entries for allocation reversals, payment voids, and fund
    applications.  Sorted newest first.
    """
    allocation_ids = [
        row["allocation_id"]
        for row in conn.execute(
            "SELECT allocation_id FROM payment_allocations WHERE payment_id = ?",
            (payment_id,),
        ).fetchall()
    ]
    entity_ids = [payment_id] + allocation_ids
    if not entity_ids:
        return []
    placeholders = ",".join("?" * len(entity_ids))
    rows = conn.execute(
        f"""
        SELECT entity_type, entity_id, action, details, created_at
        FROM audit_log
        WHERE entity_id IN ({placeholders})
          AND action IN ('allocation_reversed', 'payment_voided', 'funds_applied')
        ORDER BY created_at DESC
        """,
        entity_ids,
    ).fetchall()
    history: list[dict[str, Any]] = []
    for row in rows:
        details = {}
        try:
            details = json.loads(row["details"]) if row["details"] else {}
        except (ValueError, TypeError):
            pass
        history.append({
            "entity_type": row["entity_type"],
            "entity_id": row["entity_id"],
            "action": row["action"],
            "reason": details.get("reason"),
            "amount_cents": details.get("amount_cents"),
            "created_at": row["created_at"],
        })
    return history


def get_payment_detail_view(conn: sqlite3.Connection, payment_id: str) -> dict[str, Any]:
    """Return a payment detail view with related invoice information.

    Includes allocation correction details (reversed_at, reversal_reason)
    and payment void details (voided_at, void_reason).  Also includes a
    correction history list derived from the audit log.
    """
    detail = get_payment_detail(conn, payment_id)
    payment = detail["payment"]
    allocations: list[dict[str, Any]] = []
    for alloc in detail["allocations"]:
        invoice_info = None
        if alloc.get("invoice_line_item_id"):
            row = conn.execute(
                """
                SELECT i.invoice_id, i.invoice_number, bp.billing_name AS bill_to_name
                FROM invoice_line_items li
                JOIN invoices i ON i.invoice_id = li.invoice_id
                JOIN billing_parties bp ON bp.billing_party_id = i.bill_to_party_id
                WHERE li.invoice_line_item_id = ?
                """,
                (alloc["invoice_line_item_id"],),
            ).fetchone()
            if row:
                invoice_info = {
                    "invoice_id": row["invoice_id"],
                    "invoice_number": row["invoice_number"],
                    "bill_to_name": row["bill_to_name"],
                }
        allocations.append({
            "allocation_id": alloc["allocation_id"],
            "amount_cents": alloc["amount_cents"],
            "status": alloc["status"],
            "reversed_at": alloc.get("reversed_at"),
            "reversal_reason": alloc.get("reversal_reason"),
            "created_at": alloc.get("created_at"),
            "invoice_info": invoice_info,
        })
    correction_history = get_payment_correction_history(conn, payment_id)
    return {
        "payment_id": payment["payment_id"],
        "billing_party_id": payment["billing_party_id"],
        "amount_cents": payment["amount_cents"],
        "received_at": payment["received_at"],
        "method": payment["method"],
        "reference_number": payment["reference_number"],
        "received_from_name": payment["received_from_name"],
        "administrative_note": payment["administrative_note"],
        "status": payment["status"],
        "source_type": payment["source_type"],
        "voided_at": payment.get("voided_at"),
        "void_reason": payment.get("void_reason"),
        "allocated_cents": detail["allocated_cents"],
        "unapplied_cents": detail["unapplied_cents"],
        "allocations": allocations,
        "correction_history": correction_history,
    }


def client_account_summary(conn: sqlite3.Connection, person_id: str) -> dict[str, Any]:
    """Return billed, paid, and current balance for a person's billing parties.

    Uses finalized, non-void invoices only.
    Paid amount counts only posted payments with active allocations.
    """
    invoice_rows = conn.execute(
        """
        SELECT i.invoice_id, i.total_cents
        FROM invoices i
        JOIN billing_parties bp ON bp.billing_party_id = i.bill_to_party_id AND bp.person_id = ?
        WHERE i.status = 'finalized'
        """,
        (person_id,),
    ).fetchall()
    total_billed = sum(int(r["total_cents"] or 0) for r in invoice_rows)
    total_paid = sum(_invoice_paid_amount(conn, r["invoice_id"]) for r in invoice_rows)
    current_balance = max(total_billed - total_paid, 0)
    account_status = "Current" if current_balance == 0 else "Balance Due"
    return {
        "total_finalized_invoices": len(invoice_rows),
        "total_billed_cents": total_billed,
        "total_paid_cents": total_paid,
        "current_balance_cents": current_balance,
        "account_status": account_status,
    }


def list_invoice_payment_history(conn: sqlite3.Connection, invoice_id: str) -> dict[str, Any]:
    """Return read-only payment history for one invoice."""
    summary = _invoice_balance_summary(conn, invoice_id)
    rows = conn.execute(
        """
        SELECT
          p.*,
          COALESCE(SUM(CASE WHEN p.status = 'posted' AND pa.status = 'active' THEN pa.amount_cents ELSE 0 END), 0) AS amount_applied_cents,
          COALESCE(SUM(CASE WHEN pa.status = 'active' THEN 1 ELSE 0 END), 0) AS active_allocation_count,
          COALESCE(SUM(CASE WHEN pa.status = 'reversed' THEN 1 ELSE 0 END), 0) AS reversed_allocation_count
        FROM payments p
        JOIN payment_allocations pa ON pa.payment_id = p.payment_id
        JOIN invoice_line_items li ON li.invoice_line_item_id = pa.invoice_line_item_id
        WHERE li.invoice_id = ?
        GROUP BY p.payment_id
        ORDER BY p.received_at DESC, p.created_at DESC, p.payment_id DESC
        """,
        (invoice_id,),
    ).fetchall()
    payments: list[dict[str, Any]] = []
    for row in rows:
        status = "posted"
        if row["status"] == "void":
            status = "void"
        elif row["active_allocation_count"] == 0 and row["reversed_allocation_count"] > 0:
            status = "reversed"
        payments.append({
            "payment_id": row["payment_id"],
            "received_at": row["received_at"],
            "method": row["method"],
            "reference_number": row["reference_number"],
            "received_from_name": row["received_from_name"],
            "administrative_note": row["administrative_note"],
            "payment_status": status,
            "amount_applied_cents": int(row["amount_applied_cents"] or 0),
        })
    return {"invoice": summary, "payments": payments}


def record_invoice_payment(
    conn: sqlite3.Connection,
    *,
    invoice_id: str,
    payment_date: str,
    amount_cents: int,
    payment_method: str,
    reference_number: str | None = None,
    received_from_name: str | None = None,
    administrative_note: str | None = None,
    billing_party_id: str | None = None,
) -> dict[str, Any]:
    """Create one manual payment and allocate it across a finalized invoice atomically."""
    if not text(payment_date):
        raise ValueError("Payment date is required.")
    received = _validate_received_at(payment_date)
    method = _validate_payment_method(payment_method, required=True)
    if not isinstance(amount_cents, int) or amount_cents <= 0:
        raise ValueError("Payment amount must be greater than zero.")

    _begin_immediate(conn)
    try:
        invoice = _invoice_summary_row(conn, invoice_id)
        if invoice["status"] == "draft":
            raise ValueError("Cannot record a payment for a draft invoice.")
        if invoice["status"] == "void":
            raise ValueError("Cannot record a payment for a void invoice.")
        if invoice["status"] != "finalized":
            raise ValueError("Only a finalized invoice can accept a payment.")
        if billing_party_id and billing_party_id != invoice["bill_to_party_id"]:
            raise ValueError("Payment Bill To party does not match the invoice Bill To party.")

        summary = _invoice_balance_summary(conn, invoice_id)
        if summary["balance_cents"] <= 0:
            raise ValueError("Invoice is already fully paid.")
        if amount_cents > summary["balance_cents"]:
            raise ValueError("Payment amount cannot exceed the current invoice balance.")

        ref = text(reference_number) if reference_number is not None else None
        from_name = text(received_from_name) if received_from_name is not None else None
        note = text(administrative_note) if administrative_note is not None else None

        duplicate = _find_recent_duplicate_invoice_payment(
            conn,
            invoice_id=invoice_id,
            billing_party_id=invoice["bill_to_party_id"],
            amount_cents=amount_cents,
            received_at=received,
            method=method,
            reference_number=ref,
            received_from_name=from_name,
            administrative_note=note,
        )
        if duplicate is not None:
            conn.rollback()
            detail = get_payment_detail(conn, duplicate["payment_id"])
            refreshed_summary = _invoice_balance_summary(conn, invoice_id)
            return {
                "invoice": refreshed_summary,
                "payment": detail["payment"],
                "allocations": detail["allocations"],
                "duplicate_submission_ignored": True,
            }

        payment = _insert_payment_record(
            conn,
            billing_party_id=invoice["bill_to_party_id"],
            amount_cents=amount_cents,
            received_at=received,
            method=method,
            reference_number=ref,
            received_from_name=from_name,
            administrative_note=note,
        )
        remaining = amount_cents
        allocations: list[dict[str, Any]] = []
        for line in _invoice_line_rows_for_invoice(conn, invoice_id):
            if remaining <= 0:
                break
            if not line["source_session_id"]:
                raise ValueError("Invoice line is missing a source session and cannot accept a payment.")
            unpaid_cents = int(line["line_amount_cents"] or 0) - invoice_line_paid_amount(conn, line["invoice_line_item_id"])
            if unpaid_cents <= 0:
                continue
            alloc_amount = min(unpaid_cents, remaining)
            allocations.append(
                _allocate_payment_to_session_locked(
                    conn,
                    payment_id=payment["payment_id"],
                    session_id=line["source_session_id"],
                    amount_cents=alloc_amount,
                    invoice_line_item_id=line["invoice_line_item_id"],
                )
            )
            remaining -= alloc_amount
        if remaining != 0:
            raise RuntimeError("Payment allocation did not fully apply to the invoice.")
        conn.commit()
        warning = refresh_reports_after_commit(conn)
        result = {
            "invoice": _invoice_balance_summary(conn, invoice_id),
            "payment": payment,
            "allocations": allocations,
            "duplicate_submission_ignored": False,
        }
        if warning:
            result["report_warning"] = warning
        return result
    except Exception:
        conn.rollback()
        raise


def record_or_validate_paid_at_session_payment_locked(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    billing_party_id: str,
    amount_cents: int,
    payment_date: str,
    payment_method: str,
    reference_number: str | None = None,
    administrative_note: str | None = None,
) -> dict[str, Any]:
    """Atomically record or validate a paid-at-session payment and allocation.

    Must be run inside an active BEGIN IMMEDIATE write transaction. Does NOT commit.
    """
    # 1. Query the existing source payment
    existing = conn.execute(
        "SELECT * FROM payments WHERE source_type = 'paid_at_session_backfill' AND source_session_id = ?",
        (session_id,),
    ).fetchall()

    if not existing:
        # Validate inputs
        if not billing_party_id or not text(billing_party_id):
            raise ValueError("billing_party_id is required.")
        party = conn.execute(
            "SELECT billing_party_id FROM billing_parties WHERE billing_party_id = ?", (billing_party_id,)
        ).fetchone()
        if party is None:
            raise ValueError("Bill To party was not found.")
        if not isinstance(amount_cents, int) or amount_cents <= 0:
            raise ValueError("Payment amount must be greater than zero.")
        
        received = _validate_received_at(payment_date)
        method_val = _validate_payment_method(payment_method, required=True)
        ref = text(reference_number) if reference_number is not None else None
        note = text(administrative_note) if administrative_note is not None else None

        payment_id = new_id()
        now = now_iso()
        conn.execute(
            """INSERT INTO payments
               (payment_id, billing_party_id, amount_cents, received_at, method,
                reference_number, administrative_note, status, source_type,
                source_session_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'posted', 'paid_at_session_backfill', ?, ?, ?)""",
            (payment_id, billing_party_id, amount_cents, received, method_val,
             ref, note, session_id, now, now),
        )
        _audit(conn, "payment", payment_id, "paid_at_session_payment_created", {
            "amount_cents": amount_cents,
            "source_type": "paid_at_session_backfill",
            "source_session_id": session_id,
        })

        # Create allocation
        allocation_id = new_id()
        conn.execute(
            """INSERT INTO payment_allocations
               (allocation_id, payment_id, session_id, invoice_line_item_id,
                amount_cents, status, created_at, updated_at)
               VALUES (?, ?, ?, NULL, ?, 'active', ?, ?)""",
            (allocation_id, payment_id, session_id, amount_cents, now, now),
        )
        _audit(conn, "payment_allocation", allocation_id, "paid_at_session_allocation_created", {
            "payment_id": payment_id,
            "session_id": session_id,
            "amount_cents": amount_cents,
        })

        return {"payment_id": payment_id, "outcome": "created"}

    if len(existing) > 1:
        raise ValueError("Inconsistency: multiple paid-at-session payments exist for this session.")

    payment = existing[0]

    # Validate existing payment details
    if payment["status"] != "posted":
        raise ValueError(f"Inconsistency: paid-at-session payment is not posted (status: {payment['status']}).")
    if payment["billing_party_id"] != billing_party_id:
        raise ValueError("Inconsistency: paid-at-session payment billing party does not match session.")
    if payment["amount_cents"] != amount_cents:
        raise ValueError(
            f"Inconsistency: paid-at-session payment amount ({payment['amount_cents']}) "
            f"does not match session charge ({amount_cents})."
        )

    # Query allocations for this payment
    allocations = conn.execute(
        "SELECT * FROM payment_allocations WHERE payment_id = ?",
        (payment["payment_id"],),
    ).fetchall()

    active_allocations = [a for a in allocations if a["status"] == "active"]

    # Check for any conflicting active allocations for this session (pointing to other payments)
    session_allocations = conn.execute(
        "SELECT * FROM payment_allocations WHERE session_id = ? AND status = 'active'",
        (session_id,),
    ).fetchall()

    if not active_allocations:
        # Validate that no other conflicting allocation exists for this session
        if session_allocations:
            raise ValueError("Inconsistency: conflicting active allocations exist for this session.")

        # Repair the missing allocation
        allocation_id = new_id()
        now = now_iso()
        conn.execute(
            """INSERT INTO payment_allocations
               (allocation_id, payment_id, session_id, invoice_line_item_id,
                amount_cents, status, created_at, updated_at)
               VALUES (?, ?, ?, NULL, ?, 'active', ?, ?)""",
            (allocation_id, payment["payment_id"], session_id, amount_cents, now, now),
        )
        _audit(conn, "payment_allocation", allocation_id, "paid_at_session_allocation_repaired", {
            "payment_id": payment["payment_id"],
            "session_id": session_id,
            "amount_cents": amount_cents,
        })
        return {"payment_id": payment["payment_id"], "outcome": "repaired_allocation"}

    if len(active_allocations) == 1:
        alloc = active_allocations[0]
        if alloc["session_id"] != session_id:
            raise ValueError("Inconsistency: active allocation is for a different session.")
        if alloc["amount_cents"] != amount_cents:
            raise ValueError(
                f"Inconsistency: active allocation amount ({alloc['amount_cents']}) "
                f"does not match session charge ({amount_cents})."
            )
        # Check that the session has no other active allocations
        if len(session_allocations) != 1 or session_allocations[0]["allocation_id"] != alloc["allocation_id"]:
            raise ValueError("Inconsistency: conflicting active allocations exist for this session.")

        # Reused payment/allocation
        _audit(conn, "payment", payment["payment_id"], "paid_at_session_payment_reused", {
            "session_id": session_id,
        })
        return {"payment_id": payment["payment_id"], "outcome": "reused"}

    raise ValueError(
        f"Inconsistency: expected exactly one active allocation for paid-at-session payment, "
        f"found {len(active_allocations)}."
    )



# ---------------------------------------------------------------------------
# Dry-run backfill analyzer (read-only)
# ---------------------------------------------------------------------------

def dry_run_paid_at_session_backfill(conn: sqlite3.Connection) -> dict[str, Any]:
    """Analyze paid_at_session sessions and return a sanitized aggregate report.

    This function is strictly read-only.  It performs no INSERT, UPDATE,
    DELETE, commit, audit, or migration.  It classifies every
    ``paid_at_session`` session into exactly one category and returns
    aggregate counts and a total proposed amount.
    """
    sessions = conn.execute(
        "SELECT id, billing_party_id, review_status, rate_cents_snapshot, "
        "approved_rate_cents, session_date, start_at "
        "FROM sessions WHERE payment_status = 'paid_at_session'"
    ).fetchall()

    sessions_considered = len(sessions)
    eligible = 0
    already_backfilled = 0
    not_approved = 0
    missing_billing_party = 0
    missing_or_invalid_amount = 0
    missing_or_invalid_date = 0
    existing_manual_allocation_conflict = 0
    total_amount_proposed_cents = 0
    rate_disagreement_count = 0
    existing_reversed_manual_allocation_count = 0

    for s in sessions:
        # 1. already backfilled
        backfill_payment = conn.execute(
            "SELECT 1 FROM payments WHERE source_type = 'paid_at_session_backfill' "
            "AND source_session_id = ?",
            (s["id"],),
        ).fetchone()
        if backfill_payment is not None:
            already_backfilled += 1
            continue

        # 2. not approved
        if s["review_status"] != "approved":
            not_approved += 1
            continue

        # 3. missing Bill To party
        if not s["billing_party_id"]:
            missing_billing_party += 1
            continue

        # 4. missing or invalid amount
        snapshot = s["rate_cents_snapshot"]
        approved = s["approved_rate_cents"]
        snapshot_valid = snapshot is not None and isinstance(snapshot, int) and snapshot > 0
        approved_valid = approved is not None and isinstance(approved, int) and approved > 0
        if not snapshot_valid and not approved_valid:
            missing_or_invalid_amount += 1
            continue
        if snapshot_valid and approved_valid and snapshot != approved:
            rate_disagreement_count += 1
        amount = snapshot if snapshot_valid else approved

        # 5. missing or invalid date
        session_date = s["session_date"]
        start_at = s["start_at"]
        date_valid = False
        if session_date and text(session_date):
            date_valid = True
        elif start_at and text(start_at):
            date_valid = True
        if not date_valid:
            missing_or_invalid_date += 1
            continue

        # 6. existing manual allocation conflict
        active_manual = conn.execute(
            "SELECT 1 FROM payment_allocations pa "
            "JOIN payments p ON p.payment_id = pa.payment_id "
            "WHERE pa.session_id = ? AND pa.status = 'active' "
            "AND p.source_type = 'manual'",
            (s["id"],),
        ).fetchone()
        if active_manual is not None:
            existing_manual_allocation_conflict += 1
            continue

        # Check for reversed manual allocations (informational only)
        reversed_manual = conn.execute(
            "SELECT 1 FROM payment_allocations pa "
            "JOIN payments p ON p.payment_id = pa.payment_id "
            "WHERE pa.session_id = ? AND pa.status = 'reversed' "
            "AND p.source_type = 'manual'",
            (s["id"],),
        ).fetchone()
        if reversed_manual is not None:
            existing_reversed_manual_allocation_count += 1

        # 7. eligible
        eligible += 1
        total_amount_proposed_cents += amount

    return {
        "sessions_considered": sessions_considered,
        "sessions_eligible": eligible,
        "sessions_already_backfilled": already_backfilled,
        "sessions_skipped": {
            "not_approved": not_approved,
            "missing_billing_party": missing_billing_party,
            "missing_or_invalid_amount": missing_or_invalid_amount,
            "missing_or_invalid_date": missing_or_invalid_date,
            "existing_manual_allocation_conflict": existing_manual_allocation_conflict,
        },
        "total_amount_proposed_cents": total_amount_proposed_cents,
        "rate_disagreement_count": rate_disagreement_count,
        "existing_reversed_manual_allocation_count": existing_reversed_manual_allocation_count,
    }
