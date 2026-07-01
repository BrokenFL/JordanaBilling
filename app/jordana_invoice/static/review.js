const { api, sanitizeUiErrorMessage, getWriteToken } = window.JordanaAPI;
const { create: createOverlay } = window.JordanaOverlay;

const approvalState = { submitting: false, candidateId: null };
const duplicateState = { submitting: false, candidateId: null };
const restoreState = { submitting: false, candidateId: null };
const billingWizardState = { submitting: false };

let paymentOverlayReturnFocus = null;
let paymentDetailReturnFocus = null;

const state = {
  items: [],
  selected: null,
  offset: 0,
  limit: 25,
  participants: [],
  account: null,
  billingParty: null,
  dirty: new Set(),
  returnCandidate: null,
  returnContext: null,
  detail: null,
  invoice: null,
  eligibleSessions: [],
  sessions: { items: [], offset: 0, limit: 30, total: 0 },
  editSteps: { clients: false, session: false },
  settingsSaving: false,
  syncRunning: false,
  rateCard: {
    mode: "create",
    replacingRuleId: null,
    resolvedPerson: null,
    resolvedAccount: null,
    participantSelections: [],
    scopeResults: []
  },
  currentPersonId: null,
  personShowAllSessions: false,
  billingSetupSaving: false,
  finalizeInProgress: false,
  unpaid: {
    items: [],
    selectedInvoiceId: null,
    paymentHistory: [],
    submitting: false
  },
  payments: {
    activeTab: "outstanding",
    paidItems: [],
    allPaymentsItems: [],
    selectedPaidInvoiceId: null,
    selectedPaymentId: null
  },
  financialSummary: {
    month: "",
    data: null
  },
  invoiceLibrary: {
    items: [],
    total: 0,
    offset: 0,
    limit: 50,
    search: "",
    status: "",
    paymentStatus: "",
    billToPartyId: "",
    dateFilter: "",
    dateFrom: "",
    dateTo: "",
    loaded: false
  }
};
const RETURN_CONTEXT_KEY = "reviewBillingReturnContext";
const BUSINESS_PROFILE_DEFAULTS = {
  business_name: "",
  provider_display_name: "",
  credentials_display: "",
  address_line_1: "",
  address_line_2: "",
  city: "",
  state: "",
  postal_code: "",
  phone: "",
  email: "",
  payee_name: "",
  payment_address_line_1: "",
  payment_address_line_2: "",
  payment_city: "",
  payment_state: "",
  payment_postal_code: "",
  zelle_recipient: "",
  logo_path: "",
  logo_contains_business_details: false,
  show_email_below_logo: false,
  invoice_total_label: "TOTAL DUE",
  invoice_number_format: "YYYY-NNNN",
  insurance_ein: "",
  insurance_npi: "",
  insurance_sw: "",
};

const $ = (id) => document.getElementById(id);
const fmt = (v) => v ? escapeHtml(v) : "-";
const money = (v) => v ? `$${v}` : "—";
const fmtDateTime = (v) => v ? new Date(v).toLocaleString([], { month:"short", day:"numeric", hour:"numeric", minute:"2-digit" }) : "-";
const billingTypeLabel = (v, customDescription = "") => {
  if (v === "custom" && customDescription) return escapeHtml(customDescription);
  return ({psychotherapy:"Psychotherapy Session", psychotherapy_house_call:"Psychotherapy Session / House Call", psychotherapy_weekend:"Psychotherapy Session / Weekend", psychotherapy_evening:"Psychotherapy Session / Evening", custom:"Custom"}[v] || escapeHtml(v) || "Psychotherapy Session");
};
const appointmentStatusRuleLabel = (v) => ({scheduled:"Scheduled", cancelled:"Cancelled", no_show:"No-Show"}[v] || escapeHtml(v) || "Scheduled");
const userFacingSessionLabel = (billingType, appointmentStatus = "", customDescription = "") => {
  const specialBase = {
    psychotherapy: "Psychotherapy Session",
    psychotherapy_house_call: "House Call Psychotherapy Session",
    psychotherapy_weekend: "Weekend Psychotherapy Session",
    psychotherapy_evening: "Evening Psychotherapy Session",
    custom: escapeHtml(customDescription) || "Custom"
  };
  const defaultBase = billingTypeLabel(billingType, customDescription);
  const base = ["cancelled", "no_show"].includes(appointmentStatus)
    ? (specialBase[billingType] || defaultBase)
    : defaultBase;
  if (appointmentStatus === "cancelled") return `Cancelled ${base}`;
  if (appointmentStatus === "no_show") return `No-Show ${base}`;
  return defaultBase;
};
const billingTypeShort = (v, customDescription = "") => {
  if (v === "custom" && customDescription) return escapeHtml(customDescription);
  return ({psychotherapy:"Standard", psychotherapy_house_call:"House Call", psychotherapy_weekend:"Weekend", psychotherapy_evening:"Evening", custom:"Custom"}[v] || escapeHtml(v) || "Standard");
};
const appointmentMethodLabel = (v) => ({phone:"Phone", facetime:"FaceTime", office:"Office", unknown:"Unknown"}[v] || escapeHtml(v) || "Unknown");
const serviceLabel = (v) => ({phone:"Phone", facetime:"FaceTime", office:"Office", house_call:"House Call", unknown:"Unknown"}[v] || escapeHtml(v) || "Unknown");
const timeLabel = (v) => ({standard:"Standard", evening:"Evening", weekend:"Weekend", weekend_evening:"Weekend + Evening"}[v] || escapeHtml(v) || "Standard");
const participantState = (p) => ({
  person_id: p.person_id,
  display_name: p.display_name || p.participant_name,
  participant_name: p.participant_name,
  first_name: p.first_name || "",
  last_name: p.last_name || "",
  billing_email: p.billing_email || "",
  billing_phone: p.billing_phone || "",
  is_primary: !!p.is_primary,
  is_proposed: !!p.is_proposed,
  source: p.source || "",
  relationship_role: p.relationship_role
});

function currentLocalMonth() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

function summaryMoney(value) {
  return money(centString(Number(value) || 0));
}

function renderFinancialSummary(data) {
  const values = {
    invoiceDraftValue: data.draft_invoice_value_cents,
    invoiceFinalizedValue: data.finalized_invoice_value_for_month_cents,
    invoiceOutstandingValue: data.outstanding_balance_cents,
    paymentsInvoicedValue: data.finalized_invoice_value_for_month_cents,
    paymentsReceivedValue: data.payments_received_for_month_cents,
    paymentsOutstandingValue: data.outstanding_balance_cents,
  };
  Object.entries(values).forEach(([id, value]) => {
    const node = $(id);
    if (node) node.textContent = summaryMoney(value);
  });
}

async function loadFinancialSummary() {
  const month = state.financialSummary.month || currentLocalMonth();
  state.financialSummary.month = month;
  ["invoiceSummaryMonth", "paymentsSummaryMonth"].forEach(id => {
    const input = $(id);
    if (!input) return;
    input.value = month;
    input.onchange = async () => {
      if (!input.value) return;
      state.financialSummary.month = input.value;
      await loadFinancialSummary();
    };
  });
  try {
    const data = await api(`/api/financial-summary?month=${encodeURIComponent(month)}`);
    state.financialSummary.data = data;
    renderFinancialSummary(data);
  } catch (_) {
    state.financialSummary.data = null;
    ["invoiceDraftValue", "invoiceFinalizedValue", "invoiceOutstandingValue", "paymentsInvoicedValue", "paymentsReceivedValue", "paymentsOutstandingValue"].forEach(id => {
      const node = $(id);
      if (node) node.textContent = "Unavailable";
    });
  }
}

async function loadList() {
  const params = new URLSearchParams({
    q: $("searchBox").value,
    review_status: $("statusFilter").value,
    billing_session_type: $("serviceFilter").value,
    calendar_filter: $("calendarFilter").value,
    limit: state.limit,
    offset: state.offset
  });
  const data = await api(`/api/review/candidates?${params}`);
  state.items = data.items;
  renderStatus(data.status);
  renderRows(data.items, data.total);
  if (!data.items.some(item => item.candidate_id === state.selected)) state.selected = null;
}

function renderStatus(s) {
  $("demoBanner").hidden = !s.demo_mode;
  $("lastSync").textContent = fmtDateTime(s.last_sync);
  $("needsReview").textContent = s.needs_review;
  $("navNeeds").textContent = s.needs_review;
  $("readyApprove").textContent = s.ready_to_approve;
  $("approvedMonth").textContent = s.approved_this_month;
  $("personalAdmin").textContent = s.personal_admin;
}

async function refreshDashboardStatus() {
  const status = await api("/api/status");
  renderStatus(status);
}

function renderRows(items, total) {
  $("resultCount").textContent = `Showing ${items.length ? state.offset + 1 : 0} to ${state.offset + items.length} of ${total} results`;
  $("candidateRows").innerHTML = items.length ? items.map(item => `
    <tr data-id="${escapeAttr(item.candidate_id)}" class="${state.selected === item.candidate_id ? "selected" : ""}">
      <td><span class="dot ${statusColor(item.status, item.classification)}"></span>${calendarBadge(item)}</td>
      <td>${fmt(item.date)}</td>
      <td>${fmt(item.time)}</td>
      <td><span class="primary">${fmt(item.suggested_client)}</span></td>
      <td>${fmt(item.raw_title)}</td>
      <td>${fmt(item.duration_minutes)}</td>
      <td>${money(item.rate)}</td>
      <td><button class="review-btn" data-review-id="${escapeAttr(item.candidate_id)}">Review</button></td>
    </tr>
  `).join("") : '<tr class="empty-row"><td colspan="8">No sessions need review.</td></tr>';
  document.querySelectorAll("#candidateRows tr[data-id]").forEach(row => {
    row.addEventListener("click", (e) => {
      if (e.target.closest("button") || e.target.closest("a")) return;
      if (reviewOverlayCtrl) reviewOverlayCtrl.setReturnFocus(row);
      selectCandidate(row.dataset.id);
    });
    const reviewBtn = row.querySelector(".review-btn");
    if (reviewBtn) reviewBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (reviewOverlayCtrl) reviewOverlayCtrl.setReturnFocus(reviewBtn);
      selectCandidate(row.dataset.id);
    });
  });
}

function statusColor(status, classification) {
  if (status === "approved") return "green";
  if (status === "excluded" || ["personal", "administrative", "nonbillable"].includes(classification)) return "gray";
  if (status === "ready_for_approval") return "amber";
  return "red";
}

async function selectCandidate(candidateId) {
  state.selected = candidateId;
  state.editSteps = { clients: false, session: false };
  document.querySelectorAll("#candidateRows tr").forEach(row => row.classList.toggle("selected", row.dataset.id === candidateId));
  const data = await api(`/api/review/candidates/${candidateId}`);
  state.detail = data;
  state.participants = data.participants.map(participantState);
  state.account = data.account;
  state.billingParty = data.billing_party || data.effective_billing_party;
  renderInspector(data);
  openReviewOverlay();
}

function renderInspector(data) {
  const s = data.session;
  const isSession = Boolean(s.id);
  const readiness = data.readiness || {};
  const effectiveBillingParty = data.effective_billing_party || data.billing_party;
  const clientsEditing = state.editSteps.clients;
  const sessionEditing = state.editSteps.session;
  const clientsLocked = !readiness.clients_ready;
  const billingLocked = !readiness.clients_ready;
  const sessionLocked = !readiness.clients_ready || !readiness.billing_ready;
  const currentRate = centString(s.approved_rate_cents || s.suggested_rate_cents);
  const suggestedRate = centString(s.suggested_rate_cents);
  const rateChanged = currentRate !== suggestedRate && currentRate !== "";
  const showCancellation = ["cancelled", "no_show"].includes(s.appointment_status);
  const showSessionSave = !sessionLocked && (!readiness.session_ready || state.dirty.has("session"));
  const showRelationshipSave = !readiness.clients_ready || state.dirty.has("relationship");
  const showBillingSave = !billingLocked && (!readiness.billing_ready || state.dirty.has("billing"));
  const confirmedDuration = s.custom_duration_minutes || s.approved_duration_minutes || s.duration_minutes;
  const confirmedRate = centString(s.approved_rate_cents || s.suggested_rate_cents);
  const participantIds = state.participants.map(p => p.person_id).filter(Boolean);
  const overlayContent = $("reviewOverlayContent");
  if (!overlayContent) return;
  overlayContent.innerHTML = `
    <div class="inspector-header">
      <div>
        <h2>${fmt(s.raw_calendar_title || s.title)}</h2>
        <div class="meta"><span>${fmt(s.session_date)}</span><span>${fmt(startRange(s))}</span><span>${fmt(s.duration_minutes)} min</span><span>${calendarLabel(s)}</span><span>${appointmentBadge(s.appointment_status)}</span></div>
      </div>
      <div><span class="badge">${fmt(s.review_status).replaceAll("_", " ")}</span><div class="confidence ${s.authority_score >= 60 ? "good" : "low"}">Review confidence: ${s.authority_score || 0}%</div><div class="help">${(s.authority_reasons || []).map(escapeHtml).join(", ")}</div></div>
    </div>
    ${titleTimeWarning(s)}

    <section class="section">
      <details>
      <summary class="section-summary">View Calendar Evidence</summary>
      <div class="kv">
        <label>Raw Title</label><strong>${fmt(s.raw_calendar_title || s.title)}</strong>
        <label>Calendar</label><span>${fmt(s.calendar_name)}</span>
        <label>Calendar Disposition</label><span>${calendarDispositionLabel(s.calendar_disposition)}</span>
        <label>Original Start</label><span>${fmt(s.start_at)}</span>
        <label>Original End</label><span>${fmt(s.end_at)}</span>
        <label>Parsed Title Time</label><span>${fmt(s.title_time_text)} ${s.title_time_normalized ? `(${escapeHtml(s.title_time_normalized)})` : ""}</span>
        <label>Calendar Duration</label><span>${fmt(s.calendar_duration_minutes || s.duration_minutes)} minutes</span>
        <label>Notes</label><span>${fmt(s.notes)}</span>
        <label>Captured</label><span>${fmt(s.captured_at)}</span>
      </div>
      <details><summary>Raw payload</summary><pre class="evidence-raw">${escapeHtml(s.raw_json || "")}</pre></details>
      </details>
    </section>

    <section class="section">
      <div class="section-title-row"><h3>Clients in this session</h3><span class="save-state" id="relationshipState">Needs review</span></div>
      <div class="help">Clients attending this session</div>
      ${readiness.clients_ready && !clientsEditing
        ? `<div class="relationship-summary success"><strong>Confirmed</strong><div>${state.participants.map(p => fmt(p.display_name || p.participant_name)).join(", ")}</div></div>`
        : `<div class="chips" id="participantChips"></div>
           <div class="combobox"><input id="personInput" placeholder="Search or add a client..." list="peopleList"><button class="mini" id="addPerson">+</button></div>
           <datalist id="peopleList"></datalist>
           <div id="personWarning"></div>
           <div id="personEditor" class="drawer" hidden></div>`}
      <div class="inline-actions">
        ${readiness.clients_ready && !clientsEditing
          ? '<button id="changeClientsBtn">Change</button>'
          : '<button id="saveRelationshipBtn" class="save">Confirm Client(s)</button>'}
      </div>
    </section>

    <section class="section">
      <div class="section-title-row"><h3>Bill to</h3><span class="save-state" id="billingState">Needs review</span></div>
      <div class="help">Choose which confirmed client should receive and pay the invoice.</div>
      ${billingLocked
        ? `<div class="readonly-note">Confirm Client(s) first.</div>`
        : readiness.billing_ready
          ? `${billToSummary(data)}
             <div class="inline-actions"><button id="editBillingRelationship">Change payer or shared billing</button></div>`
          : `<label class="field wide">Bill to client<select id="billToClientSelect">${billToClientOptions(data)}</select></label>
             <div class="inline-actions">
               <button id="editBillingRelationship">Change payer or shared billing</button>
               ${showBillingSave ? '<button id="saveBillingBtn" class="save">Save Bill To</button>' : ""}
             </div>`}
    </section>

    <section class="section">
      <div class="section-title-row"><h3>Session Details</h3><span class="save-state" id="sessionState">Needs review</span></div>
      ${sessionLocked
        ? `<div class="readonly-note">${!readiness.clients_ready ? "Confirm Client(s) first." : "Confirm Bill To first."}</div>`
        : readiness.session_ready && !sessionEditing
          ? `<div class="relationship-summary success"><strong>Confirmed</strong><div>${userFacingSessionLabel(s.billing_session_type || mapLegacyToType(s), s.appointment_status, s.custom_service_description || "")} • ${fmt(confirmedDuration)} min • ${money(confirmedRate)}</div></div>
             <div class="inline-actions"><button id="changeSessionBtn">Change</button></div>`
          : `<div class="field-grid">
               <label class="field">Session Type<select id="billingTypeInput">${billingTypeOptions(s.billing_session_type || mapLegacyToType(s))}</select></label>
               <label class="field">Duration<select id="durationChoiceInput">${durationOptions(s.duration_choice || durationToChoice(s.approved_duration_minutes || s.duration_minutes))}</select></label>
               <label class="field" id="customDurationField" ${(s.duration_choice === "custom" || !["30","60","90","120"].includes(String(s.approved_duration_minutes || s.duration_minutes))) ? "" : "hidden"}>Custom Minutes<input id="customDurationInput" type="number" min="1" value="${escapeAttr(s.custom_duration_minutes || s.approved_duration_minutes || s.duration_minutes || "")}"></label>
               <label class="field" id="customDescField" ${s.billing_session_type === "custom" ? "" : "hidden"}>Custom Description<input id="customDescInput" value="${escapeAttr(s.custom_service_description || "")}"></label>
               <label class="field" id="customCodeField" ${s.billing_session_type === "custom" ? "" : "hidden"}>Custom Code<input id="customCodeInput" value="${escapeAttr(s.custom_service_code || "")}"></label>
               <label class="field">Rate for this session<input id="approvedRateInput" value="${escapeAttr(currentRate)}"><span class="help" id="sessionRateHelp">This rate applies only to this session unless you save it as a future default.</span><span class="help" id="sessionRatePreview"></span></label>
               <label class="field">Payment Handling<select id="paymentInput"><option value="unpaid" ${s.payment_status === "unpaid" ? "selected" : ""}>Invoice billing</option><option value="paid_at_session" ${s.payment_status === "paid_at_session" ? "selected" : ""}>Paid at session</option></select></label>
               <div class="field wide" id="paidAtSessionSection" ${s.payment_status === "paid_at_session" ? "" : "hidden"} style="background: rgba(0,0,0,0.02); padding: 12px; border-radius: 6px; border: 1px solid rgba(0,0,0,0.1); margin-top: 8px;">
                 <h4 style="margin: 0 0 8px 0;">Paid at Session Details</h4>
                 <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px;">
                   <label class="field">Amount received ($)<input id="paymentAmountInput" type="text" value="${escapeAttr(currentRate)}"></label>
                   <label class="field">Payment Date<input id="paymentDateInput" type="date" value="${escapeAttr(s.session_date || (s.start_at ? s.start_at.substring(0, 10) : ''))}"></label>
                   <label class="field">Payment Method<select id="paymentMethodInput"><option value="" selected disabled>Select method...</option><option value="zelle">Zelle</option><option value="check">Check</option><option value="cash">Cash</option><option value="ach">ACH</option><option value="card">Card</option><option value="other">Other</option></select></label>
                   <label class="field">Reference #<input id="paymentRefInput" placeholder="Optional"></label>
                   <label class="field">Admin Note<input id="paymentNoteInput" placeholder="Optional"></label>
                 </div>
               </div>
               ${showCancellation ? `<label class="field">Cancellation/No-Show Billing<select id="billingTreatmentInput">${optionSet(["unresolved","billable","not_billable","waived"], s.billing_treatment || "billable")}</select></label>` : ""}
               <details class="field wide"><summary>Advanced</summary><div class="field-grid"><label class="field">Appointment Method<span class="readonly-value">${appointmentMethodLabel(s.appointment_method || s.service_mode)}</span></label></div></details>
               ${rateChanged ? `<label class="field wide">Override Reason<input id="overrideReasonInput" value="${escapeAttr(s.rate_override_reason || "")}"></label>` : ""}
             </div>
             ${houseCallSuggestion(s)}
             ${rateChanged ? `<div class="rate-scope" id="rateScope">
               ${participantIds.length === 1
                 ? '<label class="checkbox-field wide"><input type="checkbox" id="saveFuturePersonRate"><span>Save as this client\u2019s future default rate</span></label>'
                 : participantIds.length > 1
                   ? '<label class="checkbox-field wide"><input type="checkbox" id="saveFutureJointRate"><span>Save as the future rate for these clients together</span></label>'
                   : '<div class="help">Future defaults can be managed in Rate Card after the clients are confirmed.</div>'}
             </div>` : ""}
             <div class="inline-actions">${showSessionSave ? '<button id="saveSessionBtn" class="save">Save Session</button>' : ""}</div>`}
    </section>

    <section class="section">
      <details>
        <summary class="section-summary">Shared billing and relationships</summary>
        <div id="relationshipEditor" class="drawer"></div>
      </details>
      <div class="hint">Suggestion reasons: ${(s.authority_reasons || []).map(escapeHtml).join(", ") || safeList(s.review_reasons).map(escapeHtml).join(" ") || escapeHtml(s.explanation) || "Calendar title matched the parser pattern."}</div>
    </section>

    <section class="section">
      <h3>Review Checklist</h3>
      <div class="checklist">${data.checklist.map(c => `<div class="check ${c.resolved ? "done" : ""}"><span></span><label>${escapeHtml(c.label)}</label></div>`).join("")}</div>
    </section>

    <div class="actions">
      <button id="prevSessionBtn">Previous</button>
      ${isSession && readiness.all_ready ? '<button class="approve" id="approveBtn">Approve Session</button>' : ""}
      ${!isSession ? '<button class="approve" id="sendToReviewBtn">Send to Review</button>' : ""}
      <button id="saveNextBtn" class="save">Save and next</button>
      <button id="personalBtn">Mark Personal/Admin</button>
      <button id="duplicateBtn">Confirm Duplicate & Next</button>
      <button class="danger" id="excludeBtn">Exclude</button>
    </div>
  `;
  wireInspector();
  renderParticipantChips();
  renderRelationshipEditor(data);
}

function wireInspector() {
  if ($("personInput")) $("personInput").addEventListener("input", debounce(async e => fillDatalist("peopleList", await api(`/api/people?q=${encodeURIComponent(e.target.value)}`), "display_name"), 160));
  if ($("addPerson")) $("addPerson").onclick = createPersonFromInput;
  if ($("approveBtn")) $("approveBtn").onclick = () => save(true);
  if ($("saveRelationshipBtn")) $("saveRelationshipBtn").onclick = saveRelationshipSection;
  if ($("changeClientsBtn")) $("changeClientsBtn").onclick = () => { state.editSteps.clients = true; markDirty("relationship"); renderInspector(state.detail); };
  if ($("saveBillingBtn")) $("saveBillingBtn").onclick = saveBillingSection;
  if ($("changeSessionBtn")) $("changeSessionBtn").onclick = () => { state.editSteps.session = true; markDirty("session"); renderInspector(state.detail); };
  if ($("saveSessionBtn")) $("saveSessionBtn").onclick = saveSessionSection;
  if ($("editBillingRelationship")) $("editBillingRelationship").onclick = openBillingRelationshipEditor;
  if ($("personalBtn")) $("personalBtn").onclick = () => mark("personal");
  if ($("duplicateBtn")) $("duplicateBtn").onclick = confirmDuplicateAndNext;
  if ($("excludeBtn")) $("excludeBtn").onclick = () => mark("nonbillable");
  if ($("sendToReviewBtn")) $("sendToReviewBtn").onclick = sendToReview;
  if ($("prevSessionBtn")) $("prevSessionBtn").onclick = goToPreviousSession;
  if ($("saveNextBtn")) $("saveNextBtn").onclick = saveAndNext;
  [
    "billingTypeInput",
    "durationChoiceInput",
    "customDurationInput",
    "customDescInput",
    "customCodeInput",
    "approvedRateInput",
    "paymentInput",
    "billingTreatmentInput",
    "overrideReasonInput",
    "paymentAmountInput",
    "paymentDateInput",
    "paymentMethodInput",
    "paymentRefInput",
    "paymentNoteInput"
  ].forEach(id => {
    const element = $(id);
    if (element) element.addEventListener("input", async () => {
      markDirty("session");
      syncSessionCustomFields();
      await updateSessionRatePreview();
    });
  });
  if ($("billToClientSelect")) $("billToClientSelect").addEventListener("input", () => markDirty("billing"));
  syncSessionCustomFields();
  updateSessionRatePreview();
}

function syncSessionCustomFields() {
  const billingType = $("billingTypeInput")?.value;
  const durationChoice = $("durationChoiceInput")?.value;
  if ($("customDurationField")) $("customDurationField").hidden = durationChoice !== "custom";
  if ($("customDescField")) $("customDescField").hidden = billingType !== "custom";
  if ($("customCodeField")) $("customCodeField").hidden = billingType !== "custom";

  const paymentHandling = $("paymentInput")?.value;
  const isPaidAtSession = paymentHandling === "paid_at_session";
  const paidAtSessionSection = $("paidAtSessionSection");
  if (paidAtSessionSection) {
    paidAtSessionSection.hidden = !isPaidAtSession;
    
    const paymentAmountInput = $("paymentAmountInput");
    const paymentDateInput = $("paymentDateInput");
    const paymentMethodInput = $("paymentMethodInput");
    const paymentRefInput = $("paymentRefInput");
    const paymentNoteInput = $("paymentNoteInput");

    if (paymentAmountInput) {
      paymentAmountInput.disabled = !isPaidAtSession;
      if (!isPaidAtSession) paymentAmountInput.value = "";
    }
    if (paymentDateInput) {
      paymentDateInput.disabled = !isPaidAtSession;
      if (!isPaidAtSession) paymentDateInput.value = "";
    }
    if (paymentMethodInput) {
      paymentMethodInput.disabled = !isPaidAtSession;
      if (!isPaidAtSession) paymentMethodInput.value = "";
    }
    if (paymentRefInput) {
      paymentRefInput.disabled = !isPaidAtSession;
      if (!isPaidAtSession) paymentRefInput.value = "";
    }
    if (paymentNoteInput) {
      paymentNoteInput.disabled = !isPaidAtSession;
      if (!isPaidAtSession) paymentNoteInput.value = "";
    }
  }
}

function markDirty(section) {
  state.dirty.add(section);
  if (section === "session" && $("sessionState")) {
    $("sessionState").textContent = "Unsaved changes";
    $("sessionState").className = "save-state dirty";
  }
  if (section === "relationship" && $("relationshipState")) {
    $("relationshipState").textContent = "Unsaved changes";
    $("relationshipState").className = "save-state dirty";
  }
  if (section === "billing" && $("billingState")) {
    $("billingState").textContent = "Unsaved changes";
    $("billingState").className = "save-state dirty";
  }
}

function markSaved(section, message = "Saved") {
  state.dirty.delete(section);
  const id = section === "session" ? "sessionState" : section === "billing" ? "billingState" : "relationshipState";
  if ($(id)) {
    $(id).textContent = message;
    $(id).className = "save-state saved";
  }
}

function clearSavedState(section, message = "Save failed") {
  const id = section === "session" ? "sessionState" : section === "billing" ? "billingState" : "relationshipState";
  if ($(id)) {
    $(id).textContent = message;
    $(id).className = "save-state dirty";
  }
}

function renderParticipantChips() {
  const chips = $("participantChips");
  if (!chips) return;
  chips.innerHTML = state.participants.map((p, i) => `<span class="chip ${p.is_proposed ? "proposed" : "linked"}">${escapeHtml(p.display_name || p.participant_name)}${p.is_proposed ? '<small>proposed</small>' : ''}<button data-edit="${i}">Edit</button><button data-i="${i}">×</button></span>`).join("");
  document.querySelectorAll("#participantChips button[data-i]").forEach(btn => btn.onclick = () => { state.participants.splice(Number(btn.dataset.i), 1); renderParticipantChips(); renderRelationshipEditor(state.detail); markDirty("relationship"); });
  document.querySelectorAll("#participantChips button[data-edit]").forEach(btn => btn.onclick = () => showPersonEditor(Number(btn.dataset.edit)));
}

function confirmedSessionClients() {
  return state.participants.filter(p => p.person_id);
}

function billToClientOptions(data) {
  const clients = confirmedSessionClients();
  const selectedPersonId = data.effective_billing_party?.person_id || data.billing_party?.person_id || "";
  if (!clients.length) return `<option value="">Confirm Client(s) first</option>`;
  const needsChoice = clients.length > 1 && !selectedPersonId;
  return [
    needsChoice ? `<option value="">Choose payer...</option>` : "",
    ...clients.map(p => {
      const name = p.display_name || p.participant_name || "Unnamed client";
      const selected = p.person_id === selectedPersonId || (!selectedPersonId && clients.length === 1 && p.person_id) ? "selected" : "";
      return `<option value="${escapeAttr(p.person_id)}" ${selected}>${fmt(name)}</option>`;
    })
  ].join("");
}

function billToSummary(data) {
  const billingParty = data.effective_billing_party || data.billing_party;
  if (!billingParty) return "";
  return `<div class="relationship-summary success"><strong>${fmt(billingParty.billing_name)}</strong><div>Saved billing setup</div></div>`;
}

function sessionClientSummary(participants = state.participants) {
  return participants
    .filter(p => p.person_id)
    .map(p => ({
      person_id: p.person_id,
      display_name: p.display_name || p.participant_name || "",
      relationship_role: p.relationship_role || "",
      is_primary: !!p.is_primary
    }));
}

function buildReturnContext() {
  const session = state.detail && state.detail.session ? state.detail.session : null;
  if (!state.selected || !session || !session.id) return null;
  return {
    returnView: "review",
    candidateId: state.selected,
    sessionId: session.id,
    accountId: state.account ? state.account.account_id : "",
    billingPartyId: state.billingParty ? state.billingParty.billing_party_id : "",
    billToPersonId: state.billingParty ? state.billingParty.person_id || "" : "",
    participants: sessionClientSummary()
  };
}

function validReturnContext(value) {
  return !!(value && typeof value.candidateId === "string" && value.candidateId && typeof value.sessionId === "string" && value.sessionId);
}

function returnContextHash(context) {
  if (!validReturnContext(context)) return "#clients";
  const params = new URLSearchParams({
    returnView: context.returnView || "review",
    candidateId: context.candidateId,
    sessionId: context.sessionId
  });
  if (context.accountId) params.set("accountId", context.accountId);
  if (context.billToPersonId) params.set("billToPersonId", context.billToPersonId);
  if (context.billingPartyId) params.set("billingPartyId", context.billingPartyId);
  const participantIds = (context.participants || []).map(p => p.person_id).filter(Boolean);
  if (participantIds.length) params.set("participantIds", participantIds.join(","));
  return `#clients?${params.toString()}`;
}

function persistReturnContext(context) {
  if (!validReturnContext(context)) {
    clearReturnContext();
    return null;
  }
  state.returnContext = context;
  try {
    sessionStorage.setItem(RETURN_CONTEXT_KEY, JSON.stringify(context));
  } catch (_) {}
  return context;
}

function hashReturnContext() {
  const raw = location.hash.startsWith("#") ? location.hash.slice(1) : "";
  const [view, query] = raw.split("?");
  if (view !== "clients" || !query) return null;
  const params = new URLSearchParams(query);
  const candidateId = params.get("candidateId") || "";
  const sessionId = params.get("sessionId") || "";
  if (!candidateId || !sessionId) return null;
  return {
    returnView: params.get("returnView") || "review",
    candidateId,
    sessionId,
    accountId: params.get("accountId") || "",
    billingPartyId: params.get("billingPartyId") || "",
    billToPersonId: params.get("billToPersonId") || "",
    participants: []
  };
}

function readReturnContext() {
  if (validReturnContext(state.returnContext)) return state.returnContext;
  const fromHash = hashReturnContext();
  let fromStorage = null;
  try {
    fromStorage = JSON.parse(sessionStorage.getItem(RETURN_CONTEXT_KEY) || "null");
  } catch (_) {
    fromStorage = null;
  }
  if (validReturnContext(fromStorage)) {
    if (fromHash && fromHash.candidateId === fromStorage.candidateId && fromHash.sessionId === fromStorage.sessionId) {
      fromStorage.accountId = fromHash.accountId || fromStorage.accountId || "";
      fromStorage.billingPartyId = fromHash.billingPartyId || fromStorage.billingPartyId || "";
      fromStorage.billToPersonId = fromHash.billToPersonId || fromStorage.billToPersonId || "";
    }
    state.returnContext = fromStorage;
    return fromStorage;
  }
  if (fromHash) {
    state.returnContext = fromHash;
    return fromHash;
  }
  return null;
}

function clearReturnContext() {
  state.returnContext = null;
  try {
    sessionStorage.removeItem(RETURN_CONTEXT_KEY);
  } catch (_) {}
  if (location.hash.startsWith("#clients?")) location.hash = "#clients";
}


function splitDisplayName(name) {
  const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
  return { first: parts[0] || "", last: parts.slice(1).join(" ") };
}

function normalizeParticipantName(name) {
  return String(name || "").trim().toLowerCase().split(/\s+/).filter(Boolean).join(" ");
}

function replaceMatchingProposedParticipant(person) {
  const normalizedTarget = normalizeParticipantName(person.display_name);
  const proposedIndex = state.participants.findIndex(participant =>
    participant.is_proposed &&
    normalizeParticipantName(participant.display_name || participant.participant_name) === normalizedTarget
  );
  const nextParticipant = {
    person_id: person.person_id,
    display_name: person.display_name,
    participant_name: person.display_name,
    first_name: person.first_name || "",
    last_name: person.last_name || "",
    billing_email: person.billing_email || "",
    billing_phone: person.billing_phone || "",
    is_primary: proposedIndex >= 0 ? !!state.participants[proposedIndex].is_primary : state.participants.length === 0,
    is_proposed: false
  };
  if (proposedIndex >= 0) {
    state.participants[proposedIndex] = { ...state.participants[proposedIndex], ...nextParticipant };
    return true;
  }
  state.participants.push(nextParticipant);
  return false;
}

async function createPersonFromInput() {
  const name = $("personInput").value.trim();
  if (!name) return;
  const rows = await api(`/api/people?q=${encodeURIComponent(name)}`);
  const exact = rows.find(row => normalizeParticipantName(row.display_name) === normalizeParticipantName(name));
  if (!exact) {
    state.participants.push({ display_name: name, participant_name: name, is_primary: state.participants.length === 0, is_proposed: true, source: "manual" });
    $("personInput").value = "";
    renderParticipantChips();
    renderRelationshipEditor(state.detail);
    markDirty("relationship");
    return;
  }
  const person = exact;
  replaceMatchingProposedParticipant(person);
  $("personInput").value = "";
  renderParticipantChips();
  renderRelationshipEditor(state.detail);
  markDirty("relationship");
}

async function createAccountFromInput() {
  const name = $("accountInput").value.trim();
  if (!name) return;
  state.account = await findOrCreate("/api/accounts", "account_name", name, { account_name: name, account_type: name.toLowerCase().includes("family") ? "family" : "individual" });
  $("accountInput").value = state.account.account_name;
  markDirty("relationship");
}

function showPersonEditor(index) {
  const p = state.participants[index];
  const split = splitDisplayName(p.display_name || p.participant_name || "");
  const mergeButton = !p.is_proposed && p.person_id ? '<button id="mergePersonBtn">Merge...</button>' : "";
  $("personEditor").hidden = false;
  $("personEditor").innerHTML = `
    <h4>Edit Client</h4>
    <div class="field-grid">
      <label class="field">First name<input id="editPersonFirst" value="${escapeAttr(p.first_name || split.first)}"></label>
      <label class="field">Last name<input id="editPersonLast" value="${escapeAttr(p.last_name || split.last)}"></label>
      <label class="field">Email<input id="editPersonEmail" value="${escapeAttr(p.billing_email || "")}"></label>
      <label class="field">Phone<input id="editPersonPhone" value="${escapeAttr(p.billing_phone || "")}"></label>
    </div>
    <div class="inline-actions"><button id="savePersonEdit" class="save">Save Client</button><button id="cancelPersonEdit">Cancel</button>${mergeButton}</div>
  `;
  $("savePersonEdit").onclick = async () => {
    const first = $("editPersonFirst").value.trim();
    const last = $("editPersonLast").value.trim();
    const display = `${first} ${last}`.trim();
    if (!p.person_id) {
      state.participants[index] = {
        ...p,
        first_name: first,
        last_name: last,
        billing_email: $("editPersonEmail").value.trim(),
        billing_phone: $("editPersonPhone").value.trim(),
        display_name: display,
        participant_name: display
      };
      markDirty("relationship");
    } else {
      const updated = await api(`/api/people/${p.person_id}`, { method: "POST", body: JSON.stringify({
        first_name: first,
        last_name: last,
        display_name: display,
        billing_email: $("editPersonEmail").value || null,
        billing_phone: $("editPersonPhone").value || null,
        active: true
      }) });
      state.participants[index] = { ...p, ...updated, display_name: updated.display_name };
      markSaved("relationship", "Client saved");
    }
    $("personEditor").hidden = true;
    renderParticipantChips();
  };
  $("cancelPersonEdit").onclick = () => $("personEditor").hidden = true;
  if ($("mergePersonBtn")) $("mergePersonBtn").onclick = async () => {
    const target = prompt("Merge this client into which existing display name?");
    if (!target || !p.person_id) return;
    const rows = await api(`/api/people?q=${encodeURIComponent(target)}`);
    const survivor = rows.find(row => normalizeParticipantName(row.display_name) === normalizeParticipantName(target)) || rows[0];
    if (!survivor) return alert("No matching survivor person found.");
    if (!confirm(`Merge ${p.display_name} into ${survivor.display_name}? The duplicate will be marked inactive, not deleted.`)) return;
    const merged = await api(`/api/people/${survivor.person_id}/merge`, { method: "POST", body: JSON.stringify({ duplicate_person_id: p.person_id, reason: "Merged from review UI" }) });
    state.participants[index] = { ...p, person_id: merged.person_id, display_name: merged.display_name };
    $("personEditor").hidden = true;
    renderParticipantChips();
    markSaved("relationship", "Client merged");
  };
}

