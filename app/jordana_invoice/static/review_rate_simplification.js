(() => {
  "use strict";

  function confirmedParticipantIds() {
    return (state.participants || []).map((participant) => participant.person_id).filter(Boolean);
  }

  function setOptionLabel(select, value, label) {
    if (!select) return;
    const option = [...select.options].find((item) => item.value === value);
    if (option) option.textContent = label;
  }

  function simplifyPaymentAndApprovalControls() {
    const payment = document.getElementById("paymentInput");
    setOptionLabel(payment, "unresolved", "Needs confirmation");
    setOptionLabel(payment, "unpaid", "Unpaid");
    setOptionLabel(payment, "partially_paid", "Partially paid");
    setOptionLabel(payment, "paid", "Paid");
    setOptionLabel(payment, "waived", "Waived");
    setOptionLabel(payment, "not_billable", "Not billable");

    const billableInput = document.getElementById("billableInput");
    if (billableInput) {
      const field = billableInput.closest("label.field");
      if (field) field.remove();
    }

    const approveButton = document.getElementById("approveBtn");
    if (approveButton) approveButton.textContent = "Final Approve Session";
  }

  function simplifyRateControls() {
    const saveButton = document.getElementById("saveSessionBtn");
    if (saveButton) saveButton.textContent = "Save Session";

    const help = document.getElementById("sessionRateHelp");
    if (help) {
      help.textContent = "This rate applies only to this session unless you save it as a future default.";
    }

    const scope = document.getElementById("rateScope");
    if (!scope || scope.dataset.simplified === "true") {
      simplifyPaymentAndApprovalControls();
      return;
    }

    const participantIds = confirmedParticipantIds();
    let option = "";
    if (participantIds.length === 1) {
      option = `
        <label class="checkbox-field wide">
          <input type="checkbox" id="saveFuturePersonRate">
          <span>Save as this client’s future default rate</span>
        </label>`;
    } else if (participantIds.length > 1) {
      option = `
        <label class="checkbox-field wide">
          <input type="checkbox" id="saveFutureJointRate">
          <span>Save as the future rate for these clients together</span>
        </label>`;
    }

    scope.dataset.simplified = "true";
    scope.innerHTML = option || '<div class="help">Future defaults can be managed in Rate Card after the clients are confirmed.</div>';
    simplifyPaymentAndApprovalControls();
  }

  const originalRenderInspector = renderInspector;
  renderInspector = function patchedRenderInspector(data) {
    originalRenderInspector(data);
    simplifyRateControls();
  };

  collectPayload = function simplifiedCollectPayload() {
    const durationChoice = document.getElementById("durationChoiceInput")?.value || "60";
    const customMinutes = document.getElementById("customDurationInput")?.value || "";
    const approvedMinutes = durationChoice === "custom" ? customMinutes : durationChoice;
    const participantIds = confirmedParticipantIds();
    const saveFuturePerson = document.getElementById("saveFuturePersonRate")?.checked === true;
    const saveFutureJoint = document.getElementById("saveFutureJointRate")?.checked === true;
    const paymentStatus = document.getElementById("paymentInput")?.value || state.detail?.session?.payment_status || "unresolved";

    let rateScope = "session_only";
    let rateScopePersonId = null;
    if (saveFutureJoint && participantIds.length > 1) {
      rateScope = "future_joint";
    } else if (saveFuturePerson && participantIds.length === 1) {
      rateScope = "future_person";
      rateScopePersonId = participantIds[0];
    }

    const billableStatus = ["waived", "not_billable"].includes(paymentStatus) ? "nonbillable" : "approved";

    return {
      ...collectRelationshipPayload(),
      approved_duration_minutes: approvedMinutes,
      billing_session_type: document.getElementById("billingTypeInput")?.value || state.detail?.session?.billing_session_type || "psychotherapy",
      duration_choice: durationChoice,
      custom_duration_minutes: durationChoice === "custom" ? customMinutes : "",
      custom_service_description: document.getElementById("customDescInput")?.value || "",
      custom_service_code: document.getElementById("customCodeInput")?.value || "",
      time_category: document.getElementById("timeCategoryInput")?.value || state.detail?.session?.time_category || "standard",
      suggested_rate: centString(state.detail?.session?.suggested_rate_cents),
      billing_party_id: state.billingParty ? state.billingParty.billing_party_id : state.detail?.effective_billing_party?.billing_party_id || null,
      approved_rate: document.getElementById("approvedRateInput")?.value || "",
      payment_status: paymentStatus,
      billing_treatment: document.getElementById("billingTreatmentInput")?.value || state.detail?.session?.billing_treatment || "",
      billable_status: billableStatus,
      rate_override_reason: document.getElementById("overrideReasonInput")?.value || state.detail?.session?.rate_override_reason || "",
      rate_scope: rateScope,
      rate_scope_person_id: rateScopePersonId
    };
  };

  saveSessionSection = async function simplifiedSaveSessionSection() {
    const button = document.getElementById("saveSessionBtn");
    if (button) button.disabled = true;
    try {
      const updated = await api(`/api/review/candidates/${state.selected}/save-session`, {
        method: "POST",
        body: JSON.stringify(collectPayload())
      });
      state.detail = updated;
      state.editSteps.session = false;
      renderInspector(updated);
      markSaved("session", "Session saved");
      await loadList();
      requestAnimationFrame(() => {
        const approveButton = document.getElementById("approveBtn");
        if (approveButton) approveButton.scrollIntoView({ behavior: "smooth", block: "center" });
      });
    } catch (error) {
      alert(`Could not save session: ${error.message}`);
    } finally {
      if (button && document.body.contains(button)) button.disabled = false;
    }
  };

  const originalWireInspector = wireInspector;
  wireInspector = function patchedWireInspector() {
    originalWireInspector();
    const saveButton = document.getElementById("saveSessionBtn");
    if (saveButton) saveButton.onclick = saveSessionSection;
    simplifyRateControls();
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", simplifyRateControls);
  } else {
    simplifyRateControls();
  }
})();