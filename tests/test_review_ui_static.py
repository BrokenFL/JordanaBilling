import unittest
from pathlib import Path


class ReviewUiStaticTests(unittest.TestCase):
    def test_inspector_dirty_list_does_not_reference_removed_session_fields(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertNotIn('"durationInput"', js)
        self.assertNotIn('"serviceInput"', js)
        self.assertIn('"durationChoiceInput"', js)
        self.assertIn('"billingTypeInput"', js)


if __name__ == "__main__":
    unittest.main()
