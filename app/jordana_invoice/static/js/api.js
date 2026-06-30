(function () {
  "use strict";

  class ApiError extends Error {
    constructor(message, { status = null, body = null } = {}) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.body = body;
    }
  }

  function getWriteToken() {
    return window.__JORDANA_BOOTSTRAP__?.writeToken || "";
  }

  async function api(path, options = {}) {
    const method = (options.method || "GET").toUpperCase();
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
      headers["X-Jordana-Write-Token"] = getWriteToken();
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

  window.JordanaAPI = { api, ApiError, sanitizeUiErrorMessage };
})();