function renderRelationshipEditor(data) {
  const members = data && data.account_members ? data.account_members : [];
  const accountName = state.account ? state.account.account_name : "No billing relationship selected";
  const billingName = (data.effective_billing_party || state.billingParty)?.billing_name || "No billing party selected";
  $("relationshipEditor").innerHTML = `
    <h4>Relationship Summary</h4>
    <div class="kv">
      <label>Relationship</label><strong>${escapeHtml(accountName)}</strong>
      <label>Members</label><span>${(members.length ? members : state.participants).map(m => escapeHtml(m.display_name || m.participant_name || "")).filter(Boolean).join(", ") || "None"}</span>
      <label>Default payer</label><span>${escapeHtml(billingName)}</span>
    </div>
    <div class="inline-actions"><button id="openAccountRecord">Open Billing Relationship Record</button></div>
  `;
  if ($("openAccountRecord")) $("openAccountRecord").onclick = () => openAccountRecord(state.account && state.account.account_id);
}

function openBillingRelationshipEditor() {
  if (!closeReviewOverlay()) return;
  const returnContext = persistReturnContext(buildReturnContext());
  if (!returnContext) {
    location.hash = "clients";
    showClients();
    return;
  }
  const accountId = state.account && state.account.account_id;
  if (accountId) {
    location.hash = returnContextHash({ ...returnContext, accountId });
    openAccountRecord(accountId, { returnContext });
    return;
  }
  location.hash = returnContextHash(returnContext);
  showClients();
}

async function saveRelationshipSection() {
  await resolveTypedSelections();
  const sessionDraft = collectSessionDraftValues();
  const updated = await api(`/api/review/candidates/${state.selected}/save-relationship`, {
    method: "POST",
    body: JSON.stringify({
      participants: collectParticipants(),
      account_id: state.account ? state.account.account_id : null,
      primary_person_id: state.participants.find(p => p.is_primary)?.person_id || state.participants[0]?.person_id || null,
      default_billing_party_id: state.billingParty ? state.billingParty.billing_party_id : null,
      billing_party_id: state.billingParty ? state.billingParty.billing_party_id : null
    })
  });
  state.detail = updated;
  state.account = updated.account;
  state.billingParty = updated.billing_party || updated.effective_billing_party;
  state.participants = updated.participants.map(participantState);
  state.editSteps.clients = false;
  renderInspector(updated);
  restoreSessionDraftValues(sessionDraft);
  markSaved("relationship", "Client(s) saved. Session suggestions refreshed.");
  await loadList();
}

async function saveBillingSection() {
  await resolveTypedSelections();
  const selectedPersonId = $("billToClientSelect").value;
  if (!selectedPersonId) return alert("Choose a confirmed client before saving Bill To.");
  const sessionDraft = collectSessionDraftValues();
  const updated = await api(`/api/review/candidates/${state.selected}/save-billing`, {
    method: "POST",
    body: JSON.stringify({ bill_to_person_id: selectedPersonId })
  });
  state.detail = updated;
  state.billingParty = updated.billing_party || updated.effective_billing_party;
  renderInspector(updated);
  restoreSessionDraftValues(sessionDraft);
  markSaved("billing", "Bill to saved");
  await loadList();
}

async function saveSessionSection() {
  const button = $("saveSessionBtn");
  if (button) button.disabled = true;
  try {
    const updated = await api(`/api/review/candidates/${state.selected}/save-session`, { method: "POST", body: JSON.stringify(collectPayload()) });
    state.detail = updated;
    state.editSteps.session = false;
    renderInspector(updated);
    markSaved("session", "Session saved");
    await loadList();
    requestAnimationFrame(() => $("approveBtn")?.scrollIntoView({ behavior: "smooth", block: "center" }));
  } catch (error) {
    clearSavedState("session", "Save failed");
    alert(`Could not save session: ${sanitizeUiErrorMessage(error.message, "Please check required fields and try again.")}`);
  } finally {
    if (button && document.body.contains(button)) button.disabled = false;
  }
}

async function save(approve) {
  if (approve && approvalState.submitting) return;
  if (approve) {
    approvalState.submitting = true;
    approvalState.candidateId = state.selected;
    if (reviewOverlayCtrl) reviewOverlayCtrl.beginPending(["approveBtn"]);
  }
  await resolveTypedSelections();
  try {
    const updated = await api(`/api/review/candidates/${state.selected}/${approve ? "approve" : "save"}`, {
      method: "POST",
      body: JSON.stringify(collectPayload())
    });
    state.detail = updated;
    state.editSteps = { clients: false, session: false };
    state.dirty.clear();
    await loadList();
    if (approve) {
      const staging = updated.invoice_staging;
      closeReviewOverlay({ clearCandidate: true, skipDirtyCheck: true });
      const firstReviewBtn = document.querySelector("#candidateRows .review-btn");
      if (firstReviewBtn) firstReviewBtn.focus();
      else $("searchBox")?.focus();
      
      let successMsg = "Session approved.";
      let warningMsg = null;
      if (staging) {
        if (staging.status === "success") {
          successMsg = "Session approved and added to monthly draft.";
        } else if (staging.status === "not_required") {
          successMsg = "Session approved and paid-at-session payment confirmed. Invoice staging was not required.";
        } else if (staging.status === "warning") {
          warningMsg = "Invoice staging warning: staging completed with errors — review invoices when ready.";
        } else if (staging.status === "unavailable") {
          warningMsg = "Invoice staging warning: database busy, session will stage later.";
        } else if (staging.status === "error") {
          warningMsg = "Invoice staging warning: unexpected error occurred, session will stage later.";
        }
      }
      showReviewSuccess(successMsg);
      if (warningMsg) {
        showReviewWarning(warningMsg);
      }
      if (updated.report_warning) {
        showReviewWarning(updated.report_warning);
      }
      
      if (!document.getElementById("invoicesView").hidden) {
        await loadInvoices();
        if (state.invoice && state.invoice.invoice && state.invoice.invoice.invoice_id) {
          await openInvoice(state.invoice.invoice.invoice_id);
        }
      }
      
      approvalState.submitting = false;
      approvalState.candidateId = null;
    } else {
      renderInspector(updated);
    }
  } catch (error) {
    if (approve) {
      approvalState.submitting = false;
      approvalState.candidateId = null;
      if (reviewOverlayCtrl) reviewOverlayCtrl.endPending();
      const msg = error.message || "";
      if (msg.startsWith("Cannot approve")) {
        alert(sanitizeUiErrorMessage(msg, "Could not approve session. Please check required fields and try again."));
      } else {
        alert(sanitizeUiErrorMessage(msg, "Could not approve session. Please check required fields and try again."));
      }
    } else {
      alert(error.message);
    }
  }
}

function collectSessionDraftValues() {
  const durationChoice = $("durationChoiceInput")?.value || "60";
  const customMinutes = $("customDurationInput")?.value || "";
  const approvedMinutes = durationChoice === "custom" ? customMinutes : durationChoice;
  return {
    approved_duration_minutes: approvedMinutes,
    billing_session_type: $("billingTypeInput")?.value || "psychotherapy",
    duration_choice: durationChoice,
    custom_duration_minutes: durationChoice === "custom" ? customMinutes : "",
    custom_service_description: $("customDescInput")?.value || "",
    custom_service_code: $("customCodeInput")?.value || "",
    approved_rate: $("approvedRateInput")?.value || "",
    payment_status: $("paymentInput")?.value || "",
    billing_treatment: $("billingTreatmentInput")?.value || "",
    rate_override_reason: $("overrideReasonInput")?.value || ""
  };
}

function restoreSessionDraftValues(values) {
  if (!values) return;
  if ($("billingTypeInput")) $("billingTypeInput").value = values.billing_session_type;
  if ($("durationChoiceInput")) $("durationChoiceInput").value = values.duration_choice;
  if ($("customDurationInput")) $("customDurationInput").value = values.custom_duration_minutes;
  if ($("customDescInput")) $("customDescInput").value = values.custom_service_description;
  if ($("customCodeInput")) $("customCodeInput").value = values.custom_service_code;
  if ($("approvedRateInput")) $("approvedRateInput").value = values.approved_rate;
  if ($("paymentInput")) $("paymentInput").value = values.payment_status;
  if ($("billingTreatmentInput")) $("billingTreatmentInput").value = values.billing_treatment;
  if ($("overrideReasonInput")) $("overrideReasonInput").value = values.rate_override_reason;
}

async function updateSessionRatePreview() {
  if (!$("sessionRatePreview") || !state.detail?.session?.id) return;
  const participantIds = confirmedSessionClients().map(p => p.person_id).filter(Boolean);
  const payload = {
    session_date: state.detail.session.session_date || state.detail.session.start_at?.slice(0, 10) || "",
    duration_choice: $("durationChoiceInput")?.value || durationToChoice(state.detail.session.approved_duration_minutes || state.detail.session.duration_minutes),
    custom_duration_minutes: $("customDurationInput")?.value || "",
    billing_session_type: $("billingTypeInput")?.value || state.detail.session.billing_session_type || "psychotherapy",
    appointment_status: state.detail.session.appointment_status || "scheduled",
    custom_service_description: $("customDescInput")?.value || "",
    custom_service_code: $("customCodeInput")?.value || "",
    time_category: state.detail.session.time_category || "standard",
    participant_person_ids: participantIds,
    person_id: participantIds.length === 1 ? participantIds[0] : "",
    client_account_id: state.account?.account_id || state.detail.session.account_id || "",
    service_mode: state.detail.session.service_mode || "office"
  };
  try {
    const preview = await api("/api/rate-rules/preview", { method: "POST", body: JSON.stringify(payload) });
    const previewText = preview.amount
      ? `Suggested by Rate Card: $${preview.amount} (${preview.explanation})`
      : "No matching Rate Card rule. This session will stay marked Needs Rate unless you enter one.";
    $("sessionRatePreview").textContent = previewText;
  } catch (err) {
    $("sessionRatePreview").textContent = err.message || "Unable to preview suggested rate.";
  }
}

async function resolveTypedSelections() {
  const accountField = $("accountInput");
  if (!accountField) return;
  const accountName = accountField.value.trim();
  if (accountName && (!state.account || state.account.account_name !== accountName)) {
    state.account = await findOrCreate("/api/accounts", "account_name", accountName, { account_name: accountName, account_type: accountName.toLowerCase().includes("family") ? "family" : "individual" });
  }
}

async function mark(classification) {
  await api(`/api/review/candidates/${state.selected}/mark`, { method: "POST", body: JSON.stringify({ classification, reason: classification }) });
  await loadList();
}

async function confirmDuplicateAndNext() {
  if (duplicateState.submitting) return;
  duplicateState.submitting = true;
  duplicateState.candidateId = state.selected;
  if (reviewOverlayCtrl) reviewOverlayCtrl.beginPending(["duplicateBtn"]);
  try {
    await api(`/api/review/candidates/${state.selected}/mark`, {
      method: "POST",
      body: JSON.stringify({ classification: "duplicate", reason: "duplicate" })
    });
    state.dirty.clear();
    await loadList();
    closeReviewOverlay({ clearCandidate: true, skipDirtyCheck: true });
    const items = state.items;
    if (items.length) {
      selectCandidate(items[0].candidate_id);
    } else {
      const firstReviewBtn = document.querySelector("#candidateRows .review-btn");
      if (firstReviewBtn) firstReviewBtn.focus();
      else $("searchBox")?.focus();
    }
    showReviewSuccess("Duplicate resolved");
    duplicateState.submitting = false;
    duplicateState.candidateId = null;
  } catch (error) {
    duplicateState.submitting = false;
    duplicateState.candidateId = null;
    if (reviewOverlayCtrl) reviewOverlayCtrl.endPending();
    alert(error.message || "Could not mark as duplicate. Please try again.");
  }
}

async function sendToReview() {
  try {
    await api(`/api/review/candidates/${state.selected}/send-to-review`, { method: "POST", body: JSON.stringify({ reason: "Manually promoted to review queue" }) });
    await loadList();
    if (state.selected) await selectCandidate(state.selected);
  } catch (err) {
    alert("Could not promote to review: " + err.message);
  }
}

function collectPayload() {
  const session = state.detail?.session || {};
  const durationChoice = $("durationChoiceInput")?.value
    || session.duration_choice
    || durationToChoice(session.approved_duration_minutes || session.duration_minutes)
    || "60";
  const customMinutes = $("customDurationInput")?.value || session.custom_duration_minutes || "";
  const approvedMinutes = durationChoice === "custom"
    ? customMinutes
    : ($("durationChoiceInput")?.value || session.approved_duration_minutes || session.duration_minutes || durationChoice);
  const participantIds = state.participants.map(p => p.person_id).filter(Boolean);
  const futurePerson = $("saveFuturePersonRate")?.checked === true;
  const futureJoint = $("saveFutureJointRate")?.checked === true;
  const paymentStatus = $("paymentInput")?.value || session.payment_status || "unpaid";

  let rateScope = "session_only";
  let rateScopePersonId = null;
  if (futureJoint && participantIds.length > 1) {
    rateScope = "future_joint";
  } else if (futurePerson && participantIds.length === 1) {
    rateScope = "future_person";
    rateScopePersonId = participantIds[0];
  }

  const rate = $("approvedRateInput")?.value
    || centString(session.approved_rate_cents)
    || centString(session.suggested_rate_cents)
    || "";

  return {
    ...collectRelationshipPayload(),
    approved_duration_minutes: approvedMinutes,
    billing_session_type: $("billingTypeInput")?.value || session.billing_session_type || "psychotherapy",
    duration_choice: durationChoice,
    custom_duration_minutes: durationChoice === "custom" ? customMinutes : "",
    custom_service_description: $("customDescInput")?.value || session.custom_service_description || "",
    custom_service_code: $("customCodeInput")?.value || session.custom_service_code || "",
    time_category: session.time_category || "standard",
    suggested_rate: centString(session.suggested_rate_cents),
    billing_party_id: state.billingParty?.billing_party_id || state.detail?.effective_billing_party?.billing_party_id || null,
    approved_rate: rate,
    payment_status: paymentStatus,
    billing_treatment: $("billingTreatmentInput")?.value || session.billing_treatment || "",
    billable_status: paymentStatus === "paid_at_session" ? "nonbillable" : "approved",
    rate_override_reason: $("overrideReasonInput")?.value || session.rate_override_reason || "",
    rate_scope: rateScope,
    rate_scope_person_id: rateScopePersonId,
    amount_received: $("paymentAmountInput")?.value || "",
    payment_date: $("paymentDateInput")?.value || "",
    payment_method: $("paymentMethodInput")?.value || "",
    reference_number: $("paymentRefInput")?.value || "",
    administrative_note: $("paymentNoteInput")?.value || ""
  };
}

function collectRelationshipPayload() {
  return {
    participants: collectParticipants(),
    account_id: state.account ? state.account.account_id : null,
    billing_party_id: state.billingParty ? state.billingParty.billing_party_id : null
  };
}

function collectParticipants() {
  const roleSelects = [...document.querySelectorAll("[data-role]")];
  roleSelects.forEach(select => {
    const index = Number(select.dataset.role);
    if (state.participants[index]) {
      state.participants[index].relationship_role = select.value;
    }
  });
  return state.participants;
}

function fillDatalist(id, rows, label) {
  $(id).innerHTML = rows.map(row => `<option value="${escapeAttr(row[label])}"></option>`).join("");
}

async function findOrCreate(path, label, value, createPayload) {
  const rows = await api(`${path}?q=${encodeURIComponent(value)}`);
  const existing = rows.find(row => String(row[label]).toLowerCase() === value.toLowerCase());
  if (existing) return existing;
  return api(path, { method: "POST", body: JSON.stringify(createPayload) });
}

function optionSet(options, selected) {
  return options.map(v => `<option value="${v}" ${v === selected ? "selected" : ""}>${timeLabel(serviceLabel(v))}</option>`).join("");
}
function billingTypeOptions(selected) {
  const types = [
    {value: "psychotherapy", label: "Psychotherapy Session"},
    {value: "psychotherapy_house_call", label: "Psychotherapy Session / House Call"},
    {value: "psychotherapy_weekend", label: "Psychotherapy Session / Weekend"},
    {value: "psychotherapy_evening", label: "Psychotherapy Session / Evening"},
    {value: "custom", label: "Custom"}
  ];
  return types.map(t => `<option value="${t.value}" ${t.value === selected ? "selected" : ""}>${t.label}</option>`).join("");
}
function durationOptions(selected) {
  const choices = [
    {value: "30", label: "30 minutes"},
    {value: "60", label: "60 minutes"},
    {value: "90", label: "90 minutes"},
    {value: "120", label: "120 minutes"},
    {value: "custom", label: "Custom"}
  ];
  return choices.map(c => `<option value="${c.value}" ${c.value === selected ? "selected" : ""}>${c.label}</option>`).join("");
}
function durationToChoice(minutes) {
  if (!minutes) return "60";
  if ([30, 60, 90, 120].includes(Number(minutes))) return String(minutes);
  return "custom";
}
function mapLegacyToType(s) {
  if (s.service_mode === "house_call") return "psychotherapy_house_call";
  if (s.is_weekend) return "psychotherapy_weekend";
  if (s.is_evening) return "psychotherapy_evening";
  return "psychotherapy";
}
function houseCallSuggestion(s) {
  if (!s.house_call_suggested) return "";
  return `<div class="suggestion-note"><strong>Location suggests House Call:</strong> ${escapeHtml(s.location_text || "Location field present")}. Please confirm the session type.</div>`;
}
function centString(cents) { return cents ? (Number(cents) / 100).toFixed(2) : ""; }
function parseMoneyToCents(value) {
  const raw = String(value || "").trim().replace(/[$,]/g, "");
  if (!raw) return null;
  if (!/^-?\d+(\.\d{1,2})?$/.test(raw)) return null;
  return Math.round(Number(raw) * 100);
}
function paymentMethodLabel(method) {
  return ({
    zelle: "Zelle",
    check: "Check",
    cash: "Cash",
    ach: "ACH",
    card: "Card",
    other: "Other",
    Multiple: "Multiple"
  }[method] || escapeHtml(method) || "Other");
}
function paymentHandlingLabel(status) {
  return ({
    unpaid: "Invoice billing",
    paid_at_session: "Paid at session"
  }[status] || escapeHtml(status) || "—");
}
function paymentStatusLabel(status) {
  return ({
    unpaid: "Unpaid",
    partially_paid: "Partially Paid",
    paid: "Paid",
    posted: "Posted",
    reversed: "Reversed",
    void: "Void"
  }[status] || escapeHtml(status) || "Unknown");
}
function safeList(raw) { try { return Array.isArray(raw) ? raw : JSON.parse(raw || "[]"); } catch { return []; } }
function startRange(s) {
  const formatter = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" });
  const start = s.start_at ? formatter.format(new Date(s.start_at)) : "";
  const end = s.end_at ? formatter.format(new Date(s.end_at)) : "";
  return start && end ? `${start} - ${end}` : start || end;
}
function debounce(fn, ms) { let t; return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); }; }

["searchBox","statusFilter","serviceFilter","calendarFilter"].forEach(id => { const el = $(id); if (el) el.addEventListener("input", () => { state.offset = 0; loadList(); }); });
$("prevPage").onclick = () => { state.offset = Math.max(0, state.offset - state.limit); loadList(); };
$("nextPage").onclick = () => { state.offset += state.limit; loadList(); };
document.getElementById("calendarImportNav").onclick = (event) => {
  event.preventDefault();
  location.hash = "calendar-import";
  showCalendarImport();
};
document.getElementById("rateCardNav").onclick = (event) => {
  event.preventDefault();
  location.hash = "rate-card";
  showRateCard();
};
document.getElementById("clientsNav").onclick = (event) => {
  event.preventDefault();
  location.hash = "clients";
  showClients();
};
document.getElementById("peopleNav").onclick = (event) => {
  event.preventDefault();
  location.hash = "people";
  showPeople();
};
document.getElementById("sessionsNav").onclick = (event) => {
  event.preventDefault();
  location.hash = "sessions";
  showSessions();
};
document.getElementById("invoicesNav").onclick = (event) => {
  event.preventDefault();
  history.pushState({}, "", "/invoices");
  showInvoices();
};
document.getElementById("reportsNav").onclick = (event) => {
  event.preventDefault();
  history.pushState({}, "", "/reports");
  showReports();
};
document.getElementById("paymentsNav").onclick = (event) => {
  event.preventDefault();
  history.pushState({}, "", "/payments");
  showPayments();
};
document.getElementById("settingsNav").onclick = (event) => {
  event.preventDefault();
  location.hash = "settings";
  showSettings();
};
document.getElementById("reviewNav").onclick = () => {
  location.hash = "";
  showReviewWorkbench();
};

function hideViews() {
  ["reviewWorkbench","calendarImportView","rateCardView","clientsView","peopleView","sessionsView","invoicesView","paymentsView","reportsView","settingsView"].forEach(id => document.getElementById(id).hidden = true);
  ["reviewNav","calendarImportNav","rateCardNav","clientsNav","peopleNav","sessionsNav","invoicesNav","reportsNav","paymentsNav","settingsNav"].forEach(id => document.getElementById(id).classList.remove("active"));
}

function showRateCard() {
  hideViews();
  document.getElementById("rateCardView").hidden = false;
  document.getElementById("rateCardNav").classList.add("active");
  $("pageTitle").textContent = "Rate Card";
  $("pageSubtitle").textContent = "Manage standard rates and exceptions";
  document.title = "Jordana Billing - Rate Card";
  resetRateCardForm();
  loadRateRules();
}

function reviewOverlayKeydownHandler(e, overlay) {
  if (e.key === "Escape") {
    e.preventDefault();
    closeReviewOverlay();
    return;
  }
  if (e.key === "Tab") {
    if (!overlay || overlay.hidden) return;
    const focusable = overlay.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), a[href]');
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }
}

const reviewOverlayCtrl = createOverlay({
  overlay: "reviewOverlay",
  closeBtn: "reviewOverlayClose",
  firstFocusSelector: "button, input, select, a[href]",
  keydownHandler: reviewOverlayKeydownHandler,
  bodyLock: false
});

function openReviewOverlay() {
  if (!reviewOverlayCtrl) return;
  reviewOverlayCtrl.open({});
}

function closeReviewOverlay({ clearCandidate = false, skipDirtyCheck = false } = {}) {
  if (!reviewOverlayCtrl) return true;
  if (reviewOverlayCtrl.isOpen() && !skipDirtyCheck && state.dirty.size > 0) {
    if (!confirm("You have unsaved changes. Close anyway?")) return false;
    state.dirty.clear();
  }
  if (clearCandidate) {
    state.selected = null;
    state.detail = null;
    state.participants = [];
    state.account = null;
    state.billingParty = null;
    state.editSteps = { clients: false, session: false };
  }
  reviewOverlayCtrl.close({ restoreFocus: true });
  return true;
}

function showReviewSuccess(message) {
  const workbench = $("reviewWorkbench");
  if (!workbench) return;
  const banner = document.createElement("div");
  banner.className = "review-success-banner";
  banner.textContent = message;
  banner.setAttribute("role", "status");
  workbench.prepend(banner);
  setTimeout(() => { if (document.body.contains(banner)) banner.remove(); }, 5000);
}

function showReviewWarning(message) {
  const workbench = $("reviewWorkbench");
  if (!workbench) return;
  const banner = document.createElement("div");
  banner.className = "review-warning-banner";
  banner.textContent = message;
  banner.setAttribute("role", "status");
  workbench.prepend(banner);
  setTimeout(() => { if (document.body.contains(banner)) banner.remove(); }, 8000);
}

function showInvoiceSuccess(message) {
  const view = $("invoicesView");
  if (!view) return;
  const banner = document.createElement("div");
  banner.className = "review-success-banner";
  banner.textContent = message;
  banner.setAttribute("role", "status");
  view.prepend(banner);
  setTimeout(() => { if (document.body.contains(banner)) banner.remove(); }, 5000);
}

function showUnpaidSuccess(message) {
  const view = $("paymentsView");
  if (!view) return;
  const banner = document.createElement("div");
  banner.className = "review-success-banner";
  banner.textContent = message;
  banner.setAttribute("role", "status");
  view.prepend(banner);
  setTimeout(() => { if (document.body.contains(banner)) banner.remove(); }, 5000);
}

async function loadOutstandingInvoices(selectedInvoiceId = state.unpaid.selectedInvoiceId) {
  const data = await api("/api/payments/outstanding-invoices");
  state.unpaid.items = data.items || [];
  renderOutstandingInvoices(state.unpaid.items);
  await loadFinancialSummary();
  if (!state.unpaid.items.length) {
    state.unpaid.selectedInvoiceId = null;
    $("unpaidWorkspace").innerHTML = `<div class="empty-state">No outstanding finalized invoices.</div>`;
    return;
  }
  const nextId = state.unpaid.items.some(item => item.invoice_id === selectedInvoiceId)
    ? selectedInvoiceId
    : state.unpaid.items[0].invoice_id;
  await openOutstandingInvoice(nextId, { preserveFocus: true });
}

function renderOutstandingInvoices(items) {
  const tbody = $("unpaidRows");
  tbody.innerHTML = items.length
    ? items.map(item => `
      <tr data-invoice-id="${escapeAttr(item.invoice_id)}" class="${state.unpaid.selectedInvoiceId === item.invoice_id ? "selected" : ""}">
        <td><span class="status-pill ${escapeAttr(item.payment_status)}">${escapeHtml(paymentStatusLabel(item.payment_status))}</span></td>
        <td>${fmt(item.invoice_number)}</td>
        <td>${fmt(item.bill_to_display_name)}</td>
        <td>${fmt(item.invoice_date)}</td>
        <td>${money(centString(item.total_cents))}</td>
        <td>${money(centString(item.paid_cents))}</td>
        <td>${money(centString(item.balance_cents))}</td>
        <td><button class="review-btn record-payment-btn" data-record-payment="${escapeAttr(item.invoice_id)}">Record Payment</button></td>
      </tr>
    `).join("")
    : '<tr class="empty-row"><td colspan="8">No outstanding finalized invoices.</td></tr>';

  document.querySelectorAll("#unpaidRows tr[data-invoice-id]").forEach(row => {
    row.addEventListener("click", (event) => {
      if (event.target.closest("button")) return;
      openOutstandingInvoice(row.dataset.invoiceId);
    });
  });
  document.querySelectorAll(".record-payment-btn").forEach(button => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      openPaymentOverlay(button.dataset.recordPayment, button);
    });
  });
}

async function openOutstandingInvoice(invoiceId, { preserveFocus = false } = {}) {
  state.unpaid.selectedInvoiceId = invoiceId;
  document.querySelectorAll("#unpaidRows tr[data-invoice-id]").forEach(row => {
    row.classList.toggle("selected", row.dataset.invoiceId === invoiceId);
  });
  const data = await api(`/api/invoices/${invoiceId}/payments`);
  state.unpaid.paymentHistory = data.payments || [];
  renderOutstandingInvoiceWorkspace(data);
  if (!preserveFocus) {
    const row = document.querySelector(`#unpaidRows tr[data-invoice-id="${CSS.escape(invoiceId)}"]`);
    if (row) row.focus?.();
  }
}

function renderOutstandingInvoiceWorkspace(data) {
  const invoice = data.invoice;
  $("unpaidWorkspace").innerHTML = `
    <div class="payment-panel">
      <div class="payment-panel-header">
        <div>
          <h3>${fmt(invoice.invoice_number)}</h3>
          <div class="help">${fmt(invoice.bill_to_display_name)} • Invoice date ${fmt(invoice.invoice_date)}</div>
        </div>
        <button class="save" id="workspaceRecordPayment">Record Payment</button>
      </div>
      <div class="payment-panel-summary">
        <div class="payment-summary-card"><label>Total</label><strong>${money(centString(invoice.total_cents))}</strong></div>
        <div class="payment-summary-card"><label>Paid</label><strong>${money(centString(invoice.paid_cents))}</strong></div>
        <div class="payment-summary-card"><label>Balance</label><strong>${money(centString(invoice.balance_cents))}</strong></div>
      </div>
      <section class="section">
        <h3>Payment History</h3>
        <table class="review-table payment-history-table">
          <thead><tr><th>Payment Date</th><th>Method</th><th>Reference</th><th>Received From</th><th>Amount Applied</th><th>Status</th></tr></thead>
          <tbody>${(data.payments || []).map(payment => `
            <tr>
              <td>${fmt(payment.received_at)}</td>
              <td>${escapeHtml(paymentMethodLabel(payment.method))}</td>
              <td>${fmt(payment.reference_number)}</td>
              <td>${fmt(payment.received_from_name)}</td>
              <td>${money(centString(payment.amount_applied_cents))}</td>
              <td><span class="status-pill ${escapeAttr(payment.payment_status)}">${escapeHtml(paymentStatusLabel(payment.payment_status))}</span></td>
            </tr>
          `).join("") || '<tr class="empty-row"><td colspan="6">No payment history yet.</td></tr>'}</tbody>
        </table>
      </section>
    </div>
  `;
  $("workspaceRecordPayment").onclick = (event) => openPaymentOverlay(invoice.invoice_id, event.currentTarget);
}

function closePaymentOverlay() {
  const overlay = $("paymentOverlay");
  if (!overlay) return;
  overlay.hidden = true;
  state.unpaid.submitting = false;
  if (paymentOverlayReturnFocus && document.body.contains(paymentOverlayReturnFocus)) {
    paymentOverlayReturnFocus.focus();
  }
  paymentOverlayReturnFocus = null;
}

async function openPaymentOverlay(invoiceId, focusReturnEl = null) {
  const data = await api(`/api/invoices/${invoiceId}/payments`);
  const invoice = data.invoice;
  paymentOverlayReturnFocus = focusReturnEl;
  $("paymentOverlayContent").innerHTML = `
    <form id="paymentForm">
      <div class="payment-form-grid">
        <label class="field">Bill To<input value="${escapeAttr(invoice.bill_to_display_name || "")}" readonly /></label>
        <label class="field">Invoice Number<input value="${escapeAttr(invoice.invoice_number || "")}" readonly /></label>
        <label class="field">Outstanding Balance<input value="${escapeAttr(money(centString(invoice.balance_cents)))}" readonly /></label>
        <label class="field">Payment Date<input id="paymentDateInput" type="date" value="${escapeAttr(new Date().toISOString().slice(0, 10))}" required /></label>
        <label class="field">Amount Received<input id="paymentAmountInput" value="${escapeAttr(centString(invoice.balance_cents))}" required /></label>
        <label class="field">Payment Method<select id="paymentMethodInput" required><option value="">Select a method</option><option value="zelle">Zelle</option><option value="check">Check</option><option value="cash">Cash</option><option value="ach">ACH</option><option value="card">Card</option><option value="other">Other</option></select></label>
        <label class="field">Reference Number<input id="paymentReferenceInput" value="" /></label>
        <label class="field">Received From<input id="paymentReceivedFromInput" value="${escapeAttr(invoice.bill_to_display_name || "")}" /></label>
        <label class="field wide">Administrative Note<input id="paymentAdministrativeNoteInput" value="" aria-describedby="paymentAdministrativeNoteHelp" /></label>
      </div>
      <div class="help" id="paymentAdministrativeNoteHelp">Administrative only. Do not include clinical information.</div>
      <div class="payment-form-note">The payment will be applied automatically to this invoice from the oldest service date forward.</div>
      <div class="payment-form-message" id="paymentFormMessage" aria-live="polite"></div>
      <div class="payment-form-actions">
        <button type="button" id="paymentCancelBtn">Cancel</button>
        <button type="submit" class="save" id="paymentSubmitBtn">Record Payment</button>
      </div>
    </form>
  `;
  const overlay = $("paymentOverlay");
  overlay.hidden = false;
  $("paymentOverlayClose").onclick = closePaymentOverlay;
  $("paymentCancelBtn").onclick = closePaymentOverlay;
  $("paymentForm").onsubmit = async (event) => {
    event.preventDefault();
    await submitInvoicePayment(invoice);
  };
  requestAnimationFrame(() => $("paymentDateInput")?.focus());
}

async function submitInvoicePayment(invoice) {
  if (state.unpaid.submitting) return;
  const submitBtn = $("paymentSubmitBtn");
  const cancelBtn = $("paymentCancelBtn");
  const closeBtn = $("paymentOverlayClose");
  const message = $("paymentFormMessage");
  state.unpaid.submitting = true;
  submitBtn.disabled = true;
  cancelBtn.disabled = true;
  closeBtn.disabled = true;
  message.textContent = "";

  const amountCents = parseMoneyToCents($("paymentAmountInput").value);
  const payload = {
    payment_date: $("paymentDateInput").value,
    amount_cents: amountCents,
    payment_method: $("paymentMethodInput").value,
    reference_number: $("paymentReferenceInput").value.trim() || null,
    received_from_name: $("paymentReceivedFromInput").value.trim() || null,
    administrative_note: $("paymentAdministrativeNoteInput").value.trim() || null
  };

  try {
    await api(`/api/invoices/${invoice.invoice_id}/payments`, {
      method: "POST",
      body: JSON.stringify(payload)
    });
    closePaymentOverlay();
    await loadOutstandingInvoices(invoice.invoice_id);
    showUnpaidSuccess("Payment recorded successfully.");
  } catch (err) {
    state.unpaid.submitting = false;
    submitBtn.disabled = false;
    cancelBtn.disabled = false;
    closeBtn.disabled = false;
    message.textContent = sanitizeUiErrorMessage(err.message, "Payment could not be recorded.");
  }
}

function goToPreviousSession() {
  const items = state.items;
  if (!items.length) return;
  const currentIndex = items.findIndex(item => item.candidate_id === state.selected);
  if (currentIndex <= 0) return;
  selectCandidate(items[currentIndex - 1].candidate_id);
}

function goToNextSession() {
  const items = state.items;
  if (!items.length) return;
  const currentIndex = items.findIndex(item => item.candidate_id === state.selected);
  if (currentIndex < 0 || currentIndex >= items.length - 1) return;
  selectCandidate(items[currentIndex + 1].candidate_id);
}

async function saveAndNext() {
  await save(false);
  const items = state.items;
  if (!items.length) return;
  const next = items.find(item => item.candidate_id !== state.selected);
  if (next) {
    selectCandidate(next.candidate_id);
  } else if (items.length) {
    selectCandidate(items[0].candidate_id);
  }
}

function showReviewWorkbench() {
  hideViews();
  document.getElementById("reviewWorkbench").hidden = false;
  document.getElementById("reviewNav").classList.add("active");
  $("pageTitle").textContent = "Session Review";
  $("pageSubtitle").textContent = "Review calendar events and confirm details";
  document.title = "Jordana Billing - Session Review";
  if (state.returnCandidate) {
    selectCandidate(state.returnCandidate);
    state.returnCandidate = null;
  }
}

function renderClientsLanding(returnContext = null) {
  if (!returnContext) {
    $("accountRecord").innerHTML = `<div class="empty-state">Open a billing relationship record.</div>`;
    closeOrganizationRecord();
    return;
  }
  const names = (returnContext.participants || []).map(p => fmt(p.display_name)).join(", ");
  $("accountRecord").innerHTML = `
    <div class="record-banner">
      <a href="#" class="return-link" id="returnToReviewFromClients">← Return to review</a>
      <h3>Finish billing relationship for this session</h3>
      <div class="help">Session ID ${fmt(returnContext.sessionId)}${names ? ` • Clients: ${names}` : ""}</div>
      <div class="record-actions">
        <button id="createRelationshipForReturn" class="save">Create Billing Relationship</button>
      </div>
    </div>
  `;
  $("returnToReviewFromClients").onclick = async (event) => {
    event.preventDefault();
    const current = readReturnContext();
    if (!validReturnContext(current)) {
      clearReturnContext();
      location.hash = "";
      showReviewWorkbench();
      return;
    }
    location.hash = "";
    await showReviewWorkbench();
    await selectCandidate(current.candidateId);
  };
  $("createRelationshipForReturn").onclick = () => {
    const current = readReturnContext();
    openCreateRelationshipModal(current, $("createRelationshipForReturn"));
  };
}

