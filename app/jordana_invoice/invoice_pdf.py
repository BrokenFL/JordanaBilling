from __future__ import annotations

import hashlib
import base64
import io
import os
import re
from pathlib import Path
from typing import Any

from .invoice_rendering import build_invoice_render_model, money as format_money

POINTS_PER_INCH = 72.0
LETTER_PAGE_WIDTH = 8.5 * POINTS_PER_INCH
LETTER_PAGE_HEIGHT = 11.0 * POINTS_PER_INCH
LEFT_RIGHT_MARGIN = 0.50 * POINTS_PER_INCH
TOP_MARGIN = 0.50 * POINTS_PER_INCH
BOTTOM_MARGIN = 0.55 * POINTS_PER_INCH
CONTENT_WIDTH = LETTER_PAGE_WIDTH - (2 * LEFT_RIGHT_MARGIN)

BODY_FONT_SIZE = 10.25
BODY_LEADING = 13.0
SMALL_FONT_SIZE = 9.0
SMALL_LEADING = 11.0
LABEL_FONT_SIZE = 9.0
LABEL_LEADING = 11.0
TITLE_FONT_SIZE = 29.0
TITLE_LEADING = 31.0
TOTAL_FONT_SIZE = 14.5
TOTAL_LEADING = 18.0

LOGO_MAX_WIDTH = 2.1004 * POINTS_PER_INCH
LOGO_MAX_HEIGHT = 1.3452 * POINTS_PER_INCH
LOGO_OPTICAL_RIGHT_SHIFT = 0.0

HEADER_LEFT_WIDTH = 3.65 * POINTS_PER_INCH
HEADER_RIGHT_WIDTH = CONTENT_WIDTH - HEADER_LEFT_WIDTH
HEADER_TO_TABLE_SPACING = (0.13 * POINTS_PER_INCH) + 5.0
META_TO_BILLTO_SPACING = 0.16 * POINTS_PER_INCH
TITLE_TO_META_SPACING = 0.10 * POINTS_PER_INCH
LOGO_TO_PROVIDER_SPACING = 0.08 * POINTS_PER_INCH
LOGO_TO_PROVIDER_SPACING_REDUCTION = 9.0
RIGHT_HEADER_BLOCK_WIDTH = 2.45 * POINTS_PER_INCH
META_LABEL_WIDTH = 1.00 * POINTS_PER_INCH
META_VALUE_WIDTH = 1.02 * POINTS_PER_INCH
TABLE_COLUMN_WIDTHS = [
    1.12 * POINTS_PER_INCH,
    1.65 * POINTS_PER_INCH,
    2.78 * POINTS_PER_INCH,
    0.85 * POINTS_PER_INCH,
    1.10 * POINTS_PER_INCH,
]
TOTAL_COLUMN_WIDTHS = [6.15 * POINTS_PER_INCH, 1.35 * POINTS_PER_INCH]

TABLE_ROW_TOP_PADDING = 9
TABLE_ROW_BOTTOM_PADDING = 9
TABLE_CELL_LEFT_PADDING = 6
TABLE_CELL_RIGHT_PADDING = 6
TABLE_HEADER_BORDER_WIDTH = 0.5
PAYMENT_FOOTER_MIN_CLEARANCE = 0.30 * POINTS_PER_INCH


