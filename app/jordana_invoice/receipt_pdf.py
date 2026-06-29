from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .invoice_rendering import money as format_money


def generate_receipt_pdf(
    snapshot: dict[str, Any],
    output_path: str | Path,
) -> str:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_RIGHT
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            Image,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as error:
        raise RuntimeError("PDF generation requires the project PDF dependencies. Run: python -m pip install -e .") from error

    path = Path(output_path)
    if path.exists():
        raise FileExistsError(f"Finalized receipt PDF already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")

    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "ReceiptBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=10.25,
        leading=13,
        textColor=colors.HexColor("#102A43"),
    )
    small = ParagraphStyle(
        "ReceiptSmall",
        parent=body,
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#42526A"),
    )
    label = ParagraphStyle(
        "ReceiptLabel",
        parent=body,
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#526171"),
    )
    title = ParagraphStyle(
        "ReceiptTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=28,
        leading=31,
        alignment=TA_RIGHT,
        textColor=colors.HexColor("#102A43"),
    )
    total = ParagraphStyle(
        "ReceiptTotal",
        parent=body,
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=18,
        textColor=colors.HexColor("#102A43"),
    )

    def para(value: Any, style=body):
        return Paragraph(_escape(value), style)

    def page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(doc.leftMargin, 0.42 * inch, f"Receipt {snapshot.get('receipt_number') or ''}")
        canvas.drawRightString(letter[0] - doc.rightMargin, 0.42 * inch, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(temp_path),
        pagesize=letter,
        rightMargin=0.50 * inch,
        leftMargin=0.50 * inch,
        topMargin=0.50 * inch,
        bottomMargin=0.55 * inch,
        title=f"Payment Receipt {snapshot.get('receipt_number') or ''}",
    )

    story = []
    logo = _logo_flowable(snapshot.get("logo_path"), 1.50 * inch, 1.05 * inch)
    sender = [para(line, small) for line in snapshot.get("sender_lines") or []]
    left = ([logo, Spacer(1, 0.08 * inch)] if logo else [para(snapshot.get("business_name") or "Business", styles["Heading2"])]) + sender
    meta_rows = [
        [para("Receipt Number", label), para(snapshot.get("receipt_number") or "", body)],
        [para("Payment Date", label), para(snapshot.get("payment_date_display") or "", body)],
        [para("Payment Method", label), para(snapshot.get("payment_method_display") or "", body)],
    ]
    if snapshot.get("reference_number"):
        meta_rows.append([para("Reference", label), para(snapshot.get("reference_number"), body)])
    meta = [para("PAYMENT RECEIPT", title), Spacer(1, 0.08 * inch), Table(meta_rows, colWidths=[1.25 * inch, 1.65 * inch])]
    header = Table([[left, meta]], colWidths=[4.55 * inch, 2.95 * inch])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(header)
    story.append(Spacer(1, 0.24 * inch))

    story.append(para("BILL TO", label))
    for line in snapshot.get("bill_to_lines") or []:
        story.append(para(line, body))
    if snapshot.get("received_from_name"):
        story.append(para(f"Received From: {snapshot['received_from_name']}", small))
    story.append(Spacer(1, 0.18 * inch))

    rows = [[
        para("Invoice / Session", label),
        para("Date", label),
        para("Amount Paid", label),
        para("Remaining Balance", label),
    ]]
    for allocation in snapshot.get("allocations") or []:
        rows.append([
            para(allocation.get("reference_display") or "", body),
            para(allocation.get("service_date_display") or "", body),
            para(format_money(allocation.get("amount_cents")), body),
            para(format_money(allocation.get("remaining_balance_cents")), body),
        ])
    table = Table(rows, colWidths=[2.75 * inch, 1.45 * inch, 1.45 * inch, 1.85 * inch], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F0F4F8")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BCCCDC")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(table)
    story.append(Spacer(1, 0.18 * inch))

    summary_rows = [
        [para("Amount Received", total), para(format_money(snapshot.get("amount_cents")), total)],
    ]
    if int(snapshot.get("unapplied_cents") or 0) > 0:
        summary_rows.append([para("Unapplied Amount", body), para(format_money(snapshot.get("unapplied_cents")), body)])
    if snapshot.get("paid_in_full"):
        summary_rows.append([para("PAID IN FULL", total), para("", total)])
    summary = Table(summary_rows, colWidths=[5.75 * inch, 1.75 * inch])
    summary.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEABOVE", (0, 0), (-1, 0), 0.7, colors.HexColor("#102A43")),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(summary)

    doc.build(story, onFirstPage=page, onLaterPages=page)
    temp_path.replace(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _logo_flowable(path_value: Any, max_width: float, max_height: float):
    if not path_value:
        return None
    try:
        from reportlab.platypus import Image
    except ImportError:
        return None
    path = Path(str(path_value)).expanduser()
    if not path.is_file():
        return None
    try:
        image = Image(str(path))
        scale = min(max_width / image.imageWidth, max_height / image.imageHeight, 1)
        image.drawWidth = image.imageWidth * scale
        image.drawHeight = image.imageHeight * scale
        return image
    except Exception:
        return None


def _escape(value: Any) -> str:
    return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