async function showClients() {
  hideViews();
  document.getElementById("clientsView").hidden = false;
  document.getElementById("clientsNav").classList.add("active");
  $("pageTitle").textContent = "Billing Relationships";
  $("pageSubtitle").textContent = "Who receives invoices and who they pay for";
  document.title = "Jordana Billing - Billing Relationships";
  const hashContext = hashReturnContext();
  if (!hashContext) {
    clearReturnContext();
  }
  const returnContext = readReturnContext();
  renderClientsLanding(returnContext);
  await loadClients();
  if (validReturnContext(returnContext) && returnContext.accountId) {
    await openAccountRecord(returnContext.accountId, { returnContext });
  }
}

async function showPeople() {
  hideViews();
  document.getElementById("peopleView").hidden = false;
  document.getElementById("peopleListView").hidden = false;
  document.getElementById("personRecordView").hidden = true;
  document.getElementById("peopleNav").classList.add("active");
  $("pageTitle").textContent = "Clients";
  $("pageSubtitle").textContent = "Permanent clients and billing relationships";
  document.title = "Jordana Billing - Clients";
  await loadPeople();
}

async function showPersonRecordPage(personId) {
  hideViews();
  document.getElementById("peopleView").hidden = false;
  document.getElementById("peopleListView").hidden = true;
  document.getElementById("personRecordView").hidden = false;
  document.getElementById("peopleNav").classList.add("active");
  $("pageTitle").textContent = "Client";
  $("pageSubtitle").textContent = "Client billing workspace";
  document.title = "Jordana Billing - Client";
  await openPersonRecord(personId);
}

function renderSessions(rows, total) {
  state.sessions.items = rows;
  state.sessions.total = total;
  $("sessionsResultCount").textContent = `Showing ${rows.length ? state.sessions.offset + 1 : 0} to ${state.sessions.offset + rows.length} of ${total} results`;
  $("sessionsRows").innerHTML = rows.map(row => {
    const canRestore = row.review_status === "excluded" && row.candidate_id;
    const canPromote = !row.session_id && row.review_status === "needs_classification" && row.candidate_id;
    let actionCell = `<td></td>`;
    if (canRestore)
      actionCell = `<td><button class="restore-session-btn link-btn" data-cid="${escapeAttr(row.candidate_id)}">Return to Review</button></td>`;
    else if (canPromote)
      actionCell = `<td><button class="send-session-to-review-btn link-btn" data-cid="${escapeAttr(row.candidate_id)}">Send to Review</button></td>`;
    return `
    <tr>
      <td>${fmt(row.date)}</td>
      <td>${fmt(row.time)}</td>
      <td><span class="primary">${fmt(row.client_participants)}</span></td>
      <td>${fmt(row.calendar_title)}</td>
      <td>${fmt(row.session_length)}</td>
      <td>${money(row.rate)}</td>
      <td>${escapeHtml(paymentHandlingLabel(row.payment_status))}</td>
      <td>${fmt(row.review_status)}</td>
      ${actionCell}
    </tr>`;
  }).join("") || `<tr><td colspan="9" class="readonly-note">No appointments found.</td></tr>`;
  $("sessionsRows").querySelectorAll(".restore-session-btn").forEach(btn => {
    btn.addEventListener("click", () => restoreSessionRow(btn.dataset.cid));
  });
  $("sessionsRows").querySelectorAll(".send-session-to-review-btn").forEach(btn => {
    btn.addEventListener("click", () => sendSessionRowToReview(btn.dataset.cid));
  });
}

async function restoreSessionRow(candidateId) {
  if (restoreState.submitting) return;
  if (restoreState.candidateId && restoreState.candidateId !== candidateId) return;
  restoreState.submitting = true;
  restoreState.candidateId = candidateId;
  const btn = document.querySelector(`.restore-session-btn[data-cid="${CSS.escape(candidateId)}"]`);
  if (btn) btn.disabled = true;
  try {
    const result = await api(`/api/review/candidates/${candidateId}/restore`, { method: "POST", body: JSON.stringify({ reason: "Returned to review queue from Sessions view" }) });
    await loadSessions();
    if (result && result.warning) {
      alert(result.warning);
    }
  } catch (err) {
    alert(sanitizeUiErrorMessage(err.message, "Could not restore session. Please try again."));
    if (btn && document.body.contains(btn)) btn.disabled = false;
  } finally {
    restoreState.submitting = false;
    restoreState.candidateId = null;
  }
}

async function sendSessionRowToReview(candidateId) {
  try {
    await api(`/api/review/candidates/${candidateId}/send-to-review`, { method: "POST", body: JSON.stringify({ reason: "Promoted to review queue from Sessions view" }) });
    await loadSessions();
  } catch (err) {
    alert("Could not promote to review: " + err.message);
  }
}

async function loadSessions() {
  const params = new URLSearchParams({
    date_range: $("sessionsDateFilter").value,
    review_status: $("sessionsReviewStatusFilter").value,
    payment_status: $("sessionsPaymentStatusFilter").value,
    limit: state.sessions.limit,
    offset: state.sessions.offset
  });
  const data = await api(`/api/sessions?${params}`);
  renderSessions(data.items, data.total);
}

async function showSessions() {
  hideViews();
  $("sessionsView").hidden = false;
  $("sessionsNav").classList.add("active");
  $("pageTitle").textContent = "Sessions";
  $("pageSubtitle").textContent = "Read-only appointment ledger";
  document.title = "Jordana Billing - Sessions";
  await loadSessions();
}

async function showInvoices() {
  hideViews();
  $("invoicesView").hidden = false;
  $("invoicesNav").classList.add("active");
  $("pageTitle").textContent = "Invoices";
  $("pageSubtitle").textContent = "Search, filter, preview, finalize, and preserve invoice history";
  document.title = "Jordana Billing - Invoices";
  await loadInvoiceBillToFilter();
  await loadInvoices();
}

async function showReports() {
  hideViews();
  $("reportsView").hidden = false;
  $("reportsNav").classList.add("active");
  $("pageTitle").textContent = "Reports";
  $("pageSubtitle").textContent = "Download billing and appointment exports";
  document.title = "Jordana Billing - Reports";
  await loadReports();
}

async function showPayments() {
  hideViews();
  $("paymentsView").hidden = false;
  $("paymentsNav").classList.add("active");
  $("pageTitle").textContent = "Payments";
  $("pageSubtitle").textContent = "Record payments, review outstanding and paid invoices, and browse the payment ledger";
  document.title = "Jordana Billing - Payments";
  setupPaymentsTabs();
  if (state.payments.activeTab === "outstanding") {
    await loadOutstandingInvoices();
  } else if (state.payments.activeTab === "paid") {
    await loadPaidInvoices();
  } else if (state.payments.activeTab === "all-payments") {
    await loadAllPayments();
  }
}

function setupPaymentsTabs() {
  document.querySelectorAll(".payments-tab").forEach(tab => {
    tab.onclick = () => {
      switchPaymentsTab(tab.dataset.paymentsTab);
    };
  });
}

function switchPaymentsTab(tabName) {
  state.payments.activeTab = tabName;
  document.querySelectorAll(".payments-tab").forEach(tab => {
    tab.classList.toggle("active", tab.dataset.paymentsTab === tabName);
  });
  $("paymentsOutstandingPanel").hidden = tabName !== "outstanding";
  $("paymentsPaidPanel").hidden = tabName !== "paid";
  $("paymentsAllPaymentsPanel").hidden = tabName !== "all-payments";
  if (tabName === "outstanding") loadOutstandingInvoices();
  else if (tabName === "paid") loadPaidInvoices();
  else if (tabName === "all-payments") loadAllPayments();
}

async function loadPaidInvoices() {
  const data = await api("/api/payments/paid-invoices");
  state.payments.paidItems = data.items || [];
  renderPaidInvoices(state.payments.paidItems);
  await loadFinancialSummary();
}

function renderPaidInvoices(items) {
  const tbody = $("paidRows");
  tbody.innerHTML = items.length
    ? items.map(item => `
      <tr data-invoice-id="${escapeAttr(item.invoice_id)}">
        <td><span class="primary">${fmt(item.invoice_number)}</span></td>
        <td>${fmt(item.bill_to_display_name)}</td>
        <td>${fmt(item.invoice_date)}</td>
        <td>${money(centString(item.total_cents))}</td>
        <td>${fmt(item.paid_date)}</td>
        <td>${escapeHtml(paymentMethodLabel(item.payment_method))}</td>
        <td><span class="status-pill paid">Paid</span></td>
        <td><button class="mini" data-open-paid-invoice="${escapeAttr(item.invoice_id)}">Open</button></td>
      </tr>
    `).join("")
    : '<tr class="empty-row"><td colspan="8">No paid invoices.</td></tr>';
  document.querySelectorAll("#paidRows [data-open-paid-invoice]").forEach(btn => {
    btn.onclick = () => openPaidInvoice(btn.dataset.openPaidInvoice);
  });
}

async function openPaidInvoice(invoiceId) {
  const data = await api(`/api/invoices/${invoiceId}/payments`);
  const invoice = data.invoice;
  const payments = data.payments || [];
  $("unpaidWorkspace").innerHTML = `
    <div class="payment-panel">
      <div class="payment-panel-header">
        <div>
          <h3>${fmt(invoice.invoice_number)}</h3>
          <div class="help">${fmt(invoice.bill_to_display_name)} • Invoice date ${fmt(invoice.invoice_date)}</div>
        </div>
      </div>
      <div class="payment-panel-summary">
        <div class="payment-summary-card"><label>Total</label><strong>${money(centString(invoice.total_cents))}</strong></div>
        <div class="payment-summary-card"><label>Total Paid</label><strong>${money(centString(invoice.paid_cents))}</strong></div>
        <div class="payment-summary-card"><label>Balance</label><strong>$0.00</strong></div>
      </div>
      <section class="section">
        <h3>Payment History</h3>
        <table class="review-table payment-history-table">
          <thead><tr><th>Payment Date</th><th>Method</th><th>Reference</th><th>Received From</th><th>Amount Applied</th><th>Status</th></tr></thead>
          <tbody>${payments.map(payment => `
            <tr>
              <td>${fmt(payment.received_at)}</td>
              <td>${escapeHtml(paymentMethodLabel(payment.method))}</td>
              <td>${fmt(payment.reference_number)}</td>
              <td>${fmt(payment.received_from_name)}</td>
              <td>${money(centString(payment.amount_applied_cents))}</td>
              <td><span class="status-pill ${escapeAttr(payment.payment_status)}">${escapeHtml(paymentStatusLabel(payment.payment_status))}</span></td>
            </tr>
          `).join("") || '<tr class="empty-row"><td colspan="6">No payment history.</td></tr>'}</tbody>
        </table>
      </section>
    </div>
  `;
  switchPaymentsTab("paid");
  $("paymentsOutstandingPanel").hidden = true;
  $("paymentsAllPaymentsPanel").hidden = true;
  const paidPanel = $("paymentsPaidPanel");
  paidPanel.hidden = false;
  const layout = document.createElement("div");
  layout.className = "invoice-layout";
  const tableSection = document.createElement("section");
  tableSection.appendChild($("paidRows").closest("table"));
  layout.appendChild(tableSection);
  layout.appendChild($("unpaidWorkspace"));
  paidPanel.innerHTML = "";
  paidPanel.appendChild(layout);
}

async function loadAllPayments() {
  const data = await api("/api/payments");
  state.payments.allPaymentsItems = data.items || [];
  renderAllPayments(state.payments.allPaymentsItems);
  await loadFinancialSummary();
}

function renderAllPayments(items) {
  const tbody = $("allPaymentsRows");
  tbody.innerHTML = items.length
    ? items.map(item => `
      <tr data-payment-id="${escapeAttr(item.payment_id)}">
        <td>${fmt(item.received_at)}</td>
        <td>${fmt(item.bill_to_name)}</td>
        <td>${fmt(item.invoice_numbers)}</td>
        <td>${escapeHtml(paymentMethodLabel(item.method))}</td>
        <td>${fmt(item.reference_number)}</td>
        <td>${fmt(item.received_from_name)}</td>
        <td>${money(centString(item.amount_applied_cents))}</td>
        <td><span class="status-pill ${escapeAttr(item.status)}">${escapeHtml(paymentStatusLabel(item.status))}</span></td>
        <td><button class="mini" data-open-payment="${escapeAttr(item.payment_id)}">Open</button></td>
      </tr>
    `).join("")
    : '<tr class="empty-row"><td colspan="9">No payments recorded.</td></tr>';
  document.querySelectorAll("#allPaymentsRows [data-open-payment]").forEach(btn => {
    btn.onclick = () => openPaymentDetail(btn.dataset.openPayment);
  });
}

async function openPaymentDetail(paymentId) {
  const data = await api(`/api/payments/${paymentId}`);
  paymentDetailReturnFocus = document.activeElement;
  const allocs = data.allocations || [];
  const history = data.correction_history || [];
  const canVoid = data.status === "posted" && allocs.every(a => a.status !== "active");
  const hasUnapplied = data.status === "posted" && data.unapplied_cents > 0;
  const activeAllocs = allocs.filter(a => a.status === "active");
  const reversedAllocs = allocs.filter(a => a.status === "reversed");
  const receipt = data.receipt || null;
  const receiptActions = receipt
    ? `<button type="button" id="openReceiptBtn">Open Receipt</button><button type="button" id="showReceiptInFinderBtn">Show in Finder</button>`
    : data.status === "posted"
      ? `<button type="button" id="previewReceiptBtn">Preview Receipt</button><button type="button" id="createReceiptBtn" class="save">Create Receipt</button>`
      : `<span class="help">Receipts are available for posted payments only.</span>`;

  const allocRows = allocs.map(a => `
    <tr>
      <td>${money(centString(a.amount_cents))}</td>
      <td><span class="status-pill ${escapeAttr(a.status)}">${escapeHtml(a.status === "active" ? "Active" : "Reversed")}</span></td>
      <td>${a.invoice_info ? fmt(a.invoice_info.invoice_number) : "—"}</td>
      <td>${a.invoice_info ? fmt(a.invoice_info.bill_to_name) : "—"}</td>
      <td>${a.reversed_at ? fmt(a.reversed_at) : "—"}</td>
      <td>${a.reversal_reason ? escapeHtml(a.reversal_reason) : "—"}</td>
      <td>${a.status === "active" ? `<button class="mini danger" data-reverse-alloc="${escapeAttr(a.allocation_id)}">Reverse</button>` : ""}</td>
    </tr>
  `).join("");

  const historyRows = history.length ? history.map(h => `
    <tr>
      <td>${escapeHtml(h.action === "allocation_reversed" ? "Allocation Reversed" : h.action === "payment_voided" ? "Payment Voided" : h.action === "funds_applied" ? "Funds Applied" : escapeHtml(h.action))}</td>
      <td>${h.amount_cents != null ? money(centString(h.amount_cents)) : "—"}</td>
      <td>${h.reason ? escapeHtml(h.reason) : "—"}</td>
      <td>${fmt(h.created_at)}</td>
    </tr>
  `).join("") : '<tr class="empty-row"><td colspan="4">No corrections recorded.</td></tr>';

  $("paymentDetailOverlayContent").innerHTML = `
    <div class="payment-detail-section">
      <div class="payment-detail-grid">
        <label class="field">Date<input value="${escapeAttr(data.received_at || "")}" readonly /></label>
        <label class="field">Method<input value="${escapeAttr(paymentMethodLabel(data.method))}" readonly /></label>
        <label class="field">Reference<input value="${escapeAttr(data.reference_number || "")}" readonly /></label>
        <label class="field">Received From<input value="${escapeAttr(data.received_from_name || "")}" readonly /></label>
        <label class="field">Amount<input value="${escapeAttr(money(centString(data.amount_cents)))}" readonly /></label>
        <label class="field">Applied<input value="${escapeAttr(money(centString(data.allocated_cents)))}" readonly /></label>
        <label class="field">Unapplied<input value="${escapeAttr(money(centString(data.unapplied_cents)))}" readonly /></label>
        <label class="field">Status<input value="${escapeAttr(paymentStatusLabel(data.status))}" readonly /></label>
      </div>
      ${data.status === "void" && data.void_reason ? `<p class="payment-detail-void-reason"><strong>Void Reason:</strong> ${escapeHtml(data.void_reason)}</p>` : ""}
      ${data.voided_at ? `<p class="payment-detail-voided-at"><strong>Voided At:</strong> ${fmt(data.voided_at)}</p>` : ""}
    </div>
    <div class="payment-detail-section">
      <h3>Receipt</h3>
      <div class="payment-receipt-actions">${receiptActions}</div>
      <div id="paymentReceiptPreview" class="receipt-preview-inline" hidden></div>
    </div>
    <div class="payment-detail-section">
      <h3>Allocations</h3>
      <table class="review-table">
        <thead><tr><th>Amount</th><th>Status</th><th>Invoice</th><th>Bill To</th><th>Reversed At</th><th>Reason</th><th>Actions</th></tr></thead>
        <tbody>${allocRows || '<tr class="empty-row"><td colspan="7">No allocations.</td></tr>'}</tbody>
      </table>
    </div>
    <div class="payment-detail-section">
      <h3>Correction History</h3>
      <table class="review-table">
        <thead><tr><th>Action</th><th>Amount</th><th>Reason</th><th>Date</th></tr></thead>
        <tbody>${historyRows}</tbody>
      </table>
    </div>
    ${hasUnapplied ? `
      <div class="payment-detail-section">
        <h3>Apply Available Funds</h3>
        <form id="applyFundsForm" class="payment-detail-action-form">
          <label class="field">Invoice ID<input id="applyFundsInvoiceId" placeholder="Invoice UUID" required /></label>
          <label class="field">Amount (cents)<input id="applyFundsAmount" type="number" min="1" max="${data.unapplied_cents}" placeholder="e.g. 5000" required /></label>
          <button type="submit" class="save">Apply Funds</button>
        </form>
      </div>
    ` : ""}
    ${data.status === "posted" ? `
      <div class="payment-detail-section">
        <h3>Void Payment</h3>
        <form id="voidPaymentForm" class="payment-detail-action-form">
          <label class="field">Void Reason<input id="voidPaymentReason" placeholder="Administrative reason" required ${canVoid ? "" : "disabled"} /></label>
          <button type="submit" class="danger" ${canVoid ? "" : "disabled"}>${canVoid ? "Void Payment" : "Reverse all allocations first"}</button>
        </form>
      </div>
    ` : ""}
  `;

  const overlay = $("paymentDetailOverlay");
  overlay.hidden = false;
  $("paymentDetailOverlayClose").onclick = closePaymentDetailOverlay;

  if ($("openReceiptBtn")) $("openReceiptBtn").onclick = () => { window.open(`/api/payments/${encodeURIComponent(paymentId)}/receipt-pdf`, "_blank"); };
  if ($("showReceiptInFinderBtn")) $("showReceiptInFinderBtn").onclick = async () => {
    try {
      await api(`/api/payments/${encodeURIComponent(paymentId)}/receipt-document-action`, {
        method: "POST",
        body: JSON.stringify({ action: "show_in_finder" }),
      });
    } catch (err) {
      alert(err.message || "Show in Finder failed.");
    }
  };
  if ($("previewReceiptBtn")) $("previewReceiptBtn").onclick = async () => {
    try {
      const preview = await api(`/api/payments/${encodeURIComponent(paymentId)}/receipt-preview`);
      renderPaymentReceiptPreview(preview.snapshot || {});
    } catch (err) {
      alert(err.message || "Receipt preview failed.");
    }
  };
  if ($("createReceiptBtn")) $("createReceiptBtn").onclick = async () => {
    try {
      const preview = $("paymentReceiptPreview");
      const selected = preview && preview.querySelector("[name='receiptFilingOwner']") ? preview.querySelector("[name='receiptFilingOwner']").value : "";
      await api(`/api/payments/${encodeURIComponent(paymentId)}/receipt`, {
        method: "POST",
        body: JSON.stringify({ filing_owner_person_id: selected || null }),
      });
      await openPaymentDetail(paymentId);
      await loadAllPayments();
    } catch (err) {
      alert(err.message || "Receipt creation failed.");
    }
  };

  document.querySelectorAll("[data-reverse-alloc]").forEach(btn => {
    btn.onclick = async () => {
      const reason = prompt("Enter a reversal reason (administrative):");
      if (!reason || !reason.trim()) return;
      try {
        await api(`/api/payments/allocations/${encodeURIComponent(btn.dataset.reverseAlloc)}/reverse`, {
          method: "POST",
          body: JSON.stringify({ reason: reason.trim() }),
        });
        await openPaymentDetail(paymentId);
        await loadAllPayments();
      } catch (err) {
        alert(err.message || "Reversal failed.");
      }
    };
  });

  const applyForm = $("applyFundsForm");
  if (applyForm) {
    applyForm.onsubmit = async (e) => {
      e.preventDefault();
      const invoiceId = $("applyFundsInvoiceId").value.trim();
      const amount = parseInt($("applyFundsAmount").value, 10);
      if (!invoiceId || !amount || amount <= 0) return;
      try {
        await api(`/api/payments/${encodeURIComponent(paymentId)}/apply-funds`, {
          method: "POST",
          body: JSON.stringify({ invoice_id: invoiceId, amount_cents: amount }),
        });
        await openPaymentDetail(paymentId);
        await loadAllPayments();
      } catch (err) {
        alert(err.message || "Apply funds failed.");
      }
    };
  }

  const voidForm = $("voidPaymentForm");
  if (voidForm) {
    voidForm.onsubmit = async (e) => {
      e.preventDefault();
      const reason = $("voidPaymentReason").value.trim();
      if (!reason) return;
      try {
        await api(`/api/payments/${encodeURIComponent(paymentId)}/void`, {
          method: "POST",
          body: JSON.stringify({ reason }),
        });
        await openPaymentDetail(paymentId);
        await loadAllPayments();
      } catch (err) {
        alert(err.message || "Void failed.");
      }
    };
  }
}

function renderPaymentReceiptPreview(snapshot) {
  const target = $("paymentReceiptPreview");
  if (!target) return;
  const filing = snapshot.filing_owner || {};
  const eligible = filing.eligible_clients || [];
  const selected = filing.selected || null;
  const filingHtml = selected
    ? `<div class="relationship-summary success"><strong>File receipt under</strong><div>${fmt(selected.display_name)}</div></div>`
    : eligible.length
      ? `<label class="field">File receipt under<select name="receiptFilingOwner"><option value="">Select client...</option>${eligible.map(person => `<option value="${escapeAttr(person.person_id)}">${fmt(person.display_name)}</option>`).join("")}</select></label><div class="help">${fmt(filing.message)}</div>`
      : `<div class="reports-error">${fmt(filing.message || "Filing owner is unresolved.")}</div>`;
  target.hidden = false;
  target.innerHTML = `
    <article class="invoice-preview receipt-preview">
      <header class="invoice-preview-header">
        <div class="invoice-preview-left">
          <div class="invoice-preview-sender">${(snapshot.sender_lines || []).map(line => `<div>${fmt(line)}</div>`).join("")}</div>
          <div class="invoice-billto"><strong>BILL TO</strong>${(snapshot.bill_to_lines || []).map(line => `<div>${fmt(line)}</div>`).join("")}</div>
        </div>
        <div class="invoice-preview-title">
          <h3>${fmt(snapshot.document_title || "DRAFT PAYMENT RECEIPT")}</h3>
          ${snapshot.receipt_number ? `<div><strong>Receipt Number:</strong> ${fmt(snapshot.receipt_number)}</div>` : ""}
          <div><strong>Payment Date:</strong> ${fmt(snapshot.payment_date_display)}</div>
          <div><strong>Payment Method:</strong> ${fmt(snapshot.payment_method_display)}</div>
          ${snapshot.reference_number ? `<div><strong>Reference:</strong> ${fmt(snapshot.reference_number)}</div>` : ""}
        </div>
      </header>
      ${filingHtml}
      <table class="invoice-preview-table"><thead><tr><th>Invoice / Session</th><th>Date</th><th>Amount Paid</th><th>Remaining Balance</th></tr></thead><tbody>${(snapshot.allocations || []).map(a => `<tr><td>${fmt(a.reference_display)}</td><td>${fmt(a.service_date_display)}</td><td>${money(centString(a.amount_cents))}</td><td>${money(centString(a.remaining_balance_cents))}</td></tr>`).join("")}</tbody></table>
      <div class="invoice-payment-summary">
        <div class="payment-summary-card"><label>Amount Received</label><strong>${money(centString(snapshot.amount_cents))}</strong></div>
        <div class="payment-summary-card"><label>Unapplied</label><strong>${money(centString(snapshot.unapplied_cents))}</strong></div>
        <div class="payment-summary-card"><label>Status</label><strong>${snapshot.paid_in_full ? "PAID IN FULL" : "Partial payment"}</strong></div>
      </div>
    </article>
  `;
}

function closePaymentDetailOverlay() {
  const overlay = $("paymentDetailOverlay");
  if (!overlay) return;
  overlay.hidden = true;
  if (paymentDetailReturnFocus && document.body.contains(paymentDetailReturnFocus)) {
    paymentDetailReturnFocus.focus();
  }
  paymentDetailReturnFocus = null;
}

async function loadReports() {
  const grid = $("reportCardGrid");
  const errBox = $("reportsError");
  const yearSelect = $("reportsYearSelect");
  errBox.hidden = true;
  grid.innerHTML = "";
  let data;
  try {
    data = await api("/api/reports");
  } catch (err) {
    errBox.textContent = "Unable to load report metadata. Please try again.";
    errBox.hidden = false;
    yearSelect.innerHTML = "";
    grid.innerHTML = "";
    return;
  }
  const years = data.years || [];
  const defaultYear = data.default_year || new Date().getFullYear();
  if (years.length) {
    yearSelect.innerHTML = years.map(y => `<option value="${y}"${y === defaultYear ? " selected" : ""}>${y}</option>`).join("");
  } else {
    yearSelect.innerHTML = `<option value="${escapeAttr(defaultYear)}">${fmt(defaultYear)}</option>`;
  }
  grid.innerHTML = (data.reports || []).map(r => `
    <div class="report-card">
      <h3>${escapeHtml(r.display_name)}</h3>
      <p class="report-card-desc">${escapeHtml(r.description)}</p>
      <button class="save report-download-btn" data-type="${encodeURIComponent(r.type)}">Download CSV</button>
    </div>
  `).join("");
  document.querySelectorAll(".report-download-btn").forEach(btn => {
    btn.onclick = () => {
      const type = btn.dataset.type;
      const year = encodeURIComponent(yearSelect.value);
      window.location.href = `/api/reports/download?type=${type}&year=${year}`;
    };
  });
}

function renderSyncStatus(status) {
  $("syncCurrentStatus").textContent = status.current_status || "Idle";
  $("syncLastAttempt").textContent = fmtDateTime(status.last_attempt);
  $("syncLastSuccess").textContent = fmtDateTime(status.last_success);
  $("syncLastMode").textContent = status.last_mode || "-";
  $("syncRowsFetched").textContent = String(status.rows_fetched || 0);
  $("syncNewSnapshotsImported").textContent = String(status.new_raw_snapshots_imported || 0);
  $("syncDuplicateSnapshotsSkipped").textContent = String(status.duplicate_snapshots_skipped || 0);
  $("syncReviewItemsChanged").textContent = String(status.review_items_changed || 0);
  $("syncNextAutomatic").textContent = fmtDateTime(status.next_automatic_sync);
  $("syncRawSnapshotCount").textContent = String(status.raw_snapshot_count || 0);
  $("syncOpenReviewCount").textContent = String(status.open_review_count || 0);
  $("syncLastError").textContent = status.last_error || "-";
}

function setSyncRunMessage(message, isSuccess = false) {
  const node = $("syncRunMessage");
  node.textContent = message;
  node.className = isSuccess ? "settings-message success" : "settings-message";
}

function setSyncRunning(isRunning) {
  state.syncRunning = isRunning;
  $("syncNowBtn").disabled = isRunning;
  $("syncNowBtn").textContent = isRunning ? "Syncing..." : "Sync Calendar";
  if ($("syncRebuildBtn")) $("syncRebuildBtn").disabled = isRunning;
}

async function loadSyncStatus() {
  setSyncRunMessage("");
  const status = await api("/api/sync/status");
  renderSyncStatus(status);
}

async function runSyncNow() {
  if (state.syncRunning) return;
  setSyncRunning(true);
  setSyncRunMessage("");
  try {
    const result = await api("/api/sync/run", { method: "POST", body: JSON.stringify({}) });
    renderSyncStatus(result.status);
    setSyncRunMessage(`Sync complete. Fetched ${result.rows_fetched} row(s); imported ${result.rows_imported} new row(s); skipped ${result.duplicate_snapshots_skipped || 0} duplicate snapshot(s); changed ${result.review_items_changed || 0} review item(s).`, true);
    await refreshDashboardStatus();
    if (!document.querySelector("[hidden]#calendarImportView")) await loadList();
  } catch (err) {
    setSyncRunMessage(err.message || "Sync failed.");
    try {
      renderSyncStatus(await api("/api/sync/status"));
    } catch (_) {}
  } finally {
    setSyncRunning(false);
  }
}

async function showCalendarImport() {
  hideViews();
  $("calendarImportView").hidden = false;
  $("calendarImportNav").classList.add("active");
  $("pageTitle").textContent = "Calendar Import";
  $("pageSubtitle").textContent = "Pull Shortcut snapshots already staged in Google Sheets";
  document.title = "Jordana Billing - Calendar Import";
  await loadSyncStatus();
}

async function rebuildCalendarDataFromSheet() {
  if (state.syncRunning) return;
  const confirmed = confirm("Rebuild Calendar Data from Sheet rereads all staged Sheet evidence, creates a private SQLite backup first, and preserves approved sessions, invoices, rates, Bill To selections, and payments. Continue?");
  if (!confirmed) return;
  setSyncRunning(true);
  $("syncRebuildMessage").textContent = "";
  try {
    const result = await api("/api/sync/rebuild", { method: "POST", body: JSON.stringify({ confirmed: true }) });
    renderSyncStatus(result.status);
    $("syncRebuildMessage").className = "settings-message success";
    $("syncRebuildMessage").textContent = `Rebuild complete. Fetched ${result.rows_fetched} row(s); imported ${result.rows_imported} new row(s); skipped ${result.duplicate_snapshots_skipped || 0} duplicate snapshot(s).`;
    await refreshDashboardStatus();
    await loadList();
  } catch (err) {
    $("syncRebuildMessage").className = "settings-message";
    $("syncRebuildMessage").textContent = err.message || "Rebuild failed.";
    try {
      renderSyncStatus(await api("/api/sync/status"));
    } catch (_) {}
  } finally {
    setSyncRunning(false);
  }
}

function businessProfileFromResponse(profile) {
  return {
    ...BUSINESS_PROFILE_DEFAULTS,
    ...(profile || {}),
    logo_contains_business_details: !!profile?.logo_contains_business_details,
    show_email_below_logo: !!profile?.show_email_below_logo
  };
}

function businessProfileFieldValue(name) {
  return $(name)?.type === "checkbox" ? $(name).checked : $(name).value.trim();
}

function paymentAddressReady() {
  return Boolean(
    businessProfileFieldValue("paymentAddressLine1Input") &&
    businessProfileFieldValue("paymentCityInput") &&
    businessProfileFieldValue("paymentStateInput") &&
    businessProfileFieldValue("paymentPostalCodeInput")
  );
}

function renderBusinessProfileReadiness() {
  const missing = [];
  if (!businessProfileFieldValue("businessNameInput")) missing.push("business name");
  if (!businessProfileFieldValue("payeeNameInput")) missing.push("payee name");
  if (!paymentAddressReady()) missing.push("payment address");
  if (!businessProfileFieldValue("zelleRecipientInput")) missing.push("Zelle recipient");
  const notice = $("settingsReadiness");
  if (!missing.length) {
    notice.textContent = "Invoice settings are ready for future finalized invoices.";
    notice.className = "settings-readiness ready";
    return;
  }
  notice.textContent = `Missing for invoice readiness: ${missing.join(", ")}.`;
  notice.className = "settings-readiness";
}

function setBusinessProfileMessage(message, isSuccess = false) {
  const node = $("businessProfileMessage");
  node.textContent = message;
  node.className = isSuccess ? "settings-message success" : "settings-message";
}

function setBusinessProfileSaving(isSaving) {
  state.settingsSaving = isSaving;
  $("businessProfileSaveBtn").disabled = isSaving;
  $("businessProfileSaveBtn").textContent = isSaving ? "Saving..." : "Save Invoice Settings";
}

function populateBusinessProfileForm(profile) {
  const next = businessProfileFromResponse(profile);
  $("businessNameInput").value = next.business_name;
  $("providerDisplayNameInput").value = next.provider_display_name;
  $("credentialsDisplayInput").value = next.credentials_display;
  $("addressLine1Input").value = next.address_line_1;
  $("addressLine2Input").value = next.address_line_2;
  $("cityInput").value = next.city;
  $("stateInput").value = next.state;
  $("postalCodeInput").value = next.postal_code;
  $("phoneInput").value = next.phone;
  $("emailInput").value = next.email;
  $("payeeNameInput").value = next.payee_name;
  $("paymentAddressLine1Input").value = next.payment_address_line_1;
  $("paymentAddressLine2Input").value = next.payment_address_line_2;
  $("paymentCityInput").value = next.payment_city;
  $("paymentStateInput").value = next.payment_state;
  $("paymentPostalCodeInput").value = next.payment_postal_code;
  $("zelleRecipientInput").value = next.zelle_recipient;
  $("logoPathInput").value = next.logo_path;
  $("logoContainsBusinessDetailsInput").checked = next.logo_contains_business_details;
  $("showEmailBelowLogoInput").checked = next.show_email_below_logo;
  $("invoiceTotalLabelInput").value = next.invoice_total_label;
  $("invoiceNumberFormatInput").value = next.invoice_number_format;
  $("insuranceEinInput").value = next.insurance_ein || "";
  $("insuranceNpiInput").value = next.insurance_npi || "";
  $("insuranceSwInput").value = next.insurance_sw || "";
  renderBusinessProfileReadiness();
}

function collectBusinessProfilePayload() {
  return {
    business_name: $("businessNameInput").value.trim(),
    provider_display_name: $("providerDisplayNameInput").value.trim(),
    credentials_display: $("credentialsDisplayInput").value.trim(),
    address_line_1: $("addressLine1Input").value.trim(),
    address_line_2: $("addressLine2Input").value.trim(),
    city: $("cityInput").value.trim(),
    state: $("stateInput").value.trim(),
    postal_code: $("postalCodeInput").value.trim(),
    phone: $("phoneInput").value.trim(),
    email: $("emailInput").value.trim(),
    payee_name: $("payeeNameInput").value.trim(),
    payment_address_line_1: $("paymentAddressLine1Input").value.trim(),
    payment_address_line_2: $("paymentAddressLine2Input").value.trim(),
    payment_city: $("paymentCityInput").value.trim(),
    payment_state: $("paymentStateInput").value.trim(),
    payment_postal_code: $("paymentPostalCodeInput").value.trim(),
    zelle_recipient: $("zelleRecipientInput").value.trim(),
    logo_path: $("logoPathInput").value.trim(),
    logo_contains_business_details: $("logoContainsBusinessDetailsInput").checked,
    show_email_below_logo: $("showEmailBelowLogoInput").checked,
    invoice_total_label: $("invoiceTotalLabelInput").value.trim() || BUSINESS_PROFILE_DEFAULTS.invoice_total_label,
    invoice_number_format: $("invoiceNumberFormatInput").value.trim() || BUSINESS_PROFILE_DEFAULTS.invoice_number_format,
    insurance_ein: $("insuranceEinInput").value.trim(),
    insurance_npi: $("insuranceNpiInput").value.trim(),
    insurance_sw: $("insuranceSwInput").value.trim(),
  };
}

async function loadBusinessProfile() {
  setBusinessProfileMessage("");
  const profile = await api("/api/business-profile");
  populateBusinessProfileForm(profile);
}

async function saveBusinessProfile(event) {
  event.preventDefault();
  if (state.settingsSaving) return;
  const payload = collectBusinessProfilePayload();
  if (!payload.business_name) {
    setBusinessProfileMessage("Business name is required.");
    $("businessNameInput").focus();
    renderBusinessProfileReadiness();
    return;
  }
  setBusinessProfileSaving(true);
  setBusinessProfileMessage("");
  try {
    const saved = await api("/api/business-profile", { method: "POST", body: JSON.stringify(payload) });
    populateBusinessProfileForm(saved);
    setBusinessProfileMessage("Invoice settings saved.", true);
  } catch (err) {
    setBusinessProfileMessage(err.message || "Failed to save invoice settings.");
  } finally {
    setBusinessProfileSaving(false);
  }
}

