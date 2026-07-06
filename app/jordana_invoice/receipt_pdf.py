from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from .invoice_pdf import _generate_invoice_pdf_bytes
from .invoice_rendering import money as format_money


def generate_receipt_pdf(
    snapshot: dict[str, Any],
    output_path: str | Path,
) -> str:
    """Generate a receipt PDF with the canonical invoice layout."""
    path = Path(output_path)
    if path.exists():
        raise FileExistsError(f"Finalized receipt PDF already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    number = snapshot.get("receipt_number") or "Draft"
    render_model = _receipt_render_model(snapshot)
    invoice = {
        "invoice_number": number,
        "invoice_date": snapshot.get("payment_date") or "",
        "status": "finalized" if snapshot.get("receipt_number") else "draft",
        "business_name_snapshot": snapshot.get("business_name") or "Business",
        "total_cents": snapshot.get("amount_cents") or 0,
        "total_label_snapshot": "AMOUNT PAID",
        "notes": render_model.get("notes") or "",
    }
    pdf_bytes = _generate_invoice_pdf_bytes(
        invoice,
        [],
        render_model=render_model,
        meta_rows=[
            ("", f"Paid on {snapshot.get('payment_date_display') or snapshot.get('payment_date') or ''}".strip()),
            ("", number),
        ],
        page_footer_label=f"Receipt {number}",
        doc_title=f"Receipt {number}",
        document_title="RECEIPT",
    )
    try:
        temp_path.write_bytes(pdf_bytes)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError("Receipt PDF generation did not produce a valid file.")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _receipt_render_model(snapshot: dict[str, Any]) -> dict[str, Any]:
    note_parts = []
    if snapshot.get("payment_method_display"):
        note_parts.append(f"Payment method: {snapshot['payment_method_display']}")
    if snapshot.get("reference_number"):
        note_parts.append(f"Reference: {snapshot['reference_number']}")
    if snapshot.get("received_from_name"):
        note_parts.append(f"Received from: {snapshot['received_from_name']}")
    if int(snapshot.get("unapplied_cents") or 0) > 0:
        note_parts.append(f"Unapplied amount: {format_money(snapshot.get('unapplied_cents'))}")
    if snapshot.get("paid_in_full"):
        note_parts.append("Paid in full.")

    return {
        "logo_path": snapshot.get("logo_path"),
        "sender_lines": snapshot.get("sender_lines") or [],
        "bill_to_lines": snapshot.get("bill_to_lines") or [],
        "invoice_number_display": snapshot.get("receipt_number") or "DRAFT",
        "invoice_date_display": snapshot.get("payment_date_display") or snapshot.get("payment_date") or "",
        "billing_period_display": "",
        "lines": [_receipt_line_model(row) for row in snapshot.get("allocations") or []],
        "payment_title": "",
        "payment_name": "",
        "payment_lines": [],
        "payment_zelle_line": "",
        "payment_zelle_title": "",
        "payment_zelle_value": "",
        "payment_account_name_line": "",
        "notes": " ".join(note_parts),
        "total_label": "AMOUNT PAID",
        "total_display": format_money(snapshot.get("amount_cents")),
        "account_summary": None,
        "insurance_coding": [],
        "suppress_payment_instructions": True,
    }


def _receipt_line_model(allocation: dict[str, Any]) -> dict[str, Any]:
    return {
        "service_date_display": allocation.get("service_date_display") or "",
        "participants_display": allocation.get("participants_display") or "",
        "description_display": allocation.get("description_display") or allocation.get("reference_display") or "",
        "duration_display": allocation.get("duration_display") or "",
        "amount_display": format_money(allocation.get("amount_cents")),
    }
