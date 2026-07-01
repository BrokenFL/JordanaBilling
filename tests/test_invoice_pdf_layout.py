"""Focused tests for invoice PDF presentation refinement."""
import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from jordana_invoice.invoice_pdf import (
    BODY_FONT_SIZE,
    CONTENT_WIDTH,
    HEADER_LEFT_WIDTH,
    HEADER_RIGHT_WIDTH,
    HEADER_TO_TABLE_SPACING,
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
    PAYMENT_FOOTER_MIN_CLEARANCE,
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
    _build_session_table,
    _footer_pushdown_height,
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
        "business_name_snapshot": "Psychotherapy of the Palm Beaches",
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
        "zelle_recipient_snapshot": "jordana.singer@gmail.com",
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
        approx_right = s[1] + len(s[0]) * s[2] * 0.5
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
        self.assertGreaterEqual(BODY_FONT_SIZE, 10.0)
        self.assertLessEqual(BODY_FONT_SIZE, 10.5)
        self.assertAlmostEqual(SMALL_FONT_SIZE, 9.0, delta=0.1)
        self.assertGreaterEqual(TITLE_FONT_SIZE, 28.0)
        self.assertLessEqual(TITLE_FONT_SIZE, 30.0)
        self.assertGreaterEqual(TOTAL_FONT_SIZE, 14.0)
        self.assertLessEqual(TOTAL_FONT_SIZE, 15.0)
        self.assertAlmostEqual(LABEL_FONT_SIZE, 9.0, delta=0.1)

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
        self.assertAlmostEqual(HEADER_TO_TABLE_SPACING, (0.13 * 72) + 5.0, delta=0.1)
        self.assertAlmostEqual(META_TO_BILLTO_SPACING, 0.16 * 72, delta=0.1)
        self.assertAlmostEqual(LOGO_TO_PROVIDER_SPACING, 0.08 * 72, delta=0.1)
        self.assertAlmostEqual(LOGO_TO_PROVIDER_SPACING_REDUCTION, 9.0, delta=0.1)
        self.assertAlmostEqual(RIGHT_HEADER_BLOCK_WIDTH, 2.45 * 72, delta=0.1)
        self.assertAlmostEqual(LOGO_OPTICAL_RIGHT_SHIFT, 0.0, delta=0.1)

    def test_row_padding_matches_print_spacing(self):
        self.assertEqual(TABLE_ROW_TOP_PADDING, 9)
        self.assertEqual(TABLE_ROW_BOTTOM_PADDING, 9)
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
        table_header = ParagraphStyle("TH", parent=body, fontName="Helvetica-Bold")

        def para(value, style=body):
            return Paragraph(str(value), style)

        table = _build_session_table(build_invoice_render_model(_sample_invoice(), _sample_lines()), para, table_header)
        self.assertEqual(table._colWidths, TABLE_COLUMN_WIDTHS)
        for cell in table._cellvalues[0]:
            self.assertEqual(cell.style.fontName, "Helvetica-Bold")

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
        # "June 22, 2026" at 9pt Helvetica is ~58pt
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
        self.assertIn("Please make all checks payable to:", texts[-1])

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
        self.assertIn("Invoice Number", text)
        self.assertIn("Invoice Date", text)
        self.assertNotIn("Billing Period", text)

    def test_draft_preview_uses_approved_layout(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        pdf_bytes = generate_draft_pdf_bytes(_sample_invoice(invoice_number=""), _sample_lines())
        text = self._extract_pdf_text(pdf_bytes)
        self.assertIn("INVOICE", text)
        self.assertIn("DRAFT", text)
        self.assertIn("Invoice Number", text)
        self.assertIn("Invoice Date", text)
        self.assertIn("BILL TO", text)
        self.assertIn("Jordana Singer LCSW", text)
        self.assertNotIn("Billing Period", text)
        self.assertNotIn("FROM", text)
        self.assertIn("TOTAL DUE", text)
        self.assertIn("Account name: Psychotherapy of the Palm Beaches", text)

    def test_finalized_pdf_uses_approved_layout_without_draft_label(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        text = self._extract_pdf_text(self._generate_pdf())
        self.assertIn("INVOICE", text)
        self.assertNotIn("DRAFT", text)
        self.assertIn("Invoice Number", text)
        self.assertIn("Invoice Date", text)
        self.assertIn("BILL TO", text)
        self.assertIn("Jordana Singer LCSW", text)
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
        bill_to_idx = text.index("BILL TO")
        provider_idx = text.index("Jordana Singer LCSW")
        table_idx = text.index("Participants")
        self.assertNotIn("FROM", text)
        self.assertLess(invoice_idx, bill_to_idx)
        self.assertLess(bill_to_idx, table_idx)
        self.assertLess(provider_idx, table_idx)

    def test_metadata_block_is_tight_and_left_column_weighted(self):
        self.assertLess(META_LABEL_WIDTH + META_VALUE_WIDTH, HEADER_LEFT_WIDTH)
        self.assertAlmostEqual(META_LABEL_WIDTH, 1.00 * 72, delta=0.1)
        self.assertAlmostEqual(META_VALUE_WIDTH, 1.02 * 72, delta=0.1)

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
        payment_table = [f for f in footer if isinstance(f, Table)][-1]
        payment_paragraphs = payment_table._cellvalues[0][0]
        self.assertTrue(payment_paragraphs, "Centered payment paragraphs not found")
        for paragraph in payment_paragraphs:
            self.assertEqual(paragraph.style.alignment, TA_CENTER)

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
        self.assertIn("Please make all checks payable to:", text)
        self.assertIn("Or send payment via Zelle to:", text)
        self.assertIn("jordana.singer@gmail.com", text)
        self.assertIn("Account name: Psychotherapy of the Palm Beaches", text)


if __name__ == "__main__":
    unittest.main()
