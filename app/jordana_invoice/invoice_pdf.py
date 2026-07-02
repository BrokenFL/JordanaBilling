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

BODY_FONT_SIZE = 10.75
BODY_LEADING = 11.6
BLOCK_LEADING = round(BODY_FONT_SIZE * 1.25, 2)
SMALL_FONT_SIZE = BODY_FONT_SIZE
SMALL_LEADING = BODY_LEADING
LABEL_FONT_SIZE = BODY_FONT_SIZE
LABEL_LEADING = BODY_LEADING
TITLE_FONT_SIZE = 14.5
TITLE_LEADING = 16.0
TOTAL_FONT_SIZE = 13.0
TOTAL_LEADING = 14.3

LOGO_MAX_WIDTH = 2.1004 * POINTS_PER_INCH
LOGO_MAX_HEIGHT = 1.3452 * POINTS_PER_INCH
LOGO_OPTICAL_RIGHT_SHIFT = 0.0

HEADER_LEFT_WIDTH = 3.65 * POINTS_PER_INCH
HEADER_RIGHT_WIDTH = CONTENT_WIDTH - HEADER_LEFT_WIDTH
HEADER_TO_TABLE_SPACING = 0.285 * POINTS_PER_INCH
PROVIDER_TO_BILLTO_SPACING = 0.22 * POINTS_PER_INCH
INVOICE_TO_DATE_SPACING = 3.5
DATE_TO_METADATA_SPACING = 0.0
INVOICE_BLOCK_VERTICAL_LIFT = 8.0
LOGO_VERTICAL_LIFT = 7.0
META_TO_BILLTO_SPACING = (0.10 * POINTS_PER_INCH) + 16.0
BILLTO_LABEL_TO_DETAILS_SPACING = 3.5
TITLE_TO_META_SPACING = 0.0
LOGO_TO_PROVIDER_SPACING = 1.5
LOGO_TO_PROVIDER_SPACING_REDUCTION = 0.0
RIGHT_HEADER_BLOCK_WIDTH = 2.45 * POINTS_PER_INCH
META_LABEL_WIDTH = 2.02 * POINTS_PER_INCH
META_VALUE_WIDTH = 0.0
TABLE_COLUMN_WIDTHS = [
    1.12 * POINTS_PER_INCH,
    1.65 * POINTS_PER_INCH,
    2.78 * POINTS_PER_INCH,
    0.85 * POINTS_PER_INCH,
    1.10 * POINTS_PER_INCH,
]
TOTAL_COLUMN_WIDTHS = [6.15 * POINTS_PER_INCH, 1.35 * POINTS_PER_INCH]

TABLE_ROW_TOP_PADDING = 4
TABLE_ROW_BOTTOM_PADDING = 4
TABLE_CELL_LEFT_PADDING = 6
TABLE_CELL_RIGHT_PADDING = 6
TABLE_HEADER_BORDER_WIDTH = 0.5
PAYMENT_FOOTER_MIN_CLEARANCE = 0.30 * POINTS_PER_INCH
PAYMENT_COLUMN_HEADING_TO_DETAIL_SPACING = 3.5
PAYMENT_ZELLE_TOP_SPACING = 0.60 * BODY_LEADING
PAYMENT_CHECK_COLUMN_WIDTH = 2.10 * POINTS_PER_INCH
PAYMENT_ZELLE_COLUMN_WIDTH = 3.30 * POINTS_PER_INCH
PAYMENT_COLUMN_CENTER_SEPARATION = (PAYMENT_CHECK_COLUMN_WIDTH + PAYMENT_ZELLE_COLUMN_WIDTH) / 2.0
BILLTO_SHORT_HEIGHT_MAX = 35.0
BILLTO_MEDIUM_HEIGHT_MAX = 60.0
INVOICE_TO_BILLTO_GAP_SHORT = 26.0
INVOICE_TO_BILLTO_GAP_MEDIUM = 17.0
INVOICE_TO_BILLTO_GAP_LONG = 13.0
PAYMENT_SECTION_TOP_SPACING = (0.12 * POINTS_PER_INCH) + BODY_LEADING


