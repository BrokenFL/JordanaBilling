import unittest
from pathlib import Path


class ReviewUiStaticTests(unittest.TestCase):
    def test_inspector_dirty_list_does_not_reference_removed_session_fields(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertNotIn('"durationInput"', js)
        self.assertNotIn('"serviceInput"', js)
        self.assertIn('"durationChoiceInput"', js)
        self.assertIn('"billingTypeInput"', js)

    def test_sidebar_uses_user_facing_client_and_billing_relationship_labels(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()

        self.assertIn('id="peopleNav">Clients</a>', html)
        self.assertIn('id="clientsNav">Billing Relationships</a>', html)
        self.assertNotIn('id="peopleNav">People</a>', html)
        self.assertNotIn('id="clientsNav">Clients & Accounts</a>', html)

    def test_review_client_and_bill_to_controls_use_approved_terms(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn("Clients in this session", js)
        self.assertIn("Save Client(s)", js)
        self.assertIn("Search or add a client...", js)
        self.assertIn("Bill to client", js)
        self.assertIn("Edit Billing Relationship", js)
        self.assertNotIn("Save Participants", js)
        self.assertNotIn("Open Person Record", js)
        self.assertNotIn("Same as sole participant", js)
        self.assertNotIn("Search or create a bill-to contact", js)

    def test_inline_review_client_editor_does_not_expose_person_code(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("function showPersonEditor")
        end = js.index("function renderRelationshipEditor")
        editor = js[start:end]

        self.assertIn("First name", editor)
        self.assertIn("Last name", editor)
        self.assertIn("Email", editor)
        self.assertIn("Phone", editor)
        self.assertNotIn("Person code", editor)
        self.assertNotIn("person_code", editor)

    def test_billing_relationship_round_trip_uses_stable_return_context(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn('const RETURN_CONTEXT_KEY = "reviewBillingReturnContext"', js)
        self.assertIn("function buildReturnContext()", js)
        self.assertIn("candidateId: state.selected", js)
        self.assertIn("sessionId: session.id", js)
        self.assertIn("function validReturnContext(value)", js)
        self.assertIn("function persistReturnContext(context)", js)
        self.assertIn("function readReturnContext()", js)
        self.assertIn("function clearReturnContext()", js)
        self.assertIn("returnContextHash", js)

    def test_billing_relationship_save_returns_to_review_and_reloads_session(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("async function openAccountRecord")
        end = js.index("async function loadPeople")
        account_record = js[start:end]

        self.assertIn("/save-relationship", account_record)
        self.assertIn("/save-billing", account_record)
        self.assertIn("await loadList();", account_record)
        self.assertIn("await showReviewWorkbench();", account_record)
        self.assertIn("await selectCandidate(currentContext.candidateId);", account_record)
        self.assertIn('billing_party_type: "organization"', account_record)
        self.assertIn("Select the bill-to client before saving this billing relationship.", account_record)

    def test_invalid_or_missing_return_context_falls_back_to_normal_clients_screen(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn('if (view !== "clients" || !query) return null;', js)
        self.assertIn("if (!candidateId || !sessionId) return null;", js)
        self.assertIn('renderClientsLanding(returnContext);', js)
        self.assertIn('Open a billing relationship record.', Path("app/jordana_invoice/static/review.html").read_text())

    def test_rate_card_form_is_present_and_responsive(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()
        css = Path("app/jordana_invoice/static/review.css").read_text()
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn('id="rateRuleForm"', html)
        self.assertIn('id="rateFormMessage"', html)
        self.assertIn('type="submit"', html)
        self.assertIn("Add Rate Rule", html)
        self.assertIn("rate-form", css)
        self.assertIn("flex-wrap", css)
        self.assertIn("rate-form-message", css)
        self.assertIn("rateRuleForm", js)
        self.assertIn("rateFormMessage", js)

    def test_rate_card_supports_all_billing_session_types(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()
        for value in ("psychotherapy", "psychotherapy_house_call", "psychotherapy_weekend", "psychotherapy_evening", "custom"):
            self.assertIn(f'value="{value}"', html)

    def test_rate_card_form_validates_required_fields(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        self.assertIn("Amount is required", js)
        self.assertIn("Duration is required", js)
        self.assertIn("Session type is required", js)
        self.assertIn("Effective date is required", js)


if __name__ == "__main__":
    unittest.main()