async function showSettings() {
  hideViews();
  $("settingsView").hidden = false;
  $("settingsNav").classList.add("active");
  $("pageTitle").textContent = "Invoice Settings";
  $("pageSubtitle").textContent = "Business profile for future finalized invoices";
  document.title = "Jordana Billing - Invoice Settings";
  await loadBusinessProfile();
}

function _invoiceDateRangeParams() {
  const filter = state.invoiceLibrary.dateFilter;
  if (!filter) return {};
  const now = new Date();
  const y = now.getFullYear();
  const m = now.getMonth();
  if (filter === "this_month") {
    const start = `${y}-${String(m + 1).padStart(2, "0")}-01`;
    const next = new Date(y, m + 1, 1);
    const end = `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, "0")}-${String(next.getDate()).padStart(2, "0")}`;
    return { invoice_date_from: start, invoice_date_to: end };
  }
  if (filter === "last_month") {
    const prev = new Date(y, m - 1, 1);
    const start = `${prev.getFullYear()}-${String(prev.getMonth() + 1).padStart(2, "0")}-01`;
    const end = `${y}-${String(m + 1).padStart(2, "0")}-01`;
    return { invoice_date_from: start, invoice_date_to: end };
  }
  if (filter === "this_quarter") {
    const qStart = Math.floor(m / 3) * 3;
    const start = `${y}-${String(qStart + 1).padStart(2, "0")}-01`;
    const qEndMonth = qStart + 3;
    const next = new Date(y, qEndMonth, 1);
    const end = `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, "0")}-${String(next.getDate()).padStart(2, "0")}`;
    return { invoice_date_from: start, invoice_date_to: end };
  }
  if (filter === "this_year") {
    return { invoice_date_from: `${y}-01-01`, invoice_date_to: `${y}-12-31` };
  }
  if (filter === "custom") {
    const params = {};
    if (state.invoiceLibrary.dateFrom) params.invoice_date_from = state.invoiceLibrary.dateFrom;
    if (state.invoiceLibrary.dateTo) params.invoice_date_to = state.invoiceLibrary.dateTo;
    return params;
  }
  return {};
}

function _buildInvoiceQueryParams() {
  const lib = state.invoiceLibrary;
  const params = new URLSearchParams();
  if (lib.status) params.set("status", lib.status);
  if (lib.search) params.set("search", lib.search);
  if (lib.billToPartyId) params.set("bill_to_party_id", lib.billToPartyId);
  if (lib.paymentStatus) params.set("payment_status", lib.paymentStatus);
  const dateRange = _invoiceDateRangeParams();
  if (dateRange.invoice_date_from) params.set("invoice_date_from", dateRange.invoice_date_from);
  if (dateRange.invoice_date_to) params.set("invoice_date_to", dateRange.invoice_date_to);
  params.set("limit", String(lib.limit));
  params.set("offset", String(lib.offset));
  return params.toString();
}

async function loadInvoiceBillToFilter() {
  const parties = await api("/api/billing-parties?q=");
  const select = $("invoiceBillToFilter");
  const current = select.value;
  select.innerHTML = '<option value="">All Bill To</option>' + parties.map(p =>
    `<option value="${escapeAttr(p.billing_party_id)}">${fmt(p.billing_name)}</option>`
  ).join("");
  select.value = current;
}

async function loadInvoices() {
  const lib = state.invoiceLibrary;
  lib.status = $("invoiceStatusFilter").value || "";
  lib.paymentStatus = $("invoicePaymentStatusFilter").value || "";
  lib.billToPartyId = $("invoiceBillToFilter").value || "";
  lib.dateFilter = $("invoiceDateFilter").value || "";
  lib.dateFrom = $("invoiceDateFrom") ? $("invoiceDateFrom").value : "";
  lib.dateTo = $("invoiceDateTo") ? $("invoiceDateTo").value : "";
  lib.search = $("invoiceSearch") ? $("invoiceSearch").value.trim() : "";
  const qs = _buildInvoiceQueryParams();
  const result = await api(`/api/invoices?${qs}`);
  lib.items = result.items || [];
  lib.total = result.total || 0;
  lib.loaded = true;
  renderInvoiceLibrary();
  await loadFinancialSummary();
}

function renderInvoiceLibrary() {
  const lib = state.invoiceLibrary;
  const items = lib.items;
  const tbody = $("invoiceRows");
  tbody.innerHTML = items.length
    ? items.map(row => `
      <tr data-invoice="${escapeAttr(row.invoice_id)}">
        <td><span class="primary">${fmt(row.invoice_number || "Draft")}</span></td>
        <td>${fmt(row.invoice_date)}</td>
        <td>${fmt(row.billing_period_start)} – ${fmt(row.billing_period_end)}</td>
        <td>${fmt(row.bill_to_name_snapshot || row.current_bill_to_name)}</td>
        <td>${fmt(row.filing_owner_display || "—")}</td>
        <td>${escapeHtml(row.participants_display || "—")}</td>
        <td><span class="status-pill ${escapeAttr(row.status)}">${fmt(row.status)}</span></td>
        <td><span class="status-pill ${escapeAttr(row.payment_status || "unpaid")}">${escapeHtml(paymentStatusLabel(row.payment_status))}</span></td>
        <td>${money(centString(row.total_cents))}</td>
        <td>${money(centString(row.paid_cents || 0))}</td>
        <td>${money(centString(row.balance_cents || 0))}</td>
        <td><button class="mini" data-open-invoice="${escapeAttr(row.invoice_id)}">Open</button></td>
      </tr>`).join("")
    : `<tr><td colspan="12" class="readonly-note">No invoices found.</td></tr>`;
  document.querySelectorAll("#invoiceRows [data-open-invoice]").forEach(btn => {
    btn.onclick = (e) => { e.stopPropagation(); openInvoice(btn.dataset.openInvoice); };
  });
  document.querySelectorAll("#invoiceRows tr[data-invoice]").forEach(row => {
    row.onclick = () => openInvoice(row.dataset.invoice);
  });
  const start = lib.offset + 1;
  const end = Math.min(lib.offset + lib.limit, lib.total);
  $("invoiceResultCount").textContent = lib.total === 0 ? "No results" : `Showing ${start}–${end} of ${lib.total}`;
  $("invoicePrevPage").disabled = lib.offset === 0;
  $("invoiceNextPage").disabled = lib.offset + lib.limit >= lib.total;
}

async function startInvoiceBuilder() {
  const parties = await api("/api/billing-parties?q=");
  const today = new Date().toISOString().slice(0, 10);
  const monthStart = `${today.slice(0,7)}-01`;
  $("invoiceWorkspace").innerHTML = `<div class="invoice-builder">
    <div><h3>Create Invoice Draft</h3><div class="help">Only approved, invoice-eligible sessions can be selected.</div></div>
    <div class="field-grid">
      <label class="field wide">Bill to<select id="draftBillTo"><option value="">Select bill-to party</option>${parties.map(p => `<option value="${escapeAttr(p.billing_party_id)}">${fmt(p.billing_name)}</option>`).join("")}</select></label>
      <label class="field">Period start<input id="draftPeriodStart" type="date" value="${monthStart}"></label>
      <label class="field">Period end<input id="draftPeriodEnd" type="date" value="${today}"></label>
      <label class="field">Invoice date<input id="draftInvoiceDate" type="date" value="${today}"></label>
      <label class="field">Delivery<select id="draftDelivery">${optionSet(["unresolved","email","mail","both"], "unresolved")}</select></label>
    </div>
    <div><div class="section-title-row"><h3>Eligible sessions</h3><button id="refreshEligible" class="mini">Refresh</button></div><div class="eligible-list" id="eligibleSessions"><div class="empty-state">Select a bill-to party and period.</div></div></div>
    <div class="actions"><button id="saveInvoiceDraft" class="save">Save Draft</button></div>
  </div>`;
  ["draftBillTo","draftPeriodStart","draftPeriodEnd"].forEach(id => $(id).onchange = loadEligibleInvoiceSessions);
  $("refreshEligible").onclick = loadEligibleInvoiceSessions;
  $("saveInvoiceDraft").onclick = async () => {
    const sessionIds = [...document.querySelectorAll("#eligibleSessions input:checked")].map(input => input.value);
    const created = await api("/api/invoices", { method:"POST", body:JSON.stringify({
      bill_to_party_id:$("draftBillTo").value, billing_period_start:$("draftPeriodStart").value,
      billing_period_end:$("draftPeriodEnd").value, invoice_date:$("draftInvoiceDate").value,
      delivery_method:$("draftDelivery").value, session_ids:sessionIds
    })});
    await loadInvoices(); await renderInvoiceEditor(created);
  };
}

async function loadEligibleInvoiceSessions() {
  const party = $("draftBillTo").value;
  if (!party) return;
  const rows = await api(`/api/invoices/eligible-sessions?bill_to_party_id=${encodeURIComponent(party)}&period_start=${$("draftPeriodStart").value}&period_end=${$("draftPeriodEnd").value}`);
  state.eligibleSessions = rows;
  $("eligibleSessions").innerHTML = rows.map(row => `<label class="eligible-row ${row.eligible ? "" : "ineligible"}">
    <input type="checkbox" value="${escapeAttr(row.id)}" ${row.eligible ? "" : "disabled"}><span>${fmt(row.session_date)}</span><span>${fmt(row.participants)}<small class="secondary">${escapeHtml(row.ineligibility_reasons.join("; "))}</small></span><span>${serviceLabel(row.service_mode)}</span><strong>${money(centString(row.rate_cents_snapshot || row.approved_rate_cents))}</strong>
  </label>`).join("") || `<div class="empty-state">No sessions in this period.</div>`;
}

async function openInvoice(invoiceId) {
  const data = await api(`/api/invoices/${invoiceId}`);
  state.invoice = data;
  if (data.invoice.status === "draft") return renderInvoiceEditor(data);
  renderInvoicePreview(data);
}

async function renderInvoiceEditor(data) {
  state.invoice = data;
  const i = data.invoice;
  const filing = data.filing_owner || {};
  const selectedFilingId = (filing.selected && filing.selected.person_id) || i.filing_owner_person_id || "";
  const filingOptions = (filing.eligible_clients || []).map(person =>
    `<option value="${escapeAttr(person.person_id)}" ${person.person_id === selectedFilingId ? "selected" : ""}>${fmt(person.display_name)}${person.person_code ? ` (${escapeHtml(person.person_code)})` : ""}</option>`
  ).join("");
  const filingControl = `<label class="field wide">File invoice under<select id="filingOwnerSelect"><option value="">Select filing client</option>${filingOptions}</select><span class="help">${escapeHtml(filing.message || (filing.selected ? `Resolved from ${String(filing.source || "").replaceAll("_", " ")}.` : "Choose the client folder for the finalized PDF."))}</span></label>`;
  $("invoiceWorkspace").innerHTML = `<div class="invoice-builder">
    <div class="section-title-row"><h3>Draft Invoice</h3><span class="status-pill">Draft</span></div>
    <div class="field-grid">
      <label class="field">Invoice date<input id="editInvoiceDate" type="date" value="${escapeAttr(i.invoice_date)}"></label>
      <label class="field">Delivery<select id="editDelivery">${optionSet(["unresolved","email","mail","both"], i.delivery_method)}</select></label>
      ${filingControl}
    </div>
    <table class="invoice-editor-lines"><thead><tr><th>Date / participants</th><th>Description</th><th>Duration</th><th>Amount</th><th></th></tr></thead><tbody>${data.lines.map(line => `<tr data-line="${escapeAttr(line.invoice_line_item_id)}" data-description="${escapeAttr(line.description_snapshot)}"><td>${escapeHtml(line.service_date)}<small class="secondary">${fmt(line.participants_snapshot)}</small></td><td>${escapeHtml(line.description_snapshot)}</td><td>${line.duration_minutes == null ? "-" : `${line.duration_minutes} min`}</td><td>${money(centString(line.line_amount_cents))}</td><td><div class="line-item-actions"><button class="edit-line secondary" type="button">Edit</button><button class="remove-line danger" type="button">×</button></div></td></tr>`).join("")}</tbody></table>
    <div class="invoice-total"><span>TOTAL</span><span>${money(centString(i.total_cents))}</span></div>
    <div class="actions"><button id="saveDraftChanges" class="save">Save Draft</button><button id="addDraftSessions">Add Sessions</button><button id="printPreviewBtn">Preview PDF</button><button id="reviewFinalizeBtn" class="approve">Review and Finalize</button></div>
  </div>`;

  document.querySelectorAll(".edit-line").forEach(button => button.onclick = () => {
    const row = button.closest("tr");
    const lineId = row.dataset.line;
    const line = data.lines.find(l => l.invoice_line_item_id === lineId);
    openLineEditModal(line, data);
  });

  document.querySelectorAll(".remove-line").forEach(button => button.onclick = async () => {
    const lineId = button.closest("tr").dataset.line;
    const updated = await api(`/api/invoices/${i.invoice_id}/remove-line`, {method:"POST", body:JSON.stringify({invoice_line_item_id:lineId})});
    await renderInvoiceEditor(updated); await loadInvoices();
  });

  $("saveDraftChanges").onclick = async () => {
    const lines = [...document.querySelectorAll("#invoiceWorkspace tr[data-line]")].map((row, index) => ({invoice_line_item_id:row.dataset.line, description_snapshot:row.dataset.description, sort_order:index}));
    const updated = await api(`/api/invoices/${i.invoice_id}`, {method:"POST", body:JSON.stringify({invoice_date:$("editInvoiceDate").value, delivery_method:$("editDelivery").value, lines})});
    await renderInvoiceEditor(updated); await loadInvoices();
  };

  $("addDraftSessions").onclick = () => showAddSessionsToDraft(data);
  if ($("filingOwnerSelect")) $("filingOwnerSelect").onchange = async () => {
    const updated = await api(`/api/invoices/${i.invoice_id}/filing-owner`, {method:"POST", body:JSON.stringify({person_id:$("filingOwnerSelect").value})});
    await renderInvoiceEditor(updated); await loadInvoices();
  };

  $("reviewFinalizeBtn").onclick = async () => {
    const lines = [...document.querySelectorAll("#invoiceWorkspace tr[data-line]")].map((row, index) => ({invoice_line_item_id:row.dataset.line, description_snapshot:row.dataset.description, sort_order:index}));
    const draftData = {invoice_date:$("editInvoiceDate").value, delivery_method:$("editDelivery").value, lines};
    const preview = await api(`/api/invoices/${i.invoice_id}/preview-finalize`, {method:"POST", body:JSON.stringify(draftData)});
    renderFinalizationPreview(preview, {included: false, diagnosisCode: ""});
  };

  $("printPreviewBtn").onclick = () => {
    window.open(`/api/invoices/${i.invoice_id}/draft-pdf`, "_blank");
  };
  // /print-preview HTML endpoint remains available as a fallback
}


