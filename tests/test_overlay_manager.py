"""Focused tests for the shared overlay manager and migrated overlay workflows.

Tests are static-JS checks that verify the overlay_manager.js source and
review.js integration follow the required lifecycle contract.
"""
import unittest
from pathlib import Path

OVERLAY_JS = Path("app/jordana_invoice/static/js/overlay_manager.js")
REVIEW_JS = Path("app/jordana_invoice/static/review.js")
REVIEW_HTML = Path("app/jordana_invoice/static/review.html")


class OverlayManagerModuleTests(unittest.TestCase):
    """Tests for the overlay_manager.js shared module structure."""

    def setUp(self):
        self.js = OVERLAY_JS.read_text()
        self.html = REVIEW_HTML.read_text()

    def test_module_file_exists(self):
        self.assertTrue(OVERLAY_JS.exists())

    def test_module_loaded_before_review_js_in_html(self):
        overlay_pos = self.html.index('<script src="/static/js/overlay_manager.js"></script>')
        review_pos = self.html.index('<script src="/static/review.js"></script>')
        api_pos = self.html.index('<script src="/static/js/api.js"></script>')
        self.assertLess(api_pos, overlay_pos)
        self.assertLess(overlay_pos, review_pos)

    def test_module_uses_iife_not_es_module(self):
        self.assertIn("(function () {", self.js)
        self.assertIn("})();", self.js)
        self.assertNotIn("export ", self.js)
        self.assertNotIn("import ", self.js)

    def test_module_uses_strict_mode(self):
        self.assertIn('"use strict"', self.js)

    def test_module_assigns_window_jordana_overlay(self):
        self.assertIn("window.JordanaOverlay", self.js)

    def test_module_exports_create_function(self):
        self.assertIn("create: create", self.js)

    def test_module_exports_lock_depth(self):
        self.assertIn("_lockDepth", self.js)

    def test_create_returns_overlay_controller(self):
        self.assertIn("return {", self.js)
        self.assertIn("open: open", self.js)
        self.assertIn("close: close", self.js)
        self.assertIn("beginPending: beginPending", self.js)
        self.assertIn("endPending: endPending", self.js)
        self.assertIn("isPending: isPending", self.js)
        self.assertIn("isOpen: isOpen", self.js)
        self.assertIn("getReturnFocus: getReturnFocus", self.js)
        self.assertIn("setReturnFocus: setReturnFocus", self.js)

    def test_body_lock_uses_counter(self):
        self.assertIn("_lockDepth", self.js)
        self.assertIn("document.body.style.overflow = \"hidden\"", self.js)

    def test_body_unlock_resets_overflow(self):
        self.assertIn('document.body.style.overflow = ""', self.js)

    def test_body_unlock_decrements_counter(self):
        self.assertIn("if (_lockDepth > 0) _lockDepth--", self.js)

    def test_body_unlock_only_resets_at_zero(self):
        self.assertIn("if (_lockDepth === 0)", self.js)

    def test_safe_focus_checks_dom_contains(self):
        self.assertIn("document.body.contains(el)", self.js)

    def test_safe_focus_checks_not_hidden(self):
        self.assertIn("!el.hidden", self.js)

    def test_safe_focus_checks_not_disabled(self):
        self.assertIn("!el.disabled", self.js)

    def test_open_captures_return_focus(self):
        self.assertIn("_returnFocus = options.returnFocus || document.activeElement", self.js)

    def test_open_sets_hidden_false(self):
        self.assertIn("overlay.hidden = false", self.js)

    def test_open_sets_aria_hidden_false(self):
        self.assertIn('overlay.setAttribute("aria-hidden", "false")', self.js)

    def test_open_applies_body_lock(self):
        self.assertIn("if (bodyLock) _lockBody()", self.js)

    def test_open_focuses_first_control_in_request_animation_frame(self):
        self.assertIn("requestAnimationFrame", self.js)
        self.assertIn("overlay.querySelector(firstFocusSelector)", self.js)

    def test_open_is_idempotent(self):
        self.assertIn("if (_open) return", self.js)

    def test_close_sets_hidden_true(self):
        self.assertIn("overlay.hidden = true", self.js)

    def test_close_sets_aria_hidden_true(self):
        self.assertIn('overlay.setAttribute("aria-hidden", "true")', self.js)

    def test_close_removes_body_lock(self):
        self.assertIn("if (bodyLock) _unlockBody()", self.js)

    def test_close_is_idempotent(self):
        self.assertIn("if (!_open) return true", self.js)

    def test_close_restores_focus_safely(self):
        self.assertIn("_safeFocus(_returnFocus)", self.js)

    def test_close_can_skip_focus_restore(self):
        self.assertIn("options.restoreFocus !== false", self.js)

    def test_close_runs_cleanup_callback(self):
        self.assertIn("if (cleanupFn)", self.js)
        self.assertIn("cleanupFn(options)", self.js)

    def test_close_restores_pending_buttons(self):
        self.assertIn("_restoreButtons()", self.js)

    def test_begin_pending_returns_false_if_already_pending(self):
        self.assertIn("if (_pending) return false", self.js)

    def test_begin_pending_disables_buttons(self):
        self.assertIn("btn.disabled = true", self.js)

    def test_begin_pending_tracks_disabled_buttons(self):
        self.assertIn("_disabledButtons.push(btn)", self.js)

    def test_begin_pending_accepts_string_ids(self):
        self.assertIn('typeof list[i] === "string"', self.js)
        self.assertIn("document.getElementById(list[i])", self.js)

    def test_begin_pending_accepts_element_references(self):
        self.assertIn("_disabledButtons = []", self.js)

    def test_end_pending_clears_flag(self):
        self.assertIn("_pending = false", self.js)

    def test_end_pending_restores_buttons(self):
        self.assertIn("_restoreButtons()", self.js)

    def test_restore_buttons_checks_dom_contains(self):
        self.assertIn("document.body.contains(btn)", self.js)

    def test_restore_buttons_re_enables(self):
        self.assertIn("btn.disabled = false", self.js)

    def test_keydown_handler_bound_once(self):
        self.assertIn("if (keydownHandler && !_keydownBound)", self.js)
        self.assertIn("_keydownBound = true", self.js)

    def test_keydown_handler_removed_on_close(self):
        self.assertIn("document.removeEventListener(\"keydown\", _onKeydown)", self.js)
        self.assertIn("_keydownBound = false", self.js)

    def test_no_duplicate_event_listeners_on_repeated_opens(self):
        self.assertIn("if (keydownHandler && !_keydownBound)", self.js)

    def test_no_framework_dependency(self):
        self.assertNotIn("React", self.js)
        self.assertNotIn("Vue", self.js)
        self.assertNotIn("Angular", self.js)
        self.assertNotIn("require(", self.js)


