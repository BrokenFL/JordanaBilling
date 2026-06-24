(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const busy = new Set();
  const norm = (value) => String(value || "").trim().toLowerCase().replace(/\s+/g, " ");

  function simplifyStatusFilter() {
    const filter = $("statusFilter");
    if (!filter) return;
    const current = ["approved", "excluded"].includes(filter.value) ? filter.value : "needs_review";
    filter.innerHTML = [
      '<option value="needs_review">Needs Review</option>',
      '<option value="approved">Approved</option>',
      '<option value="excluded">Excluded</option>'
    ].join("");
    filter.value = current;
  }

  function polish() {
    const billable = $("billableInput");
    if (billable) billable.closest("label.field")?.remove();
    if ($("saveSessionBtn")) $("saveSessionBtn").textContent = "Save Session";
    if ($("approveBtn")) $("approveBtn").textContent = "Final Approve Session";
    if ($("sessionRateHelp")) {
      $("sessionRateHelp").textContent = "This rate applies only to this session unless you save it as a future default.";
    }
  }

  const originalRenderInspector = renderInspector;
  renderInspector = (data) => {
    if (data?.session?.review_status === "approved") {
      data.session.authority_score = 100;
      data.session.authority_reasons = ["Human approved"];
    }
    originalRenderInspector(data);
    polish();
  };

  loadList = async () => {
    const selectedStatus = $("statusFilter")?.value || "needs_review";
    const serverStatus = selectedStatus === "needs_review" ? "" : selectedStatus;
    const params = new URLSearchParams({
      q: $("searchBox")?.value || "",
      review_status: serverStatus,
      billing_session_type: $("serviceFilter")?.value || "",
      time_category: $("timeFilter")?.value || "",
      calendar_filter: $("calendarFilter")?.value || "",
      limit: state.limit,
      offset: state.offset
    });

    const data = await api(`/api/review/candidates?${params}`);
    let items = data.items || [];
    if (selectedStatus === "needs_review") {
      items = items.filter((item) => !["approved", "excluded"].includes(item.status));
    }
    if (selectedStatus === "approved") {
      items = items.map((item) => ({ ...item, authority_score: 100, authority_reasons: ["Human approved"] }));
    }

    state.items = items;
    renderStatus(data.status);
    renderRows(items, items.length);
    if (!items.some((item) => item.candidate_id === state.selected)) state.selected = null;
    if (!state.selected && items.length) await selectCandidate(items[0].candidate_id);
    if (!items.length) $("inspector").innerHTML = '<div class="empty-state">No sessions in this view.</div>';
  };

  async function exactPerson(name) {
    if (!name) return null;
    const rows = await api(`/api/people?q=${encodeURIComponent(name)}`);
    return rows.find((row) => norm(row.display_name) === norm(name)) || null;
  }

  async function autoConfirmClient(data) {
    const candidateId = state.selected;
    const participants = data?.participants || [];
    const readiness = data?.readiness || {};
    if (!candidateId || busy.has(candidateId) || participants.length !== 1) return;
    if (readiness.clients_ready && readiness.billing_ready) return;

    const participant = participants[0];
    const name = participant.display_name || participant.participant_name || "";
    const person = participant.person_id ? participant : await exactPerson(name);
    const personId = person?.person_id;
    if (!personId) return;

    busy.add(candidateId);
    try {
      let updated = data;
      if (!readiness.clients_ready) {
        updated = await api(`/api/review/candidates/${candidateId}/save-relationship`, {
          method: "POST",
          body: JSON.stringify({
            participants: [{ ...participantState(participant), person_id: personId, is_proposed: false, is_primary: true }],
            account_id: data.account?.account_id || null,
            primary_person_id: personId,
            billing_party_id: data.effective_billing_party?.billing_party_id || data.billing_party?.billing_party_id || null
          })
        });
      }
      if (!(updated.readiness || {}).billing_ready) {
        updated = await api(`/api/review/candidates/${candidateId}/save-billing`, {
          method: "POST",
          body: JSON.stringify({ bill_to_person_id: personId })
        });
      }
      state.detail = updated;
      state.participants = (updated.participants || []).map(participantState);
      state.account = updated.account;
      state.billingParty = updated.billing_party || updated.effective_billing_party;
      renderInspector(updated);
      await loadList();
    } finally {
      busy.delete(candidateId);
    }
  }

  const originalSelectCandidate = selectCandidate;
  selectCandidate = async (candidateId) => {
    await originalSelectCandidate(candidateId);
    if (state.detail?.session?.review_status === "approved") {
      state.detail.session.authority_score = 100;
      renderInspector(state.detail);
      return;
    }
    await autoConfirmClient(state.detail);
  };

  simplifyStatusFilter();
  polish();
  loadList();
})();