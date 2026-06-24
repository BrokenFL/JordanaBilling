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
  logo_path: "",
  logo_contains_business_details: false,
  show_email_below_logo: false,
  invoice_total_label: "TOTAL DUE",
  invoice_number_format: "YYYY-NNNN"
};

const $ = (id) => document.getElementById(id);
const fmt = (v) => v || "-";
const money = (v) => v ? `$${v}` : "—";
const fmtDateTime = (v) => v ? new Date(v).toLocaleString([], { month:"short", day:"numeric", hour:"numeric", minute:"2-digit" }) : "-";
const billingTypeLabel = (v, customDescription = "") => {
  if (v === "custom" && customDescription) return customDescription;
  return ({psychotherapy:"Psychotherapy Session", psychotherapy_house_call:"Psychotherapy Session / House Call", psychotherapy_weekend:"Psychotherapy Session / Weekend", psychotherapy_evening:"Psychotherapy Session / Evening", custom:"Custom"}[v] || v || "Psychotherapy Session");
};
const appointmentStatusRuleLabel = (v) => ({scheduled:"Scheduled", cancelled:"Cancelled", no_show:"No-Show"}[v] || v || "Scheduled");
const userFacingSessionLabel = (billingType, appointmentStatus = "", customDescription = "") => {
  const specialBase = {
    psychotherapy: "Psychotherapy Session",
    psychotherapy_house_call: "House Call Psychotherapy Session",
    psychotherapy_weekend: "Weekend Psychotherapy Session",
    psychotherapy_evening: "Evening Psychotherapy Session",
    custom: customDescription || "Custom"
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
  if (v === "custom" && customDescription) return customDescription;
  return ({psychotherapy:"Standard", psychotherapy_house_call:"House Call", psychotherapy_weekend:"Weekend", psychotherapy_evening:"Evening", custom:"Custom"}[v] || v || "Standard");
};
const appointmentMethodLabel = (v) => ({phone:"Phone", facetime:"FaceTime", office:"Office", unknown:"Unknown"}[v] || v || "Unknown");
const serviceLabel = (v) => ({phone:"Phone", facetime:"FaceTime", office:"Office", house_call:"House Call", unknown:"Unknown"}[v] || v || "Unknown");
const timeLabel = (v) => ({standard:"Standard", evening:"Evening", weekend:"Weekend", weekend_evening:"Weekend + Evening"}[v] || v || "Standard");
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

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const json = await res.json();
  if (!res.ok || json.ok === false) throw new Error(json.error || "Request failed");
  return json;
}

