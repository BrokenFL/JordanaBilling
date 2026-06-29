from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

from .invoice_rendering import (
    compact_address_lines,
    format_long_date,
    money,
    resolve_logo_path,
    split_snapshot_lines,
)
from .invoice_services import (
    _filing_owner_folder,
    _sanitize_path_part,
    resolve_invoice_filing_owner,
)
from .payment_services import get_payment_detail, payment_unapplied_amount
from .receipt_pdf import generate_receipt_pdf
from .util import new_id, now_iso


def preview_payment_receipt(
    conn: sqlite3.Connection,
    payment_id: str,
    *,
    filing_owner_person_id: str | None = None,
) -> dict[str, Any]:
    existing = get_payment_receipt(conn, payment_id)
    if existing:
        return {"mode": "finalized", "receipt": existing, "snapshot": json.loads(existing["snapshot_json"])}
    snapshot = _build_receipt_snapshot(
        conn,
        payment_id,
        receipt_number=None,
        filing_owner_person_id=filing_owner_person_id,
        require_filing_owner=False,
    )
    return {"mode": "preview", "receipt": None, "snapshot": snapshot}


def create_payment_receipt(
    conn: sqlite3.Connection,
    payment_id: str,
    *,
    filing_owner_person_id: str | None = None,
    pdf_root: str | Path | None = None,
) -> dict[str, Any]:
    existing = get_payment_receipt(conn, payment_id)
    if existing:
        return {"receipt": existing, "snapshot": json.loads(existing["snapshot_json"]), "created": False}

    root = Path(pdf_root or os.getenv("JORDANA_RECEIPTS_DIR", "Receipts")).expanduser()
    pdf_path: Path | None = None
    pdf_existed_before = False
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as error:
        if "locked" in str(error).lower():
            raise RuntimeError("Cannot create receipt: database is locked by another operation. Please retry in a moment.") from error
        raise
    try:
        existing_locked = get_payment_receipt(conn, payment_id)
        if existing_locked:
            conn.commit()
            return {"receipt": existing_locked, "snapshot": json.loads(existing_locked["snapshot_json"]), "created": False}
        payment = conn.execute("SELECT * FROM payments WHERE payment_id = ?", (payment_id,)).fetchone()
        if not payment:
            raise ValueError("Payment was not found.")
        number = _next_receipt_number(conn, int(str(payment["received_at"])[:4]))
        snapshot = _build_receipt_snapshot(
            conn,
            payment_id,
            receipt_number=number,
            filing_owner_person_id=filing_owner_person_id,
            require_filing_owner=True,
        )
        filing_owner = snapshot["filing_owner"]["selected"]
        client_folder = _filing_owner_folder(conn, root, filing_owner)
        month_folder = _sanitize_path_part(_receipt_month_label(snapshot["payment_date"]), "Unknown Month")
        pdf_path = client_folder / month_folder / f"Receipt_{number}.pdf"
        pdf_existed_before = pdf_path.exists()
        if pdf_existed_before:
            raise ValueError("A finalized receipt PDF already exists at the target receipt location.")
        checksum = generate_receipt_pdf(snapshot, pdf_path)
        now = now_iso()
        receipt_id = new_id()
        snapshot_json = json.dumps(snapshot, sort_keys=True)
        conn.execute(
            """INSERT INTO payment_receipts (
              receipt_id, payment_id, receipt_number, status, payment_received_at,
              amount_cents, filing_owner_person_id, filing_owner_person_code_snapshot,
              filing_owner_display_name_snapshot, snapshot_json, pdf_path, pdf_sha256,
              created_at, updated_at
            ) VALUES (?, ?, ?, 'finalized', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                receipt_id,
                payment_id,
                number,
                payment["received_at"],
                payment["amount_cents"],
                filing_owner["person_id"],
                filing_owner.get("person_code"),
                filing_owner.get("display_name"),
                snapshot_json,
                str(pdf_path),
                checksum,
                now,
                now,
            ),
        )
        _audit(conn, receipt_id, "receipt_created", {"payment_id": payment_id, "receipt_number": number, "pdf_sha256": checksum})
        conn.commit()
        receipt = get_payment_receipt(conn, payment_id)
        return {"receipt": receipt, "snapshot": snapshot, "created": True}
    except Exception:
        conn.rollback()
        if pdf_path and not pdf_existed_before and pdf_path.exists():
            pdf_path.unlink()
        raise


def get_payment_receipt(conn: sqlite3.Connection, payment_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM payment_receipts WHERE payment_id = ?", (payment_id,)).fetchone()
    return dict(row) if row else None


def get_payment_receipt_by_id(conn: sqlite3.Connection, receipt_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM payment_receipts WHERE receipt_id = ?", (receipt_id,)).fetchone()
    return dict(row) if row else None


def trusted_receipt_document_action(
    conn: sqlite3.Connection,
    receipt_id: str,
    action: str,
    *,
    pdf_root: str | Path | None = None,
) -> dict[str, Any]:
    if action not in {"open_pdf", "show_in_finder"}:
        raise ValueError("Unsupported receipt document action.")
    receipt = get_payment_receipt_by_id(conn, receipt_id)
    if not receipt:
        raise ValueError("Receipt was not found.")
    pdf_path = Path(str(receipt["pdf_path"] or "")).expanduser()
    root = Path(pdf_root or os.getenv("JORDANA_RECEIPTS_DIR", "Receipts")).expanduser()
    try:
        resolved_pdf = pdf_path.resolve(strict=False)
        resolved_root = root.resolve(strict=False)
        resolved_pdf.relative_to(resolved_root)
    except (OSError, ValueError):
        raise ValueError("Stored receipt path is outside the configured receipt folder.")
    if not resolved_pdf.is_file():
        raise ValueError("The PDF file for this receipt is missing from the expected location.")
    args = ["open", str(resolved_pdf)]
    if action == "show_in_finder":
        args = ["open", "-R", str(resolved_pdf)]
    subprocess.run(args, check=True)
    return {"ok": True, "action": action}


def _build_receipt_snapshot(
    conn: sqlite3.Connection,
    payment_id: str,
    *,
    receipt_number: str | None,
    filing_owner_person_id: str | None,
    require_filing_owner: bool,
) -> dict[str, Any]:
    detail = get_payment_detail(conn, payment_id)
    payment = detail["payment"]
    if payment["status"] != "posted":
        raise ValueError("Only posted payments can have receipts.")
    active_allocations = [a for a in detail["allocations"] if a["status"] == "active"]
    if not active_allocations:
        raise ValueError("A receipt requires at least one active allocation.")

    party = conn.execute("SELECT * FROM billing_parties WHERE billing_party_id = ?", (payment["billing_party_id"],)).fetchone()
    if not party:
        raise ValueError("Payment Bill To party was not found.")
    profile = conn.execute("SELECT * FROM business_profile WHERE active = 1 LIMIT 1").fetchone()
    if not profile:
        raise ValueError("Business profile is required before creating a receipt.")

    allocation_rows = [_allocation_snapshot(conn, alloc) for alloc in active_allocations]
    filing = _resolve_receipt_filing_owner(
        conn,
        allocation_rows,
        selected_person_id=filing_owner_person_id,
        require_selected=require_filing_owner,
    )
    if require_filing_owner and not filing.get("selected"):
        raise ValueError(filing.get("message") or "Choose which client this receipt should be filed under.")

    paid_in_full = bool(all(int(row["remaining_balance_cents"] or 0) == 0 for row in allocation_rows))
    payment_date = str(payment["received_at"])[:10]
    sender_lines = [
        value for value in [
            " ".join(part for part in [profile["provider_display_name"], profile["credentials_display"]] if part).strip(),
            *compact_address_lines(profile["address_line_1"], profile["address_line_2"], profile["city"], profile["state"], profile["postal_code"]),
            profile["phone"],
        ]
        if value
    ]
    bill_to_lines = [party["billing_name"]]
    delivery = party["preferred_delivery_method"]
    if delivery in {"mail", "both"}:
        bill_to_lines.extend(compact_address_lines(
            party["billing_address_line_1"],
            party["billing_address_line_2"],
            party["billing_city"],
            party["billing_state"],
            party["billing_postal_code"],
        ))
    if delivery in {"email", "both"} and party["billing_email"]:
        bill_to_lines.append(f"Via Email: {party['billing_email']}")

    return {
        "snapshot_version": 1,
        "document_title": "PAYMENT RECEIPT" if receipt_number else "DRAFT PAYMENT RECEIPT",
        "receipt_number": receipt_number or "",
        "payment_id": payment["payment_id"],
        "payment_date": payment_date,
        "payment_date_display": format_long_date(payment_date),
        "payment_method": payment["method"],
        "payment_method_display": _payment_method_label(payment["method"]),
        "reference_number": payment["reference_number"] or "",
        "received_from_name": payment["received_from_name"] or "",
        "amount_cents": payment["amount_cents"],
        "amount_display": money(payment["amount_cents"]),
        "allocated_cents": detail["allocated_cents"],
        "unapplied_cents": payment_unapplied_amount(conn, payment_id),
        "unapplied_display": money(payment_unapplied_amount(conn, payment_id)),
        "paid_in_full": paid_in_full,
        "bill_to_party_id": payment["billing_party_id"],
        "bill_to_lines": [line for line in bill_to_lines if line],
        "payer": dict(party),
        "business_profile": dict(profile),
        "business_name": profile["business_name"],
        "provider_name": profile["provider_display_name"],
        "sender_lines": sender_lines,
        "logo_path": resolve_logo_path(profile["logo_path"]),
        "allocations": allocation_rows,
        "filing_owner": filing,
    }


def _allocation_snapshot(conn: sqlite3.Connection, alloc: dict[str, Any]) -> dict[str, Any]:
    session = conn.execute("SELECT * FROM sessions WHERE id = ?", (alloc["session_id"],)).fetchone()
    if not session:
        raise ValueError("Payment allocation session was not found.")
    invoice = None
    line = None
    if alloc.get("invoice_line_item_id"):
        line = conn.execute("SELECT * FROM invoice_line_items WHERE invoice_line_item_id = ?", (alloc["invoice_line_item_id"],)).fetchone()
        if line:
            invoice = conn.execute("SELECT * FROM invoices WHERE invoice_id = ?", (line["invoice_id"],)).fetchone()
    charge_cents = int(line["line_amount_cents"] if line else (session["rate_cents_snapshot"] or session["approved_rate_cents"] or 0))
    if invoice and line:
        paid_cents = conn.execute(
            """SELECT COALESCE(SUM(pa.amount_cents), 0)
               FROM payment_allocations pa
               JOIN payments p ON p.payment_id = pa.payment_id
               WHERE pa.invoice_line_item_id = ?
                 AND pa.status = 'active'
                 AND p.status = 'posted'""",
            (line["invoice_line_item_id"],),
        ).fetchone()[0]
        reference = f"Invoice {invoice['invoice_number'] or invoice['invoice_id']}"
    else:
        paid_cents = conn.execute(
            """SELECT COALESCE(SUM(pa.amount_cents), 0)
               FROM payment_allocations pa
               JOIN payments p ON p.payment_id = pa.payment_id
               WHERE pa.session_id = ?
                 AND pa.status = 'active'
                 AND p.status = 'posted'""",
            (alloc["session_id"],),
        ).fetchone()[0]
        reference = "Session"
    remaining = max(0, charge_cents - int(paid_cents or 0))
    return {
        "allocation_id": alloc["allocation_id"],
        "session_id": alloc["session_id"],
        "invoice_line_item_id": alloc.get("invoice_line_item_id"),
        "invoice_id": invoice["invoice_id"] if invoice else "",
        "invoice_number": invoice["invoice_number"] if invoice else "",
        "reference_display": reference,
        "service_date": session["session_date"],
        "service_date_display": format_long_date(session["session_date"]),
        "amount_cents": alloc["amount_cents"],
        "amount_display": money(alloc["amount_cents"]),
        "charge_cents": charge_cents,
        "remaining_balance_cents": remaining,
        "remaining_balance_display": money(remaining),
    }


def _resolve_receipt_filing_owner(
    conn: sqlite3.Connection,
    allocations: list[dict[str, Any]],
    *,
    selected_person_id: str | None,
    require_selected: bool,
) -> dict[str, Any]:
    invoice_ids = sorted({row["invoice_id"] for row in allocations if row.get("invoice_id")})
    if invoice_ids and any(not row.get("invoice_id") for row in allocations):
        return {"selected": None, "eligible_clients": [], "source": "ambiguous", "message": "This payment mixes invoice and non-invoice allocations. Receipt creation is not supported for this payment."}
    if len(invoice_ids) > 1:
        owners = []
        for invoice_id in invoice_ids:
            filing = resolve_invoice_filing_owner(conn, invoice_id)
            selected = filing.get("selected")
            if not selected:
                return {"selected": None, "eligible_clients": [], "source": "ambiguous", "message": "One invoice allocation does not have a resolved filing owner."}
            owners.append(selected)
        owner_ids = {owner["person_id"] for owner in owners}
        if len(owner_ids) != 1:
            return {"selected": None, "eligible_clients": owners, "source": "ambiguous", "message": "This payment covers allocations with different filing owners. Create separate receipts after splitting the payment."}
        return {"selected": owners[0], "eligible_clients": owners, "source": "invoice_filing_owner", "message": ""}
    if len(invoice_ids) == 1:
        filing = resolve_invoice_filing_owner(conn, invoice_ids[0])
        if not filing.get("selected"):
            return {"selected": None, "eligible_clients": filing.get("eligible_clients", []), "source": "invoice_filing_owner", "message": filing.get("message") or "Invoice filing owner must be resolved before creating a receipt."}
        return {"selected": filing["selected"], "eligible_clients": [filing["selected"]], "source": "invoice_filing_owner", "message": ""}
    eligible = _eligible_session_participants(conn, [row["session_id"] for row in allocations])
    selected = None
    chosen = str(selected_person_id or "").strip()
    if chosen:
        selected = next((person for person in eligible if person["person_id"] == chosen), None)
        if not selected:
            raise ValueError("File receipt under must be one of the eligible session participants.")
    elif len(eligible) == 1:
        selected = eligible[0]
    message = ""
    if not selected and len(eligible) > 1:
        message = "Choose which session participant this receipt should be filed under."
    elif not selected:
        message = "Add an eligible session participant before creating this receipt."
    if require_selected and not selected:
        raise ValueError(message)
    return {"selected": selected, "eligible_clients": eligible, "source": "session_participant", "message": message}


def _eligible_session_participants(conn: sqlite3.Connection, session_ids: list[str]) -> list[dict[str, Any]]:
    if not session_ids:
        return []
    placeholders = ",".join("?" * len(session_ids))
    rows = conn.execute(
        f"""
        SELECT DISTINCT p.person_id, p.display_name, p.person_code
        FROM session_participants sp
        JOIN people p ON p.person_id = sp.person_id
        WHERE sp.session_id IN ({placeholders}) AND p.active = 1
        ORDER BY p.display_name, p.person_id
        """,
        session_ids,
    ).fetchall()
    return [dict(row) for row in rows]


def _next_receipt_number(conn: sqlite3.Connection, year: int) -> str:
    row = conn.execute("SELECT last_value FROM receipt_sequences WHERE sequence_year = ?", (year,)).fetchone()
    next_value = int(row["last_value"]) + 1 if row else 1
    now = now_iso()
    conn.execute(
        """INSERT INTO receipt_sequences (sequence_year, last_value, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(sequence_year) DO UPDATE SET last_value = excluded.last_value, updated_at = excluded.updated_at""",
        (year, next_value, now),
    )
    return f"R-{year}-{next_value:04d}"


def _receipt_month_label(value: str) -> str:
    try:
        parsed = date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return "Unknown Month"
    return parsed.strftime("%B %Y")


def _payment_method_label(value: str) -> str:
    return {
        "zelle": "Zelle",
        "check": "Check",
        "cash": "Cash",
        "ach": "ACH",
        "card": "Card",
        "other": "Other",
    }.get(str(value or ""), str(value or "").title())


def _audit(conn: sqlite3.Connection, receipt_id: str, action: str, details: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO audit_log (id, entity_type, entity_id, action, details, created_at) VALUES (?, 'payment_receipt', ?, ?, ?, ?)",
        (new_id(), receipt_id, action, json.dumps(details, sort_keys=True), now_iso()),
    )
