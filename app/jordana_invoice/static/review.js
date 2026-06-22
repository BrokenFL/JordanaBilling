const state = { items: [], selected: null, offset: 0, limit: 25, participants: [], account: null, billingParty: null };

const $ = (id) => document.getElementById(id);
const fmt = (v) => v || "-";
const money = (v) => v ? `$${v}` : "—";
const serviceLabel = (v) => ({phone:"Phone", facetime:"FaceTime", office:"Office", house_call:"House Call", unknown:"Unknown"}[v] || v || "Unknown");
const timeLabel = (v) => ({standard:"Standard", evening:"Evening", weekend:"Weekend", weekend_evening:"Weekend + Evening"}[v] || v || "Standard");

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
    service_mode: $("serviceFilter").value,
    time_category: $("timeFilter").value,
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
      <td><span class="dot ${statusColor(item.status, item.classification)}"></span></td>
      <td>${fmt(item.date)}</td>
      <td>${fmt(item.time)}</td>
      <td>${fmt(item.raw_title)}</td>
      <td><span class="primary">${fmt(item.suggested_client)}</span><span class="secondary">${fmt(item.account_name)}</span></td>
      <td>${fmt(item.duration_minutes)}</td>
      <td>${serviceLabel(item.service_mode)}</td>
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
  state.participants = data.participants.map(p => ({ person_id: p.person_id, display_name: p.display_name || p.participant_name, participant_name: p.participant_name, is_primary: !!p.is_primary }));
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
        <div class="meta"><span>${fmt(s.session_date)}</span><span>${fmt(startRange(s))}</span><span>${fmt(s.duration_minutes)} min</span></div>
      </div>
      <div><span class="badge">${fmt(s.review_status).replaceAll("_", " ")}</span><div class="confidence ${Math.round((s.confidence || 0) * 100) >= 90 ? "good" : "low"}">Confidence: ${Math.round((s.confidence || 0) * 100)}%</div></div>
    </div>

    <section class="section">
      <h3>Calendar Evidence</h3>
      <div class="kv">
        <label>Raw Title</label><strong>${fmt(s.raw_calendar_title || s.title)}</strong>
        <label>Calendar</label><span>${fmt(s.calendar_name)}</span>
        <label>Original Start</label><span>${fmt(s.start_at)}</span>
        <label>Original End</label><span>${fmt(s.end_at)}</span>
        <label>Calendar Duration</label><span>${fmt(s.calendar_duration_minutes || s.duration_minutes)} minutes</span>
        <label>Notes</label><span>${fmt(s.notes)}</span>
        <label>Captured</label><span>${fmt(s.captured_at)}</span>
      </div>
    </section>

    <section class="section">
      <h3>Suggested Interpretation</h3>
      <label class="field wide">Who attended?</label>
      <div class="help">Select everyone who participated in this session. This may be one person or multiple people, such as Fred Colin + Bobsey Colin.</div>
      <div class="chips" id="participantChips"></div>
      <div class="combobox"><input id="personInput" placeholder="Search, create, or correct a person" list="peopleList"><button class="mini" id="addPerson">+</button></div>
      <datalist id="peopleList"></datalist>
      <div id="personWarning"></div>
      <div id="personEditor" class="drawer" hidden></div>
      <div class="field-grid">
        <label class="field wide">Client / Family Account
          <span class="help">The individual, couple, household, or family group this session belongs to.</span>
          <div class="combobox"><input id="accountInput" placeholder="Search or create a client/family account" value="${data.account ? data.account.account_name : ""}" list="accountList"><button class="mini" id="addAccount">+</button></div>
        </label>
        <datalist id="accountList"></datalist>
        <label class="field wide">Who should be billed?
          <span class="help">The person or organization responsible for payment. This does not have to be someone who attended.</span>
          <div class="combobox"><input id="billingInput" placeholder="Search or create the person or organization to bill" value="${data.billing_party ? data.billing_party.billing_name : ""}" list="billingList"><button class="mini" id="addBilling">+</button></div>
        </label>
        <datalist id="billingList"></datalist>
      </div>
      <div class="inline-actions">
        <button id="sameAsPrimary">Same as primary participant</button>
        <button id="editAccount">Edit Account</button>
        <button id="editBilling">Edit Billing Party</button>
      </div>
      <div id="relationshipEditor" class="drawer"></div>
      <div class="hint">Suggestion reasons: ${(safeList(s.review_reasons).join(" ") || s.explanation || "Calendar title matched the parser pattern.")}</div>
    </section>

    <section class="section">
      <h3>Session Details</h3>
      <div class="field-grid">
        <label class="field">Duration (min)<input id="durationInput" type="number" value="${s.approved_duration_minutes || s.duration_minutes || ""}"></label>
        <label class="field">Service Mode<select id="serviceInput">${optionSet(["phone","facetime","office","house_call","unknown"], s.service_mode)}</select></label>
        <label class="field">Time Category<select id="timeCategoryInput">${optionSet(["standard","evening","weekend","weekend_evening"], s.time_category)}</select></label>
        <label class="field">Suggested Rate<span class="help">Calculated from the Rate Card. Jordana can override it.</span><input id="suggestedRateInput" value="${centString(s.suggested_rate_cents)}"></label>
        <label class="field">Approved Rate<span class="help">The final amount that will be used for this session.</span><input id="approvedRateInput" value="${centString(s.approved_rate_cents)}"></label>
        <label class="field">Payment Status<span class="help">Whether payment has already been received.</span><select id="paymentInput">${optionSet(["unresolved","unpaid","partially_paid","paid","waived","not_billable"], s.payment_status)}</select></label>
        <label class="field wide">Override Reason<input id="overrideReasonInput" value="${s.rate_override_reason || ""}"></label>
      </div>
    </section>

    <section class="section">
      <h3>Review Checklist</h3>
      <div class="checklist">${data.checklist.map(c => `<div class="check ${c.resolved ? "done" : ""}"><span></span><label>${c.label}</label></div>`).join("")}</div>
    </section>

    <div class="actions">
      ${isSession ? '<button class="approve" id="approveBtn">Approve</button><button class="save" id="saveBtn">Save Changes</button>' : ""}
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
  if ($("saveBtn")) $("saveBtn").onclick = () => save(false);
  if ($("approveBtn")) $("approveBtn").onclick = () => save(true);
  $("sameAsPrimary").onclick = sameAsPrimaryParticipant;
  $("editAccount").onclick = showAccountEditor;
  $("editBilling").onclick = showBillingEditor;
  $("personalBtn").onclick = () => mark("personal");
  $("duplicateBtn").onclick = () => mark("duplicate");
  $("excludeBtn").onclick = () => mark("nonbillable");
}