async function loadList() {
  const params = new URLSearchParams({
    q: $("searchBox").value,
    review_status: $("statusFilter").value,
    billing_session_type: $("serviceFilter").value,
    time_category: $("timeFilter").value,
    calendar_filter: $("calendarFilter").value,
    limit: state.limit,
    offset: state.offset
  });
  const data = await api(`/api/review/candidates?${params}`);
  state.items = data.items;
  renderStatus(data.status);
  renderRows(data.items, data.total);
  if (!state.selected && data.items.length) selectCandidate(data.items[0].candidate_id);
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
  $("candidateRows").innerHTML = items.map(item => `
    <tr data-id="${item.candidate_id}" class="${state.selected === item.candidate_id ? "selected" : ""}">
      <td><span class="dot ${statusColor(item.status, item.classification)}"></span>${calendarBadge(item)}</td>
      <td>${fmt(item.date)}</td>
      <td>${fmt(item.time)}</td>
      <td>${fmt(item.raw_title)}</td>
      <td><span class="primary">${fmt(item.suggested_client)}</span></td>
      <td>${fmt(item.duration_minutes)}</td>
      <td>${userFacingSessionLabel(item.billing_session_type || item.service_mode, item.appointment_status, item.custom_service_description || "")}</td>
      <td>${timeLabel(item.time_category)}</td>
      <td>${money(item.rate)}</td>
      <td><span class="confidence ${item.authority_score >= 60 ? "good" : "low"}">${item.authority_score || 0}%</span></td>
    </tr>
  `).join("");
  document.querySelectorAll("#candidateRows tr").forEach(row => row.addEventListener("click", () => selectCandidate(row.dataset.id)));
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
  $("inspector").innerHTML = `
    <div class="inspector-header">
      <div>
        <h2>${fmt(s.raw_calendar_title || s.title)}</h2>
        <div class="meta"><span>${fmt(s.session_date)}</span><span>${fmt(startRange(s))}</span><span>${fmt(s.duration_minutes)} min</span><span>${calendarLabel(s)}</span><span>${appointmentBadge(s.appointment_status)}</span></div>
      </div>
      <div><span class="badge">${fmt(s.review_status).replaceAll("_", " ")}</span><div class="confidence ${s.authority_score >= 60 ? "good" : "low"}">Review confidence: ${s.authority_score || 0}%</div><div class="help">${(s.authority_reasons || []).join(", ")}</div></div>
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
        <label>Parsed Title Time</label><span>${fmt(s.title_time_text)} ${s.title_time_normalized ? `(${s.title_time_normalized})` : ""}</span>
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
          ? `<div class="relationship-summary success"><strong>Confirmed</strong><div>${userFacingSessionLabel(s.billing_session_type || mapLegacyToType(s), s.appointment_status, s.custom_service_description || "")} • ${fmt(s.custom_duration_minutes || s.approved_duration_minutes || s.duration_minutes)} min • ${timeLabel(s.time_category)} • ${money(currentRate)} • ${fmt(s.payment_status)}</div></div>
             <div class="inline-actions"><button id="changeSessionBtn">Change</button></div>`
          : `<div class="field-grid">
               <label class="field">Session Type<select id="billingTypeInput">${billingTypeOptions(s.billing_session_type || mapLegacyToType(s))}</select></label>
               <label class="field">Duration<select id="durationChoiceInput">${durationOptions(s.duration_choice || durationToChoice(s.approved_duration_minutes || s.duration_minutes))}</select></label>
               <label class="field" id="customDurationField" ${(s.duration_choice === "custom" || !["30","60","90","120"].includes(String(s.approved_duration_minutes || s.duration_minutes))) ? "" : "hidden"}>Custom Minutes<input id="customDurationInput" type="number" min="1" value="${s.custom_duration_minutes || s.approved_duration_minutes || s.duration_minutes || ""}"></label>
               <label class="field" id="customDescField" ${s.billing_session_type === "custom" ? "" : "hidden"}>Custom Description<input id="customDescInput" value="${s.custom_service_description || ""}"></label>
               <label class="field" id="customCodeField" ${s.billing_session_type === "custom" ? "" : "hidden"}>Custom Code<input id="customCodeInput" value="${s.custom_service_code || ""}"></label>
               <label class="field">Time Category<select id="timeCategoryInput">${optionSet(["standard","evening","weekend","weekend_evening"], s.time_category)}</select></label>
               <label class="field">Rate for this session<input id="approvedRateInput" value="${currentRate}"><span class="help" id="sessionRateHelp">${rateSourceDescription(s, data.participants)}</span><span class="help" id="sessionRatePreview"></span></label>
               <label class="field">Payment Status<select id="paymentInput">${optionSet(["unresolved","unpaid","partially_paid","paid","waived","not_billable"], s.payment_status)}</select></label>
               ${showCancellation ? `<label class="field">Cancellation/No-Show Billing<select id="billingTreatmentInput">${optionSet(["unresolved","billable","not_billable","waived"], s.billing_treatment || "billable")}</select></label>` : ""}
               <details class="field wide"><summary>Advanced</summary><div class="field-grid"><label class="field">Appointment Method<span class="readonly-value">${appointmentMethodLabel(s.appointment_method || s.service_mode)}</span></label><label class="field">Billable Status<select id="billableInput">${optionSet(["proposed","approved","excluded","nonbillable"], s.billable_status || "proposed")}</select></label></div></details>
               ${rateChanged ? `<label class="field wide">Override Reason<input id="overrideReasonInput" value="${s.rate_override_reason || ""}"></label>` : ""}
             </div>
             ${houseCallSuggestion(s)}
             ${rateChanged ? `<div class="rate-scope" id="rateScope">
               <strong>Apply this rate to:</strong>
               <label><input type="radio" name="rateScope" value="session_only" checked> This session only</label>
               <label><input type="radio" name="rateScope" value="future_person"> Future sessions for this client</label>
               <select id="rateScopePerson">${state.participants.map(p => `<option value="${p.person_id || ""}">${p.display_name || p.participant_name || ""}</option>`).join("")}</select>
               <label><input type="radio" name="rateScope" value="future_joint" ${state.participants.length < 2 ? "disabled" : ""}> Future joint sessions for these clients</label>
             </div>` : ""}
             <div class="inline-actions">${showSessionSave ? '<button id="saveSessionBtn" class="save">Save Session Draft</button>' : ""}</div>`}
    </section>

    <section class="section">
      <details>
        <summary class="section-summary">Shared billing and relationships</summary>
        <div id="relationshipEditor" class="drawer"></div>
      </details>
      <div class="hint">Suggestion reasons: ${(s.authority_reasons || []).join(", ") || safeList(s.review_reasons).join(" ") || s.explanation || "Calendar title matched the parser pattern."}</div>
    </section>

    <section class="section">
      <h3>Review Checklist</h3>
      <div class="checklist">${data.checklist.map(c => `<div class="check ${c.resolved ? "done" : ""}"><span></span><label>${c.label}</label></div>`).join("")}</div>
    </section>

    <div class="actions">
      ${isSession && readiness.all_ready ? '<button class="approve" id="approveBtn">Approve Session</button>' : ""}
      ${!isSession ? '<button class="approve" id="sendToReviewBtn">Send to Review</button>' : ""}
      <button id="personalBtn">Mark Personal/Admin</button>
      <button id="duplicateBtn">Mark Duplicate</button>
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
  if ($("duplicateBtn")) $("duplicateBtn").onclick = () => mark("duplicate");
  if ($("excludeBtn")) $("excludeBtn").onclick = () => mark("nonbillable");
  if ($("sendToReviewBtn")) $("sendToReviewBtn").onclick = sendToReview;
  [
    "billingTypeInput",
    "durationChoiceInput",
    "customDurationInput",
    "customDescInput",
    "customCodeInput",
    "timeCategoryInput",
    "approvedRateInput",
    "paymentInput",
    "billingTreatmentInput",
    "billableInput",
    "overrideReasonInput"
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

function renderParticipantChips() {
  const chips = $("participantChips");
  if (!chips) return;
  chips.innerHTML = state.participants.map((p, i) => `<span class="chip ${p.is_proposed ? "proposed" : "linked"}">${p.display_name || p.participant_name}${p.is_proposed ? '<small>proposed</small>' : ''}<button data-edit="${i}">Edit</button><button data-i="${i}">×</button></span>`).join("");
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
      return `<option value="${p.person_id}" ${selected}>${fmt(name)}</option>`;
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

function payerDisplayOptions(members = [], returnContext = null) {
  const seen = new Set();
  const options = [];
  const add = (personId, displayName) => {
    if (!personId || seen.has(personId)) return;
    seen.add(personId);
    options.push({ person_id: personId, display_name: displayName || "Unnamed client" });
  };
  members.forEach(member => add(member.person_id, member.display_name));
  (returnContext?.participants || []).forEach(participant => add(participant.person_id, participant.display_name));
  return options;
}

function relationshipNameSuggestion(returnContext) {
  const names = (returnContext?.participants || []).map(p => p.display_name).filter(Boolean);
  if (!names.length) return "New Billing Relationship";
  if (names.length === 1) return `${names[0]} Billing Relationship`;
  return `${names[0]} + ${names[1]} Billing Relationship`;
}

function recordBillingPartyDraft(data, payerOptions, returnContext) {
  const billing = data.billing_party || {};
  const isPerson = billing.person_id || billing.billing_party_type === "person" || (!billing.billing_party_type && payerOptions.length);
  const selectedPersonId = billing.person_id || returnContext?.billToPersonId || payerOptions[0]?.person_id || "";
  return {
    billing_party_id: billing.billing_party_id || "",
    billing_party_type: isPerson ? "person" : "organization",
    person_id: selectedPersonId,
    organization_name: billing.organization_name || "",
    billing_name: billing.billing_name || "",
    billing_email: billing.billing_email || "",
    billing_phone: billing.billing_phone || "",
    administrative_notes: data.account?.administrative_notes || ""
  };
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
      <label class="field">First name<input id="editPersonFirst" value="${p.first_name || split.first}"></label>
      <label class="field">Last name<input id="editPersonLast" value="${p.last_name || split.last}"></label>
      <label class="field">Email<input id="editPersonEmail" value="${p.billing_email || ""}"></label>
      <label class="field">Phone<input id="editPersonPhone" value="${p.billing_phone || ""}"></label>
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
      <label>Relationship</label><strong>${accountName}</strong>
      <label>Members</label><span>${(members.length ? members : state.participants).map(m => m.display_name || m.participant_name || "").filter(Boolean).join(", ") || "None"}</span>
      <label>Default payer</label><span>${billingName}</span>
    </div>
    <div class="inline-actions"><button id="openAccountRecord">Open Billing Relationship Record</button></div>
  `;
  if ($("openAccountRecord")) $("openAccountRecord").onclick = () => openAccountRecord(state.account && state.account.account_id);
}

