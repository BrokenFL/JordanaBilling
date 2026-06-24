(() => {
  "use strict";

  const autoConfirming = new Set();

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

  const originalLoadList = loadList;
  loadList = async function simplifiedLoadList() {
    const params = new URLSearchParams({
      q: document.getElementById("searchBox").value,
      review_status: document.getElementById("statusFilter").value,
      billing_session_type: document.getElementById("serviceFilter").value,
      time_category: document.getElementById("timeFilter").value,
      calendar_filter: document.getElementById("calendarFilter").value,
      limit: state.limit,
      offset: state.offset
    });
    const data = await api(`/api/review/candidates?${params}`);
    const explicitApprovedView = document.getElementById("statusFilter").value === "approved";
    const visibleItems = explicitApprovedView
      ? data.items.map((item) => ({ ...item, authority_score: 100 }))
      : data.items.filter((item) => item.status !== "approved");

    state.items = visibleItems;
    renderStatus(data.status);
    renderRows(visibleItems, Math.max(0, data.total - (data.items.length - visibleItems.length)));

    const selectedStillVisible = visibleItems.some((item) => item.candidate_id === state.selected);
    if (!selectedStillVisible) state.selected = null;
    if (!state.selected && visibleItems.length) await selectCandidate(visibleItems[0].candidate_id);
    if (!visibleItems.length) {
      document.getElementById("inspector").innerHTML = '<div class="empty-state">No sessions need review.</div>';
    }
  };

  async function autoConfirmKnownClientAndPayer(data) {
    const candidateId = state.selected;
    const readiness = data?.readiness || {};
    const participants = data?.participants || [];
    const knownParticipants = participants.filter((participant) => participant.person_id && !participant.is_proposed);
    const payer = data?.effective_billing_party || data?.billing_party;

    if (!candidateId || autoConfirming.has(candidateId)) return;
    if (participants.length !== 1 || knownParticipants.length !== 1 || !payer) return;
    if (readiness.clients_ready && readiness.billing_ready) return;

    autoConfirming.add(candidateId);
    try {
      let updated = data;
      if (!readiness.clients_ready) {
        updated = await api(`/api/review/candidates/${candidateId}/save-relationship`, {
          method: "POST",
          body: JSON.stringify({
            participants: participants.map(participantState),
            account_id: data.account?.account_id || null,
            primary_person_id: knownParticipants[0].person_id,
            default_billing_party_id: payer.billing_party_id || null,
            billing_party_id: payer.billing_party_id || null
          })
        });
      }

      const refreshedReadiness = updated.readiness || {};
      const payerPersonId = (updated.effective_billing_party || updated.billing_party || payer)?.person_id;
      if (!refreshedReadiness.billing_ready && payerPersonId) {
        updated = await api(`/api/review/candidates/${candidateId}/save-billing`, {
          method: "POST",
          body: JSON.stringify({ bill_to_person_id: payerPersonId })
        });
      }

      state.detail = updated;
      state.participants = updated.participants.map(participantState);
      state.account = updated.account;
      state.billingParty = updated.billing_party || updated.effective_billing_party;
      renderInspector(updated);
      await loadList();
    } catch (error) {
      console.warn("Automatic known-client confirmation skipped:", error);
    } finally {
      autoConfirming.delete(candidateId);
    }
  }

  const originalSelectCandidate = selectCandidate;
  selectCandidate = async function simplifiedSelectCandidate(candidateId) {
    await originalSelectCandidate(candidateId);
    await autoConfirmKnownClientAndPayer(state.detail);
  };

  collectPayload = function simplifiedCollectPayload() {
    const session = state.detail?.session || {};
    const durationChoice = document.getElementById("durationChoiceInput")?.value
      || session.duration_choice
      || durationToChoice(session.approved_duration_minutes || session.duration_minutes)
      || "60";
    const customMinutes = document.getElementById("customDurationInput")?.value
      || session.custom_duration_minutes
      || "";
    const approvedMinutes = durationChoice === "custom"
      ? customMinutes
      : (document.getElementById("durationChoiceInput")?.value || session.approved_duration_minutes || session.duration_minutes || durationChoice);
    const participantIds = confirmedParticipantIds();
    const saveFuturePerson = document.getElementById("saveFuturePersonRate")?.checked === true;
    const saveFutureJoint = document.getElementById("saveFutureJointRate")?.checked === true;
    const paymentStatus = document.getElementById("paymentInput")?.value || session.payment_status || "unresolved";

    let rateScope = "session_only";
    let rateScopePersonId = null;
    if (saveFutureJoint && participantIds.length > 1) {
      rateScope = "future_joint";
    } else if (saveFuturePerson && participantIds.length === 1) {
      rateScope = "future_person";
      rateScopePersonId = participantIds[0];
    }

    const billableStatus = ["waived", "not_billable"].includes(paymentStatus) ? "nonbillable" : "approved";
    const approvedRate = document.getElementById("approvedRateInput")?.value
      || centString(session.approved_rate_cents)
      || centString(session.suggested_rate_cents)
      || "";

    return {
      ...collectRelationshipPayload(),
      approved_duration_minutes: approvedMinutes,
      billing_session_type: document.getElementById("billingTypeInput")?.value || session.billing_session_type || "psychotherapy",
      duration_choice: durationChoice,
      custom_duration_minutes: durationChoice === "custom" ? customMinutes : "",
      custom_service_description: document.getElementById("customDescInput")?.value || session.custom_service_description || "",
      custom_service_code: document.getElementById("customCodeInput")?.value || session.custom_service_code || "",
      time_category: document.getElementById("timeCategoryInput")?.value || session.time_category || "standard",
      suggested_rate: centString(session.suggested_rate_cents),
      billing_party_id: state.billingParty ? state.billingParty.billing_party_id : state.detail?.effective_billing_party?.billing_party_id || null,
      approved_rate: approvedRate,
      payment_status: paymentStatus,
      billing_treatment: document.getElementById("billingTreatmentInput")?.value || session.billing_treatment || "",
      billable_status: billableStatus,
      rate_override_reason: document.getElementById("overrideReasonInput")?.value || session.rate_override_reason || "",
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

  save = async function simplifiedSave(approve) {
    await resolveTypedSelections();
    const payload = collectPayload();
    const action = approve ? "approve" : "save";
    try {
      const updated = await api(`/api/review/candidates/${state.selected}/${action}`, {
        method: "POST",
        body: JSON.stringify(payload)
      });
      state.detail = updated;
      state.editSteps = { clients: false, session: false };
      if (approve) state.selected = null;
      await loadList();
      if (!approve) renderInspector(updated);
    } catch (error) {
      alert(error.message);
    }
  };

  const originalWireInspector = wireInspector;
  wireInspector = function patchedWireInspector() {
    originalWireInspector();
    const saveButton = document.getElementById("saveSessionBtn");
    if (saveButton) saveButton.onclick = saveSessionSection;
    const approveButton = document.getElementById("approveBtn");
    if (approveButton) approveButton.onclick = () => save(true);
    simplifyRateControls();
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", simplifyRateControls);
  } else {
    simplifyRateControls();
  }
})();