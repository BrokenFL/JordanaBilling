(() => {
  "use strict";

  function confirmedParticipantIds() {
    return (state.participants || []).map((participant) => participant.person_id).filter(Boolean);
  }

  function simplifyRateControls() {
    const saveButton = document.getElementById("saveSessionBtn");
    if (saveButton) saveButton.textContent = "Save Session";

    const help = document.getElementById("sessionRateHelp");
    if (help) {
      help.textContent = "This rate applies only to this session unless you save it as a future default.";
    }

    const scope = document.getElementById("rateScope");
    if (!scope || scope.dataset.simplified === "true") return;

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

    let rateScope = "session_only";
    let rateScopePersonId = null;
    if (saveFutureJoint && participantIds.length > 1) {
      rateScope = "future_joint";
    } else if (saveFuturePerson && participantIds.length === 1) {
      rateScope = "future_person";
      rateScopePersonId = participantIds[0];
    }

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
      payment_status: document.getElementById("paymentInput")?.value || state.detail?.session?.payment_status || "unresolved",
      billing_treatment: document.getElementById("billingTreatmentInput")?.value || state.detail?.session?.billing_treatment || "",
      billable_status: document.getElementById("billableInput")?.value || state.detail?.session?.billable_status || "proposed",
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