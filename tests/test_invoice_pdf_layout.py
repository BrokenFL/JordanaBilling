"""Focused tests for invoice PDF presentation refinement."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from jordana_invoice.invoice_pdf import (
    BODY_FONT_SIZE,
    CONTENT_WIDTH,
    HEADER_LEFT_WIDTH,
    HEADER_RIGHT_WIDTH,
    LABEL_FONT_SIZE,
    LETTER_PAGE_HEIGHT,
    LETTER_PAGE_WIDTH,
    LOGO_LEFT_SHIFT,
    LOGO_MAX_HEIGHT,
    LOGO_MAX_WIDTH,
    LOGO_SENDER_SPACING,
    PAYMENT_FOOTER_MIN_CLEARANCE,
    SMALL_FONT_SIZE,
    TABLE_CELL_LEFT_PADDING,
    TABLE_CELL_RIGHT_PADDING,
    TABLE_COLUMN_WIDTHS,
    TABLE_ROW_BOTTOM_PADDING,
    TABLE_ROW_TOP_PADDING,
    TITLE_FONT_SIZE,
    TOTAL_COLUMN_WIDTHS,
    TOTAL_FONT_SIZE,
    _footer_pushdown_height,
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
        "provider_name_snapshot": "Demo Provider",
        "bill_to_name_snapshot": "Avery Stone",
        "bill_to_address_snapshot": "10 Sample Street\nExample, FL 00000",
        "bill_to_email_snapshot": "avery@example.test",
        "delivery_method": "both",
        "total_cents": 15000,
        "total_label_snapshot": "TOTAL DUE",
        "payee_name_snapshot": "Demo Payee",
        "payment_address_snapshot": "Demo Payee\n100 Example Avenue\nExample, FL 00000",
        "business_phone_snapshot": "555-0100",
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
        self.assertAlmostEqual(LOGO_MAX_WIDTH, 1.725 * 72, delta=0.1)
        self.assertAlmostEqual(LOGO_MAX_HEIGHT, 1.2075 * 72, delta=0.1)

    def test_logo_scales_within_bounds(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        from jordana_invoice.invoice_pdf import _logo_flowable

        logo_path = Path("app/jordana_invoice/static/assets/jordana-logo.png")
        image = _logo_flowable(str(logo_path), LOGO_MAX_WIDTH, LOGO_MAX_HEIGHT)
        self.assertIsNotNone(image)
        self.assertLessEqual(image.drawWidth, LOGO_MAX_WIDTH + 0.1)
        self.assertLessEqual(image.drawHeight, LOGO_MAX_HEIGHT + 0.1)

    def test_logo_left_shift_matches_optical_padding(self):
        self.assertAlmostEqual(LOGO_LEFT_SHIFT, 0.24 * 72, delta=0.1)

    def test_logo_sender_spacing_reduced_from_original(self):
        original_spacing = 0.08 * 72
        self.assertLess(LOGO_SENDER_SPACING, original_spacing)
        reduction = original_spacing - LOGO_SENDER_SPACING
        self.assertGreaterEqual(reduction, 4.0)
        self.assertLessEqual(reduction, 6.0)

    def test_row_padding_matches_print_spacing(self):
        self.assertEqual(TABLE_ROW_TOP_PADDING, 9)
        self.assertEqual(TABLE_ROW_BOTTOM_PADDING, 9)
        self.assertGreaterEqual(TABLE_CELL_LEFT_PADDING, 6)
        self.assertGreaterEqual(TABLE_CELL_RIGHT_PADDING, 6)

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
        self.assertGreater(_footer_pushdown_height(render), 0.80 * 72)
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


if __name__ == "__main__":
    unittest.main()
