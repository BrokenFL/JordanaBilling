(function () {
  "use strict";

  const WRITE_TOKEN = window.__JORDANA_BOOTSTRAP__?.writeToken || "";

  async function api(path, options = {}) {
    const method = (options.method || "GET").toUpperCase();
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
      headers["X-Jordana-Write-Token"] = WRITE_TOKEN;
    }
    const res = await fetch(path, { ...options, method, headers });
    const json = await res.json();
    if (!res.ok || json.ok === false) throw new Error(json.error || "Request failed");
    return json;
  }

  function sanitizeUiErrorMessage(message, fallback = "An unexpected error occurred.") {
    const raw = String(message || "").trim();
    if (!raw) return fallback;
    if (raw.includes("/") || raw.toLowerCase().includes("traceback") || raw.toLowerCase().includes("select ")) {
      return fallback;
    }
    return raw;
  }

  window.JordanaAPI = { api, sanitizeUiErrorMessage };
})();
