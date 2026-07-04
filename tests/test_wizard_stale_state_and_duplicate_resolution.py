"""Focused tests for billing-relationship wizard stale state and duplicate resolution.

Issue 1: Billing relationship wizard carries stale client selections.
Issue 2: Duplicate resolution should complete and advance.

Tests are static-JS checks that verify the review.js source contains the
correct patterns, following the same approach as test_review_ui_static.py.
"""
import unittest
from pathlib import Path

JS_PATH = Path("app/jordana_invoice/static/review.js")


class WizardStaleStateTests(unittest.TestCase):
    """Issue 1: Billing relationship wizard must not carry stale client selections."""

    def setUp(self):
        self.js = JS_PATH.read_text()
        self.wizard_start = self.js.index("function openCreateRelationshipModal")
        self.wizard_end = self.js.index("\nfunction openCoveredSearch")
        if self.wizard_end < self.wizard_start:
            self.wizard_end = len(self.js)
        self.wizard = self.js[self.wizard_start:self.wizard_end]

    def test_changing_payer_type_clears_covered_clients(self):
        """1. Changing payer clears stale covered-client state."""
        select_start = self.wizard.index("function selectPayerType")
        select_end = self.wizard.index("function showPayerSearch", select_start)
        select_fn = self.wizard[select_start:select_end]
        self.assertIn("coveredClients = []", select_fn)

    def test_payer_not_automatically_added_as_covered(self):
        """2. Payer is not automatically added as covered."""
        select_start = self.wizard.index("function selectPayerType")
        select_end = self.wizard.index("function showPayerSearch", select_start)
        select_fn = self.wizard[select_start:select_end]
        self.assertNotIn("coveredClients.unshift", select_fn)
        self.assertNotIn("coveredClients.push", select_fn)

        select_payer_start = self.wizard.index("function selectPayer(")
        select_payer_end = self.wizard.index("function showPayerSelected", select_payer_start)
        select_payer_fn = self.wizard[select_payer_start:select_payer_end]
        self.assertNotIn("coveredClients.unshift", select_payer_fn)
        self.assertNotIn("coveredClients.push", select_payer_fn)

    def test_no_preselect_participants_call(self):
        """3. Launch-context client is not auto-preselected; it remains selectable."""
        self.assertNotIn("preselectParticipants()", self.wizard)
        self.assertNotIn("function preselectParticipants", self.wizard)

    def test_selected_clients_omitted_from_search_results(self):
        """4. Selected clients do not appear as normal actionable search results."""
        render_start = self.wizard.index("function renderCoveredResults")
        render_end = self.wizard.index("function addCoveredClient", render_start)
        render_fn = self.wizard[render_start:render_end]
        self.assertIn("!selectedIds.has(row.person_id)", render_fn)
        self.assertNotIn("Click to remove", render_fn)
        self.assertNotIn("already-included", render_fn)

    def test_removing_chip_restores_client_to_search(self):
        """5. Removing a selected chip makes that client available in search again."""
        self.assertIn("function removeCoveredClient", self.wizard)
        remove_start = self.wizard.index("function removeCoveredClient")
        remove_end = self.wizard.index("function renderCoveredChips", remove_start)
        remove_fn = self.wizard[remove_start:remove_end]
        self.assertIn("coveredClients = coveredClients.filter", remove_fn)
        self.assertIn("renderStep2()", remove_fn)

    def test_step2_continue_enabled_state_reflects_selected_list(self):
        """6. Step 2 Continue enabled state reflects actual selected-client list."""
        update_start = self.wizard.index("function updateContinueDisabled")
        update_end = self.wizard.index("function renderStep2", update_start)
        update_fn = self.wizard[update_start:update_end]
        self.assertIn("coveredClients.length === 0", update_fn)

    def test_cancel_back_preserves_no_unintended_changes(self):
        """7. Cancel/back behavior preserves no unintended relationship changes."""
        self.assertIn("function doCancel", self.wizard)
        self.assertIn("function goBack", self.wizard)
        back_start = self.wizard.index("function goBack()")
        back_end = self.wizard.index("function renderStep", back_start)
        back_fn = self.wizard[back_start:back_end]
        self.assertIn("step--", back_fn)
        self.assertNotIn("coveredClients = []", back_fn)

    def test_handlePersonCreated_does_not_auto_add_for_step1(self):
        """Newly created person in Step 1 is not auto-added to covered clients."""
        handle_start = self.wizard.index("function handlePersonCreated")
        handle_end = self.wizard.index("function showCreateOrgForm", handle_start)
        handle_fn = self.wizard[handle_start:handle_end]
        client_branch = handle_fn.index("formPayerType === \"client\"")
        client_branch_end = handle_fn.index("} else if (formPayerType === \"person\"", client_branch)
        client_section = handle_fn[client_branch:client_branch_end]
        self.assertNotIn("coveredClients.unshift", client_section)
        self.assertNotIn("coveredClients.push", client_section)

    def test_step2_search_results_use_available_filter(self):
        """Search results filter out selected clients using available array."""
        render_start = self.wizard.index("function renderCoveredResults")
        render_end = self.wizard.index("function addCoveredClient", render_start)
        render_fn = self.wizard[render_start:render_end]
        self.assertIn("const available", render_fn)
        self.assertIn("addCoveredClient(pid, available)", render_fn)