def generate_invoice_pdf(
    invoice: dict[str, Any],
    lines: list[dict[str, Any]],
    output_path: str | Path,
    *,
    render_model: dict[str, Any] | None = None,
) -> str:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT, TA_RIGHT
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
    body = ParagraphStyle(
        "InvoiceBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=BODY_FONT_SIZE,
        leading=BODY_LEADING,
        textColor=colors.HexColor("#102A43"),
    )
    small = ParagraphStyle(
        "InvoiceSmall",
        parent=body,
        fontSize=SMALL_FONT_SIZE,
        leading=SMALL_LEADING,
        textColor=colors.HexColor("#42526A"),
    )
    table_header = ParagraphStyle(
        "InvoiceTableHeader",
        parent=small,
        fontName="Helvetica-Bold",
    )
    compact = ParagraphStyle(
        "InvoiceCompact",
        parent=body,
        fontSize=BODY_FONT_SIZE,
        leading=BODY_FONT_SIZE * 1.15,
        spaceAfter=0,
    )
    label = ParagraphStyle(
        "InvoiceLabel",
        parent=compact,
        fontName="Helvetica-Bold",
        fontSize=LABEL_FONT_SIZE,
        leading=LABEL_FONT_SIZE * 1.15,
        textColor=colors.HexColor("#526171"),
        spaceAfter=2,
    )
    title = ParagraphStyle(
        "InvoiceTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=TITLE_FONT_SIZE,
        leading=TITLE_LEADING,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#102A43"),
    )
    total_label_style = ParagraphStyle(
        "InvoiceTotalLabel",
        parent=body,
        fontName="Helvetica-Bold",
        fontSize=TOTAL_FONT_SIZE,
        leading=TOTAL_LEADING,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#102A43"),
    )
    total_amount_style = ParagraphStyle(
        "InvoiceTotalAmount",
        parent=total_label_style,
        alignment=TA_RIGHT,
    )
    meta_label = ParagraphStyle(
        "InvoiceMetaLabel",
        parent=small,
        fontName="Helvetica-Bold",
        alignment=TA_LEFT,
        textColor=colors.HexColor("#526171"),
    )
    meta_value = ParagraphStyle(
        "InvoiceMetaValue",
        parent=body,
        alignment=TA_LEFT,
    )
    payment_title_style = ParagraphStyle(
        "InvoicePaymentTitle",
        parent=body,
        fontName="Helvetica-Bold",
        fontSize=BODY_FONT_SIZE,
        leading=BODY_LEADING,
        spaceAfter=2,
    )

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
        str(temp_path), pagesize=letter, rightMargin=0.50 * inch, leftMargin=0.50 * inch,
        topMargin=0.50 * inch, bottomMargin=0.55 * inch, title=f"Invoice {invoice.get('invoice_number') or 'Draft'}",
    )
    render = render_model or build_invoice_render_model(invoice, lines)
    story = []
    meta = _build_meta_block(
        [
            ("Invoice Number", render.get("invoice_number_display") or ""),
            ("Invoice Date", render.get("invoice_date_display") or ""),
        ],
        title,
        meta_label,
        meta_value,
        para,
    )
    header = _build_header_table(
        render, meta, compact, label, styles["Heading2"], invoice.get("business_name_snapshot") or "",
    )
    story.extend([header, Spacer(1, HEADER_TO_TABLE_SPACING)])

    story.append(_build_session_table(render, para, table_header))
    story.append(Spacer(1, _footer_pushdown_height(render)))
    footer = _build_pdf_footer(
        render,
        int(invoice.get("total_cents") or 0),
        body,
        small,
        total_label_style,
        total_amount_style,
        payment_title_style,
    )
    footer.extend(_build_insurance_coding_flowables(render, small))
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


def _build_meta_block(
    rows: list[tuple[str, Any]],
    title_style: Any,
    meta_label_style: Any,
    meta_value_style: Any,
    para: Any,
    *,
    extra_title_flowables: list[Any] | None = None,
) -> list[Any]:
    from reportlab.platypus import Spacer, Table, TableStyle

    meta = [para("INVOICE", title_style)]
    meta.extend(extra_title_flowables or [])
    meta.append(Spacer(1, TITLE_TO_META_SPACING))
    meta_rows = [[para(label, meta_label_style), para(value, meta_value_style)] for label, value in rows]
    meta.append(
        Table(
            meta_rows,
            colWidths=[META_LABEL_WIDTH, META_VALUE_WIDTH],
            hAlign="RIGHT",
            style=TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]),
        )
    )
    return meta


