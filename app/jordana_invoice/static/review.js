const state = { items: [], selected: null, offset: 0, limit: 25, participants: [], account: null, billingParty: null, dirty: new Set(), returnCandidate: null, invoice: null, eligibleSessions: [] };

const $ = (id) => document.getElementById(id);
const fmt = (v) => v || "-";
const money = (v) => v ? `$${v}` : "—";
const billingTypeLabel = (v) => ({psychotherapy:"Psychotherapy Session", psychotherapy_house_call:"Psychotherapy Session / House Call", psychotherapy_weekend:"Psychotherapy Session / Weekend", psychotherapy_evening:"Psychotherapy Session / Evening", custom:"Custom"}[v] || v || "Psychotherapy Session");
const billingTypeShort = (v) => ({psychotherapy:"Standard", psychotherapy_house_call:"House Call", psychotherapy_weekend:"Weekend", psychotherapy_evening:"Evening", custom:"Custom"}[v] || v || "Standard");
const appointmentMethodLabel = (v) => ({phone:"Phone", facetime:"FaceTime", office:"Office", unknown:"Unknown"}[v] || v || "Unknown");
const serviceLabel = (v) => ({phone:"Phone", facetime:"FaceTime", office:"Office", house_call:"House Call", unknown:"Unknown"}[v] || v || "Unknown");
const timeLabel = (v) => ({standard:"Standard", evening:"Evening", weekend:"Weekend", weekend_evening:"Weekend + Evening"}[v] || v || "Standard");
const participantState = (p) => ({ person_id: p.person_id, display_name: p.display_name || p.participant_name, participant_name: p.participant_name, is_primary: !!p.is_primary, is_proposed: !!p.is_proposed, source: p.source || "", relationship_role: p.relationship_role });

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
  $("lastSync").textContent = s.last_sync ? new Date(s.last_sync).toLocaleString([], { month:"short", day:"numeric", hour:"numeric", minute:"2-digit" }) : "-";
  $("needsReview").textContent = s.needs_review;
  $("navNeeds").textContent = s.needs_review;
  $("readyApprove").textContent = s.ready_to_approve;
  $("approvedMonth").textContent = s.approved_this_month;
  $("personalAdmin").textContent = s.personal_admin;
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
      <td>${billingTypeShort(item.billing_session_type || item.service_mode)}</td>
      <td>${timeLabel(item.time_category)}</td>
      <td>${money(item.rate)}</td>
      <td><span class="confidence ${item.confidence >= 90 ? "good" : "low"}">${item.confidence || 0}%</span></td>
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
  document.querySelectorAll("#candidateRows tr").forEach(row => row.classList.toggle("selected", row.dataset.id === candidateId));
  const data = await api(`/api/review/candidates/${candidateId}`);
  state.detail = data;
  state.participants = data.participants.map(participantState);
  state.account = data.account;
  state.billingParty = data.billing_party;
  renderInspector(data);
}