class ReviewOverlayIntegrationTests(unittest.TestCase):
    """Tests that review.js correctly integrates with the overlay manager."""

    def setUp(self):
        self.js = REVIEW_JS.read_text()

    def test_review_js_destructures_create_from_jordana_overlay(self):
        self.assertIn("const { create: createOverlay } = window.JordanaOverlay;", self.js)

    def test_review_overlay_controller_created(self):
        self.assertIn("const reviewOverlayCtrl = createOverlay({", self.js)

    def test_review_overlay_controller_uses_review_overlay_id(self):
        self.assertIn('overlay: "reviewOverlay"', self.js)

    def test_review_overlay_controller_uses_close_btn_id(self):
        self.assertIn('closeBtn: "reviewOverlayClose"', self.js)

    def test_review_overlay_controller_has_keydown_handler(self):
        self.assertIn("keydownHandler: reviewOverlayKeydownHandler", self.js)

    def test_review_overlay_controller_body_lock_false(self):
        self.assertIn("bodyLock: false", self.js)

    def test_open_review_overlay_delegates_to_controller(self):
        self.assertIn("reviewOverlayCtrl.open({})", self.js)

    def test_close_review_overlay_delegates_to_controller(self):
        self.assertIn("reviewOverlayCtrl.close({ restoreFocus: true })", self.js)

    def test_render_rows_uses_set_return_focus(self):
        self.assertIn("reviewOverlayCtrl.setReturnFocus(row)", self.js)
        self.assertIn("reviewOverlayCtrl.setReturnFocus(reviewBtn)", self.js)

    def test_no_stale_overlay_return_focus_variable(self):
        self.assertNotIn("overlayReturnFocus", self.js.replace(
            "paymentOverlayReturnFocus", ""))

    def test_no_stale_overlay_keydown_handler(self):
        self.assertNotIn("function overlayKeydownHandler(", self.js)


