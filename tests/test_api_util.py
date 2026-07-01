import unittest
import subprocess
import textwrap
from pathlib import Path


class ApiUtilStaticTests(unittest.TestCase):
    """Static tests for the shared frontend API utility module."""

    def setUp(self):
        self.api_js = Path("app/jordana_invoice/static/js/api.js").read_text()
        self.review_js = Path("app/jordana_invoice/static/review.js").read_text()
        self.review_html = Path("app/jordana_invoice/static/review.html").read_text()

    # --- Module structure ---

    def test_api_module_file_exists(self):
        self.assertTrue(Path("app/jordana_invoice/static/js/api.js").exists())

    def test_api_module_loaded_before_review_js_in_html(self):
        api_pos = self.review_html.index('<script src="/static/js/api.js"></script>')
        review_pos = self.review_html.index('<script src="/static/review.js"></script>')
        self.assertLess(api_pos, review_pos)

    def test_api_module_assigns_window_jordana_api(self):
        self.assertIn("window.JordanaAPI", self.api_js)
        self.assertIn("getWriteToken", self.api_js)

    def test_review_js_destructures_from_window_jordana_api(self):
        self.assertIn("const { api, sanitizeUiErrorMessage, getWriteToken } = window.JordanaAPI;", self.review_js)

    def test_review_js_no_longer_defines_local_api_function(self):
        self.assertNotIn("async function api(path, options = {}) {", self.review_js)

    def test_review_js_no_longer_defines_local_sanitize_function(self):
        start = self.review_js.index("function safeList")
        before = self.review_js[:start]
        self.assertNotIn("function sanitizeUiErrorMessage(", before)

    # --- GET request behavior ---

    def test_get_request_default_method(self):
        self.assertIn('(options.method || "GET").toUpperCase()', self.api_js)

    def test_get_request_preserves_content_type_header(self):
        self.assertIn('"Content-Type": "application/json"', self.api_js)

    def test_get_requests_do_not_receive_write_token(self):
        write_methods = self.api_js[self.api_js.index("[") : self.api_js.index("]") + 1]
        self.assertIn('"POST"', write_methods)
        self.assertIn('"PUT"', write_methods)
        self.assertIn('"PATCH"', write_methods)
        self.assertIn('"DELETE"', write_methods)
        self.assertNotIn('"GET"', write_methods)

    # --- POST / write request behavior ---

    def test_post_request_adds_write_token_header(self):
        self.assertIn('headers["X-Jordana-Write-Token"] = getWriteToken()', self.api_js)

    def test_write_token_is_read_per_write_request(self):
        self.assertIn("function getWriteToken()", self.api_js)
        self.assertIn('window.__JORDANA_BOOTSTRAP__?.writeToken || ""', self.api_js)
        self.assertNotIn("const WRITE_TOKEN", self.api_js)

    def test_review_js_direct_write_fetches_use_current_write_token(self):
        self.assertNotIn("const WRITE_TOKEN", self.review_js)
        self.assertEqual(self.review_js.count('"X-Jordana-Write-Token": getWriteToken()'), 2)

    def test_write_token_not_in_urls(self):
        self.assertNotIn("writeToken", self.api_js.replace(
            'window.__JORDANA_BOOTSTRAP__?.writeToken || ""', ""))

    def test_write_token_not_logged(self):
        self.assertNotIn("console.log", self.api_js)
        self.assertNotIn("console.error", self.api_js)
        self.assertNotIn("console.warn", self.api_js)

    # --- Header merging ---

    def test_caller_headers_are_preserved(self):
        self.assertIn("...(options.headers || {})", self.api_js)

    def test_content_type_is_default_but_overridable(self):
        self.assertIn(
            '{ "Content-Type": "application/json", ...(options.headers || {}) }',
            self.api_js,
        )

    # --- Body serialization ---

    def test_api_does_not_serialize_body(self):
        self.assertNotIn("JSON.stringify", self.api_js)

    def test_callers_still_serialize_body_in_review_js(self):
        self.assertIn("body:JSON.stringify(", self.review_js)

    def test_actual_review_page_order_still_sends_bootstrap_token_for_save_relationship(self):
        api_pos = self.review_html.index('<script src="/static/js/api.js"></script>')
        review_marker = '<script src="/static/review.js"></script>'
        rendered_html = self.review_html.replace(
            review_marker,
            '<script nonce="test-nonce">window.__JORDANA_BOOTSTRAP__={"writeToken": "test-bootstrap-token"};</script>\n'
            f"    {review_marker}",
            1,
        )
        bootstrap_pos = rendered_html.index("window.__JORDANA_BOOTSTRAP__")
        review_pos = rendered_html.index(review_marker)
        self.assertLess(api_pos, bootstrap_pos)
        self.assertLess(bootstrap_pos, review_pos)

        script = textwrap.dedent(
            f"""
            global.window = {{}};
            let captured = null;
            global.fetch = async (path, options) => {{
              captured = {{path, options}};
              return {{ok: true, json: async () => ({{ok: true}})}};
            }};
            {self.api_js}
            window.__JORDANA_BOOTSTRAP__ = {{writeToken: "test-bootstrap-token"}};
            (async () => {{
              await window.JordanaAPI.api(
                "/api/review/candidates/candidate-1/save-relationship",
                {{method: "POST", body: "{{}}"}}
              );
              if (captured.path !== "/api/review/candidates/candidate-1/save-relationship") process.exit(2);
              if (captured.options.headers["X-Jordana-Write-Token"] !== "test-bootstrap-token") process.exit(3);
              if (!captured.options.headers["X-Jordana-Write-Token"]) process.exit(4);
            }})().catch(() => process.exit(5));
            """
        )
        result = subprocess.run(["node", "-e", script], check=False, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    # --- Response parsing ---

    def test_successful_json_response_is_returned(self):
        self.assertIn("const json = await res.json()", self.api_js)
        self.assertIn("return json;", self.api_js)

    def test_error_thrown_when_not_ok(self):
        self.assertIn("!res.ok", self.api_js)

    def test_error_thrown_when_ok_false(self):
        self.assertIn("json.ok === false", self.api_js)

    def test_error_message_from_json_error_field(self):
        self.assertIn('json.error || "Request failed"', self.api_js)

    def test_error_is_plain_error_not_api_error(self):
        self.assertIn("throw new Error(", self.api_js)

    # --- Warning handling ---

    def test_warning_does_not_cause_failure(self):
        self.assertNotIn("warning", self.api_js.split("window.JordanaAPI")[0])

    def test_restore_warning_path_preserved_in_review_js(self):
        self.assertIn("result.warning", self.review_js)

    def test_approval_staging_warning_path_preserved_in_review_js(self):
        self.assertIn('staging.status === "warning"', self.review_js)

    # --- Error sanitization ---

    def test_sanitize_function_exported_from_api_module(self):
        self.assertIn("sanitizeUiErrorMessage", self.api_js)
        self.assertIn("window.JordanaAPI = { api, sanitizeUiErrorMessage, getWriteToken }", self.api_js)

    def test_sanitize_preserves_fallback_default(self):
        self.assertIn('"An unexpected error occurred."', self.api_js)

    def test_sanitize_blocks_paths(self):
        self.assertIn('raw.includes("/")', self.api_js)

    def test_sanitize_blocks_traceback(self):
        self.assertIn('"traceback"', self.api_js)

    def test_sanitize_blocks_sql(self):
        self.assertIn('"select "', self.api_js)

    def test_sanitize_still_callable_in_review_js(self):
        self.assertIn("sanitizeUiErrorMessage(err.message", self.review_js)
        self.assertIn("sanitizeUiErrorMessage(msg", self.review_js)
        self.assertIn("sanitizeUiErrorMessage(error.message", self.review_js)

    def test_api_error_class_not_present(self):
        self.assertNotIn("ApiError", self.api_js)

    # --- No payload logging ---

    def test_no_request_body_logging(self):
        self.assertNotIn("options.body", self.api_js.replace(
            "const res = await fetch(path, { ...options, method, headers });", ""))

    # --- Direct fetch inventory ---

    def test_direct_fetch_pdf_blob_remains_in_review_js(self):
        self.assertIn("await fetch(`/api/invoices/${i.invoice_id}/draft-pdf`", self.review_js)
        self.assertIn("await res.blob()", self.review_js)

    def test_direct_fetch_billing_setup_remains_in_review_js(self):
        self.assertIn('await fetch("/api/billing-relationships/setup"', self.review_js)
        self.assertIn("throw json;", self.review_js)

    def test_review_js_still_uses_api_for_standard_requests(self):
        self.assertIn("await api(", self.review_js)

    # --- Call-site result shapes ---

    def test_api_returns_json_object_unchanged(self):
        self.assertIn("return json;", self.api_js)
        self.assertNotIn("return { ...json }", self.api_js)
        self.assertNotIn("return json.data", self.api_js)

    # --- IIFE pattern ---

    def test_module_uses_iife_not_es_module(self):
        self.assertIn("(function () {", self.api_js)
        self.assertIn("})();", self.api_js)
        self.assertNotIn("export ", self.api_js)
        self.assertNotIn("import ", self.api_js)

    def test_module_uses_strict_mode(self):
        self.assertIn('"use strict"', self.api_js)


if __name__ == "__main__":
    unittest.main()