function renderParticipantChips() {
  $("participantChips").innerHTML = state.participants.map((p, i) => `<span class="chip">${p.display_name || p.participant_name}${i === 0 ? " (Primary)" : ""}<button data-edit="${i}">Edit</button><button data-i="${i}">×</button></span>`).join("");
  document.querySelectorAll("#participantChips button[data-i]").forEach(btn => btn.onclick = () => { state.participants.splice(Number(btn.dataset.i), 1); renderParticipantChips(); renderRelationshipEditor(state.detail); });
  document.querySelectorAll("#participantChips button[data-edit]").forEach(btn => btn.onclick = () => showPersonEditor(Number(btn.dataset.edit)));
}

async function createPersonFromInput() {
  const name = $("personInput").value.trim();
  if (!name) return;
  const person = await findOrCorrectPerson(name);
  state.participants.push({ person_id: person.person_id, display_name: person.display_name, is_primary: state.participants.length === 0 });
  $("personInput").value = "";
  renderParticipantChips();
  renderRelationshipEditor(state.detail);
}

async function createAccountFromInput() {
  const name = $("accountInput").value.trim();
  if (!name) return;
  state.account = await findOrCreate("/api/accounts", "account_name", name, { account_name: name, account_type: name.toLowerCase().includes("family") ? "family" : "individual" });
  $("accountInput").value = state.account.account_name;
}