function showAccountEditor() {
  if (!state.account) return alert("Select or create a billing relationship first.");
  const name = prompt("Billing relationship name", state.account.account_name);
  if (!name) return;
  const type = prompt("Relationship type: individual, household, family, couple, organization, other", state.account.account_type || "individual") || "individual";
  api(`/api/accounts/${state.account.account_id}`, { method: "POST", body: JSON.stringify({ account_name: name, account_type: type, default_billing_party_id: state.billingParty ? state.billingParty.billing_party_id : null }) })
    .then(updated => { state.account = updated; $("accountInput").value = updated.account_name; renderRelationshipEditor(state.detail); });
}

function openBillingRelationshipEditor() {
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
  const updated = await api(`/api/review/candidates/${state.selected}/save-session`, { method: "POST", body: JSON.stringify(collectPayload()) });
  state.detail = updated;
  state.editSteps.session = false;
  renderInspector(updated);
  markSaved("session", "Session draft saved");
  await loadList();
}

async function save(approve) {
  await resolveTypedSelections();
  const payload = collectPayload();
  const action = approve ? "approve" : "save";
  try {
    const updated = await api(`/api/review/candidates/${state.selected}/${action}`, { method: "POST", body: JSON.stringify(payload) });
    state.detail = updated;
    state.editSteps = { clients: false, session: false };
    renderInspector(updated);
    await loadList();
  } catch (err) {
    alert(err.message);
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
    time_category: $("timeCategoryInput")?.value || "",
    approved_rate: $("approvedRateInput")?.value || "",
    payment_status: $("paymentInput")?.value || "",
    billing_treatment: $("billingTreatmentInput")?.value || "",
    billable_status: $("billableInput")?.value || "",
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
  if ($("timeCategoryInput")) $("timeCategoryInput").value = values.time_category;
  if ($("approvedRateInput")) $("approvedRateInput").value = values.approved_rate;
  if ($("paymentInput")) $("paymentInput").value = values.payment_status;
  if ($("billingTreatmentInput")) $("billingTreatmentInput").value = values.billing_treatment;
  if ($("billableInput")) $("billableInput").value = values.billable_status;
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
    time_category: $("timeCategoryInput")?.value || state.detail.session.time_category || "standard",
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
  const durationChoice = $("durationChoiceInput")?.value || "60";
  const customMinutes = $("customDurationInput")?.value || "";
  const approvedMinutes = durationChoice === "custom" ? customMinutes : durationChoice;
  return {
    ...collectRelationshipPayload(),
    approved_duration_minutes: approvedMinutes,
    billing_session_type: $("billingTypeInput").value,
    duration_choice: durationChoice,
    custom_duration_minutes: durationChoice === "custom" ? customMinutes : "",
    custom_service_description: $("customDescInput")?.value || "",
    custom_service_code: $("customCodeInput")?.value || "",
    time_category: $("timeCategoryInput").value,
    suggested_rate: centString(state.detail?.session?.suggested_rate_cents),
    billing_party_id: state.billingParty ? state.billingParty.billing_party_id : state.detail?.effective_billing_party?.billing_party_id || null,
    approved_rate: $("approvedRateInput").value,
    payment_status: $("paymentInput").value,
    billing_treatment: $("billingTreatmentInput").value,
    billable_status: $("billableInput").value,
    rate_override_reason: $("overrideReasonInput").value,
    rate_scope: document.querySelector("input[name='rateScope']:checked")?.value || "session_only",
    rate_scope_person_id: $("rateScopePerson")?.value || null
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
  $(id).innerHTML = rows.map(row => `<option value="${row[label]}"></option>`).join("");
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
  return `<div class="suggestion-note"><strong>Location suggests House Call:</strong> ${s.location_text || "Location field present"}. Please confirm the session type.</div>`;
}
function centString(cents) { return cents ? (Number(cents) / 100).toFixed(2) : ""; }
function safeList(raw) { try { return Array.isArray(raw) ? raw : JSON.parse(raw || "[]"); } catch { return []; } }
function startRange(s) {
  const formatter = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" });
  const start = s.start_at ? formatter.format(new Date(s.start_at)) : "";
  const end = s.end_at ? formatter.format(new Date(s.end_at)) : "";
  return start && end ? `${start} - ${end}` : start || end;
}
function debounce(fn, ms) { let t; return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); }; }

["searchBox","statusFilter","serviceFilter","timeFilter","calendarFilter"].forEach(id => $(id).addEventListener("input", () => { state.offset = 0; loadList(); }));
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
  ["reviewWorkbench","calendarImportView","rateCardView","clientsView","peopleView","sessionsView","invoicesView","reportsView","settingsView"].forEach(id => document.getElementById(id).hidden = true);
  ["reviewNav","calendarImportNav","rateCardNav","clientsNav","peopleNav","sessionsNav","invoicesNav","reportsNav","settingsNav"].forEach(id => document.getElementById(id).classList.remove("active"));
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
  $("createRelationshipForReturn").onclick = async () => {
    const current = readReturnContext();
    const suggested = relationshipNameSuggestion(current);
    const name = prompt("Billing relationship name", suggested);
    if (!name) return;
    const account = await api("/api/accounts", { method: "POST", body: JSON.stringify({ account_name: name, account_type: "individual" }) });
    const nextContext = persistReturnContext({ ...current, accountId: account.account_id });
    location.hash = returnContextHash(nextContext);
    await loadClients();
    await openAccountRecord(account.account_id, { returnContext: nextContext });
  };
}

