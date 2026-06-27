import re
import unittest
from pathlib import Path


REVIEW_JS = Path("app/jordana_invoice/static/review.js").read_text()


class HtmlEscapingFunctionTests(unittest.TestCase):
    """Verify that escapeHtml, escapeAttr, and fmt are defined correctly."""

    def test_escape_html_function_exists(self):
        self.assertIn("function escapeHtml(value)", REVIEW_JS)

    def test_escape_html_replaces_dangerous_chars(self):
        self.assertIn("&amp;", REVIEW_JS)
        self.assertIn("&lt;", REVIEW_JS)
        self.assertIn("&gt;", REVIEW_JS)
        self.assertIn("&quot;", REVIEW_JS)
        self.assertIn("&#039;", REVIEW_JS)

    def test_escape_html_handles_null_undefined_false(self):
        self.assertIn("value === null || value === undefined || value === false", REVIEW_JS)

    def test_escape_attr_function_exists(self):
        self.assertIn("function escapeAttr(value)", REVIEW_JS)

    def test_escape_attr_delegates_to_escape_html(self):
        self.assertIn("return escapeHtml(value);", REVIEW_JS)

    def test_fmt_uses_escape_html(self):
        self.assertIn("const fmt = (v) => v ? escapeHtml(v) : \"-\";", REVIEW_JS)


class ReviewJsEscapingTests(unittest.TestCase):
    """Static-analysis tests ensuring user-controlled fields are escaped in review.js."""

    def test_billing_type_label_uses_escape_html(self):
        self.assertIn('escapeHtml(customDescription)', REVIEW_JS)
        self.assertIn('escapeHtml(v)', REVIEW_JS)

    def test_rate_scope_person_uses_escape_attr_and_escape_html(self):
        self.assertIn('escapeAttr(p.person_id', REVIEW_JS)
        self.assertIn('escapeHtml(p.display_name', REVIEW_JS)

    def test_bill_to_summary_uses_fmt(self):
        self.assertIn("fmt(billingParty.billing_name)", REVIEW_JS)

    def test_rule_explanation_uses_escape_html(self):
        self.assertIn("escapeHtml(row.participant_names)", REVIEW_JS)
        self.assertIn("escapeHtml(row.account_name)", REVIEW_JS)
        self.assertIn("escapeHtml(row.display_name)", REVIEW_JS)

    def test_rate_source_description_uses_escape_html(self):
        self.assertIn("escapeHtml(names[0]", REVIEW_JS)
        self.assertIn("names.map(escapeHtml)", REVIEW_JS)

    def test_relationship_editor_uses_escape_html(self):
        start = REVIEW_JS.index("function renderRelationshipEditor")
        end = REVIEW_JS.index("function openBillingRelationshipEditor")
        section = REVIEW_JS[start:end]
        self.assertIn("escapeHtml(accountName)", section)
        self.assertIn("escapeHtml(billingName)", section)

    def test_invoice_editor_escapes_input_values(self):
        start = REVIEW_JS.index("function renderInvoiceEditor")
        end = REVIEW_JS.index("function renderInvoicePreview")
        section = REVIEW_JS[start:end]
        self.assertIn("escapeAttr(i.invoice_date)", section)
        self.assertIn("escapeAttr(line.invoice_line_item_id)", section)
        self.assertIn("escapeHtml(line.description_snapshot)", section)

    def test_invoice_preview_escapes_values(self):
        start = REVIEW_JS.index("function renderInvoicePreview")
        end_marker = REVIEW_JS.index("function renderRateScopeResults", start)
        section = REVIEW_JS[start:end_marker]
        self.assertIn("escapeAttr(i.status)", section)
        self.assertIn("fmt(line.service_date_display)", section)

    def test_rate_scope_results_uses_escape_attr(self):
        start = REVIEW_JS.index("function renderRateScopeResults")
        end = REVIEW_JS.index("function renderRateRulePreview", start)
        section = REVIEW_JS[start:end]
        self.assertIn("escapeAttr(mode)", section)
        self.assertIn("escapeHtml(code)", section)

    def test_no_unescaped_raw_title_in_innerhtml(self):
        self.assertNotIn("${item.raw_title}", REVIEW_JS)
        self.assertNotIn("${row.raw_title}", REVIEW_JS)

    def test_no_unescaped_display_name_in_innerhtml(self):
        self.assertNotIn("${p.display_name}}", REVIEW_JS)
        self.assertNotIn("${row.display_name}}", REVIEW_JS)

    def test_no_unescaped_participant_names_in_innerhtml(self):
        self.assertNotIn("${row.participant_names}}", REVIEW_JS)

    def test_no_unescaped_account_name_in_innerhtml(self):
        self.assertNotIn("${row.account_name}}", REVIEW_JS)

    def test_no_unescaped_billing_name_in_innerhtml(self):
        self.assertNotIn("${billingParty.billing_name}}", REVIEW_JS)

    def test_no_unescaped_person_id_in_data_id(self):
        self.assertNotIn('data-id="${p.person_id', REVIEW_JS)
        self.assertNotIn('data-id="${row.person_id', REVIEW_JS)
        self.assertNotIn('data-id="${item.person_id', REVIEW_JS)

    def test_no_unescaped_candidate_id_in_data_id(self):
        self.assertNotIn('data-id="${item.candidate_id', REVIEW_JS)

    def test_no_unescaped_invoice_id_in_data_invoice(self):
        self.assertNotIn('data-invoice-id="${inv.invoice_id', REVIEW_JS)

    def test_no_unescaped_line_item_id_in_data_line(self):
        self.assertNotIn('data-line="${line.invoice_line_item_id', REVIEW_JS)

    def test_no_unescaped_billing_party_id_in_data(self):
        self.assertNotIn('data-bpid="${', REVIEW_JS)

    def test_no_unescaped_person_id_in_data_cid(self):
        self.assertNotIn('data-cid="${p.person_id', REVIEW_JS)

    def test_calendar_label_uses_fmt(self):
        self.assertIn("fmt(session.calendar_name)", REVIEW_JS)

    def test_title_time_warning_uses_fmt(self):
        self.assertIn("fmt(session.title_time_text)", REVIEW_JS)


class CsvSafeIntegrationTests(unittest.TestCase):
    """Verify that csv_safe is applied in CSV export code paths."""

    def test_csv_reports_imports_csv_safe(self):
        csv_reports = Path("app/jordana_invoice/csv_reports.py").read_text()
        self.assertIn("from .util import csv_safe", csv_reports)

    def test_csv_reports_applies_csv_safe_in_write_rows(self):
        csv_reports = Path("app/jordana_invoice/csv_reports.py").read_text()
        self.assertIn("csv_safe(row.get(column, \"\"))", csv_reports)

    def test_csv_reports_applies_csv_safe_in_stream_csv(self):
        csv_reports = Path("app/jordana_invoice/csv_reports.py").read_text()
        self.assertIn("csv_safe(row.get(col, \"\"))", csv_reports)


if __name__ == "__main__":
    unittest.main()