def _build_header_table(
    render: dict[str, Any],
    meta: list[Any],
    compact_style: Any,
    label_style: Any,
    heading_style: Any,
    fallback_business_name: str,
) -> Any:
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle

    def para(value: Any, style=compact_style):
        return Paragraph(_escape(value), style)

    provider_label_style = ParagraphStyle(
        "InvoiceProviderLabel",
        parent=label_style,
        alignment=TA_CENTER,
    )
    provider_value_style = ParagraphStyle(
        "InvoiceProviderValue",
        parent=compact_style,
        alignment=TA_CENTER,
    )

    left = list(meta)
    left.append(Spacer(1, META_TO_BILLTO_SPACING))
    left.append(para("BILL TO", label_style))
    left.extend(para(value) for value in (render.get("bill_to_lines") or []) if value)

    logo_flowable = _logo_flowable(render.get("logo_path"), LOGO_MAX_WIDTH, LOGO_MAX_HEIGHT)
    right = []
    if logo_flowable is not None:
        right.append(_OpticallyShiftedFlowable(logo_flowable, LOGO_OPTICAL_RIGHT_SHIFT))
    else:
        right.append(Paragraph(_escape(fallback_business_name or "Business"), heading_style))
    right.append(Spacer(1, LOGO_TO_PROVIDER_SPACING + provider_label_style.leading - LOGO_TO_PROVIDER_SPACING_REDUCTION))
    right.extend(Paragraph(_escape(value), provider_value_style) for value in (render.get("sender_lines") or []) if value)

    right_block = Table(
        [[right]],
        colWidths=[RIGHT_HEADER_BLOCK_WIDTH],
        hAlign="RIGHT",
        style=TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]),
    )

    header = Table([[left, [right_block]]], colWidths=[HEADER_LEFT_WIDTH, HEADER_RIGHT_WIDTH], hAlign="LEFT")
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return header


def _build_session_table(render: dict[str, Any], para: Any, table_header_style: Any) -> Any:
    from reportlab.lib import colors
    from reportlab.platypus import LongTable, TableStyle

    data = [[
        para("Date", table_header_style),
        para("Participants", table_header_style),
        para("Service", table_header_style),
        para("Duration", table_header_style),
        para("Amount", table_header_style),
    ]]
    for line in render.get("lines") or []:
        data.append([
            para(line.get("service_date_display")),
            para(line.get("participants_display")),
            para(line.get("description_display")),
            para(line.get("duration_display")),
            para(line.get("amount_display")),
        ])

    table = LongTable(data, colWidths=TABLE_COLUMN_WIDTHS, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EAF0F6")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#102A43")),
        ("BOX", (0, 0), (-1, 0), TABLE_HEADER_BORDER_WIDTH, colors.HexColor("#9FB3C8")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor("#9FB3C8")),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#D9E2EC")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), TABLE_ROW_TOP_PADDING),
        ("BOTTOMPADDING", (0, 0), (-1, -1), TABLE_ROW_BOTTOM_PADDING),
        ("LEFTPADDING", (0, 0), (-1, -1), TABLE_CELL_LEFT_PADDING),
        ("RIGHTPADDING", (0, 0), (-1, -1), TABLE_CELL_RIGHT_PADDING),
    ]))
    return table


class _OpticallyShiftedFlowable:
    def __init__(self, flowable: Any, x_offset: float) -> None:
        self.flowable = flowable
        self.x_offset = x_offset

    @property
    def drawWidth(self) -> float:
        return float(getattr(self.flowable, "drawWidth", getattr(self.flowable, "width", 0.0)) or 0.0)

    @property
    def drawHeight(self) -> float:
        return float(getattr(self.flowable, "drawHeight", getattr(self.flowable, "height", 0.0)) or 0.0)

    def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:
        if hasattr(self.flowable, "wrap"):
            return self.flowable.wrap(availWidth, availHeight)
        return self.drawWidth, self.drawHeight

    def wrapOn(self, canvas: Any, availWidth: float, availHeight: float) -> tuple[float, float]:
        if hasattr(self.flowable, "wrapOn"):
            return self.flowable.wrapOn(canvas, availWidth, availHeight)
        return self.wrap(availWidth, availHeight)

    def drawOn(self, canvas: Any, x: float, y: float, _sW: float = 0) -> None:
        self.flowable.drawOn(canvas, x + self.x_offset, y, _sW)

    def getSpaceBefore(self) -> float:
        return float(self.flowable.getSpaceBefore()) if hasattr(self.flowable, "getSpaceBefore") else 0.0

    def getSpaceAfter(self) -> float:
        return float(self.flowable.getSpaceAfter()) if hasattr(self.flowable, "getSpaceAfter") else 0.0


def _footer_pushdown_height(render: dict[str, Any]) -> float:
    line_count = max(0, len(render.get("lines") or []))
    if line_count >= 9:
        return 0.0
    line_reduction = min(line_count, 8) * (0.17 * POINTS_PER_INCH)
    extra_space = (0.42 * POINTS_PER_INCH) - line_reduction
    return max(0.0, min(extra_space, 0.30 * POINTS_PER_INCH))


def _escape(value: Any) -> str:
    return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")


