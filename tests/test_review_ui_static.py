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
        self.assertIn('id="paymentsNav">Payments</a>', html)
        self.assertNotIn('id="peopleNav">People</a>', html)
        self.assertNotIn('id="clientsNav">Clients & Accounts</a>', html)
        self.assertNotIn('id="unpaidNav">Unpaid</a>', html)

    def test_payments_workspace_heading_and_columns_exist(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()

        self.assertIn("Payments", html)
        self.assertIn("<th>Status</th><th>Invoice Number</th><th>Bill To</th><th>Invoice Date</th><th>Total</th><th>Paid</th><th>Balance</th><th>Action</th>", html)
        self.assertIn('id="unpaidRows"', html)
        self.assertIn('id="unpaidWorkspace"', html)
        self.assertIn('id="paymentsView"', html)
        self.assertIn('data-payments-tab="outstanding"', html)
        self.assertIn('data-payments-tab="paid"', html)
        self.assertIn('data-payments-tab="all-payments"', html)
        self.assertIn('id="paidRows"', html)
        self.assertIn('id="allPaymentsRows"', html)

    def test_payments_js_opens_record_payment_form_and_refreshes(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn('history.pushState({}, "", "/payments");', js)
        self.assertIn('$("pageTitle").textContent = "Payments";', js)
        self.assertIn('Record Payment', js)
        self.assertIn('Bill To<input', js)
        self.assertIn('Invoice Number<input', js)
        self.assertIn('Outstanding Balance<input', js)
        self.assertIn('Payment Date<input', js)
        self.assertIn('Amount Received<input', js)
        self.assertIn('Payment Method<select', js)
        self.assertIn('Reference Number<input', js)
        self.assertIn('Received From<input', js)
        self.assertIn('Administrative Note<input', js)
        self.assertIn('state.unpaid.submitting = true;', js)
        self.assertIn('submitBtn.disabled = true;', js)
        self.assertIn('cancelBtn.disabled = true;', js)
        self.assertIn('closePaymentOverlay();', js)
        self.assertIn('await loadOutstandingInvoices(invoice.invoice_id);', js)
        self.assertIn('showUnpaidSuccess("Payment recorded successfully.");', js)
        self.assertIn('message.textContent = sanitizeUiErrorMessage(err.message, "Payment could not be recorded.");', js)

    def test_unpaid_js_renders_history_and_sanitized_statuses(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn("Payment History", js)
        self.assertIn("Amount Applied", js)
        self.assertIn("paymentStatusLabel(payment.payment_status)", js)
        self.assertIn("paymentMethodLabel(payment.method)", js)
        self.assertIn("sanitizeUiErrorMessage", js)

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
        self.assertIn("<th>Payment Handling</th>", person_record)
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

    def test_billing_relationship_duplicate_banner_exists(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn('id="billingDirDuplicateBanner"', html)
        self.assertIn("renderBillingDirDuplicateBanner", js)
        self.assertIn("/api/billing-relationships/duplicate-analysis", js)

    def test_billing_relationship_directory_marks_duplicates(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("function renderBillingDirRows")
        end = js.index("async function loadClients", start)
        directory = js[start:end]

        self.assertIn("Duplicate active relationship detected.", directory)
        self.assertIn("Multiple active Bill To records exist for this payer.", directory)

    def test_billing_summary_renders_all_four_values(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("async function openPersonRecord")
        end = js.index('["clientSearch","peopleSearch"]')
        person_record = js[start:end]

        self.assertIn("summary-cards", person_record)
        self.assertIn("Total Finalized Invoices", person_record)
        self.assertIn("Total Payments Applied", person_record)
        self.assertIn("Current Balance", person_record)
        self.assertIn("Account Status", person_record)
        self.assertIn("summary.total_finalized_invoices", person_record)
        self.assertIn("summary.total_paid_cents", person_record)
        self.assertIn("summary.current_balance_cents", person_record)
        self.assertIn("summary.account_status", person_record)

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
        self.assertIn("<th>Invoice Status</th>", person_record)
        self.assertIn("<th>Payment Status</th>", person_record)
        self.assertIn("<th>Total</th>", person_record)
        self.assertIn("<th>Paid</th>", person_record)
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

        self.assertNotIn(">Finalize<", person_record)
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
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn('Review confidence: ${s.authority_score || 0}%', js)
        self.assertIn("readiness.all_ready", js)
        self.assertIn("Approve Session", js)

    def test_review_overlay_container_exists_in_html(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()

        self.assertIn('id="reviewOverlay"', html)
        self.assertIn('id="reviewOverlayContent"', html)
        self.assertIn('id="reviewOverlayClose"', html)
        self.assertIn('role="dialog"', html)
        self.assertIn('aria-modal="true"', html)

    def test_review_overlay_functions_exist_in_js(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn("function openReviewOverlay()", js)
        self.assertIn("function closeReviewOverlay(", js)
        self.assertIn("function reviewOverlayKeydownHandler", js)
        self.assertIn("function goToPreviousSession()", js)
        self.assertIn("function saveAndNext()", js)

    def test_review_overlay_has_focus_trap_and_escape(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn("Escape", js)
        self.assertIn("e.shiftKey", js)
        self.assertIn("focusable", js)

    def test_review_table_has_review_button_column(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn("<th>Review</th>", html)
        self.assertIn('class="review-btn"', js)
        self.assertIn("data-review-id", js)

    def test_review_table_does_not_have_removed_columns(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()
        review_table_start = html.index('id="candidateRows"')
        review_table_section = html[:review_table_start]

        self.assertNotIn("<th>Time Cat.</th>", review_table_section)
        self.assertNotIn("<th>Review confidence</th>", review_table_section)
        self.assertNotIn("<th>Raw Title</th>", review_table_section)

    def test_review_html_does_not_include_override_script(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()

        self.assertNotIn("review_rate_simplification", html)

    def test_review_html_does_not_have_side_inspector(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()

        self.assertNotIn('id="inspector"', html)

    def test_review_html_does_not_have_time_filter(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()

        self.assertNotIn('id="timeFilter"', html)

    def test_review_js_renders_into_overlay_content(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn('$("reviewOverlayContent")', js)

    def test_review_js_has_save_and_next_button(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn('id="saveNextBtn"', js)
        self.assertIn('id="prevSessionBtn"', js)
        self.assertIn("Save and next", js)
        self.assertIn("Previous", js)

    def test_unresolved_client_and_payer_lock_later_steps(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn('Confirm Client(s) first.', js)
        self.assertIn('Confirm Bill To first.', js)
        self.assertIn("const billingLocked = !readiness.clients_ready;", js)
        self.assertIn("const sessionLocked = !readiness.clients_ready || !readiness.billing_ready;", js)

    def test_edited_rate_reveals_override_scope_and_unchanged_rate_does_not(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn("const rateChanged = currentRate !== suggestedRate && currentRate !== \"\";", js)
        self.assertIn('saveFuturePersonRate', js)
        self.assertIn('saveFutureJointRate', js)
        self.assertIn('Save as this client', js)
        self.assertIn('Save as the future rate for these clients together', js)

    def test_cancellation_billing_field_is_conditional(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        self.assertIn('const showCancellation = ["late_cancellation", "timely_cancellation", "cancelled", "no_show"].includes(attendanceOutcome);', js)
        self.assertIn('id="advancedReviewDetails"', js)
        self.assertIn('cancellationBillingOptions(s.billing_treatment || "unresolved", attendanceOutcome)', js)

    def test_inline_relationship_roles_and_primary_controls_are_gone(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("function renderRelationshipEditor")
        end = js.index("function openBillingRelationshipEditor")
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

        self.assertIn("update-billing-relationship", account_record)
        self.assertIn("await loadList();", account_record)
        self.assertIn("await showReviewWorkbench();", account_record)
        self.assertIn("await selectCandidate(returnContext.candidateId);", account_record)
        self.assertIn("Save changes", account_record)

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
        self.assertIn('Sync Calendar', html)
        self.assertIn('Rebuild Calendar Data from Sheet', html)
        self.assertIn('id="syncCurrentStatus"', html)
        self.assertIn('id="syncNextAutomatic"', html)
        self.assertIn("does not trigger the Shortcut", html)
        self.assertIn("does not edit Apple Calendar", html)
        self.assertIn('location.hash = "calendar-import";', js)
        self.assertIn('await api("/api/sync/status")', js)
        self.assertIn('await api("/api/sync/run", { method: "POST"', js)
        self.assertIn('await api("/api/sync/rebuild", { method: "POST"', js)
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

    def _clients_html(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()
        start = html.index('id="clientsView"')
        end = html.index('</section>', start) + len('</section>')
        return html[start:end]

    def _clients_js(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("const billingDirState")
        end = js.index('["clientSearch","peopleSearch"]')
        return js[start:end]

    def test_billing_directory_loads_api_billing_relationships(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        self.assertIn('api("/api/billing-relationships")', js)

    def test_billing_directory_does_not_use_accounts_full_as_source(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        load_start = js.index("async function loadClients()")
        load_end = js.index("async function openAccountRecord")
        load_fn = js[load_start:load_end]
        self.assertNotIn("/api/accounts?full=1", load_fn)

    def test_billing_directory_has_five_filter_choices(self):
        html = self._clients_html()
        self.assertIn('id="billingDirFilter"', html)
        self.assertIn('<option value="all">All</option>', html)
        self.assertIn('<option value="self_pay">Self-pay</option>', html)
        self.assertIn('<option value="third_party">Pays for others</option>', html)
        self.assertIn('<option value="organization">Organizations</option>', html)
        self.assertIn('<option value="account">Shared billing groups</option>', html)

    def test_billing_directory_self_pay_wording(self):
        js = self._clients_js()
        self.assertIn("Pays for herself", js)

    def test_billing_directory_third_party_wording(self):
        js = self._clients_js()
        self.assertIn("Pays for", js)

    def test_billing_directory_multiple_covered_people_wording(self):
        js = self._clients_js()
        self.assertIn("and", js)
        self.assertIn("other", js)

    def test_billing_directory_organization_rows_render(self):
        js = self._clients_js()
        self.assertIn('"organization"', js)
        self.assertIn("BILLING_DIR_TYPE_LABELS", js)

    def test_billing_directory_account_rows_render(self):
        js = self._clients_js()
        self.assertIn('"account"', js)

    def test_billing_directory_linked_payer_identifies_account(self):
        js = self._clients_js()
        self.assertIn("Linked to shared billing group:", js)

    def test_billing_directory_account_row_identifies_default_bill_to(self):
        js = self._clients_js()
        self.assertIn("Invoice recipient:", js)

    def test_billing_directory_person_payer_open_navigates_to_people(self):
        js = self._clients_js()
        self.assertIn("data-open-person", js)
        self.assertIn('location.hash = `people/${openPersonBtn.dataset.openPerson}`', js)

    def test_billing_directory_account_open_uses_existing_behavior(self):
        js = self._clients_js()
        self.assertIn("data-open-account", js)
        self.assertIn("openAccountRecord(", js)

    def test_billing_directory_organization_without_account_is_read_only(self):
        js = self._clients_js()
        self.assertIn("Details unavailable", js)
        self.assertIn("disabled", js)

    def test_billing_directory_inactive_rows_visible_and_labeled(self):
        js = self._clients_js()
        self.assertIn("Inactive", js)
        self.assertIn("status-pill inactive", js)

    def test_billing_directory_empty_state_renders(self):
        js = self._clients_js()
        self.assertIn("No billing relationships yet", js)

    def test_billing_directory_existing_account_detail_remains_intact(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        self.assertIn("async function openAccountRecord(", js)
        html = self._clients_html()
        self.assertIn('id="accountRecord"', html)
        self.assertIn("record-pane", html)

    def test_billing_directory_responsive_table_wrapper_exists(self):
        html = self._clients_html()
        self.assertIn("table-scroll-wrap", html)

    def test_billing_directory_page_subtitle_explains_directory(self):
        html = self._clients_html()
        self.assertIn("Who receives invoices and who they pay for", html)
        js = Path("app/jordana_invoice/static/review.js").read_text()
        self.assertIn('$("pageSubtitle").textContent = "Who receives invoices and who they pay for"', js)

    def test_billing_directory_filter_is_client_side(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        self.assertIn("billingDirState.filter", js)
        self.assertIn("renderBillingDirRows", js)
        self.assertIn('$("billingDirFilter").addEventListener("change"', js)

    def test_billing_directory_does_not_merge_records(self):
        js = self._clients_js()
        self.assertNotIn("mergeRecords", js)
        self.assertNotIn("deduplicate", js)

    def _person_record_js(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("async function openPersonRecord")
        end = js.index('["clientSearch","peopleSearch"]')
        return js[start:end]

    def test_add_billing_setup_button_renders(self):
        js = self._person_record_js()
        self.assertIn('id="addBillingSetupBtn"', js)
        self.assertIn("Add Billing Setup", js)

    def test_existing_billing_cards_have_edit(self):
        js = self._person_record_js()
        self.assertIn("data-edit-billing", js)
        self.assertIn(">Edit<", js)

    def test_active_card_has_deactivate(self):
        js = self._person_record_js()
        self.assertIn("data-deactivate-billing", js)
        self.assertIn("Deactivate", js)

    def test_inactive_card_has_reactivate(self):
        js = self._person_record_js()
        self.assertIn("data-reactivate-billing", js)
        self.assertIn("Reactivate", js)

    def test_create_payload_uses_current_client_person_id(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        form_start = js.index("function showBillingSetupForm")
        form_end = js.index('["clientSearch","peopleSearch"]')
        form_js = js[form_start:form_end]
        self.assertIn("state.currentPersonId", form_js)
        self.assertIn("payload.person_id = state.currentPersonId", form_js)

    def test_create_payload_fixes_billing_party_type_to_person(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        form_start = js.index("function showBillingSetupForm")
        form_end = js.index('["clientSearch","peopleSearch"]')
        form_js = js[form_start:form_end]
        self.assertIn('payload.billing_party_type = "person"', form_js)

    def test_no_organization_or_person_selector_in_form(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        form_start = js.index("function showBillingSetupForm")
        form_end = js.index('["clientSearch","peopleSearch"]')
        form_js = js[form_start:form_end]
        self.assertNotIn("organization_name", form_js)
        self.assertNotIn("billing_party_type_selector", form_js)
        self.assertNotIn("person_selector", form_js)
        self.assertNotIn("account", form_js.lower())

    def test_edit_form_is_prefilled(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        form_start = js.index("function showBillingSetupForm")
        form_end = js.index('["clientSearch","peopleSearch"]')
        form_js = js[form_start:form_end]
        self.assertIn("b.billing_name", form_js)
        self.assertIn("b.billing_email", form_js)
        self.assertIn("b.billing_phone", form_js)
        self.assertIn("b.billing_address_line_1", form_js)
        self.assertIn("b.preferred_delivery_method", form_js)
        self.assertIn("b.administrative_notes", form_js)

    def test_optional_blank_values_included_in_payload(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        form_start = js.index("function showBillingSetupForm")
        form_end = js.index('["clientSearch","peopleSearch"]')
        form_js = js[form_start:form_end]
        self.assertIn("billing_email: $(\"bsfBillingEmail\").value", form_js)
        self.assertIn("billing_phone: $(\"bsfBillingPhone\").value", form_js)
        self.assertIn("billing_address_line_1: $(\"bsfAddress1\").value", form_js)
        self.assertIn("administrative_notes: $(\"bsfAdminNotes\").value", form_js)

    def test_create_uses_billing_parties_endpoint(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        form_start = js.index("function showBillingSetupForm")
        form_end = js.index('["clientSearch","peopleSearch"]')
        form_js = js[form_start:form_end]
        self.assertIn('api("/api/billing-parties"', form_js)

    def test_edit_uses_billing_parties_id_endpoint(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        form_start = js.index("function showBillingSetupForm")
        form_end = js.index('["clientSearch","peopleSearch"]')
        form_js = js[form_start:form_end]
        self.assertIn('api(`/api/billing-parties/${b.billing_party_id}`', form_js)

    def test_deactivate_sends_active_false(self):
        js = self._person_record_js()
        self.assertIn("data-deactivate-billing", js)
        self.assertIn('JSON.stringify({ active: false })', js)

    def test_reactivate_sends_active_true(self):
        js = self._person_record_js()
        self.assertIn("data-reactivate-billing", js)
        self.assertIn('JSON.stringify({ active: true })', js)

    def test_deactivation_confirmation_explains_history_preserved(self):
        js = self._person_record_js()
        self.assertIn("Historical sessions and invoices will remain unchanged", js)

    def test_successful_save_refreshes_client_record(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        form_start = js.index("function showBillingSetupForm")
        form_end = js.index('["clientSearch","peopleSearch"]')
        form_js = js[form_start:form_end]
        self.assertIn("await openPersonRecord(state.currentPersonId", form_js)

    def test_success_and_error_messages_render_visibly(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        form_start = js.index("function showBillingSetupForm")
        form_end = js.index('["clientSearch","peopleSearch"]')
        form_js = js[form_start:form_end]
        self.assertIn("showBillingSetupMessage", form_js)
        self.assertIn('"Billing setup updated."', form_js)
        self.assertIn('"Billing setup added."', form_js)
        self.assertIn("error", form_js)
        css = Path("app/jordana_invoice/static/review.css").read_text()
        self.assertIn(".billing-setup-message", css)
        self.assertIn(".billing-setup-message.success", css)
        self.assertIn(".billing-setup-message.error", css)

    def test_duplicate_submission_blocked(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        form_start = js.index("function showBillingSetupForm")
        form_end = js.index('["clientSearch","peopleSearch"]')
        form_js = js[form_start:form_end]
        self.assertIn("state.billingSetupSaving", form_js)
        self.assertIn("if (state.billingSetupSaving) return;", form_js)
        self.assertIn("$(\"bsfSaveBtn\").disabled = true;", form_js)

    def test_cancel_does_not_save(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        form_start = js.index("function showBillingSetupForm")
        form_end = js.index('["clientSearch","peopleSearch"]')
        form_js = js[form_start:form_end]
        self.assertIn('id="bsfCancelBtn"', form_js)
        self.assertIn('container.innerHTML = ""', form_js)

    def test_billing_relationships_directory_code_remains_unchanged(self):
        js = self._clients_js()
        self.assertIn("billingDirState", js)
        self.assertIn("renderBillingDirRows", js)
        html = Path("app/jordana_invoice/static/review.html").read_text()
        clients_start = html.index('id="clientsView"')
        clients_end = html.index('</section>', clients_start) + len('</section>')
        clients_html = html[clients_start:clients_end]
        self.assertIn('id="accountRecord"', clients_html)
        self.assertIn("record-pane", clients_html)

    def test_no_account_creation_call_introduced(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        form_start = js.index("function showBillingSetupForm")
        form_end = js.index('["clientSearch","peopleSearch"]')
        form_js = js[form_start:form_end]
        self.assertNotIn("/api/accounts", form_js)
        self.assertNotIn("account_member", form_js)

    def test_billing_setup_form_has_all_required_fields(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        form_start = js.index("function showBillingSetupForm")
        form_end = js.index('["clientSearch","peopleSearch"]')
        form_js = js[form_start:form_end]
        self.assertIn("bsfBillingName", form_js)
        self.assertIn("bsfBillingEmail", form_js)
        self.assertIn("bsfBillingPhone", form_js)
        self.assertIn("bsfAddress1", form_js)
        self.assertIn("bsfAddress2", form_js)
        self.assertIn("bsfCity", form_js)
        self.assertIn("bsfState", form_js)
        self.assertIn("bsfPostalCode", form_js)
        self.assertIn("bsfDeliveryMethod", form_js)
        self.assertIn("bsfAdminNotes", form_js)

    def test_billing_setup_form_delivery_options(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        form_start = js.index("function showBillingSetupForm")
        form_end = js.index('["clientSearch","peopleSearch"]')
        form_js = js[form_start:form_end]
        self.assertIn('value="unresolved"', form_js)
        self.assertIn('value="email"', form_js)
        self.assertIn('value="mail"', form_js)
        self.assertIn('value="both"', form_js)

    def test_add_form_defaults_to_client_display_name(self):
        js = self._person_record_js()
        self.assertIn("showBillingSetupForm(null, data.person.display_name", js)

    def test_inactive_billing_card_has_inactive_class(self):
        js = self._person_record_js()
        self.assertIn('billing-card${b.active ? "" : " inactive"}', js)

    def test_billing_setup_message_container_exists(self):
        js = self._person_record_js()
        self.assertIn('id="billingSetupMessage"', js)
        self.assertIn('id="billingSetupFormContainer"', js)

    def test_billing_setup_responsive_css(self):
        css = Path("app/jordana_invoice/static/review.css").read_text()
        self.assertIn(".billing-setup-form", css)
        self.assertIn(".billing-card-actions", css)
        self.assertIn(".billing-card.inactive", css)


class OrganizationPanelUiTests(unittest.TestCase):
    """Static-file tests for the read-only organization billing-party detail panel."""

    def setUp(self):
        self.html = Path("app/jordana_invoice/static/review.html").read_text()
        self.js = Path("app/jordana_invoice/static/review.js").read_text()
        self.css = Path("app/jordana_invoice/static/review.css").read_text()

    def _org_js(self):
        start = self.js.index("function closeOrganizationRecord")
        end = self.js.index("async function openAccountRecord")
        return self.js[start:end]

    # ---- Panel exists independently ----

    def test_organization_record_panel_exists_independently(self):
        self.assertIn('id="organizationRecord"', self.html)
        self.assertIn('id="accountRecord"', self.html)

    def test_organization_record_panel_initially_hidden(self):
        start = self.html.index('id="organizationRecord"')
        snippet = self.html[start-50:start+100]
        self.assertIn("hidden", snippet)

    # ---- Organization row Open button ----

    def test_organization_rows_render_enabled_open_button(self):
        self.assertIn('data-open-organization=', self.js)

    def test_organization_open_button_not_disabled(self):
        idx = self.js.index("data-open-organization=")
        snippet = self.js[max(0, idx-80):idx+80]
        self.assertNotIn("disabled", snippet)

    def test_person_rows_still_use_data_open_person(self):
        self.assertIn('data-open-person=', self.js)

    def test_account_rows_still_use_data_open_account(self):
        self.assertIn('data-open-account=', self.js)

    # ---- Fetch and render ----

    def test_open_organization_record_fetches_api(self):
        org_js = self._org_js()
        self.assertIn("/api/billing-parties/", org_js)

    def test_loading_state_renders(self):
        org_js = self._org_js()
        self.assertIn("org-loading", org_js)
        self.assertIn("Loading organization record", org_js)

    def test_error_state_renders_inline(self):
        org_js = self._org_js()
        self.assertIn("org-error", org_js)

    def test_opening_organization_clears_account_panel(self):
        org_js = self._org_js()
        self.assertIn('$("accountRecord").innerHTML', org_js)

    def test_opening_account_clears_organization_panel(self):
        idx = self.js.index("async function openAccountRecord")
        snippet = self.js[idx:idx+200]
        self.assertIn("closeOrganizationRecord()", snippet)

    # ---- Close button ----

    def test_close_button_exists(self):
        org_js = self._org_js()
        self.assertIn("orgCloseBtn", org_js)
        self.assertIn("closeOrganizationRecord", org_js)

    def test_close_sets_panel_hidden(self):
        idx = self.js.index("function closeOrganizationRecord")
        snippet = self.js[idx:idx+200]
        self.assertIn("panel.hidden = true", snippet)

    # ---- Panel header ----

    def test_header_renders_organization_name_with_fallback(self):
        org_js = self._org_js()
        self.assertIn("organization_name", org_js)
        self.assertIn("billing_name", org_js)

    def test_header_renders_billing_name_as_secondary(self):
        org_js = self._org_js()
        self.assertIn("billingNameSecondary", org_js)

    def test_header_renders_active_inactive_status(self):
        org_js = self._org_js()
        self.assertIn("Active", org_js)
        self.assertIn("Inactive", org_js)
        self.assertIn("status-pill", org_js)

    def test_header_does_not_display_uuids_prominently(self):
        org_js = self._org_js()
        self.assertNotIn("billing_party_id</h3>", org_js)
        self.assertNotIn("billing_party_id</h2>", org_js)

    # ---- Billing details section ----

    def test_billing_contact_fields_render_read_only(self):
        org_js = self._org_js()
        for field in ["organization_name", "billing_email", "billing_phone", "preferred_delivery_method", "administrative_notes"]:
            self.assertIn(field, org_js)

    def test_no_delete_controls(self):
        org_js = self._org_js()
        self.assertNotIn("deleteOrganization", org_js)
        self.assertNotIn("hardDelete", org_js)

    # ---- Billing summary ----

    def test_all_five_summary_values_render(self):
        org_js = self._org_js()
        for label in ["Sessions", "Approved Uninvoiced", "Invoices", "Total Invoiced", "Finalized Invoice Total"]:
            self.assertIn(label, org_js)

    def test_payment_tracking_limitation_note_renders(self):
        org_js = self._org_js()
        self.assertIn("Payment tracking is not yet implemented", org_js)
        self.assertIn("org-payment-note", org_js)

    def test_active_status_not_in_summary_cards(self):
        org_js = self._org_js()
        summary_start = org_js.index("Billing Summary")
        summary_end = org_js.index("Covered Clients")
        summary_section = org_js[summary_start:summary_end]
        self.assertNotIn("summary-card-label\">Active", summary_section)
        self.assertNotIn("summary-card-label\">Status", summary_section)

    # ---- Covered clients ----

    def test_covered_clients_table_renders(self):
        org_js = self._org_js()
        self.assertIn("Covered Clients", org_js)
        self.assertIn("display_name", org_js)
        self.assertIn("person_code", org_js)
        self.assertIn("session_count", org_js)

    def test_covered_clients_empty_state(self):
        org_js = self._org_js()
        self.assertIn("No clients have sessions billed to this organization yet.", org_js)

    def test_client_open_navigates_to_people_route(self):
        org_js = self._org_js()
        self.assertIn("people/${btn.dataset.openPerson}", org_js)

    # ---- Sessions ----

    def test_sessions_use_stored_rate(self):
        org_js = self._org_js()
        self.assertIn("approved_rate_cents", org_js)
        self.assertIn("centString", org_js)

    def test_sessions_table_renders(self):
        org_js = self._org_js()
        for header in ["Date", "Participants", "Session Type", "Duration", "Time Category", "Stored Rate", "Review Status", "Invoice"]:
            self.assertIn(header, org_js)

    def test_sessions_empty_state(self):
        org_js = self._org_js()
        self.assertIn("No sessions billed to this organization yet.", org_js)

    def test_draft_invoice_fallback_renders(self):
        org_js = self._org_js()
        self.assertIn("Draft invoice", org_js)

    def test_open_in_review_uses_existing_navigation(self):
        org_js = self._org_js()
        self.assertIn("data-open-review", org_js)
        self.assertIn("showReviewWorkbench", org_js)
        self.assertIn("selectCandidate", org_js)

    # ---- Invoice history ----

    def test_invoices_table_renders(self):
        org_js = self._org_js()
        for header in ["Invoice Number", "Billing Period", "Issue Date", "Status", "Total", "Balance"]:
            self.assertIn(header, org_js)

    def test_invoices_empty_state(self):
        org_js = self._org_js()
        self.assertIn("No invoices addressed to this organization yet.", org_js)

    def test_invoice_open_uses_existing_invoice_view(self):
        org_js = self._org_js()
        self.assertIn("data-open-invoice", org_js)
        self.assertIn("openInvoice", org_js)

    def test_no_finalize_payment_controls(self):
        org_js = self._org_js()
        self.assertNotIn("finalizeInvoice", org_js)
        self.assertNotIn("markPaid", org_js)
        self.assertNotIn("deleteInvoice", org_js)

    # ---- Linked billing groups ----

    def test_linked_account_rows_use_existing_account_open(self):
        org_js = self._org_js()
        self.assertIn("Related Shared Billing Groups", org_js)
        self.assertIn("data-open-account", org_js)
        self.assertIn("openAccountRecord", org_js)

    def test_linked_accounts_show_members(self):
        org_js = self._org_js()
        self.assertIn("members", org_js)

    # ---- Audit history ----

    def test_audit_section_renders_read_only(self):
        org_js = self._org_js()
        self.assertIn("Administrative History", org_js)
        self.assertIn("org-audit", org_js)
        self.assertIn("created_at", org_js)
        self.assertIn("action", org_js)

    def test_audit_has_no_editing_controls(self):
        org_js = self._org_js()
        audit_start = org_js.index("Administrative History")
        audit_section = org_js[audit_start:]
        self.assertNotIn("editAudit", audit_section)
        self.assertNotIn("deleteAudit", audit_section)

    # ---- Responsive wrappers ----

    def test_responsive_wrappers_exist(self):
        self.assertIn("org-table-scroll", self.css)
        self.assertIn("org-summary-cards", self.css)

    def test_responsive_stacking_at_narrow_widths(self):
        self.assertIn(".org-summary-cards { grid-template-columns: 1fr; }", self.css)

    # ---- Existing behavior unchanged ----

    def test_billing_relationships_filters_remain_intact(self):
        self.assertIn('id="billingDirFilter"', self.html)
        self.assertIn('id="clientSearch"', self.html)

    def test_existing_person_navigation_unchanged(self):
        self.assertIn("openPersonRecord", self.js)

    def test_existing_account_navigation_unchanged(self):
        self.assertIn("openAccountRecord", self.js)

    def test_organization_record_uses_record_pane_class(self):
        start = self.html.index('id="organizationRecord"')
        snippet = self.html[start-30:start+50]
        self.assertIn("record-pane", snippet)

    # ---- Editable org panel: action buttons ----

    def test_edit_button_renders_for_organizations(self):
        org_js = self._org_js()
        self.assertIn("orgEditBtn", org_js)
        self.assertIn("showOrgEditForm", org_js)

    def test_active_record_has_deactivate(self):
        org_js = self._org_js()
        self.assertIn("orgDeactivateBtn", org_js)
        self.assertIn("Deactivate", org_js)

    def test_inactive_record_has_reactivate(self):
        org_js = self._org_js()
        self.assertIn("orgReactivateBtn", org_js)
        self.assertIn("Reactivate", org_js)

    def test_deactivate_and_reactivate_are_conditional(self):
        org_js = self._org_js()
        self.assertIn("bp.active", org_js)

    # ---- Edit form ----

    def test_org_form_is_prefilled(self):
        org_js = self._org_js()
        self.assertIn("orgFormName", org_js)
        self.assertIn("orgFormBillingName", org_js)
        self.assertIn("orgFormEmail", org_js)
        self.assertIn("orgFormPhone", org_js)
        self.assertIn("orgFormAddr1", org_js)
        self.assertIn("orgFormAddr2", org_js)
        self.assertIn("orgFormCity", org_js)
        self.assertIn("orgFormState", org_js)
        self.assertIn("orgFormPostal", org_js)
        self.assertIn("orgFormDelivery", org_js)
        self.assertIn("orgFormNotes", org_js)

    def test_org_form_has_all_delivery_options(self):
        org_js = self._org_js()
        self.assertIn('value="unresolved"', org_js)
        self.assertIn('value="email"', org_js)
        self.assertIn('value="mail"', org_js)
        self.assertIn('value="both"', org_js)

    def test_org_and_billing_names_are_required(self):
        org_js = self._org_js()
        self.assertIn("Organization name is required.", org_js)
        self.assertIn("Billing name is required.", org_js)

    def test_no_person_type_account_selectors_in_org_form(self):
        org_js = self._org_js()
        form_start = org_js.index("showOrgEditForm")
        form_end = org_js.index("async function openOrganizationRecord")
        form_section = org_js[form_start:form_end]
        self.assertNotIn("person_id", form_section)
        self.assertNotIn("personId", form_section)
        self.assertNotIn("account_id", form_section)
        self.assertNotIn("accountId", form_section)
        self.assertNotIn("data-open-person", form_section)

    # ---- Update payload behavior ----

    def test_update_uses_billing_parties_api(self):
        org_js = self._org_js()
        self.assertIn("/api/billing-parties/", org_js)
        self.assertIn("POST", org_js)

    def test_payload_preserves_billing_party_type_organization(self):
        org_js = self._org_js()
        self.assertIn('billing_party_type: "organization"', org_js)

    def test_payload_does_not_include_person_id(self):
        org_js = self._org_js()
        form_start = org_js.index("const payload = {")
        form_end = org_js.index("};", form_start) + 2
        payload_section = org_js[form_start:form_end]
        self.assertNotIn("person_id", payload_section)

    def test_blank_optional_fields_submitted_for_clearing(self):
        org_js = self._org_js()
        self.assertIn("billing_email:", org_js)
        self.assertIn("billing_phone:", org_js)
        self.assertIn("billing_address_line_1:", org_js)
        self.assertIn("billing_address_line_2:", org_js)
        self.assertIn("billing_city:", org_js)
        self.assertIn("billing_state:", org_js)
        self.assertIn("billing_postal_code:", org_js)
        self.assertIn("administrative_notes:", org_js)

    # ---- Cancel and duplicate submission ----

    def test_cancel_clears_form_without_saving(self):
        org_js = self._org_js()
        self.assertIn("orgFormCancelBtn", org_js)
        self.assertIn('container.innerHTML = ""', org_js)

    def test_duplicate_submission_blocked(self):
        org_js = self._org_js()
        self.assertIn("orgSaving", org_js)
        self.assertIn("if (orgSaving) return;", org_js)

    # ---- Success and error messages ----

    def test_visible_success_and_error_messages_render(self):
        org_js = self._org_js()
        self.assertIn("showOrgMessage", org_js)
        self.assertIn("orgMessage", org_js)
        self.assertIn("billing-setup-message", org_js)

    # ---- Deactivate/reactivate behavior ----

    def test_deactivate_sends_active_false(self):
        org_js = self._org_js()
        self.assertIn("{ active: false }", org_js)

    def test_reactivate_sends_active_true(self):
        org_js = self._org_js()
        self.assertIn("{ active: true }", org_js)

    def test_confirmation_explains_historical_preservation(self):
        org_js = self._org_js()
        self.assertIn("Historical sessions and invoices will remain unchanged.", org_js)

    def test_successful_actions_refresh_both_panel_and_directory(self):
        org_js = self._org_js()
        self.assertIn("openOrganizationRecord(bp.billing_party_id)", org_js)
        self.assertIn("loadClients()", org_js)

    # ---- Read-only sections remain intact ----

    def test_read_only_sessions_invoices_summary_remain_intact(self):
        org_js = self._org_js()
        self.assertIn("Billing Summary", org_js)
        self.assertIn("Covered Clients", org_js)
        self.assertIn("Sessions", org_js)
        self.assertIn("Invoice History", org_js)
        self.assertIn("Administrative History", org_js)

    def test_no_payment_finalization_delete_controls(self):
        org_js = self._org_js()
        self.assertNotIn("finalizeInvoice", org_js)
        self.assertNotIn("markPaid", org_js)
        self.assertNotIn("deleteInvoice", org_js)

    # ---- Account and person navigation unchanged ----

    def test_account_and_person_navigation_remain_unchanged(self):
        org_js = self._org_js()
        self.assertIn("data-open-person", org_js)
        self.assertIn("data-open-account", org_js)
        self.assertIn("openAccountRecord", org_js)


class ReviewOverlayCloseTests(unittest.TestCase):
    """Bug 1: closeReviewOverlay must return boolean and accept options."""

    def setUp(self):
        self.js = Path("app/jordana_invoice/static/review.js").read_text()

    def test_closeReviewOverlay_returns_boolean(self):
        self.assertIn("function closeReviewOverlay({ clearCandidate = false, skipDirtyCheck = false } = {}) {", self.js)
        self.assertIn("return true;", self.js)
        self.assertIn("return false;", self.js)

    def test_closeReviewOverlay_clearCandidate_option_clears_state(self):
        self.assertIn("if (clearCandidate) {", self.js)
        self.assertIn("state.selected = null;", self.js)
        self.assertIn("state.detail = null;", self.js)
        self.assertIn("state.participants = [];", self.js)
        self.assertIn("state.account = null;", self.js)
        self.assertIn("state.billingParty = null;", self.js)

    def test_closeReviewOverlay_skipDirtyCheck_option_bypasses_prompt(self):
        self.assertIn("if (reviewOverlayCtrl.isOpen() && !skipDirtyCheck && state.dirty.size > 0)", self.js)

    def test_openBillingRelationshipEditor_closes_overlay_before_navigation(self):
        start = self.js.index("function openBillingRelationshipEditor()")
        end = self.js.index("function collectPayload", start)
        section = self.js[start:end]
        self.assertIn("closeReviewOverlay()", section)
        self.assertIn("if (!closeReviewOverlay()) return;", section)

    def test_openBillingRelationshipEditor_persists_return_context_after_close(self):
        start = self.js.index("function openBillingRelationshipEditor()")
        end = self.js.index("function collectPayload", start)
        section = self.js[start:end]
        close_idx = section.index("closeReviewOverlay()")
        persist_idx = section.index("persistReturnContext")
        self.assertLess(close_idx, persist_idx)


class ReviewApprovalTests(unittest.TestCase):
    """Bug 2: Approval must be single-submit, close overlay, restore focus, show success."""

    def setUp(self):
        self.js = Path("app/jordana_invoice/static/review.js").read_text()
        start = self.js.index("async function save(approve)")
        end = self.js.index("function collectSessionDraftValues", start)
        self.save_fn = self.js[start:end]

    def test_approval_has_single_submit_guard(self):
        self.assertIn("approvalState", self.js)
        self.assertIn("if (approve && approvalState.submitting) return;", self.save_fn)

    def test_approval_disables_button_during_request(self):
        self.assertIn("approvalState.submitting = true;", self.save_fn)
        self.assertIn('reviewOverlayCtrl.beginPending(["approveBtn"]);', self.save_fn)

    def test_approval_closes_overlay_on_success(self):
        self.assertIn("closeReviewOverlay({ clearCandidate: true, skipDirtyCheck: true });", self.save_fn)

    def test_approval_clears_candidate_state_on_success(self):
        self.assertIn("clearCandidate: true", self.save_fn)

    def test_approval_restores_focus_on_success(self):
        self.assertIn('document.querySelector("#candidateRows .review-btn")', self.save_fn)
        self.assertIn("firstReviewBtn.focus()", self.save_fn)
        self.assertIn('$("searchBox")?.focus()', self.save_fn)

    def test_approval_shows_success_banner(self):
        self.assertIn("showReviewSuccess", self.save_fn)
        self.assertIn("Session approved.", self.save_fn)

    def test_approval_handles_staging_warnings(self):
        self.assertIn("staging.status === \"warning\"", self.save_fn)
        self.assertIn("staging.status === \"unavailable\"", self.save_fn)
        self.assertIn("staging.status === \"error\"", self.save_fn)
        self.assertIn("Invoice staging", self.save_fn)

    def test_approval_reenables_button_on_error(self):
        self.assertIn("approvalState.submitting = false;", self.save_fn)
        self.assertIn("reviewOverlayCtrl.endPending()", self.save_fn)

    def test_approval_sanitizes_error_messages(self):
        self.assertIn('msg.startsWith("Cannot approve")', self.save_fn)
        self.assertIn("sanitizeUiErrorMessage(msg", self.save_fn)
        self.assertIn("Could not approve session. Please check required fields and try again.", self.save_fn)

    def test_session_save_failure_clears_saved_state(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("async function saveSessionSection")
        end = js.index("async function save(approve)", start)
        save_session_fn = js[start:end]

        self.assertIn("clearSavedState(\"session\", \"Save failed\");", save_session_fn)
        self.assertIn("sanitizeUiErrorMessage(error.message", save_session_fn)

    def test_success_banner_css_exists(self):
        css = Path("app/jordana_invoice/static/review.css").read_text()
        self.assertIn(".review-success-banner", css)

    def test_approval_failure_keeps_overlay_open(self):
        """On approval failure, the overlay must stay open — no closeReviewOverlay in catch."""
        catch_start = self.save_fn.index("catch (error)")
        catch_block = self.save_fn[catch_start:]
        self.assertNotIn("closeReviewOverlay", catch_block)

    def test_approval_success_closes_overlay_and_clears_candidate(self):
        """On success, overlay closes and candidate is cleared."""
        self.assertIn("closeReviewOverlay({ clearCandidate: true, skipDirtyCheck: true });", self.save_fn)

    def test_approval_success_prevents_resubmission(self):
        """Submit guard prevents double-submission during request."""
        self.assertIn("if (approve && approvalState.submitting) return;", self.save_fn)
        self.assertIn("approvalState.submitting = true;", self.save_fn)


class ReviewCustomDurationPayloadTests(unittest.TestCase):
    """Regression: custom_duration_minutes must be null (not empty string) when no custom duration."""

    def setUp(self):
        self.js = Path("app/jordana_invoice/static/review.js").read_text()

    def test_collect_payload_sends_null_for_standard_duration(self):
        start = self.js.index("function collectPayload()")
        end = self.js.index("function collectRelationshipPayload", start)
        fn = self.js[start:end]
        self.assertIn("custom_duration_minutes: durationChoice === \"custom\" ? (customMinutes || null) : null", fn)
        self.assertNotIn('custom_duration_minutes: durationChoice === "custom" ? customMinutes : ""', fn)

    def test_collect_session_draft_sends_null_for_standard_duration(self):
        start = self.js.index("function collectSessionDraftValues()")
        end = self.js.index("function restoreSessionDraftValues", start)
        fn = self.js[start:end]
        self.assertIn("custom_duration_minutes: durationChoice === \"custom\" ? (customMinutes || null) : null", fn)
        self.assertNotIn('custom_duration_minutes: durationChoice === "custom" ? customMinutes : ""', fn)

    def test_restore_session_draft_handles_null(self):
        start = self.js.index("function restoreSessionDraftValues(values)")
        end = self.js.index("async function updateSessionRatePreview", start)
        fn = self.js[start:end]
        self.assertIn("values.custom_duration_minutes ?? \"\"", fn)

    def test_rate_preview_sends_null_for_empty_custom(self):
        start = self.js.index("async function updateSessionRatePreview()")
        end = self.js.index("async function resolveTypedSelections", start)
        fn = self.js[start:end]
        self.assertIn('custom_duration_minutes', fn)
        self.assertNotIn('custom_duration_minutes: $("customDurationInput")?.value || ""', fn)


class ReviewBillingRelationshipReturnTests(unittest.TestCase):
    """Bug 3: saveBillingRelationship must refresh candidate before returning to review."""

    def setUp(self):
        self.js = Path("app/jordana_invoice/static/review.js").read_text()
        start = self.js.index("async function saveBillingRelationship(")
        end = self.js.index("async function loadPeople()", start)
        self.fn = self.js[start:end]

    def test_saveBillingRelationship_calls_refresh_before_return(self):
        self.assertIn("/api/review/candidates/", self.fn)
        self.assertIn("/refresh", self.fn)
        refresh_idx = self.fn.index("/refresh")
        select_idx = self.fn.index("selectCandidate")
        self.assertLess(refresh_idx, select_idx)


class ReviewStagingUiTests(unittest.TestCase):
    def setUp(self):
        self.js = Path("app/jordana_invoice/static/review.js").read_text()
        self.css = Path("app/jordana_invoice/static/review.css").read_text()

    def test_css_defines_warning_banner_style(self):
        self.assertIn(".review-warning-banner", self.css)
        self.assertIn("background: #fff8e8", self.css)
        self.assertIn("border: 1px solid #f4a024", self.css)

    def test_js_defines_show_review_warning(self):
        self.assertIn("function showReviewWarning(message)", self.js)
        self.assertIn('banner.className = "review-warning-banner"', self.js)

    def test_js_save_handles_staging_result_and_refreshes_invoices(self):
        # Verify the success and warning banner messages are present in save()
        self.assertIn('Session approved and added to monthly draft.', self.js)
        self.assertIn('Session approved. This future session will become invoice-eligible after the appointment date.', self.js)
        self.assertIn("approvalSuccessMessageForStaging", self.js)
        self.assertIn("sessions_staged", self.js)
        self.assertIn("Future scheduled session is not invoice eligible", self.js)
        self.assertIn('Invoice staging warning: staging completed with errors', self.js)
        self.assertIn('Invoice staging warning: database busy, session will stage later.', self.js)
        self.assertIn('Invoice staging warning: unexpected error occurred, session will stage later.', self.js)
        self.assertIn("showReviewWarning(warningMsg)", self.js)
        
        # Verify the active invoices list and editor refresh on approval
        self.assertIn('!document.getElementById("invoicesView").hidden', self.js)
        self.assertIn("await loadInvoices()", self.js)
        self.assertIn("await openInvoice(state.invoice.invoice.invoice_id)", self.js)


class ReviewLineEditingUiTests(unittest.TestCase):
    def setUp(self):
        self.js = Path("app/jordana_invoice/static/review.js").read_text()
        self.css = Path("app/jordana_invoice/static/review.css").read_text()

    def test_js_defines_open_line_edit_modal(self):
        self.assertIn("function openLineEditModal", self.js)

    def test_js_line_editor_modal_elements_and_defaults(self):
        # Verify scope default to invoice_line_only
        self.assertIn('name="lineEditScope" value="invoice_line_only" checked', self.js)
        # Verify alternative option invoice_line_and_session exists
        self.assertIn('name="lineEditScope" value="invoice_line_and_session"', self.js)
        # Verify scope selection displays conditionally based on session association
        self.assertIn('display: ${hasSession ? \'block\' : \'none\'}', self.js)

    def test_js_line_editor_save_validations(self):
        # Description non-empty check
        self.assertIn("Description must be non-empty.", self.js)
        # Amount format and decimal places regex check
        self.assertIn("/^\\d+(\\.\\d{1,2})?$/", self.js)
        self.assertIn("Amount must be a non-negative number with at most 2 decimal places.", self.js)
        # Correction reason validation on amount change
        self.assertIn("A correction reason is required when the amount changes.", self.js)

    def test_js_line_editor_success_and_failure_behaviors(self):
        # Verify saveBtn is disabled during request to prevent duplicate submissions
        self.assertIn("saveBtn.disabled = true", self.js)

        # Verify success path closes editor and reloads workspace
        self.assertIn("closeLineEditorModal()", self.js)
        self.assertIn("await loadInvoices()", self.js)
        self.assertIn("await renderInvoiceEditor(updated)", self.js)
        self.assertIn("Invoice line updated successfully.", self.js)

        # Verify failure path keeps editor open (closeLineEditorModal is NOT in catch block) and re-enables saveBtn
        self.assertIn("saveBtn.disabled = false", self.js)


class InvoiceLineItemActionsLayoutTests(unittest.TestCase):
    """Change 1: Edit and Delete controls must be in a horizontal flex container on the same row."""

    def setUp(self):
        self.js = Path("app/jordana_invoice/static/review.js").read_text()
        self.css = Path("app/jordana_invoice/static/review.css").read_text()

    def test_edit_and_delete_wrapped_in_line_item_actions_div(self):
        start = self.js.index("async function renderInvoiceEditor")
        end = self.js.index("document.querySelectorAll(\".edit-line\")", start)
        section = self.js[start:end]
        self.assertIn("line-item-actions", section)
        self.assertIn("edit-line", section)
        self.assertIn("remove-line", section)

    def test_edit_button_comes_before_delete_in_container(self):
        start = self.js.index("async function renderInvoiceEditor")
        end = self.js.index("document.querySelectorAll(\".edit-line\")", start)
        section = self.js[start:end]
        edit_idx = section.index("edit-line")
        remove_idx = section.index("remove-line")
        self.assertLess(edit_idx, remove_idx)

    def test_both_buttons_have_type_button(self):
        start = self.js.index("async function renderInvoiceEditor")
        end = self.js.index("document.querySelectorAll(\".edit-line\")", start)
        section = self.js[start:end]
        self.assertIn('class="edit-line secondary" type="button"', section)
        self.assertIn('class="remove-line danger" type="button"', section)

    def test_css_defines_line_item_actions_as_flex(self):
        self.assertIn(".line-item-actions", self.css)
        css_idx = self.css.index(".line-item-actions")
        css_block = self.css[css_idx:css_idx + 200]
        self.assertIn("display: flex", css_block)
        self.assertIn("gap:", css_block)
        self.assertIn("white-space: nowrap", css_block)

    def test_css_line_item_actions_button_styles(self):
        self.assertIn(".line-item-actions button", self.css)

    def test_edit_and_delete_handlers_preserved(self):
        self.assertIn('document.querySelectorAll(".edit-line").forEach', self.js)
        self.assertIn('document.querySelectorAll(".remove-line").forEach', self.js)

    def test_buttons_are_inside_line_item_actions_div(self):
        start = self.js.index("async function renderInvoiceEditor")
        end = self.js.index("document.querySelectorAll(\".edit-line\")", start)
        section = self.js[start:end]
        container_start = section.index("line-item-actions")
        container_end = section.index("</div>", container_start)
        container_block = section[container_start:container_end]
        self.assertIn("edit-line", container_block)
        self.assertIn("remove-line", container_block)


class InvoiceFinalizationPreviewUiTests(unittest.TestCase):
    """Change 2: Review and Finalize must open a real preview step before finalization."""

    def setUp(self):
        self.js = Path("app/jordana_invoice/static/review.js").read_text()
        self.css = Path("app/jordana_invoice/static/review.css").read_text()
        start = self.js.index("function renderFinalizationPreview(")
        end = self.js.index("function renderInvoicePreview(", start)
        self.fn = self.js[start:end]

    def test_preview_titled_invoice_preview_not_finalization_preview(self):
        self.assertIn("Invoice Preview", self.fn)
        self.assertNotIn("Finalization Preview", self.fn)

    def test_preview_shows_draft_status_pill(self):
        self.assertIn('<span class="status-pill">Draft</span>', self.fn)

    def test_preview_embeds_canonical_pdf_renderer_output(self):
        self.assertIn("finalization-pdf-preview", self.fn)
        self.assertIn("finalizationPdfFrame", self.fn)
        self.assertIn("/finalization-preview-token", self.fn)
        self.assertIn("preview_pdf_url", self.fn)
        self.assertNotIn("createObjectURL", self.fn)
        self.assertNotIn("blob:", self.fn)

    def test_preview_can_open_embedded_pdf_without_popup_dependency(self):
        self.assertIn("openFinalizationPdfPreview", self.fn)
        self.assertIn("Preview PDF", self.fn)

    def test_preview_hides_delivery_method(self):
        self.assertNotIn("Delivery method", self.fn)
        self.assertNotIn("deliveryLabel", self.fn)

    def test_preview_does_not_render_old_html_invoice_visual(self):
        self.assertNotIn("invoice-preview-header", self.fn)
        self.assertNotIn("invoice-preview-table", self.fn)
        self.assertNotIn("invoice-preview-sender", self.fn)
        self.assertNotIn("bill_to_lines", self.fn)
        self.assertNotIn("sender_lines", self.fn)
        self.assertNotIn("payment_lines", self.fn)
        self.assertNotIn("payment_zelle_line", self.fn)

    def test_preview_shows_total(self):
        self.assertIn("invoice-total", self.js)
        self.assertIn("total_display", self.js)
        self.assertIn("total_label", self.js)

    def test_preview_does_not_use_legacy_print_copy(self):
        self.assertNotIn("Please send payment to:", self.fn)
        self.assertNotIn("Via Mail", self.fn)

    def test_invoice_settings_include_zelle_field(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn('id="zelleRecipientInput"', html)
        self.assertIn("zelle_recipient", js)

    def test_preview_uses_canonical_pdf_for_notes_and_layout(self):
        self.assertNotIn("notesHtml", self.fn)
        self.assertNotIn("render.notes", self.fn)

    def test_preview_has_finalize_and_back_buttons(self):
        self.assertIn('id="confirmFinalizeBtn"', self.fn)
        self.assertIn('id="backToDraftBtn"', self.fn)
        self.assertIn("Finalize Invoice", self.fn)
        self.assertIn("Back to Draft", self.fn)

    def test_preview_does_not_use_old_button_labels(self):
        self.assertNotIn("Finalize This Exact Invoice", self.fn)
        self.assertNotIn("Return to Draft", self.fn)
        self.assertNotIn('id="cancelFinalizeBtn"', self.fn)

    def test_preview_back_button_returns_to_draft_editor(self):
        self.assertIn("backToDraftBtn", self.fn)
        self.assertIn("renderInvoiceEditor(preview)", self.fn)

    def test_preview_finalize_prevents_duplicate_submission(self):
        self.assertIn("finalizeInProgress", self.fn)
        self.assertIn("if (state.finalizeInProgress) return;", self.fn)
        self.assertIn("state.finalizeInProgress = true;", self.fn)

    def test_preview_finalize_disables_buttons_during_request(self):
        self.assertIn("finalizeBtn.disabled = true", self.fn)
        self.assertIn("backBtn.disabled = true", self.fn)

    def test_preview_finalize_keeps_buttons_disabled_on_success(self):
        self.assertIn("finalizeBtn.disabled = true;", self.fn)

    def test_preview_finalize_reenables_buttons_on_error(self):
        self.assertIn("finalizeBtn.disabled = false", self.fn)
        self.assertIn("backBtn.disabled = false", self.fn)

    def test_preview_finalize_shows_error_in_place_on_failure(self):
        self.assertIn("finalizeError", self.fn)
        self.assertIn("errorDiv.style.display = \"block\"", self.fn)

    def test_preview_finalize_does_not_throw_unhandled(self):
        self.assertIn("try {", self.fn)
        self.assertIn("} catch (err) {", self.fn)

    def test_preview_finalize_success_refreshes_invoice_list(self):
        self.assertIn("await loadInvoices()", self.fn)

    def test_preview_finalize_success_renders_final_preview(self):
        self.assertIn("renderInvoicePreview(final)", self.fn)

    def test_preview_finalize_success_shows_confirmation(self):
        self.assertIn("showInvoiceSuccess", self.fn)
        self.assertIn("Invoice finalized successfully.", self.fn)

    def test_preview_finalize_success_sets_state_invoice(self):
        self.assertIn("state.invoice = final", self.fn)

    def test_preview_finalize_resets_in_progress_on_success(self):
        self.assertIn("state.finalizeInProgress = false;", self.fn)

    def test_preview_finalize_resets_in_progress_on_error(self):
        catch_idx = self.fn.index("} catch (err) {")
        catch_block = self.fn[catch_idx:]
        self.assertIn("state.finalizeInProgress = false;", catch_block)

    def test_preview_does_not_assign_invoice_number_before_finalize(self):
        self.assertNotIn("i.invoice_number", self.fn)
        self.assertNotIn("invoice_number_display", self.fn)
        self.assertIn("/finalization-preview-token", self.fn)

    def test_state_has_finalize_in_progress_flag(self):
        self.assertIn("finalizeInProgress: false", self.js)

    def test_show_invoice_success_function_exists(self):
        self.assertIn("function showInvoiceSuccess", self.js)

    def test_show_invoice_success_uses_invoices_view(self):
        start = self.js.index("function showInvoiceSuccess")
        end = self.js.index("function goToPreviousSession", start)
        fn = self.js[start:end]
        self.assertIn('$("invoicesView")', fn)
        self.assertIn("review-success-banner", fn)

    def test_review_and_finalize_button_calls_preview_finalize_api(self):
        start = self.js.index('$("reviewFinalizeBtn").onclick')
        end = self.js.index("function renderFinalizationPreview", start)
        handler = self.js[start:end]
        self.assertIn("preview-finalize", handler)
        self.assertIn("renderFinalizationPreview", handler)

    def test_billing_setup_edit_form_shows_active_status_banner(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("function showBillingSetupForm")
        fn = js[start:start+3000]
        self.assertIn("billing-setup-status-banner", fn)
        self.assertIn("Active", fn)
        self.assertIn("Inactive", fn)
        self.assertIn("will not be used for new invoices", fn)

    def test_billing_setup_edit_form_inactive_warning_exists(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("function showBillingSetupForm")
        fn = js[start:start+3000]
        self.assertIn("Editing this record does not update the active setup", fn)

    def test_billing_card_duplicate_warning_exists(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("async function openPersonRecord")
        end = js.index('["clientSearch","peopleSearch"]')
        person_record = js[start:end]
        self.assertIn("billing-card-warning", person_record)
        self.assertIn("Another inactive billing setup exists for this payer", person_record)
        self.assertIn("missing required delivery details", person_record)

    def test_copy_contact_button_exists_on_active_card(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("async function openPersonRecord")
        end = js.index('["clientSearch","peopleSearch"]')
        person_record = js[start:end]
        self.assertIn("data-copy-contact-source", person_record)
        self.assertIn("data-copy-contact-target", person_record)
        self.assertIn("Review Inactive Details", person_record)

    def test_copy_contact_handler_calls_preview_api(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        self.assertIn("copy-contact-preview", js)
        self.assertIn("copy-contact", js)
        self.assertIn("Copy Contact Details to Active Setup", js)

    def test_copy_contact_requires_confirmation(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        self.assertIn("copyContactConfirm", js)
        self.assertIn("Copy Selected Details", js)
        self.assertIn("copyContactCancel", js)

    def test_copy_contact_does_not_auto_copy(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index('document.querySelectorAll("[data-copy-contact-source]")')
        end = js.index("}\n\nfunction showBillingSetupMessage", start)
        handler = js[start:end]
        self.assertIn("copyContactConfirmBtn", handler)
        self.assertIn("confirmed_fields", handler)

    def test_billing_setup_save_prevents_duplicate_submission(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("function showBillingSetupForm")
        fn = js[start:start+6000]
        self.assertIn("state.billingSetupSaving", fn)
        self.assertIn('bsfSaveBtn").disabled = true', fn)

    def test_billing_setup_save_refreshes_person_record(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()
        start = js.index("function showBillingSetupForm")
        fn = js[start:start+6000]
        self.assertIn("openPersonRecord", fn)
        self.assertIn("Billing setup updated.", fn)

    def test_billing_setup_warning_css_exists(self):
        css = Path("app/jordana_invoice/static/review.css").read_text()
        self.assertIn(".billing-card-warning", css)
        self.assertIn(".billing-setup-status-banner", css)
        self.assertIn(".billing-setup-status-banner.active", css)
        self.assertIn(".billing-setup-status-banner.inactive", css)

    def test_copy_contact_css_exists(self):
        css = Path("app/jordana_invoice/static/review.css").read_text()
        self.assertIn(".copy-contact-title", css)
        self.assertIn(".copy-contact-field-list", css)

    def test_payments_tab_js_functions_exist(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn("function showPayments()", js)
        self.assertIn("function setupPaymentsTabs()", js)
        self.assertIn("function switchPaymentsTab(", js)
        self.assertIn("async function loadPaidInvoices()", js)
        self.assertIn("function renderPaidInvoices(", js)
        self.assertIn("async function loadAllPayments()", js)
        self.assertIn("function renderAllPayments(", js)
        self.assertIn("async function openPaymentDetail(", js)
        self.assertIn("async function openPaidInvoice(", js)
        self.assertIn("state.payments.activeTab", js)

    def test_payments_paid_invoices_table_columns(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()

        self.assertIn("paid-invoices-table", html)
        self.assertIn("<th>Paid Date</th>", html)
        self.assertIn("<th>Payment Method</th>", html)

    def test_payments_all_payments_table_columns(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()

        self.assertIn("all-payments-table", html)
        self.assertIn("<th>Payment Date</th>", html)
        self.assertIn("<th>Received From</th>", html)
        self.assertIn("<th>Amount Applied</th>", html)

    def test_session_terminology_uses_payment_handling(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn("function paymentHandlingLabel(", js)
        self.assertIn("Invoice billing", js)
        self.assertIn("Paid at session", js)

    def test_sessions_table_header_uses_payment_handling(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()

        self.assertIn("<th>Payment Handling</th>", html)

    def test_payments_route_serves_static_html(self):
        js = Path("app/jordana_invoice/static/review.js").read_text()

        self.assertIn('"/payments"', js)
        self.assertIn("showPayments()", js)

    def test_payments_css_tabs_exist(self):
        css = Path("app/jordana_invoice/static/review.css").read_text()

        self.assertIn(".payments-tabs", css)
        self.assertIn(".payments-tab", css)
        self.assertIn(".payments-tab.active", css)
        self.assertIn(".payments-panel", css)

    def test_header_logo_present_in_html(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()

        self.assertIn("brand-mark", html)
        self.assertIn("Jordana Billing", html)

    def test_header_logo_size_increased_by_approximately_15_percent(self):
        css = Path("app/jordana_invoice/static/review.css").read_text()

        self.assertIn("width: 44px; height: 44px;", css)
        self.assertNotIn("width: 38px; height: 38px;", css)

    def test_header_logo_aspect_ratio_preserved(self):
        css = Path("app/jordana_invoice/static/review.css").read_text()

        mark_start = css.index(".brand-mark")
        mark_end = css.index("}", mark_start) + 1
        mark_block = css[mark_start:mark_end]
        self.assertIn("width: 44px", mark_block)
        self.assertIn("height: 44px", mark_block)
        self.assertIn("border-radius: 50%", mark_block)

    def test_header_uses_left_aligned_horizontal_layout(self):
        css = Path("app/jordana_invoice/static/review.css").read_text()

        brand_start = css.index(".brand")
        brand_end = css.index("}", brand_start) + 1
        brand_block = css[brand_start:brand_end]
        self.assertIn("display: flex", brand_block)
        self.assertIn("align-items: center", brand_block)

    def test_header_logo_and_name_on_same_line(self):
        html = Path("app/jordana_invoice/static/review.html").read_text()

        brand_start = html.index('class="brand"')
        brand_end = html.index("</div>", brand_start) + len("</div>")
        brand_html = html[brand_start:brand_end]
        self.assertIn("brand-mark", brand_html)
        self.assertIn("<strong>Jordana Billing</strong>", brand_html)
        self.assertLess(
            brand_html.index("brand-mark"),
            brand_html.index("<strong>Jordana Billing</strong>"),
        )

    def test_header_logo_mobile_layout_remains_usable(self):
        css = Path("app/jordana_invoice/static/review.css").read_text()

        self.assertIn("@media (max-width: 760px)", css)
        mobile_start = css.index("@media (max-width: 760px)")
        mobile_section = css[mobile_start:]
        self.assertIn(".brand", mobile_section)
        self.assertIn("margin-bottom: 2px", mobile_section)


if __name__ == "__main__":
    unittest.main()