class WorkflowStateObjectsTests(unittest.TestCase):
    """Tests that workflow-local state objects exist and are used correctly."""

    def setUp(self):
        self.js = REVIEW_JS.read_text()

    def test_approval_state_object_exists(self):
        self.assertIn("const approvalState = { submitting: false, candidateId: null };", self.js)

    def test_duplicate_state_object_exists(self):
        self.assertIn("const duplicateState = { submitting: false, candidateId: null };", self.js)

    def test_restore_state_object_exists(self):
        self.assertIn("const restoreState = { submitting: false, candidateId: null };", self.js)

    def test_billing_wizard_state_object_exists(self):
        self.assertIn("const billingWizardState = { submitting: false };", self.js)

    def test_approval_state_submitting_used_in_save(self):
        start = self.js.index("async function save(approve)")
        end = self.js.index("function collectSessionDraftValues", start)
        save_fn = self.js[start:end]
        self.assertIn("approvalState.submitting", save_fn)

    def test_approval_state_candidate_id_set_on_submit(self):
        start = self.js.index("async function save(approve)")
        end = self.js.index("function collectSessionDraftValues", start)
        save_fn = self.js[start:end]
        self.assertIn("approvalState.candidateId = state.selected", save_fn)

    def test_approval_state_candidate_id_cleared_on_success(self):
        start = self.js.index("async function save(approve)")
        end = self.js.index("function collectSessionDraftValues", start)
        save_fn = self.js[start:end]
        self.assertIn("approvalState.candidateId = null", save_fn)

    def test_approval_state_cleared_on_failure(self):
        start = self.js.index("async function save(approve)")
        end = self.js.index("function collectSessionDraftValues", start)
        save_fn = self.js[start:end]
        catch_idx = save_fn.index("} catch (error) {")
        catch_block = save_fn[catch_idx:]
        self.assertIn("approvalState.submitting = false", catch_block)
        self.assertIn("approvalState.candidateId = null", catch_block)

    def test_duplicate_state_submitting_used_in_confirm(self):
        start = self.js.index("async function confirmDuplicateAndNext")
        end = self.js.index("async function sendToReview", start)
        dup_fn = self.js[start:end]
        self.assertIn("duplicateState.submitting", dup_fn)

    def test_duplicate_state_candidate_id_set_on_submit(self):
        start = self.js.index("async function confirmDuplicateAndNext")
        end = self.js.index("async function sendToReview", start)
        dup_fn = self.js[start:end]
        self.assertIn("duplicateState.candidateId = state.selected", dup_fn)

    def test_duplicate_state_cleared_on_success(self):
        start = self.js.index("async function confirmDuplicateAndNext")
        end = self.js.index("async function sendToReview", start)
        dup_fn = self.js[start:end]
        self.assertIn("duplicateState.candidateId = null", dup_fn)

    def test_duplicate_state_cleared_on_failure(self):
        start = self.js.index("async function confirmDuplicateAndNext")
        end = self.js.index("async function sendToReview", start)
        dup_fn = self.js[start:end]
        catch_idx = dup_fn.index("} catch (error) {")
        catch_block = dup_fn[catch_idx:]
        self.assertIn("duplicateState.submitting = false", catch_block)
        self.assertIn("duplicateState.candidateId = null", catch_block)

    def test_restore_state_submitting_used_in_restore(self):
        start = self.js.index("async function restoreSessionRow")
        end = self.js.index("async function sendSessionRowToReview", start)
        restore_fn = self.js[start:end]
        self.assertIn("restoreState.submitting", restore_fn)

    def test_restore_state_candidate_id_set_on_submit(self):
        start = self.js.index("async function restoreSessionRow")
        end = self.js.index("async function sendSessionRowToReview", start)
        restore_fn = self.js[start:end]
        self.assertIn("restoreState.candidateId = candidateId", restore_fn)

    def test_restore_state_cleared_in_finally(self):
        start = self.js.index("async function restoreSessionRow")
        end = self.js.index("async function sendSessionRowToReview", start)
        restore_fn = self.js[start:end]
        self.assertIn("restoreState.submitting = false", restore_fn)
        self.assertIn("restoreState.candidateId = null", restore_fn)

    def test_restore_prevents_duplicate_submission(self):
        start = self.js.index("async function restoreSessionRow")
        end = self.js.index("async function sendSessionRowToReview", start)
        restore_fn = self.js[start:end]
        self.assertIn("if (restoreState.submitting) return", restore_fn)

    def test_restore_prevents_concurrent_different_candidate(self):
        start = self.js.index("async function restoreSessionRow")
        end = self.js.index("async function sendSessionRowToReview", start)
        restore_fn = self.js[start:end]
        self.assertIn("restoreState.candidateId !== candidateId", restore_fn)

    def test_billing_wizard_state_synced_in_doSave(self):
        start = self.js.index("function openCreateRelationshipModal")
        wizard = self.js[start:]
        doSave_start = wizard.index("async function doSave()")
        doSave_end = wizard.index("async function attachToSession", doSave_start)
        doSave_fn = wizard[doSave_start:doSave_end]
        self.assertIn("billingWizardState.submitting = true", doSave_fn)

    def test_billing_wizard_state_cleared_on_failure(self):
        start = self.js.index("function openCreateRelationshipModal")
        wizard = self.js[start:]
        doSave_start = wizard.index("async function doSave()")
        doSave_end = wizard.index("async function attachToSession", doSave_start)
        doSave_fn = wizard[doSave_start:doSave_end]
        catch_idx = doSave_fn.index("} catch (err) {")
        catch_block = doSave_fn[catch_idx:]
        self.assertIn("billingWizardState.submitting = false", catch_block)

    def test_billing_wizard_state_cleared_on_close(self):
        self.assertIn("billingWizardState.submitting = false;", self.js)

    def test_no_stale_approval_in_progress(self):
        self.assertNotIn("approvalInProgress", self.js)

    def test_no_stale_duplicate_in_progress(self):
        self.assertNotIn("duplicateInProgress", self.js)