def _build_insurance_coding_flowables(render: dict[str, Any], small_style: Any):
    """Build the compact insurance coding block for the PDF.

    Returns a list of flowables (Paragraphs) with zero spacing between lines,
    or an empty list if insurance coding is not present.
    """
    from reportlab.platypus import Paragraph, Spacer
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT

    insurance_coding = render.get("insurance_coding")
    if not insurance_coding:
        return []

    coding_style = ParagraphStyle(
        "InsuranceCoding",
        parent=small_style,
        fontSize=SMALL_FONT_SIZE,
        leading=SMALL_LEADING,
        alignment=TA_LEFT,
        spaceBefore=0,
        spaceAfter=0,
    )

    flowables = [Spacer(1, 0.14 * 72.0)]
    for item in insurance_coding:
        text = f"{_escape(item['label'])}: {_escape(item['value'])}"
        flowables.append(Paragraph(text, coding_style))
    return flowables


def generate_draft_pdf_bytes(
    invoice: dict[str, Any],
    lines: list[dict[str, Any]],
    *,
    render_model: dict[str, Any] | None = None,
) -> bytes:
    """Generate a draft invoice PDF as an in-memory byte stream.

    Uses the same ReportLab render model and layout as final invoice generation.
    The PDF is clearly marked DRAFT, does not assign an invoice number, does not
    write to disk, does not change invoice status, revision, pdf_path, or checksum,
    and does not create any audit event.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT, TA_RIGHT
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

    buf = io.BytesIO()
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "InvoiceBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=BODY_FONT_SIZE,
        leading=BODY_LEADING,
        textColor=colors.HexColor("#102A43"),
    )
    small = ParagraphStyle(
        "InvoiceSmall",
        parent=body,
        fontSize=SMALL_FONT_SIZE,
        leading=SMALL_LEADING,
        textColor=colors.HexColor("#42526A"),
    )
    table_header = ParagraphStyle(
        "InvoiceTableHeader",
        parent=small,
        fontName="Helvetica-Bold",
    )
    compact = ParagraphStyle(
        "InvoiceCompact",
        parent=body,
        fontSize=BODY_FONT_SIZE,
        leading=BODY_FONT_SIZE * 1.15,
        spaceAfter=0,
    )
    label = ParagraphStyle(
        "InvoiceLabel",
        parent=compact,
        fontName="Helvetica-Bold",
        fontSize=LABEL_FONT_SIZE,
        leading=LABEL_FONT_SIZE * 1.15,
        textColor=colors.HexColor("#526171"),
        spaceAfter=2,
    )
    title = ParagraphStyle(
        "InvoiceTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=TITLE_FONT_SIZE,
        leading=TITLE_LEADING,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#102A43"),
    )
    total_label_style = ParagraphStyle(
        "InvoiceTotalLabel",
        parent=body,
        fontName="Helvetica-Bold",
        fontSize=TOTAL_FONT_SIZE,
        leading=TOTAL_LEADING,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#102A43"),
    )
    total_amount_style = ParagraphStyle(
        "InvoiceTotalAmount",
        parent=total_label_style,
        alignment=TA_RIGHT,
    )
    meta_label = ParagraphStyle(
        "InvoiceMetaLabel",
        parent=small,
        fontName="Helvetica-Bold",
        alignment=TA_LEFT,
        textColor=colors.HexColor("#526171"),
    )
    meta_value = ParagraphStyle(
        "InvoiceMetaValue",
        parent=body,
        alignment=TA_LEFT,
    )
    payment_title_style = ParagraphStyle(
        "InvoicePaymentTitle",
        parent=body,
        fontName="Helvetica-Bold",
        fontSize=BODY_FONT_SIZE,
        leading=BODY_LEADING,
        spaceAfter=2,
    )
    draft_label_style = ParagraphStyle(
        "DraftLabel",
        parent=body,
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=18,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#B0B0B0"),
    )

    def para(value: Any, style=body):
        return Paragraph(_escape(value), style)

    def page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(doc.leftMargin, 0.42 * inch, "Invoice DRAFT")
        canvas.drawRightString(letter[0] - doc.rightMargin, 0.42 * inch, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buf, pagesize=letter, rightMargin=0.50 * inch, leftMargin=0.50 * inch,
        topMargin=0.50 * inch, bottomMargin=0.55 * inch, title="Invoice DRAFT",
    )
    render = render_model or build_invoice_render_model(invoice, lines)
    story = []
    meta = _build_meta_block(
        [
            ("Invoice Number", "DRAFT"),
            ("Invoice Date", render.get("invoice_date_display") or ""),
        ],
        title,
        meta_label,
        meta_value,
        para,
        extra_title_flowables=[para("DRAFT", draft_label_style)],
    )
    header = _build_header_table(
        render, meta, compact, label, styles["Heading2"], invoice.get("business_name_snapshot") or "",
    )
    story.extend([header, Spacer(1, HEADER_TO_TABLE_SPACING)])

    story.append(_build_session_table(render, para, table_header))
    story.append(Spacer(1, _footer_pushdown_height(render)))
    footer = _build_pdf_footer(
        render,
        int(invoice.get("total_cents") or 0),
        body,
        small,
        total_label_style,
        total_amount_style,
        payment_title_style,
    )
    footer.extend(_build_insurance_coding_flowables(render, small))
    story.append(KeepTogether(footer))
    doc.build(story, onFirstPage=page, onLaterPages=page)
    pdf_bytes = buf.getvalue()
    buf.close()
    if not pdf_bytes:
        raise RuntimeError("Draft PDF preview generation did not produce valid bytes.")
    return pdf_bytes


def _build_pdf_footer(
    render: dict[str, Any],
    total_cents: int,
    body_style: Any,
    small_style: Any,
    total_label_style: Any,
    total_amount_style: Any,
    payment_title_style: Any,
) -> list[Any]:
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib import colors
    from reportlab.lib.units import inch

    def para(text: str, style=body_style):
        return Paragraph(text, style)

    summary = render.get("account_summary")

    if summary and (summary.get("prior_unpaid_balance_cents", 0) > 0 or summary.get("current_invoice_paid_cents", 0) > 0):
        summary_label_style = ParagraphStyle(
            "SummaryLabel",
            parent=body_style,
            fontSize=BODY_FONT_SIZE,
            leading=BODY_LEADING,
            alignment=TA_RIGHT,
        )
        summary_amount_style = ParagraphStyle(
            "SummaryAmount",
            parent=body_style,
            fontSize=BODY_FONT_SIZE,
            leading=BODY_LEADING,
            alignment=TA_RIGHT,
        )

        total_due_label_style = ParagraphStyle(
            "TotalDueLabel",
            parent=total_label_style,
            fontName="Helvetica-Bold",
            fontSize=TOTAL_FONT_SIZE,
            leading=TOTAL_LEADING,
            alignment=TA_RIGHT,
            textColor=colors.HexColor("#102A43"),
        )
        total_due_amount_style = ParagraphStyle(
            "TotalDueAmount",
            parent=total_due_label_style,
            alignment=TA_RIGHT,
        )

        has_prior = summary.get("prior_unpaid_balance_cents", 0) > 0
        has_payments = summary.get("current_invoice_paid_cents", 0) > 0

        rows_data = []
        rows_data.append(("Current Charges", summary["current_invoice_total_display"]))
        if has_payments:
            rows_data.append(("Payments Applied", f"-{summary['current_invoice_paid_display']}"))
            rows_data.append(("Current Invoice Balance", summary["current_invoice_balance_display"]))
        if has_prior:
            rows_data.append(("Prior Unpaid Balance", summary["prior_unpaid_balance_display"]))

        summary_rows = []
        span_cmds = []
        for i, (label, amount) in enumerate(rows_data):
            summary_rows.append([
                para(label, summary_label_style), "", "", "",
                para(amount, summary_amount_style),
            ])
            span_cmds.append(("SPAN", (0, i), (3, i)))

        total_row_idx = len(summary_rows)
        summary_rows.append([
            para("TOTAL AMOUNT DUE", total_due_label_style), "", "", "",
            para(summary["total_amount_due_display"], total_due_amount_style),
        ])
        span_cmds.append(("SPAN", (0, total_row_idx), (3, total_row_idx)))

        summary_table_style = TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), TABLE_CELL_LEFT_PADDING),
            ("RIGHTPADDING", (0, 0), (-1, -1), TABLE_CELL_RIGHT_PADDING),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ] + span_cmds)
        summary_table_style.add("LINEABOVE", (0, total_row_idx), (-1, total_row_idx), 1, colors.HexColor("#102A43"))
        summary_table_style.add("LINEBELOW", (0, total_row_idx), (-1, total_row_idx), 0.6, colors.HexColor("#9FB3C8"))
        summary_table_style.add("TOPPADDING", (0, total_row_idx), (-1, total_row_idx), 10)
        summary_table_style.add("BOTTOMPADDING", (0, total_row_idx), (-1, total_row_idx), 4)

        footer_table = Table(summary_rows, colWidths=TABLE_COLUMN_WIDTHS, hAlign="LEFT", style=summary_table_style)

        prior_list = summary.get("prior_invoices") or []
        prior_flowables = []
        if prior_list and has_prior:
            summary_small_left = ParagraphStyle(
                "SummarySmallLeft",
                parent=small_style,
                fontSize=8,
                leading=10,
                alignment=TA_LEFT,
            )
            from .invoice_rendering import format_long_date
            prior_flowables.append(Spacer(1, 0.04 * inch))
            if len(prior_list) == 1:
                item = prior_list[0]
                remaining_display = f"${int(item['remaining_balance_cents']) / 100:,.2f}"
                date_display = format_long_date(item["invoice_date"])
                note = f"Includes prior invoice {item['invoice_number']} dated {date_display} &mdash; {remaining_display} remaining"
                prior_flowables.append(Paragraph(note, summary_small_left))
            else:
                prior_flowables.append(Paragraph("<b>Prior unpaid invoices:</b>", summary_small_left))
                for item in prior_list:
                    remaining_display = f"${int(item['remaining_balance_cents']) / 100:,.2f}"
                    date_display = format_long_date(item["invoice_date"])
                    desc = f"Invoice {item['invoice_number']} &mdash; {date_display} &mdash; {remaining_display} remaining"
                    prior_flowables.append(Paragraph(desc, summary_small_left))

        footer_table_flowables = [footer_table] + prior_flowables
    else:
        footer_table_flowables = [
            Table(
                [[
                    para(render.get("total_label") or "TOTAL DUE", total_label_style), "", "", "",
                    para(render.get("total_display") or f"${total_cents / 100:,.2f}", total_amount_style),
                ]],
                colWidths=TABLE_COLUMN_WIDTHS,
                hAlign="LEFT",
                style=TableStyle([
                    ("SPAN", (0, 0), (3, 0)),
                    ("LINEABOVE", (0, 0), (-1, 0), 1, colors.HexColor("#102A43")),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#9FB3C8")),
                    ("TOPPADDING", (0, 0), (-1, -1), 12),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), TABLE_CELL_LEFT_PADDING),
                    ("RIGHTPADDING", (0, 0), (-1, -1), TABLE_CELL_RIGHT_PADDING),
                ]),
            )
        ]

    payment_title_center_style = ParagraphStyle(
        "PaymentTitleCenter",
        parent=payment_title_style,
        fontSize=SMALL_FONT_SIZE,
        leading=SMALL_LEADING,
        alignment=TA_CENTER,
        spaceAfter=1,
    )
    payment_value_center_style = ParagraphStyle(
        "PaymentValueCenter",
        parent=small_style,
        alignment=TA_CENTER,
        spaceAfter=1,
    )

    payment_flowables: list[Any] = [
        Paragraph(_escape(render.get("payment_title") or "Please make all checks payable to:"), payment_title_center_style),
    ]
    if render.get("payment_name"):
        payment_flowables.append(Paragraph(_escape(render.get("payment_name")), payment_value_center_style))
    for value in render.get("payment_lines") or []:
        payment_flowables.append(Paragraph(_escape(value), payment_value_center_style))
    zelle_title = render.get("payment_zelle_title")
    zelle_value = render.get("payment_zelle_value")
    if zelle_title and zelle_value:
        payment_flowables.append(Paragraph(_escape(zelle_title), payment_title_center_style))
        payment_flowables.append(Paragraph(_escape(zelle_value), payment_value_center_style))
    account_name_line = render.get("payment_account_name_line")
    if account_name_line:
        payment_flowables.append(Paragraph(_escape(account_name_line), payment_value_center_style))

    footer = footer_table_flowables + [
        Spacer(1, 0.18 * inch),
        Table(
            [[payment_flowables]],
            colWidths=[CONTENT_WIDTH],
            style=TableStyle([
                ("TOPPADDING", (0, 0), (-1, -1), 12),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ]),
        ),
    ]
    return footer
