(() => {
  "use strict";

  const $id = id => document.getElementById(id);
  const ids = () => state.participants.map(p => p.person_id).filter(Boolean);

  function label(select, value, text) {
    const option = select && [...select.options].find(item => item.value === value);
    if (option) option.textContent = text;
  }

  function removeReviewTimeCategoryControls() {
    $id("timeFilter")?.remove();
    $id("timeCategoryInput")?.closest("label.field")?.remove();
  }

  function simplifyConfirmedSessionSummary() {
    const session = state.detail?.session;
    if (!session) return;

    const sessionSection = [...document.querySelectorAll("#inspector .section")]
      .find(section => section.querySelector("h3")?.textContent.trim() === "Session Details");
    const summary = sessionSection?.querySelector(".relationship-summary.success > div");
    if (!summary) return;

    const duration = session.custom_duration_minutes || session.approved_duration_minutes || session.duration_minutes;
    const rate = centString(session.approved_rate_cents || session.suggested_rate_cents);
    summary.textContent = [
      userFacingSessionLabel(
        session.billing_session_type || mapLegacyToType(session),
        session.appointment_status,
        session.custom_service_description || ""
      ),
      `${fmt(duration)} min`,
      money(rate)
    ].join(" • ");
  }

  function polish() {
    const payment = $id("paymentInput");
    if (payment) {
      payment.innerHTML = [
        '<option value="unpaid">Unpaid</option>',
        '<option value="paid_at_session">Paid at time of session</option>',
      ].join("");
      const current = session?.payment_status || "unpaid";
      payment.value = ["unpaid", "paid_at_session"].includes(current) ? current : "unpaid";
    }

    const billable = $id("billableInput");
    if (billable) billable.closest("label.field")?.remove();

    removeReviewTimeCategoryControls();
    simplifyConfirmedSessionSummary();

    if ($id("approveBtn")) $id("approveBtn").textContent = "Final Approve Session";
    if ($id("saveSessionBtn")) $id("saveSessionBtn").textContent = "Save Session";
    if ($id("sessionRateHelp")) {
      $id("sessionRateHelp").textContent = "This rate applies only to this session unless you save it as a future default.";
    }

    const rateScope = $id("rateScope");
    if (rateScope && !rateScope.dataset.simplified) {
      const participantCount = ids().length;
      rateScope.dataset.simplified = "true";
      rateScope.innerHTML = participantCount === 1
        ? '<label class="checkbox-field wide"><input type="checkbox" id="saveFuturePersonRate"><span>Save as this client’s future default rate</span></label>'
        : participantCount > 1
          ? '<label class="checkbox-field wide"><input type="checkbox" id="saveFutureJointRate"><span>Save as the future rate for these clients together</span></label>'
          : '<div class="help">Future defaults can be managed in Rate Card after the clients are confirmed.</div>';
    }
  }

  const oldRenderInspector = renderInspector;
  renderInspector = data => {
    oldRenderInspector(data);
    polish();
  };

  renderRows = (items, total) => {
    $id("resultCount").textContent = `Showing ${items.length ? state.offset + 1 : 0} to ${state.offset + items.length} of ${total} results`;
    $id("candidateRows").innerHTML = items.map(item => `
      <tr data-id="${item.candidate_id}" class="${state.selected === item.candidate_id ? "selected" : ""}">
        <td><span class="dot ${statusColor(item.status, item.classification)}"></span>${calendarBadge(item)}</td>
        <td>${fmt(item.date)}</td>
        <td>${fmt(item.time)}</td>
        <td>${fmt(item.raw_title)}</td>
        <td><span class="primary">${fmt(item.suggested_client)}</span></td>
        <td>${fmt(item.duration_minutes)}</td>
        <td>${userFacingSessionLabel(item.billing_session_type || item.service_mode, item.appointment_status, item.custom_service_description || "")}</td>
        <td>${money(item.rate)}</td>
        <td><span class="confidence ${item.authority_score >= 60 ? "good" : "low"}">${item.authority_score || 0}%</span></td>
      </tr>
    `).join("");
    document.querySelectorAll("#candidateRows tr").forEach(row => {
      row.addEventListener("click", () => selectCandidate(row.dataset.id));
    });
  };

  const timeCategoryHeader = [...document.querySelectorAll(".review-table thead th")]
    .find(header => header.textContent.trim() === "Time Cat.");
  timeCategoryHeader?.remove();

  loadList = async () => {
    const query = new URLSearchParams({
      q: $id("searchBox").value,
      review_status: $id("statusFilter").value,
      billing_session_type: $id("serviceFilter").value,
      calendar_filter: $id("calendarFilter").value,
      limit: state.limit,
      offset: state.offset
    });
    const data = await api(`/api/review/candidates?${query}`);
    state.items = data.items;
    renderStatus(data.status);
    renderRows(data.items, data.total);
    if (!data.items.some(item => item.candidate_id === state.selected)) state.selected = null;
    if (!state.selected && data.items.length) await selectCandidate(data.items[0].candidate_id);
    if (!data.items.length) $id("inspector").innerHTML = '<div class="empty-state">No sessions need review.</div>';
  };

  collectPayload = () => {
    const session = state.detail?.session || {};
    const durationChoice = $id("durationChoiceInput")?.value
      || session.duration_choice
      || durationToChoice(session.approved_duration_minutes || session.duration_minutes)
      || "60";
    const customMinutes = $id("customDurationInput")?.value || session.custom_duration_minutes || "";
    const approvedMinutes = durationChoice === "custom"
      ? customMinutes
      : ($id("durationChoiceInput")?.value || session.approved_duration_minutes || session.duration_minutes || durationChoice);
    const participantIds = ids();
    const futurePerson = $id("saveFuturePersonRate")?.checked === true;
    const futureJoint = $id("saveFutureJointRate")?.checked === true;
    const paymentStatus = $id("paymentInput")?.value || session.payment_status || "unpaid";

    let rateScope = "session_only";
    let rateScopePersonId = null;
    if (futureJoint && participantIds.length > 1) {
      rateScope = "future_joint";
    } else if (futurePerson && participantIds.length === 1) {
      rateScope = "future_person";
      rateScopePersonId = participantIds[0];
    }

    const rate = $id("approvedRateInput")?.value
      || centString(session.approved_rate_cents)
      || centString(session.suggested_rate_cents)
      || "";

    return {
      ...collectRelationshipPayload(),
      approved_duration_minutes: approvedMinutes,
      billing_session_type: $id("billingTypeInput")?.value || session.billing_session_type || "psychotherapy",
      duration_choice: durationChoice,
      custom_duration_minutes: durationChoice === "custom" ? customMinutes : "",
      custom_service_description: $id("customDescInput")?.value || session.custom_service_description || "",
      custom_service_code: $id("customCodeInput")?.value || session.custom_service_code || "",
      time_category: session.time_category || "standard",
      suggested_rate: centString(session.suggested_rate_cents),
      billing_party_id: state.billingParty?.billing_party_id || state.detail?.effective_billing_party?.billing_party_id || null,
      approved_rate: rate,
      payment_status: paymentStatus,
      billing_treatment: $id("billingTreatmentInput")?.value || session.billing_treatment || "",
      billable_status: paymentStatus === "paid_at_session" ? "nonbillable" : "approved",
      rate_override_reason: $id("overrideReasonInput")?.value || session.rate_override_reason || "",
      rate_scope: rateScope,
      rate_scope_person_id: rateScopePersonId
    };
  };

  saveSessionSection = async () => {
    const button = $id("saveSessionBtn");
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
      requestAnimationFrame(() => $id("approveBtn")?.scrollIntoView({ behavior: "smooth", block: "center" }));
    } catch (error) {
      alert(`Could not save session: ${error.message}`);
    } finally {
      if (button && document.body.contains(button)) button.disabled = false;
    }
  };

  save = async approve => {
    await resolveTypedSelections();
    try {
      const updated = await api(`/api/review/candidates/${state.selected}/${approve ? "approve" : "save"}`, {
        method: "POST",
        body: JSON.stringify(collectPayload())
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

  const oldWireInspector = wireInspector;
  wireInspector = () => {
    oldWireInspector();
    if ($id("saveSessionBtn")) $id("saveSessionBtn").onclick = saveSessionSection;
    if ($id("approveBtn")) $id("approveBtn").onclick = () => save(true);
    polish();
  };

  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", polish)
    : polish();
})();