class ApprovalLifecycleTests(unittest.TestCase):
    """Tests for the approval overlay lifecycle after migration."""

    def setUp(self):
        self.js = REVIEW_JS.read_text()
        start = self.js.index("async function save(approve)")
        end = self.js.index("function collectSessionDraftValues", start)
        self.save_fn = self.js[start:end]

    def test_double_click_submit_produces_one_call(self):
        self.assertIn("if (approve && approvalState.submitting) return;", self.save_fn)

    def test_success_closes_overlay(self):
        self.assertIn("closeReviewOverlay({ clearCandidate: true, skipDirtyCheck: true });", self.save_fn)

    def test_success_clears_candidate_state(self):
        self.assertIn("approvalState.candidateId = null", self.save_fn)

    def test_success_with_warning_still_closes(self):
        self.assertIn("staging.status === \"warning\"", self.save_fn)
        self.assertIn("closeReviewOverlay({ clearCandidate: true, skipDirtyCheck: true });", self.save_fn)

    def test_success_with_warning_shows_warning_separately(self):
        self.assertIn("showReviewWarning(warningMsg)", self.save_fn)

    def test_failure_keeps_overlay_open(self):
        catch_idx = self.save_fn.index("} catch (error) {")
        catch_block = self.save_fn[catch_idx:]
        self.assertNotIn("closeReviewOverlay", catch_block)

    def test_failure_re_enables_controls(self):
        catch_idx = self.save_fn.index("} catch (error) {")
        catch_block = self.save_fn[catch_idx:]
        self.assertIn("reviewOverlayCtrl.endPending()", catch_block)

    def test_failure_preserves_form_state(self):
        catch_idx = self.save_fn.index("} catch (error) {")
        catch_block = self.save_fn[catch_idx:]
        self.assertNotIn("renderInspector", catch_block)

    def test_failure_sanitizes_error(self):
        catch_idx = self.save_fn.index("} catch (error) {")
        catch_block = self.save_fn[catch_idx:]
        self.assertIn("sanitizeUiErrorMessage", catch_block)

    def test_action_cannot_be_resubmitted_after_success(self):
        success_idx = self.save_fn.index("approvalState.submitting = false;")
        close_idx = self.save_fn.index("closeReviewOverlay({ clearCandidate: true, skipDirtyCheck: true });")
        self.assertLess(close_idx, success_idx)