function openLineEditModal(line, invoiceData) {
  const originatingBtn = document.activeElement;
  closeLineEditorModal();

  const overlay = document.createElement("div");
  overlay.id = "lineEditorModalOverlay";
  overlay.className = "billing-modal-overlay";

  const hasSession = !!line.source_session_id;

  overlay.innerHTML = `
    <div class="billing-modal line-editor-modal" id="lineEditorModal" role="dialog" aria-modal="true" aria-labelledby="lineEditorModalTitle">
      <h3 id="lineEditorModalTitle">Edit Invoice Line</h3>
      <p class="modal-instruction">Modify the description or amount for this draft invoice line.</p>
      
      <div class="modal-body" style="display: flex; flex-direction: column; gap: 12px; margin-bottom: 16px;">
        <div class="modal-error" id="lineEditorError" role="alert" style="color: var(--red); font-size: 13px; display: none;"></div>
        
        <div style="display: flex; flex-direction: column; gap: 4px;">
          <label style="font-size: 12px; color: var(--muted); font-weight: bold;">Description</label>
          <input type="text" id="lineEditDescription" style="padding: 7px 10px; border: 1px solid var(--line); border-radius: 6px; font-size: 13px;" required />
        </div>
        
        <div style="display: flex; flex-direction: column; gap: 4px;">
          <label style="font-size: 12px; color: var(--muted); font-weight: bold;">Amount ($)</label>
          <input type="text" id="lineEditAmount" style="padding: 7px 10px; border: 1px solid var(--line); border-radius: 6px; font-size: 13px;" required />
        </div>
        
        <div id="lineEditScopeSection" style="display: ${hasSession ? 'block' : 'none'}; flex-direction: column; gap: 4px;">
          <label style="font-size: 12px; color: var(--muted); font-weight: bold;">Correction Scope</label>
          <div style="display: flex; gap: 16px; font-size: 13px; margin-top: 4px;">
            <label style="display: flex; align-items: center; gap: 6px; cursor: pointer;">
              <input type="radio" name="lineEditScope" value="invoice_line_only" checked /> Invoice line only
            </label>
            <label style="display: flex; align-items: center; gap: 6px; cursor: pointer;">
              <input type="radio" name="lineEditScope" value="invoice_line_and_session" /> Invoice line and approved session
            </label>
          </div>
        </div>
        
        <div id="lineEditReasonSection" style="display: none; flex-direction: column; gap: 4px;">
          <label for="lineEditReason" style="font-size: 12px; color: var(--muted); font-weight: bold;">Correction Reason (required)</label>
          <textarea id="lineEditReason" placeholder="Enter reason for changing the amount..." style="padding: 7px 10px; border: 1px solid var(--line); border-radius: 6px; font-size: 13px; min-height: 60px; resize: vertical;"></textarea>
        </div>
      </div>
      
      <div class="modal-actions" style="display: flex; gap: 8px; justify-content: flex-end; margin-top: 12px;">
        <button type="button" class="save" id="lineEditorSave">Save Changes</button>
        <button type="button" class="modal-cancel" id="lineEditorCancel">Cancel</button>
      </div>
    </div>
  `;

  document.body.appendChild(overlay);
  document.body.style.overflow = "hidden";

  const descInput = document.getElementById("lineEditDescription");
  const amountInput = document.getElementById("lineEditAmount");
  const reasonTextarea = document.getElementById("lineEditReason");
  const reasonSection = document.getElementById("lineEditReasonSection");
  const errorDisplay = document.getElementById("lineEditorError");
  const saveBtn = document.getElementById("lineEditorSave");
  const cancelBtn = document.getElementById("lineEditorCancel");

  descInput.value = line.description_snapshot;
  amountInput.value = centString(line.line_amount_cents);

  const originalCents = line.line_amount_cents;

  const checkAmountChange = () => {
    const amountStr = amountInput.value.trim().replace(/[$,]/g, "");
    const isValidAmount = /^\d+(\.\d{1,2})?$/.test(amountStr);
    if (!isValidAmount) {
      reasonSection.style.display = "none";
      return;
    }
    const newCents = Math.round(parseFloat(amountStr) * 100);
    if (newCents !== originalCents) {
      reasonSection.style.display = "flex";
    } else {
      reasonSection.style.display = "none";
    }
  };

  amountInput.addEventListener("input", checkAmountChange);

  const closeAndRestore = () => {
    closeLineEditorModal();
    if (originatingBtn) originatingBtn.focus();
  };

  cancelBtn.onclick = closeAndRestore;

  saveBtn.onclick = async () => {
    errorDisplay.style.display = "none";
    errorDisplay.textContent = "";

    const desc = descInput.value.trim();
    if (!desc) {
      showError("Description must be non-empty.");
      return;
    }

    const amountStr = amountInput.value.trim().replace(/[$,]/g, "");
    if (!/^\d+(\.\d{1,2})?$/.test(amountStr)) {
      showError("Amount must be a non-negative number with at most 2 decimal places.");
      return;
    }

    const newCents = Math.round(parseFloat(amountStr) * 100);
    const amountChanged = (newCents !== originalCents);

    let reason = "";
    let scope = "invoice_line_only";

    if (amountChanged) {
      reason = reasonTextarea.value.trim();
      if (!reason) {
        showError("A correction reason is required when the amount changes.");
        return;
      }

      if (hasSession) {
        const selectedScope = document.querySelector('input[name="lineEditScope"]:checked');
        scope = selectedScope ? selectedScope.value : "invoice_line_only";
      }
    }

    saveBtn.disabled = true;
    cancelBtn.disabled = true;
    descInput.disabled = true;
    amountInput.disabled = true;
    reasonTextarea.disabled = true;
    document.querySelectorAll('input[name="lineEditScope"]').forEach(r => r.disabled = true);

    try {
      const payload = {
        invoice_line_item_id: line.invoice_line_item_id,
        description: desc,
        amount_cents: newCents,
        amount_scope: scope,
        reason: reason,
        expected_revision: invoiceData.invoice.revision
      };

      const updated = await api(`/api/invoices/${invoiceData.invoice.invoice_id}/update-line`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      closeLineEditorModal();
      showReviewSuccess("Invoice line updated successfully.");

      await loadInvoices();
      await renderInvoiceEditor(updated);

      const editBtn = document.querySelector(`#invoiceWorkspace tr[data-line="${escapeAttr(line.invoice_line_item_id)}"] .edit-line`);
      if (editBtn) editBtn.focus();
      else if (originatingBtn) originatingBtn.focus();

    } catch (err) {
      saveBtn.disabled = false;
      cancelBtn.disabled = false;
      descInput.disabled = false;
      amountInput.disabled = false;
      reasonTextarea.disabled = false;
      document.querySelectorAll('input[name="lineEditScope"]').forEach(r => r.disabled = false);

      showError(err.message || "An unexpected error occurred.");
    }
  };

  function showError(msg) {
    errorDisplay.textContent = msg;
    errorDisplay.style.display = "block";
  }

  descInput.focus();
  document.addEventListener("keydown", lineEditorModalTrapKeydown);
}

function closeLineEditorModal() {
  const overlay = document.getElementById("lineEditorModalOverlay");
  if (overlay) {
    overlay.remove();
  }
  document.body.style.overflow = "";
  document.removeEventListener("keydown", lineEditorModalTrapKeydown);
}

function lineEditorModalTrapKeydown(e) {
  if (e.key === "Escape") {
    e.preventDefault();
    const cancelBtn = document.getElementById("lineEditorCancel");
    if (cancelBtn) cancelBtn.click();
  }
}


async function showAddSessionsToDraft(data) {
  const i = data.invoice;
  const rows = await api(`/api/invoices/eligible-sessions?bill_to_party_id=${encodeURIComponent(i.bill_to_party_id)}&period_start=${i.billing_period_start}&period_end=${i.billing_period_end}`);
  const eligible = rows.filter(row => row.eligible);
  $("invoiceWorkspace").innerHTML = `<div class="invoice-builder">
    <div><h3>Add Sessions to Draft</h3><div class="help">Sessions already attached to an invoice are excluded by the backend.</div></div>
    <div class="eligible-list">${eligible.map(row => `<label class="eligible-row"><input type="checkbox" value="${escapeAttr(row.id)}"><span>${fmt(row.session_date)}</span><span>${fmt(row.participants)}</span><span>${serviceLabel(row.service_mode)}</span><strong>${money(centString(row.rate_cents_snapshot || row.approved_rate_cents))}</strong></label>`).join("") || `<div class="empty-state">No additional eligible sessions.</div>`}</div>
    <div class="actions"><button id="confirmAddSessions" class="save" ${eligible.length ? "" : "disabled"}>Add Selected</button><button id="cancelAddSessions">Return to Draft</button></div>`;
  $("cancelAddSessions").onclick = () => renderInvoiceEditor(data);
  $("confirmAddSessions").onclick = async () => {
    const sessionIds = [...document.querySelectorAll("#invoiceWorkspace input:checked")].map(input => input.value);
    if (!sessionIds.length) return;
    const updated = await api(`/api/invoices/${i.invoice_id}/add-sessions`, {method:"POST", body:JSON.stringify({session_ids:sessionIds})});
    await loadInvoices(); renderInvoiceEditor(updated);
  };
}

function buildPreviewSummaryHtml(render, data, invoice) {
  const summary = render.account_summary;
  const fallbackTotal = `<tr style="border-top:1px solid #102a43;border-bottom:none;"><td colspan="4" style="text-align:right;font-weight:850;font-size:13pt;padding:10px 6px;border-bottom:none;">${fmt(render.total_label)}</td><td style="text-align:right;font-weight:850;font-size:13pt;padding:10px 6px;border-bottom:none;">${fmt(render.total_display)}</td></tr>`;

  if (summary && (summary.prior_unpaid_balance_cents > 0 || summary.current_invoice_paid_cents > 0)) {
    const hasPrior = summary.prior_unpaid_balance_cents > 0;
    const hasPayments = summary.current_invoice_paid_cents > 0;

    const rows = [];
    rows.push(["Current Charges", fmt(summary.current_invoice_total_display)]);
    if (hasPayments) {
      rows.push(["Payments Applied", `-${fmt(summary.current_invoice_paid_display)}`]);
      rows.push(["Current Invoice Balance", fmt(summary.current_invoice_balance_display)]);
    }
    if (hasPrior) {
      rows.push(["Prior Unpaid Balance", fmt(summary.prior_unpaid_balance_display)]);
    }

    let rowsHtml = rows.map(([label, amount]) =>
      `<tr><td colspan="4" style="text-align:right;border-bottom:0.3pt solid #D9E2EC;">${fmt(label)}</td><td style="text-align:right;border-bottom:0.3pt solid #D9E2EC;">${amount}</td></tr>`
    ).join("");
    rowsHtml += `<tr style="border-top:1px solid #102a43;border-bottom:none;"><td colspan="4" style="text-align:right;font-weight:850;font-size:13pt;padding:10px 6px;border-bottom:none;">TOTAL AMOUNT DUE</td><td style="text-align:right;font-weight:850;font-size:13pt;padding:10px 6px;border-bottom:none;">${fmt(summary.total_amount_due_display)}</td></tr>`;

    let noteHtml = "";
    const priorList = summary.prior_invoices || [];
    if (priorList.length > 0 && hasPrior) {
      if (priorList.length === 1) {
        const item = priorList[0];
        noteHtml = `<div style="margin-top:4px;font-size:8pt;color:#42526A;">Includes prior invoice ${fmt(item.invoice_number)} dated ${fmt(item.invoice_date)} &mdash; ${money(centString(item.remaining_balance_cents))} remaining</div>`;
      } else {
        const itemsHtml = priorList.map(item => `<div>Invoice ${fmt(item.invoice_number)} &middot; ${fmt(item.invoice_date)} &mdash; ${money(centString(item.remaining_balance_cents))} remaining</div>`).join("");
        noteHtml = `<div style="margin-top:4px;font-size:8pt;color:#42526A;line-height:1.4;"><strong style="color:#102A43;">Prior unpaid invoices:</strong>${itemsHtml}</div>`;
      }
    }

    return { rowsHtml, noteHtml };
  } else if ((invoice.status === "finalized" || invoice.status === "void") && !data.as_finalized_summary) {
    return {
      rowsHtml: fallbackTotal,
      noteHtml: `<div style="margin: 16px 0; padding: 10px; background: #F0F4F8; color: #42526A; font-size: 9pt; border-radius: 4px; border: 1px dashed #BCCCDC; width: 100%; text-align: center; line-height: 1.4;">Historical account summary snapshot not available for this legacy invoice.</div>`,
    };
  }

  return { rowsHtml: fallbackTotal, noteHtml: "" };
}

function renderFinalizationPreview(preview, insuranceState) {
  const i = preview.invoice;
  const render = preview.render_model || {};
  const logoHtml = render.logo_data_uri
    ? `<div class="invoice-preview-logo"><img src="${escapeAttr(render.logo_data_uri)}" alt="Jordana invoice logo"></div>`
    : "";
  const senderHtml = (render.sender_lines || []).map(line => `<div>${fmt(line)}</div>`).join("");
  const billToHtml = (render.bill_to_lines || []).map(line => `<div>${fmt(line)}</div>`).join("");
  const revision = preview.preview_revision;
  const readiness = preview.readiness || {ready: true, errors: []};
  const filing = preview.filing_owner || {};
  const filingName = (filing.selected && filing.selected.display_name) || preview.invoice.filing_owner_display || "";
  const ready = readiness.ready;
  const readinessHtml = ready
    ? `<div class="settings-readiness ready">Ready to finalize — all checks passed.</div>`
    : `<div class="settings-readiness not-ready"><strong>Not ready to finalize.</strong> Fix the following before confirming:<ul>${readiness.errors.map(e => `<li>${escapeHtml(e.message)}</li>`).join("")}</ul></div>`;
  const notesHtml = render.notes ? `<div class="invoice-notes"><b>Notes:</b> ${escapeHtml(render.notes)}</div>` : "";
  const profile = preview.business_profile || {};
  const insState = insuranceState || {included: false, diagnosisCode: ""};
  const insuranceChecked = insState.included ? "checked" : "";
  const insuranceDiagnosis = escapeAttr(insState.diagnosisCode || "");
  const insuranceEin = escapeHtml(profile.insurance_ein || "");
  const insuranceNpi = escapeHtml(profile.insurance_npi || "");
  const insuranceSw = escapeHtml(profile.insurance_sw || "");
  const insuranceBlockHtml = insState.included ? `
      <div class="insurance-coding-preview" style="margin-top:10px;font-size:9pt;line-height:1.3;">
        <div>Diagnosis Code: ${escapeHtml(insState.diagnosisCode || "")}</div>
        <div>EIN: ${insuranceEin}</div>
        <div>NPI: ${insuranceNpi}</div>
        <div>SW: ${insuranceSw}</div>
      </div>` : "";
  $("invoiceWorkspace").innerHTML = `<div class="invoice-builder"><div class="section-title-row"><h3>Invoice Preview</h3><span class="status-pill">Draft</span></div>
    <div class="help">Review the invoice below carefully. Click <strong>Finalize Invoice</strong> to finalize. If the invoice has changed since this preview, finalization will be rejected.</div>
    ${readinessHtml}
    <div id="finalizeError" class="reports-error" style="display:none;"></div>
    <div class="relationship-summary ${filingName ? "success" : ""}"><strong>File invoice under</strong><div>${fmt(filingName || "Selection required")}</div></div>
    <div class="insurance-coding-section" style="margin:12px 0;padding:10px;border:1px solid #D9E2EC;border-radius:4px;">
      <label class="checkbox-field"><input id="insuranceCodingCheckbox" type="checkbox" ${insuranceChecked} /><span>Add Insurance Coding</span></label>
      <div id="insuranceCodingFields" style="margin-top:8px;${insState.included ? "" : "display:none;"}">
        <label class="field">Diagnosis Code
          <input id="insuranceDiagnosisCodeInput" type="text" value="${insuranceDiagnosis}" />
        </label>
        <div style="margin-top:6px;font-size:9pt;color:#42526A;">
          <div>EIN: ${insuranceEin}</div>
          <div>NPI: ${insuranceNpi}</div>
          <div>SW: ${insuranceSw}</div>
        </div>
      </div>
    </div>
    <article class="invoice-preview">
      <header class="invoice-preview-header">
        <div class="invoice-preview-left">
          ${logoHtml}
          <div class="invoice-preview-sender">${senderHtml}</div>
          <div class="invoice-billto"><strong>BILL TO</strong>${billToHtml}</div>
        </div>
        <div class="invoice-preview-title">
          <h3>INVOICE</h3>
          <div><strong>Invoice Date:</strong> ${fmt(render.invoice_date_display)}</div>
          <div><strong>Billing Period:</strong> ${fmt(render.billing_period_display)}</div>
          <div><strong>Invoice Number:</strong> ${fmt(render.invoice_number_display)}</div>
        </div>
      </header>
      <table class="invoice-preview-table"><thead><tr><th>Date</th><th>Participants</th><th>Service</th><th>Duration</th><th>Amount</th></tr></thead><tbody>${(render.lines || []).map(line => `<tr><td>${fmt(line.service_date_display)}</td><td>${fmt(line.participants_display)}</td><td>${fmt(line.description_display)}</td><td>${fmt(line.duration_display)}</td><td>${fmt(line.amount_display)}</td></tr>`).join("")}${buildPreviewSummaryHtml(render, preview, i).rowsHtml}</tbody></table>
      ${buildPreviewSummaryHtml(render, preview, i).noteHtml}
      <div class="invoice-payment"><div><b>${fmt(render.payment_title)}</b></div><div>${fmt(render.payment_name)}</div>${(render.payment_lines || []).map(line => `<div>${fmt(line)}</div>`).join("")}${render.payment_zelle_line ? `<div>${fmt(render.payment_zelle_line)}</div>` : ""}</div>
      ${insuranceBlockHtml}
      ${notesHtml}
    </article>
    <div class="actions"><button id="confirmFinalizeBtn" class="approve" ${ready ? "" : "disabled"}>Finalize Invoice</button><button id="repreviewBtn">Update Preview</button><button id="draftPdfPreviewBtn">Preview PDF</button><button id="backToDraftBtn">Back to Draft</button></div>
  </div>`;
  const finalizeBtn = $("confirmFinalizeBtn");
  const backBtn = $("backToDraftBtn");
  const errorDiv = $("finalizeError");
  const insCheckbox = $("insuranceCodingCheckbox");
  const insFields = $("insuranceCodingFields");
  const insDiagnosisInput = $("insuranceDiagnosisCodeInput");
  const repreviewBtn = $("repreviewBtn");
  backBtn.onclick = () => { state.finalizeInProgress = false; renderInvoiceEditor(preview); };
  if (insCheckbox) insCheckbox.onchange = () => {
    insFields.style.display = insCheckbox.checked ? "" : "none";
  };
  function collectInsurancePayload() {
    return {
      insurance_coding_included: insCheckbox ? insCheckbox.checked : false,
      insurance_diagnosis_code: insDiagnosisInput ? insDiagnosisInput.value.trim() : "",
    };
  }
  if (repreviewBtn) repreviewBtn.onclick = async () => {
    const lines = [...document.querySelectorAll("#invoiceWorkspace tr[data-line]")].map((row, index) => ({invoice_line_item_id:row.dataset.line, description_snapshot:row.dataset.description, sort_order:index}));
    const draftData = {invoice_date:$("editInvoiceDate")?.value || i.invoice_date, delivery_method:$("editDelivery")?.value || i.delivery_method, lines, ...collectInsurancePayload()};
    try {
      const repreview = await api(`/api/invoices/${i.invoice_id}/preview-finalize`, {method:"POST", body:JSON.stringify(draftData)});
      renderFinalizationPreview(repreview, {included: collectInsurancePayload().insurance_coding_included, diagnosisCode: collectInsurancePayload().insurance_diagnosis_code});
    } catch (err) {
      errorDiv.textContent = err.message || "Failed to update preview.";
      errorDiv.style.display = "block";
    }
  };
  if ($("draftPdfPreviewBtn")) $("draftPdfPreviewBtn").onclick = async () => {
    const payload = collectInsurancePayload();
    try {
      const res = await fetch(`/api/invoices/${i.invoice_id}/draft-pdf`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Jordana-Write-Token": getWriteToken(),
        },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        let msg = "Failed to generate PDF preview.";
        try {
          const err = await res.json();
          if (err && err.error) msg = err.error;
        } catch {
          try {
            const text = await res.text();
            if (text && text.length < 200) msg = text;
          } catch { /* use default */ }
        }
        throw new Error(msg);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      window.open(url, "_blank");
    } catch (err) {
      errorDiv.textContent = err.message || "Failed to generate PDF preview.";
      errorDiv.style.display = "block";
    }
  };
  finalizeBtn.onclick = async () => {
    if (state.finalizeInProgress) return;
    state.finalizeInProgress = true;
    finalizeBtn.disabled = true;
    backBtn.disabled = true;
    errorDiv.style.display = "none";
    errorDiv.textContent = "";
    try {
      const ins = collectInsurancePayload();
      const final = await api(`/api/invoices/${i.invoice_id}/finalize`, {method:"POST", body:JSON.stringify({confirmed:true, expected_revision:revision, insurance_coding_included:ins.insurance_coding_included, insurance_diagnosis_code:ins.insurance_diagnosis_code})});
      state.finalizeInProgress = false;
      state.invoice = final;
      finalizeBtn.disabled = true;
      backBtn.disabled = true;
      await loadInvoices();
      renderInvoicePreview(final);
      showInvoiceSuccess("Invoice finalized successfully.");
    } catch (err) {
      state.finalizeInProgress = false;
      finalizeBtn.disabled = false;
      backBtn.disabled = false;
      errorDiv.textContent = err.message || "An unexpected error occurred during finalization.";
      errorDiv.style.display = "block";
    }
  };
}

function renderInvoicePreview(data) {
  const i = data.invoice;
  const render = data.render_model || {};
  const logoHtml = render.logo_data_uri
    ? `<div class="invoice-preview-logo"><img src="${escapeAttr(render.logo_data_uri)}" alt="Jordana invoice logo"></div>`
    : "";
  const senderHtml = (render.sender_lines || []).map(line => `<div>${fmt(line)}</div>`).join("");
  const billToHtml = (render.bill_to_lines || []).map(line => `<div>${fmt(line)}</div>`).join("");
  const notesHtml = render.notes ? `<div class="invoice-notes"><b>Notes:</b> ${escapeHtml(render.notes)}</div>` : "";
  const voidHtml = i.status === "void" && i.void_reason ? `<div class="invoice-void-info"><strong>Voided:</strong> ${fmt(i.voided_at)} — ${escapeHtml(i.void_reason)}</div>` : "";

  const paidCents = data.current_status ? data.current_status.current_invoice_paid_cents : (i.paid_cents || 0);
  const balanceCents = data.current_status ? data.current_status.current_invoice_balance_cents : (i.balance_cents || 0);
  const paymentSummaryHtml = (i.status === "finalized" || i.status === "void")
    ? `<div>
         <div style="font-size: 11px; font-weight: bold; color: #42526A; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px;">Current Status of This Invoice</div>
         <div class="invoice-payment-summary" style="margin-bottom: 16px;">
           <div class="payment-summary-card"><label>Total Charges</label><strong>${money(centString(i.total_cents))}</strong></div>
           <div class="payment-summary-card"><label>Payments Applied</label><strong>${money(centString(paidCents))}</strong></div>
           <div class="payment-summary-card"><label>Remaining Balance</label><strong>${money(centString(balanceCents))}</strong></div>
           <div class="payment-summary-card"><label>Payment Status</label><strong>${escapeHtml(paymentStatusLabel(i.payment_status))}</strong></div>
         </div>
       </div>`
    : "";

  const filingDisplay = i.filing_owner_display || i.filing_owner_display_name_snapshot || (data.filing_owner?.selected?.display_name) || "";
  const pdfButtonsHtml = i.status === "finalized"
    ? `<button id="openPdfBtn">Open PDF</button><button id="showPdfInFinderBtn">Show in Finder</button><button id="openClientFolderBtn">Open client invoice folder</button><button id="printPdfBtn">Print PDF</button>`
    : "";
  const insCoding = render.insurance_coding;
  const insCodingHtml = insCoding ? `<div class="insurance-coding-preview" style="margin-top:10px;font-size:9pt;line-height:1.3;">${insCoding.map(item => `<div>${escapeHtml(item.label)}: ${escapeHtml(item.value)}</div>`).join("")}</div>` : "";
  $("invoiceWorkspace").innerHTML = `<div class="invoice-builder"><div class="section-title-row"><h3>Invoice Preview</h3><span class="status-pill ${escapeAttr(i.status)}">${fmt(i.status)}</span></div>
    ${voidHtml}
    ${paymentSummaryHtml}
    <div class="relationship-summary"><strong>File invoice under</strong><div>${fmt(filingDisplay || "—")}</div></div>
    <article class="invoice-preview">
      <header class="invoice-preview-header">
        <div class="invoice-preview-left">
          ${logoHtml}
          <div class="invoice-preview-sender">${senderHtml}</div>
          <div class="invoice-billto"><strong>BILL TO</strong>${billToHtml}</div>
        </div>
        <div class="invoice-preview-title"><h3>INVOICE</h3><div><strong>Invoice Date:</strong> ${fmt(render.invoice_date_display)}</div><div><strong>Billing Period:</strong> ${fmt(render.billing_period_display)}</div><div><strong>Invoice Number:</strong> ${fmt(render.invoice_number_display)}</div></div>
      </header>
      <table class="invoice-preview-table"><thead><tr><th>Date</th><th>Participants</th><th>Service</th><th>Duration</th><th>Amount</th></tr></thead><tbody>${(render.lines || []).map(line => `<tr><td>${fmt(line.service_date_display)}</td><td>${fmt(line.participants_display)}</td><td>${fmt(line.description_display)}</td><td>${fmt(line.duration_display)}</td><td>${fmt(line.amount_display)}</td></tr>`).join("")}${buildPreviewSummaryHtml(render, data, i).rowsHtml}</tbody></table>
      ${buildPreviewSummaryHtml(render, data, i).noteHtml}
      <div class="invoice-payment"><div><b>${fmt(render.payment_title)}</b></div><div>${fmt(render.payment_name)}</div>${(render.payment_lines || []).map(line => `<div>${fmt(line)}</div>`).join("")}${render.payment_zelle_line ? `<div>${fmt(render.payment_zelle_line)}</div>` : ""}</div>
      ${insCodingHtml}
      ${notesHtml}
    </article>
    <div class="actions">${i.status === "draft" ? `<button id="returnToDraft">Return to Draft</button>` : ""}${i.status === "finalized" ? `<button id="voidInvoice" class="danger">Void Invoice</button>` : ""}${pdfButtonsHtml}</div></div>`;
  if ($("returnToDraft")) $("returnToDraft").onclick = () => renderInvoiceEditor(data);
  if ($("voidInvoice")) $("voidInvoice").onclick = async () => { const reason = prompt("Reason for voiding this invoice"); if (!reason) return; const result = await api(`/api/invoices/${i.invoice_id}/void`, {method:"POST", body:JSON.stringify({reason})}); await loadInvoices(); renderInvoicePreview(result); };
  if ($("openPdfBtn")) $("openPdfBtn").onclick = () => { window.open(`/api/invoices/${i.invoice_id}/final-pdf`, "_blank"); };
  if ($("showPdfInFinderBtn")) $("showPdfInFinderBtn").onclick = () => api(`/api/invoices/${i.invoice_id}/document-action`, {method:"POST", body:JSON.stringify({action:"show_in_finder"})});
  if ($("openClientFolderBtn")) $("openClientFolderBtn").onclick = () => api(`/api/invoices/${i.invoice_id}/document-action`, {method:"POST", body:JSON.stringify({action:"open_client_folder"})});
  if ($("printPdfBtn")) $("printPdfBtn").onclick = () => { window.open(`/api/invoices/${i.invoice_id}/final-pdf`, "_blank"); };
}

$("newInvoiceBtn").onclick = startInvoiceBuilder;
$("invoiceStatusFilter").onchange = () => { state.invoiceLibrary.offset = 0; loadInvoices(); };
$("invoicePaymentStatusFilter").onchange = () => { state.invoiceLibrary.offset = 0; loadInvoices(); };
$("invoiceBillToFilter").onchange = () => { state.invoiceLibrary.offset = 0; loadInvoices(); };
$("invoiceDateFilter").onchange = () => {
  state.invoiceLibrary.offset = 0;
  const custom = $("invoiceDateFilter").value === "custom";
  $("invoiceCustomDateRange").hidden = !custom;
  loadInvoices();
};
$("invoiceSearch").addEventListener("input", () => {
  clearTimeout(state.invoiceLibrary._searchTimer);
  state.invoiceLibrary._searchTimer = setTimeout(() => { state.invoiceLibrary.offset = 0; loadInvoices(); }, 300);
});
$("invoiceDateFrom").onchange = () => { state.invoiceLibrary.offset = 0; loadInvoices(); };
$("invoiceDateTo").onchange = () => { state.invoiceLibrary.offset = 0; loadInvoices(); };
$("invoicePrevPage").onclick = () => {
  const lib = state.invoiceLibrary;
  if (lib.offset > 0) { lib.offset = Math.max(0, lib.offset - lib.limit); loadInvoices(); }
};
$("invoiceNextPage").onclick = () => {
  const lib = state.invoiceLibrary;
  if (lib.offset + lib.limit < lib.total) { lib.offset += lib.limit; loadInvoices(); }
};
["sessionsDateFilter","sessionsReviewStatusFilter","sessionsPaymentStatusFilter"].forEach(id => $(id).addEventListener("input", () => {
  state.sessions.offset = 0;
  loadSessions();
}));
$("sessionsPrevPage").onclick = () => {
  state.sessions.offset = Math.max(0, state.sessions.offset - state.sessions.limit);
  loadSessions();
};
$("sessionsNextPage").onclick = () => {
  state.sessions.offset += state.sessions.limit;
  loadSessions();
};
document.getElementById("rateRuleForm").onsubmit = async (event) => {
  event.preventDefault();
  const message = $("rateFormMessage");
  try {
    const payload = buildRateRulePayload();
    const endpoint = state.rateCard.mode === "replace" && state.rateCard.replacingRuleId
      ? `/api/rate-rules/${state.rateCard.replacingRuleId}/replace`
      : "/api/rate-rules";
    await api(endpoint, { method: "POST", body: JSON.stringify(payload) });
    resetRateCardForm();
    message.textContent = state.rateCard.mode === "replace" ? "Rate rule replaced." : "Rate rule saved.";
    message.className = "rate-form-message success";
    await loadRateRules();
  } catch (err) {
    message.textContent = err.message || "Failed to save rate rule.";
    message.className = "rate-form-message";
  }
};
$("rateAppliesTo").addEventListener("change", () => {
  clearRateScopeSelections();
  syncRateCardScopeUi();
  renderRateRulePreview();
});
document.getElementById("rateAppliesSearch").addEventListener("input", debounce(async e => {
  const mode = $("rateAppliesTo").value;
  const query = e.target.value.trim();
  if (!query || mode === "everyone") {
    state.rateCard.scopeResults = [];
    renderRateScopeResults();
    return;
  }
  const rows = mode === "account"
    ? await api(`/api/accounts?q=${encodeURIComponent(query)}`)
    : await api(`/api/people?q=${encodeURIComponent(query)}`);
  state.rateCard.scopeResults = rows;
  renderRateScopeResults();
}, 160));

["rateAmountInput","rateDurationChoice","rateCustomDurationMinutes","rateBillingSessionType","rateCustomDescription","rateCustomCode","rateAppointmentStatus","rateTimeCategory","rateEffectiveFrom"].forEach(id => {
  $(id).addEventListener("input", () => {
    syncRateCardCustomFields();
    renderRateRulePreview();
  });
});

async function loadRateRules() {
  const rows = await api("/api/rate-rules");
  renderRateRuleTable("rateRowsStandard", rows.filter(row => row.scope_type === "everyone" && !row.ended));
  renderRateRuleTable("rateRowsExceptions", rows.filter(row => row.scope_type !== "everyone" && !row.ended));
  renderRateRuleTable("rateRowsEnded", rows.filter(row => row.ended));
}

function renderRateRuleTable(targetId, rows) {
  $(targetId).innerHTML = rows.map(row => `
    <tr>
      <td>$${escapeHtml(row.amount)}</td>
      <td>${fmt(row.duration_label)}</td>
      <td>${fmt(row.session_type_label)}</td>
      <td>${fmt(row.appointment_status_label)}</td>
      <td>${timeLabel(row.time_category)}</td>
      <td>${fmt(row.scope_label)}</td>
      <td>${fmt(row.effective_from)}${row.effective_through ? ` to ${fmt(row.effective_through)}` : ""}</td>
      <td>${row.ended ? '<span class="readonly-note">Ended</span>' : `<button class="mini" data-replace-rate="${escapeAttr(row.rate_rule_id)}">Replace</button><button class="mini danger" data-end-rate="${escapeAttr(row.rate_rule_id)}">End</button>`}</td>
    </tr>
  `).join("") || `<tr><td colspan="8" class="readonly-note">No rate rules in this section.</td></tr>`;
  document.querySelectorAll(`#${targetId} [data-replace-rate]`).forEach(button => {
    button.onclick = () => startRateRuleReplacement(rows.find(row => row.rate_rule_id === button.dataset.replaceRate));
  });
  document.querySelectorAll(`#${targetId} [data-end-rate]`).forEach(button => {
    button.onclick = () => promptToEndRateRule(button.dataset.endRate);
  });
}

function buildRateRulePayload() {
  const appliesTo = $("rateAppliesTo").value;
  const amountValue = Number(String($("rateAmountInput").value || "").replace(/[$,]/g, ""));
  const payload = {
    amount: $("rateAmountInput").value,
    duration_choice: $("rateDurationChoice").value,
    custom_duration_minutes: $("rateCustomDurationMinutes").value,
    billing_session_type: $("rateBillingSessionType").value,
    custom_service_description: $("rateCustomDescription").value,
    custom_service_code: $("rateCustomCode").value,
    appointment_status: $("rateAppointmentStatus").value,
    time_category: $("rateTimeCategory").value,
    applies_to: appliesTo,
    effective_from: $("rateEffectiveFrom").value
  };
  if (!payload.amount || Number.isNaN(amountValue) || amountValue <= 0) {
    throw new Error("Amount is required and must be greater than 0.");
  }
  if (!payload.duration_choice) throw new Error("Duration is required.");
  if (payload.duration_choice === "custom" && !$("rateCustomDurationMinutes").value) {
    throw new Error("Custom duration requires actual minutes.");
  }
  if (!payload.billing_session_type) throw new Error("Session type is required.");
  if (payload.billing_session_type === "custom" && !$("rateCustomDescription").value.trim()) {
    throw new Error("Custom session type requires a description.");
  }
  if (!payload.time_category) throw new Error("Time category is required.");
  if (!payload.effective_from || !/^\d{4}-\d{2}-\d{2}$/.test(payload.effective_from)) {
    throw new Error("Effective date is required in YYYY-MM-DD format.");
  }
  if (appliesTo === "person") {
    if (!state.rateCard.resolvedPerson?.person_id) throw new Error("Select one resolved client for a One Client rule.");
    payload.person_id = state.rateCard.resolvedPerson.person_id;
  } else if (appliesTo === "participants") {
    if (state.rateCard.participantSelections.length < 2) throw new Error("Select at least two resolved clients for a Clients Together rule.");
    payload.participant_person_ids = state.rateCard.participantSelections.map(person => person.person_id);
  } else if (appliesTo === "account") {
    if (!state.rateCard.resolvedAccount?.account_id) throw new Error("Select one resolved billing relationship for this rule.");
    payload.client_account_id = state.rateCard.resolvedAccount.account_id;
  }
  return payload;
}

function syncRateCardCustomFields() {
  const customType = $("rateBillingSessionType").value === "custom";
  const customDuration = $("rateDurationChoice").value === "custom";
  $("rateCustomDescription").hidden = !customType;
  $("rateCustomCode").hidden = !customType;
  $("rateCustomDurationMinutes").hidden = !customDuration;
}

function syncRateCardScopeUi() {
  const mode = $("rateAppliesTo").value;
  $("rateScopeResolver").hidden = mode === "everyone";
  $("rateAppliesSearch").placeholder = mode === "account" ? "Search billing relationships..." : "Search clients...";
  renderRateScopeResults();
  renderResolvedRateScope();
}

function clearRateScopeSelections() {
  state.rateCard.resolvedPerson = null;
  state.rateCard.resolvedAccount = null;
  state.rateCard.participantSelections = [];
  state.rateCard.scopeResults = [];
  $("rateAppliesSearch").value = "";
  renderResolvedRateScope();
  renderRateScopeResults();
}

function renderResolvedRateScope() {
  const mode = $("rateAppliesTo").value;
  if (mode === "person") {
    $("rateScopeResolved").textContent = state.rateCard.resolvedPerson ? `Resolved client: ${state.rateCard.resolvedPerson.display_name}` : "No client resolved yet.";
  } else if (mode === "account") {
    $("rateScopeResolved").textContent = state.rateCard.resolvedAccount ? `Resolved billing relationship: ${state.rateCard.resolvedAccount.account_name}` : "No billing relationship resolved yet.";
  } else if (mode === "participants") {
    $("rateScopeResolved").textContent = state.rateCard.participantSelections.length ? "Resolved clients together:" : "Add at least two resolved clients.";
  } else {
    $("rateScopeResolved").textContent = "";
  }
  $("rateParticipantSelections").innerHTML = state.rateCard.participantSelections.map(person => `<span class="chip linked">${escapeHtml(person.display_name)}<button data-remove-rate-participant="${escapeAttr(person.person_id)}">×</button></span>`).join("");
  document.querySelectorAll("[data-remove-rate-participant]").forEach(button => {
    button.onclick = () => {
      state.rateCard.participantSelections = state.rateCard.participantSelections.filter(person => person.person_id !== button.dataset.removeRateParticipant);
      renderResolvedRateScope();
      renderRateRulePreview();
    };
  });
}

function renderRateScopeResults() {
  const mode = $("rateAppliesTo").value;
  const rows = state.rateCard.scopeResults || [];
  $("rateScopeResults").innerHTML = rows.map(row => {
    const label = mode === "account" ? row.account_name : row.display_name;
    const code = row.account_code || row.person_code || "";
    return `<button type="button" class="mini" data-rate-scope-pick="${escapeAttr(mode)}:${escapeAttr(mode === "account" ? row.account_id : row.person_id)}">${fmt(label)}${code ? ` (${escapeHtml(code)})` : ""}</button>`;
  }).join("");
  document.querySelectorAll("[data-rate-scope-pick]").forEach(button => {
    button.onclick = () => {
      const [pickMode, id] = button.dataset.rateScopePick.split(":");
      const picked = rows.find(row => (pickMode === "account" ? row.account_id : row.person_id) === id);
      if (!picked) return;
      if (pickMode === "account") {
        state.rateCard.resolvedAccount = picked;
      } else if (pickMode === "person") {
        state.rateCard.resolvedPerson = picked;
      } else if (!state.rateCard.participantSelections.some(person => person.person_id === picked.person_id)) {
        state.rateCard.participantSelections = [...state.rateCard.participantSelections, picked];
      }
      $("rateAppliesSearch").value = "";
      state.rateCard.scopeResults = [];
      renderRateScopeResults();
      renderResolvedRateScope();
      renderRateRulePreview();
    };
  });
}

function renderRateRulePreview() {
  syncRateCardCustomFields();
  const scope = $("rateAppliesTo").value;
  const duration = $("rateDurationChoice").value === "custom"
    ? `${$("rateCustomDurationMinutes").value || "?"} minutes`
    : `${$("rateDurationChoice").value || "?"} minutes`;
  const sessionType = billingTypeLabel($("rateBillingSessionType").value, $("rateCustomDescription").value.trim());
  const appointmentStatus = appointmentStatusRuleLabel($("rateAppointmentStatus").value);
  const timeCategory = timeLabel($("rateTimeCategory").value);
  const amount = $("rateAmountInput").value.trim() || "?";
  const effective = $("rateEffectiveFrom").value || "today";
  let scopeText = "for everyone";
  if (scope === "person") scopeText = state.rateCard.resolvedPerson ? `for client ${state.rateCard.resolvedPerson.display_name}` : "for one resolved client";
  if (scope === "participants") scopeText = state.rateCard.participantSelections.length ? `for clients ${state.rateCard.participantSelections.map(person => person.display_name).join(" + ")}` : "for resolved clients together";
  if (scope === "account") scopeText = state.rateCard.resolvedAccount ? `for billing relationship ${state.rateCard.resolvedAccount.account_name}` : "for one resolved billing relationship";
  const action = state.rateCard.mode === "replace" ? "replace the selected rule with" : "save";
  $("rateRulePreview").textContent = `This will ${action} a ${appointmentStatus.toLowerCase()} ${timeCategory.toLowerCase()} ${sessionType.toLowerCase()} rate of $${amount} for ${duration} ${scopeText}, effective ${effective}.`;
}

function resetRateCardForm() {
  state.rateCard.mode = "create";
  state.rateCard.replacingRuleId = null;
  $("rateRuleForm").reset();
  $("rateDurationChoice").value = "60";
  $("rateBillingSessionType").value = "psychotherapy";
  $("rateAppointmentStatus").value = "scheduled";
  $("rateTimeCategory").value = "standard";
  $("rateAppliesTo").value = "everyone";
  $("rateEffectiveFrom").value = new Date().toISOString().slice(0, 10);
  clearRateScopeSelections();
  syncRateCardCustomFields();
  syncRateCardScopeUi();
  renderRateRulePreview();
}

function startRateRuleReplacement(row) {
  if (!row) return;
  state.rateCard.mode = "replace";
  state.rateCard.replacingRuleId = row.rate_rule_id;
  $("rateAmountInput").value = row.amount;
  if ([30, 60, 90, 120].includes(Number(row.duration_minutes))) {
    $("rateDurationChoice").value = String(row.duration_minutes);
    $("rateCustomDurationMinutes").value = "";
  } else {
    $("rateDurationChoice").value = "custom";
    $("rateCustomDurationMinutes").value = row.duration_minutes || "";
  }
  $("rateBillingSessionType").value = row.billing_session_type;
  $("rateCustomDescription").value = row.custom_service_description || "";
  $("rateCustomCode").value = row.custom_service_code || "";
  $("rateAppointmentStatus").value = row.appointment_status || "scheduled";
  $("rateTimeCategory").value = row.time_category;
  $("rateEffectiveFrom").value = new Date().toISOString().slice(0, 10);
  $("rateAppliesTo").value = row.scope_type === "everyone" ? "everyone" : row.scope_type;
  state.rateCard.resolvedPerson = row.scope_type === "person" ? { person_id: row.person_id, display_name: row.display_name } : null;
  state.rateCard.resolvedAccount = row.scope_type === "account" ? { account_id: row.client_account_id, account_name: row.account_name } : null;
  state.rateCard.participantSelections = row.scope_type === "participants"
    ? (row.participant_person_ids || []).map((personId, index) => ({ person_id: personId, display_name: (row.participant_names || "").split(" + ")[index] || personId }))
    : [];
  $("rateFormMessage").textContent = `Replacing ${row.scope_label}. Saving will end the old rule on the day before the new effective date.`;
  $("rateFormMessage").className = "rate-form-message";
  syncRateCardCustomFields();
  syncRateCardScopeUi();
  renderResolvedRateScope();
  renderRateRulePreview();
  $("rateAmountInput").focus();
}

async function promptToEndRateRule(ruleId) {
  const effectiveThrough = prompt("End this rule on which date? Use YYYY-MM-DD.", new Date().toISOString().slice(0, 10));
  if (!effectiveThrough) return;
  await api(`/api/rate-rules/${ruleId}/end`, { method: "POST", body: JSON.stringify({ effective_through: effectiveThrough }) });
  $("rateFormMessage").textContent = "Rate rule ended.";
  $("rateFormMessage").className = "rate-form-message success";
  await loadRateRules();
}

const billingDirState = {
  records: [],
  filter: "all",
  statusFilter: "active",
  duplicateAnalysis: null
};

const BILLING_DIR_TYPE_LABELS = {
  self_pay: "Self-pay",
  third_party: "Pays for others",
  organization: "Organization",
  account: "Shared billing group",
};

function billingDirCoversText(rec) {
  const people = rec.covered_people || [];
  if (!people.length) return "—";
  return people.map(p => escapeHtml(p.display_name || "Unknown")).join(", ");
}

function billingDirPayerName(rec) {
  if (rec.record_type === "account") {
    return escapeHtml(rec.account_name || "Unnamed group");
  }
  return escapeHtml(rec.payer_display_name || rec.organization_name || rec.billing_name || "Unknown");
}

function billingDirPayerSubtext(rec) {
  if (rec.record_type === "self_pay") {
    const people = rec.covered_people || [];
    if (people.length > 1) {
      return `Pays for ${people.map(p => escapeHtml(p.display_name || "Unknown")).join(" and ")}`;
    }
    return "Pays for herself";
  }
  if (rec.record_type === "third_party") {
    const people = rec.covered_people || [];
    if (people.length === 1) {
      return `Pays for ${escapeHtml(people[0].display_name || "Unknown")}`;
    }
    if (people.length > 1) {
      return `Pays for ${people.map(p => escapeHtml(p.display_name || "Unknown")).join(" and ")}`;
    }
    return "Pays for others";
  }
  if (rec.record_type === "organization") {
    const people = rec.covered_people || [];
    if (people.length === 1) {
      return `Pays for ${escapeHtml(people[0].display_name || "Unknown")}`;
    }
    if (people.length > 1) {
      return `Pays for ${escapeHtml(people[0].display_name || "Unknown")} and ${people.length - 1} other${people.length - 1 === 1 ? "" : "s"}`;
    }
    return "";
  }
  if (rec.record_type === "account") {
    if (rec.billing_name) {
      return `Invoice recipient: ${escapeHtml(rec.billing_name)}`;
    }
    return "";
  }
  return "";
}

function billingDirLinkedText(rec) {
  if (rec.record_type !== "account" && rec.account_id) {
    return `<div class="dir-muted">Linked to shared billing group: ${escapeHtml(rec.account_name || "")}</div>`;
  }
  if (rec.record_type === "account" && rec.billing_name) {
    return `<div class="dir-muted">Invoice recipient: ${escapeHtml(rec.billing_name)}</div>`;
  }
  return "";
}

function renderBillingDirDuplicateBanner() {
  const banner = $("billingDirDuplicateBanner");
  if (!banner) return;
  const summary = billingDirState.duplicateAnalysis?.summary;
  if (!summary || (!summary.exact_active_duplicate_group_count && !summary.payer_record_conflict_count)) {
    banner.hidden = true;
    banner.textContent = "";
    return;
  }
  const parts = [];
  if (summary.exact_active_duplicate_group_count) {
    parts.push(`${summary.exact_active_duplicate_group_count} duplicate active relationship group${summary.exact_active_duplicate_group_count === 1 ? "" : "s"}`);
  }
  if (summary.payer_record_conflict_count) {
    parts.push(`${summary.payer_record_conflict_count} payer delivery conflict${summary.payer_record_conflict_count === 1 ? "" : "s"}`);
  }
  banner.hidden = false;
  banner.textContent = `${parts.join(" and ")} detected. Existing duplicates remain visible here and should be resolved later through an explicit audited deactivation or merge workflow.`;
}

function billingDirDeliveryText(rec) {
  if (rec.record_type === "account") return "—";
  const method = rec.preferred_delivery_method;
  if (!method || method === "unresolved") return "—";
  return escapeHtml(method);
}

function billingDirOpenButton(rec) {
  if (rec.record_type === "account") {
    return `<button class="mini" data-open-account="${escapeHtml(rec.account_id)}">Open</button>`;
  }
  if (rec.record_type === "organization") {
    return `<button class="mini" data-open-organization="${escapeHtml(rec.billing_party_id)}">Open</button>`;
  }
  if (rec.payer_person_id) {
    const normalizeBtn = rec.has_payer_record_conflict
      ? `<button class="mini" data-normalize-payer="${escapeHtml(rec.payer_person_id)}">Normalize</button>`
      : "";
    return `<button class="mini" data-open-person="${escapeHtml(rec.payer_person_id)}">Open</button> ${normalizeBtn}`;
  }
  return `<button class="mini" disabled title="No detail view available">Details unavailable</button>`;
}

function renderBillingDirRows() {
  const filter = billingDirState.filter;
  const statusFilter = billingDirState.statusFilter;
  const search = ($("clientSearch").value || "").toLowerCase();
  let rows = billingDirState.records;
  if (statusFilter === "active") {
    rows = rows.filter(r => r.active);
  } else if (statusFilter === "inactive") {
    rows = rows.filter(r => !r.active);
  }
  if (filter !== "all") {
    rows = rows.filter(r => r.record_type === filter);
  }
  if (search) {
    rows = rows.filter(r => {
      const name = (r.payer_display_name || r.organization_name || r.billing_name || r.account_name || "").toLowerCase();
      const covers = (r.covered_people || []).map(p => (p.display_name || "").toLowerCase()).join(" ");
      return name.includes(search) || covers.includes(search);
    });
  }
  if (!rows.length) {
    $("clientRows").innerHTML = `<tr><td colspan="8" class="readonly-note">No billing relationships yet</td></tr>`;
    return;
  }
  $("clientRows").innerHTML = rows.map(rec => {
    const typeLabel = BILLING_DIR_TYPE_LABELS[rec.record_type] || rec.record_type;
    const payerName = billingDirPayerName(rec);
    const subtext = billingDirPayerSubtext(rec);
    const linked = billingDirLinkedText(rec);
    const duplicateWarning = rec.has_exact_active_duplicate
      ? `<div class="billing-dir-warning">Duplicate active relationship detected.</div>`
      : rec.has_payer_record_conflict
        ? `<div class="billing-dir-warning">Multiple active Bill To records exist for this payer.</div>`
        : "";
    const covers = billingDirCoversText(rec);
    const delivery = billingDirDeliveryText(rec);
    const status = rec.active ? "Active" : "Inactive";
    const statusClass = rec.active ? "status-pill active" : "status-pill inactive";
    const openBtn = billingDirOpenButton(rec);
    return `<tr data-record-id="${escapeHtml(rec.record_id)}">
      <td><span class="dir-type-label">${escapeHtml(typeLabel)}</span></td>
      <td><span class="primary">${payerName}</span><div class="dir-subtext">${subtext}</div>${linked}${duplicateWarning}</td>
      <td>${covers}</td>
      <td>${fmt(rec.session_count || 0)}</td>
      <td>${fmt(rec.latest_session_date)}</td>
      <td>${delivery}</td>
      <td><span class="${statusClass}">${status}</span></td>
      <td>${openBtn}</td>
    </tr>`;
  }).join("");
  document.querySelectorAll("#clientRows tr").forEach(tr => {
    const openAccountBtn = tr.querySelector("[data-open-account]");
    if (openAccountBtn) {
      openAccountBtn.onclick = (e) => {
        e.stopPropagation();
        openAccountRecord(openAccountBtn.dataset.openAccount, { returnContext: readReturnContext() });
      };
    }
    const openPersonBtn = tr.querySelector("[data-open-person]");
    if (openPersonBtn) {
      openPersonBtn.onclick = (e) => {
        e.stopPropagation();
        location.hash = `people/${openPersonBtn.dataset.openPerson}`;
      };
    }
    const openOrgBtn = tr.querySelector("[data-open-organization]");
    if (openOrgBtn) {
      openOrgBtn.onclick = (e) => {
        e.stopPropagation();
        openOrganizationRecord(openOrgBtn.dataset.openOrganization);
      };
    }
    const normalizeBtn = tr.querySelector("[data-normalize-payer]");
    if (normalizeBtn) {
      normalizeBtn.onclick = async (e) => {
        e.stopPropagation();
        if (!confirm("Normalize duplicate billing parties for this payer? This will select one canonical record, copy missing fields, deactivate redundant records, and repoint safe references. Finalized invoices and payment history will not be changed.")) return;
        try {
          const result = await api("/api/billing-relationships/normalize-payer", {
            method: "POST",
            body: JSON.stringify({ person_id: normalizeBtn.dataset.normalizePayer }),
          });
          alert(`Normalized: ${result.deactivated_count} redundant record(s) deactivated. ${result.fields_copied.length} field(s) copied. ${result.conflicts.length} conflict(s) found.`);
          await loadClients();
        } catch (err) {
          alert(err.message || "Failed to normalize payer records.");
        }
      };
    }
  });
}

async function loadClients() {
  const [records, duplicateAnalysis] = await Promise.all([
    api("/api/billing-relationships"),
    api("/api/billing-relationships/duplicate-analysis"),
  ]);
  billingDirState.records = records;
  billingDirState.duplicateAnalysis = duplicateAnalysis;
  renderBillingDirDuplicateBanner();
  renderBillingDirRows();
}

function closeOrganizationRecord() {
  const panel = $("organizationRecord");
  if (!panel) return;
  panel.hidden = true;
  panel.innerHTML = `<div class="empty-state">Open an organization billing record.</div>`;
}

function orgDeliveryLabel(method) {
  return ({ email: "Email", mail: "Mail", both: "Both", unresolved: "Unresolved" }[method] || method || "—");
}

function orgAddress(bp) {
  const parts = [
    bp.billing_address_line_1,
    bp.billing_address_line_2,
    bp.billing_city,
    bp.billing_state,
    bp.billing_postal_code
  ].filter(Boolean);
  return parts.length ? parts.join(", ") : "—";
}

function orgInvoiceStatusLabel(status) {
  return ({ draft: "Draft", finalized: "Finalized", void: "Void" }[status] || status || "—");
}

function showOrgMessage(msg, type) {
  const el = $("orgMessage");
  if (!el) return;
  el.textContent = msg;
  el.className = `billing-setup-message ${type || ""}`;
}

let orgSaving = false;

function showOrgEditForm(bp) {
  const container = $("orgEditFormContainer");
  if (!container) return;
  container.innerHTML = `
    <div class="billing-setup-form">
      <h4>Edit Organization</h4>
      <div class="field-grid">
        <label class="field wide">Organization Name <input id="orgFormName" value="${escapeHtml(bp.organization_name || "")}"></label>
        <label class="field wide">Billing Name <input id="orgFormBillingName" value="${escapeHtml(bp.billing_name || "")}"></label>
        <label class="field">Billing Email <input id="orgFormEmail" value="${escapeHtml(bp.billing_email || "")}"></label>
        <label class="field">Billing Phone <input id="orgFormPhone" value="${escapeHtml(bp.billing_phone || "")}"></label>
        <label class="field">Address Line 1 <input id="orgFormAddr1" value="${escapeHtml(bp.billing_address_line_1 || "")}"></label>
        <label class="field">Address Line 2 <input id="orgFormAddr2" value="${escapeHtml(bp.billing_address_line_2 || "")}"></label>
        <label class="field">City <input id="orgFormCity" value="${escapeHtml(bp.billing_city || "")}"></label>
        <label class="field">State <input id="orgFormState" value="${escapeHtml(bp.billing_state || "")}"></label>
        <label class="field">Postal Code <input id="orgFormPostal" value="${escapeHtml(bp.billing_postal_code || "")}"></label>
        <label class="field">Preferred Delivery
          <select id="orgFormDelivery">
            <option value="unresolved"${(bp.preferred_delivery_method || "unresolved") === "unresolved" ? " selected" : ""}>Unresolved</option>
            <option value="email"${bp.preferred_delivery_method === "email" ? " selected" : ""}>Email</option>
            <option value="mail"${bp.preferred_delivery_method === "mail" ? " selected" : ""}>Mail</option>
            <option value="both"${bp.preferred_delivery_method === "both" ? " selected" : ""}>Email and mail</option>
          </select>
        </label>
        <label class="field wide">Administrative Notes <input id="orgFormNotes" value="${escapeHtml(bp.administrative_notes || "")}"></label>
      </div>
      <div class="record-actions">
        <button id="orgFormSaveBtn" class="save">Save Changes</button>
        <button id="orgFormCancelBtn">Cancel</button>
      </div>
    </div>
  `;
  $("orgFormCancelBtn").onclick = () => { container.innerHTML = ""; showOrgMessage("", ""); };
  $("orgFormSaveBtn").onclick = async () => {
    if (orgSaving) return;
    const orgName = $("orgFormName").value.trim();
    if (!orgName) { showOrgMessage("Organization name is required.", "error"); return; }
    const billingName = $("orgFormBillingName").value.trim();
    if (!billingName) { showOrgMessage("Billing name is required.", "error"); return; }
    orgSaving = true;
    $("orgFormSaveBtn").disabled = true;
    $("orgFormCancelBtn").disabled = true;
    const payload = {
      billing_party_type: "organization",
      organization_name: orgName,
      billing_name: billingName,
      billing_email: $("orgFormEmail").value,
      billing_phone: $("orgFormPhone").value,
      billing_address_line_1: $("orgFormAddr1").value,
      billing_address_line_2: $("orgFormAddr2").value,
      billing_city: $("orgFormCity").value,
      billing_state: $("orgFormState").value,
      billing_postal_code: $("orgFormPostal").value,
      preferred_delivery_method: $("orgFormDelivery").value,
      administrative_notes: $("orgFormNotes").value
    };
    try {
      await api(`/api/billing-parties/${bp.billing_party_id}`, {
        method: "POST",
        body: JSON.stringify(payload)
      });
      await openOrganizationRecord(bp.billing_party_id);
      await loadClients();
    } catch (err) {
      showOrgMessage(err.message || "Failed to save organization.", "error");
      $("orgFormSaveBtn").disabled = false;
      $("orgFormCancelBtn").disabled = false;
    } finally {
      orgSaving = false;
    }
  };
}

async function openOrganizationRecord(billingPartyId) {
  const panel = $("organizationRecord");
  if (!panel) return;
  panel.hidden = false;
  panel.innerHTML = `<div class="org-loading">Loading organization record…</div>`;
  $("accountRecord").innerHTML = `<div class="empty-state">Open a billing relationship record.</div>`;

  let data;
  try {
    data = await api(`/api/billing-parties/${billingPartyId}`);
  } catch (err) {
    panel.innerHTML = `<div class="org-error">${escapeHtml(err.message || "Failed to load organization record.")}</div>
      <div style="margin-top:8px"><button class="mini" id="orgCloseBtn">Close</button></div>`;
    if ($("orgCloseBtn")) $("orgCloseBtn").onclick = () => closeOrganizationRecord();
    return;
  }

  const bp = data.billing_party;
  const displayName = bp.organization_name || bp.billing_name || "Unknown Organization";
  const billingNameSecondary = (bp.billing_name && bp.billing_name !== bp.organization_name) ? bp.billing_name : "";
  const statusText = bp.active ? "Active" : "Inactive";
  const statusClass = bp.active ? "status-pill active" : "status-pill inactive";
  const summary = data.billing_summary;

  const coveredClientsHtml = (data.covered_clients || []).length
    ? `<div class="org-table-scroll"><table class="org-table"><thead><tr><th>Client</th><th>Code</th><th>Sessions</th><th>Latest Session</th><th>Open</th></tr></thead><tbody>
        ${(data.covered_clients || []).map(c => `<tr>
          <td>${escapeHtml(c.display_name)}</td>
          <td>${escapeHtml(c.person_code)}</td>
          <td>${c.session_count || 0}</td>
          <td>${fmt(c.latest_session_date)}</td>
          <td><button class="mini" data-open-person="${escapeHtml(c.person_id)}">Open</button></td>
        </tr>`).join("")}
      </tbody></table></div>`
    : `<span class="readonly-note">No clients have sessions billed to this organization yet.</span>`;

  const sessionsHtml = (data.sessions || []).length
    ? `<div class="org-table-scroll"><table class="org-table"><thead><tr><th>Date</th><th>Participants</th><th>Session Type</th><th>Duration</th><th>Time Category</th><th>Stored Rate</th><th>Review Status</th><th>Invoice</th><th>Open in Review</th></tr></thead><tbody>
        ${(data.sessions || []).map(s => {
          const invLabel = s.invoice_id ? (s.invoice_number || "Draft invoice") : "—";
          return `<tr>
            <td>${fmt(s.session_date)}</td>
            <td>${escapeHtml(s.participant_names || "—")}</td>
            <td>${escapeHtml(s.billing_session_type || "—")}</td>
            <td>${s.approved_duration_minutes || s.duration_minutes || "—"} min</td>
            <td>${escapeHtml(timeLabel(s.time_category))}</td>
            <td>${money(centString(s.approved_rate_cents))}</td>
            <td>${escapeHtml(s.review_status || "—")}</td>
            <td>${escapeHtml(invLabel)}</td>
            <td>${s.candidate_id ? `<button class="mini" data-open-review="${escapeHtml(s.candidate_id)}">Open</button>` : "—"}</td>
          </tr>`;
        }).join("")}
      </tbody></table></div>`
    : `<span class="readonly-note">No sessions billed to this organization yet.</span>`;

  const invoicesHtml = (data.invoices || []).length
    ? `<div class="org-table-scroll"><table class="org-table"><thead><tr><th>Invoice Number</th><th>Billing Period</th><th>Issue Date</th><th>Status</th><th>Total</th><th>Balance</th><th>Open</th></tr></thead><tbody>
        ${(data.invoices || []).map(inv => `<tr>
          <td>${escapeHtml(inv.invoice_number || "—")}</td>
          <td>${fmt(inv.billing_period_start)} – ${fmt(inv.billing_period_end)}</td>
          <td>${fmt(inv.invoice_date)}</td>
          <td><span class="status-pill ${escapeAttr(inv.status)}">${escapeHtml(orgInvoiceStatusLabel(inv.status))}</span></td>
          <td>${money(centString(inv.total_cents))}</td>
          <td>${money(centString(inv.balance_cents))}</td>
          <td>${inv.invoice_id ? `<button class="mini" data-open-invoice="${escapeHtml(inv.invoice_id)}">Open</button>` : "—"}</td>
        </tr>`).join("")}
      </tbody></table></div>`
    : `<span class="readonly-note">No invoices addressed to this organization yet.</span>`;

  const linkedAccountsHtml = (data.linked_accounts || []).length
    ? `<div class="org-table-scroll"><table class="org-table"><thead><tr><th>Account Name</th><th>Status</th><th>Members</th><th>Open</th></tr></thead><tbody>
        ${(data.linked_accounts || []).map(a => `<tr>
          <td>${escapeHtml(a.account_name)}</td>
          <td>${a.active ? "Active" : "Inactive"}</td>
          <td>${escapeHtml((a.members || []).map(m => m.display_name).join(", ") || "None")}</td>
          <td><button class="mini" data-open-account="${escapeHtml(a.account_id)}">Open</button></td>
        </tr>`).join("")}
      </tbody></table></div>`
    : `<span class="readonly-note">No linked shared billing groups.</span>`;

  const auditHtml = (data.audit || []).length
    ? `<div class="org-audit">${(data.audit || []).map(a => `<div><span>${fmt(a.created_at)}</span> <strong>${escapeHtml(a.action)}</strong> <span>${escapeHtml(a.details || "")}</span></div>`).join("")}</div>`
    : `<span class="readonly-note">No administrative history.</span>`;

  panel.innerHTML = `
    <button class="mini org-close-btn" id="orgCloseBtn">Close</button>
    <h3>${escapeHtml(displayName)}</h3>
    ${billingNameSecondary ? `<div class="org-header-meta"><span>Billing name: ${escapeHtml(billingNameSecondary)}</span></div>` : ""}
    <div class="org-header-meta"><span class="${statusClass}">${statusText}</span></div>
    <div class="record-actions" id="orgActions">
      <button class="save" id="orgEditBtn">Edit</button>
      ${bp.active
        ? `<button id="orgDeactivateBtn">Deactivate</button>`
        : `<button class="save" id="orgReactivateBtn">Reactivate</button>`}
    </div>
    <div id="orgEditFormContainer"></div>
    <div id="orgMessage" class="billing-setup-message"></div>

    <div class="org-section">
      <h4>Billing Details</h4>
      <div class="org-billing-details">
        <label>Organization name</label><span>${escapeHtml(bp.organization_name || "—")}</span>
        <label>Billing name</label><span>${escapeHtml(bp.billing_name || "—")}</span>
        <label>Email</label><span>${escapeHtml(bp.billing_email || "—")}</span>
        <label>Phone</label><span>${escapeHtml(bp.billing_phone || "—")}</span>
        <label>Address</label><span>${escapeHtml(orgAddress(bp))}</span>
        <label>Delivery method</label><span>${escapeHtml(orgDeliveryLabel(bp.preferred_delivery_method))}</span>
        <label>Admin notes</label><span>${escapeHtml(bp.administrative_notes || "—")}</span>
      </div>
    </div>

    <div class="org-section">
      <h4>Billing Summary</h4>
      <div class="org-summary-cards">
        <div class="summary-card"><div class="summary-card-label">Sessions</div><div class="summary-card-value">${summary.total_sessions || 0}</div></div>
        <div class="summary-card"><div class="summary-card-label">Approved Uninvoiced</div><div class="summary-card-value">${summary.approved_uninvoiced_sessions || 0}</div></div>
        <div class="summary-card"><div class="summary-card-label">Invoices</div><div class="summary-card-value">${summary.invoice_count || 0}</div></div>
        <div class="summary-card"><div class="summary-card-label">Total Invoiced</div><div class="summary-card-value">${money(centString(summary.total_invoiced_cents))}</div></div>
        <div class="summary-card"><div class="summary-card-label">Finalized Invoice Total</div><div class="summary-card-value">${money(centString(summary.finalized_invoice_total_cents))}</div></div>
      </div>
      <div class="org-payment-note">Finalized invoice totals reflect non-void finalized invoices only. Payment tracking is not yet implemented.</div>
    </div>

    <div class="org-section">
      <h4>Covered Clients</h4>
      ${coveredClientsHtml}
    </div>

    <div class="org-section">
      <h4>Sessions</h4>
      ${sessionsHtml}
    </div>

    <div class="org-section">
      <h4>Invoice History</h4>
      ${invoicesHtml}
    </div>

    <div class="org-section">
      <h4 class="secondary-heading">Related Shared Billing Groups</h4>
      ${linkedAccountsHtml}
    </div>

    <div class="org-section">
      <h4 class="secondary-heading">Administrative History</h4>
      ${auditHtml}
    </div>
  `;

  if ($("orgCloseBtn")) $("orgCloseBtn").onclick = () => closeOrganizationRecord();

  if ($("orgEditBtn")) $("orgEditBtn").onclick = () => showOrgEditForm(data.billing_party);
  if ($("orgDeactivateBtn")) $("orgDeactivateBtn").onclick = () => {
    showOrgMessage("", "");
    const existing = document.getElementById("orgDeactivateConfirm");
    if (existing) existing.remove();
    const box = document.createElement("div");
    box.id = "orgDeactivateConfirm";
    box.className = "lifecycle-confirm-box";
    box.style.display = "block";
    box.innerHTML = `
      <p>Deactivating this organization prevents it from being suggested for future billing. Historical sessions and invoices will remain unchanged.</p>
      <div class="wizard-confirm-actions">
        <button type="button" id="orgDeactNo" class="modal-cancel">Cancel</button>
        <button type="button" id="orgDeactYes" class="modal-submit">Deactivate</button>
      </div>`;
    panel.prepend(box);
    document.getElementById("orgDeactNo").onclick = () => { box.remove(); $("orgDeactivateBtn").focus(); };
    document.getElementById("orgDeactYes").onclick = async () => {
      box.remove();
      try {
        await api(`/api/billing-parties/${bp.billing_party_id}`, {
          method: "POST",
          body: JSON.stringify({ active: false })
        });
        await openOrganizationRecord(bp.billing_party_id);
        await loadClients();
      } catch (err) {
        showOrgMessage(err.message || "Failed to deactivate organization.", "error");
      }
    };
  };
  if ($("orgReactivateBtn")) $("orgReactivateBtn").onclick = async () => {
    try {
      await api(`/api/billing-parties/${bp.billing_party_id}`, {
        method: "POST",
        body: JSON.stringify({ active: true })
      });
      await openOrganizationRecord(bp.billing_party_id);
      await loadClients();
    } catch (err) {
      showOrgMessage(err.message || "Failed to reactivate organization.", "error");
    }
  };

  panel.querySelectorAll("[data-open-person]").forEach(btn => {
    btn.onclick = (e) => { e.stopPropagation(); location.hash = `people/${btn.dataset.openPerson}`; };
  });
  panel.querySelectorAll("[data-open-account]").forEach(btn => {
    btn.onclick = (e) => { e.stopPropagation(); openAccountRecord(btn.dataset.openAccount, { returnContext: readReturnContext() }); };
  });
  panel.querySelectorAll("[data-open-invoice]").forEach(btn => {
    btn.onclick = (e) => { e.stopPropagation(); openInvoice(btn.dataset.openInvoice); };
  });
  panel.querySelectorAll("[data-open-review]").forEach(btn => {
    btn.onclick = async (e) => {
      e.stopPropagation();
      const candidateId = btn.dataset.openReview;
      if (!candidateId) return;
      await showReviewWorkbench();
      await selectCandidate(candidateId);
    };
  });
}

function showLifecycleConfirm(accountId, action, accountName) {
  const box = $("lifecycleConfirmBox");
  if (!box) return;
  const isDeactivate = action === "deactivate";
  const heading = isDeactivate ? "Deactivate this billing relationship?" : "Reactivate this billing relationship?";
  const explanation = isDeactivate
    ? "It will no longer appear in active searches or be suggested for future sessions. Existing sessions, invoices, rates, payments, and history will remain unchanged."
    : "It will appear in active searches and be suggested for future sessions again.";
  const confirmLabel = isDeactivate ? "Deactivate" : "Reactivate";
  const confirmBtnId = isDeactivate ? "confirmDeactivateBtn" : "confirmReactivateBtn";
  const spinnerText = isDeactivate ? "Deactivating…" : "Reactivating…";
  box.hidden = false;
  box.innerHTML = `
    <div class="lifecycle-confirm-content">
      <h4>${escapeHtml(heading)}</h4>
      <p class="lifecycle-explanation">${escapeHtml(explanation)}</p>
      <div class="lifecycle-confirm-actions">
        <button type="button" id="lifecycleCancelBtn" class="modal-back">Cancel</button>
        <button type="button" id="${confirmBtnId}" class="${isDeactivate ? "danger" : "save"}">${escapeHtml(confirmLabel)}</button>
      </div>
      <div class="lifecycle-error" id="lifecycleError"></div>
    </div>
  `;
  const cancelBtn = $("lifecycleCancelBtn");
  const confirmBtn = $(confirmBtnId);
  const errorDisplay = $("lifecycleError");
  const triggerBtn = isDeactivate ? $("deactivateAccountBtn") : $("reactivateAccountBtn");
  let inFlight = false;

  function closeConfirm() {
    box.hidden = true;
    box.innerHTML = "";
    if (triggerBtn) triggerBtn.focus();
  }

  cancelBtn.onclick = closeConfirm;
  document.addEventListener("keydown", function escHandler(e) {
    if (e.key === "Escape" && !box.hidden) {
      closeConfirm();
      document.removeEventListener("keydown", escHandler);
    }
  });

  confirmBtn.onclick = async () => {
    if (inFlight) return;
    inFlight = true;
    confirmBtn.disabled = true;
    cancelBtn.disabled = true;
    confirmBtn.textContent = spinnerText;
    errorDisplay.textContent = "";
    try {
      await api(`/api/accounts/${accountId}/${action}`, { method: "POST", body: "{}" });
      await openAccountRecord(accountId);
      await loadClients();
    } catch (err) {
      confirmBtn.disabled = false;
      cancelBtn.disabled = false;
      confirmBtn.textContent = confirmLabel;
      inFlight = false;
      errorDisplay.textContent = escapeHtml(err.message || "Request failed.");
    }
  };
}

async function openAccountRecord(accountId, options = {}) {
  if (!accountId) return alert("Select or create a billing relationship first.");
  closeOrganizationRecord();
  const returnContext = validReturnContext(options.returnContext) ? persistReturnContext(options.returnContext) : readReturnContext();
  if (returnContext) {
    persistReturnContext({ ...returnContext, accountId });
    if (!location.hash.startsWith("#clients?")) location.hash = returnContextHash({ ...returnContext, accountId });
  }
  state.returnCandidate = state.selected;
  const data = await api(`/api/accounts/${accountId}`);
  const bp = data.billing_party || {};
  const isActive = data.account.active;
  const statusPill = isActive ? '<span class="status-pill active">Active</span>' : '<span class="status-pill inactive">Inactive</span>';
  const lifecycleBtn = isActive
    ? '<button id="deactivateAccountBtn" class="danger">Deactivate Billing Relationship</button>'
    : '<button id="reactivateAccountBtn" class="save">Reactivate Billing Relationship</button>';

  const payerType = bp.billing_party_type === "organization" ? "organization" : (bp.person_id ? "person" : "person");
  const payerName = bp.billing_name || bp.organization_name || "Not set";
  const payerTypeLabel = payerType === "organization" ? "Organization" : (bp.person_id && (data.members || []).some(m => m.person_id === bp.person_id) ? "Client" : "Another person");
  const addressSummary = billingAddressSummary(bp);
  const deliveryLabel = { email: "Email", mail: "Mail", both: "Both", unresolved: "Unresolved" }[bp.preferred_delivery_method] || "Unresolved";

  const coveredHtml = (data.members || []).length
    ? `<div class="covered-clients-list">${data.members.map(m => `
      <div class="covered-client-row" data-person-id="${escapeHtml(m.person_id)}">
        <span class="covered-client-name">${escapeHtml(m.display_name)}</span>
        ${m.person_code ? `<span class="help">${escapeHtml(m.person_code)}</span>` : ""}
        <button type="button" class="covered-client-remove" data-person-id="${escapeHtml(m.person_id)}" aria-label="Remove ${escapeHtml(m.display_name)} from Pays for">&times;</button>
      </div>`).join("")}</div>`
    : '<div class="readonly-note">No covered clients.</div>';

  $("accountRecord").innerHTML = `
    ${returnContext ? `<a href="#" class="return-link" id="returnFromAccount">← Return to ${escapeHtml(state.detail?.session?.raw_calendar_title || "")} — ${escapeHtml(state.detail?.session?.session_date || "")}</a>` : ""}
    <h3>${escapeHtml(data.account.account_name)}</h3>
    <div class="meta">${statusPill}</div>
    <div id="lifecycleConfirmBox" class="lifecycle-confirm-box" hidden></div>
    <div class="record-actions">${lifecycleBtn}</div>

    <div class="editor-section" id="editorRecipientSection">
      <h4>Invoice recipient</h4>
      <div class="kv">
        <label>Name</label><span>${escapeHtml(payerName)}</span>
        <label>Type</label><span>${escapeHtml(payerTypeLabel)}</span>
        <label>Email</label><span>${escapeHtml(bp.billing_email || "—")}</span>
        <label>Phone</label><span>${escapeHtml(bp.billing_phone || "—")}</span>
        <label>Delivery method</label><span>${escapeHtml(deliveryLabel)}</span>
        ${addressSummary ? `<label>Address</label><span>${escapeHtml(addressSummary)}</span>` : ""}
      </div>
      <button type="button" id="changeRecipientBtn" class="save">Change invoice recipient</button>
      <div id="recipientSearchArea" hidden></div>
    </div>

    <div class="editor-section" id="editorCoveredSection">
      <h4>Pays for</h4>
      ${coveredHtml}
      <button type="button" id="addCoveredBtn" class="save">Add Client</button>
      <div id="coveredSearchArea" hidden></div>
    </div>

    <div class="editor-section" id="editorDeliverySection">
      <h4>Billing delivery</h4>
      <div class="field-grid">
        <label class="field">Billing name<input id="editBillingName" value="${escapeHtml(bp.billing_name || "")}"></label>
        <label class="field">Billing email<input id="editBillingEmail" type="email" value="${escapeHtml(bp.billing_email || "")}"></label>
        <label class="field">Billing phone<input id="editBillingPhone" type="tel" value="${escapeHtml(bp.billing_phone || "")}"></label>
        ${payerType === "organization" ? `<label class="field">Organization name<input id="editBillingContactName" value="${escapeHtml(bp.organization_name || "")}"></label>` : ""}
        <label class="field">Address line 1<input id="editAddr1" value="${escapeHtml(bp.billing_address_line_1 || "")}"></label>
        <label class="field">Address line 2<input id="editAddr2" value="${escapeHtml(bp.billing_address_line_2 || "")}"></label>
        <label class="field">City<input id="editCity" value="${escapeHtml(bp.billing_city || "")}"></label>
        <label class="field">State<input id="editState" value="${escapeHtml(bp.billing_state || "")}"></label>
        <label class="field">Postal code<input id="editPostal" value="${escapeHtml(bp.billing_postal_code || "")}"></label>
        <label class="field">Preferred delivery method
          <select id="editDeliveryMethod">
            <option value="unresolved" ${bp.preferred_delivery_method === "unresolved" || !bp.preferred_delivery_method ? "selected" : ""}>Unresolved</option>
            <option value="email" ${bp.preferred_delivery_method === "email" ? "selected" : ""}>Email</option>
            <option value="mail" ${bp.preferred_delivery_method === "mail" ? "selected" : ""}>Mail</option>
            <option value="both" ${bp.preferred_delivery_method === "both" ? "selected" : ""}>Both</option>
          </select>
        </label>
        <label class="field wide">Administrative notes<input id="editAdminNotes" value="${escapeHtml(data.account.administrative_notes || "")}"></label>
      </div>
    </div>

    <div class="record-actions">
      <button type="button" id="saveBillingRelationshipBtn" class="save">Save changes</button>
    </div>
    <div id="editorErrorBox" class="lifecycle-confirm-box" hidden></div>
    <div id="editorDuplicateBox" class="lifecycle-confirm-box" hidden></div>

    <h4>Session History</h4><div class="compact-list">${data.sessions.slice(0, 8).map(s => `<div><span>${fmt(s.session_date)} ${fmt(s.duration_minutes)} min ${serviceLabel(s.service_mode)} ${timeLabel(s.time_category)}</span><span>${money(centString(s.approved_rate_cents))} ${fmt(s.approved_rate_source || s.rate_source)}</span></div>`).join("") || "<span class='readonly-note'>No sessions yet.</span>"}</div>
    <h4>Rates</h4><div class="compact-list">${data.rates.map(r => `<div><span>${money(centString(r.amount_cents))} ${fmt(r.duration_minutes || "Any")} min</span><span>${r.active ? "Active" : "Inactive"}</span></div>`).join("") || "<span class='readonly-note'>No relationship-specific rates.</span>"}</div>
    <h4>Aliases</h4><div class="compact-list">${data.aliases.map(a => `<div><span>${fmt(a.raw_alias)}</span><span>${fmt(a.classification)}</span></div>`).join("") || "<span class='readonly-note'>No aliases yet.</span>"}</div>
  `;

  let editState = {
    payer_kind: payerType === "organization" ? "organization" : (bp.person_id && (data.members || []).some(m => m.person_id === bp.person_id) ? "client" : "person"),
    payer_person_id: bp.person_id || null,
    organization_billing_party_id: payerType === "organization" ? bp.billing_party_id : null,
    covered_client_ids: (data.members || []).map(m => m.person_id),
  };

  let editorDirty = false;
  const markEditorDirty = () => { editorDirty = true; };

  if ($("returnFromAccount")) $("returnFromAccount").onclick = async (event) => {
    event.preventDefault();
    if (editorDirty) {
      const confirmBox = $("editorDirtyConfirm");
      if (!confirmBox) {
        const box = document.createElement("div");
        box.id = "editorDirtyConfirm";
        box.className = "lifecycle-confirm-box";
        box.style.display = "block";
        box.innerHTML = `
          <p>You have unsaved changes. Return to review without saving?</p>
          <div class="wizard-confirm-actions">
            <button type="button" id="editorDirtyNo" class="modal-cancel">Keep editing</button>
            <button type="button" id="editorDirtyYes" class="modal-submit">Return without saving</button>
          </div>`;
        $("accountRecord").prepend(box);
        document.getElementById("editorDirtyNo").onclick = () => { box.remove(); };
        document.getElementById("editorDirtyYes").onclick = async () => {
          box.remove();
          if (!validReturnContext(returnContext)) {
            clearReturnContext();
            location.hash = "";
            showReviewWorkbench();
            return;
          }
          location.hash = "";
          await showReviewWorkbench();
          await selectCandidate(returnContext.candidateId);
        };
        return;
      }
    }
    if (!validReturnContext(returnContext)) {
      clearReturnContext();
      location.hash = "";
      showReviewWorkbench();
      return;
    }
    location.hash = "";
    await showReviewWorkbench();
    await selectCandidate(returnContext.candidateId);
  };

  if ($("deactivateAccountBtn")) {
    $("deactivateAccountBtn").onclick = () => showLifecycleConfirm(accountId, "deactivate", data.account.account_name);
  }
  if ($("reactivateAccountBtn")) {
    $("reactivateAccountBtn").onclick = () => showLifecycleConfirm(accountId, "reactivate", data.account.account_name);
  }

  $("changeRecipientBtn").onclick = () => { markEditorDirty(); openRecipientSearch(accountId, data, editState, returnContext); };
  $("addCoveredBtn").onclick = () => { markEditorDirty(); openCoveredSearch(accountId, data, editState, returnContext); };
  $("saveBillingRelationshipBtn").onclick = () => saveBillingRelationship(accountId, editState, returnContext);

  document.querySelectorAll(".covered-client-remove").forEach(btn => {
    btn.onclick = () => {
      const pid = btn.dataset.personId;
      editState.covered_client_ids = editState.covered_client_ids.filter(id => id !== pid);
      btn.closest(".covered-client-row").remove();
      markEditorDirty();
    };
  });

  const deliveryInputs = ["editBillingName", "editBillingEmail", "editBillingPhone", "editBillingContactName", "editAddr1", "editAddr2", "editCity", "editState", "editPostal", "editDeliveryMethod", "editAdminNotes"];
  deliveryInputs.forEach(id => {
    const el = $(id);
    if (el) el.addEventListener("change", markEditorDirty);
  });

  if (!location.hash.startsWith("#clients")) {
    location.hash = "clients";
    showClients();
  }
}

function openRecipientSearch(accountId, data, editState, returnContext) {
  const area = $("recipientSearchArea");
  area.hidden = false;
  area.innerHTML = `
    <div class="wizard-payer-types" id="editorPayerTypes">
      <div class="wizard-payer-choice ${editState.payer_kind === "client" ? "selected" : ""}" data-type="client" tabindex="0" role="radio">
        <strong>A client</strong><span class="help">Someone who attends sessions and pays for themselves</span>
      </div>
      <div class="wizard-payer-choice ${editState.payer_kind === "person" ? "selected" : ""}" data-type="person" tabindex="0" role="radio">
        <strong>Another person</strong><span class="help">Someone who receives invoices but does not attend</span>
      </div>
      <div class="wizard-payer-choice ${editState.payer_kind === "organization" ? "selected" : ""}" data-type="organization" tabindex="0" role="radio">
        <strong>An organization</strong><span class="help">A company or agency that receives invoices</span>
      </div>
    </div>
    <div class="modal-search-wrap">
      <label for="editorRecipientInput">Search</label>
      <input id="editorRecipientInput" class="modal-search" type="search" placeholder="Type a name..." autocomplete="off">
    </div>
    <div class="modal-results" id="editorRecipientResults"></div>
    <div class="modal-selected" id="editorRecipientSelected" hidden></div>
  `;
  const choices = area.querySelectorAll(".wizard-payer-choice");
  choices.forEach(el => {
    el.addEventListener("click", () => {
      choices.forEach(c => c.classList.remove("selected"));
      el.classList.add("selected");
      editState.payer_kind = el.dataset.type;
      if (el.dataset.type !== "organization") {
        editState.organization_billing_party_id = null;
      }
      if (el.dataset.type === "organization") {
        editState.payer_person_id = null;
      }
      $("editorRecipientSelected").hidden = true;
      $("editorRecipientResults").innerHTML = "";
      $("editorRecipientInput").value = "";
      $("editorRecipientInput").focus();
    });
    el.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); el.click(); } });
  });

  const input = $("editorRecipientInput");
  const results = $("editorRecipientResults");
  const selectedDiv = $("editorRecipientSelected");
  let searchRows = [];

  const doSearch = debounce(async (q) => {
    if (!q.trim()) { searchRows = []; results.innerHTML = ""; return; }
    try {
      if (editState.payer_kind === "organization") {
        searchRows = await api(`/api/organization-billing-parties?q=${encodeURIComponent(q)}`);
      } else {
        searchRows = await api(`/api/people?q=${encodeURIComponent(q)}`);
      }
      renderRecipientResults(results, searchRows, editState.payer_kind, editState);
    } catch (err) { results.innerHTML = `<div class="modal-empty">${escapeHtml(err.message || "Search failed.")}</div>`; }
  }, 200);
  input.addEventListener("input", (e) => doSearch(e.target.value));
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); doSearch(e.target.value); } });
  input.focus();
}

