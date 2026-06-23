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
        self.assertIn("Confirm Client(s)", js)
        self.assertIn("Search or add a client...", js)
        self.assertIn("Bill to client", js)
        self.assertIn("Change payer or shared billing", js)
        self.assertNotIn("Save Participants", js)
        self.assertNotIn("Open Person Record", js)
        self.assertNotIn("Same as sole participant", js)
        self.assertNotIn("Search or create a bill-to contact", js)

    def test_confirmed_client_summary_renders_without_participant_chips(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        self.assertIn("if (!chips) return;", js)
        self.assertIn('relationship-summary success', js)

    def test_unresolved_client_step_contains_exactly_one_confirm_action(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("Clients in this session")
        end = js.index("<section class=\"section\">", start + 1)
        section = js[start:end]
        self.assertIn("Confirm Client(s)", section)
        self.assertIn('button id="changeClientsBtn">Change</button>', section)
        self.assertIn('button id="saveRelationshipBtn" class="save">Confirm Client(s)</button>', section)

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

    def test_selecting_existing_client_replaces_matching_proposed_chip(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn("function replaceMatchingProposedParticipant(person)", js)
        self.assertIn("participant.is_proposed &&", js)
        self.assertIn("state.participants[proposedIndex] = { ...state.participants[proposedIndex], ...nextParticipant };", js)
        self.assertNotIn("state.participants.push({ person_id: person.person_id, display_name: person.display_name, is_primary: state.participants.length === 0 });", js)

    def test_merge_is_hidden_for_proposed_participants(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("function showPersonEditor")
        end = js.index("function renderRelationshipEditor")
        editor = js[start:end]

        self.assertIn('const mergeButton = !p.is_proposed && p.person_id ? \'<button id="mergePersonBtn">Merge...</button>\' : "";', editor)
        self.assertIn('if ($("mergePersonBtn")) $("mergePersonBtn").onclick = async () => {', editor)

    def test_client_record_ui_has_new_sections_and_collapsed_advanced(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("async function openPersonRecord")
        end = js.index('["clientSearch","peopleSearch"]')
        person_record = js[start:end]

        self.assertIn("Client Details", person_record)
        self.assertIn("Billing Summary", person_record)
        self.assertIn("Bill-To Records", person_record)
        self.assertIn("Recent Sessions", person_record)
        self.assertIn("Client Rate Overrides", person_record)
        self.assertIn("Uses standard Rate Card. No client-specific override.", person_record)
        self.assertIn("<details>", person_record)
        self.assertIn("<summary>Advanced</summary>", person_record)
        self.assertIn("Known Calendar Names", person_record)
        self.assertEqual(person_record.count("<h5>Billing Relationships</h5>"), 1)

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

    def test_guided_review_uses_review_confidence_and_final_approval_gate(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn("Review confidence", html)
        self.assertIn('Review confidence: ${s.authority_score || 0}%', js)
        self.assertIn("readiness.all_ready", js)
        self.assertIn("Approve Session", js)

    def test_unresolved_client_and_payer_lock_later_steps(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn('Confirm Client(s) first.', js)
        self.assertIn('Confirm Bill To first.', js)
        self.assertIn("const billingLocked = !readiness.clients_ready;", js)
        self.assertIn("const sessionLocked = !readiness.clients_ready || !readiness.billing_ready;", js)

    def test_edited_rate_reveals_override_scope_and_unchanged_rate_does_not(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn("const rateChanged = currentRate !== suggestedRate && currentRate !== \"\";", js)
        self.assertIn('Apply this rate to:', js)
        self.assertIn('This session only', js)
        self.assertIn('Future sessions for this client', js)
        self.assertIn('Future joint sessions for these clients', js)

    def test_cancelled_no_show_billing_field_is_conditional(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        self.assertIn('const showCancellation = ["cancelled", "no_show"].includes(s.appointment_status);', js)

    def test_inline_relationship_roles_and_primary_controls_are_gone(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("function renderRelationshipEditor")
        end = js.index("function showAccountEditor")
        section = js[start:end]

        self.assertNotIn("data-role", section)
        self.assertNotIn("primaryMember", section)
        self.assertNotIn("Quick Edit Billing Relationship", section)
        self.assertIn("Open Billing Relationship Record", section)

    def test_frontend_time_display_uses_eastern_12_hour_format(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        self.assertIn('timeZone: "America/New_York"', js)
        self.assertIn('hour: "numeric"', js)
        self.assertIn('minute: "2-digit"', js)

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