def _times_canvasmaker(*args: Any, **kwargs: Any) -> Any:
    from reportlab.pdfgen.canvas import Canvas

    canvas = Canvas(*args, **kwargs)
    canvas.setFont("Times-Roman", BODY_FONT_SIZE)
    return canvas


def _register_invoice_font_aliases() -> None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.pdfmetrics import Font

    for alias, base_font in (
        ("Helvetica", "Times-Roman"),
        ("Helvetica-Bold", "Times-Bold"),
        ("Helvetica-Oblique", "Times-Italic"),
        ("Helvetica-BoldOblique", "Times-BoldItalic"),
    ):
        pdfmetrics.registerFont(Font(alias, base_font, "WinAnsiEncoding"))


def _generate_invoice_pdf_bytes(
    invoice: dict[str, Any],
    lines: list[dict[str, Any]],
    *,
    render_model: dict[str, Any] | None = None,
    meta_rows: list[tuple[str, str]] | None = None,
    page_footer_label: str = "Invoice Draft",
    doc_title: str = "Invoice Draft",
) -> bytes:
    """Shared canonical PDF rendering for both draft previews and finalized invoices.

    Both generate_invoice_pdf (finalized) and generate_draft_pdf_bytes (draft
    preview) delegate to this function so that typography, spacing, header
    layout, table, total, payment section, footer, insurance/coding block,
    and late-cancellation rendering are always identical.  The only intended
    differences are parameterised via *meta_rows*, *page_footer_label*, and
    *doc_title*.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
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
    _register_invoice_font_aliases()
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "InvoiceBody",
        parent=styles["BodyText"],
        fontName="Times-Roman",
        fontSize=BODY_FONT_SIZE,
        leading=BODY_LEADING,
        textColor=colors.HexColor("#102A43"),
        spaceBefore=0,
        spaceAfter=0,
    )
    small = ParagraphStyle(
        "InvoiceSmall",
        parent=body,
        fontSize=SMALL_FONT_SIZE,
        leading=SMALL_LEADING,
        textColor=colors.HexColor("#42526A"),
        spaceBefore=0,
        spaceAfter=0,
    )
    table_header = ParagraphStyle(
        "InvoiceTableHeader",
        parent=small,
        fontName="Times-Bold",
    )
    compact = ParagraphStyle(
        "InvoiceCompact",
        parent=body,
        fontSize=BODY_FONT_SIZE,
        leading=BODY_LEADING,
        spaceBefore=0,
        spaceAfter=0,
    )
    label = ParagraphStyle(
        "InvoiceLabel",
        parent=compact,
        fontName="Times-Bold",
        fontSize=LABEL_FONT_SIZE,
        leading=BLOCK_LEADING,
        textColor=colors.HexColor("#526171"),
        spaceBefore=0,
        spaceAfter=0,
    )
    block_text = ParagraphStyle(
        "InvoiceBlockText",
        parent=body,
        fontSize=BODY_FONT_SIZE,
        leading=BLOCK_LEADING,
        spaceBefore=0,
        spaceAfter=0,
    )
    title = ParagraphStyle(
        "InvoiceTitle",
        parent=styles["Heading1"],
        fontName="Times-Bold",
        fontSize=TITLE_FONT_SIZE,
        leading=TITLE_LEADING,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#102A43"),
        spaceBefore=0,
        spaceAfter=0,
    )
    total_label_style = ParagraphStyle(
        "InvoiceTotalLabel",
        parent=body,
        fontName="Times-Bold",
        fontSize=TOTAL_FONT_SIZE,
        leading=TOTAL_LEADING,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#102A43"),
        spaceBefore=0,
        spaceAfter=0,
    )
    total_amount_style = ParagraphStyle(
        "InvoiceTotalAmount",
        parent=total_label_style,
        alignment=TA_RIGHT,
        spaceBefore=0,
        spaceAfter=0,
    )
    meta_label = ParagraphStyle(
        "InvoiceMetaLabel",
        parent=small,
        fontName="Times-Bold",
        alignment=TA_LEFT,
        textColor=colors.HexColor("#526171"),
        spaceBefore=0,
        spaceAfter=0,
    )
    meta_value = ParagraphStyle(
        "InvoiceMetaValue",
        parent=body,
        alignment=TA_CENTER,
        spaceBefore=0,
        spaceAfter=0,
    )
    payment_title_style = ParagraphStyle(
        "InvoicePaymentTitle",
        parent=body,
        fontName="Times-Bold",
        fontSize=BODY_FONT_SIZE,
        leading=BODY_LEADING,
        spaceBefore=0,
        spaceAfter=0,
    )

    def para(value: Any, style=body):
        return Paragraph(_escape(value), style)

    def page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Times-Roman", 8)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(doc.leftMargin, 0.42 * inch, page_footer_label)
        canvas.drawRightString(letter[0] - doc.rightMargin, 0.42 * inch, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buf, pagesize=letter, rightMargin=0.50 * inch, leftMargin=0.50 * inch,
        topMargin=0.50 * inch, bottomMargin=0.55 * inch, title=doc_title,
    )
    render = render_model or build_invoice_render_model(invoice, lines)
    story = []
    meta = _build_meta_block(
        meta_rows or [
            ("", render.get("invoice_date_display") or ""),
            ("", render.get("invoice_number_display") or ""),
        ],
        title,
        meta_label,
        meta_value,
        para,
    )
    header = _build_header_table(
        render, meta, block_text, label, styles["Heading2"], invoice.get("business_name_snapshot") or "",
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
    doc.build(story, onFirstPage=page, onLaterPages=page, canvasmaker=_times_canvasmaker)
    pdf_bytes = buf.getvalue()
    buf.close()
    if not pdf_bytes:
        raise RuntimeError("Invoice PDF generation did not produce valid bytes.")
    return pdf_bytes


def generate_invoice_pdf(
    invoice: dict[str, Any],
    lines: list[dict[str, Any]],
    output_path: str | Path,
    *,
    render_model: dict[str, Any] | None = None,
) -> str:
    path = Path(output_path)
    if path.exists():
        raise FileExistsError(f"Finalized invoice PDF already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    number = invoice.get("invoice_number") or "Draft"
    resolved_model = render_model or build_invoice_render_model(invoice, lines)
    pdf_bytes = _generate_invoice_pdf_bytes(
        invoice, lines,
        render_model=resolved_model,
        meta_rows=[
            ("", resolved_model.get("invoice_date_display") or ""),
            ("", f"Invoice No. {resolved_model.get('invoice_number_display')}" if resolved_model.get("invoice_number_display") else ""),
        ],
        page_footer_label=f"Invoice {number}",
        doc_title=f"Invoice {number}",
    )
    try:
        temp_path.write_bytes(pdf_bytes)
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
    from reportlab.platypus import Spacer

    meta = [para("INVOICE", title_style)]
    meta.extend(extra_title_flowables or [])
    values = [value for _label, value in rows if value]
    if values:
        meta.append(Spacer(1, INVOICE_TO_DATE_SPACING))
        meta.append(para(values[0], meta_value_style))
    if len(values) > 1:
        if DATE_TO_METADATA_SPACING:
            meta.append(Spacer(1, DATE_TO_METADATA_SPACING))
        meta.append(para(values[1], meta_value_style))
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
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.styles import ParagraphStyle

    def para(value: Any, style=compact_style):
        return Paragraph(_escape(value), style)

    provider_value_style = ParagraphStyle(
        "InvoiceProviderValue",
        parent=compact_style,
        alignment=TA_LEFT,
        leading=BLOCK_LEADING,
    )
    right_heading_style = ParagraphStyle(
        "InvoiceHeaderFallbackLogo",
        parent=heading_style,
        alignment=TA_CENTER,
        fontName="Times-Bold",
    )

    left_block = [
        Paragraph(_escape(value), provider_value_style)
        for value in (render.get("sender_lines") or [])
        if value
    ]
    left_block.append(Spacer(1, PROVIDER_TO_BILLTO_SPACING))
    left_block.extend([para("Bill To:", label_style), Spacer(1, BILLTO_LABEL_TO_DETAILS_SPACING)])
    left_block.extend(para(value) for value in (render.get("bill_to_lines") or []) if value)

    provider_block_height = len(left_block[: len(render.get("sender_lines") or [])]) * BLOCK_LEADING
    invoice_top_offset = provider_block_height + PROVIDER_TO_BILLTO_SPACING

    logo_flowable = _logo_flowable(
        render.get("logo_path"),
        LOGO_MAX_WIDTH,
        min(LOGO_MAX_HEIGHT, invoice_top_offset),
    )
    if logo_flowable is not None:
        logo = (
            _OpticallyShiftedFlowable(logo_flowable, LOGO_OPTICAL_RIGHT_SHIFT)
            if LOGO_OPTICAL_RIGHT_SHIFT
            else logo_flowable
        )
    else:
        logo = Paragraph(_escape(fallback_business_name or "Business"), right_heading_style)
    _logo_width, logo_height = (
        logo.wrap(HEADER_RIGHT_WIDTH, 10_000)
        if hasattr(logo, "wrap")
        else (getattr(logo, "drawWidth", LOGO_MAX_WIDTH), getattr(logo, "drawHeight", 0.0))
    )
    logo_to_invoice_spacing = max(0.0, invoice_top_offset - float(logo_height or 0.0))
    right_block = _RightHeaderBlockFlowable(
        logo=logo,
        meta_flowables=meta,
        logo_to_invoice_spacing=logo_to_invoice_spacing,
        invoice_top_offset=max(0.0, invoice_top_offset - INVOICE_BLOCK_VERTICAL_LIFT),
    )
    return Table(
        [[left_block, [right_block]]],
        colWidths=[HEADER_LEFT_WIDTH, HEADER_RIGHT_WIDTH],
        hAlign="LEFT",
        style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]),
    )


class _RightHeaderBlockFlowable:
    def __init__(
        self,
        *,
        logo: Any,
        meta_flowables: list[Any],
        logo_to_invoice_spacing: float,
        invoice_top_offset: float,
    ) -> None:
        self.logo = logo
        self.meta_flowables = meta_flowables
        self.logo_to_invoice_spacing = logo_to_invoice_spacing
        self.invoice_top_offset = invoice_top_offset
        self.width = HEADER_RIGHT_WIDTH
        self.height = 0.0
        self._logo_size = (0.0, 0.0)
        self._meta_size = (0.0, 0.0)
        self._meta_table = None

    def _build_meta_table(self) -> Any:
        from reportlab.platypus import Table, TableStyle

        return Table(
            [[self.meta_flowables]],
            colWidths=[min(LOGO_MAX_WIDTH, HEADER_RIGHT_WIDTH)],
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

    def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:
        logo_width, logo_height = (
            self.logo.wrap(self.width, availHeight)
            if hasattr(self.logo, "wrap")
            else (getattr(self.logo, "drawWidth", 0.0), getattr(self.logo, "drawHeight", 0.0))
        )
        self._meta_table = self._build_meta_table()
        meta_width, meta_height = self._meta_table.wrap(self.width, availHeight)
        self._logo_size = (float(logo_width or 0.0), float(logo_height or 0.0))
        self._meta_size = (float(meta_width or 0.0), float(meta_height or 0.0))
        self.height = max(self._logo_size[1], self.invoice_top_offset + self._meta_size[1])
        return self.width, self.height

    def wrapOn(self, canvas: Any, availWidth: float, availHeight: float) -> tuple[float, float]:
        return self.wrap(availWidth, availHeight)

    def drawOn(self, canvas: Any, x: float, y: float, _sW: float = 0) -> None:
        if self._meta_table is None:
            self.wrap(self.width, 10_000)
        logo_width, logo_height = self._logo_size
        meta_width, meta_height = self._meta_size
        logo_x = x + self.width - logo_width
        logo_y = y + self.height - logo_height + LOGO_VERTICAL_LIFT
        logo_center_x = logo_x + (logo_width / 2.0)
        meta_x = logo_center_x - (meta_width / 2.0)
        meta_y = y + self.height - self.invoice_top_offset - meta_height
        self.logo.drawOn(canvas, logo_x, logo_y)
        self._meta_table.drawOn(canvas, meta_x, meta_y)

    def getSpaceBefore(self) -> float:
        return 0.0

    def getSpaceAfter(self) -> float:
        return 0.0


class _InvoiceHeaderFlowable:
    def __init__(self, invoice_block: list[Any], bill_to_block: list[Any], logo: Any, provider_block: list[Any]) -> None:
        self.invoice_block = invoice_block
        self.bill_to_block = bill_to_block
        self.logo = logo
        self.provider_block = provider_block
        self.width = CONTENT_WIDTH
        self.height = 0.0
        self._last_metrics: dict[str, float] = {}

    def _flowable_size(self, flowable: Any, width: float) -> tuple[float, float]:
        if hasattr(flowable, "wrap"):
            wrapped_width, wrapped_height = flowable.wrap(width, 10_000)
            return float(wrapped_width or width), float(wrapped_height or 0)
        return float(getattr(flowable, "drawWidth", width) or width), float(getattr(flowable, "drawHeight", 0) or 0)

    def _block_height(self, flowables: list[Any], width: float) -> float:
        return sum(self._flowable_size(flowable, width)[1] for flowable in flowables)

    def _invoice_gap_for_bill_to_height(self, bill_to_height: float) -> float:
        if bill_to_height <= BILLTO_SHORT_HEIGHT_MAX:
            return INVOICE_TO_BILLTO_GAP_SHORT
        if bill_to_height <= BILLTO_MEDIUM_HEIGHT_MAX:
            return INVOICE_TO_BILLTO_GAP_MEDIUM
        return INVOICE_TO_BILLTO_GAP_LONG

    def _layout(self) -> dict[str, float]:
        logo_width, logo_height = self._flowable_size(self.logo, RIGHT_HEADER_BLOCK_WIDTH)
        provider_height = self._block_height(self.provider_block, RIGHT_HEADER_BLOCK_WIDTH)
        bill_to_height = self._block_height(self.bill_to_block, HEADER_LEFT_WIDTH)
        invoice_height = self._block_height(self.invoice_block, HEADER_LEFT_WIDTH)

        provider_bottom = 0.0
        provider_top = provider_bottom + provider_height
        logo_bottom = provider_top + LOGO_TO_PROVIDER_SPACING
        logo_top = logo_bottom + logo_height

        bill_to_top = provider_bottom + bill_to_height
        invoice_gap = self._invoice_gap_for_bill_to_height(bill_to_height)
        invoice_bottom = bill_to_top + invoice_gap
        invoice_top = invoice_bottom + invoice_height
        header_height = max(logo_top, invoice_top)

        return {
            "header_height": header_height,
            "logo_width": logo_width,
            "logo_height": logo_height,
            "logo_top": logo_top,
            "logo_bottom": logo_bottom,
            "provider_top": provider_top,
            "provider_bottom": provider_bottom,
            "provider_height": provider_height,
            "bill_to_top": bill_to_top,
            "bill_to_bottom": provider_bottom,
            "bill_to_height": bill_to_height,
            "invoice_top": invoice_top,
            "invoice_bottom": invoice_bottom,
            "invoice_height": invoice_height,
            "invoice_to_bill_to_gap": invoice_bottom - bill_to_top,
            "invoice_gap_rule": invoice_gap,
        }

    def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:
        metrics = self._layout()
        self.height = metrics["header_height"]
        self._last_metrics = metrics
        return self.width, self.height

    def wrapOn(self, canvas: Any, availWidth: float, availHeight: float) -> tuple[float, float]:
        return self.wrap(availWidth, availHeight)

    def _draw_block(self, canvas: Any, flowables: list[Any], x: float, top: float, width: float) -> None:
        cursor = top
        for flowable in flowables:
            _, height = self._flowable_size(flowable, width)
            cursor -= height
            flowable.drawOn(canvas, x, cursor)

    def drawOn(self, canvas: Any, x: float, y: float, _sW: float = 0) -> None:
        metrics = self._last_metrics or self._layout()
        self._last_metrics = metrics
        right_x = x + HEADER_LEFT_WIDTH + HEADER_RIGHT_WIDTH - RIGHT_HEADER_BLOCK_WIDTH
        logo_x = right_x + ((RIGHT_HEADER_BLOCK_WIDTH - metrics["logo_width"]) / 2.0)

        self._draw_block(canvas, self.invoice_block, x, y + metrics["invoice_top"], HEADER_LEFT_WIDTH)
        self._draw_block(canvas, self.bill_to_block, x, y + metrics["bill_to_top"], HEADER_LEFT_WIDTH)
        self.logo.drawOn(canvas, logo_x, y + metrics["logo_bottom"])
        self._draw_block(canvas, self.provider_block, right_x, y + metrics["provider_top"], RIGHT_HEADER_BLOCK_WIDTH)

    def getSpaceBefore(self) -> float:
        return 0.0

    def getSpaceAfter(self) -> float:
        return 0.0

    def getKeepWithNext(self) -> bool:
        return False

    def split(self, availWidth: float, availHeight: float) -> list[Any]:
        return []


def _build_session_table(render: dict[str, Any], para: Any, table_header_style: Any) -> Any:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph
    from reportlab.platypus import LongTable, TableStyle

    table_value_style = ParagraphStyle(
        "InvoiceTableValue",
        parent=getattr(table_header_style, "parent", table_header_style),
        fontName="Times-Roman",
        fontSize=BODY_FONT_SIZE,
        leading=BODY_LEADING,
        alignment=TA_CENTER,
        spaceBefore=0,
        spaceAfter=0,
    )
    centered_header_style = ParagraphStyle(
        "InvoiceTableHeaderCentered",
        parent=table_header_style,
        fontName="Times-Bold",
        fontSize=BODY_FONT_SIZE,
        leading=BODY_LEADING,
        alignment=TA_CENTER,
        spaceBefore=0,
        spaceAfter=0,
    )

    def table_para(value: Any, style: Any = table_value_style) -> Any:
        return Paragraph(_escape(value), style)

    data = [[
        table_para("Date", centered_header_style),
        table_para("Participants", centered_header_style),
        table_para("Service", centered_header_style),
        table_para("Duration", centered_header_style),
        table_para("Amount", centered_header_style),
    ]]
    for line in render.get("lines") or []:
        data.append([
            table_para(line.get("service_date_display")),
            table_para(line.get("participants_display")),
            table_para(line.get("description_display")),
            table_para(line.get("duration_display")),
            table_para(line.get("amount_display")),
        ])

    table = LongTable(data, colWidths=TABLE_COLUMN_WIDTHS, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EAF0F6")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#102A43")),
        ("FONTNAME", (0, 0), (-1, -1), "Times-Roman"),
        ("FONTSIZE", (0, 0), (-1, -1), BODY_FONT_SIZE),
        ("LEADING", (0, 0), (-1, -1), BODY_LEADING),
        ("BOX", (0, 0), (-1, 0), TABLE_HEADER_BORDER_WIDTH, colors.HexColor("#9FB3C8")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor("#9FB3C8")),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#D9E2EC")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
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

    flowables = [Spacer(1, 4 * BODY_LEADING)]
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

    Delegates to the same shared canonical renderer as finalized invoices
    (_generate_invoice_pdf_bytes) so that typography, spacing, header layout,
    table, total, payment section, footer, insurance/coding block, and
    late-cancellation rendering are always identical.  The PDF is clearly
    marked DRAFT, does not assign an invoice number, does not write to disk,
    does not change invoice status, revision, pdf_path, or checksum, and
    does not create any audit event.
    """
    resolved_model = render_model or build_invoice_render_model(invoice, lines)
    return _generate_invoice_pdf_bytes(
        invoice, lines,
        render_model=resolved_model,
        meta_rows=[
            ("", resolved_model.get("invoice_date_display") or ""),
            ("", "DRAFT"),
        ],
        page_footer_label="Invoice DRAFT",
        doc_title="Invoice DRAFT",
    )


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
            fontName="Times-Bold",
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
            ("FONTNAME", (0, 0), (-1, -1), "Times-Roman"),
            ("FONTSIZE", (0, 0), (-1, -1), BODY_FONT_SIZE),
            ("LEADING", (0, 0), (-1, -1), BODY_LEADING),
            ("LEFTPADDING", (0, 0), (-1, -1), TABLE_CELL_LEFT_PADDING),
            ("RIGHTPADDING", (0, 0), (-1, -1), TABLE_CELL_RIGHT_PADDING),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ] + span_cmds)
        summary_table_style.add("LINEABOVE", (0, total_row_idx), (-1, total_row_idx), 1, colors.HexColor("#102A43"))
        summary_table_style.add("LINEBELOW", (0, total_row_idx), (-1, total_row_idx), 0.6, colors.HexColor("#9FB3C8"))
        summary_table_style.add("TOPPADDING", (0, total_row_idx), (-1, total_row_idx), 8)
        summary_table_style.add("BOTTOMPADDING", (0, total_row_idx), (-1, total_row_idx), 4)

        footer_table = Table(summary_rows, colWidths=TABLE_COLUMN_WIDTHS, hAlign="LEFT", style=summary_table_style)

        prior_list = summary.get("prior_invoices") or []
        prior_flowables = []
        if prior_list and has_prior:
            summary_small_left = ParagraphStyle(
                "SummarySmallLeft",
                parent=small_style,
                fontSize=BODY_FONT_SIZE,
                leading=BODY_LEADING,
                alignment=TA_LEFT,
                spaceBefore=0,
                spaceAfter=0,
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
                    ("FONTNAME", (0, 0), (-1, -1), "Times-Roman"),
                    ("FONTSIZE", (0, 0), (-1, -1), BODY_FONT_SIZE),
                    ("LEADING", (0, 0), (-1, -1), BODY_LEADING),
                    ("LINEABOVE", (0, 0), (-1, 0), 1, colors.HexColor("#102A43")),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#9FB3C8")),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), TABLE_CELL_LEFT_PADDING),
                    ("RIGHTPADDING", (0, 0), (-1, -1), TABLE_CELL_RIGHT_PADDING),
                ]),
            )
        ]

    payment_column_heading_style = ParagraphStyle(
        "PaymentColumnHeading",
        parent=payment_title_style,
        fontName="Times-Bold",
        fontSize=BODY_FONT_SIZE,
        leading=BLOCK_LEADING,
        alignment=TA_CENTER,
        spaceBefore=0,
        spaceAfter=0,
    )
    payment_value_style = ParagraphStyle(
        "PaymentValue",
        parent=small_style,
        fontName="Times-Roman",
        fontSize=BODY_FONT_SIZE,
        leading=BLOCK_LEADING,
        alignment=TA_CENTER,
        spaceBefore=0,
        spaceAfter=0,
    )

    payment_block: list[Any] = [
        Paragraph("Please make checks payable to:", payment_column_heading_style),
        Spacer(1, PAYMENT_COLUMN_HEADING_TO_DETAIL_SPACING),
    ]
    if render.get("payment_name"):
        payment_block.append(Paragraph(_escape(render.get("payment_name")), payment_value_style))
    for value in render.get("payment_lines") or []:
        if str(value).strip():
            payment_block.append(Paragraph(_escape(str(value).strip()), payment_value_style))

    payment_block.extend([
        Spacer(1, PAYMENT_ZELLE_TOP_SPACING),
        Paragraph("Or pay via Zelle:", payment_column_heading_style),
        Spacer(1, PAYMENT_COLUMN_HEADING_TO_DETAIL_SPACING),
    ])
    zelle_value = render.get("payment_zelle_value")
    if zelle_value:
        payment_block.append(Paragraph(_escape(zelle_value), payment_value_style))

    payment_table = Table(
        [[payment_block]],
        colWidths=[CONTENT_WIDTH],
        hAlign="CENTER",
        style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTNAME", (0, 0), (-1, -1), "Times-Roman"),
            ("FONTSIZE", (0, 0), (-1, -1), BODY_FONT_SIZE),
            ("LEADING", (0, 0), (-1, -1), BLOCK_LEADING),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]),
    )

    footer = footer_table_flowables + [
        Spacer(1, PAYMENT_SECTION_TOP_SPACING),
        payment_table,
    ]
    return footer
