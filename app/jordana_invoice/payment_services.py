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

import sqlite3
from typing import Any

from .db import DatabaseBusyError
from .util import json_dumps, new_id, normalize_payment_status, now_iso, text


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
    method_val = text(method) or "other"
    ref = text(reference_number) if reference_number is not None else None
    from_name = text(received_from_name) if received_from_name is not None else None
    note = text(administrative_note) if administrative_note is not None else None

    # Provenance validation
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
    conn.commit()
    row = conn.execute("SELECT * FROM payments WHERE payment_id = ?", (payment_id,)).fetchone()
    return dict(row)


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

        line_invoice_party: str | None = None
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
            line_invoice_party = invoice["bill_to_party_id"]

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
        conn.commit()
        row = conn.execute(
            "SELECT * FROM payment_allocations WHERE allocation_id = ?", (allocation_id,)
        ).fetchone()
        return dict(row)
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


def reverse_allocation(conn: sqlite3.Connection, allocation_id: str) -> dict[str, Any]:
    """Reverse an active allocation, preserving the row.

    Reversing a second time raises ``ValueError`` (not idempotent).
    """
    _begin_immediate(conn)
    try:
        row = conn.execute(
            "SELECT * FROM payment_allocations WHERE allocation_id = ?", (allocation_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Allocation was not found.")
        if row["status"] != "active":
            raise ValueError("Allocation is already reversed.")
        now = now_iso()
        conn.execute(
            "UPDATE payment_allocations SET status = 'reversed', reversed_at = ?, updated_at = ? WHERE allocation_id = ?",
            (now, now, allocation_id),
        )
        _audit(conn, "payment_allocation", allocation_id, "allocation_reversed", {
            "amount_cents": row["amount_cents"],
        })
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM payment_allocations WHERE allocation_id = ?", (allocation_id,)
        ).fetchone()
        return dict(updated)
    except Exception:
        conn.rollback()
        raise


def void_payment(conn: sqlite3.Connection, payment_id: str) -> dict[str, Any]:
    """Void a posted payment.

    Rejects voiding if the payment has active allocations.
    Re-voiding a void payment raises ``ValueError`` (not idempotent).
    """
    _begin_immediate(conn)
    try:
        payment = _payment_row(conn, payment_id)
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
            "UPDATE payments SET status = 'void', voided_at = ?, updated_at = ? WHERE payment_id = ?",
            (now, now, payment_id),
        )
        _audit(conn, "payment", payment_id, "payment_voided", {"amount_cents": payment["amount_cents"]})
        conn.commit()
        updated = conn.execute("SELECT * FROM payments WHERE payment_id = ?", (payment_id,)).fetchone()
        return dict(updated)
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