async function showClients() {
  hideViews();
  document.getElementById("clientsView").hidden = false;
  document.getElementById("clientsNav").classList.add("active");
  $("pageTitle").textContent = "Billing Relationships";
  $("pageSubtitle").textContent = "Relationship and shared billing records";
  document.title = "Jordana Billing - Billing Relationships";
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
      actionCell = `<td><button class="restore-session-btn link-btn" data-cid="${row.candidate_id}">Return to Review</button></td>`;
    else if (canPromote)
      actionCell = `<td><button class="send-session-to-review-btn link-btn" data-cid="${row.candidate_id}">Send to Review</button></td>`;
    return `
    <tr>
      <td>${fmt(row.date)}</td>
      <td>${fmt(row.time)}</td>
      <td><span class="primary">${fmt(row.client_participants)}</span></td>
      <td>${fmt(row.calendar_title)}</td>
      <td>${fmt(row.session_length)}</td>
      <td>${money(row.rate)}</td>
      <td>${fmt(row.payment_status)}</td>
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
  try {
    await api(`/api/review/candidates/${candidateId}/restore`, { method: "POST", body: JSON.stringify({ reason: "Returned to review queue from Sessions view" }) });
    await loadSessions();
  } catch (err) {
    alert("Could not restore session: " + err.message);
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
  $("pageSubtitle").textContent = "Draft, finalize, and preserve invoice history";
  document.title = "Jordana Billing - Invoices";
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
    yearSelect.innerHTML = `<option value="${defaultYear}">${defaultYear}</option>`;
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
  $("syncLastAttempt").textContent = fmtDateTime(status.last_attempt);
  $("syncLastSuccess").textContent = fmtDateTime(status.last_success);
  $("syncTotalRowsImported").textContent = String(status.total_rows_imported || 0);
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
  $("syncNowBtn").textContent = isRunning ? "Syncing..." : "Sync Now";
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
    setSyncRunMessage(`Sync complete. Fetched ${result.rows_fetched} row(s); imported ${result.rows_imported} new row(s).`, true);
    await refreshDashboardStatus();
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
  $("logoPathInput").value = next.logo_path;
  $("logoContainsBusinessDetailsInput").checked = next.logo_contains_business_details;
  $("showEmailBelowLogoInput").checked = next.show_email_below_logo;
  $("invoiceTotalLabelInput").value = next.invoice_total_label;
  $("invoiceNumberFormatInput").value = next.invoice_number_format;
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
    logo_path: $("logoPathInput").value.trim(),
    logo_contains_business_details: $("logoContainsBusinessDetailsInput").checked,
    show_email_below_logo: $("showEmailBelowLogoInput").checked,
    invoice_total_label: $("invoiceTotalLabelInput").value.trim() || BUSINESS_PROFILE_DEFAULTS.invoice_total_label,
    invoice_number_format: $("invoiceNumberFormatInput").value.trim() || BUSINESS_PROFILE_DEFAULTS.invoice_number_format
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

async function loadInvoices() {
  const rows = await api(`/api/invoices?status=${encodeURIComponent($("invoiceStatusFilter").value || "")}`);
  $("invoiceRows").innerHTML = rows.map(row => `
    <tr data-invoice="${row.invoice_id}">
      <td><span class="primary">${row.invoice_number || "Draft"}</span></td>
      <td>${fmt(row.bill_to_name_snapshot || row.current_bill_to_name)}</td>
      <td>${fmt(row.billing_period_start)} - ${fmt(row.billing_period_end)}</td>
      <td>${fmt(row.invoice_date)}</td><td>${row.line_count}</td><td>${money(centString(row.total_cents))}</td>
      <td><span class="status-pill ${row.status}">${row.status}</span></td><td>${fmt(row.delivery_method)}</td>
    </tr>`).join("") || `<tr><td colspan="8" class="readonly-note">No invoices yet.</td></tr>`;
  document.querySelectorAll("#invoiceRows tr[data-invoice]").forEach(row => row.onclick = () => openInvoice(row.dataset.invoice));
}

async function startInvoiceBuilder() {
  const parties = await api("/api/billing-parties?q=");
  const today = new Date().toISOString().slice(0, 10);
  const monthStart = `${today.slice(0,7)}-01`;
  $("invoiceWorkspace").innerHTML = `<div class="invoice-builder">
    <div><h3>Create Invoice Draft</h3><div class="help">Only approved, invoice-eligible sessions can be selected.</div></div>
    <div class="field-grid">
      <label class="field wide">Bill to<select id="draftBillTo"><option value="">Select bill-to party</option>${parties.map(p => `<option value="${p.billing_party_id}">${fmt(p.billing_name)}</option>`).join("")}</select></label>
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
    <input type="checkbox" value="${row.id}" ${row.eligible ? "" : "disabled"}><span>${fmt(row.session_date)}</span><span>${fmt(row.participants)}<small class="secondary">${row.ineligibility_reasons.join("; ")}</small></span><span>${serviceLabel(row.service_mode)}</span><strong>${money(centString(row.rate_cents_snapshot || row.approved_rate_cents))}</strong>
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
  $("invoiceWorkspace").innerHTML = `<div class="invoice-builder">
    <div class="section-title-row"><h3>Draft Invoice</h3><span class="status-pill">Draft</span></div>
    <div class="field-grid">
      <label class="field">Invoice date<input id="editInvoiceDate" type="date" value="${i.invoice_date}"></label>
      <label class="field">Delivery<select id="editDelivery">${optionSet(["unresolved","email","mail","both"], i.delivery_method)}</select></label>
    </div>
    <table class="invoice-editor-lines"><thead><tr><th>Date / participants</th><th>Description</th><th>Duration</th><th>Amount</th><th></th></tr></thead><tbody>${data.lines.map(line => `<tr data-line="${line.invoice_line_item_id}"><td>${line.service_date}<small class="secondary">${fmt(line.participants_snapshot)}</small></td><td><input class="line-description" value="${escapeHtml(line.description_snapshot)}"></td><td>${line.duration_minutes == null ? "-" : `${line.duration_minutes} min`}</td><td>${money(centString(line.line_amount_cents))}</td><td><button class="remove-line danger">×</button></td></tr>`).join("")}</tbody></table>
    <div class="invoice-total"><span>TOTAL</span><span>${money(centString(i.total_cents))}</span></div>
    <div class="actions"><button id="saveDraftChanges" class="save">Save Draft</button><button id="addDraftSessions">Add Sessions</button><button id="previewDraft">Preview</button><button id="finalizeInvoice" class="approve">Finalize Invoice</button></div>
  </div>`;
  document.querySelectorAll(".remove-line").forEach(button => button.onclick = async () => {
    const lineId = button.closest("tr").dataset.line;
    const updated = await api(`/api/invoices/${i.invoice_id}/remove-line`, {method:"POST", body:JSON.stringify({invoice_line_item_id:lineId})});
    await renderInvoiceEditor(updated); await loadInvoices();
  });
  $("saveDraftChanges").onclick = async () => {
    const lines = [...document.querySelectorAll("#invoiceWorkspace tr[data-line]")].map((row, index) => ({invoice_line_item_id:row.dataset.line, description_snapshot:row.querySelector(".line-description").value, sort_order:index}));
    const updated = await api(`/api/invoices/${i.invoice_id}`, {method:"POST", body:JSON.stringify({invoice_date:$("editInvoiceDate").value, delivery_method:$("editDelivery").value, lines})});
    await renderInvoiceEditor(updated); await loadInvoices();
  };
  $("addDraftSessions").onclick = () => showAddSessionsToDraft(data);
  $("previewDraft").onclick = () => renderInvoicePreview({...data, invoice:{...i, invoice_date:$("editInvoiceDate").value, delivery_method:$("editDelivery").value}});
  $("finalizeInvoice").onclick = async () => {
    if (!confirm("Finalize this invoice? Its number and snapshots cannot be edited afterward.")) return;
    const final = await api(`/api/invoices/${i.invoice_id}/finalize`, {method:"POST", body:JSON.stringify({confirmed:true})});
    await loadInvoices(); renderInvoicePreview(final);
  };
}