function renderInspector(data) {
  const s = data.session;
  const isSession = Boolean(s.id);
  $("inspector").innerHTML = `
    <div class="inspector-header">
      <div>
        <h2>${fmt(s.raw_calendar_title || s.title)}</h2>
        <div class="meta"><span>${fmt(s.session_date)}</span><span>${fmt(startRange(s))}</span><span>${fmt(s.duration_minutes)} min</span><span>${calendarLabel(s)}</span><span>${appointmentBadge(s.appointment_status)}</span></div>
      </div>
      <div><span class="badge">${fmt(s.review_status).replaceAll("_", " ")}</span><div class="confidence ${Math.round((s.confidence || 0) * 100) >= 90 ? "good" : "low"}">Confidence: ${Math.round((s.confidence || 0) * 100)}%</div></div>
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
      <div class="section-title-row"><h3>Participants</h3><span class="save-state" id="relationshipState">Needs review</span></div>
      <div class="help">Clients in this session</div>
      <div class="chips" id="participantChips"></div>
      <div class="combobox"><input id="personInput" placeholder="Search existing person or add a new one" list="peopleList"><button class="mini" id="addPerson">+</button></div>
      <datalist id="peopleList"></datalist>
      <div id="personWarning"></div>
      <div id="personEditor" class="drawer" hidden></div>
      <div class="inline-actions">
        <button id="saveRelationshipBtn" class="save">Save Participants</button>
        <button id="openPersonRecord">Open Person Record</button>
      </div>
    </section>

    <section class="section">
      <div class="section-title-row"><h3>Bill to</h3><span class="save-state" id="billingState">Needs review</span></div>
      <div class="help">Person or organization responsible for receiving and paying the invoice. This does not add them as a participant.</div>
      <div class="combobox"><input id="billingInput" placeholder="Search or create a bill-to contact" value="${data.billing_party ? data.billing_party.billing_name : ""}" list="billingList"><button class="mini" id="addBilling">+</button></div>
      <datalist id="billingList"></datalist>
      <div id="billingEditor" class="drawer" hidden></div>
      <div class="inline-actions">
        <button id="saveBillingBtn" class="save">Save Bill To</button>
        <button id="sameAsPrimary">Same as sole participant</button>
        <button id="editBilling">Edit Bill To</button>
      </div>
    </section>

    <section class="section">
      <div class="section-title-row"><h3>Session Details</h3><span class="save-state" id="sessionState">Needs review</span></div>
      <div class="field-grid">
        <label class="field">Session Type<select id="billingTypeInput">${billingTypeOptions(s.billing_session_type || mapLegacyToType(s))}</select></label>
        <label class="field">Duration<select id="durationChoiceInput">${durationOptions(s.duration_choice || durationToChoice(s.approved_duration_minutes || s.duration_minutes))}</select></label>
        <label class="field" id="customDurationField" ${(s.duration_choice === "custom" || !["30","60","90","120"].includes(String(s.approved_duration_minutes || s.duration_minutes))) ? "" : "hidden"}>Custom Minutes<input id="customDurationInput" type="number" min="1" value="${s.custom_duration_minutes || s.approved_duration_minutes || s.duration_minutes || ""}"></label>
        <label class="field" id="customDescField" ${s.billing_session_type === "custom" ? "" : "hidden"}>Custom Description<input id="customDescInput" value="${s.custom_service_description || ""}"></label>
        <label class="field" id="customCodeField" ${s.billing_session_type === "custom" ? "" : "hidden"}>Custom Code<input id="customCodeInput" value="${s.custom_service_code || ""}"></label>
        <label class="field">Appointment Method<span class="help">Internal evidence (Office/Phone/FaceTime)</span><span class="readonly-value">${appointmentMethodLabel(s.appointment_method || s.service_mode)}</span></label>
        <label class="field">Time Category<select id="timeCategoryInput">${optionSet(["standard","evening","weekend","weekend_evening"], s.time_category)}</select></label>
        <label class="field">Suggested Rate<span class="help">${rateSourceDescription(s, data.participants)}</span><input id="suggestedRateInput" value="${centString(s.suggested_rate_cents)}"></label>
        <label class="field">Suggested/editable rate<span class="help">The final amount saved for this session.</span><input id="approvedRateInput" value="${centString(s.approved_rate_cents || s.suggested_rate_cents)}"></label>
        <label class="field">Payment Status<span class="help">Whether payment has already been received.</span><select id="paymentInput">${optionSet(["unresolved","unpaid","partially_paid","paid","waived","not_billable"], s.payment_status)}</select></label>
        <label class="field">Cancellation/No-Show Billing<span class="help">Separate billing decision for cancelled or no-show appointments.</span><select id="billingTreatmentInput">${optionSet(["unresolved","billable","not_billable","waived"], s.billing_treatment || "billable")}</select></label>
        <label class="field">Billable Status<select id="billableInput">${optionSet(["proposed","approved","excluded","nonbillable"], s.billable_status || "proposed")}</select></label>
        <label class="field wide">Override Reason<input id="overrideReasonInput" value="${s.rate_override_reason || ""}"></label>
      </div>
      ${houseCallSuggestion(s)}
      <div class="rate-scope" id="rateScope">
        <strong>Apply this rate to:</strong>
        <label><input type="radio" name="rateScope" value="session_only" checked> This session only</label>
        <label><input type="radio" name="rateScope" value="future_person"> Future sessions for this participant</label>
        <select id="rateScopePerson">${state.participants.map(p => `<option value="${p.person_id || ""}">${p.display_name || p.participant_name || ""}</option>`).join("")}</select>
        <label><input type="radio" name="rateScope" value="future_joint" ${state.participants.length < 2 ? "disabled" : ""}> Future joint sessions for these participants</label>
      </div>
      <div class="inline-actions"><button id="saveSessionBtn" class="save">Save Session Draft</button></div>
    </section>

    <section class="section">
      <details>
        <summary class="section-summary">Advanced relationships and shared billing</summary>
        <div class="field-grid">
          <label class="field wide">Related Account
            <span class="help">Optional backend relationship support for families, couples, shared billing, default payer, or special joint rates.</span>
            <div class="combobox"><input id="accountInput" placeholder="Search or create an account" value="${data.account ? data.account.account_name : ""}" list="accountList"><button class="mini" id="addAccount">+</button></div>
          </label>
          <datalist id="accountList"></datalist>
        </div>
        <div class="inline-actions">
          <button id="editAccount">Quick Edit Account</button>
          <button id="openAccountRecord">Open Account Record</button>
        </div>
        <div id="relationshipEditor" class="drawer"></div>
      </details>
      <div class="hint">Suggestion reasons: ${(safeList(s.review_reasons).join(" ") || s.explanation || "Calendar title matched the parser pattern.")}</div>
    </section>

    <section class="section">
      <h3>Review Checklist</h3>
      <div class="checklist">${data.checklist.map(c => `<div class="check ${c.resolved ? "done" : ""}"><span></span><label>${c.label}</label></div>`).join("")}</div>
    </section>

    <div class="actions">
      ${isSession ? '<button class="approve" id="approveBtn">Approve Session</button>' : ""}
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
  $("personInput").addEventListener("input", debounce(async e => fillDatalist("peopleList", await api(`/api/people?q=${encodeURIComponent(e.target.value)}`), "display_name"), 160));
  $("accountInput").addEventListener("input", debounce(async e => fillDatalist("accountList", await api(`/api/accounts?q=${encodeURIComponent(e.target.value)}`), "account_name"), 160));
  $("billingInput").addEventListener("input", debounce(async e => fillDatalist("billingList", await api(`/api/billing-parties?q=${encodeURIComponent(e.target.value)}`), "billing_name"), 160));
  $("addPerson").onclick = createPersonFromInput;
  $("addAccount").onclick = createAccountFromInput;
  $("addBilling").onclick = createBillingFromInput;
  if ($("approveBtn")) $("approveBtn").onclick = () => save(true);
  $("saveRelationshipBtn").onclick = saveRelationshipSection;
  $("saveBillingBtn").onclick = saveBillingSection;
  $("saveSessionBtn").onclick = saveSessionSection;
  $("sameAsPrimary").onclick = sameAsPrimaryParticipant;
  $("editAccount").onclick = showAccountEditor;
  $("editBilling").onclick = showBillingEditor;
  $("openPersonRecord").onclick = () => openPrimaryPersonRecord();
  $("openAccountRecord").onclick = () => openAccountRecord(state.account && state.account.account_id);
  $("personalBtn").onclick = () => mark("personal");
  $("duplicateBtn").onclick = () => mark("duplicate");
  $("excludeBtn").onclick = () => mark("nonbillable");
  [
    "billingTypeInput",
    "durationChoiceInput",
    "customDurationInput",
    "customDescInput",
    "customCodeInput",
    "timeCategoryInput",
    "suggestedRateInput",
    "approvedRateInput",
    "paymentInput",
    "billingTreatmentInput",
    "billableInput",
    "overrideReasonInput"
  ].forEach(id => {
    const element = $(id);
    if (element) element.addEventListener("input", () => markDirty("session"));
  });
  $("accountInput").addEventListener("input", () => markDirty("relationship"));
  $("billingInput").addEventListener("input", () => markDirty("billing"));
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
  $("participantChips").innerHTML = state.participants.map((p, i) => `<span class="chip ${p.is_proposed ? "proposed" : "linked"}">${p.display_name || p.participant_name}${p.is_proposed ? '<small>proposed</small>' : ''}<button data-edit="${i}">Edit</button><button data-i="${i}">×</button></span>`).join("");
  document.querySelectorAll("#participantChips button[data-i]").forEach(btn => btn.onclick = () => { state.participants.splice(Number(btn.dataset.i), 1); renderParticipantChips(); renderRelationshipEditor(state.detail); markDirty("relationship"); });
  document.querySelectorAll("#participantChips button[data-edit]").forEach(btn => btn.onclick = () => showPersonEditor(Number(btn.dataset.edit)));
}

async function createPersonFromInput() {
  const name = $("personInput").value.trim();
  if (!name) return;
  const rows = await api(`/api/people?q=${encodeURIComponent(name)}`);
  const exact = rows.find(row => String(row.display_name).toLowerCase() === name.toLowerCase());
  if (!exact) {
    showNewPersonForm(name);
    return;
  }
  const person = exact;
  state.participants.push({ person_id: person.person_id, display_name: person.display_name, is_primary: state.participants.length === 0 });
  $("personInput").value = "";
  renderParticipantChips();
  renderRelationshipEditor(state.detail);
  markDirty("relationship");
}

function showNewPersonForm(name = "") {
  const firstLast = name.split(/\s+/);
  $("personEditor").hidden = false;
  $("personEditor").innerHTML = `
    <h4>New Person</h4>
    <div class="field-grid">
      <label class="field">First Name<input id="newPersonFirst" value="${firstLast[0] || ""}"></label>
      <label class="field">Last Name<input id="newPersonLast" value="${firstLast.slice(1).join(" ")}"></label>
      <label class="field">Preferred Name<input id="newPersonPreferred" value="${firstLast[0] || ""}"></label>
      <label class="field">Display Name<input id="newPersonDisplay" value="${name}"></label>
      <label class="field">Email<input id="newPersonEmail"></label>
      <label class="field">Phone<input id="newPersonPhone"></label>
      <label class="field wide">Administrative Notes<input id="newPersonNotes"></label>
    </div>
    <div class="inline-actions"><button id="saveNewPerson" class="save">Save Person</button><button id="cancelNewPerson">Cancel</button></div>
  `;
  $("saveNewPerson").onclick = async () => {
    const display = $("newPersonDisplay").value.trim() || `${$("newPersonFirst").value.trim()} ${$("newPersonLast").value.trim()}`.trim();
    const person = await api("/api/people", { method: "POST", body: JSON.stringify({
      first_name: $("newPersonFirst").value.trim(),
      last_name: $("newPersonLast").value.trim(),
      preferred_name: $("newPersonPreferred").value.trim(),
      display_name: display,
      billing_email: $("newPersonEmail").value.trim(),
      billing_phone: $("newPersonPhone").value.trim(),
      administrative_notes: $("newPersonNotes").value.trim()
    }) });
    state.participants.push({ person_id: person.person_id, display_name: person.display_name, is_primary: state.participants.length === 0 });
    $("personInput").value = "";
    $("personEditor").hidden = true;
    renderParticipantChips();
    renderRelationshipEditor(state.detail);
    markDirty("relationship");
  };
  $("cancelNewPerson").onclick = () => $("personEditor").hidden = true;
}

async function createAccountFromInput() {
  const name = $("accountInput").value.trim();
  if (!name) return;
  state.account = await findOrCreate("/api/accounts", "account_name", name, { account_name: name, account_type: name.toLowerCase().includes("family") ? "family" : "individual" });
  $("accountInput").value = state.account.account_name;
  markDirty("relationship");
}

async function createBillingFromInput() {
  const name = $("billingInput").value.trim();
  if (!name) return;
  const rows = await api(`/api/billing-parties?q=${encodeURIComponent(name)}`);
  const exact = rows.find(row => String(row.billing_name).toLowerCase() === name.toLowerCase());
  if (exact) {
    state.billingParty = exact;
    $("billingInput").value = state.billingParty.billing_name;
    markDirty("billing");
    return;
  }
  showBillingForm({ billing_name: name });
}

async function findOrCorrectPerson(name) {
  const rows = await api(`/api/people?q=${encodeURIComponent(name)}`);
  const exact = rows.find(row => String(row.display_name).toLowerCase() === name.toLowerCase());
  if (exact) return exact;
  const firstToken = name.split(/\s+/)[0].toLowerCase();
  const similar = rows.find(row => String(row.display_name || "").toLowerCase() === firstToken || row.similar_match);
  if (similar) {
    $("personWarning").innerHTML = `<div class="warning">Similar person already exists: <strong>${similar.display_name}</strong>. Use Edit Person to correct this person to ${name}, or press + again after clearing this warning to create a new person.</div>`;
    if (confirm(`Similar person already exists: ${similar.display_name}. Update that person to ${name} instead of creating a duplicate?`)) {
      return api(`/api/people/${similar.person_id}`, { method: "POST", body: JSON.stringify({ display_name: name }) });
    }
  }
  return api("/api/people", { method: "POST", body: JSON.stringify({ display_name: name }) });
}

function showPersonEditor(index) {
  const p = state.participants[index];
  $("personEditor").hidden = false;
  $("personEditor").innerHTML = `
    <h4>Edit Person</h4>
    <div class="field-grid">
      <label class="field">Display name<input id="editPersonDisplay" value="${p.display_name || p.participant_name || ""}"></label>
      <label class="field">Person code<input id="editPersonCode" value="${p.person_code || ""}"></label>
      <label class="field">Email<input id="editPersonEmail" value="${p.billing_email || ""}"></label>
      <label class="field">Phone<input id="editPersonPhone" value="${p.billing_phone || ""}"></label>
    </div>
    <div class="inline-actions"><button id="savePersonEdit" class="save">Save Person</button><button id="cancelPersonEdit">Cancel</button><button id="mergePersonBtn">Merge...</button></div>
  `;
  $("savePersonEdit").onclick = async () => {
    if (!p.person_id) {
      const display = $("editPersonDisplay").value.trim();
      state.participants[index] = { ...p, display_name: display, participant_name: display };
      markDirty("relationship");
    } else {
      const updated = await api(`/api/people/${p.person_id}`, { method: "POST", body: JSON.stringify({
        display_name: $("editPersonDisplay").value,
        person_code: $("editPersonCode").value || null,
        billing_email: $("editPersonEmail").value || null,
        billing_phone: $("editPersonPhone").value || null,
        active: true
      }) });
      state.participants[index] = { ...p, ...updated, display_name: updated.display_name };
      markSaved("relationship", "Person saved");
    }
    $("personEditor").hidden = true;
    renderParticipantChips();
  };
  $("cancelPersonEdit").onclick = () => $("personEditor").hidden = true;
  $("mergePersonBtn").onclick = async () => {
    const target = prompt("Merge this person into which existing display name?");
    if (!target || !p.person_id) return;
    const rows = await api(`/api/people?q=${encodeURIComponent(target)}`);
    const survivor = rows.find(row => row.display_name.toLowerCase() === target.toLowerCase()) || rows[0];
    if (!survivor) return alert("No matching survivor person found.");
    if (!confirm(`Merge ${p.display_name} into ${survivor.display_name}? The duplicate will be marked inactive, not deleted.`)) return;
    const merged = await api(`/api/people/${survivor.person_id}/merge`, { method: "POST", body: JSON.stringify({ duplicate_person_id: p.person_id, reason: "Merged from review UI" }) });
    state.participants[index] = { ...p, person_id: merged.person_id, display_name: merged.display_name };
    $("personEditor").hidden = true;
    renderParticipantChips();
    markSaved("relationship", "Person merged");
  };
}

function renderRelationshipEditor(data) {
  const members = data && data.account_members ? data.account_members : [];
  const accountName = state.account ? state.account.account_name : "No account selected";
  const billingName = state.billingParty ? state.billingParty.billing_name : "No billing party selected";
  $("relationshipEditor").innerHTML = `
    <h4>Relationship Editor</h4>
    <div class="kv">
      <label>Account</label><strong>${accountName}</strong>
      <label>Default payer</label><span>${billingName}</span>
    </div>
    <div class="member-list">
      ${(members.length ? members : state.participants).map((m, i) => `
        <div class="member-row">
          <span>${m.display_name || m.participant_name || ""}</span>
          <select data-role="${i}">
            ${optionSet(["primary","spouse","child","parent","family_member","couple_member","payer","other"], m.relationship_role || (i === 0 ? "primary" : "family_member"))}
          </select>
          <label><input type="radio" name="primaryMember" ${m.is_primary || i === 0 ? "checked" : ""}> Primary</label>
        </div>
      `).join("")}
    </div>
  `;
}

async function sameAsPrimaryParticipant() {
  const primary = state.participants[0];
  if (!primary) return alert("Add a participant first.");
  if (!primary.person_id) {
    const person = await findOrCorrectPerson(primary.display_name || primary.participant_name);
    primary.person_id = person.person_id;
    primary.display_name = person.display_name;
  }
  state.billingParty = await findOrCreate("/api/billing-parties", "billing_name", primary.display_name, {
    billing_name: primary.display_name,
    billing_party_type: "person",
    person_id: primary.person_id
  });
  $("billingInput").value = state.billingParty.billing_name;
  renderRelationshipEditor(state.detail);
}

function showAccountEditor() {
  if (!state.account) return alert("Select or create an account first.");
  const name = prompt("Account name", state.account.account_name);
  if (!name) return;
  const type = prompt("Account type: individual, household, family, couple, organization, other", state.account.account_type || "individual") || "individual";
  api(`/api/accounts/${state.account.account_id}`, { method: "POST", body: JSON.stringify({ account_name: name, account_type: type, default_billing_party_id: state.billingParty ? state.billingParty.billing_party_id : null }) })
    .then(updated => { state.account = updated; $("accountInput").value = updated.account_name; renderRelationshipEditor(state.detail); });
}

function showBillingEditor() {
  if (!state.billingParty) return showBillingForm({ billing_name: $("billingInput").value.trim() });
  showBillingForm(state.billingParty);
}

function showBillingForm(billing = {}) {
  $("billingEditor").hidden = false;
  $("billingEditor").innerHTML = `
    <h4>${billing.billing_party_id ? "Edit Bill To" : "New Bill-To Contact"}</h4>
    <div class="field-grid">
      <label class="field">Type<select id="billToType">${optionSet(["person","organization"], billing.billing_party_type || "person")}</select></label>
      <label class="field">Organization Name<input id="billToOrg" value="${billing.organization_name || ""}"></label>
      <label class="field">First Name<input id="billToFirst" value=""></label>
      <label class="field">Last Name<input id="billToLast" value=""></label>
      <label class="field">Display Name<input id="billToDisplay" value="${billing.billing_name || ""}"></label>
      <label class="field">Billing Email<input id="billToEmail" value="${billing.billing_email || ""}"></label>
      <label class="field">Billing Phone<input id="billToPhone" value="${billing.billing_phone || ""}"></label>
      <label class="field wide">Billing Address<input id="billToAddress" value="${billing.billing_address_line_1 || ""}"></label>
      <label class="field wide">Administrative Billing Notes<input id="billToNotes" value="${billing.administrative_notes || ""}"></label>
    </div>
    <div class="inline-actions"><button id="saveBillToForm" class="save">Save Bill To</button><button id="cancelBillToForm">Cancel</button></div>
  `;
  $("saveBillToForm").onclick = async () => {
    const display = $("billToDisplay").value.trim() || `${$("billToFirst").value.trim()} ${$("billToLast").value.trim()}`.trim() || $("billToOrg").value.trim();
    const payload = {
      billing_party_type: $("billToType").value,
      organization_name: $("billToOrg").value.trim(),
      billing_name: display,
      billing_email: $("billToEmail").value.trim(),
      billing_phone: $("billToPhone").value.trim(),
      billing_address_line_1: $("billToAddress").value.trim(),
      administrative_notes: $("billToNotes").value.trim()
    };
    state.billingParty = billing.billing_party_id
      ? await api(`/api/billing-parties/${billing.billing_party_id}`, { method: "POST", body: JSON.stringify(payload) })
      : await api("/api/billing-parties", { method: "POST", body: JSON.stringify(payload) });
    $("billingInput").value = state.billingParty.billing_name;
    $("billingEditor").hidden = true;
    markDirty("billing");
  };
  $("cancelBillToForm").onclick = () => $("billingEditor").hidden = true;
}

async function savePersonSection() {
  const primary = state.participants[0];
  if (!primary) return alert("Add or select a person first.");
  let payload = { person: { person_id: primary.person_id, display_name: primary.display_name || primary.participant_name } };
  const updated = await api(`/api/review/candidates/${state.selected}/save-person`, { method: "POST", body: JSON.stringify(payload) });
  const sessionDraft = collectSessionDraftValues();
  state.detail = updated;
  state.participants = updated.participants.map(participantState);
  renderInspector(updated);
  restoreSessionDraftValues(sessionDraft);
  markSaved("relationship", "Person saved. Suggestions refreshed.");
  await loadList();
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
  state.billingParty = updated.billing_party;
  state.participants = updated.participants.map(participantState);
  renderInspector(updated);
  restoreSessionDraftValues(sessionDraft);
  markSaved("relationship", "Relationship saved. Session suggestions refreshed.");
  await loadList();
}

async function saveBillingSection() {
  await resolveTypedSelections();
  const sessionDraft = collectSessionDraftValues();
  const updated = await api(`/api/review/candidates/${state.selected}/save-billing`, {
    method: "POST",
    body: JSON.stringify({ billing_party_id: state.billingParty ? state.billingParty.billing_party_id : null })
  });
  state.detail = updated;
  state.billingParty = updated.billing_party;
  renderInspector(updated);
  restoreSessionDraftValues(sessionDraft);
  markSaved("billing", "Bill to saved");
  await loadList();
}

async function saveSessionSection() {
  const updated = await api(`/api/review/candidates/${state.selected}/save-session`, { method: "POST", body: JSON.stringify(collectPayload()) });
  state.detail = updated;
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
    suggested_rate: $("suggestedRateInput")?.value || "",
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
  if ($("suggestedRateInput")) $("suggestedRateInput").value = values.suggested_rate;
  if ($("approvedRateInput")) $("approvedRateInput").value = values.approved_rate;
  if ($("paymentInput")) $("paymentInput").value = values.payment_status;
  if ($("billingTreatmentInput")) $("billingTreatmentInput").value = values.billing_treatment;
  if ($("billableInput")) $("billableInput").value = values.billable_status;
  if ($("overrideReasonInput")) $("overrideReasonInput").value = values.rate_override_reason;
}

async function resolveTypedSelections() {
  const accountName = $("accountInput").value.trim();
  if (accountName && (!state.account || state.account.account_name !== accountName)) {
    state.account = await findOrCreate("/api/accounts", "account_name", accountName, { account_name: accountName, account_type: accountName.toLowerCase().includes("family") ? "family" : "individual" });
  }
  const billingName = $("billingInput").value.trim();
  if (billingName && (!state.billingParty || state.billingParty.billing_name !== billingName)) {
    state.billingParty = await findOrCreate("/api/billing-parties", "billing_name", billingName, { billing_name: billingName, billing_party_type: "person" });
  }
}

async function mark(classification) {
  await api(`/api/review/candidates/${state.selected}/mark`, { method: "POST", body: JSON.stringify({ classification, reason: classification }) });
  await loadList();
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
    suggested_rate: $("suggestedRateInput").value,
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
function startRange(s) { return `${(s.start_at || "").split("T")[1]?.slice(0,5) || ""} - ${(s.end_at || "").split("T")[1]?.slice(0,5) || ""}`; }
function debounce(fn, ms) { let t; return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); }; }

["searchBox","statusFilter","serviceFilter","timeFilter","calendarFilter"].forEach(id => $(id).addEventListener("input", () => { state.offset = 0; loadList(); }));
$("prevPage").onclick = () => { state.offset = Math.max(0, state.offset - state.limit); loadList(); };
$("nextPage").onclick = () => { state.offset += state.limit; loadList(); };
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
document.getElementById("invoicesNav").onclick = (event) => {
  event.preventDefault();
  history.pushState({}, "", "/invoices");
  showInvoices();
};
document.getElementById("reviewNav").onclick = () => {
  location.hash = "";
  showReviewWorkbench();
};

function hideViews() {
  ["reviewWorkbench","rateCardView","clientsView","peopleView","invoicesView"].forEach(id => document.getElementById(id).hidden = true);
  ["reviewNav","rateCardNav","clientsNav","peopleNav","invoicesNav"].forEach(id => document.getElementById(id).classList.remove("active"));
}

function showRateCard() {
  hideViews();
  document.getElementById("rateCardView").hidden = false;
  document.getElementById("rateCardNav").classList.add("active");
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

async function showClients() {
  hideViews();
  document.getElementById("clientsView").hidden = false;
  document.getElementById("clientsNav").classList.add("active");
  $("pageTitle").textContent = "Clients & Accounts";
  $("pageSubtitle").textContent = "Relationship and shared billing records";
  await loadClients();
}

async function showPeople() {
  hideViews();
  document.getElementById("peopleView").hidden = false;
  document.getElementById("peopleNav").classList.add("active");
  $("pageTitle").textContent = "People";
  $("pageSubtitle").textContent = "Permanent people and billing relationships";
  await loadPeople();
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
document.getElementById("rateRuleForm").onsubmit = async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const payload = Object.fromEntries(form.entries());
  if (payload.applies_to === "account" && payload.applies_to_search) {
    const rows = await api(`/api/accounts?q=${encodeURIComponent(payload.applies_to_search)}`);
    const match = rows.find(row => row.account_name.toLowerCase() === payload.applies_to_search.toLowerCase()) || rows[0];
    if (match) payload.client_account_id = match.account_id;
  }
  if (payload.applies_to === "person" && payload.applies_to_search) {
    const rows = await api(`/api/people?q=${encodeURIComponent(payload.applies_to_search)}`);
    const match = rows.find(row => row.display_name.toLowerCase() === payload.applies_to_search.toLowerCase()) || rows[0];
    if (match) payload.person_id = match.person_id;
  }
  await api("/api/rate-rules", { method: "POST", body: JSON.stringify(payload) });
  event.currentTarget.reset();
  event.currentTarget.effective_from.value = "2026-01-01";
  await loadRateRules();
};
document.getElementById("rateAppliesSearch").addEventListener("input", debounce(async e => {
  const mode = $("rateAppliesTo").value;
  const rows = mode === "person"
    ? await api(`/api/people?q=${encodeURIComponent(e.target.value)}`)
    : await api(`/api/accounts?q=${encodeURIComponent(e.target.value)}`);
  fillDatalist("rateAppliesList", rows, mode === "person" ? "display_name" : "account_name");
}, 160));

async function loadRateRules() {
  const rows = await api("/api/rate-rules");
  document.getElementById("rateRows").innerHTML = rows.map(row => `
    <tr>
      <td>$${row.amount}</td>
      <td>${row.duration_minutes || "Any"}</td>
      <td>${serviceLabel(row.service_mode || row.rate_group || "Any")}</td>
      <td>${timeLabel(row.time_category)}</td>
      <td>${row.participant_names || row.account_name || row.display_name || "Everyone"}</td>
      <td>${row.effective_from}</td>
      <td>${ruleExplanation(row)}</td>
    </tr>
  `).join("");
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
  document.querySelectorAll("#clientRows tr").forEach(row => row.onclick = () => openAccountRecord(row.dataset.account));
}

async function openAccountRecord(accountId) {
  if (!accountId) return alert("Select or create an account first.");
  state.returnCandidate = state.selected;
  const data = await api(`/api/accounts/${accountId}`);
  $("accountRecord").innerHTML = `
    ${state.returnCandidate ? `<a href="#" class="return-link" id="returnFromAccount">← Return to ${fmt(state.detail?.session?.raw_calendar_title)} — ${fmt(state.detail?.session?.session_date)}</a>` : ""}
    <h3>${fmt(data.account.account_name)}</h3>
    <div class="meta"><span>${fmt(data.account.account_code)}</span><span>${fmt(data.account.account_type)}</span><span>${data.account.active ? "Active" : "Inactive"}</span></div>
    <div class="record-actions"><button id="editAccountRecord" class="save">Save Account</button><button id="addMemberRecord">Add Member</button></div>
    <div class="field-grid">
      <label class="field">Account Name<input id="recordAccountName" value="${fmt(data.account.account_name)}"></label>
      <label class="field">Type<select id="recordAccountType">${optionSet(["individual","household","family","couple","organization","other"], data.account.account_type)}</select></label>
      <label class="field wide">Admin Notes<input id="recordAccountNotes" value="${data.account.administrative_notes || ""}"></label>
    </div>
    <h4>Members</h4><div class="compact-list">${data.members.map(m => `<div><span>${fmt(m.display_name)} ${m.is_primary ? "(Primary)" : ""}</span><span>${fmt(m.relationship_role)}</span></div>`).join("") || "<span class='readonly-note'>No members yet.</span>"}</div>
    <h4>Billing</h4><div class="kv"><label>Default payer</label><span>${fmt(data.billing_party?.billing_name)}</span><label>Email</label><span>${fmt(data.billing_party?.billing_email)}</span><label>Phone</label><span>${fmt(data.billing_party?.billing_phone)}</span></div>
    <h4>Rates</h4><div class="compact-list">${data.rates.map(r => `<div><span>${money(centString(r.amount_cents))} ${fmt(r.duration_minutes || "Any")} min</span><span>${r.active ? "Active" : "Inactive"}</span></div>`).join("") || "<span class='readonly-note'>No account-specific rates.</span>"}</div>
    <h4>Session History</h4><div class="compact-list">${data.sessions.slice(0, 8).map(s => `<div><span>${fmt(s.session_date)} ${fmt(s.duration_minutes)} min ${serviceLabel(s.service_mode)} ${timeLabel(s.time_category)}</span><span>${money(centString(s.approved_rate_cents))} ${fmt(s.approved_rate_source || s.rate_source)}</span></div>`).join("") || "<span class='readonly-note'>No sessions yet.</span>"}</div>
    <h4>Active Rate Exceptions</h4><div class="compact-list">${(data.active_rate_exceptions || []).map(r => `<div><span>${fmt(r.effective_from)} ${fmt(r.duration_minutes || "Any")} min ${serviceLabel(r.service_mode || r.rate_group || "Any")}</span><span>${money(centString(r.amount_cents))}</span></div>`).join("") || "<span class='readonly-note'>No person-specific rate exceptions.</span>"}</div>
    <h4>Shared Rate Exceptions</h4><div class="compact-list">${(data.joint_rate_exceptions || []).map(r => `<div><span>${fmt(r.participant_names)} ${fmt(r.duration_minutes || "Any")} min</span><span>${money(centString(r.amount_cents))}</span></div>`).join("") || "<span class='readonly-note'>No joint-session exceptions.</span>"}</div>
    <h4>Aliases</h4><div class="compact-list">${data.aliases.map(a => `<div><span>${fmt(a.raw_alias)}</span><span>${fmt(a.classification)}</span></div>`).join("") || "<span class='readonly-note'>No aliases yet.</span>"}</div>
  `;
  if ($("returnFromAccount")) $("returnFromAccount").onclick = (event) => { event.preventDefault(); location.hash = ""; showReviewWorkbench(); };
  $("editAccountRecord").onclick = async () => {
    await api(`/api/accounts/${accountId}`, { method: "POST", body: JSON.stringify({ account_name: $("recordAccountName").value, account_type: $("recordAccountType").value, administrative_notes: $("recordAccountNotes").value }) });
    await openAccountRecord(accountId);
    await loadClients();
  };
  if (location.hash !== "#clients") {
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
  document.querySelectorAll("#peopleRows tr").forEach(row => row.onclick = () => openPersonRecord(row.dataset.person));
}

function openPrimaryPersonRecord() {
  const primary = state.participants.find(p => p.is_primary && p.person_id) || state.participants.find(p => p.person_id);
  if (!primary) return alert("Select or create a person first.");
  openPersonRecord(primary.person_id);
}

async function openPersonRecord(personId) {
  state.returnCandidate = state.selected;
  const data = await api(`/api/people/${personId}`);
  $("personRecord").innerHTML = `
    ${state.returnCandidate ? `<a href="#" class="return-link" id="returnFromPerson">← Return to ${fmt(state.detail?.session?.raw_calendar_title)} — ${fmt(state.detail?.session?.session_date)}</a>` : ""}
    <h3>${fmt(data.person.display_name)}</h3>
    <div class="meta"><span>${fmt(data.person.person_code)}</span><span>${fmt(data.person.active_status)}</span></div>
    <div class="field-grid">
      <label class="field">First Name<input id="recordFirstName" value="${data.person.first_name || ""}"></label>
      <label class="field">Last Name<input id="recordLastName" value="${data.person.last_name || ""}"></label>
      <label class="field">Preferred Name<input id="recordPreferredName" value="${data.person.preferred_name || ""}"></label>
      <label class="field">Display Name<input id="recordDisplayName" value="${data.person.display_name || ""}"></label>
      <label class="field">Email<input id="recordPersonEmail" value="${data.person.billing_email || ""}"></label>
      <label class="field">Phone<input id="recordPersonPhone" value="${data.person.billing_phone || ""}"></label>
      <label class="field wide">Admin Notes<input id="recordPersonNotes" value="${data.person.administrative_notes || ""}"></label>
    </div>
    <div class="record-actions"><button id="savePersonRecord" class="save">Save Person</button></div>
    <h4>Accounts</h4><div class="compact-list">${data.accounts.map(a => `<div><span>${fmt(a.account_name)}</span><span>${fmt(a.relationship_role)}</span></div>`).join("") || "<span class='readonly-note'>No accounts yet.</span>"}</div>
    <h4>Billing Relationships</h4><div class="compact-list">${data.billing_parties.map(b => `<div><span>${fmt(b.billing_name)}</span><span>${fmt(b.billing_email)}</span></div>`).join("") || "<span class='readonly-note'>No billing links yet.</span>"}</div>
    <h4>Session History</h4><div class="compact-list">${data.sessions.slice(0, 8).map(s => `<div><span>${fmt(s.session_date)} ${fmt(s.raw_calendar_title)}</span><span>${money(centString(s.approved_rate_cents))}</span></div>`).join("") || "<span class='readonly-note'>No sessions yet.</span>"}</div>
    <h4>Aliases</h4><div class="compact-list">${data.aliases.map(a => `<div><span>${fmt(a.raw_alias)}</span><span>${fmt(a.classification)}</span></div>`).join("") || "<span class='readonly-note'>No aliases yet.</span>"}</div>
  `;
  if ($("returnFromPerson")) $("returnFromPerson").onclick = (event) => { event.preventDefault(); location.hash = ""; showReviewWorkbench(); };
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
  if (location.hash !== "#people") {
    location.hash = "people";
    showPeople();
  }
}
["clientSearch","peopleSearch"].forEach(id => $(id).addEventListener("input", debounce(() => id === "clientSearch" ? loadClients() : loadPeople(), 180)));
$("newAccountBtn").onclick = async () => {
  const name = prompt("Account name");
  if (!name) return;
  const account = await api("/api/accounts", { method: "POST", body: JSON.stringify({ account_name: name, account_type: "individual" }) });
  await loadClients();
  await openAccountRecord(account.account_id);
};
$("newPersonBtn").onclick = async () => {
  const name = prompt("Person display name");
  if (!name) return;
  const person = await api("/api/people", { method: "POST", body: JSON.stringify({ display_name: name }) });
  await loadPeople();
  await openPersonRecord(person.person_id);
};

loadList();
if (location.hash === "#rate-card") showRateCard();
if (location.hash === "#clients" || location.pathname === "/clients") showClients();
if (location.hash === "#people" || location.pathname === "/people") showPeople();
if (location.pathname === "/invoices") showInvoices();
window.addEventListener("beforeunload", event => {
  if (state.dirty.size) {
    event.preventDefault();
    event.returnValue = "";
  }
});

function ruleExplanation(row) {
  const scope = row.participant_names ? row.participant_names : row.account_name ? `account ${row.account_name}` : row.display_name ? `person ${row.display_name}` : "everyone";
  return `Applies to ${scope}; ${row.duration_minutes || "any"} minutes; ${serviceLabel(row.service_mode || row.rate_group || "any")}; ${timeLabel(row.time_category)}.`;
}

function rateSourceDescription(session, participants = []) {
  const names = participants.map(p => p.display_name || p.participant_name).filter(Boolean);
  const first = names[0] || "Participant";
  const joined = names.join(" + ");
  const source = session.approved_rate_source || session.rate_source;
  if (source === "person_exception") return `${first} exception`;
  if (source === "participant_combination_exception") return `${joined} joint-session exception`;
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