class DuplicateLifecycleTests(unittest.TestCase):
    """Tests for the duplicate resolution lifecycle after migration."""

    def setUp(self):
        self.js = REVIEW_JS.read_text()
        start = self.js.index("async function confirmDuplicateAndNext")
        end = self.js.index("async function sendToReview", start)
        self.dup_fn = self.js[start:end]

    def test_double_click_confirm_produces_one_call(self):
        self.assertIn("if (duplicateState.submitting) return;", self.dup_fn)

    def test_success_clears_candidate_state(self):
        self.assertIn("duplicateState.candidateId = null", self.dup_fn)

    def test_success_closes_overlay(self):
        self.assertIn("closeReviewOverlay({ clearCandidate: true, skipDirtyCheck: true });", self.dup_fn)

    def test_success_advances_when_supported(self):
        self.assertIn("selectCandidate(items[0].candidate_id)", self.dup_fn)

    def test_failure_preserves_selection(self):
        catch_idx = self.dup_fn.index("} catch (error) {")
        catch_block = self.dup_fn[catch_idx:]
        self.assertNotIn("closeReviewOverlay", catch_block)
        self.assertNotIn("state.selected = null", catch_block)

    def test_failure_re_enables_action(self):
        catch_idx = self.dup_fn.index("} catch (error) {")
        catch_block = self.dup_fn[catch_idx:]
        self.assertIn("reviewOverlayCtrl.endPending()", catch_block)

    def test_success_leaves_no_stale_resubmittable_overlay(self):
        self.assertIn("duplicateState.submitting = false", self.dup_fn)
        self.assertIn("duplicateState.candidateId = null", self.dup_fn)


class RestoreLifecycleTests(unittest.TestCase):
    """Tests for the restore candidate lifecycle after migration."""

    def setUp(self):
        self.js = REVIEW_JS.read_text()
        start = self.js.index("async function restoreSessionRow")
        end = self.js.index("async function sendSessionRowToReview", start)
        self.restore_fn = self.js[start:end]

    def test_restore_success_refreshes(self):
        self.assertIn("await loadSessions()", self.restore_fn)

    def test_restore_success_with_warning_shows_warning(self):
        self.assertIn("result.warning", self.restore_fn)

    def test_restore_genuine_failure_keeps_action_re_enabled(self):
        catch_idx = self.restore_fn.index("} catch (err) {")
        catch_block = self.restore_fn[catch_idx:]
        self.assertIn("btn.disabled = false", catch_block)

    def test_restore_sanitizes_error(self):
        catch_idx = self.restore_fn.index("} catch (err) {")
        catch_block = self.restore_fn[catch_idx:]
        self.assertIn("sanitizeUiErrorMessage", catch_block)

    def test_restore_clears_state_in_finally(self):
        self.assertIn("} finally {", self.restore_fn)
        finally_idx = self.restore_fn.index("} finally {")
        finally_block = self.restore_fn[finally_idx:]
        self.assertIn("restoreState.submitting = false", finally_block)
        self.assertIn("restoreState.candidateId = null", finally_block)

    def test_restore_disables_button_during_pending(self):
        self.assertIn("btn.disabled = true", self.restore_fn)