async function showAddSessionsToDraft(data) {
  const i = data.invoice;
  const rows = await api(`/api/invoices/eligible-sessions?bill_to_party_id=${encodeURIComponent(i.bill_to_party_id)}&period_start=${i.billing_period_start}&period_end=${i.billing_period_end}`);
  const eligible = rows.filter(row => row.eligible);
  $("invoiceWorkspace").innerHTML = `<div class="invoice-builder">
    <div><h3>Add Sessions to Draft</h3><div class="help">Sessions already attached to an invoice are excluded by the backend.</div></div>
    <div class="eligible-list">${eligible.map(row => `<label class="eligible-row"><input type="checkbox" value="${row.id}"><span>${fmt(row.session_date)}</span><span>${fmt(row.participants)}</span><span>${serviceLabel(row.service_mode)}</span><strong>${money(centString(row.rate_cents_snapshot || row.approved_rate_cents))}</strong></label>`).join("") || `<div class="empty-state">No additional eligible sessions.</div>`}</div>
    <div class="actions"><button id="confirmAddSessions" class="save" ${eligible.length ? "" : "disabled"}>Add Selected</button><button id="cancelAddSessions">Return to Draft</button></div>
  </div>`;
  $("cancelAddSessions").onclick = () => renderInvoiceEditor(data);
  $("confirmAddSessions").onclick = async () => {
    const sessionIds = [...document.querySelectorAll("#invoiceWorkspace input:checked")].map(input => input.value);
    if (!sessionIds.length) return;
    const updated = await api(`/api/invoices/${i.invoice_id}/add-sessions`, {method:"POST", body:JSON.stringify({session_ids:sessionIds})});
    await loadInvoices(); renderInvoiceEditor(updated);
  };
}

function renderInvoicePreview(data) {
  const i = data.invoice;
  const profile = data.business_profile || {};
  const party = data.billing_party || {};
  const business = i.business_name_snapshot || profile.business_name || "Business profile not configured";
  const provider = i.provider_name_snapshot || profile.provider_display_name || "";
  const credentials = i.credentials_snapshot || profile.credentials_display || "";
  const currentAddress = [party.billing_address_line_1, party.billing_address_line_2, [party.billing_city, party.billing_state].filter(Boolean).join(", ") + (party.billing_postal_code ? ` ${party.billing_postal_code}` : "")].filter(Boolean).join("\n");
  const billto = [i.bill_to_name_snapshot || party.billing_name, i.bill_to_address_snapshot || currentAddress, i.bill_to_email_snapshot || party.billing_email].filter(Boolean).join("\n");
  $("invoiceWorkspace").innerHTML = `<div class="invoice-builder"><div class="section-title-row"><h3>Invoice Preview</h3><span class="status-pill ${i.status}">${i.status}</span></div>
    <article class="invoice-preview">
      <header class="invoice-preview-header"><div class="invoice-preview-brand">${fmt(business)}<small class="secondary">${provider} ${credentials}</small></div><div class="invoice-preview-title"><h3>INVOICE</h3><div><strong>Invoice number:</strong> ${i.invoice_number || "Draft"}</div><div><strong>Invoice date:</strong> ${fmt(i.invoice_date)}</div><div><strong>Billing period:</strong> ${fmt(i.billing_period_start)} - ${fmt(i.billing_period_end)}</div></div></header>
      <div class="invoice-billto"><strong>BILL TO</strong>${fmt(billto)}</div>
      <table class="invoice-preview-table"><thead><tr><th>Date</th><th>Participants</th><th>Service</th><th>Duration</th><th>Amount</th></tr></thead><tbody>${data.lines.map(line => `<tr><td>${line.service_date}</td><td>${fmt(line.participants_snapshot)}</td><td>${fmt(line.description_snapshot)}</td><td>${line.duration_minutes == null ? "-" : `${line.duration_minutes} min`}</td><td>${money(centString(line.line_amount_cents))}</td></tr>`).join("")}</tbody></table>
      <div class="invoice-total"><span>${i.total_label_snapshot || profile.invoice_total_label || "TOTAL DUE"}</span><span>${money(centString(i.total_cents))}</span></div>
      <div class="invoice-payment"><b>Please make all checks payable to:</b> ${fmt(i.payee_name_snapshot || profile.payee_name)}\n<b>Please send payment to:</b> ${fmt(i.payment_address_snapshot || [profile.payee_name, profile.payment_address_line_1, [profile.payment_city, profile.payment_state].filter(Boolean).join(", ") + (profile.payment_postal_code ? ` ${profile.payment_postal_code}` : "")].filter(Boolean).join("\n"))}</div>
    </article>
    <div class="actions">${i.status === "draft" ? `<button id="returnToDraft">Return to Draft</button>` : ""}${i.status === "finalized" ? `<button id="voidInvoice" class="danger">Void Invoice</button>` : ""}</div></div>`;
  if ($("returnToDraft")) $("returnToDraft").onclick = () => renderInvoiceEditor(data);
  if ($("voidInvoice")) $("voidInvoice").onclick = async () => { const reason = prompt("Reason for voiding this invoice"); if (!reason) return; const result = await api(`/api/invoices/${i.invoice_id}/void`, {method:"POST", body:JSON.stringify({reason})}); await loadInvoices(); renderInvoicePreview(result); };
}

$("newInvoiceBtn").onclick = startInvoiceBuilder;
$("invoiceStatusFilter").onchange = loadInvoices;
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
      <td>$${row.amount}</td>
      <td>${fmt(row.duration_label)}</td>
      <td>${fmt(row.session_type_label)}</td>
      <td>${fmt(row.appointment_status_label)}</td>
      <td>${timeLabel(row.time_category)}</td>
      <td>${fmt(row.scope_label)}</td>
      <td>${fmt(row.effective_from)}${row.effective_through ? ` to ${fmt(row.effective_through)}` : ""}</td>
      <td>${row.ended ? '<span class="readonly-note">Ended</span>' : `<button class="mini" data-replace-rate="${row.rate_rule_id}">Replace</button><button class="mini danger" data-end-rate="${row.rate_rule_id}">End</button>`}</td>
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
  $("rateParticipantSelections").innerHTML = state.rateCard.participantSelections.map(person => `<span class="chip linked">${person.display_name}<button data-remove-rate-participant="${person.person_id}">×</button></span>`).join("");
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
    return `<button type="button" class="mini" data-rate-scope-pick="${mode}:${mode === "account" ? row.account_id : row.person_id}">${fmt(label)}${code ? ` (${code})` : ""}</button>`;
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

async function loadClients() {
  const rows = await api(`/api/accounts?full=1&q=${encodeURIComponent($("clientSearch").value || "")}`);
  $("clientRows").innerHTML = rows.map(row => `
    <tr data-account="${row.account_id}">
      <td>${fmt(row.account_code)}</td>
      <td><span class="primary">${fmt(row.account_name)}</span></td>
      <td>${fmt(row.account_type)}</td>
      <td>${fmt(row.primary_person)}</td>
      <td>${fmt(row.members)}</td>
      <td>${fmt(row.billing_party_name)}</td>
      <td>${money(row.current_default_rate)}</td>
      <td>${money(row.outstanding_balance)}</td>
      <td>${fmt(row.last_session)}</td>
      <td>${row.active ? "Active" : "Inactive"}</td>
    </tr>
  `).join("");
  document.querySelectorAll("#clientRows tr").forEach(row => row.onclick = () => openAccountRecord(row.dataset.account, { returnContext: readReturnContext() }));
}

