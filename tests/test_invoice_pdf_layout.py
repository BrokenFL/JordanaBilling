"""Focused tests for invoice PDF presentation refinement."""
import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from jordana_invoice.invoice_pdf import (
    BODY_FONT_SIZE,
    BLOCK_LEADING,
    BILLTO_LABEL_TO_DETAILS_SPACING,
    CONTENT_WIDTH,
    HEADER_LEFT_WIDTH,
    HEADER_RIGHT_WIDTH,
    HEADER_TO_TABLE_SPACING,
    DATE_TO_METADATA_SPACING,
    INVOICE_BLOCK_VERTICAL_LIFT,
    INVOICE_TO_DATE_SPACING,
    BILLTO_MEDIUM_HEIGHT_MAX,
    BILLTO_SHORT_HEIGHT_MAX,
    INVOICE_TO_BILLTO_GAP_LONG,
    INVOICE_TO_BILLTO_GAP_MEDIUM,
    INVOICE_TO_BILLTO_GAP_SHORT,
    LABEL_FONT_SIZE,
    LETTER_PAGE_HEIGHT,
    LETTER_PAGE_WIDTH,
    LOGO_MAX_HEIGHT,
    LOGO_MAX_WIDTH,
    LOGO_OPTICAL_RIGHT_SHIFT,
    LOGO_TO_PROVIDER_SPACING_REDUCTION,
    LOGO_TO_PROVIDER_SPACING,
    META_LABEL_WIDTH,
    META_TO_BILLTO_SPACING,
    META_VALUE_WIDTH,
    PAYMENT_COLUMN_HEADING_TO_DETAIL_SPACING,
    PAYMENT_CHECK_COLUMN_WIDTH,
    PAYMENT_COLUMN_CENTER_SEPARATION,
    PAYMENT_FOOTER_MIN_CLEARANCE,
    PAYMENT_SECTION_TOP_SPACING,
    PAYMENT_ZELLE_TOP_SPACING,
    PAYMENT_ZELLE_COLUMN_WIDTH,
    PROVIDER_TO_BILLTO_SPACING,
    RIGHT_HEADER_BLOCK_WIDTH,
    SMALL_FONT_SIZE,
    TABLE_CELL_LEFT_PADDING,
    TABLE_CELL_RIGHT_PADDING,
    TABLE_COLUMN_WIDTHS,
    TABLE_HEADER_BORDER_WIDTH,
    TABLE_ROW_BOTTOM_PADDING,
    TABLE_ROW_TOP_PADDING,
    TITLE_FONT_SIZE,
    TOTAL_COLUMN_WIDTHS,
    TOTAL_FONT_SIZE,
    _build_pdf_footer,
    _build_header_table,
    _build_session_table,
    _footer_pushdown_height,
    _generate_invoice_pdf_bytes,
    generate_draft_pdf_bytes,
    generate_invoice_pdf,
)

# US Letter in points
PAGE_W = LETTER_PAGE_WIDTH
PAGE_H = LETTER_PAGE_HEIGHT
MARGIN_IN = 0.50
MARGIN_PT = MARGIN_IN * 72
CONTENT_W = CONTENT_WIDTH


def _sample_invoice(**overrides):
    base = {
        "invoice_number": "2026-0042",
        "invoice_date": "2026-06-01",
        "billing_period_start": "2026-05-01",
        "billing_period_end": "2026-05-31",
        "business_name_snapshot": "Demo Practice",
        "provider_name_snapshot": "Jordana Singer",
        "credentials_snapshot": "LCSW",
        "business_address_snapshot": "3659 Siena Circle\nWellington, FL 33414",
        "bill_to_name_snapshot": "Avery Stone",
        "bill_to_address_snapshot": "10 Sample Street\nExample, FL 00000",
        "bill_to_email_snapshot": "avery@example.test",
        "delivery_method": "both",
        "total_cents": 15000,
        "total_label_snapshot": "TOTAL DUE",
        "payee_name_snapshot": "Jordana Singer",
        "payment_address_snapshot": "Jordana Singer\n3659 Siena Circle\nWellington, FL 33414",
        "business_phone_snapshot": "(561) 385-8900",
        "zelle_recipient_snapshot": "demo-zelle@example.test",
    }
    base.update(overrides)
    return base


def _sample_lines(count=3, date_start=22):
    return [
        {
            "service_date": f"2026-05-{date_start + i:02d}",
            "participants_snapshot": "Avery Stone",
            "description_snapshot": "Office Visit",
            "duration_minutes": 60,
            "line_amount_cents": 5000,
        }
        for i in range(count)
    ]


def _multi_page_lines():
    return [
        {
            "service_date": f"2026-05-{1 + (i % 28):02d}",
            "participants_snapshot": "Avery Stone & Taylor Reed",
            "description_snapshot": "Correspondence - Weekend Evening",
            "duration_minutes": 60,
            "line_amount_cents": 15000,
        }
        for i in range(40)
    ]


def _has_pdf_deps():
    try:
        import reportlab  # noqa: F401
        from pypdf import PdfReader  # noqa: F401
        return True
    except ImportError:
        return False


class InvoicePdfLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _generate_pdf(self, invoice=None, lines=None, filename="Invoice_2026-0042.pdf"):
        path = self.root / filename
        generate_invoice_pdf(invoice or _sample_invoice(), lines or _sample_lines(), path)
        return path

    def _extract_pdf_text(self, path_or_bytes):
        from pypdf import PdfReader

        source = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes) else path_or_bytes
        reader = PdfReader(source)
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    # --- 1. Page size and margins ---

    def test_page_size_is_us_letter(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from pypdf import PdfReader
        path = self._generate_pdf()
        reader = PdfReader(path)
        page = reader.pages[0]
        self.assertAlmostEqual(float(page.mediabox.width), PAGE_W, delta=1)
        self.assertAlmostEqual(float(page.mediabox.height), PAGE_H, delta=1)

    def test_left_margin_via_footer_position(self):
        """Footer 'Invoice ...' is drawn at leftMargin on canvas — verifiable."""
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from pypdf import PdfReader
        path = self._generate_pdf()
        reader = PdfReader(path)
        snips = []
        reader.pages[0].extract_text(visitor_text=lambda t, cm, tm, fd, fs: snips.append((t, tm[4])) if t and t.strip() else None)
        footer = [s for s in snips if s[0].startswith("Invoice 2026")]
        self.assertTrue(footer, "Footer 'Invoice ...' not found")
        self.assertAlmostEqual(footer[0][1], MARGIN_PT, delta=4)

    def test_right_margin_via_footer_position(self):
        """Footer 'Page N' is right-aligned at pageWidth - rightMargin."""
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from pypdf import PdfReader
        path = self._generate_pdf()
        reader = PdfReader(path)
        snips = []
        reader.pages[0].extract_text(visitor_text=lambda t, cm, tm, fd, fs: snips.append((t, tm[4], fs)) if t and t.strip() else None)
        page_snips = [s for s in snips if s[0].startswith("Page ")]
        self.assertTrue(page_snips, "Footer 'Page N' not found")
        # drawRightString places the right edge at PAGE_W - MARGIN_PT = 576
        # pypdf returns the left edge of the text, so estimate right edge
        s = page_snips[0]
        approx_right = s[1] + len(s[0]) * s[2] * 0.35
        self.assertLessEqual(approx_right, PAGE_W - MARGIN_PT + 4)

    # --- 2. Column widths sum exactly to content width ---

    def test_table_column_widths_sum_to_content_width(self):
        self.assertAlmostEqual(sum(TABLE_COLUMN_WIDTHS), CONTENT_W, delta=0.5)

    def test_header_column_widths_sum_to_content_width(self):
        self.assertAlmostEqual(HEADER_LEFT_WIDTH + HEADER_RIGHT_WIDTH, CONTENT_W, delta=0.5)

    def test_total_section_uses_full_width(self):
        self.assertAlmostEqual(sum(TOTAL_COLUMN_WIDTHS), CONTENT_W, delta=0.5)

    # --- 3. Typography, logo, and row spacing scale up for print ---

    def test_typography_sizes_match_print_contract(self):
        self.assertGreaterEqual(BODY_FONT_SIZE, 10.7)
        self.assertLessEqual(BODY_FONT_SIZE, 10.8)
        self.assertAlmostEqual(SMALL_FONT_SIZE, BODY_FONT_SIZE, delta=0.1)
        self.assertAlmostEqual(TITLE_FONT_SIZE, 14.5, delta=0.1)
        self.assertAlmostEqual(TOTAL_FONT_SIZE, 13.0, delta=0.1)
        self.assertAlmostEqual(LABEL_FONT_SIZE, BODY_FONT_SIZE, delta=0.1)

    def test_logo_max_dimensions_match_refinement(self):
        self.assertAlmostEqual(LOGO_MAX_WIDTH, 2.1004 * 72, delta=0.1)
        self.assertAlmostEqual(LOGO_MAX_HEIGHT, 1.3452 * 72, delta=0.1)

    def test_logo_scales_within_bounds(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from jordana_invoice.invoice_pdf import _logo_flowable

        logo_path = Path("app/jordana_invoice/static/assets/jordana-logo.png")
        image = _logo_flowable(str(logo_path), LOGO_MAX_WIDTH, LOGO_MAX_HEIGHT)
        self.assertIsNotNone(image)
        self.assertLessEqual(image.drawWidth, LOGO_MAX_WIDTH + 0.1)
        self.assertLessEqual(image.drawHeight, LOGO_MAX_HEIGHT + 0.1)

    def test_two_column_header_spacing_is_compact(self):
        self.assertAlmostEqual(HEADER_TO_TABLE_SPACING, 0.285 * 72, delta=0.1)
        self.assertAlmostEqual(PROVIDER_TO_BILLTO_SPACING, 0.22 * 72, delta=0.1)
        self.assertAlmostEqual(INVOICE_TO_DATE_SPACING, 3.5, delta=0.1)
        self.assertAlmostEqual(DATE_TO_METADATA_SPACING, 0.0, delta=0.1)
        self.assertAlmostEqual(INVOICE_BLOCK_VERTICAL_LIFT, 8.0, delta=0.1)
        self.assertAlmostEqual(BILLTO_LABEL_TO_DETAILS_SPACING, 3.5, delta=0.1)
        self.assertAlmostEqual(LOGO_OPTICAL_RIGHT_SHIFT, 0.0, delta=0.1)
        self.assertAlmostEqual(BLOCK_LEADING, BODY_FONT_SIZE * 1.25, delta=0.01)

    def test_row_padding_matches_print_spacing(self):
        self.assertEqual(TABLE_ROW_TOP_PADDING, 4)
        self.assertEqual(TABLE_ROW_BOTTOM_PADDING, 4)
        self.assertGreaterEqual(TABLE_CELL_LEFT_PADDING, 6)
        self.assertGreaterEqual(TABLE_CELL_RIGHT_PADDING, 6)
        self.assertAlmostEqual(TABLE_HEADER_BORDER_WIDTH, 0.5, delta=0.01)

    # --- 4. Header/table/total use full frame width (content present) ---

    def test_invoice_text_present_on_page(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from pypdf import PdfReader
        path = self._generate_pdf()
        reader = PdfReader(path)
        text = reader.pages[0].extract_text() or ""
        self.assertIn("INVOICE", text)

    def test_table_headers_present_on_first_page(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from pypdf import PdfReader
        path = self._generate_pdf()
        reader = PdfReader(path)
        text = reader.pages[0].extract_text() or ""
        for name in ("Date", "Participants", "Service", "Duration", "Amount"):
            self.assertIn(name, text)

    def test_session_table_helper_uses_bold_header_and_full_border(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.platypus import Paragraph
        from jordana_invoice.invoice_rendering import build_invoice_render_model

        styles = getSampleStyleSheet()
        body = ParagraphStyle("B", parent=styles["BodyText"])
        table_header = ParagraphStyle("TH", parent=body, fontName="Times-Bold")

        def para(value, style=body):
            return Paragraph(str(value), style)

        table = _build_session_table(build_invoice_render_model(_sample_invoice(), _sample_lines()), para, table_header)
        self.assertEqual(table._colWidths, TABLE_COLUMN_WIDTHS)
        for cell in table._cellvalues[0]:
            self.assertEqual(cell.style.fontName, "Times-Bold")

        header_box = [cmd for cmd in table._linecmds if cmd[0] == "BOX" and cmd[1] == (0, 0) and cmd[2] == (-1, 0)]
        self.assertTrue(header_box, "Table header full-row border not found")
        self.assertAlmostEqual(header_box[0][3], TABLE_HEADER_BORDER_WIDTH, delta=0.01)

    def test_total_bar_present_on_single_page(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from pypdf import PdfReader
        path = self._generate_pdf()
        reader = PdfReader(path)
        text = reader.pages[0].extract_text() or ""
        self.assertIn("TOTAL DUE", text)

    # --- 5. Date column wide enough to avoid avoidable wrapping ---

    def test_date_column_wider_than_old_width(self):
        old_w = 0.78 * 72
        self.assertGreater(TABLE_COLUMN_WIDTHS[0], old_w)

    def test_date_column_fits_long_date_text(self):
        available = TABLE_COLUMN_WIDTHS[0] - (TABLE_CELL_LEFT_PADDING + TABLE_CELL_RIGHT_PADDING)
        # "June 22, 2026" at the body size in Times-Roman is roughly 58pt.
        self.assertGreaterEqual(available, 58)

    def test_long_date_does_not_wrap_in_pdf(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from pypdf import PdfReader
        lines = [{
            "service_date": "2026-06-22",
            "participants_snapshot": "Avery Stone",
            "description_snapshot": "Office Visit",
            "duration_minutes": 60,
            "line_amount_cents": 5000,
        }]
        path = self._generate_pdf(lines=lines)
        reader = PdfReader(path)
        text = reader.pages[0].extract_text() or ""
        self.assertIn("June 22, 2026", text)

    # --- 6. Short-invoice footer placement and page balance ---

    def test_short_invoice_stays_one_page(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from pypdf import PdfReader

        path = self._generate_pdf()
        self.assertEqual(len(PdfReader(path).pages), 1)

    def test_short_invoice_uses_footer_pushdown_spacing(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")

        render = {
            "lines": _sample_lines(),
        }
        self.assertLessEqual(_footer_pushdown_height(render), 0.30 * 72)
        self.assertGreaterEqual(PAYMENT_FOOTER_MIN_CLEARANCE, 0.30 * 72)

    def test_multi_page_invoice_uses_no_extra_footer_pushdown(self):
        render = {
            "lines": _multi_page_lines(),
        }
        self.assertEqual(_footer_pushdown_height(render), 0.0)

    # --- 7. Multi-page table headers still repeat ---

    def test_multi_page_table_headers_repeat(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from pypdf import PdfReader
        path = self._generate_pdf(lines=_multi_page_lines())
        reader = PdfReader(path)
        self.assertGreater(len(reader.pages), 1)
        # All pages except the last must have table headers (last page may be totals only)
        for page in reader.pages[:-1]:
            text = page.extract_text() or ""
            self.assertIn("Date", text)
            self.assertIn("Participants", text)

    def test_multi_page_invoice_still_splits_normally(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from pypdf import PdfReader

        path = self._generate_pdf(lines=_multi_page_lines())
        self.assertGreater(len(PdfReader(path).pages), 1)

    # --- 8. Footer/page numbering behavior remains ---

    def test_footer_invoice_number_on_every_page(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from pypdf import PdfReader
        path = self._generate_pdf(lines=_multi_page_lines())
        reader = PdfReader(path)
        self.assertGreater(len(reader.pages), 1)
        for page in reader.pages:
            text = page.extract_text() or ""
            self.assertIn("Invoice 2026-0042", text)

    def test_page_numbering_increments(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from pypdf import PdfReader
        path = self._generate_pdf(lines=_multi_page_lines())
        reader = PdfReader(path)
        self.assertGreater(len(reader.pages), 1)
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            self.assertIn(f"Page {i + 1}", text)

    def test_total_and_payment_only_on_last_page(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from pypdf import PdfReader
        path = self._generate_pdf(lines=_multi_page_lines())
        reader = PdfReader(path)
        self.assertGreater(len(reader.pages), 1)
        texts = [page.extract_text() or "" for page in reader.pages]
        self.assertNotIn("TOTAL DUE", "\n".join(texts[:-1]))
        self.assertIn("TOTAL DUE", texts[-1])
        self.assertIn("Please make checks payable to:", texts[-1])

    # --- 9. Existing-file overwrite protection remains ---

    def test_overwrite_protection_raises(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        path = self._generate_pdf()
        self.assertTrue(path.is_file())
        with self.assertRaises(FileExistsError):
            generate_invoice_pdf(_sample_invoice(), _sample_lines(), path)

    # --- 10. PDF generation produces a nonempty valid file ---

    def test_generates_nonempty_valid_pdf(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        path = self._generate_pdf()
        self.assertTrue(path.is_file())
        self.assertGreater(path.stat().st_size, 1000)
        with open(path, "rb") as f:
            header = f.read(5)
        self.assertEqual(header, b"%PDF-")

    def test_returns_sha256_hash(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        path = self._generate_pdf()
        path2 = self.root / "Invoice_2026-0043.pdf"
        result = generate_invoice_pdf(
            _sample_invoice(invoice_number="2026-0043"), _sample_lines(), path2,
        )
        self.assertEqual(len(result), 64)
        self.assertEqual(result, hashlib.sha256(path2.read_bytes()).hexdigest())

    # --- 11. Header contract: balanced columns, compact metadata, no period row ---

    def _extract_text_positions(self, path):
        from pypdf import PdfReader
        reader = PdfReader(path)
        snips = []
        reader.pages[0].extract_text(
            visitor_text=lambda t, cm, tm, fd, fs: snips.append((t, tm[4], tm[5])) if t and t.strip() else None
        )
        return snips

    def test_billing_period_removed_from_pdf_metadata(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        path = self._generate_pdf()
        from pypdf import PdfReader
        text = PdfReader(path).pages[0].extract_text() or ""
        self.assertNotIn("Invoice Number", text)
        self.assertNotIn("Invoice Date", text)
        self.assertNotIn("Billing Period", text)

    def test_draft_preview_uses_approved_layout(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        pdf_bytes = generate_draft_pdf_bytes(_sample_invoice(invoice_number=""), _sample_lines())
        text = self._extract_pdf_text(pdf_bytes)
        self.assertIn("INVOICE", text)
        self.assertIn("DRAFT", text)
        self.assertNotIn("Invoice Number", text)
        self.assertNotIn("Invoice Date", text)
        self.assertIn("Bill To:", text)
        self.assertIn("Jordana Singer, LCSW", text)
        self.assertNotIn("Demo Practice", text)
        self.assertNotIn("Billing Period", text)
        self.assertNotIn("FROM", text)
        self.assertIn("TOTAL DUE", text)
        self.assertNotIn("Account name:", text)

    def test_finalized_pdf_uses_approved_layout_without_draft_label(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        text = self._extract_pdf_text(self._generate_pdf())
        self.assertIn("INVOICE", text)
        self.assertNotIn("DRAFT", text)
        self.assertNotIn("Invoice Number", text)
        self.assertNotIn("Invoice Date", text)
        self.assertIn("Invoice No. 2026-0042", text)
        self.assertIn("Bill To:", text)
        self.assertIn("Jordana Singer, LCSW", text)
        self.assertNotIn("Billing Period", text)
        self.assertNotIn("FROM", text)

    def test_long_client_multiline_address_and_delivery_line_render(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        invoice = _sample_invoice(
            bill_to_name_snapshot="Alexandria Montgomery-Sterling Family Account",
            bill_to_address_snapshot="12345 Long Billing Address Boulevard\nApartment 12B\nExample Harbor, FL 00000",
            bill_to_email_snapshot="billing.long-client@example.test",
            delivery_method="both",
        )
        text = self._extract_pdf_text(self._generate_pdf(invoice=invoice, lines=_sample_lines(2)))
        self.assertIn("Alexandria Montgomery-Sterling Family Account", text)
        self.assertIn("Apartment 12B", text)
        self.assertIn("Example Harbor, FL 00000", text)
        self.assertIn("Via Email: billing.long-client@example.test", text)
        self.assertIn("TOTAL DUE", text)

    def test_delivery_line_absent_when_email_not_available(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        invoice = _sample_invoice(bill_to_email_snapshot="", delivery_method="mail")
        text = self._extract_pdf_text(self._generate_pdf(invoice=invoice))
        self.assertNotIn("Via Email:", text)

    def test_multiple_session_rows_preserved(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        lines = _sample_lines(count=6, date_start=10)
        text = self._extract_pdf_text(self._generate_pdf(lines=lines))
        for day in range(10, 16):
            self.assertIn(f"May {day}, 2026", text)
        self.assertEqual(text.count("Office Visit"), 6)

    def test_invoice_billto_provider_and_table_read_in_compact_order(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        path = self._generate_pdf()
        from pypdf import PdfReader
        text = PdfReader(path).pages[0].extract_text() or ""
        invoice_idx = text.index("INVOICE")
        bill_to_idx = text.index("Bill To")
        provider_idx = text.index("Jordana Singer, LCSW")
        table_idx = text.index("Participants")
        self.assertNotIn("FROM", text)
        self.assertLess(provider_idx, bill_to_idx)
        self.assertLess(invoice_idx, table_idx)
        self.assertLess(bill_to_idx, table_idx)
        self.assertLess(provider_idx, table_idx)

    def test_metadata_block_is_tight_and_left_column_weighted(self):
        self.assertLess(META_LABEL_WIDTH + META_VALUE_WIDTH, HEADER_LEFT_WIDTH)
        self.assertAlmostEqual(META_LABEL_WIDTH, 2.02 * 72, delta=0.1)
        self.assertAlmostEqual(META_VALUE_WIDTH, 0.0, delta=0.1)

    def test_header_uses_stable_table_for_short_medium_and_long_bill_to(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import Paragraph
        from reportlab.platypus import Table
        from jordana_invoice.invoice_rendering import build_invoice_render_model

        styles = getSampleStyleSheet()
        body = ParagraphStyle("B", parent=styles["BodyText"])
        label = ParagraphStyle("L", parent=body)
        title = ParagraphStyle("T", parent=body)

        def para(value, style=body):
            return Paragraph(str(value), style)

        meta = [para("INVOICE", title), para("June 28, 2026", body), para("DRAFT", body)]
        samples = [
            _sample_invoice(bill_to_address_snapshot="", bill_to_email_snapshot="", delivery_method="mail"),
            _sample_invoice(
                bill_to_address_snapshot="10 Sample Street\nExample, FL 00000",
                bill_to_email_snapshot="",
                delivery_method="mail",
            ),
            _sample_invoice(
                bill_to_address_snapshot="12345 Long Billing Address Boulevard\nApartment 12B\nExample Harbor, FL 00000",
                bill_to_email_snapshot="billing.long-client@example.test",
                delivery_method="both",
            ),
        ]
        previous_bill_to_height = 0.0
        for invoice in samples:
            render = build_invoice_render_model(invoice, _sample_lines())
            header = _build_header_table(render, meta, body, label, styles["Heading2"], "Business")
            self.assertIsInstance(header, Table)
            self.assertEqual(header._colWidths, [HEADER_LEFT_WIDTH, HEADER_RIGHT_WIDTH])
            _, height = header.wrap(CONTENT_WIDTH, 10_000)
            self.assertGreater(height, previous_bill_to_height)
            previous_bill_to_height = height

    def test_bill_to_variants_keep_supported_lines(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        variants = [
            _sample_invoice(bill_to_address_snapshot="", bill_to_email_snapshot="", delivery_method="mail"),
            _sample_invoice(bill_to_address_snapshot="10 Sample Street\nExample, FL 00000", bill_to_email_snapshot="", delivery_method="mail"),
            _sample_invoice(
                bill_to_address_snapshot="12345 Long Billing Address Boulevard\nApartment 12B\nExample Harbor, FL 00000",
                bill_to_email_snapshot="billing.long-client@example.test",
                delivery_method="both",
            ),
            _sample_invoice(bill_to_address_snapshot="", bill_to_email_snapshot="email-only@example.test", delivery_method="email"),
            _sample_invoice(bill_to_address_snapshot="10 Sample Street\nExample, FL 00000", bill_to_email_snapshot="", delivery_method="mail"),
        ]
        for index, invoice in enumerate(variants):
            text = self._extract_pdf_text(self._generate_pdf(invoice=invoice, filename=f"Invoice_variant_{index}.pdf"))
            self.assertIn("Bill To:", text)
            self.assertIn(invoice["bill_to_name_snapshot"], text)

    # --- 12. Payment block: centered, phone omitted, compact wording ---

    def test_payment_block_omits_phone_number(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        path = self._generate_pdf()
        from pypdf import PdfReader
        text = PdfReader(path).pages[0].extract_text() or ""
        self.assertEqual(text.count("(561) 385-8900"), 1)

    def test_payment_block_is_centered(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from reportlab.lib.enums import TA_CENTER
        from jordana_invoice.invoice_rendering import build_invoice_render_model
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

        styles = getSampleStyleSheet()
        body = ParagraphStyle("B", parent=styles["BodyText"])
        small = ParagraphStyle("S", parent=body)
        total_label = ParagraphStyle("TL", parent=body)
        total_amount = ParagraphStyle("TA", parent=body)
        payment_title = ParagraphStyle("PT", parent=body)
        render = build_invoice_render_model(_sample_invoice(), _sample_lines())
        footer = _build_pdf_footer(render, 15000, body, small, total_label, total_amount, payment_title)
        from reportlab.platypus import Table
        payment_columns = [f for f in footer if isinstance(f, Table)][-1]
        self.assertEqual(payment_columns.hAlign, "CENTER")
        payment_block = payment_columns._cellvalues[0][0]
        for paragraph in payment_block:
            if hasattr(paragraph, "style"):
                self.assertEqual(paragraph.style.alignment, TA_CENTER)
        self.assertEqual(PAYMENT_COLUMN_HEADING_TO_DETAIL_SPACING, 3.5)
        self.assertAlmostEqual(PAYMENT_SECTION_TOP_SPACING, (0.12 * 72) + 11.6, delta=0.1)
        self.assertAlmostEqual(PAYMENT_ZELLE_TOP_SPACING, 0.60 * 11.6, delta=0.1)
        self.assertEqual(payment_columns._colWidths, [CONTENT_WIDTH])

    def test_total_due_rules_align_to_session_table_width(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.platypus import Table
        from jordana_invoice.invoice_rendering import build_invoice_render_model

        styles = getSampleStyleSheet()
        body = ParagraphStyle("B", parent=styles["BodyText"])
        small = ParagraphStyle("S", parent=body)
        total_label = ParagraphStyle("TL", parent=body)
        total_amount = ParagraphStyle("TA", parent=body)
        payment_title = ParagraphStyle("PT", parent=body)
        render = build_invoice_render_model(_sample_invoice(), _sample_lines())
        footer = _build_pdf_footer(render, 15000, body, small, total_label, total_amount, payment_title)
        total_table = next(f for f in footer if isinstance(f, Table))
        self.assertEqual(total_table._colWidths, TABLE_COLUMN_WIDTHS)
        self.assertAlmostEqual(sum(total_table._colWidths), sum(TABLE_COLUMN_WIDTHS), delta=0.1)
        self.assertTrue(any(cmd[0] == "LINEABOVE" and cmd[1] == (0, 0) and cmd[2] == (-1, 0) for cmd in total_table._linecmds))
        self.assertTrue(any(cmd[0] == "LINEBELOW" and cmd[1] == (0, 0) and cmd[2] == (-1, 0) for cmd in total_table._linecmds))

    def test_payment_block_uses_compact_zelle_and_account_lines(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        path = self._generate_pdf()
        from pypdf import PdfReader
        text = PdfReader(path).pages[0].extract_text() or ""
        self.assertNotIn("PAYMENT:", text)
        self.assertIn("Please make checks payable to:", text)
        self.assertIn("Or pay via Zelle:", text)
        self.assertNotIn("Business email:", text)
        self.assertIn("demo-zelle@example.test", text)
        self.assertNotIn("Account name:", text)

    def test_render_model_uses_settings_for_provider_company_address_phone_and_zelle(self):
        from jordana_invoice.invoice_rendering import build_invoice_render_model

        invoice = _sample_invoice(
            business_name_snapshot="",
            provider_name_snapshot="",
            credentials_snapshot="",
            business_address_snapshot="",
            business_phone_snapshot="",
            payee_name_snapshot="",
            payment_address_snapshot="",
            zelle_recipient_snapshot="",
            status="draft",
        )
        profile = {
            "business_name": "Custom Settings Practice",
            "provider_display_name": "Jordana Singer",
            "credentials_display": "LCSW",
            "address_line_1": "3659 Siena Circle",
            "city": "Wellington",
            "state": "FL",
            "postal_code": "33414",
            "phone": "(561) 385-8900",
            "payee_name": "Jordana Singer",
            "payment_address_line_1": "3659 Siena Circle",
            "payment_city": "Wellington",
            "payment_state": "FL",
            "payment_postal_code": "33414",
            "zelle_recipient": "settings-zelle@example.test",
        }
        render = build_invoice_render_model(invoice, _sample_lines(), business_profile=profile)
        self.assertEqual(render["sender_lines"][0], "Jordana Singer, LCSW")
        self.assertNotIn("Custom Settings Practice", render["sender_lines"])
        self.assertIn("3659 Siena Circle", render["sender_lines"])
        self.assertIn("Wellington, FL 33414", render["sender_lines"])
        self.assertIn("(561) 385-8900", render["sender_lines"])
        self.assertEqual(render["payment_zelle_value"], "settings-zelle@example.test")

    def test_provider_name_does_not_duplicate_license_suffix(self):
        from jordana_invoice.invoice_rendering import build_invoice_render_model

        invoice = _sample_invoice(provider_name_snapshot="Jordana Singer, LCSW", credentials_snapshot="LCSW")
        render = build_invoice_render_model(invoice, _sample_lines())
        self.assertEqual(render["sender_lines"][0], "Jordana Singer, LCSW")
        self.assertNotIn("LCSW, LCSW", render["sender_lines"][0])

    def test_missing_company_name_uses_existing_omission_fallback(self):
        from jordana_invoice.invoice_rendering import build_invoice_render_model

        invoice = _sample_invoice(business_name_snapshot="")
        render = build_invoice_render_model(invoice, _sample_lines(), business_profile={"business_name": ""})
        self.assertEqual(render["sender_lines"][0], "Jordana Singer, LCSW")
        self.assertNotIn("", render["sender_lines"][1:])

    def test_renderer_does_not_hardcode_private_company_name(self):
        import inspect
        from jordana_invoice import invoice_pdf, invoice_rendering

        combined = inspect.getsource(invoice_pdf) + inspect.getsource(invoice_rendering)
        self.assertNotIn("Psychotherapy of the Palm Beaches", combined)


# --- 13. Preview / Finalization parity (canonical shared renderer) ---

class InvoicePreviewFinalizationParityTests(unittest.TestCase):
    """Regression tests proving draft preview and finalized PDFs use the same
    canonical renderer (_generate_invoice_pdf_bytes)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _generate_final_pdf(self, invoice=None, lines=None, filename="Invoice_final.pdf"):
        path = self.root / filename
        generate_invoice_pdf(invoice or _sample_invoice(), lines or _sample_lines(), path)
        return path

    def _extract_pdf_text(self, path_or_bytes):
        from pypdf import PdfReader
        source = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes) else path_or_bytes
        reader = PdfReader(source)
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    def _extract_text_positions(self, path_or_bytes):
        from pypdf import PdfReader
        source = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes) else path_or_bytes
        reader = PdfReader(source)
        snips = []
        reader.pages[0].extract_text(
            visitor_text=lambda t, cm, tm, fd, fs: snips.append((t, tm[4], tm[5])) if t and t.strip() else None
        )
        return snips

    def test_both_functions_delegate_to_same_canonical_renderer(self):
        """generate_invoice_pdf and generate_draft_pdf_bytes must both call
        _generate_invoice_pdf_bytes — verify via source inspection."""
        import inspect
        src_final = inspect.getsource(generate_invoice_pdf)
        src_draft = inspect.getsource(generate_draft_pdf_bytes)
        self.assertIn("_generate_invoice_pdf_bytes", src_final)
        self.assertIn("_generate_invoice_pdf_bytes", src_draft)

    def test_draft_and_finalized_share_same_render_path(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        invoice = _sample_invoice()
        lines = _sample_lines()
        draft_bytes = generate_draft_pdf_bytes(
            _sample_invoice(invoice_number=""),
            lines,
        )
        final_path = self._generate_final_pdf(invoice=invoice, lines=lines)
        final_bytes = final_path.read_bytes()
        draft_text = self._extract_pdf_text(draft_bytes)
        final_text = self._extract_pdf_text(final_bytes)
        shared_strings = [
            "INVOICE",
            "Bill To:",
            "Avery Stone",
            "Jordana Singer, LCSW",
            "TOTAL DUE",
            "Please make checks payable to:",
            "Or pay via Zelle:",
            "demo-zelle@example.test",
        ]
        for s in shared_strings:
            self.assertIn(s, draft_text, f"Draft PDF missing: {s}")
            self.assertIn(s, final_text, f"Finalized PDF missing: {s}")

    def test_only_difference_is_draft_label_vs_invoice_number(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        invoice = _sample_invoice()
        lines = _sample_lines()
        draft_bytes = generate_draft_pdf_bytes(
            _sample_invoice(invoice_number=""),
            lines,
        )
        final_path = self._generate_final_pdf(invoice=invoice, lines=lines)
        final_bytes = final_path.read_bytes()
        draft_text = self._extract_pdf_text(draft_bytes)
        final_text = self._extract_pdf_text(final_bytes)
        self.assertIn("DRAFT", draft_text)
        self.assertNotIn("DRAFT", final_text)
        self.assertIn("Invoice No. 2026-0042", final_text)
        self.assertNotIn("2026-0042", draft_text)

    def test_layout_parity_short_bill_to(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        invoice = _sample_invoice(
            bill_to_address_snapshot="",
            bill_to_email_snapshot="",
            delivery_method="mail",
        )
        lines = _sample_lines()
        draft_bytes = generate_draft_pdf_bytes(_sample_invoice(invoice_number="", bill_to_address_snapshot="", bill_to_email_snapshot="", delivery_method="mail"), lines)
        final_path = self._generate_final_pdf(invoice=invoice, lines=lines)
        final_bytes = final_path.read_bytes()
        draft_snips = self._extract_text_positions(draft_bytes)
        final_snips = self._extract_text_positions(final_bytes)
        draft_provider = [s for s in draft_snips if "Jordana" in s[0]]
        final_provider = [s for s in final_snips if "Jordana" in s[0]]
        self.assertTrue(draft_provider and final_provider)
        self.assertAlmostEqual(draft_provider[0][2], final_provider[0][2], delta=1.0,
                               msg="Provider block Y position differs between draft and final")

    def test_layout_parity_medium_bill_to(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        invoice = _sample_invoice(
            bill_to_address_snapshot="10 Sample Street\nExample, FL 00000",
            bill_to_email_snapshot="",
            delivery_method="mail",
        )
        lines = _sample_lines()
        draft_bytes = generate_draft_pdf_bytes(
            _sample_invoice(invoice_number="", bill_to_address_snapshot="10 Sample Street\nExample, FL 00000", bill_to_email_snapshot="", delivery_method="mail"),
            lines,
        )
        final_path = self._generate_final_pdf(invoice=invoice, lines=lines)
        final_bytes = final_path.read_bytes()
        draft_snips = self._extract_text_positions(draft_bytes)
        final_snips = self._extract_text_positions(final_bytes)
        draft_provider = [s for s in draft_snips if "Jordana" in s[0]]
        final_provider = [s for s in final_snips if "Jordana" in s[0]]
        self.assertTrue(draft_provider and final_provider)
        self.assertAlmostEqual(draft_provider[0][2], final_provider[0][2], delta=1.0)

    def test_layout_parity_long_bill_to_with_email(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        invoice = _sample_invoice(
            bill_to_address_snapshot="12345 Long Billing Address Boulevard\nApartment 12B\nExample Harbor, FL 00000",
            bill_to_email_snapshot="billing.long-client@example.test",
            delivery_method="both",
        )
        lines = _sample_lines()
        draft_bytes = generate_draft_pdf_bytes(
            _sample_invoice(invoice_number="",
                bill_to_address_snapshot="12345 Long Billing Address Boulevard\nApartment 12B\nExample Harbor, FL 00000",
                bill_to_email_snapshot="billing.long-client@example.test",
                delivery_method="both"),
            lines,
        )
        final_path = self._generate_final_pdf(invoice=invoice, lines=lines)
        final_bytes = final_path.read_bytes()
        draft_text = self._extract_pdf_text(draft_bytes)
        final_text = self._extract_pdf_text(final_bytes)
        for s in ["Alexandria Montgomery-Sterling Family Account" if "Alexandria" in invoice.get("bill_to_name_snapshot", "") else "Avery Stone",
                  "Apartment 12B", "Example Harbor, FL 00000",
                  "Via Email: billing.long-client@example.test"]:
            self.assertIn(s, draft_text)
            self.assertIn(s, final_text)

    def test_right_invoice_block_is_present_across_all_bill_to_sizes(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import Paragraph
        from reportlab.platypus import Table
        from jordana_invoice.invoice_rendering import build_invoice_render_model

        styles = getSampleStyleSheet()
        body = ParagraphStyle("B", parent=styles["BodyText"])
        label = ParagraphStyle("L", parent=body)
        title = ParagraphStyle("T", parent=body)

        def para(value, style=body):
            return Paragraph(str(value), style)

        meta = [para("INVOICE", title), para("June 28, 2026", body), para("DRAFT", body)]
        variants = [
            _sample_invoice(bill_to_address_snapshot="", bill_to_email_snapshot="", delivery_method="mail"),
            _sample_invoice(bill_to_address_snapshot="10 Sample Street\nExample, FL 00000", bill_to_email_snapshot="", delivery_method="mail"),
            _sample_invoice(
                bill_to_address_snapshot="12345 Long Billing Address Boulevard\nApartment 12B\nExample Harbor, FL 00000",
                bill_to_email_snapshot="billing.long-client@example.test",
                delivery_method="both",
            ),
        ]
        for invoice in variants:
            render = build_invoice_render_model(invoice, _sample_lines())
            header = _build_header_table(render, meta, body, label, styles["Heading2"], "Business")
            self.assertIsInstance(header, Table)
            right_cell = header._cellvalues[0][1]
            right_block = right_cell[0] if isinstance(right_cell, list) else right_cell
            right_text_parts = []
            if hasattr(right_block, "meta_flowables"):
                right_text_parts.extend(getattr(item, "text", "") for item in right_block.meta_flowables)
            else:
                for flowable in right_block:
                    right_text_parts.append(getattr(flowable, "text", ""))
                    if isinstance(flowable, Table):
                        for row in flowable._cellvalues:
                            for cell in row:
                                if isinstance(cell, list):
                                    right_text_parts.extend(getattr(item, "text", "") for item in cell)
            right_text = "\n".join(right_text_parts)
            self.assertIn("INVOICE", right_text)
            self.assertIn("June 28, 2026", right_text)
            self.assertIn("DRAFT", right_text)

    def test_header_widths_prevent_bill_to_table_overlap_all_sizes(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import Paragraph
        from reportlab.platypus import Table
        from jordana_invoice.invoice_rendering import build_invoice_render_model

        styles = getSampleStyleSheet()
        body = ParagraphStyle("B", parent=styles["BodyText"])
        label = ParagraphStyle("L", parent=body)
        title = ParagraphStyle("T", parent=body)

        def para(value, style=body):
            return Paragraph(str(value), style)

        meta = [para("INVOICE", title), para("June 28, 2026", body)]
        cases = [
            _sample_invoice(bill_to_address_snapshot="", bill_to_email_snapshot="", delivery_method="mail"),
            _sample_invoice(bill_to_address_snapshot="10 Sample Street\nExample, FL 00000", bill_to_email_snapshot="", delivery_method="mail"),
            _sample_invoice(
                bill_to_address_snapshot="12345 Long Billing Address Boulevard\nApartment 12B\nExample Harbor, FL 00000",
                bill_to_email_snapshot="billing.long-client@example.test",
                delivery_method="both"),
        ]
        for invoice in cases:
            render = build_invoice_render_model(invoice, _sample_lines())
            header = _build_header_table(render, meta, body, label, styles["Heading2"], "Business")
            self.assertIsInstance(header, Table)
            self.assertAlmostEqual(sum(header._colWidths), CONTENT_WIDTH, delta=0.1)
            _, height = header.wrap(CONTENT_WIDTH, 10_000)
            self.assertGreater(height, 0)

    def test_late_cancellation_description_renders_identically(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        desc = "Late Cancellation - 50% Fee"
        lines = [{
            "service_date": "2026-05-15",
            "participants_snapshot": "Avery Stone",
            "description_snapshot": desc,
            "duration_minutes": 60,
            "line_amount_cents": 7500,
        }]
        invoice = _sample_invoice(total_cents=7500)
        draft_bytes = generate_draft_pdf_bytes(_sample_invoice(invoice_number="", total_cents=7500), lines)
        final_path = self._generate_final_pdf(invoice=invoice, lines=lines, filename="Invoice_late_cancel.pdf")
        final_bytes = final_path.read_bytes()
        draft_text = self._extract_pdf_text(draft_bytes)
        final_text = self._extract_pdf_text(final_bytes)
        self.assertIn(desc, draft_text)
        self.assertIn(desc, final_text)

    def test_late_cancellation_waived_renders_identically(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        desc = "Late Cancellation - Waived"
        lines = [{
            "service_date": "2026-05-20",
            "participants_snapshot": "Avery Stone",
            "description_snapshot": desc,
            "duration_minutes": 0,
            "line_amount_cents": 0,
        }]
        invoice = _sample_invoice(total_cents=0)
        draft_bytes = generate_draft_pdf_bytes(_sample_invoice(invoice_number="", total_cents=0), lines)
        final_path = self._generate_final_pdf(invoice=invoice, lines=lines, filename="Invoice_waived.pdf")
        final_bytes = final_path.read_bytes()
        draft_text = self._extract_pdf_text(draft_bytes)
        final_text = self._extract_pdf_text(final_bytes)
        self.assertIn(desc, draft_text)
        self.assertIn(desc, final_text)

    def test_late_cancellation_custom_description_renders_identically(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        desc = "Custom Late Cancel - Full Session Rate"
        lines = [{
            "service_date": "2026-05-25",
            "participants_snapshot": "Avery Stone",
            "description_snapshot": desc,
            "duration_minutes": 45,
            "line_amount_cents": 15000,
        }]
        invoice = _sample_invoice(total_cents=15000)
        draft_bytes = generate_draft_pdf_bytes(_sample_invoice(invoice_number="", total_cents=15000), lines)
        final_path = self._generate_final_pdf(invoice=invoice, lines=lines, filename="Invoice_custom_cancel.pdf")
        final_bytes = final_path.read_bytes()
        draft_text = self._extract_pdf_text(draft_bytes)
        final_text = self._extract_pdf_text(final_bytes)
        self.assertIn(desc, draft_text)
        self.assertIn(desc, final_text)

    def test_insurance_coding_block_renders_identically(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from jordana_invoice.invoice_rendering import build_invoice_render_model
        profile = {
            "insurance_ein": "12-3456789",
            "insurance_npi": "1234567890",
            "insurance_sw": "SW001",
        }
        invoice = _sample_invoice(
            status="finalized",
            insurance_coding_included=1,
            insurance_diagnosis_code_snapshot="F41.1",
            insurance_ein_snapshot="12-3456789",
            insurance_npi_snapshot="1234567890",
            insurance_sw_snapshot="SW001",
        )
        lines = _sample_lines()
        draft_model = build_invoice_render_model(
            _sample_invoice(invoice_number="", status="draft"),
            lines,
            business_profile=profile,
            insurance_coding_payload={
                "insurance_coding_included": True,
                "insurance_diagnosis_code": "F41.1",
            },
        )
        draft_bytes = generate_draft_pdf_bytes(
            _sample_invoice(invoice_number="", status="draft"),
            lines,
            render_model=draft_model,
        )
        final_model = build_invoice_render_model(invoice, lines)
        final_path = self.root / "Invoice_insurance.pdf"
        generate_invoice_pdf(invoice, lines, final_path, render_model=final_model)
        final_bytes = final_path.read_bytes()
        draft_text = self._extract_pdf_text(draft_bytes)
        final_text = self._extract_pdf_text(final_bytes)
        for s in ["Diagnosis Code: F41.1", "EIN: 12-3456789", "NPI: 1234567890", "SW: SW001"]:
            self.assertIn(s, draft_text, f"Draft PDF missing insurance line: {s}")
            self.assertIn(s, final_text, f"Finalized PDF missing insurance line: {s}")

    def test_old_renderer_not_reachable_from_finalization(self):
        """Verify that generate_invoice_pdf delegates to _generate_invoice_pdf_bytes
        and does not contain its own style definitions or story-building code."""
        import inspect
        src = inspect.getsource(generate_invoice_pdf)
        self.assertIn("_generate_invoice_pdf_bytes", src)
        forbidden = ["fontName=\"Times-Roman\"", "fontName=\"Helvetica\"", "SimpleDocTemplate", "ParagraphStyle"]
        for token in forbidden:
            self.assertNotIn(token, src,
                             f"generate_invoice_pdf still contains '{token}' — it should be a thin wrapper")


if __name__ == "__main__":
    unittest.main()