async function createBillingFromInput() {
  const name = $("billingInput").value.trim();
  if (!name) return;
  state.billingParty = await findOrCreate("/api/billing-parties", "billing_name", name, { billing_name: name, billing_party_type: "person" });
  $("billingInput").value = state.billingParty.billing_name;
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
      const created = await api("/api/people", { method: "POST", body: JSON.stringify({ display_name: $("editPersonDisplay").value }) });
      state.participants[index] = { ...p, person_id: created.person_id, display_name: created.display_name };
    } else {
      const updated = await api(`/api/people/${p.person_id}`, { method: "POST", body: JSON.stringify({
        display_name: $("editPersonDisplay").value,
        person_code: $("editPersonCode").value || null,
        billing_email: $("editPersonEmail").value || null,
        billing_phone: $("editPersonPhone").value || null,
        active: true
      }) });
      state.participants[index] = { ...p, ...updated, display_name: updated.display_name };
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
  if (!state.billingParty) return alert("Select or create a billing party first.");
  const name = prompt("Billing name", state.billingParty.billing_name);
  if (!name) return;
  const email = prompt("Billing email", state.billingParty.billing_email || "") || "";
  api(`/api/billing-parties/${state.billingParty.billing_party_id}`, { method: "POST", body: JSON.stringify({ billing_name: name, billing_email: email }) })
    .then(updated => { state.billingParty = updated; $("billingInput").value = updated.billing_name; renderRelationshipEditor(state.detail); });
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
  const roleSelects = [...document.querySelectorAll("[data-role]")];
  roleSelects.forEach(select => {
    const index = Number(select.dataset.role);
    if (state.participants[index]) {
      state.participants[index].relationship_role = select.value;
    }
  });
  return {
    participants: state.participants,
    account_id: state.account ? state.account.account_id : null,
    billing_party_id: state.billingParty ? state.billingParty.billing_party_id : null,
    approved_duration_minutes: $("durationInput").value,
    service_mode: $("serviceInput").value,
    time_category: $("timeCategoryInput").value,
    suggested_rate: $("suggestedRateInput").value,
    approved_rate: $("approvedRateInput").value,
    payment_status: $("paymentInput").value,
    rate_override_reason: $("overrideReasonInput").value
  };
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
function centString(cents) { return cents ? (Number(cents) / 100).toFixed(2) : ""; }
function safeList(raw) { try { return Array.isArray(raw) ? raw : JSON.parse(raw || "[]"); } catch { return []; } }
function startRange(s) { return `${(s.start_at || "").split("T")[1]?.slice(0,5) || ""} - ${(s.end_at || "").split("T")[1]?.slice(0,5) || ""}`; }
function debounce(fn, ms) { let t; return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); }; }

["searchBox","statusFilter","serviceFilter","timeFilter"].forEach(id => $(id).addEventListener("input", () => { state.offset = 0; loadList(); }));
$("prevPage").onclick = () => { state.offset = Math.max(0, state.offset - state.limit); loadList(); };
$("nextPage").onclick = () => { state.offset += state.limit; loadList(); };
document.getElementById("rateCardNav").onclick = (event) => {
  event.preventDefault();
  location.hash = "rate-card";
  showRateCard();
};
document.getElementById("reviewNav").onclick = () => {
  location.hash = "";
  showReviewWorkbench();
};

function showRateCard() {
  document.getElementById("reviewWorkbench").hidden = true;
  document.getElementById("rateCardView").hidden = false;
  document.getElementById("reviewNav").classList.remove("active");
  document.getElementById("rateCardNav").classList.add("active");
  loadRateRules();
}

function showReviewWorkbench() {
  document.getElementById("reviewWorkbench").hidden = false;
  document.getElementById("rateCardView").hidden = true;
  document.getElementById("reviewNav").classList.add("active");
  document.getElementById("rateCardNav").classList.remove("active");
}
document.getElementById("rateRuleForm").onsubmit = async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  await api("/api/rate-rules", { method: "POST", body: JSON.stringify(Object.fromEntries(form.entries())) });
  event.currentTarget.reset();
  event.currentTarget.effective_from.value = "2026-01-01";
  await loadRateRules();
};

async function loadRateRules() {
  const rows = await api("/api/rate-rules");
  document.getElementById("rateRows").innerHTML = rows.map(row => `
    <tr>
      <td>$${row.amount}</td>
      <td>${row.duration_minutes || "Any"}</td>
      <td>${serviceLabel(row.service_mode || row.rate_group || "Any")}</td>
      <td>${timeLabel(row.time_category)}</td>
      <td>${row.account_name || "Global"}</td>
      <td>${row.display_name || "Any"}</td>
      <td>${row.effective_from}</td>
    </tr>
  `).join("");
}
loadList();
if (location.hash === "#rate-card") showRateCard();
