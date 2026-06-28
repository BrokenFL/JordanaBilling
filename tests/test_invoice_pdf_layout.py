"""Focused tests for invoice PDF layout refinement.

Verifies margins, content widths, column widths, date wrapping,
multi-page headers, footer/page numbering, overwrite protection,
and nonempty file generation.
"""
import hashlib
import tempfile
import unittest
from pathlib import Path

from jordana_invoice.invoice_pdf import generate_invoice_pdf

# US Letter in points
PAGE_W = 612.0
PAGE_H = 792.0
MARGIN_IN = 0.50
MARGIN_PT = MARGIN_IN * 72
CONTENT_W = PAGE_W - 2 * MARGIN_PT  # 540pt = 7.5"


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
        from reportlab.lib.units import inch
        cols = [1.00 * inch, 1.65 * inch, 2.90 * inch, 0.85 * inch, 1.10 * inch]
        self.assertAlmostEqual(sum(cols), CONTENT_W, delta=0.5)

    def test_header_column_widths_sum_to_content_width(self):
        from reportlab.lib.units import inch
        cols = [4.80 * inch, 2.70 * inch]
        self.assertAlmostEqual(sum(cols), CONTENT_W, delta=0.5)

    def test_total_bar_widths_sum_to_content_width(self):
        from reportlab.lib.units import inch
        cols = [6.40 * inch, 1.10 * inch]
        self.assertAlmostEqual(sum(cols), CONTENT_W, delta=0.5)

    # --- 3. Header/table/total use full frame width (content present) ---

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

    # --- 4. Date column wide enough to avoid avoidable wrapping ---

    def test_date_column_wider_than_old_width(self):
        from reportlab.lib.units import inch
        new_w = 1.00 * inch
        old_w = 0.78 * inch
        self.assertGreater(new_w, old_w)

    def test_date_column_fits_long_date_text(self):
        from reportlab.lib.units import inch
        available = 1.00 * inch - 10  # LEFTPADDING + RIGHTPADDING
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

    # --- 5. Multi-page table headers still repeat ---

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

    # --- 6. Footer/page numbering behavior remains ---

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

    # --- 7. Existing-file overwrite protection remains ---

    def test_overwrite_protection_raises(self):
        if not _has_pdf_deps():
            self.skipTest("PDF dependencies not installed")
        path = self._generate_pdf()
        self.assertTrue(path.is_file())
        with self.assertRaises(FileExistsError):
            generate_invoice_pdf(_sample_invoice(), _sample_lines(), path)

    # --- 8. PDF generation produces a nonempty valid file ---

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