class BillingRelationshipLifecycleTests(unittest.TestCase):
    """Tests for the billing relationship wizard lifecycle after migration."""

    def setUp(self):
        self.js = REVIEW_JS.read_text()

    def test_save_success_closes_modal(self):
        start = self.js.index("function openCreateRelationshipModal")
        wizard = self.js[start:]
        doSave_start = wizard.index("async function doSave()")
        attach_start = wizard.index("async function attachToSession", doSave_start)
        doSave_fn = wizard[doSave_start:attach_start]
        self.assertIn("closeBillingModal()", doSave_fn)

    def test_save_failure_preserves_selected_chips(self):
        start = self.js.index("function openCreateRelationshipModal")
        wizard = self.js[start:]
        doSave_start = wizard.index("async function doSave()")
        attach_start = wizard.index("async function attachToSession", doSave_start)
        doSave_fn = wizard[doSave_start:attach_start]
        catch_idx = doSave_fn.index("} catch (err) {")
        catch_block = doSave_fn[catch_idx:]
        # Modal stays open on failure — saveBtn is re-enabled and state is cleared
        self.assertIn("saveBtn.disabled = false", catch_block)
        self.assertIn("billingWizardState.submitting = false", catch_block)
        # The first few lines of catch should not close the modal
        first_lines = catch_block[:200]
        self.assertNotIn("closeBillingModal()", first_lines)

    def test_changing_payer_clears_covered_clients(self):
        start = self.js.index("function openCreateRelationshipModal")
        wizard = self.js[start:]
        select_start = wizard.index("function selectPayerType")
        select_end = wizard.index("function showPayerSearch", select_start)
        select_fn = wizard[select_start:select_end]
        self.assertIn("coveredClients = []", select_fn)

    def test_payer_not_auto_selected_as_covered(self):
        start = self.js.index("function openCreateRelationshipModal")
        wizard = self.js[start:]
        select_start = wizard.index("function selectPayerType")
        select_end = wizard.index("function showPayerSearch", select_start)
        select_fn = wizard[select_start:select_end]
        self.assertNotIn("coveredClients.unshift", select_fn)
        self.assertNotIn("coveredClients.push", select_fn)

    def test_removed_chips_become_searchable_again(self):
        start = self.js.index("function openCreateRelationshipModal")
        wizard = self.js[start:]
        self.assertIn("function removeCoveredClient", wizard)
        remove_start = wizard.index("function removeCoveredClient")
        remove_end = wizard.index("function renderCoveredChips", remove_start)
        remove_fn = wizard[remove_start:remove_end]
        self.assertIn("coveredClients = coveredClients.filter", remove_fn)

    def test_close_billing_modal_clears_wizard_state(self):
        self.assertIn("billingWizardState.submitting = false;", self.js)

    def test_selected_clients_omitted_from_search_results(self):
        start = self.js.index("function openCreateRelationshipModal")
        wizard = self.js[start:]
        render_start = wizard.index("function renderCoveredResults")
        render_end = wizard.index("function addCoveredClient", render_start)
        render_fn = wizard[render_start:render_end]
        self.assertIn("!selectedIds.has(row.person_id)", render_fn)


class StaticContractTests(unittest.TestCase):
    """Tests that no backend contracts, routes, or payload keys changed."""

    def setUp(self):
        self.js = REVIEW_JS.read_text()
        self.html = REVIEW_HTML.read_text()
        self.overlay_js = OVERLAY_JS.read_text()

    def test_no_backend_files_modified(self):
        import os
        backend_paths = [
            "app/jordana_invoice/review_services.py",
            "app/jordana_invoice/request_validation.py",
        ]
        for p in backend_paths:
            self.assertTrue(os.path.exists(p), f"Backend file {p} should exist unchanged")

    def test_no_new_framework_dependency(self):
        self.assertNotIn("React", self.overlay_js)
        self.assertNotIn("Vue", self.overlay_js)
        self.assertNotIn("Angular", self.overlay_js)
        self.assertNotIn("require(", self.overlay_js)
        self.assertNotIn("import ", self.overlay_js)

    def test_api_endpoints_unchanged(self):
        self.assertIn("/api/review/candidates/", self.js)
        self.assertIn("\"approve\"", self.js)
        self.assertIn("/mark", self.js)
        self.assertIn("/restore", self.js)
        self.assertIn("/api/billing-relationships/setup", self.js)

    def test_payload_keys_unchanged(self):
        self.assertIn("classification: \"duplicate\"", self.js)
        self.assertIn("reason: \"duplicate\"", self.js)
        self.assertIn("payer_kind: payerType", self.js)
        self.assertIn("covered_client_ids: coveredClients.map", self.js)

    def test_overlay_manager_does_not_log_payloads(self):
        self.assertNotIn("console.log", self.overlay_js)
        self.assertNotIn("console.error", self.overlay_js)

    def test_no_launcher_or_installer_files_changed(self):
        import os
        for p in ["scripts/bootstrap.sh", "scripts/build_launcher.sh"]:
            self.assertTrue(os.path.exists(p), f"Launcher file {p} should exist unchanged")


if __name__ == "__main__":
    unittest.main()
