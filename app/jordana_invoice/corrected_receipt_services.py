"""Immutable, separately numbered corrections to payment receipts.

These documents correct a displayed service label only. They never change the
finalized invoice, its PDF, the payment, or any allocation.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .invoice_services import _filing_owner_folder, _sanitize_path_part
from .receipt_pdf import generate_receipt_pdf
from .receipt_services import (
    _build_receipt_snapshot,
    _invoice_insurance_coding,
    _next_receipt_number,
    _receipt_insurance_coding,
    _receipt_month_label,
    get_payment_receipt,
)
from .session_types import get_user_facing_session_label, validate_billing_session_type
from .util import new_id, now_iso


def list_corrected_receipts(conn: sqlite3.Connection, payment_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT correction_id, payment_id, original_receipt_id, supersedes_correction_id,
                  version, receipt_number, allocation_id, source_invoice_id,
                  previous_description_snapshot, corrected_description_snapshot,
                  reason, created_at
           FROM corrected_receipts WHERE payment_id = ? ORDER BY version""",
        (payment_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_corrected_receipt(conn: sqlite3.Connection, correction_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM corrected_receipts WHERE correction_id = ?", (correction_id,)
    ).fetchone()
    return dict(row) if row else None


def corrected_receipt_options(conn: sqlite3.Connection, payment_id: str) -> dict[str, Any]:
    """List frozen display values eligible for an explicit correction."""
    base, original, latest = _base_snapshot(conn, payment_id, filing_owner_person_id=None, require_owner=False)
    current = _current_allocations(conn, payment_id)
    _check_allocation_set(base, current)
    lines = []
    for row in base.get("allocations") or []:
        allocation_id = str(row.get("allocation_id") or "")
        if allocation_id not in current:
            continue
        source = _invoice_allocation(conn, payment_id, allocation_id)
        if source is None:
            continue
        lines.append({
            "allocation_id": allocation_id,
            "invoice_number": source["invoice_number"],
            "service_date_display": row.get("service_date_display") or "",
            "current_description": row.get("description_display") or "",
        })
    return {
        "lines": lines,
        "latest_correction_id": latest["correction_id"] if latest else None,
        "original_receipt_number": original["receipt_number"] if original else None,
        "filing_owner": base.get("filing_owner") or {},
    }


def preview_corrected_receipt(
    conn: sqlite3.Connection,
    payment_id: str,
    *,
    allocation_id: str,
    billing_session_type: str,
    custom_description: str | None = None,
    filing_owner_person_id: str | None = None,
    expected_latest_correction_id: str | None = None,
) -> dict[str, Any]:
    prepared = _prepare(
        conn, payment_id, allocation_id=allocation_id,
        billing_session_type=billing_session_type,
        custom_description=custom_description,
        filing_owner_person_id=filing_owner_person_id,
        expected_latest_correction_id=expected_latest_correction_id,
        require_owner=False,
    )
    snapshot = _preview_snapshot(prepared["snapshot"])
    return {
        "snapshot": snapshot,
        "preview_digest": _snapshot_digest(snapshot),
        "previous_description": prepared["previous_description"],
        "corrected_description": prepared["corrected_description"],
        "latest_correction_id": prepared["latest"]["correction_id"] if prepared["latest"] else None,
    }


def create_corrected_receipt(
    conn: sqlite3.Connection,
    payment_id: str,
    *,
    allocation_id: str,
    billing_session_type: str,
    custom_description: str | None = None,
    reason: str,
    filing_owner_person_id: str | None = None,
    expected_latest_correction_id: str | None = None,
    expected_preview_digest: str | None = None,
    pdf_root: str | Path | None = None,
) -> dict[str, Any]:
    reason = _short_text(reason, "Correction reason", 240)
    if not expected_preview_digest:
        raise ValueError("Preview the corrected receipt again before creating it.")
    request_digest = _request_digest(
        payment_id=payment_id, allocation_id=allocation_id,
        billing_session_type=billing_session_type,
        custom_description=custom_description, reason=reason,
        filing_owner_person_id=filing_owner_person_id,
        expected_latest_correction_id=expected_latest_correction_id,
        expected_preview_digest=expected_preview_digest,
    )
    root = Path(pdf_root or os.getenv("JORDANA_RECEIPTS_DIR", "Receipts")).expanduser()
    pdf_path: Path | None = None
    pdf_existed_before = False
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as error:
        if "locked" in str(error).lower():
            raise RuntimeError("Cannot create corrected receipt: database is busy. Please retry.") from error
        raise
    try:
        # A repeated exact request returns only the document made by that request.
        prior_request = conn.execute(
            "SELECT * FROM corrected_receipts WHERE request_digest = ?", (request_digest,)
        ).fetchone()
        if prior_request:
            conn.commit()
            prior = dict(prior_request)
            return {"correction": prior, "snapshot": json.loads(prior["snapshot_json"]),
                    "created": False, "preview_digest": expected_preview_digest}
        latest = _latest(conn, payment_id)
        if latest and latest["correction_id"] != expected_latest_correction_id:
            raise ValueError("A newer corrected receipt exists. Reopen the payment and preview again.")
        prepared = _prepare(
            conn, payment_id, allocation_id=allocation_id,
            billing_session_type=billing_session_type,
            custom_description=custom_description,
            filing_owner_person_id=filing_owner_person_id,
            expected_latest_correction_id=expected_latest_correction_id,
            require_owner=True,
        )
        preview_digest = _snapshot_digest(_preview_snapshot(prepared["snapshot"]))
        if preview_digest != expected_preview_digest:
            raise ValueError("The corrected receipt changed since preview. Preview it again before creating it.")
        payment = conn.execute("SELECT received_at FROM payments WHERE payment_id = ?", (payment_id,)).fetchone()
        number = _next_receipt_number(conn, int(str(payment["received_at"])[:4]))
        snapshot = prepared["snapshot"]
        snapshot["receipt_number"] = number
        snapshot["document_title"] = "CORRECTED RECEIPT"
        filing = snapshot["filing_owner"]["selected"]
        client_folder = _filing_owner_folder(conn, root, filing)
        month_folder = _sanitize_path_part(_receipt_month_label(snapshot["filing_month"]), "Unknown Month")
        pdf_path = client_folder / month_folder / f"Corrected_Receipt_{number}.pdf"
        pdf_existed_before = pdf_path.exists()
        if pdf_existed_before:
            raise ValueError("A corrected receipt PDF already exists at the target location.")
        checksum = generate_receipt_pdf(snapshot, pdf_path)
        correction_id = new_id()
        now = now_iso()
        latest = prepared["latest"]
        original = prepared["original"]
        source = prepared["source"]
        conn.execute(
            """INSERT INTO corrected_receipts (
              correction_id, payment_id, original_receipt_id, supersedes_correction_id,
              version, receipt_number, allocation_id, invoice_line_item_id,
              source_invoice_id, previous_description_snapshot, corrected_description_snapshot,
              billing_session_type, custom_description_snapshot, reason, request_digest,
              snapshot_json, pdf_path, pdf_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                correction_id, payment_id, original["receipt_id"] if original else None,
                latest["correction_id"] if latest else None,
                int(latest["version"]) + 1 if latest else 1, number, allocation_id,
                source["invoice_line_item_id"], source["invoice_id"],
                prepared["previous_description"], prepared["corrected_description"],
                billing_session_type, custom_description.strip() if billing_session_type == "custom" and custom_description else None,
                reason, request_digest, json.dumps(snapshot, sort_keys=True), str(pdf_path), checksum, now,
            ),
        )
        conn.execute(
            "INSERT INTO audit_log (id, entity_type, entity_id, action, details, created_at) "
            "VALUES (?, 'corrected_receipt', ?, 'receipt_corrected', ?, ?)",
            (new_id(), correction_id, json.dumps({
                "payment_id": payment_id, "allocation_id": allocation_id,
                "source_invoice_id": source["invoice_id"],
                "original_receipt_id": original["receipt_id"] if original else None,
                "supersedes_correction_id": latest["correction_id"] if latest else None,
                "previous_description": prepared["previous_description"],
                "corrected_description": prepared["corrected_description"],
                "reason": reason, "pdf_sha256": checksum,
            }, sort_keys=True), now),
        )
        conn.commit()
        return {"correction": get_corrected_receipt(conn, correction_id), "snapshot": snapshot,
                "created": True, "preview_digest": preview_digest}
    except Exception:
        conn.rollback()
        if pdf_path and not pdf_existed_before and pdf_path.exists():
            pdf_path.unlink()
        raise


def corrected_receipt_pdf_path(
    conn: sqlite3.Connection, correction_id: str, *, pdf_root: str | Path | None = None
) -> Path:
    correction = get_corrected_receipt(conn, correction_id)
    if not correction:
        raise ValueError("Corrected receipt was not found.")
    path = Path(correction["pdf_path"]).expanduser()
    root = Path(pdf_root or os.getenv("JORDANA_RECEIPTS_DIR", "Receipts")).expanduser()
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except (OSError, ValueError):
        raise ValueError("Stored corrected receipt path is outside the configured receipt folder.")
    if not path.is_file():
        raise FileNotFoundError("The corrected receipt PDF is missing from the expected location.")
    if hashlib.sha256(path.read_bytes()).hexdigest() != correction["pdf_sha256"]:
        raise ValueError("The corrected receipt PDF does not match its recorded checksum.")
    return path


def _prepare(
    conn: sqlite3.Connection,
    payment_id: str,
    *, allocation_id: str, billing_session_type: str,
    custom_description: str | None, filing_owner_person_id: str | None,
    expected_latest_correction_id: str | None, require_owner: bool,
) -> dict[str, Any]:
    base, original, latest = _base_snapshot(
        conn, payment_id, filing_owner_person_id=filing_owner_person_id,
        require_owner=require_owner,
    )
    if (latest["correction_id"] if latest else None) != expected_latest_correction_id:
        raise ValueError("A newer corrected receipt exists. Reopen the payment and preview again.")
    current = _current_allocations(conn, payment_id)
    _check_allocation_set(base, current)
    source = _invoice_allocation(conn, payment_id, allocation_id)
    if source is None:
        raise ValueError("Choose an active allocation on a finalized invoice.")
    snapshot = copy.deepcopy(base)
    target = next((row for row in snapshot.get("allocations") or [] if row.get("allocation_id") == allocation_id), None)
    if target is None:
        raise ValueError("The receipt allocation no longer matches the finalized invoice. Review the payment first.")
    if target.get("invoice_line_item_id") != source["invoice_line_item_id"]:
        # A paid-at-session receipt can predate the finalized invoice. Reconcile
        # only this stable allocation; never edit the frozen original receipt.
        if (target.get("invoice_line_item_id") or target.get("invoice_id")
                or target.get("session_id") != source["session_id"]):
            raise ValueError("The receipt allocation no longer matches the finalized invoice. Review the payment first.")
        target["invoice_line_item_id"] = source["invoice_line_item_id"]
        target["invoice_id"] = source["invoice_id"]
        target["invoice_number"] = source["invoice_number"]
        target["reference_display"] = f"Invoice {source['invoice_number']}"
        invoice = conn.execute("SELECT * FROM invoices WHERE invoice_id = ?", (source["invoice_id"],)).fetchone()
        target["insurance_coding"] = _invoice_insurance_coding(invoice)
        snapshot["insurance_coding"] = _receipt_insurance_coding(snapshot.get("allocations") or [])
    previous = str(target.get("description_display") or "").strip()
    corrected = _description_from_type(source, billing_session_type, custom_description)
    if corrected == previous:
        raise ValueError("The corrected session type must differ from the current receipt value.")
    target["description_display"] = corrected
    snapshot["snapshot_version"] = 2
    snapshot["correction"] = {
        "source_invoice_number": source["invoice_number"],
        "original_receipt_number": original["receipt_number"] if original else "",
        "supersedes_receipt_number": latest["receipt_number"] if latest else (original["receipt_number"] if original else ""),
        "allocation_id": allocation_id,
        "previous_description": previous,
        "corrected_description": corrected,
    }
    snapshot["document_title"] = "CORRECTED RECEIPT"
    return {
        "snapshot": snapshot, "original": original, "latest": latest,
        "source": source, "previous_description": previous,
        "corrected_description": corrected,
    }


def _preview_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(snapshot)
    result["document_title"] = "DRAFT CORRECTED RECEIPT"
    result["receipt_number"] = ""
    return result


def _snapshot_digest(snapshot: dict[str, Any]) -> str:
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _request_digest(**fields: Any) -> str:
    encoded = json.dumps(fields, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _base_snapshot(
    conn: sqlite3.Connection, payment_id: str, *,
    filing_owner_person_id: str | None, require_owner: bool,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    payment = conn.execute("SELECT status FROM payments WHERE payment_id = ?", (payment_id,)).fetchone()
    if not payment:
        raise ValueError("Payment was not found.")
    if payment["status"] != "posted":
        raise ValueError("Only posted payments can have corrected receipts.")
    latest = _latest(conn, payment_id)
    original = get_payment_receipt(conn, payment_id)
    if latest:
        base = json.loads(latest["snapshot_json"])
    elif original:
        base = json.loads(original["snapshot_json"])
    else:
        base = _build_receipt_snapshot(
            conn, payment_id, receipt_number=None,
            filing_owner_person_id=filing_owner_person_id,
            require_filing_owner=require_owner,
        )
    if base.get("payment_id") != payment_id:
        raise ValueError("The stored receipt does not match this payment.")
    if require_owner and not (base.get("filing_owner") or {}).get("selected"):
        raise ValueError("Choose which client the corrected receipt should be filed under.")
    return base, original, latest


def _latest(conn: sqlite3.Connection, payment_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM corrected_receipts WHERE payment_id = ? ORDER BY version DESC LIMIT 1",
        (payment_id,),
    ).fetchone()
    return dict(row) if row else None


def _current_allocations(conn: sqlite3.Connection, payment_id: str) -> dict[str, int]:
    return {row["allocation_id"]: int(row["amount_cents"]) for row in conn.execute(
        "SELECT allocation_id, amount_cents FROM payment_allocations WHERE payment_id = ? AND status = 'active'",
        (payment_id,),
    ).fetchall()}


def _check_allocation_set(snapshot: dict[str, Any], current: dict[str, int]) -> None:
    frozen = {
        str(row.get("allocation_id") or ""): int(row.get("amount_cents") or 0)
        for row in snapshot.get("allocations") or []
    }
    if not current or frozen != current:
        raise ValueError("Payment allocations changed since this receipt snapshot. Review the payment before correcting it.")


def _invoice_allocation(conn: sqlite3.Connection, payment_id: str, allocation_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT pa.allocation_id, pa.session_id, li.invoice_line_item_id, li.appointment_status_snapshot,
                  i.invoice_id, i.invoice_number
           FROM payment_allocations pa
           JOIN invoice_line_items li ON li.invoice_line_item_id = pa.invoice_line_item_id
           JOIN invoices i ON i.invoice_id = li.invoice_id
           WHERE pa.payment_id = ? AND pa.allocation_id = ? AND pa.status = 'active'
             AND i.status = 'finalized'""",
        (payment_id, allocation_id),
    ).fetchone()


def _description_from_type(
    source: sqlite3.Row, billing_session_type: str, custom_description: str | None,
) -> str:
    try:
        billing_session_type = validate_billing_session_type(billing_session_type)
    except ValueError as error:
        raise ValueError("Choose a supported billing session type.") from error
    custom = _short_text(custom_description, "Custom session type", 160) if billing_session_type == "custom" else None
    return get_user_facing_session_label(
        billing_session_type, source["appointment_status_snapshot"], custom,
    )


def _short_text(value: str | None, label: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} is required.")
    text = value.strip()
    if not text:
        raise ValueError(f"{label} is required.")
    if len(text) > limit or any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ValueError(f"{label} must be one line and at most {limit} characters.")
    return text