async function openAccountRecord(accountId, options = {}) {
  if (!accountId) return alert("Select or create a billing relationship first.");
  const returnContext = validReturnContext(options.returnContext) ? persistReturnContext(options.returnContext) : readReturnContext();
  if (returnContext) {
    persistReturnContext({ ...returnContext, accountId });
    if (!location.hash.startsWith("#clients?")) location.hash = returnContextHash({ ...returnContext, accountId });
  }
  state.returnCandidate = state.selected;
  const data = await api(`/api/accounts/${accountId}`);
  const payerOptions = payerDisplayOptions(data.members || [], returnContext);
  const billingDraft = recordBillingPartyDraft(data, payerOptions, returnContext);
  $("accountRecord").innerHTML = `
    ${returnContext ? `<a href="#" class="return-link" id="returnFromAccount">← Return to ${fmt(state.detail?.session?.raw_calendar_title)} — ${fmt(state.detail?.session?.session_date)}</a>` : ""}
    <h3>${fmt(data.account.account_name)}</h3>
    <div class="meta"><span>${fmt(data.account.account_code)}</span><span>${fmt(data.account.account_type)}</span><span>${data.account.active ? "Active" : "Inactive"}</span></div>
    <div class="record-actions"><button id="editAccountRecord" class="save">Save Billing Relationship</button><button id="addMemberRecord">Add Member</button></div>
    <div class="field-grid">
      <label class="field">Relationship Name<input id="recordAccountName" value="${fmt(data.account.account_name)}"></label>
      <label class="field">Type<select id="recordAccountType">${optionSet(["individual","household","family","couple","organization","other"], data.account.account_type)}</select></label>
      <label class="field wide">Admin Notes<input id="recordAccountNotes" value="${data.account.administrative_notes || ""}"></label>
    </div>
    <h4>Members</h4><div class="compact-list">${data.members.map(m => `<div><span>${fmt(m.display_name)} ${m.is_primary ? "(Primary)" : ""}</span><span>${fmt(m.relationship_role)}</span></div>`).join("") || "<span class='readonly-note'>No members yet.</span>"}</div>
    <h4>Billing</h4>
    <div class="field-grid">
      <label class="field">Payer Type<select id="recordBillingPartyType">
        <option value="person" ${billingDraft.billing_party_type === "person" ? "selected" : ""}>Client payer</option>
        <option value="organization" ${billingDraft.billing_party_type === "organization" ? "selected" : ""}>Organization payer</option>
      </select></label>
      <label class="field" id="recordPayerPersonField">Bill-to client<select id="recordPayerPersonId">
        <option value="">Select client payer</option>
        ${payerOptions.map(option => `<option value="${option.person_id}" ${option.person_id === billingDraft.person_id ? "selected" : ""}>${fmt(option.display_name)}</option>`).join("")}
      </select></label>
      <label class="field" id="recordOrgNameField">Organization name<input id="recordOrganizationName" value="${billingDraft.organization_name || ""}"></label>
      <label class="field">Payer name<input id="recordBillingName" value="${billingDraft.billing_name || ""}"></label>
      <label class="field">Email<input id="recordBillingEmail" value="${billingDraft.billing_email || ""}"></label>
      <label class="field">Phone<input id="recordBillingPhone" value="${billingDraft.billing_phone || ""}"></label>
    </div>
    <div class="kv"><label>Current default payer</label><span>${fmt(data.billing_party?.billing_name)}</span><label>Current email</label><span>${fmt(data.billing_party?.billing_email)}</span><label>Current phone</label><span>${fmt(data.billing_party?.billing_phone)}</span></div>
    <h4>Rates</h4><div class="compact-list">${data.rates.map(r => `<div><span>${money(centString(r.amount_cents))} ${fmt(r.duration_minutes || "Any")} min</span><span>${r.active ? "Active" : "Inactive"}</span></div>`).join("") || "<span class='readonly-note'>No relationship-specific rates.</span>"}</div>
    <h4>Session History</h4><div class="compact-list">${data.sessions.slice(0, 8).map(s => `<div><span>${fmt(s.session_date)} ${fmt(s.duration_minutes)} min ${serviceLabel(s.service_mode)} ${timeLabel(s.time_category)}</span><span>${money(centString(s.approved_rate_cents))} ${fmt(s.approved_rate_source || s.rate_source)}</span></div>`).join("") || "<span class='readonly-note'>No sessions yet.</span>"}</div>
    <h4>Active Rate Exceptions</h4><div class="compact-list">${(data.active_rate_exceptions || []).map(r => `<div><span>${fmt(r.effective_from)} ${fmt(r.duration_minutes || "Any")} min ${serviceLabel(r.service_mode || r.rate_group || "Any")}</span><span>${money(centString(r.amount_cents))}</span></div>`).join("") || "<span class='readonly-note'>No person-specific rate exceptions.</span>"}</div>
    <h4>Shared Rate Exceptions</h4><div class="compact-list">${(data.joint_rate_exceptions || []).map(r => `<div><span>${fmt(r.participant_names)} ${fmt(r.duration_minutes || "Any")} min</span><span>${money(centString(r.amount_cents))}</span></div>`).join("") || "<span class='readonly-note'>No joint-session exceptions.</span>"}</div>
    <h4>Aliases</h4><div class="compact-list">${data.aliases.map(a => `<div><span>${fmt(a.raw_alias)}</span><span>${fmt(a.classification)}</span></div>`).join("") || "<span class='readonly-note'>No aliases yet.</span>"}</div>
  `;
  const syncBillingPartyFields = () => {
    const mode = $("recordBillingPartyType").value;
    $("recordPayerPersonField").hidden = mode !== "person";
    $("recordOrgNameField").hidden = mode !== "organization";
    if (mode === "person") {
      const payer = payerOptions.find(option => option.person_id === $("recordPayerPersonId").value) || payerOptions[0];
      if (payer && !$("recordBillingName").value) $("recordBillingName").value = payer.display_name;
    }
  };
  $("recordBillingPartyType").onchange = syncBillingPartyFields;
  if ($("recordPayerPersonId")) {
    $("recordPayerPersonId").onchange = () => {
      const payer = payerOptions.find(option => option.person_id === $("recordPayerPersonId").value);
      if (payer) $("recordBillingName").value = payer.display_name;
      syncBillingPartyFields();
    };
  }
  syncBillingPartyFields();
  if ($("returnFromAccount")) $("returnFromAccount").onclick = async (event) => {
    event.preventDefault();
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
  $("editAccountRecord").onclick = async () => {
    const accountPayload = {
      account_name: $("recordAccountName").value,
      account_type: $("recordAccountType").value,
      administrative_notes: $("recordAccountNotes").value
    };
    const payerMode = $("recordBillingPartyType").value;
    const selectedPayerPersonId = $("recordPayerPersonId")?.value || "";
    const orgBillingName = $("recordBillingName").value.trim();
    if (validReturnContext(returnContext) && payerMode === "person" && !selectedPayerPersonId) {
      alert("Select the bill-to client before saving this billing relationship.");
      return;
    }
    if (validReturnContext(returnContext) && payerMode === "organization" && !orgBillingName) {
      alert("Enter the payer name before saving this billing relationship.");
      return;
    }
    await api(`/api/accounts/${accountId}`, { method: "POST", body: JSON.stringify(accountPayload) });
    if (validReturnContext(returnContext)) {
      const currentContext = persistReturnContext({ ...returnContext, accountId });
      const relationshipPayload = {
        participants: (currentContext.participants || []).map((participant, index) => ({
          person_id: participant.person_id,
          display_name: participant.display_name,
          is_primary: participant.is_primary || index === 0,
          relationship_role: participant.relationship_role || (index === 0 ? "primary" : "family_member")
        })),
        account_id: accountId,
        primary_person_id: (currentContext.participants || []).find(participant => participant.is_primary)?.person_id || currentContext.participants?.[0]?.person_id || null
      };
      await api(`/api/review/candidates/${currentContext.candidateId}/save-relationship`, { method: "POST", body: JSON.stringify(relationshipPayload) });
      if (payerMode === "person") {
        await api(`/api/review/candidates/${currentContext.candidateId}/save-billing`, {
          method: "POST",
          body: JSON.stringify({ bill_to_person_id: selectedPayerPersonId })
        });
      } else {
        const billingPayload = {
          billing_party_id: billingDraft.billing_party_id || "",
          billing_party_type: "organization",
          organization_name: $("recordOrganizationName").value.trim() || null,
          billing_name: orgBillingName,
          billing_email: $("recordBillingEmail").value.trim() || null,
          billing_phone: $("recordBillingPhone").value.trim() || null
        };
        const billingResult = billingPayload.billing_party_id
          ? await api(`/api/billing-parties/${billingPayload.billing_party_id}`, { method: "POST", body: JSON.stringify(billingPayload) })
          : await api("/api/billing-parties", { method: "POST", body: JSON.stringify(billingPayload) });
        await api(`/api/accounts/${accountId}`, {
          method: "POST",
          body: JSON.stringify({ ...accountPayload, default_billing_party_id: billingResult.billing_party_id })
        });
        await api(`/api/review/candidates/${currentContext.candidateId}/save-billing`, {
          method: "POST",
          body: JSON.stringify({ billing_party_id: billingResult.billing_party_id })
        });
      }
      clearReturnContext();
      location.hash = "";
      await loadList();
      await showReviewWorkbench();
      await selectCandidate(currentContext.candidateId);
      return;
    }
    await openAccountRecord(accountId);
    await loadClients();
  };
  $("addMemberRecord").onclick = async () => {
    const name = prompt("Add which existing client to this billing relationship?");
    if (!name) return;
    const rows = await api(`/api/people?q=${encodeURIComponent(name)}`);
    const match = rows.find(row => row.display_name.toLowerCase() === name.toLowerCase()) || rows[0];
    if (!match) {
      alert("No matching client found.");
      return;
    }
    await api("/api/account-members", {
      method: "POST",
      body: JSON.stringify({ account_id: accountId, person_id: match.person_id, relationship_role: "family_member", is_primary: false })
    });
    await loadClients();
    await openAccountRecord(accountId, { returnContext });
  };
  if (!location.hash.startsWith("#clients")) {
    location.hash = "clients";
    showClients();
  }
}

async function loadPeople() {
  const rows = await api(`/api/people?full=1&q=${encodeURIComponent($("peopleSearch").value || "")}`);
  $("peopleRows").innerHTML = rows.map(row => `
    <tr data-person="${row.person_id}">
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
  state.returnCandidate = state.selected;
  const data = await api(`/api/people/${personId}`);
  const visibleSessions = showAllSessions ? data.sessions : data.sessions.slice(0, 10);
  const summary = data.billing_summary || {};
  const billingSetup = data.billing_setup || [];
  const payers = data.payers_for_client || [];
  const peopleBilledFor = data.people_billed_for || [];
  const invoices = data.invoices || [];
  const personName = fmt(data.person.display_name);

  const billingSetupHtml = billingSetup.length
    ? billingSetup.map(b => {
        const addr = billingAddressSummary(b);
        const delivery = b.preferred_delivery_method && b.preferred_delivery_method !== "unresolved" ? fmt(b.preferred_delivery_method) : "—";
        const isSelfPay = b.person_id === personId;
        const label = isSelfPay ? `<div class="billing-card-label">Bills sent to this client</div>` : "";
        return `<div class="billing-card">
          ${label}
          <div class="billing-card-name">${fmt(b.billing_name)}</div>
          <div class="billing-card-details">
            <div>${b.billing_email ? fmt(b.billing_email) : "—"}</div>
            <div>${b.billing_phone ? fmt(b.billing_phone) : "—"}</div>
            <div>${addr ? fmt(addr) : "—"}</div>
            <div>Delivery: ${delivery}</div>
            <div>${b.active ? "Active" : "Inactive"}</div>
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
    ? data.accounts.map(a => `<div class="compact-list-item"><span>${fmt(a.account_name)} • ${fmt(a.relationship_role)}${a.is_primary ? " • Primary" : ""}</span><button class="mini" data-open-account="${a.account_id}">Open</button></div>`).join("")
    : `<span class="readonly-note">No related billing group information.</span>`;

  const sessionsRowsHtml = visibleSessions.length
    ? visibleSessions.map(s => `<tr>
        <td>${fmt(s.session_date)}</td>
        <td>${fmt(s.other_participant_names ? "With " + s.other_participant_names : "Solo")}</td>
        <td>${userFacingSessionLabel(s.billing_session_type, s.appointment_status, s.custom_service_description || "")}</td>
        <td>${fmt(s.custom_duration_minutes || s.duration_minutes)} min</td>
        <td>${timeLabel(s.time_category)}</td>
        <td>${money(centString(s.approved_rate_cents))}</td>
        <td>${fmt(s.payment_status)}</td>
        <td>${fmt(s.review_status)}</td>
        <td><button class="mini" data-open-candidate="${s.candidate_id}">Open in Review</button></td>
      </tr>`).join("")
    : `<tr><td colspan="9" class="readonly-note">No sessions yet.</td></tr>`;

  const invoiceRowsHtml = invoices.length
    ? invoices.map(inv => `<tr data-invoice-id="${inv.invoice_id}">
        <td><span class="primary">${fmt(inv.invoice_number || "Draft")}</span></td>
        <td>${fmt(inv.billing_period_start)} – ${fmt(inv.billing_period_end)}</td>
        <td>${fmt(inv.invoice_date)}</td>
        <td>${fmt(inv.bill_to_name)}</td>
        <td><span class="status-pill ${inv.status}">${fmt(inv.status)}</span></td>
        <td>${money(centString(inv.total_cents))}</td>
        <td>${money(centString(inv.balance_cents))}</td>
        <td><button class="mini" data-open-invoice="${inv.invoice_id}">Open</button></td>
      </tr>`).join("")
    : `<tr><td colspan="8" class="readonly-note">No invoices yet.</td></tr>`;

  $("personRecordView").innerHTML = `
    ${state.returnCandidate ? `<a href="#" class="return-link" id="returnFromPerson">← Return to ${fmt(state.detail?.session?.raw_calendar_title)} — ${fmt(state.detail?.session?.session_date)}</a>` : ""}
    <a href="#people" class="return-link" id="backToClients">← Back to Clients</a>
    <div class="client-workspace">
      <div class="client-header">
        <h2>${fmt(data.person.display_name)}</h2>
        <div class="meta"><span>${fmt(data.person.person_code)}</span><span>${fmt(data.person.active_status)}</span></div>
      </div>

      <div class="summary-cards">
        <div class="summary-card"><div class="summary-card-label">Active Billing Records</div><div class="summary-card-value">${fmt(summary.active_billing_parties)}</div></div>
        <div class="summary-card"><div class="summary-card-label">Approved Uninvoiced Sessions</div><div class="summary-card-value">${fmt(summary.approved_uninvoiced_sessions)}</div></div>
        <div class="summary-card"><div class="summary-card-label">Total Invoiced</div><div class="summary-card-value">${money(centString(summary.total_invoiced_cents))}</div></div>
        <div class="summary-card"><div class="summary-card-label">Outstanding Balance</div><div class="summary-card-value">${money(centString(summary.outstanding_balance_cents))}</div></div>
      </div>

      <section class="client-section">
        <h3>Client Details</h3>
        <div class="field-grid">
          <label class="field">First Name<input id="recordFirstName" value="${data.person.first_name || ""}"></label>
          <label class="field">Last Name<input id="recordLastName" value="${data.person.last_name || ""}"></label>
          <label class="field">Preferred Name<input id="recordPreferredName" value="${data.person.preferred_name || ""}"></label>
          <label class="field">Display Name<input id="recordDisplayName" value="${data.person.display_name || ""}"></label>
          <label class="field">Email<input id="recordPersonEmail" value="${data.person.billing_email || ""}"></label>
          <label class="field">Phone<input id="recordPersonPhone" value="${data.person.billing_phone || ""}"></label>
          <label class="field">Status<input value="${data.person.active_status || ""}" readonly></label>
          <label class="field wide">Administrative Notes<input id="recordPersonNotes" value="${data.person.administrative_notes || ""}"></label>
        </div>
        <div class="record-actions"><button id="savePersonRecord" class="save">Save Client</button></div>
      </section>

      <section class="client-section">
        <h3>Billing Setup</h3>
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
            <thead><tr><th>Invoice Number</th><th>Billing Period</th><th>Issue Date</th><th>Bill To</th><th>Status</th><th>Total</th><th>Balance</th><th>Open</th></tr></thead>
            <tbody>${invoiceRowsHtml}</tbody>
          </table>
        </div>
      </section>

      <section class="client-section">
        <h3>Sessions</h3>
        <div class="table-scroll-wrap">
          <table class="review-table client-sessions-table">
            <thead><tr><th>Date</th><th>Participants</th><th>Session Type</th><th>Duration</th><th>Time Category</th><th>Rate</th><th>Payment Status</th><th>Review Status</th><th>Open in Review</th></tr></thead>
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
            <label class="field">Time category<select id="personRateTimeCategory">${optionSet(["standard","evening","weekend","weekend_evening"], "standard")}</select></label>
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
          <div class="compact-list">${data.aliases.map(a => `<div class="compact-list-item"><span>${fmt(a.raw_alias)} • ${a.approved_by_user ? "approved" : "inactive"}</span><button class="mini" data-alias-id="${a.alias_id}" data-raw-alias="${escapeHtml(a.raw_alias || "")}" data-approved="${a.approved_by_user ? "1" : "0"}">${a.approved_by_user ? "Deactivate" : "Inactive"}</button></div>`).join("") || "<span class='readonly-note'>No aliases yet.</span>"}</div>
          <h4>Audit History</h4>
          <div class="compact-list">${(data.audit || []).map(entry => `<div class="compact-list-item"><span>${fmt(entry.created_at)} • ${fmt(entry.action)}</span></div>`).join("") || "<span class='readonly-note'>No audit history yet.</span>"}</div>
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
}
["clientSearch","peopleSearch"].forEach(id => $(id).addEventListener("input", debounce(() => id === "clientSearch" ? loadClients() : loadPeople(), 180)));
$("newAccountBtn").onclick = async () => {
  const returnContext = readReturnContext();
  const name = prompt("Billing relationship name", relationshipNameSuggestion(returnContext));
  if (!name) return;
  const account = await api("/api/accounts", { method: "POST", body: JSON.stringify({ account_name: name, account_type: "individual" }) });
  if (validReturnContext(returnContext)) {
    persistReturnContext({ ...returnContext, accountId: account.account_id });
    location.hash = returnContextHash({ ...returnContext, accountId: account.account_id });
  }
  await loadClients();
  await openAccountRecord(account.account_id, { returnContext });
};
$("newPersonBtn").onclick = async () => {
  const name = prompt("Client display name");
  if (!name) return;
  const person = await api("/api/people", { method: "POST", body: JSON.stringify({ display_name: name }) });
  await loadPeople();
  location.hash = "people/" + person.person_id;
};
document.getElementById("syncNowBtn").onclick = runSyncNow;
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
  "logoPathInput",
  "logoContainsBusinessDetailsInput",
  "showEmailBelowLogoInput",
  "invoiceTotalLabelInput",
  "invoiceNumberFormatInput"
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
  const scope = row.participant_names ? row.participant_names : row.account_name ? `billing relationship ${row.account_name}` : row.display_name ? `client ${row.display_name}` : "everyone";
  return `Applies to ${scope}; ${row.duration_minutes || "any"} minutes; ${billingTypeShort(row.billing_session_type || "any")}; ${timeLabel(row.time_category)}.`;
}

function rateSourceDescription(session, participants = []) {
  const names = participants.map(p => p.display_name || p.participant_name).filter(Boolean);
  const first = names[0] || "Participant";
  const joined = names.join(" + ");
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
  return String(value || "").replace(/[&<>"']/g, ch => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#039;" }[ch]));
}
