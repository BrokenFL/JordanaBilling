(function () {
  "use strict";
  const $ = id => document.getElementById(id);
  const labels = { matched: "Matched", difference: "Values differ", possible_match: "Possible match", ambiguous: "Multiple possible matches", duplicate: "Possible duplicate", calendar_only: "Calendar only", session_only: "Approved session only" };
  let initialized = false;
  let result = null;
  let sourceText = "";
  let sourceName = "";
  let busy = false;

  function previousMonth() {
    const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", year: "numeric", month: "2-digit" }).formatToParts(new Date());
    let year = Number(parts.find(part => part.type === "year").value);
    let month = Number(parts.find(part => part.type === "month").value) - 1;
    if (!month) { month = 12; year--; }
    return `${year}-${String(month).padStart(2, "0")}`;
  }

  function clearResult() {
    result = null;
    sourceText = "";
    sourceName = "";
    $("monthlyCalendarResults").hidden = true;
    $("monthlyCalendarRows").replaceChildren();
    $("monthlyCalendarError").hidden = true;
    $("monthlyCalendarMessage").textContent = "";
  }

  function download(text, filename) {
    const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function textBlock(parent, value, strong = false) {
    const node = document.createElement(strong ? "strong" : "div");
    node.textContent = value;
    parent.appendChild(node);
  }

  function renderRows() {
    const target = $("monthlyCalendarRows");
    target.replaceChildren();
    if (!result) return;
    const filter = $("monthlyCalendarFilter").value;
    const rows = result.rows.filter(row => filter === "all" || (filter === "matched" ? row.status === "matched" : row.status !== "matched"));
    for (const row of rows) {
      const tr = document.createElement("tr");
      tr.className = `calendar-result-${row.status}`;
      for (let i = 0; i < 5; i++) tr.appendChild(document.createElement("td"));
      const [date, status, calendar, approved, details] = tr.children;
      date.textContent = (row.calendar || row.session).date;
      status.textContent = labels[row.status] || row.status;
      if (row.calendar) {
        textBlock(calendar, row.calendar.title || "Untitled event", true);
        textBlock(calendar, `${row.calendar.start_time} · ${row.calendar.duration_minutes} min`);
        textBlock(calendar, row.calendar.calendar || "Unnamed calendar");
      } else calendar.textContent = "No calendar match";
      if (row.session) {
        textBlock(approved, row.session.participants || "Unspecified participants", true);
        textBlock(approved, `${row.session.start_time} · ${row.session.duration_minutes} min · $${row.session.rate}`);
        textBlock(approved, `${row.session.outcome} · Invoice: ${row.session.invoice_status} · Payment: ${row.session.payment_status}`);
        if (row.session.bill_to) textBlock(approved, `Bill To: ${row.session.bill_to}`);
      } else approved.textContent = "No approved session match";
      details.textContent = row.differences.join("; ") || "Calendar and approved session values agree";
      target.appendChild(tr);
    }
    if (!rows.length) {
      const tr = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 5;
      cell.textContent = filter === "review" ? "No differences in the supplied calendar export need review." : "No appointments in this view.";
      tr.appendChild(cell);
      target.appendChild(tr);
    }
  }

  async function compare(event) {
    event.preventDefault();
    if (busy) return;
    clearResult();
    const file = $("monthlyCalendarFile").files[0];
    const month = $("monthlyCalendarMonth").value;
    if (!file || !month) return;
    busy = true;
    const controls = [$("monthlyCalendarFile"), $("monthlyCalendarMonth"), $("monthlyCalendarCompare")];
    controls.forEach(control => { control.disabled = true; });
    $("monthlyCalendarMessage").textContent = "Comparing calendar appointments with approved sessions…";
    try {
      if (file.size > 800000) throw new Error("Calendar CSV is too large. Export one month at a time.");
      const csvText = await file.text();
      const body = JSON.stringify({ month, csv_text: csvText });
      if (new TextEncoder().encode(body).length > 1048576) throw new Error("Calendar CSV is too large. Export one month at a time.");
      const data = await window.JordanaAPI.api("/api/reports/calendar-review", { method: "POST", body });
      result = data;
      sourceText = csvText;
      sourceName = file.name;
      const summary = $("monthlyCalendarSummary");
      summary.replaceChildren();
      for (const [label, count] of [["Timed calendar events", data.calendar_count], ["Approved sessions", data.approved_count], ["Matched", data.counts.matched || 0], ["Rows for manual review", data.review_count]]) {
        const card = document.createElement("div");
        textBlock(card, String(count), true);
        textBlock(card, label);
        summary.appendChild(card);
      }
      const captured = data.metadata.exported_at ? new Date(data.metadata.exported_at).toLocaleString("en-US", { timeZone: "America/New_York" }) : "No event rows in this export";
      $("monthlyCalendarSource").textContent = `${sourceName} · Month ${data.month} · Exported ${captured}${data.metadata.excluded_all_day ? ` · ${data.metadata.excluded_all_day} all-day events excluded` : ""}${data.metadata.outside_month ? ` · ${data.metadata.outside_month} events outside the Eastern month excluded` : ""}. Times shown in Eastern Time.`;
      $("monthlyCalendarFilter").value = "review";
      $("monthlyCalendarResults").hidden = false;
      $("monthlyCalendarMessage").textContent = `Comparison ready for ${data.month}.`;
      renderRows();
    } catch (error) {
      $("monthlyCalendarMessage").textContent = "";
      $("monthlyCalendarError").textContent = window.JordanaAPI.sanitizeUiErrorMessage(error.message, "Unable to compare the calendar CSV. Export the month again and try again.");
      $("monthlyCalendarError").hidden = false;
    } finally {
      busy = false;
      controls.forEach(control => { control.disabled = false; });
    }
  }

  function initialize() {
    if (initialized || !$("monthlyCalendarForm")) return;
    initialized = true;
    $("monthlyCalendarMonth").value = previousMonth();
    $("monthlyCalendarForm").addEventListener("submit", compare);
    $("monthlyCalendarMonth").addEventListener("change", clearResult);
    $("monthlyCalendarFile").addEventListener("change", clearResult);
    $("monthlyCalendarFilter").addEventListener("change", renderRows);
    $("monthlyCalendarDownload").onclick = () => { if (result) download(sourceText, sourceName); };
    $("monthlyApprovedDownload").onclick = () => { if (result) download(result.approved_csv, `Jordana_Approved_Session_Log_${result.month}.csv`); };
    $("monthlyComparisonDownload").onclick = () => { if (result) download(result.comparison_csv, `Jordana_Calendar_Comparison_${result.month}.csv`); };
  }
  window.JordanaMonthlyCalendarReview = { initialize };
})();