function renderRecipientResults(container, rows, kind, editState) {
  if (!rows.length) { container.innerHTML = '<div class="modal-empty">No results found.</div>'; return; }
  const selectedId = kind === "organization" ? editState.organization_billing_party_id : editState.payer_person_id;
  container.innerHTML = rows.map(row => {
    const id = kind === "organization" ? row.billing_party_id : row.person_id;
    const name = kind === "organization" ? (row.organization_name || row.billing_name || "Unnamed") : (row.display_name || "Unnamed");
    return `<div class="modal-result-row ${id === selectedId ? "selected" : ""}" data-id="${escapeHtml(id)}" tabindex="0" role="button">
      <span>${escapeHtml(name)}</span>
    </div>`;
  }).join("");
  container.querySelectorAll(".modal-result-row").forEach(el => {
    el.addEventListener("click", () => {
      const id = el.dataset.id;
      if (kind === "organization") {
        editState.organization_billing_party_id = id;
        editState.payer_person_id = null;
        const org = rows.find(r => r.billing_party_id === id);
        showEditorRecipientSelected(org.organization_name || org.billing_name, "organization");
      } else {
        editState.payer_person_id = id;
        editState.organization_billing_party_id = null;
        const person = rows.find(r => r.person_id === id);
        showEditorRecipientSelected(person.display_name, kind);
      }
    });
    el.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); el.click(); } });
  });
}

function showEditorRecipientSelected(name, kind) {
  const div = $("editorRecipientSelected");
  div.hidden = false;
  const label = kind === "organization" ? "Selected organization" : kind === "client" ? "Selected client" : "Selected person";
  div.innerHTML = `${escapeHtml(label)}: <strong>${escapeHtml(name)}</strong>`;
}

function openCoveredSearch(accountId, data, editState, returnContext) {
  const area = $("coveredSearchArea");
  area.hidden = false;
  area.innerHTML = `
    <div class="modal-search-wrap">
      <label for="editorCoveredInput">Search clients to add</label>
      <input id="editorCoveredInput" class="modal-search" type="search" placeholder="Type a client name..." autocomplete="off">
    </div>
    <div class="modal-results" id="editorCoveredResults"></div>
  `;
  const input = $("editorCoveredInput");
  const results = $("editorCoveredResults");
  let searchRows = [];

  const doSearch = debounce(async (q) => {
    if (!q.trim()) { searchRows = []; results.innerHTML = ""; return; }
    try {
      searchRows = await api(`/api/people?q=${encodeURIComponent(q)}`);
      const selectedIds = new Set(editState.covered_client_ids);
      renderEditorCoveredResults(results, searchRows, selectedIds, editState);
    } catch (err) { results.innerHTML = `<div class="modal-empty">${escapeHtml(err.message || "Search failed.")}</div>`; }
  }, 200);
  input.addEventListener("input", (e) => doSearch(e.target.value));
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); doSearch(e.target.value); } });
  input.focus();
}

function renderEditorCoveredResults(container, rows, selectedIds, editState) {
  if (!rows.length) { container.innerHTML = '<div class="modal-empty">No clients found.</div>'; return; }
  container.innerHTML = rows.map(row => {
    const isSelected = selectedIds.has(row.person_id);
    return `<div class="modal-result-row ${isSelected ? "selected already-included" : ""}" data-person-id="${escapeHtml(row.person_id)}" tabindex="0" role="button">
      <span>${escapeHtml(row.display_name || "Unnamed client")}</span>
      ${isSelected ? '<span class="help already-included-label">Click to remove</span>' : (row.person_code ? `<span class="help">${escapeHtml(row.person_code)}</span>` : "")}
    </div>`;
  }).join("");
  container.querySelectorAll(".modal-result-row").forEach(el => {
    const pid = el.dataset.personId;
    if (selectedIds.has(pid)) {
      el.addEventListener("click", () => {
        editState.covered_client_ids = editState.covered_client_ids.filter(id => id !== pid);
        const row = el.closest(".covered-client-row") || document.querySelector(`.covered-client-row[data-person-id="${pid}"]`);
        if (row) row.remove();
        const newSelected = new Set(editState.covered_client_ids);
        renderEditorCoveredResults(container, rows, newSelected, editState);
      });
    } else {
      el.addEventListener("click", () => {
        if (!editState.covered_client_ids.includes(pid)) {
          editState.covered_client_ids.push(pid);
          const person = rows.find(r => r.person_id === pid);
          const list = document.querySelector(".covered-clients-list");
          if (list && person) {
            const div = document.createElement("div");
            div.className = "covered-client-row";
            div.dataset.personId = pid;
            div.innerHTML = `<span class="covered-client-name">${escapeHtml(person.display_name)}</span>${person.person_code ? `<span class="help">${escapeHtml(person.person_code)}</span>` : ""}<button type="button" class="covered-client-remove" data-person-id="${escapeHtml(pid)}" aria-label="Remove ${escapeHtml(person.display_name)}">&times;</button>`;
            list.appendChild(div);
            div.querySelector(".covered-client-remove").onclick = () => {
              editState.covered_client_ids = editState.covered_client_ids.filter(id => id !== pid);
              div.remove();
            };
          }
        }
        const newSelected = new Set(editState.covered_client_ids);
        renderEditorCoveredResults(container, rows, newSelected, editState);
      });
    }
    el.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); el.click(); } });
  });
}