class DuplicateResolutionTests(unittest.TestCase):
    """Issue 2: Duplicate resolution should complete and advance."""

    def setUp(self):
        self.js = JS_PATH.read_text()
        self.dup_start = self.js.index("async function confirmDuplicateAndNext")
        self.dup_end = self.js.index("async function sendToReview", self.dup_start)
        self.dup_fn = self.js[self.dup_start:self.dup_end]

    def test_duplicate_footer_button_removed(self):
        """1. Duplicate resolution is no longer exposed as a footer action."""
        self.assertNotIn("Confirm Duplicate & Next", self.js)
        self.assertNotIn('id="duplicateBtn"', self.js)

    def test_button_disables_while_pending(self):
        """2. Button disables while pending."""
        self.assertIn("duplicateState.submitting = true", self.dup_fn)
        self.assertIn('reviewOverlayCtrl.beginPending(["duplicateBtn"]);', self.dup_fn)

    def test_double_click_sends_one_request(self):
        """3. Double-click sends one request."""
        self.assertIn("if (duplicateState.submitting) return;", self.dup_fn)

    def test_success_closes_overlay(self):
        """4. Success closes the overlay."""
        self.assertIn("closeReviewOverlay({ clearCandidate: true, skipDirtyCheck: true });", self.dup_fn)

    def test_success_clears_selected_candidate_state(self):
        """5. Success clears selected candidate state."""
        self.assertIn("clearCandidate: true", self.dup_fn)
        self.assertIn("state.dirty.clear()", self.dup_fn)

    def test_success_removes_or_refreshes_resolved_item(self):
        """6. Success removes or refreshes the resolved item from the list."""
        self.assertIn("await loadList()", self.dup_fn)

    def test_next_unresolved_item_opens_when_supported(self):
        """7. Next unresolved item opens when supported."""
        self.assertIn("selectCandidate(items[0].candidate_id)", self.dup_fn)

    def test_no_remaining_item_restores_focus_to_list(self):
        """8. No remaining item restores focus to the list."""
        self.assertIn('document.querySelector("#candidateRows .review-btn")', self.dup_fn)
        self.assertIn("firstReviewBtn.focus()", self.dup_fn)
        self.assertIn('$("searchBox")?.focus()', self.dup_fn)

    def test_failure_keeps_overlay_open(self):
        """9. Failure keeps the overlay open."""
        self.assertNotIn("closeReviewOverlay", self._catch_block())

    def test_failure_reenables_action(self):
        """10. Failure re-enables the action."""
        catch = self._catch_block()
        self.assertIn("duplicateState.submitting = false", catch)
        self.assertIn("reviewOverlayCtrl.endPending()", catch)

    def test_already_approved_and_unrelated_records_unchanged(self):
        """11. The duplicate function only calls the mark endpoint, no other mutation."""
        self.assertIn("/mark", self.dup_fn)
        self.assertNotIn("/approve", self.dup_fn)
        self.assertNotIn("/save-relationship", self.dup_fn)
        self.assertNotIn("/save-billing", self.dup_fn)

    def test_success_message_shown(self):
        """Success shows 'Duplicate resolved' message."""
        self.assertIn("showReviewSuccess", self.dup_fn)
        self.assertIn("Duplicate resolved", self.dup_fn)

    def test_duplicate_in_progress_flag_exists_at_top_level(self):
        """The duplicateState flag is declared at module level."""
        self.assertIn("const duplicateState = { submitting: false, candidateId: null };", self.js)

    def test_duplicate_resolution_function_remains_guarded(self):
        """The legacy duplicate resolver stays guarded for non-footer callers."""
        self.assertIn("async function confirmDuplicateAndNext", self.js)
        self.assertIn("if (duplicateState.submitting) return;", self.dup_fn)

    def _catch_block(self):
        catch_idx = self.dup_fn.index("} catch (error) {")
        return self.dup_fn[catch_idx:]


if __name__ == "__main__":
    unittest.main()
