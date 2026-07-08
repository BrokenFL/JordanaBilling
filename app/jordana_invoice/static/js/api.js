(function () {
  "use strict";

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
    if (typeof window.dispatchEvent === "function" && typeof CustomEvent === "function") {
      window.dispatchEvent(new CustomEvent("jordana:api-diagnostic", {
        detail: {
          timestamp: new Date().toISOString(),
          area: diagnosticAreaForPath(path),
          event: "api_response",
          severity: (!res.ok || json.ok === false) ? "error" : "info",
          route: diagnosticRouteTemplate(path),
          status: res.status,
          message: (!res.ok || json.ok === false) ? sanitizeUiErrorMessage(json.error || "Request failed") : ""
        }
      }));
    }
    if (!res.ok || json.ok === false) throw new Error(json.error || "Request failed");
    return json;
  }

  function diagnosticAreaForPath(path) {
    const clean = String(path || "").split("?")[0];
    if (clean.startsWith("/api/review")) return "review";
    if (clean.startsWith("/api/billing-relationships") || clean.startsWith("/api/accounts") || clean.startsWith("/api/billing-parties")) return "billing_relationships";
    if (clean.startsWith("/api/invoices")) return "invoices";
    if (clean.startsWith("/api/payments")) return "payments";
    if (clean.startsWith("/api/sync") || clean.startsWith("/api/calendar-reconcile")) return "calendar_sync";
    return "other";
  }

  function diagnosticRouteTemplate(path) {
    return String(path || "")
      .split("?")[0]
      .replace(/\/([0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}|[0-9a-fA-F]{32,}|[A-Za-z0-9_-]{20,})(?=\/|$)/g, "/{id}");
  }

  function sanitizeUiErrorMessage(message, fallback = "An unexpected error occurred.") {
    const raw = String(message || "").trim();
    if (!raw) return fallback;
    if (raw.includes("/") || raw.toLowerCase().includes("traceback") || raw.toLowerCase().includes("select ")) {
      return fallback;
    }
    return raw;
  }

  window.JordanaAPI = { api, sanitizeUiErrorMessage, getWriteToken };
})();