async function saveBillingRelationship(accountId, editState, returnContext) {
  const saveBtn = $("saveBillingRelationshipBtn");
  const errorBox = $("editorErrorBox");
  const dupBox = $("editorDuplicateBox");
  if (saveBtn.disabled) return;

  if (!editState.payer_person_id && !editState.organization_billing_party_id) {
    errorBox.hidden = false;
    errorBox.textContent = "Select an invoice recipient before saving.";
    return;
  }
  if (!editState.covered_client_ids || editState.covered_client_ids.length === 0) {
    errorBox.hidden = false;
    errorBox.textContent = "At least one covered client is required for an active relationship.";
    return;
  }

  saveBtn.disabled = true;
  saveBtn.textContent = "Saving changes…";
  errorBox.hidden = true;
  dupBox.hidden = true;

  const billingDelivery = {};
  const fields = [
    ["editBillingName", "billing_name"],
    ["editBillingEmail", "billing_email"],
    ["editBillingPhone", "billing_phone"],
    ["editBillingContactName", "organization_name"],
    ["editAddr1", "billing_address_line_1"],
    ["editAddr2", "billing_address_line_2"],
    ["editCity", "billing_city"],
    ["editState", "billing_state"],
    ["editPostal", "billing_postal_code"],
    ["editDeliveryMethod", "preferred_delivery_method"],
  ];
  for (const [elId, field] of fields) {
    const el = $(elId);
    if (el) billingDelivery[field] = el.value.trim() || null;
  }
  const adminNotesEl = $("editAdminNotes");
  const adminNotes = adminNotesEl ? adminNotesEl.value.trim() : null;

  const payload = {
    payer_kind: editState.payer_kind,
    covered_client_ids: editState.covered_client_ids,
    billing_delivery: billingDelivery,
    administrative_notes: adminNotes,
  };
  if (editState.payer_kind === "organization") {
    payload.organization_billing_party_id = editState.organization_billing_party_id;
  } else {
    payload.payer_person_id = editState.payer_person_id;
  }

  try {
    await api(`/api/accounts/${accountId}/update-billing-relationship`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    if (validReturnContext(returnContext)) {
      clearReturnContext();
      location.hash = "";
      try {
        await api(`/api/review/candidates/${returnContext.candidateId}/refresh`, { method: "POST", body: "{}" });
      } catch (_) {}
      await loadList();
      await showReviewWorkbench();
      await selectCandidate(returnContext.candidateId);
      return;
    }
    await openAccountRecord(accountId);
    await loadClients();
  } catch (err) {
    saveBtn.disabled = false;
    saveBtn.textContent = "Save changes";
    const msg = err && (err.error || err.message) ? (err.error || err.message) : "Failed to save billing relationship.";
    if (msg.includes("already exists")) {
      dupBox.hidden = false;
      dupBox.innerHTML = `<p>This billing relationship already exists.</p>
        <div class="wizard-duplicate-actions">
          <button type="button" id="editorOpenExisting" class="modal-submit">Open existing relationship</button>
          <button type="button" id="editorCancelDup" class="modal-back">Cancel changes</button>
        </div>`;
      const openBtn = document.getElementById("editorOpenExisting");
      if (openBtn) openBtn.onclick = async () => {
        try {
          const dup = await api(`/api/billing-relationships/find-duplicate?payer_kind=${encodeURIComponent(editState.payer_kind)}&payer_person_id=${encodeURIComponent(editState.payer_person_id || "")}&organization_billing_party_id=${encodeURIComponent(editState.organization_billing_party_id || "")}&covered_client_ids=${encodeURIComponent(editState.covered_client_ids.join(","))}`);
          if (dup && dup.account_id) {
            await openAccountRecord(dup.account_id, { returnContext });
          }
        } catch (_) {
          dupBox.innerHTML = "<p>Could not find the existing relationship.</p>";
        }
      };
      const cancelBtn = document.getElementById("editorCancelDup");
      if (cancelBtn) cancelBtn.onclick = () => { dupBox.hidden = true; };
    } else {
      errorBox.hidden = false;
      errorBox.textContent = escapeHtml(msg);
    }
  }
}

async function loadPeople() {
  const rows = await api(`/api/people?full=1&q=${encodeURIComponent($("peopleSearch").value || "")}`);
  $("peopleRows").innerHTML = rows.map(row => `
    <tr data-person="${escapeAttr(row.person_id)}">
      <td>${fmt(row.person_code)}</td>
      <td>${fmt(row.last_name)}</td>
      <td>${fmt(row.first_name)}</td>
      <td><span class="primary">${fmt(row.display_name)}</span></td>
      <td>${fmt(row.accounts)}</td>
      <td>${fmt(row.billing_for)}</td>
      <td>${fmt(row.last_session)}</td>
      <td>${fmt(row.active_status)}</td>
    </tr>
  `).join("");
  document.querySelectorAll("#peopleRows tr").forEach(row => row.onclick = () => { location.hash = "people/" + row.dataset.person; });
}

function billingAddressSummary(billingParty) {
  if (!billingParty) return "";
  const cityLine = [billingParty.billing_city, billingParty.billing_state, billingParty.billing_postal_code].filter(Boolean).join(", ").replace(", ,", ",");
  return [
    billingParty.billing_address_line_1,
    billingParty.billing_address_line_2,
    cityLine
  ].filter(Boolean).join(" • ");
}

function personRateOverrideLine(rule) {
  const duration = rule.custom_duration_minutes || rule.duration_minutes;
  return `${money(centString(rule.amount_cents))} • ${userFacingSessionLabel(rule.billing_session_type, rule.appointment_status, rule.custom_service_description || "")} • ${appointmentStatusRuleLabel(rule.appointment_status)} • ${fmt(duration)} min • ${timeLabel(rule.time_category)} • ${fmt(rule.effective_from)}`;
}

async function openPersonRecord(personId, options = {}) {
  const showAllSessions = !!options.showAllSessions;
  state.currentPersonId = personId;
  state.personShowAllSessions = showAllSessions;
  state.returnCandidate = state.selected;
  const data = await api(`/api/people/${personId}`);
  const visibleSessions = showAllSessions ? data.sessions : data.sessions.slice(0, 10);
  const summary = data.billing_summary || {};
  const billingSetup = data.billing_setup || [];
  const payers = data.payers_for_client || [];
  const peopleBilledFor = data.people_billed_for || [];
  const invoices = data.invoices || [];
  const personName = fmt(data.person.display_name);

  const deliveryLabels = { email: "Email", mail: "Mail", both: "Email and mail", unresolved: "Unresolved" };
  const activeSetups = billingSetup.filter(b => b.active);
  const inactiveSetups = billingSetup.filter(b => !b.active);
  const billingSetupHtml = billingSetup.length
    ? billingSetup.map(b => {
        const addr = billingAddressSummary(b);
        const delivery = b.preferred_delivery_method && b.preferred_delivery_method !== "unresolved" ? deliveryLabels[b.preferred_delivery_method] || fmt(b.preferred_delivery_method) : "—";
        const isSelfPay = b.person_id === personId;
        const label = isSelfPay ? `<div class="billing-card-label">Bills sent to this client</div>` : "";
        const statusBadge = b.active
          ? `<span class="status-pill active">Active</span>`
          : `<span class="status-pill inactive">Inactive</span>`;

        let dupWarning = "";
        if (b.active) {
          const samePayerInactive = inactiveSetups.filter(i => i.billing_name === b.billing_name);
          if (samePayerInactive.length) {
            const activeMissingEmail = !b.billing_email;
            const activeMissingAddress = !b.billing_address_line_1 || !b.billing_city || !b.billing_state || !b.billing_postal_code;
            const inactiveHasInfo = samePayerInactive.some(i => i.billing_email || i.billing_address_line_1);
            if ((activeMissingEmail || activeMissingAddress) && inactiveHasInfo) {
              dupWarning = `<div class="billing-card-warning">
                <div class="billing-card-warning-title">The active billing setup is missing required delivery details. An inactive setup for this payer contains contact information.</div>
                ${samePayerInactive.map(i => `<button class="mini" data-copy-contact-source="${escapeAttr(i.billing_party_id)}" data-copy-contact-target="${escapeAttr(b.billing_party_id)}">Review Inactive Details</button>`).join("")}
              </div>`;
            } else {
              dupWarning = `<div class="billing-card-warning billing-card-warning-compact">Another inactive billing setup exists for this payer.</div>`;
            }
          }
        }

        const actionBtn = b.active
          ? `<button class="mini danger" data-deactivate-billing="${escapeAttr(b.billing_party_id)}">Deactivate</button>`
          : `<button class="mini" data-reactivate-billing="${escapeAttr(b.billing_party_id)}">Reactivate</button>`;
        return `<div class="billing-card${b.active ? "" : " inactive"}">
          ${label}
          <div class="billing-card-name">${fmt(b.billing_name)}</div>
          <div class="billing-card-details">
            <div>${b.billing_email ? fmt(b.billing_email) : "—"}</div>
            <div>${b.billing_phone ? fmt(b.billing_phone) : "—"}</div>
            <div>${addr ? fmt(addr) : "—"}</div>
            <div>Delivery: ${delivery}</div>
            <div>${statusBadge}</div>
          </div>
          ${dupWarning}
          <div class="billing-card-actions">
            <button class="mini" data-edit-billing="${escapeAttr(b.billing_party_id)}">Edit</button>
            ${actionBtn}
          </div>
        </div>`;
      }).join("")
    : `<span class="readonly-note">No billing setup saved</span>`;

  const relationshipLines = [];
  for (const p of payers) {
    const isSelfPay = p.payer_person_id === personId;
    const sessionInfo = `${fmt(p.session_count)} session${p.session_count === 1 ? "" : "s"}${p.most_recent_session_date ? ` • latest ${fmt(p.most_recent_session_date)}` : ""}`;
    if (isSelfPay) {
      relationshipLines.push(`<div class="relationship-line"><span>${escapeHtml(personName)} pays for herself</span><span class="relationship-meta">${escapeHtml(sessionInfo)}</span></div>`);
    } else {
      relationshipLines.push(`<div class="relationship-line"><span>${escapeHtml(personName)} is billed to ${escapeHtml(fmt(p.payer_display_name))}</span><span class="relationship-meta">${escapeHtml(sessionInfo)}</span></div>`);
    }
  }
  for (const p of peopleBilledFor) {
    const isSelf = p.participant_person_id === personId;
    if (isSelf) continue;
    const sessionInfo = `${fmt(p.session_count)} session${p.session_count === 1 ? "" : "s"}${p.latest_session_date ? ` • latest ${fmt(p.latest_session_date)}` : ""}`;
    relationshipLines.push(`<div class="relationship-line"><span>${escapeHtml(personName)} pays for ${escapeHtml(fmt(p.participant_display_name))}</span><span class="relationship-meta">${escapeHtml(sessionInfo)}</span></div>`);
  }
  const relationshipsHtml = relationshipLines.length
    ? relationshipLines.join("")
    : `<span class="readonly-note">No billing relationships yet.</span>`;

  const accountInfoHtml = (data.accounts || []).length
    ? data.accounts.map(a => `<div class="compact-list-item"><span>${fmt(a.account_name)} • ${fmt(a.relationship_role)}${a.is_primary ? " • Primary" : ""}</span><button class="mini" data-open-account="${escapeAttr(a.account_id)}">Open</button></div>`).join("")
    : `<span class="readonly-note">No related billing group information.</span>`;

  const sessionsRowsHtml = visibleSessions.length
    ? visibleSessions.map(s => `<tr>
        <td>${fmt(s.session_date)}</td>
        <td>${fmt(s.other_participant_names ? "With " + s.other_participant_names : "Solo")}</td>
        <td>${userFacingSessionLabel(s.billing_session_type, s.appointment_status, s.custom_service_description || "")}</td>
        <td>${fmt(s.custom_duration_minutes || s.duration_minutes)} min</td>
        <td>${timeLabel(s.time_category)}</td>
        <td>${money(centString(s.approved_rate_cents))}</td>
        <td>${escapeHtml(paymentHandlingLabel(s.payment_status))}</td>
        <td>${fmt(s.review_status)}</td>
        <td><button class="mini" data-open-candidate="${escapeAttr(s.candidate_id)}">Open in Review</button></td>
      </tr>`).join("")
    : `<tr><td colspan="9" class="readonly-note">No sessions yet.</td></tr>`;

  const invoiceRowsHtml = invoices.length
    ? invoices.map(inv => `<tr data-invoice-id="${escapeAttr(inv.invoice_id)}">
        <td><span class="primary">${fmt(inv.invoice_number || "Draft")}</span></td>
        <td>${fmt(inv.billing_period_start)} – ${fmt(inv.billing_period_end)}</td>
        <td>${fmt(inv.invoice_date)}</td>
        <td>${fmt(inv.bill_to_name)}</td>
        <td><span class="status-pill ${escapeAttr(inv.status)}">${fmt(inv.status)}</span></td>
        <td><span class="status-pill ${escapeAttr(inv.payment_status || "unpaid")}">${escapeHtml(paymentStatusLabel(inv.payment_status || "unpaid"))}</span></td>
        <td>${money(centString(inv.total_cents))}</td>
        <td>${money(centString(inv.paid_cents || 0))}</td>
        <td>${money(centString(inv.balance_cents))}</td>
        <td><button class="mini" data-open-invoice="${escapeAttr(inv.invoice_id)}">Open</button></td>
      </tr>`).join("")
    : `<tr><td colspan="10" class="readonly-note">No invoices yet.</td></tr>`;

  $("personRecordView").innerHTML = `
    ${state.returnCandidate ? `<a href="#" class="return-link" id="returnFromPerson">← Return to ${escapeHtml(state.detail?.session?.raw_calendar_title || "")} — ${escapeHtml(state.detail?.session?.session_date || "")}</a>` : ""}
    <a href="#people" class="return-link" id="backToClients">← Back to Clients</a>
    <div class="client-workspace">
      <div class="client-header">
        <h2>${fmt(data.person.display_name)}</h2>
        <div class="meta"><span>${fmt(data.person.person_code)}</span><span>${fmt(data.person.active_status)}</span></div>
      </div>

      <div class="summary-cards">
        <div class="summary-card"><div class="summary-card-label">Total Finalized Invoices</div><div class="summary-card-value">${fmt(summary.total_finalized_invoices)}</div></div>
        <div class="summary-card"><div class="summary-card-label">Total Payments Applied</div><div class="summary-card-value">${money(centString(summary.total_paid_cents))}</div></div>
        <div class="summary-card"><div class="summary-card-label">Current Balance</div><div class="summary-card-value">${money(centString(summary.current_balance_cents))}</div></div>
        <div class="summary-card"><div class="summary-card-label">Account Status</div><div class="summary-card-value">${escapeHtml(summary.account_status || "—")}</div></div>
      </div>

      <section class="client-section">
        <h3>Client Details</h3>
        <div class="field-grid">
          <label class="field">First Name<input id="recordFirstName" value="${escapeAttr(data.person.first_name || "")}"></label>
          <label class="field">Last Name<input id="recordLastName" value="${escapeAttr(data.person.last_name || "")}"></label>
          <label class="field">Preferred Name<input id="recordPreferredName" value="${escapeAttr(data.person.preferred_name || "")}"></label>
          <label class="field">Display Name<input id="recordDisplayName" value="${escapeAttr(data.person.display_name || "")}"></label>
          <label class="field">Email<input id="recordPersonEmail" value="${escapeAttr(data.person.billing_email || "")}"></label>
          <label class="field">Phone<input id="recordPersonPhone" value="${escapeAttr(data.person.billing_phone || "")}"></label>
          <label class="field">Status<input value="${escapeAttr(data.person.active_status || "")}" readonly></label>
          <label class="field wide">Administrative Notes<input id="recordPersonNotes" value="${escapeAttr(data.person.administrative_notes || "")}"></label>
        </div>
        <div class="record-actions"><button id="savePersonRecord" class="save">Save Client</button></div>
      </section>

      <section class="client-section">
        <h3>Billing Setup <button class="mini" id="addBillingSetupBtn">Add Billing Setup</button></h3>
        <div id="billingSetupMessage" class="billing-setup-message"></div>
        <div id="billingSetupFormContainer"></div>
        <div class="billing-cards">${billingSetupHtml}</div>
      </section>

      <section class="client-section">
        <h3>Billing Relationships</h3>
        <div class="relationship-list">${relationshipsHtml}</div>
        <h4 class="secondary-heading">Related billing group information</h4>
        <div class="compact-list">${accountInfoHtml}</div>
      </section>

      <section class="client-section">
        <h3>Invoices</h3>
        <div class="table-scroll-wrap">
          <table class="review-table client-invoices-table">
            <thead><tr><th>Invoice Number</th><th>Billing Period</th><th>Issue Date</th><th>Bill To</th><th>Invoice Status</th><th>Payment Status</th><th>Total</th><th>Paid</th><th>Balance</th><th>Open</th></tr></thead>
            <tbody>${invoiceRowsHtml}</tbody>
          </table>
        </div>
      </section>

      <section class="client-section">
        <h3>Sessions</h3>
        <div class="table-scroll-wrap">
          <table class="review-table client-sessions-table">
            <thead><tr><th>Date</th><th>Participants</th><th>Session Type</th><th>Duration</th><th>Time Category</th><th>Rate</th><th>Payment Handling</th><th>Review Status</th><th>Open in Review</th></tr></thead>
            <tbody>${sessionsRowsHtml}</tbody>
          </table>
        </div>
        ${data.sessions.length > 10 ? `<div class="record-actions"><button id="toggleAllSessions">${showAllSessions ? "Show newest 10" : "Show all"}</button></div>` : ""}
      </section>

      <section class="client-section">
        <h3>Rate Preferences</h3>
        <h4>Individual Rate Overrides</h4>
        <div class="compact-list">${(data.active_rate_exceptions || []).map(r => `<div class="compact-list-item"><span>${personRateOverrideLine(r)}</span></div>`).join("") || "<span class='readonly-note'>Uses standard Rate Card. No client-specific override.</span>"}</div>
        <h4>Joint-Session Overrides</h4>
        <div class="compact-list">${(data.joint_rate_exceptions || []).map(r => `<div class="compact-list-item"><span>${personRateOverrideLine(r)} • With ${fmt(r.participant_names)}</span></div>`).join("") || "<span class='readonly-note'>No joint-session overrides.</span>"}</div>
        <details>
          <summary>Add Custom Rate</summary>
          <div class="field-grid">
            <label class="field">Session type<select id="personRateSessionType">${billingTypeOptions("psychotherapy")}</select></label>
            <label class="field">Duration<select id="personRateDuration"><option value="30">30 minutes</option><option value="60" selected>60 minutes</option><option value="90">90 minutes</option><option value="120">120 minutes</option></select></label>
            <label class="field">Time category<select id="personRateTimeCategory">${optionSet(["standard","evening","weekend"], "standard")}</select></label>
            <label class="field">Amount<input id="personRateAmount" placeholder="350.00"></label>
            <label class="field">Effective date<input id="personRateEffectiveFrom" type="date"></label>
          </div>
          <div class="record-actions"><button id="savePersonRateRule" class="save">Save Client Rate Override</button></div>
        </details>
      </section>

      <details>
        <summary>Advanced</summary>
        <div class="client-section">
          <h4>Known Calendar Names</h4>
          <div class="combobox"><input id="personAliasInput" placeholder="Add approved calendar name"><button class="mini" id="savePersonAlias">+</button></div>
          <div class="compact-list">${data.aliases.map(a => `<div class="compact-list-item"><span>${fmt(a.raw_alias)} • ${a.approved_by_user ? "approved" : "inactive"}</span><button class="mini" data-alias-id="${escapeAttr(a.alias_id)}" data-raw-alias="${escapeAttr(a.raw_alias || "")}" data-approved="${a.approved_by_user ? "1" : "0"}">${a.approved_by_user ? "Deactivate" : "Inactive"}</button></div>`).join("") || "<span class='readonly-note'>No aliases yet.</span>"}</div>
          <h4>Audit History</h4>
          <div class="compact-list">${(data.audit || []).map(entry => `<div class="compact-list-item"><span>${fmt(entry.created_at)} • ${escapeHtml(entry.action || "")}</span></div>`).join("") || "<span class='readonly-note'>No audit history yet.</span>"}</div>
        </div>
      </details>
    </div>
  `;
  if ($("returnFromPerson")) $("returnFromPerson").onclick = (event) => { event.preventDefault(); location.hash = ""; showReviewWorkbench(); };
  document.querySelectorAll("[data-open-account]").forEach(button => {
    button.onclick = async () => {
      await openAccountRecord(button.dataset.openAccount);
    };
  });
  document.querySelectorAll("[data-open-candidate]").forEach(button => {
    button.onclick = async () => {
      location.hash = "";
      await loadList();
      await showReviewWorkbench();
      await selectCandidate(button.dataset.openCandidate);
    };
  });
  document.querySelectorAll("[data-open-invoice]").forEach(button => {
    button.onclick = async () => {
      history.pushState({}, "", "/invoices");
      await showInvoices();
      await openInvoice(button.dataset.openInvoice);
    };
  });
  if ($("toggleAllSessions")) $("toggleAllSessions").onclick = async () => openPersonRecord(personId, { showAllSessions: !showAllSessions });
  $("savePersonRecord").onclick = async () => {
    await api(`/api/people/${personId}`, { method: "POST", body: JSON.stringify({
      first_name: $("recordFirstName").value,
      last_name: $("recordLastName").value,
      preferred_name: $("recordPreferredName").value,
      display_name: $("recordDisplayName").value,
      billing_email: $("recordPersonEmail").value,
      billing_phone: $("recordPersonPhone").value,
      administrative_notes: $("recordPersonNotes").value,
      active: true
    }) });
    await openPersonRecord(personId);
    await loadPeople();
  };
  if ($("savePersonRateRule")) $("savePersonRateRule").onclick = async () => {
    await api("/api/rate-rules", {
      method: "POST",
      body: JSON.stringify({
        applies_to: "person",
        person_id: personId,
        billing_session_type: $("personRateSessionType").value,
        duration_minutes: $("personRateDuration").value,
        time_category: $("personRateTimeCategory").value,
        amount: $("personRateAmount").value,
        effective_from: $("personRateEffectiveFrom").value
      })
    });
    await openPersonRecord(personId, { showAllSessions });
  };
  if ($("savePersonAlias")) $("savePersonAlias").onclick = async () => {
    const rawAlias = $("personAliasInput").value.trim();
    if (!rawAlias) return;
    await api(`/api/people/${personId}/aliases`, {
      method: "POST",
      body: JSON.stringify({ raw_alias: rawAlias, approved_by_user: true })
    });
    await openPersonRecord(personId, { showAllSessions });
  };
  document.querySelectorAll("[data-alias-id]").forEach(button => {
    button.onclick = async () => {
      if (button.dataset.approved !== "1") return;
      await api(`/api/people/${personId}/aliases`, {
        method: "POST",
        body: JSON.stringify({ alias_id: button.dataset.aliasId, raw_alias: button.dataset.rawAlias || "", approved_by_user: false })
      });
      await openPersonRecord(personId, { showAllSessions });
    };
  });

  if ($("addBillingSetupBtn")) $("addBillingSetupBtn").onclick = () => {
    showBillingSetupForm(null, data.person.display_name || "");
  };
  document.querySelectorAll("[data-edit-billing]").forEach(button => {
    button.onclick = () => {
      const bp = billingSetup.find(b => b.billing_party_id === button.dataset.editBilling);
      if (bp) showBillingSetupForm(bp, data.person.display_name || "");
    };
  });
  document.querySelectorAll("[data-deactivate-billing]").forEach(button => {
    button.onclick = () => {
      const existing = document.getElementById("billingDeactConfirm");
      if (existing) existing.remove();
      const box = document.createElement("div");
      box.id = "billingDeactConfirm";
      box.className = "lifecycle-confirm-box";
      box.style.display = "block";
      box.innerHTML = `
        <p>Deactivating this billing setup prevents it from being suggested for future billing. Historical sessions and invoices will remain unchanged.</p>
        <div class="wizard-confirm-actions">
          <button type="button" id="billingDeactNo" class="modal-cancel">Cancel</button>
          <button type="button" id="billingDeactYes" class="modal-submit">Deactivate</button>
        </div>`;
      button.closest(".billing-card")?.prepend(box);
      document.getElementById("billingDeactNo").onclick = () => { box.remove(); button.focus(); };
      document.getElementById("billingDeactYes").onclick = async () => {
        box.remove();
        try {
          await api(`/api/billing-parties/${button.dataset.deactivateBilling}`, {
            method: "POST",
            body: JSON.stringify({ active: false })
          });
          await openPersonRecord(personId, { showAllSessions });
        } catch (err) {
          showBillingSetupMessage(err.message || "Failed to deactivate billing setup.", "error");
        }
      };
    };
  });
  document.querySelectorAll("[data-reactivate-billing]").forEach(button => {
    button.onclick = async () => {
      try {
        await api(`/api/billing-parties/${button.dataset.reactivateBilling}`, {
          method: "POST",
          body: JSON.stringify({ active: true })
        });
        await openPersonRecord(personId, { showAllSessions });
      } catch (err) {
        showBillingSetupMessage(err.message || "Failed to reactivate billing setup.", "error");
      }
    };
  });
  document.querySelectorAll("[data-copy-contact-source]").forEach(button => {
    button.onclick = async () => {
      const sourceId = button.dataset.copyContactSource;
      const targetId = button.dataset.copyContactTarget;
      const existing = document.getElementById("copyContactConfirm");
      if (existing) existing.remove();
      const box = document.createElement("div");
      box.id = "copyContactConfirm";
      box.className = "lifecycle-confirm-box";
      box.style.display = "block";
      box.innerHTML = `<p>Loading contact details from inactive setup…</p>`;
      button.closest(".billing-card")?.prepend(box);
      try {
        const preview = await api(`/api/billing-parties/${targetId}/copy-contact-preview?source_billing_party_id=${encodeURIComponent(sourceId)}`);
        const fields = preview.fields_to_copy || [];
        const deliveryToCopy = preview.delivery_method_to_copy;
        if (!fields.length && !deliveryToCopy) {
          box.innerHTML = `<p>No copyable contact details found. The inactive setup may not have additional information, or the active setup already has all fields populated.</p>
            <div class="wizard-confirm-actions"><button type="button" class="modal-cancel" id="copyContactClose">Close</button></div>`;
          document.getElementById("copyContactClose").onclick = () => { box.remove(); button.focus(); };
          return;
        }
        const fieldLabels = {
          billing_email: "Billing email",
          billing_address_line_1: "Address line 1",
          billing_address_line_2: "Address line 2",
          billing_city: "City",
          billing_state: "State",
          billing_postal_code: "Postal code",
        };
        const fieldListHtml = fields.map(f => `<li><label><input type="checkbox" class="copy-field-checkbox" data-field="${escapeAttr(f.field)}" checked> ${escapeHtml(fieldLabels[f.field] || f.field)}: ${escapeHtml(f.value)}</label></li>`).join("");
        const deliveryHtml = deliveryToCopy
          ? `<li><label><input type="checkbox" id="copyDeliveryCheckbox" checked> Preferred delivery method: ${escapeHtml(deliveryToCopy)}</label></li>`
          : "";
        box.innerHTML = `
          <p class="copy-contact-title">Copy Contact Details to Active Setup</p>
          <p class="copy-contact-source">From inactive setup: <strong>${escapeHtml(preview.source_billing_name || "")}</strong></p>
          <p class="copy-contact-target">To active setup: <strong>${escapeHtml(preview.target_billing_name || "")}</strong></p>
          <p class="copy-contact-note">Only empty fields on the active setup will be filled. Existing active values will not be overwritten.</p>
          <ul class="copy-contact-field-list">${fieldListHtml}${deliveryHtml}</ul>
          <div class="wizard-confirm-actions">
            <button type="button" class="modal-cancel" id="copyContactCancel">Cancel</button>
            <button type="button" class="modal-submit" id="copyContactConfirmBtn">Copy Selected Details</button>
          </div>`;
        document.getElementById("copyContactCancel").onclick = () => { box.remove(); button.focus(); };
        document.getElementById("copyContactConfirmBtn").onclick = async () => {
          const confirmedFields = Array.from(box.querySelectorAll(".copy-field-checkbox:checked")).map(cb => cb.dataset.field);
          const copyDelivery = !!box.querySelector("#copyDeliveryCheckbox:checked");
          if (!confirmedFields.length && !copyDelivery) {
            showBillingSetupMessage("Select at least one field to copy.", "error");
            return;
          }
          const confirmBtn = document.getElementById("copyContactConfirmBtn");
          const cancelBtn = document.getElementById("copyContactCancel");
          confirmBtn.disabled = true;
          cancelBtn.disabled = true;
          try {
            await api(`/api/billing-parties/${targetId}/copy-contact`, {
              method: "POST",
              body: JSON.stringify({
                source_billing_party_id: sourceId,
                confirmed_fields: confirmedFields,
                copy_delivery_method: copyDelivery,
              })
            });
            box.remove();
            await openPersonRecord(personId, { showAllSessions });
            showBillingSetupMessage("Contact details copied to active setup.", "success");
          } catch (err) {
            showBillingSetupMessage(err.message || "Failed to copy contact details.", "error");
            confirmBtn.disabled = false;
            cancelBtn.disabled = false;
          }
        };
      } catch (err) {
        box.innerHTML = `<p class="error">${escapeHtml(err.message || "Failed to load contact details.")}</p>
          <div class="wizard-confirm-actions"><button type="button" class="modal-cancel" id="copyContactErrClose">Close</button></div>`;
        document.getElementById("copyContactErrClose").onclick = () => { box.remove(); button.focus(); };
      }
    };
  });
}

function showBillingSetupMessage(message, type) {
  const el = $("billingSetupMessage");
  if (!el) return;
  el.textContent = message;
  el.className = `billing-setup-message ${type || ""}`;
  el.hidden = false;
}

function showBillingSetupForm(existing, defaultName) {
  const isEdit = !!existing;
  const b = existing || {};
  const container = $("billingSetupFormContainer");
  if (!container) return;

  const statusBanner = isEdit
    ? b.active
      ? `<div class="billing-setup-status-banner active">Editing the <strong>Active</strong> billing setup for ${escapeHtml(b.billing_name || defaultName || "")}. Changes will apply to new invoices.</div>`
      : `<div class="billing-setup-status-banner inactive">This billing setup is <strong>Inactive</strong> and will not be used for new invoices. Editing this record does not update the active setup.</div>`
    : "";

  container.innerHTML = `
    <div class="billing-setup-form">
      <h4>${isEdit ? "Edit Billing Setup" : "Add Billing Setup"}</h4>
      ${statusBanner}
      <div class="field-grid">
        <label class="field wide">Billing Name <input id="bsfBillingName" value="${escapeAttr(b.billing_name || defaultName || "")}"></label>
        <label class="field">Billing Email <input id="bsfBillingEmail" value="${escapeAttr(b.billing_email || "")}"></label>
        <label class="field">Billing Phone <input id="bsfBillingPhone" value="${escapeAttr(b.billing_phone || "")}"></label>
        <label class="field">Address Line 1 <input id="bsfAddress1" value="${escapeAttr(b.billing_address_line_1 || "")}"></label>
        <label class="field">Address Line 2 <input id="bsfAddress2" value="${escapeAttr(b.billing_address_line_2 || "")}"></label>
        <label class="field">City <input id="bsfCity" value="${escapeAttr(b.billing_city || "")}"></label>
        <label class="field">State <input id="bsfState" value="${escapeAttr(b.billing_state || "")}"></label>
        <label class="field">Postal Code <input id="bsfPostalCode" value="${escapeAttr(b.billing_postal_code || "")}"></label>
        <label class="field">Preferred Delivery
          <select id="bsfDeliveryMethod">
            <option value="unresolved"${(b.preferred_delivery_method || "unresolved") === "unresolved" ? " selected" : ""}>Unresolved</option>
            <option value="email"${b.preferred_delivery_method === "email" ? " selected" : ""}>Email</option>
            <option value="mail"${b.preferred_delivery_method === "mail" ? " selected" : ""}>Mail</option>
            <option value="both"${b.preferred_delivery_method === "both" ? " selected" : ""}>Email and mail</option>
          </select>
        </label>
        <label class="field wide">Administrative Notes <input id="bsfAdminNotes" value="${escapeAttr(b.administrative_notes || "")}"></label>
      </div>
      <div class="record-actions">
        <button id="bsfSaveBtn" class="save">${isEdit ? "Save Changes" : "Add Billing Setup"}</button>
        <button id="bsfCancelBtn">Cancel</button>
      </div>
    </div>
  `;
  $("bsfCancelBtn").onclick = () => { container.innerHTML = ""; };
  $("bsfSaveBtn").onclick = async () => {
    if (state.billingSetupSaving) return;
    const billingName = $("bsfBillingName").value.trim();
    if (!billingName) {
      showBillingSetupMessage("Billing name is required.", "error");
      return;
    }
    state.billingSetupSaving = true;
    $("bsfSaveBtn").disabled = true;
    $("bsfCancelBtn").disabled = true;
    const payload = {
      billing_name: billingName,
      billing_email: $("bsfBillingEmail").value,
      billing_phone: $("bsfBillingPhone").value,
      billing_address_line_1: $("bsfAddress1").value,
      billing_address_line_2: $("bsfAddress2").value,
      billing_city: $("bsfCity").value,
      billing_state: $("bsfState").value,
      billing_postal_code: $("bsfPostalCode").value,
      preferred_delivery_method: $("bsfDeliveryMethod").value,
      administrative_notes: $("bsfAdminNotes").value
    };
    try {
      if (isEdit) {
        payload.billing_party_type = "person";
        payload.person_id = state.currentPersonId;
        await api(`/api/billing-parties/${b.billing_party_id}`, {
          method: "POST",
          body: JSON.stringify(payload)
        });
      } else {
        payload.billing_party_type = "person";
        payload.person_id = state.currentPersonId;
        await api("/api/billing-parties", {
          method: "POST",
          body: JSON.stringify(payload)
        });
      }
      await openPersonRecord(state.currentPersonId, { showAllSessions: state.personShowAllSessions });
      showBillingSetupMessage(isEdit ? "Billing setup updated." : "Billing setup added.", "success");
    } catch (err) {
      showBillingSetupMessage(err.message || "Failed to save billing setup.", "error");
      $("bsfSaveBtn").disabled = false;
      $("bsfCancelBtn").disabled = false;
    } finally {
      state.billingSetupSaving = false;
    }
  };
}
["clientSearch","peopleSearch"].forEach(id => $(id).addEventListener("input", debounce(() => id === "clientSearch" ? renderBillingDirRows() : loadPeople(), 180)));
$("billingDirFilter").addEventListener("change", () => { billingDirState.filter = $("billingDirFilter").value; renderBillingDirRows(); });
$("billingDirStatusFilter").addEventListener("change", () => { billingDirState.statusFilter = $("billingDirStatusFilter").value; renderBillingDirRows(); });
$("newAccountBtn").onclick = () => {
  const returnContext = readReturnContext();
  openCreateRelationshipModal(returnContext, $("newAccountBtn"));
};
$("newPersonBtn").onclick = async () => {
  const name = prompt("Client display name");
  if (!name) return;
  const person = await api("/api/people", { method: "POST", body: JSON.stringify({ display_name: name }) });
  await loadPeople();
  location.hash = "people/" + person.person_id;
};
document.getElementById("syncNowBtn").onclick = runSyncNow;
document.getElementById("syncRebuildBtn").onclick = rebuildCalendarDataFromSheet;
document.getElementById("businessProfileForm").onsubmit = saveBusinessProfile;
[
  "businessNameInput",
  "providerDisplayNameInput",
  "credentialsDisplayInput",
  "addressLine1Input",
  "addressLine2Input",
  "cityInput",
  "stateInput",
  "postalCodeInput",
  "phoneInput",
  "emailInput",
  "payeeNameInput",
  "paymentAddressLine1Input",
  "paymentAddressLine2Input",
  "paymentCityInput",
  "paymentStateInput",
  "paymentPostalCodeInput",
  "zelleRecipientInput",
  "logoPathInput",
  "logoContainsBusinessDetailsInput",
  "showEmailBelowLogoInput",
  "invoiceTotalLabelInput",
  "invoiceNumberFormatInput",
  "insuranceEinInput",
  "insuranceNpiInput",
  "insuranceSwInput"
].forEach(id => $(id).addEventListener("input", renderBusinessProfileReadiness));

loadList();
if (location.hash === "#calendar-import") showCalendarImport();
if (location.hash === "#rate-card") showRateCard();
if (location.hash === "#clients" || location.pathname === "/clients") showClients();
if (location.pathname.startsWith("/people/") && location.pathname.split("/")[2]) {
  showPersonRecordPage(location.pathname.split("/")[2]);
} else if (location.hash.startsWith("#people/") && location.hash.split("/")[1]) {
  showPersonRecordPage(location.hash.split("/")[1]);
} else if (location.hash === "#people" || location.pathname === "/people") {
  showPeople();
}
if (location.hash === "#sessions") showSessions();
if (location.hash === "#settings") showSettings();
if (location.pathname === "/invoices") showInvoices();
if (location.pathname === "/unpaid" || location.pathname === "/payments") showPayments();
if (location.pathname === "/reports") showReports();
window.addEventListener("hashchange", () => {
  const hash = location.hash.startsWith("#") ? location.hash.slice(1) : location.hash;
  if (hash.startsWith("people/")) {
    const personId = hash.split("/")[1];
    if (personId) showPersonRecordPage(personId);
  } else if (hash === "people") {
    showPeople();
  }
});
window.addEventListener("beforeunload", event => {
  if (state.dirty.size) {
    event.preventDefault();
    event.returnValue = "";
  }
});

function ruleExplanation(row) {
  const scope = row.participant_names ? escapeHtml(row.participant_names) : row.account_name ? `billing relationship ${escapeHtml(row.account_name)}` : row.display_name ? `client ${escapeHtml(row.display_name)}` : "everyone";
  return `Applies to ${scope}; ${row.duration_minutes || "any"} minutes; ${billingTypeShort(row.billing_session_type || "any")}; ${timeLabel(row.time_category)}.`;
}

function rateSourceDescription(session, participants = []) {
  const names = participants.map(p => p.display_name || p.participant_name).filter(Boolean);
  const first = escapeHtml(names[0] || "Participant");
  const joined = names.map(escapeHtml).join(" + ");
  const source = session.approved_rate_source || session.rate_source;
  if (source === "person_exception") return `${first} exception`;
  if (source === "participant_combination_exception") return `${joined} joint-session exception`;
  if (source === "billing_relationship" || source === "account") return "Billing relationship rule";
  if (source === "evening_rule") return "Evening rate";
  if (source === "weekend_rule") return "Weekend rate";
  if (source === "service_rule") return `${serviceLabel(session.service_mode)} rate`;
  if (source === "manual_override") return "Manually changed for this session";
  if (session.duration_minutes) return `Default ${session.duration_minutes}-minute rate`;
  return "Default rate";
}

function appointmentBadge(status) {
  const labels = {scheduled:"Scheduled", completed:"Completed", cancelled:"Cancelled", no_show:"No Show", unresolved:"Status unresolved"};
  return labels[status] || labels.unresolved;
}

function calendarBadge(item) {
  if (item.hidden_from_review) return '<span class="cal-badge cal-hidden">hidden cal</span>';
  if (item.calendar_disposition === 'usually_personal_admin') return '<span class="cal-badge cal-personal">personal cal</span>';
  return '';
}

function calendarLabel(session) {
  if (session.calendar_is_preferred_work) return `${fmt(session.calendar_name)} · preferred work`;
  if (session.hidden_from_review) return `${fmt(session.calendar_name)} · hidden`;
  return fmt(session.calendar_name);
}

function calendarDispositionLabel(value) {
  return ({preferred_work:"Preferred work calendar", review_normally:"Review normally", usually_personal_admin:"Usually personal/admin", hidden:"Hidden from normal review"}[value] || "Review normally");
}

function titleTimeWarning(session) {
  if (session.title_time_matches_calendar !== 0) return "";
  return `<div class="warning">Title time ${fmt(session.title_time_text)} does not match Calendar start ${fmt(startRange(session).split(" - ")[0])}. Calendar start remains authoritative.</div>`;
}

function escapeHtml(value) {
  if (value === null || value === undefined || value === false) return "";
  return String(value).replace(/[&<>"']/g, ch => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#039;" }[ch]));
}
function escapeAttr(value) {
  return escapeHtml(value);
}

/* ─── Round 1: In-page modals for billing relationship creation and add-client ─── */

function closeBillingModal() {
  const overlay = document.getElementById("billingModalOverlay");
  if (overlay) {
    overlay.remove();
  }
  document.body.style.overflow = "";
  document.removeEventListener("keydown", billingModalTrapKeydown);
  billingWizardState.submitting = false;
}

function billingModalTrapKeydown(e) {
  if (e.key === "Escape") {
    e.preventDefault();
    const cancelBtn = document.getElementById("billingModalCancel");
    if (cancelBtn) cancelBtn.click();
    return;
  }
  if (e.key === "Tab") {
    const modal = document.getElementById("billingModal");
    if (!modal) return;
    const focusable = modal.querySelectorAll('input, button, select, [tabindex]:not([tabindex="-1"])');
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }
}


