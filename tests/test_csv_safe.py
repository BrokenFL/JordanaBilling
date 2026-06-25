import unittest

from jordana_invoice.util import csv_safe


class CsvSafeTests(unittest.TestCase):
    """Unit tests for the csv_safe CSV-injection neutralisation helper."""

    def test_none_returns_empty(self):
        self.assertEqual(csv_safe(None), "")

    def test_empty_string_returns_empty(self):
        self.assertEqual(csv_safe(""), "")

    def test_equals_prefix_neutralised(self):
        self.assertEqual(csv_safe("=cmd|' /C calc'!A0"), "'=cmd|' /C calc'!A0")

    def test_plus_prefix_neutralised(self):
        self.assertEqual(csv_safe("+1+1"), "'+1+1")

    def test_minus_prefix_neutralised(self):
        self.assertEqual(csv_safe("-1+1"), "'-1+1")

    def test_at_prefix_neutralised(self):
        self.assertEqual(csv_safe("@SUM(A1:A2)"), "'@SUM(A1:A2)")

    def test_whitespace_then_equals_neutralised(self):
        self.assertEqual(csv_safe("  =evil"), "'  =evil")

    def test_whitespace_then_plus_neutralised(self):
        self.assertEqual(csv_safe("\t+evil"), "'\t+evil")

    def test_whitespace_then_minus_neutralised(self):
        self.assertEqual(csv_safe("  -evil"), "'  -evil")

    def test_whitespace_then_at_neutralised(self):
        self.assertEqual(csv_safe(" @evil"), "' @evil")

    def test_only_whitespace_unchanged(self):
        self.assertEqual(csv_safe("   "), "   ")

    def test_normal_text_unchanged(self):
        self.assertEqual(csv_safe("Hello World"), "Hello World")

    def test_numeric_string_unchanged(self):
        self.assertEqual(csv_safe("12345"), "12345")

    def test_decimal_unchanged(self):
        self.assertEqual(csv_safe("3.14"), "3.14")

    def test_date_string_unchanged(self):
        self.assertEqual(csv_safe("2025-06-15"), "2025-06-15")

    def test_dollar_sign_unchanged(self):
        self.assertEqual(csv_safe("$100"), "$100")

    def test_parentheses_unchanged(self):
        self.assertEqual(csv_safe("(test)"), "(test)")

    def test_integer_value_converted(self):
        self.assertEqual(csv_safe(42), "42")

    def test_float_value_converted(self):
        self.assertEqual(csv_safe(3.14), "3.14")

    def test_boolean_value_converted(self):
        self.assertEqual(csv_safe(True), "True")

    def test_tab_then_equals_neutralised(self):
        self.assertEqual(csv_safe("\t=cmd"), "'\t=cmd")

    def test_newline_then_equals_neutralised(self):
        self.assertEqual(csv_safe("\n=cmd"), "'\n=cmd")

    def test_negative_integer_unchanged(self):
        self.assertEqual(csv_safe(-5), "-5")

    def test_negative_decimal_unchanged(self):
        self.assertEqual(csv_safe(-3.14), "-3.14")

    def test_negative_integer_string_unchanged(self):
        self.assertEqual(csv_safe("-5"), "-5")

    def test_negative_decimal_string_unchanged(self):
        self.assertEqual(csv_safe("-3.14"), "-3.14")

    def test_negative_zero_unchanged(self):
        self.assertEqual(csv_safe("-0"), "-0")

    def test_negative_float_value_unchanged(self):
        self.assertEqual(csv_safe(-0.01), "-0.01")

    def test_whitespace_then_negative_number_unchanged(self):
        self.assertEqual(csv_safe("  -42"), "  -42")

    def test_whitespace_then_negative_decimal_unchanged(self):
        self.assertEqual(csv_safe("  -3.14"), "  -3.14")

    def test_dangerous_minus_text_neutralised(self):
        self.assertEqual(csv_safe("-evil"), "'-evil")

    def test_dangerous_minus_formula_neutralised(self):
        self.assertEqual(csv_safe("-1+1"), "'-1+1")

    def test_apostrophe_prefixed_value_unchanged(self):
        self.assertEqual(csv_safe("'already safe"), "'already safe")


if __name__ == "__main__":
    unittest.main()
