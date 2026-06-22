from __future__ import annotations

import hashlib
import base64
import io
import os
import re
from pathlib import Path
from typing import Any


def generate_invoice_pdf(invoice: dict[str, Any], lines: list[dict[str, Any]], output_path: str | Path) -> str:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_RIGHT
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            Image,
            KeepTogether,
            LongTable,
            PageBreak,
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
        raise FileExistsError(f"Finalized invoice PDF already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    styles = getSampleStyleSheet()
    body = ParagraphStyle("InvoiceBody", parent=styles["BodyText"], fontName="Helvetica", fontSize=9, leading=12)
    small = ParagraphStyle("InvoiceSmall", parent=body, fontSize=8, leading=10, textColor=colors.HexColor("#42526A"))
    label = ParagraphStyle("InvoiceLabel", parent=body, fontSize=8, leading=10, textColor=colors.HexColor("#526171"), spaceAfter=3)
    title = ParagraphStyle("InvoiceTitle", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=26, leading=28, alignment=TA_RIGHT, textColor=colors.HexColor("#102A43"))
    total_style = ParagraphStyle("InvoiceTotal", parent=body, fontName="Helvetica-Bold", fontSize=13, leading=16, alignment=TA_RIGHT)

    def para(value: Any, style=body):
        return Paragraph(_escape(value), style)

    def page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#64748B"))
        number = invoice.get("invoice_number") or "Draft"
        canvas.drawString(doc.leftMargin, 0.42 * inch, f"Invoice {number}")
        canvas.drawRightString(letter[0] - doc.rightMargin, 0.42 * inch, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(temp_path), pagesize=letter, rightMargin=0.55 * inch, leftMargin=0.55 * inch,
        topMargin=0.55 * inch, bottomMargin=0.68 * inch, title=f"Invoice {invoice.get('invoice_number') or 'Draft'}",
    )
    story = []
    logo_flowable = _logo_flowable(invoice.get("logo_reference_snapshot"), 3.15 * inch, 1.35 * inch)
    if logo_flowable is None:
        fallback = [para(invoice.get("business_name_snapshot") or "Business", styles["Heading2"])]
        provider = " ".join(filter(None, [invoice.get("provider_name_snapshot"), invoice.get("credentials_snapshot")]))
        for value in (provider, invoice.get("business_address_snapshot"), invoice.get("business_phone_snapshot"), invoice.get("business_email_snapshot")):
            if value:
                fallback.append(para(value, small))
        logo_cell = fallback
    else:
        logo_cell = [logo_flowable]
        if invoice.get("show_email_below_logo_snapshot") and invoice.get("business_email_snapshot"):
            logo_cell.append(para(invoice["business_email_snapshot"], small))
    meta = [para("INVOICE", title)]
    for key, value in (
        ("Invoice number", invoice.get("invoice_number") or "Draft"),
        ("Invoice date", _date(invoice.get("invoice_date"))),
        ("Billing period", f"{_date(invoice.get('billing_period_start'))} - {_date(invoice.get('billing_period_end'))}"),
    ):
        meta.append(Paragraph(f"<b>{_escape(key)}:</b> {_escape(value)}", small))
    header = Table([[logo_cell, meta]], colWidths=[4.25 * inch, 2.15 * inch], hAlign="LEFT")
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (1, 0), (1, 0), "RIGHT"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story.extend([header, Spacer(1, 0.28 * inch), para("BILL TO", label)])
    for value in (invoice.get("bill_to_name_snapshot"), invoice.get("bill_to_address_snapshot"), invoice.get("bill_to_email_snapshot")):
        if value:
            story.append(para(value))
    story.append(Spacer(1, 0.26 * inch))

    data = [[para("Date", small), para("Participants", small), para("Service", small), para("Duration", small), para("Amount", small)]]
    for line in lines:
        data.append([
            para(_date(line.get("service_date"))),
            para(line.get("participants_snapshot")),
            para(line.get("description_snapshot") or line.get("service_name_snapshot")),
            para(f"{line['duration_minutes']} min" if line.get("duration_minutes") is not None else "-"),
            para(_money(line.get("line_amount_cents"))),
        ])
    table = LongTable(data, colWidths=[0.78 * inch, 2.05 * inch, 2.15 * inch, 0.72 * inch, 0.72 * inch], repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EAF0F6")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#102A43")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor("#9FB3C8")),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#D9E2EC")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(table)
    total = invoice.get("total_cents", 0)
    footer = [
        Spacer(1, 0.22 * inch),
        Table([[para(invoice.get("total_label_snapshot") or "TOTAL DUE", total_style), para(_money(total), total_style)]], colWidths=[5.45 * inch, 0.95 * inch], style=TableStyle([("LINEABOVE", (0, 0), (-1, 0), 1, colors.HexColor("#102A43")), ("TOPPADDING", (0, 0), (-1, -1), 9), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)])),
        Spacer(1, 0.28 * inch),
        Paragraph(f"<b>Please make all checks payable to:</b> {_escape(invoice.get('payee_name_snapshot') or '')}", body),
        Spacer(1, 0.08 * inch),
        Paragraph(f"<b>Please send payment to:</b> {_escape(invoice.get('payment_address_snapshot') or '')}", body),
    ]
    story.append(KeepTogether(footer))
    try:
        doc.build(story, onFirstPage=page, onLaterPages=page)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError("Invoice PDF generation did not produce a valid file.")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _logo_flowable(raw_path: str | None, max_width: float, max_height: float):
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    if not path.is_file():
        return None
    try:
        from reportlab.platypus import Image
        if path.suffix.casefold() == ".svg":
            source = path.read_text(encoding="utf-8", errors="ignore")
            match = re.search(r"(?:href|xlink:href)=[\"']data:image/(?:png|jpeg|jpg);base64,([^\"']+)", source, re.IGNORECASE)
            if match:
                image = Image(io.BytesIO(base64.b64decode(re.sub(r"\s+", "", match.group(1)))))
                scale = min(max_width / image.imageWidth, max_height / image.imageHeight)
                image.drawWidth = image.imageWidth * scale
                image.drawHeight = image.imageHeight * scale
                return image
            try:
                from svglib.svglib import svg2rlg
            except ImportError:
                return None
            drawing = svg2rlg(str(path))
            if drawing is None or not drawing.width or not drawing.height:
                return None
            scale = min(max_width / drawing.width, max_height / drawing.height)
            drawing.scale(scale, scale)
            drawing.width *= scale
            drawing.height *= scale
            return drawing
        image = Image(str(path))
        scale = min(max_width / image.imageWidth, max_height / image.imageHeight)
        image.drawWidth = image.imageWidth * scale
        image.drawHeight = image.imageHeight * scale
        return image
    except Exception:
        return None


def _money(cents: Any) -> str:
    return f"${int(cents or 0) / 100:,.2f}"


def _date(value: Any) -> str:
    text = str(value or "")
    if len(text) >= 10 and text[4] == "-":
        return f"{text[5:7]}/{text[8:10]}/{text[:4]}"
    return text


def _escape(value: Any) -> str:
    return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")