function openCreateRelationshipModal(returnContext, originatingBtn) {
  closeBillingModal();
  const overlay = document.createElement("div");
  overlay.id = "billingModalOverlay";
  overlay.className = "billing-modal-overlay";
  overlay.innerHTML = `
    <div class="billing-modal billing-wizard" id="billingModal" role="dialog" aria-modal="true" aria-labelledby="billingModalTitle">
      <h3 id="billingModalTitle">Set up who pays</h3>
      <p class="modal-instruction">Choose who should receive invoices and which clients they are paying for.</p>
      <div class="wizard-progress" id="wizardProgress">Step 1 of 3</div>
      <div class="wizard-body" id="wizardBody"></div>
      <div class="modal-error" id="billingModalError" role="alert"></div>
      <div class="modal-actions" id="wizardActions"></div>
    </div>
  `;
  document.body.appendChild(overlay);
  document.body.style.overflow = "hidden";

  const errorDisplay = document.getElementById("billingModalError");
  const actionsBox = document.getElementById("wizardActions");
  const bodyBox = document.getElementById("wizardBody");
  const progressBox = document.getElementById("wizardProgress");

  let step = 1;
  let payerType = null;
  let payerPerson = null;
  let payerOrg = null;
  let coveredClients = [];
  let useFuture = true;
  let saving = false;
  let setupResult = null;

  const fromReview = validReturnContext(returnContext);
  const ctxParticipants = (returnContext && Array.isArray(returnContext.participants)) ? returnContext.participants : [];

  async function suggestPayerFromContext() {
    if (!fromReview || !returnContext.billingPartyId) return;
    try {
      const bp = await api(`/api/billing-parties/${returnContext.billingPartyId}`);
      if (!bp || !bp.billing_party_id) return;
      if (bp.billing_party_type === "organization") {
        payerType = "organization";
        payerOrg = bp;
      } else if (bp.person_id) {
        const isParticipant = ctxParticipants.some(p => p.person_id === bp.person_id);
        payerType = isParticipant ? "client" : "person";
        const person = await api(`/api/people/${bp.person_id}`);
        if (person && person.person_id) payerPerson = person;
      }
    } catch (_) { /* inactive or missing — no suggestion */ }
  }

  const hasChanges = () => payerType || payerPerson || payerOrg || coveredClients.length > 0;

  function doCancel() {
    if (hasChanges()) {
      const confirmBox = document.getElementById("wizardConfirmCancel");
      if (!confirmBox) {
        const confirmEl = document.createElement("div");
        confirmEl.id = "wizardConfirmCancel";
        confirmEl.className = "wizard-confirm-cancel";
        confirmEl.innerHTML = `
          <p>You have unsaved selections. Are you sure you want to cancel?</p>
          <div class="wizard-confirm-actions">
            <button type="button" id="wizardCancelNo" class="modal-cancel">Keep editing</button>
            <button type="button" id="wizardCancelYes" class="modal-submit">Yes, cancel</button>
          </div>
        `;
        bodyBox.appendChild(confirmEl);
        document.getElementById("wizardCancelNo").onclick = () => { confirmEl.remove(); };
        document.getElementById("wizardCancelYes").onclick = () => { closeBillingModal(); if (originatingBtn) originatingBtn.focus(); };
      }
    } else {
      closeBillingModal();
      if (originatingBtn) originatingBtn.focus();
    }
  }

  function renderActions() {
    let html = `<button type="button" class="modal-cancel" id="billingModalCancel">Cancel</button>`;
    if (step === 1) {
      html += `<button type="button" class="modal-submit" id="wizardContinue" disabled>Continue</button>`;
    } else if (step === 2) {
      html += `<button type="button" class="modal-back" id="wizardBack">Back</button>`;
      html += `<button type="button" class="modal-submit" id="wizardContinue" disabled>Continue</button>`;
    } else if (step === 3) {
      html += `<button type="button" class="modal-back" id="wizardBack">Back</button>`;
      html += `<button type="button" class="modal-submit" id="wizardSave">Save Billing Relationship</button>`;
    }
    actionsBox.innerHTML = html;

    const cancelBtn = document.getElementById("billingModalCancel");
    cancelBtn.onclick = doCancel;

    if (step === 1 || step === 2) {
      const cont = document.getElementById("wizardContinue");
      if (cont) cont.onclick = () => { errorDisplay.textContent = ""; goNext(); };
    }
    if (step === 2 || step === 3) {
      const back = document.getElementById("wizardBack");
      if (back) back.onclick = () => { errorDisplay.textContent = ""; goBack(); };
    }
    if (step === 3) {
      const save = document.getElementById("wizardSave");
      if (save) save.onclick = doSave;
    }
  }

  function goNext() {
    if (step === 1) {
      if (!payerType) { errorDisplay.textContent = "Select who receives the invoice."; return; }
      if (payerType === "client" && !payerPerson) { errorDisplay.textContent = "Select a client."; return; }
      if (payerType === "person" && !payerPerson) { errorDisplay.textContent = "Select a person."; return; }
      if (payerType === "organization" && !payerOrg) { errorDisplay.textContent = "Select an organization."; return; }
      step = 2;
      renderStep();
    } else if (step === 2) {
      if (coveredClients.length === 0) { errorDisplay.textContent = "Select at least one client."; return; }
      step = 3;
      renderStep();
    }
  }

  function goBack() {
    if (step > 1) { step--; renderStep(); }
  }

  function renderStep() {
    progressBox.textContent = `Step ${step} of 3`;
    errorDisplay.textContent = "";
    if (step === 1) renderStep1();
    else if (step === 2) renderStep2();
    else if (step === 3) renderStep3();
    renderActions();
  }

  function renderStep1() {
    bodyBox.innerHTML = `
      <div class="wizard-payer-types" id="wizardPayerTypes">
        <div class="wizard-payer-choice ${payerType === "client" ? "selected" : ""}" data-type="client" tabindex="0" role="radio" aria-checked="${payerType === "client"}">
          <strong>A client</strong>
          <span class="help">Someone who attends sessions and pays for themselves or others</span>
        </div>
        <div class="wizard-payer-choice ${payerType === "person" ? "selected" : ""}" data-type="person" tabindex="0" role="radio" aria-checked="${payerType === "person"}">
          <strong>Another person</strong>
          <span class="help">Someone who receives invoices but does not need to attend sessions</span>
        </div>
        <div class="wizard-payer-choice ${payerType === "organization" ? "selected" : ""}" data-type="organization" tabindex="0" role="radio" aria-checked="${payerType === "organization"}">
          <strong>An organization</strong>
          <span class="help">A company or agency that receives invoices on behalf of clients</span>
        </div>
      </div>
      <div id="wizardPayerSearch" hidden></div>
      <div id="wizardPayerSelected" hidden></div>
    `;

    const choices = bodyBox.querySelectorAll(".wizard-payer-choice");
    choices.forEach(el => {
      const clickHandler = () => selectPayerType(el.dataset.type);
      el.addEventListener("click", clickHandler);
      el.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); clickHandler(); } });
    });

    if (payerType) showPayerSearch();
    updateContinueDisabled();
  }

  function selectPayerType(type) {
    if (payerType !== type) {
      const oldType = payerType;
      payerType = type;
      if (type === "organization") payerPerson = null;
      if (type !== "organization") payerOrg = null;
      if (oldType === "client" && type === "person") payerPerson = null;
      if (oldType === "person" && type === "client") payerPerson = null;
      coveredClients = [];
    }
    renderStep1();
  }

  function showPayerSearch() {
    const searchDiv = document.getElementById("wizardPayerSearch");
    const selectedDiv = document.getElementById("wizardPayerSelected");
    if (!payerType) { searchDiv.hidden = true; return; }
    searchDiv.hidden = false;

    if (payerType === "client" || payerType === "person") {
      const label = payerType === "client" ? "Search existing clients" : "Search existing people";
      const placeholder = payerType === "client" ? "Type a client name..." : "Type a person name...";
      const createLabel = payerType === "client" ? "Create new client" : "Create another person";
      searchDiv.innerHTML = `
        <div class="modal-search-wrap">
          <label for="wizardPayerInput">${escapeHtml(label)}</label>
          <input id="wizardPayerInput" class="modal-search" type="search" placeholder="${escapeHtml(placeholder)}" autocomplete="off">
        </div>
        <div class="modal-results" id="wizardPayerResults"></div>
        <div class="wizard-create-new">
          <button type="button" id="wizardCreateNewPerson" class="wizard-create-btn">${escapeHtml(createLabel)}</button>
        </div>
      `;
      const input = document.getElementById("wizardPayerInput");
      const results = document.getElementById("wizardPayerResults");
      const createBtn = document.getElementById("wizardCreateNewPerson");
      let searchRows = [];
      const doSearch = debounce(async (q) => {
        if (!q.trim()) { searchRows = []; results.innerHTML = ""; return; }
        try {
          searchRows = await api(`/api/people?q=${encodeURIComponent(q)}`);
          renderPayerResults(results, searchRows, "person");
        } catch (err) { errorDisplay.textContent = err.message || "Search failed."; }
      }, 200);
      input.addEventListener("input", (e) => doSearch(e.target.value));
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); doSearch(e.target.value); } });
      createBtn.addEventListener("click", () => showCreatePersonForm(payerType));
      if (payerPerson) showPayerSelected(selectedDiv, payerPerson.display_name, payerType);
      input.focus();
    } else if (payerType === "organization") {
      searchDiv.innerHTML = `
        <div class="modal-search-wrap">
          <label for="wizardPayerInput">Search existing organizations</label>
          <input id="wizardPayerInput" class="modal-search" type="search" placeholder="Type an organization name..." autocomplete="off">
        </div>
        <div class="modal-results" id="wizardPayerResults"></div>
        <div class="wizard-create-new">
          <button type="button" id="wizardCreateNewOrg" class="wizard-create-btn">Create new organization</button>
        </div>
      `;
      const input = document.getElementById("wizardPayerInput");
      const results = document.getElementById("wizardPayerResults");
      const createOrgBtn = document.getElementById("wizardCreateNewOrg");
      let searchRows = [];
      const doSearch = debounce(async (q) => {
        if (!q.trim()) { searchRows = []; results.innerHTML = ""; return; }
        try {
          searchRows = await api(`/api/organization-billing-parties?q=${encodeURIComponent(q)}`);
          renderPayerResults(results, searchRows, "organization");
        } catch (err) { errorDisplay.textContent = err.message || "Search failed."; }
      }, 200);
      input.addEventListener("input", (e) => doSearch(e.target.value));
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); doSearch(e.target.value); } });
      createOrgBtn.addEventListener("click", () => showCreateOrgForm());
      if (payerOrg) showPayerSelected(selectedDiv, payerOrg.billing_name || payerOrg.organization_name, "organization");
      input.focus();
    }
  }

  function renderPayerResults(container, rows, kind) {
    if (!rows.length) { container.innerHTML = '<div class="modal-empty">No results found. Try a different search.</div>'; return; }
    const selectedId = kind === "person" ? (payerPerson ? payerPerson.person_id : null) : (payerOrg ? payerOrg.billing_party_id : null);
    container.innerHTML = rows.map(row => {
      const id = kind === "person" ? row.person_id : row.billing_party_id;
      const name = kind === "person" ? (row.display_name || "Unnamed") : (row.organization_name || row.billing_name || "Unnamed");
      const sub = kind === "person" ? (row.person_code || "") : (row.billing_name || "");
      return `<div class="modal-result-row ${id === selectedId ? "selected" : ""}" data-id="${escapeHtml(id)}" tabindex="0" role="button">
        <span>${escapeHtml(name)}</span>
        ${sub ? `<span class="help">${escapeHtml(sub)}</span>` : ""}
      </div>`;
    }).join("");
    container.querySelectorAll(".modal-result-row").forEach(el => {
      const id = el.dataset.id;
      el.addEventListener("click", () => selectPayer(id, rows, kind));
      el.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); el.click(); } });
    });
  }

  function selectPayer(id, rows, kind) {
    if (kind === "person") {
      const person = rows.find(r => r.person_id === id);
      if (!person) return;
      payerPerson = person;
      showPayerSelected(document.getElementById("wizardPayerSelected"), person.display_name, payerType);
    } else {
      const org = rows.find(r => r.billing_party_id === id);
      if (!org) return;
      payerOrg = org;
      showPayerSelected(document.getElementById("wizardPayerSelected"), org.organization_name || org.billing_name, "organization");
    }
    updateContinueDisabled();
  }

  function showPayerSelected(container, name, kind) {
    container.hidden = false;
    const label = kind === "client" ? "Selected client" : kind === "organization" ? "Selected organization" : "Selected person";
    container.innerHTML = `${escapeHtml(label)}: <strong>${escapeHtml(name)}</strong>`;
  }

  function updateContinueDisabled() {
    const cont = document.getElementById("wizardContinue");
    if (!cont) return;
    let disabled = true;
    if (step === 1) {
      if (payerType === "client" || payerType === "person") disabled = !payerPerson;
      else if (payerType === "organization") disabled = !payerOrg;
      else disabled = true;
    } else if (step === 2) {
      disabled = coveredClients.length === 0;
    }
    cont.disabled = disabled;
  }

  function renderStep2() {
    bodyBox.innerHTML = `
      <h4 class="wizard-step-heading">Who are they paying for?</h4>
      <p class="modal-instruction">Select the clients whose sessions this payer should cover.</p>
      <div class="modal-search-wrap">
        <label for="wizardCoveredSearch">Search clients to add</label>
        <input id="wizardCoveredSearch" class="modal-search" type="search" placeholder="Type a client name..." autocomplete="off">
      </div>
      <div class="modal-results" id="wizardCoveredResults"></div>
      <div class="wizard-create-new">
        <button type="button" id="wizardCoveredCreateNew" class="wizard-create-btn">Create new client</button>
      </div>
      <div class="wizard-covered-selected" id="wizardCoveredSelected"></div>
    `;
    renderCoveredChips();
    const input = document.getElementById("wizardCoveredSearch");
    const results = document.getElementById("wizardCoveredResults");
    const createBtn = document.getElementById("wizardCoveredCreateNew");
    let searchRows = [];
    const selectedIds = new Set(coveredClients.map(c => c.person_id));
    const doSearch = debounce(async (q) => {
      if (!q.trim()) { searchRows = []; results.innerHTML = ""; return; }
      try {
        searchRows = await api(`/api/people?q=${encodeURIComponent(q)}`);
        renderCoveredResults(results, searchRows, selectedIds);
      } catch (err) { errorDisplay.textContent = err.message || "Search failed."; }
    }, 200);
    input.addEventListener("input", (e) => doSearch(e.target.value));
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); doSearch(e.target.value); } });
    createBtn.addEventListener("click", () => showCreatePersonForm("client", true));
    input.focus();
    updateContinueDisabled();
  }

  function renderCoveredResults(container, rows, selectedIds) {
    const available = rows.filter(row => !selectedIds.has(row.person_id));
    if (!available.length) { container.innerHTML = '<div class="modal-empty">No clients found. Try a different search.</div>'; return; }
    container.innerHTML = available.map(row => {
      return `<div class="modal-result-row" data-person-id="${escapeHtml(row.person_id)}" tabindex="0" role="button">
        <span>${escapeHtml(row.display_name || "Unnamed client")}</span>
        ${row.person_code ? `<span class="help">${escapeHtml(row.person_code)}</span>` : ""}
      </div>`;
    }).join("");
    container.querySelectorAll(".modal-result-row").forEach(el => {
      const pid = el.dataset.personId;
      el.addEventListener("click", () => addCoveredClient(pid, available));
      el.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); el.click(); } });
    });
  }

  function addCoveredClient(personId, rows) {
    if (coveredClients.some(c => c.person_id === personId)) return;
    const person = rows.find(r => r.person_id === personId);
    if (!person) return;
    coveredClients.push({ person_id: person.person_id, display_name: person.display_name });
    renderCoveredChips();
    renderStep2();
  }

  function removeCoveredClient(personId) {
    coveredClients = coveredClients.filter(c => c.person_id !== personId);
    renderCoveredChips();
    renderStep2();
  }

  function renderCoveredChips() {
    const container = document.getElementById("wizardCoveredSelected");
    if (!container) return;
    if (!coveredClients.length) {
      container.innerHTML = '<div class="modal-empty">No clients selected yet.</div>';
      return;
    }
    container.innerHTML = `<div class="wizard-chips">${coveredClients.map(c => `
      <span class="wizard-chip" data-person-id="${escapeHtml(c.person_id)}">
        ${escapeHtml(c.display_name)}
        <button type="button" class="wizard-chip-remove" aria-label="Remove ${escapeHtml(c.display_name)}">&times;</button>
      </span>`).join("")}</div>`;
    container.querySelectorAll(".wizard-chip-remove").forEach(btn => {
      btn.onclick = () => removeCoveredClient(btn.parentElement.dataset.personId);
    });
  }

  function showCreatePersonForm(formPayerType, isStep2 = false) {
    const heading = formPayerType === "client" ? "Create new client" : "Create another person";
    const instruction = formPayerType === "client"
      ? "Add a new client record. They will also be available as a session participant."
      : "Add the person who should receive invoices. They will not be added as a session participant unless selected separately under Pays for.";
    const submitLabel = formPayerType === "client" ? "Create Client" : "Create Person";

    const formDiv = document.createElement("div");
    formDiv.id = "wizardCreatePersonForm";
    formDiv.className = "wizard-child-form";
    formDiv.innerHTML = `
      <h4 class="wizard-step-heading">${escapeHtml(heading)}</h4>
      <p class="modal-instruction">${escapeHtml(instruction)}</p>
      <div class="wizard-form-grid">
        <label class="field">First name <span class="req">*</span><input id="wizardNewFirst" type="text" autocomplete="off"></label>
        <label class="field">Last name <span class="req">*</span><input id="wizardNewLast" type="text" autocomplete="off"></label>
        <label class="field">Preferred name <input id="wizardNewPreferred" type="text" autocomplete="off"></label>
        <label class="field">Billing email <input id="wizardNewEmail" type="email" autocomplete="off"></label>
        <label class="field">Billing phone <input id="wizardNewPhone" type="tel" autocomplete="off"></label>
        <label class="field wide">Administrative notes <input id="wizardNewNotes" type="text" autocomplete="off"></label>
      </div>
      <div class="wizard-form-error" id="wizardFormError" role="alert"></div>
      <div class="wizard-form-duplicate" id="wizardFormDuplicate" hidden></div>
      <div class="wizard-form-actions">
        <button type="button" id="wizardFormBack" class="modal-back">Back to search</button>
        <button type="button" id="wizardFormCancel" class="modal-cancel">Cancel</button>
        <button type="button" id="wizardFormSubmit" class="modal-submit">${escapeHtml(submitLabel)}</button>
      </div>
    `;

    const searchDiv = document.getElementById("wizardPayerSearch") || document.getElementById("wizardCoveredResults");
    const parentResults = isStep2 ? document.getElementById("wizardCoveredResults") : document.getElementById("wizardPayerResults");
    const parentSearchWrap = isStep2 ? bodyBox.querySelector(".modal-search-wrap") : document.getElementById("wizardPayerSearch");
    if (parentSearchWrap) parentSearchWrap.style.display = "none";
    if (parentResults) parentResults.style.display = "none";
    const createBtn = isStep2 ? document.getElementById("wizardCoveredCreateNew") : document.getElementById("wizardCreateNewPerson");
    if (createBtn) createBtn.style.display = "none";

    bodyBox.appendChild(formDiv);

    const firstInput = document.getElementById("wizardNewFirst");
    const lastInput = document.getElementById("wizardNewLast");
    const preferredInput = document.getElementById("wizardNewPreferred");
    const emailInput = document.getElementById("wizardNewEmail");
    const phoneInput = document.getElementById("wizardNewPhone");
    const notesInput = document.getElementById("wizardNewNotes");
    const formError = document.getElementById("wizardFormError");
    const dupBox = document.getElementById("wizardFormDuplicate");
    const submitBtn = document.getElementById("wizardFormSubmit");
    const backBtn = document.getElementById("wizardFormBack");
    const cancelBtn = document.getElementById("wizardFormCancel");

    let creating = false;

    function closeForm() {
      formDiv.remove();
      if (parentSearchWrap) parentSearchWrap.style.display = "";
      if (parentResults) parentResults.style.display = "";
      if (createBtn) createBtn.style.display = "";
      const input = isStep2 ? document.getElementById("wizardCoveredSearch") : document.getElementById("wizardPayerInput");
      if (input) input.focus();
    }

    backBtn.addEventListener("click", closeForm);
    cancelBtn.addEventListener("click", () => { closeForm(); doCancel(); });

    async function doCreate() {
      if (creating) return;
      formError.textContent = "";
      dupBox.hidden = true;
      dupBox.innerHTML = "";

      const first = firstInput.value.trim();
      const last = lastInput.value.trim();
      if (!first) { formError.textContent = "First name is required."; firstInput.focus(); return; }
      if (!last) { formError.textContent = "Last name is required."; lastInput.focus(); return; }

      creating = true;
      submitBtn.disabled = true;
      submitBtn.textContent = "Creating…";

      const display_name = `${first} ${last}`.trim();
      const payload = {
        first_name: first,
        last_name: last,
        display_name,
        preferred_name: preferredInput.value.trim() || null,
        billing_email: emailInput.value.trim() || null,
        billing_phone: phoneInput.value.trim() || null,
        administrative_notes: notesInput.value.trim() || null,
      };

      try {
        const person = await api("/api/people", { method: "POST", body: JSON.stringify(payload) });

        if (person.existing && !person.created) {
          creating = false;
          submitBtn.disabled = false;
          submitBtn.textContent = submitLabel;
          dupBox.hidden = false;
          dupBox.innerHTML = `
            <p>A person with this name already exists.</p>
            <div class="wizard-duplicate-info">
              <strong>${escapeHtml(person.display_name)}</strong>
              ${person.person_code ? `<span class="help">${escapeHtml(person.person_code)}</span>` : ""}
            </div>
            <div class="wizard-duplicate-actions">
              <button type="button" id="wizardUseExistingPerson" class="modal-submit">Use existing person</button>
              <button type="button" id="wizardEditAgain" class="modal-back">Go back and edit</button>
            </div>
          `;
          document.getElementById("wizardUseExistingPerson").onclick = () => {
            handlePersonCreated(person, formPayerType, isStep2);
            closeForm();
          };
          document.getElementById("wizardEditAgain").onclick = () => {
            dupBox.hidden = true;
            dupBox.innerHTML = "";
            firstInput.focus();
          };
          return;
        }

        handlePersonCreated(person, formPayerType, isStep2);
        closeForm();
      } catch (err) {
        creating = false;
        submitBtn.disabled = false;
        submitBtn.textContent = submitLabel;
        formError.textContent = (err && err.message) || "Failed to create person.";
      }
    }

    submitBtn.addEventListener("click", doCreate);
    firstInput.focus();
  }

  function handlePersonCreated(person, formPayerType, isStep2) {
    if (isStep2) {
      if (!coveredClients.some(c => c.person_id === person.person_id)) {
        coveredClients.push({ person_id: person.person_id, display_name: person.display_name });
      }
      renderStep2();
    } else {
      if (formPayerType === "client" && payerType === "client") {
        payerPerson = person;
        renderStep1();
        showPayerSearch();
        showPayerSelected(document.getElementById("wizardPayerSelected"), person.display_name, "client");
        updateContinueDisabled();
      } else if (formPayerType === "person" && payerType === "person") {
        payerPerson = person;
        renderStep1();
        showPayerSearch();
        showPayerSelected(document.getElementById("wizardPayerSelected"), person.display_name, "person");
        updateContinueDisabled();
      } else {
        renderStep1();
        showPayerSearch();
        if (payerPerson) {
          showPayerSelected(document.getElementById("wizardPayerSelected"), payerPerson.display_name, payerType);
        }
        updateContinueDisabled();
      }
    }
  }

  function showCreateOrgForm() {
    const formDiv = document.createElement("div");
    formDiv.id = "wizardCreateOrgForm";
    formDiv.className = "wizard-child-form";
    formDiv.innerHTML = `
      <h4 class="wizard-step-heading">Create new organization</h4>
      <p class="modal-instruction">Add the organization that should receive invoices.</p>
      <div class="wizard-form-grid">
        <label class="field wide">Organization name <span class="req">*</span><input id="wizardOrgName" type="text" autocomplete="off"></label>
        <label class="field wide">Billing contact name <input id="wizardOrgBillingName" type="text" autocomplete="off"></label>
        <label class="field">Billing email <input id="wizardOrgEmail" type="email" autocomplete="off"></label>
        <label class="field">Billing phone <input id="wizardOrgPhone" type="tel" autocomplete="off"></label>
        <label class="field">Address line 1 <input id="wizardOrgAddr1" type="text" autocomplete="off"></label>
        <label class="field">Address line 2 <input id="wizardOrgAddr2" type="text" autocomplete="off"></label>
        <label class="field">City <input id="wizardOrgCity" type="text" autocomplete="off"></label>
        <label class="field">State <input id="wizardOrgState" type="text" autocomplete="off"></label>
        <label class="field">Postal code <input id="wizardOrgPostal" type="text" autocomplete="off"></label>
        <label class="field">Preferred delivery method
          <select id="wizardOrgDelivery">
            <option value="unresolved">Unresolved</option>
            <option value="email">Email</option>
            <option value="mail">Mail</option>
            <option value="both">Both</option>
          </select>
        </label>
        <label class="field wide">Administrative notes <input id="wizardOrgNotes" type="text" autocomplete="off"></label>
      </div>
      <div class="wizard-form-error" id="wizardOrgFormError" role="alert"></div>
      <div class="wizard-form-duplicate" id="wizardOrgFormDuplicate" hidden></div>
      <div class="wizard-form-actions">
        <button type="button" id="wizardOrgFormBack" class="modal-back">Back to search</button>
        <button type="button" id="wizardOrgFormCancel" class="modal-cancel">Cancel</button>
        <button type="button" id="wizardOrgFormSubmit" class="modal-submit">Create Organization</button>
      </div>
    `;

    const searchDiv = document.getElementById("wizardPayerSearch");
    const createBtn = document.getElementById("wizardCreateNewOrg");
    if (searchDiv) searchDiv.style.display = "none";
    if (createBtn) createBtn.style.display = "none";

    bodyBox.appendChild(formDiv);

    const nameInput = document.getElementById("wizardOrgName");
    const billingNameInput = document.getElementById("wizardOrgBillingName");
    const emailInput = document.getElementById("wizardOrgEmail");
    const phoneInput = document.getElementById("wizardOrgPhone");
    const addr1Input = document.getElementById("wizardOrgAddr1");
    const addr2Input = document.getElementById("wizardOrgAddr2");
    const cityInput = document.getElementById("wizardOrgCity");
    const stateInput = document.getElementById("wizardOrgState");
    const postalInput = document.getElementById("wizardOrgPostal");
    const deliveryInput = document.getElementById("wizardOrgDelivery");
    const notesInput = document.getElementById("wizardOrgNotes");
    const formError = document.getElementById("wizardOrgFormError");
    const dupBox = document.getElementById("wizardOrgFormDuplicate");
    const submitBtn = document.getElementById("wizardOrgFormSubmit");
    const backBtn = document.getElementById("wizardOrgFormBack");
    const cancelBtn = document.getElementById("wizardOrgFormCancel");

    let creating = false;

    function closeOrgForm() {
      formDiv.remove();
      if (searchDiv) searchDiv.style.display = "";
      if (createBtn) createBtn.style.display = "";
      const input = document.getElementById("wizardPayerInput");
      if (input) input.focus();
    }

    backBtn.addEventListener("click", closeOrgForm);
    cancelBtn.addEventListener("click", () => { closeOrgForm(); doCancel(); });

    async function doCreateOrg() {
      if (creating) return;
      formError.textContent = "";
      dupBox.hidden = true;
      dupBox.innerHTML = "";

      const orgName = nameInput.value.trim();
      if (!orgName) { formError.textContent = "Organization name is required."; nameInput.focus(); return; }

      creating = true;
      submitBtn.disabled = true;
      submitBtn.textContent = "Creating…";

      const billingName = billingNameInput.value.trim() || orgName;
      const payload = {
        billing_party_type: "organization",
        organization_name: orgName,
        billing_name: billingName,
        billing_email: emailInput.value.trim() || null,
        billing_phone: phoneInput.value.trim() || null,
        billing_address_line_1: addr1Input.value.trim() || null,
        billing_address_line_2: addr2Input.value.trim() || null,
        billing_city: cityInput.value.trim() || null,
        billing_state: stateInput.value.trim() || null,
        billing_postal_code: postalInput.value.trim() || null,
        preferred_delivery_method: deliveryInput.value,
        administrative_notes: notesInput.value.trim() || null,
      };

      try {
        const org = await api("/api/billing-parties", { method: "POST", body: JSON.stringify(payload) });

        if (org.existing && !org.created) {
          creating = false;
          submitBtn.disabled = false;
          submitBtn.textContent = "Create Organization";
          dupBox.hidden = false;
          dupBox.innerHTML = `
            <p>An organization with this name already exists.</p>
            <div class="wizard-duplicate-info">
              <strong>${escapeHtml(org.organization_name || org.billing_name)}</strong>
              ${org.billing_email ? `<span class="help">${escapeHtml(org.billing_email)}</span>` : ""}
              ${org.billing_phone ? `<span class="help">${escapeHtml(org.billing_phone)}</span>` : ""}
            </div>
            <div class="wizard-duplicate-actions">
              <button type="button" id="wizardOrgUseExisting" class="modal-submit">Use existing organization</button>
              <button type="button" id="wizardOrgEditAgain" class="modal-back">Go back and edit</button>
            </div>
          `;
          document.getElementById("wizardOrgUseExisting").onclick = () => {
            handleOrgCreated(org);
            closeOrgForm();
          };
          document.getElementById("wizardOrgEditAgain").onclick = () => {
            dupBox.hidden = true;
            dupBox.innerHTML = "";
            nameInput.focus();
          };
          return;
        }

        handleOrgCreated(org);
        closeOrgForm();
      } catch (err) {
        creating = false;
        submitBtn.disabled = false;
        submitBtn.textContent = "Create Organization";
        formError.textContent = (err && err.message) || "Failed to create organization.";
      }
    }

    submitBtn.addEventListener("click", doCreateOrg);
    nameInput.focus();
  }

  function handleOrgCreated(org) {
    payerOrg = org;
    renderStep1();
    showPayerSearch();
    showPayerSelected(
      document.getElementById("wizardPayerSelected"),
      org.organization_name || org.billing_name,
      "organization"
    );
    updateContinueDisabled();
  }

  function renderStep3() {
    const recipientName = payerType === "organization"
      ? (payerOrg ? (payerOrg.organization_name || payerOrg.billing_name) : "")
      : (payerPerson ? payerPerson.display_name : "");
    const coveredNames = coveredClients.map(c => escapeHtml(c.display_name)).join(", ");
    bodyBox.innerHTML = `
      <h4 class="wizard-step-heading">Review billing relationship</h4>
      <div class="wizard-review-section">
        <div class="wizard-review-label">Invoice recipient</div>
        <div class="wizard-review-value">${escapeHtml(recipientName)}</div>
      </div>
      <div class="wizard-review-section">
        <div class="wizard-review-label">Pays for</div>
        <div class="wizard-review-value">${coveredNames || "None"}</div>
      </div>
      <div class="wizard-review-section">
        <label class="wizard-checkbox-label">
          <input type="checkbox" id="wizardFutureSessions" ${useFuture ? "checked" : ""}>
          Use this billing relationship for future sessions involving these clients
        </label>
      </div>
    `;
    const futureCb = document.getElementById("wizardFutureSessions");
    if (futureCb) futureCb.addEventListener("change", () => { useFuture = futureCb.checked; });
    renderActions();
  }

  async function doSave() {
    if (saving) return;
    const saveBtn = document.getElementById("wizardSave");
    if (!saveBtn) return;
    saving = true;
    billingWizardState.submitting = true;
    saveBtn.disabled = true;
    saveBtn.textContent = "Saving relationship…";
    errorDisplay.textContent = "";

    const payload = {
      payer_kind: payerType,
      covered_client_ids: coveredClients.map(c => c.person_id),
      use_for_future_sessions: useFuture,
    };
    if (payerType === "client" || payerType === "person") {
      payload.payer_person_id = payerPerson.person_id;
    } else if (payerType === "organization") {
      payload.organization_billing_party_id = payerOrg.billing_party_id;
    }

    try {
      const res = await fetch("/api/billing-relationships/setup", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Jordana-Write-Token": getWriteToken(),
        },
        body: JSON.stringify(payload),
      });
      const json = await res.json();
      if (!res.ok || json.ok === false) throw json;

      setupResult = json;
      const accountId = json.account_id;
      const billingPartyId = json.billing_party_id;

      if (fromReview) {
        saveBtn.textContent = "Attaching to session…";
        await attachToSession(accountId, billingPartyId, saveBtn);
      } else {
        let nextContext = returnContext;
        if (validReturnContext(returnContext)) {
          nextContext = persistReturnContext({ ...returnContext, accountId });
          location.hash = returnContextHash(nextContext);
        }
        closeBillingModal();
        await loadClients();
        await openAccountRecord(accountId, { returnContext: nextContext });
      }
    } catch (err) {
      saving = false;
      billingWizardState.submitting = false;
      saveBtn.disabled = false;
      saveBtn.textContent = "Save Billing Relationship";
      if (err && (err.duplicate || (err.created === false))) {
        const existingAccountId = err.account_id;
        const existingBillingPartyId = err.billing_party_id;
        if (fromReview) {
          errorDisplay.innerHTML = `This billing relationship already exists. <button type="button" id="wizardUseExisting" class="modal-link-btn">Use this billing relationship</button> <button type="button" id="wizardOpenExisting" class="modal-link-btn">Open existing relationship</button>`;
          const useBtn = document.getElementById("wizardUseExisting");
          if (useBtn) {
            useBtn.addEventListener("click", async () => {
              saving = true;
              billingWizardState.submitting = true;
              saveBtn.disabled = true;
              saveBtn.textContent = "Attaching to session…";
              errorDisplay.textContent = "";
              await attachToSession(existingAccountId, existingBillingPartyId, saveBtn);
            });
          }
        } else {
          errorDisplay.innerHTML = `This billing relationship already exists. <button type="button" id="wizardOpenExisting" class="modal-link-btn">Open existing relationship</button>`;
        }
        const openBtn = document.getElementById("wizardOpenExisting");
        if (openBtn) {
          openBtn.addEventListener("click", async () => {
            let nextContext = returnContext;
            if (validReturnContext(returnContext)) {
              nextContext = persistReturnContext({ ...returnContext, accountId: existingAccountId });
              location.hash = returnContextHash(nextContext);
            }
            closeBillingModal();
            await loadClients();
            await openAccountRecord(existingAccountId, { returnContext: nextContext });
          });
        }
      } else {
        errorDisplay.textContent = (err && err.error) || (err && err.message) || "Failed to save billing relationship.";
      }
    }
  }

  async function attachToSession(accountId, billingPartyId, saveBtn) {
    const attachPayload = {
      participants: ctxParticipants.map(p => ({
        person_id: p.person_id,
        display_name: p.display_name || "",
        is_primary: !!p.is_primary,
        relationship_role: p.relationship_role || "",
      })),
      account_id: accountId,
      primary_person_id: ctxParticipants.find(p => p.is_primary)?.person_id || ctxParticipants[0]?.person_id || null,
      billing_party_id: billingPartyId,
      default_billing_party_id: billingPartyId,
    };

    try {
      await api(`/api/review/candidates/${returnContext.candidateId}/save-relationship`, {
        method: "POST",
        body: JSON.stringify(attachPayload),
      });

      closeBillingModal();
      clearReturnContext();
      await showReviewWorkbench();
      await selectCandidate(returnContext.candidateId);

      const banner = document.createElement("div");
      banner.className = "relationship-summary success";
      banner.id = "wizardAttachSuccess";
      banner.innerHTML = "<strong>Billing relationship saved for this session.</strong>";
      const overlayContent = document.getElementById("reviewOverlayContent");
      if (overlayContent) overlayContent.prepend(banner);
      setTimeout(() => { if (banner) banner.remove(); }, 5000);

      saving = false;
      billingWizardState.submitting = false;
    } catch (attachErr) {
      saving = false;
      billingWizardState.submitting = false;
      saveBtn.disabled = false;
      saveBtn.textContent = "Save Billing Relationship";
      errorDisplay.innerHTML = `
        The billing relationship was saved, but it could not be attached to this session.
        <div class="wizard-recovery-actions">
          <button type="button" id="wizardRetryAttach" class="modal-submit">Try attaching again</button>
          <button type="button" id="wizardOpenRelFromRecovery" class="modal-link-btn">Open billing relationship</button>
          <button type="button" id="wizardReturnNoAttach" class="modal-cancel">Return to review without attaching</button>
        </div>
      `;
      const retryBtn = document.getElementById("wizardRetryAttach");
      if (retryBtn) {
        retryBtn.addEventListener("click", async () => {
          saving = true;
          billingWizardState.submitting = true;
          saveBtn.disabled = true;
          saveBtn.textContent = "Attaching to session…";
          errorDisplay.textContent = "";
          await attachToSession(accountId, billingPartyId, saveBtn);
        });
      }
      const openRelBtn = document.getElementById("wizardOpenRelFromRecovery");
      if (openRelBtn) {
        openRelBtn.addEventListener("click", async () => {
          let nextContext = returnContext;
          if (validReturnContext(returnContext)) {
            nextContext = persistReturnContext({ ...returnContext, accountId });
            location.hash = returnContextHash(nextContext);
          }
          closeBillingModal();
          await loadClients();
          await openAccountRecord(accountId, { returnContext: nextContext });
        });
      }
      const returnBtn = document.getElementById("wizardReturnNoAttach");
      if (returnBtn) {
        returnBtn.addEventListener("click", async () => {
          closeBillingModal();
          await showReviewWorkbench();
          await selectCandidate(returnContext.candidateId);
        });
      }
    }
  }

  overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) doCancel(); });
  document.addEventListener("keydown", billingModalTrapKeydown);
  (async () => {
    await suggestPayerFromContext();
    renderStep();
  })();
}
