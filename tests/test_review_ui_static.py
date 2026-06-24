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
        self.assertIn("Billing Setup", person_record)
        self.assertIn("Billing Relationships", person_record)
        self.assertIn("Sessions", person_record)
        self.assertIn("Rate Preferences", person_record)
        self.assertIn("Individual Rate Overrides", person_record)
        self.assertIn("Joint-Session Overrides", person_record)
        self.assertIn("Uses standard Rate Card. No client-specific override.", person_record)
        self.assertIn("<details>", person_record)
        self.assertIn("<summary>Advanced</summary>", person_record)
        self.assertIn("Known Calendar Names", person_record)

    def test_client_record_renders_as_full_width_workspace(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()
        js = Path("app/jordana_invoice/static/review.js").read_text()
        css = Path("app/jordana_invoice/static/review.css").read_text()

        self.assertIn('id="personRecordView"', html)
        self.assertIn('id="peopleListView"', html)
        self.assertIn("client-workspace", js)
        self.assertIn("client-section", js)
        self.assertIn("client-header", js)
        self.assertIn(".client-workspace", css)
        self.assertIn(".client-section", css)
        self.assertIn(".client-header", css)

    def test_person_record_sidebar_removed(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()

        people_start = html.index('id="peopleView"')
        people_end = html.index('</section>', people_start) + len('</section>')
        people_html = html[people_start:people_end]
        self.assertNotIn('id="personRecord"', people_html)
        self.assertNotIn("record-pane", people_html)
        self.assertNotIn("crm-layout", people_html)

    def test_people_route_supports_person_id_path(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn("showPersonRecordPage", js)
        self.assertIn('location.pathname.startsWith("/people/")', js)
        self.assertIn('location.pathname.split("/")[2]', js)
        self.assertIn('location.hash.startsWith("#people/")', js)
        self.assertIn('location.hash.split("/")[1]', js)

    def test_back_to_clients_link_exists(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn("Back to Clients", js)
        self.assertIn('id="backToClients"', js)
        self.assertIn('href="#people"', js)

    def test_hashchange_listener_supports_person_navigation(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn('window.addEventListener("hashchange"', js)
        self.assertIn('hash.startsWith("people/")', js)
        self.assertIn('showPersonRecordPage(personId)', js)

    def test_client_sessions_render_as_table_rows(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("async function openPersonRecord")
        end = js.index('["clientSearch","peopleSearch"]')
        person_record = js[start:end]

        self.assertIn("client-sessions-table", person_record)
        self.assertIn("<table", person_record)
        self.assertIn("<thead>", person_record)
        self.assertIn("<tbody>", person_record)
        self.assertIn("<th>Date</th>", person_record)
        self.assertIn("<th>Participants</th>", person_record)
        self.assertIn("<th>Session Type</th>", person_record)
        self.assertIn("<th>Duration</th>", person_record)
        self.assertIn("<th>Time Category</th>", person_record)
        self.assertIn("<th>Rate</th>", person_record)
        self.assertIn("<th>Payment Status</th>", person_record)
        self.assertIn("<th>Review Status</th>", person_record)
        self.assertIn("<th>Open in Review</th>", person_record)
        self.assertIn("<tr>", person_record)

    def test_save_client_button_remains(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("async function openPersonRecord")
        end = js.index('["clientSearch","peopleSearch"]')
        person_record = js[start:end]

        self.assertIn('id="savePersonRecord"', person_record)
        self.assertIn("Save Client", person_record)
        self.assertIn('$("savePersonRecord").onclick', person_record)

    def test_billing_setup_section_shows_empty_message(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("async function openPersonRecord")
        end = js.index('["clientSearch","peopleSearch"]')
        person_record = js[start:end]

        self.assertIn("Billing Setup", person_record)
        self.assertIn("No billing setup saved", person_record)

    def test_billing_relationships_section_shows_empty_message(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("async function openPersonRecord")
        end = js.index('["clientSearch","peopleSearch"]')
        person_record = js[start:end]

        self.assertIn("Billing Relationships", person_record)
        self.assertIn("No billing relationships yet.", person_record)

    def test_billing_summary_renders_all_four_values(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("async function openPersonRecord")
        end = js.index('["clientSearch","peopleSearch"]')
        person_record = js[start:end]

        self.assertIn("summary-cards", person_record)
        self.assertIn("Active Billing Records", person_record)
        self.assertIn("Approved Uninvoiced Sessions", person_record)
        self.assertIn("Total Invoiced", person_record)
        self.assertIn("Outstanding Balance", person_record)
        self.assertIn("summary.active_billing_parties", person_record)
        self.assertIn("summary.approved_uninvoiced_sessions", person_record)
        self.assertIn("summary.total_invoiced_cents", person_record)
        self.assertIn("summary.outstanding_balance_cents", person_record)

    def test_billing_setup_renders_card_fields(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("async function openPersonRecord")
        end = js.index('["clientSearch","peopleSearch"]')
        person_record = js[start:end]

        self.assertIn("billing-cards", person_record)
        self.assertIn("billing-card", person_record)
        self.assertIn("billing-card-name", person_record)
        self.assertIn("billing-card-details", person_record)
        self.assertIn("billing_setup", person_record)

    def test_billing_setup_self_pay_label(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("async function openPersonRecord")
        end = js.index('["clientSearch","peopleSearch"]')
        person_record = js[start:end]

        self.assertIn("Bills sent to this client", person_record)

    def test_billing_setup_empty_state(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("async function openPersonRecord")
        end = js.index('["clientSearch","peopleSearch"]')
        person_record = js[start:end]

        self.assertIn("No billing setup saved", person_record)

    def test_self_pay_relationship_language(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("async function openPersonRecord")
        end = js.index('["clientSearch","peopleSearch"]')
        person_record = js[start:end]

        self.assertIn("pays for herself", person_record)

    def test_third_party_payer_relationship_language(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("async function openPersonRecord")
        end = js.index('["clientSearch","peopleSearch"]')
        person_record = js[start:end]

        self.assertIn("is billed to", person_record)
        # Verify directionality: personName comes before "is billed to", payer_display_name after
        line_start = person_record.index("is billed to")
        # The template literal should have personName before "is billed to" and payer_display_name after
        self.assertIn("${escapeHtml(personName)} is billed to ${escapeHtml(fmt(p.payer_display_name))}", person_record)

    def test_people_billed_for_relationship_language(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("async function openPersonRecord")
        end = js.index('["clientSearch","peopleSearch"]')
        person_record = js[start:end]

        self.assertIn("pays for", person_record)
        self.assertIn("peopleBilledFor", person_record)

    def test_account_information_is_secondary(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("async function openPersonRecord")
        end = js.index('["clientSearch","peopleSearch"]')
        person_record = js[start:end]

        self.assertIn("Related billing group information", person_record)
        self.assertIn("secondary-heading", person_record)

    def test_invoice_table_renders_expected_columns(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("async function openPersonRecord")
        end = js.index('["clientSearch","peopleSearch"]')
        person_record = js[start:end]

        self.assertIn("client-invoices-table", person_record)
        self.assertIn("<th>Invoice Number</th>", person_record)
        self.assertIn("<th>Billing Period</th>", person_record)
        self.assertIn("<th>Issue Date</th>", person_record)
        self.assertIn("<th>Bill To</th>", person_record)
        self.assertIn("<th>Status</th>", person_record)
        self.assertIn("<th>Total</th>", person_record)
        self.assertIn("<th>Balance</th>", person_record)
        self.assertIn("<th>Open</th>", person_record)

    def test_invoice_open_uses_existing_route(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("async function openPersonRecord")
        end = js.index('["clientSearch","peopleSearch"]')
        person_record = js[start:end]

        self.assertIn('data-open-invoice', person_record)
        self.assertIn("openInvoice", person_record)

    def test_invoice_empty_state(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("async function openPersonRecord")
        end = js.index('["clientSearch","peopleSearch"]')
        person_record = js[start:end]

        self.assertIn("No invoices yet", person_record)

    def test_no_payment_or_finalize_controls_on_client_page(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("async function openPersonRecord")
        end = js.index('["clientSearch","peopleSearch"]')
        person_record = js[start:end]

        self.assertNotIn("Finalize", person_record)
        self.assertNotIn("Record Payment", person_record)
        self.assertNotIn("Mark Paid", person_record)

    def test_tables_have_responsive_wrappers(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        css = Path("app/jordana_invoice/static/review.css").read_text()
        start = js.index("async function openPersonRecord")
        end = js.index('["clientSearch","peopleSearch"]')
        person_record = js[start:end]

        self.assertIn("table-scroll-wrap", person_record)
        self.assertIn(".table-scroll-wrap", css)
        self.assertIn("overflow-x: auto", css)

    def test_summary_cards_responsive_css(self):
        css = Path("app/jordana_invoice/static/review.css").read_text()

        self.assertIn(".summary-cards", css)
        self.assertIn(".summary-card", css)
        self.assertIn("grid-template-columns: repeat(4, 1fr)", css)
        self.assertIn("grid-template-columns: repeat(2, 1fr)", css)
        self.assertIn("grid-template-columns: 1fr", css)

    def test_billing_relationships_view_layout_unchanged(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()

        clients_start = html.index('id="clientsView"')
        clients_end = html.index('</section>', clients_start) + len('</section>')
        clients_html = html[clients_start:clients_end]
        self.assertIn("crm-layout", clients_html)
        self.assertIn('id="accountRecord"', clients_html)
        self.assertIn("record-pane", clients_html)

    def test_people_list_row_click_uses_hash_navigation(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn('location.hash = "people/" + row.dataset.person', js)

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

    def test_rate_card_requires_explicit_scope_resolution_and_shows_preview(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn("Rate changes affect future and unapproved sessions only. Approved sessions and finalized invoices remain unchanged.", html)
        self.assertIn(">Everyone</option>", html)
        self.assertIn(">One Client</option>", html)
        self.assertIn(">Clients Together</option>", html)
        self.assertIn(">Billing Relationship</option>", html)
        self.assertIn('id="rateRulePreview"', html)
        self.assertIn("Select one resolved client for a One Client rule.", js)
        self.assertIn("Select at least two resolved clients for a Clients Together rule.", js)
        self.assertIn("Select one resolved billing relationship for this rule.", js)
        self.assertIn("This will", js)

    def test_rate_card_supports_custom_duration_and_custom_description(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn('value="custom">Custom</option>', html)
        self.assertIn('id="rateCustomDurationMinutes"', html)
        self.assertIn('id="rateCustomDescription"', html)
        self.assertIn('id="rateCustomCode"', html)
        self.assertIn('id="rateAppointmentStatus"', html)
        self.assertIn("Custom session type requires a description.", js)
        self.assertIn("Custom duration requires actual minutes.", js)

    def test_rate_card_groups_rules_and_supports_replace_and_end_actions(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn("Standard Rates", html)
        self.assertIn("Client, joint-client, and billing-relationship Exceptions", html)
        self.assertIn("Collapsed Ended Rates", html)
        self.assertIn("<th>Status</th>", html)
        self.assertIn("Replacing", js)
        self.assertIn("Saving will end the old rule on the day before the new effective date.", js)
        self.assertIn("End this rule on which date?", js)

    def test_review_queue_uses_rate_preview_endpoint_for_session_changes(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn('await api("/api/rate-rules/preview"', js)
        self.assertIn('id="sessionRatePreview"', js)
        self.assertIn("Suggested by Rate Card:", js)

    def test_settings_screen_uses_existing_business_profile_api_and_defaults(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn('id="settingsNav">Settings</a>', html)
        self.assertIn('id="settingsView"', html)
        self.assertIn('id="businessProfileForm"', html)
        self.assertIn('name="business_name"', html)
        self.assertIn('name="provider_display_name"', html)
        self.assertIn('name="credentials_display"', html)
        self.assertIn('name="payee_name"', html)
        self.assertIn('name="logo_contains_business_details"', html)
        self.assertIn('name="show_email_below_logo"', html)
        self.assertIn('name="invoice_total_label"', html)
        self.assertIn('name="invoice_number_format"', html)
        self.assertIn("future finalized invoices only", html)
        self.assertIn('location.hash = "settings";', js)
        self.assertIn('await api("/api/business-profile"', js)
        self.assertIn('await api("/api/business-profile", { method: "POST"', js)
        self.assertIn('invoice_total_label: "TOTAL DUE"', js)
        self.assertIn('invoice_number_format: "YYYY-NNNN"', js)
        self.assertIn('Missing for invoice readiness: ${missing.join(", ")}.', js)
        self.assertIn('if (state.settingsSaving) return;', js)
        self.assertIn('if (location.hash === "#settings") showSettings();', js)

    def test_calendar_import_screen_uses_sync_status_and_run_api(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn('id="calendarImportNav">Calendar Import</a>', html)
        self.assertIn('id="calendarImportView"', html)
        self.assertIn('id="syncNowBtn"', html)
        self.assertIn('id="syncRunMessage"', html)
        self.assertIn("does not trigger the Shortcut", html)
        self.assertIn("does not edit Apple Calendar", html)
        self.assertIn('location.hash = "calendar-import";', js)
        self.assertIn('await api("/api/sync/status")', js)
        self.assertIn('await api("/api/sync/run", { method: "POST"', js)
        self.assertIn('if (state.syncRunning) return;', js)
        self.assertIn('await refreshDashboardStatus();', js)
        self.assertIn('if (location.hash === "#calendar-import") showCalendarImport();', js)

    def test_sessions_screen_is_read_only_and_uses_shared_ledger_api(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn('id="sessionsNav">Sessions</a>', html)
        self.assertIn('id="sessionsView"', html)
        self.assertIn('id="sessionsDateFilter"', html)
        self.assertIn('Rolling 30 days', html)
        self.assertIn('Previous month', html)
        self.assertIn('id="sessionsReviewStatusFilter"', html)
        self.assertIn('id="sessionsPaymentStatusFilter"', html)
        self.assertIn('id="sessionsRows"', html)
        self.assertIn('id="sessionsPrevPage"', html)
        self.assertIn('id="sessionsNextPage"', html)
        self.assertIn('location.hash = "sessions";', js)
        self.assertIn('await api(`/api/sessions?${params}`)', js)
        self.assertIn('state.sessions.offset = 0;', js)
        self.assertIn('sessions: { items: [], offset: 0, limit: 30, total: 0 }', js)
        self.assertIn('Read-only appointment ledger', js)
        self.assertNotIn('saveSessions', js)

    def test_send_to_review_button_for_candidate_only_records(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn('id="sendToReviewBtn"', js)
        self.assertIn("Send to Review", js)
        self.assertIn("sendToReview", js)
        self.assertIn("/send-to-review", js)
        self.assertIn("!isSession", js)

    def test_sessions_table_has_send_to_review_for_unclassified_candidate_only_rows(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn("send-session-to-review-btn", js)
        self.assertIn("sendSessionRowToReview", js)
        self.assertIn('!row.session_id && row.review_status === "needs_classification"', js)
        self.assertIn("/send-to-review", js)

    def test_reports_nav_has_id_and_href(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()
        self.assertIn('id="reportsNav"', html)
        self.assertIn('href="/reports"', html)

    def test_reports_view_exists_and_is_hidden_initially(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()
        self.assertIn('id="reportsView"', html)
        self.assertIn('id="reportsView" hidden', html)

    def test_hide_views_includes_reports(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        self.assertIn('"reportsView"', js)
        self.assertIn('"reportsNav"', js)

    def test_reports_nav_handler_exists(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        self.assertIn('document.getElementById("reportsNav").onclick', js)
        self.assertIn("showReports()", js)

    def test_show_reports_fetches_api_reports(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        self.assertIn("async function showReports()", js)
        self.assertIn('api("/api/reports")', js)

    def test_report_cards_generated_from_api_metadata(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()
        js = Path("app/jordana_invoice/static/review.js").read_text()
        self.assertIn("report-card-grid", html)
        self.assertIn("report-card", js)
        self.assertIn("data.reports", js)
        self.assertIn("r.display_name", js)
        self.assertIn("r.description", js)
        self.assertIn("Download CSV", js)

    def test_year_selector_uses_default_year(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        self.assertIn("reportsYearSelect", js)
        self.assertIn("data.default_year", js)
        self.assertIn("defaultYear", js)

    def test_download_url_includes_encoded_type_and_year(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        self.assertIn("encodeURIComponent(r.type)", js)
        self.assertIn("encodeURIComponent(yearSelect.value)", js)
        self.assertIn("/api/reports/download?type=", js)

    def test_privacy_note_exists(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()
        self.assertIn("reports-privacy-note", html)
        self.assertIn("Store downloaded files securely", html)

    def test_reports_view_has_no_table_or_filter_controls(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()
        start = html.index('id="reportsView"')
        end = html.index("</section>", start) + len("</section>")
        reports_html = html[start:end]
        self.assertNotIn("<table", reports_html)
        self.assertNotIn("filter", reports_html.lower())
        self.assertNotIn("preview", reports_html.lower())

    def test_reports_error_handling_does_not_use_alert(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("async function loadReports()")
        end = js.index("function renderSyncStatus")
        reports_js = js[start:end]
        self.assertIn("reportsError", reports_js)
        self.assertNotIn("alert(", reports_js)

    def test_reports_direct_navigation_supports_pathname(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        self.assertIn('location.pathname === "/reports"', js)

    def test_reports_page_title_and_document_title(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        self.assertIn('$("pageTitle").textContent = "Reports"', js)
        self.assertIn('document.title = "Jordana Billing - Reports"', js)


if __name__ == "__main__":
    unittest.main()
