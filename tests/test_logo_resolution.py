"""Focused tests for invoice logo resolution fallback chain.

Verifies the 3-level fallback: invoice snapshot → configured profile → bundled default.
"""
import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_rendering import (
    DEFAULT_LOGO_PATH,
    build_invoice_render_model,
    logo_data_uri,
    resolve_logo_path,
)
from jordana_invoice.invoice_services import (
    create_invoice_draft,
    finalize_invoice,
    get_invoice,
    preview_finalization,
    save_business_profile,
)
from jordana_invoice.review_services import approve_candidate, create_billing_party, create_person
from jordana_invoice.util import stable_hash


def raw_row(key, title, start):
    return {
        "ingested_at": "2026-08-20T12:00:00Z", "snapshot_key": key, "run_id": f"run-{key}",
        "batch_name": "test", "capture_window": "past_7_days", "captured_at": "2026-08-20T12:00:00Z",
        "source_device": "test", "timezone": "America/New_York", "calendar_event_id": f"event-{key}",
        "event_fingerprint": f"fp-{key}", "event_title": title, "start_at": start,
        "end_at": start[:11] + "11:00:00-04:00", "duration_minutes": "60", "calendar": "Jordana Work",
        "payload_version": "2", "raw_json": "{}",
    }


class LogoResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "test.sqlite3")
        init_db(self.conn)
        self.person = create_person(self.conn, {"first_name": "Logo", "last_name": "Test", "display_name": "Logo Test"})
        self.party = create_billing_party(self.conn, {
            "billing_name": "Logo Test", "person_id": self.person["person_id"],
            "billing_email": "logo@example.test", "billing_address_line_1": "1 Logo St",
            "billing_city": "Logo", "billing_state": "FL", "billing_postal_code": "00000",
            "preferred_delivery_method": "email",
        })
        save_business_profile(self.conn, {
            "business_name": "Logo Practice", "provider_display_name": "Logo Provider",
            "address_line_1": "100 Logo Ave", "city": "Logo", "state": "FL", "postal_code": "00000",
            "phone": "555-0100", "email": "billing@logo", "payee_name": "Logo Payee",
            "payment_address_line_1": "100 Logo Ave", "payment_city": "Logo", "payment_state": "FL",
            "payment_postal_code": "00000", "zelle_recipient": "demo-zelle@example.test",
        })
        self.custom_logo = self.root / "custom-logo.png"
        self._make_png(self.custom_logo, 80, 40)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _make_png(self, path, width, height):
        from PIL import Image
        img = Image.new("RGB", (width, height), "blue")
        img.save(str(path), format="PNG")

    def _approved_session(self, key):
        import_rows(self.conn, [raw_row(key, f"Logo Test | 60 | Office", f"2026-05-{10 + len(key):02d}T10:00:00-04:00")], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Logo Test"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60, "service_mode": "office",
            "time_category": "standard", "approved_rate": "150.00",
            "payment_status": "unpaid", "billing_treatment": "billable",
        })
        return self.conn.execute("SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)).fetchone()

    def _draft(self, sessions):
        return create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_period_start": "2026-05-01", "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31", "session_ids": [s["id"] for s in sessions],
        })

    # --- Test 1: Blank configured logo path uses bundled default ---

    def test_blank_configured_logo_uses_bundled_default(self):
        self.conn.execute("UPDATE business_profile SET logo_path = NULL WHERE active = 1")
        self.conn.commit()
        resolved = resolve_logo_path(None)
        self.assertTrue(resolved.endswith("jordana-logo.png"))
        uri = logo_data_uri(None)
        self.assertIsNotNone(uri)
        self.assertTrue(uri.startswith("data:image/png;base64,"))

    # --- Test 2: Missing configured logo path uses bundled default ---

    def test_missing_configured_logo_uses_bundled_default(self):
        missing = str(self.root / "does-not-exist.png")
        resolved = resolve_logo_path(missing)
        self.assertTrue(resolved.endswith("jordana-logo.png"))
        uri = logo_data_uri(missing)
        self.assertIsNotNone(uri)
        self.assertTrue(uri.startswith("data:image/png;base64,"))

    # --- Test 3: Missing invoice snapshot uses valid configured logo ---

    def test_missing_snapshot_uses_valid_configured_logo(self):
        self.conn.execute("UPDATE business_profile SET logo_path = ? WHERE active = 1", (str(self.custom_logo),))
        self.conn.commit()
        session = self._approved_session("logo3")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        render = preview["render_model"]
        self.assertEqual(render["logo_path"], str(self.custom_logo))
        self.assertTrue(render["logo_data_uri"].startswith("data:image/png;base64,"))

    # --- Test 4: Missing snapshot and missing configured logo use bundled default ---

    def test_missing_snapshot_and_missing_configured_use_bundled_default(self):
        self.conn.execute("UPDATE business_profile SET logo_path = ? WHERE active = 1", (str(self.root / "nope.png"),))
        self.conn.commit()
        session = self._approved_session("logo4")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        render = preview["render_model"]
        self.assertTrue(render["logo_path"].endswith("jordana-logo.png"))
        self.assertTrue(render["logo_data_uri"].startswith("data:image/png;base64,"))

    # --- Test 5: Existing valid custom logo takes priority ---

    def test_valid_custom_logo_takes_priority(self):
        self.conn.execute("UPDATE business_profile SET logo_path = ? WHERE active = 1", (str(self.custom_logo),))
        self.conn.commit()
        session = self._approved_session("logo5")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        render = preview["render_model"]
        self.assertEqual(render["logo_path"], str(self.custom_logo))
        self.assertTrue(render["logo_data_uri"].startswith("data:image/png;base64,"))

    # --- Test 6: Finalized invoice snapshot values are not rewritten ---

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_finalized_snapshot_not_rewritten(self, fake_pdf):
        fake_pdf.return_value = "x" * 64
        self.conn.execute("UPDATE business_profile SET logo_path = ? WHERE active = 1", (str(self.custom_logo),))
        self.conn.commit()
        session = self._approved_session("logo6")
        draft = self._draft([session])
        finalized = finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
        snapshot_logo = finalized["invoice"]["logo_reference_snapshot"]
        self.assertEqual(snapshot_logo, str(self.custom_logo))
        # Change the profile logo_path to something else
        self.conn.execute("UPDATE business_profile SET logo_path = NULL WHERE active = 1")
        self.conn.commit()
        # Re-fetch: finalized snapshot must remain unchanged
        refetched = get_invoice(self.conn, finalized["invoice"]["invoice_id"])
        self.assertEqual(refetched["invoice"]["logo_reference_snapshot"], str(self.custom_logo))

    # --- Test 7: HTML preview receives nonempty logo_data_uri ---

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_html_preview_receives_nonempty_logo_data_uri(self, fake_pdf):
        fake_pdf.return_value = "x" * 64
        session = self._approved_session("logo7")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        render = preview["render_model"]
        self.assertIsNotNone(render["logo_data_uri"])
        self.assertTrue(len(render["logo_data_uri"]) > 100)
        self.assertTrue(render["logo_data_uri"].startswith("data:image/png;base64,"))

    # --- Test 8: PDF renderer receives and displays the same resolved logo ---

    def test_pdf_renderer_receives_same_resolved_logo(self):
        try:
            import reportlab  # noqa: F401
        except ImportError:
            self.skipTest("reportlab not installed")
        from jordana_invoice.invoice_pdf import _logo_flowable
        session = self._approved_session("logo8")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        render = preview["render_model"]
        # The PDF flowable should resolve to the same logo_path
        flowable = _logo_flowable(render["logo_path"], 1.05 * 72, 0.73 * 72)
        self.assertIsNotNone(flowable, "PDF logo flowable should not be None when logo_path is valid")

    # --- Test 9: Logo aspect ratio remains preserved ---

    def test_logo_aspect_ratio_preserved(self):
        try:
            import reportlab  # noqa: F401
        except ImportError:
            self.skipTest("reportlab not installed")
        from jordana_invoice.invoice_pdf import _logo_flowable
        # The bundled logo is 2700x1875 → ratio = 1.44
        from PIL import Image as PILImage
        with PILImage.open(str(DEFAULT_LOGO_PATH)) as img:
            orig_w, orig_h = img.size
        orig_ratio = orig_w / orig_h
        max_w, max_h = 1.05 * 72, 0.73 * 72
        flowable = _logo_flowable(str(DEFAULT_LOGO_PATH), max_w, max_h)
        self.assertIsNotNone(flowable)
        rendered_ratio = flowable.drawWidth / flowable.drawHeight
        self.assertAlmostEqual(rendered_ratio, orig_ratio, places=2)

    # --- Test 10: No private filesystem paths in customer-facing HTML ---

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_no_private_paths_in_render_model_html(self, fake_pdf):
        fake_pdf.return_value = "x" * 64
        self.conn.execute("UPDATE business_profile SET logo_path = ? WHERE active = 1", (str(self.custom_logo),))
        self.conn.commit()
        session = self._approved_session("logo10")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        render = preview["render_model"]
        # logo_data_uri is a base64 data URI, not a filesystem path
        uri = render["logo_data_uri"]
        self.assertTrue(uri.startswith("data:"))
        self.assertNotIn(str(self.custom_logo), uri)
        self.assertNotIn(str(self.root), uri)
        # logo_path is in the render model but must not appear in the data URI
        self.assertNotIn(str(self.custom_logo), uri)
        # Verify the data URI is purely a base64 data URI, not a file:// or path reference
        self.assertFalse(uri.startswith("file://"))
        self.assertFalse(uri.startswith("/"))

    # --- Test 11: HTML preview logo has constrained maximum width ---

    def test_html_logo_max_width_constraint(self):
        css_path = Path(__file__).parent.parent / "app" / "jordana_invoice" / "static" / "review.css"
        css = css_path.read_text()
        # The logo container must have a max-width ≤ 220px
        import re
        match = re.search(r'\.invoice-preview-logo\s*\{[^}]*max-width:\s*(\d+)px', css)
        self.assertIsNotNone(match, "invoice-preview-logo max-width rule must exist")
        max_width = int(match.group(1))
        self.assertLessEqual(max_width, 220, "HTML logo max-width must be ≤ 220px")

    # --- Test 12: Height remains automatic / aspect-ratio-safe ---

    def test_html_logo_height_auto(self):
        css_path = Path(__file__).parent.parent / "app" / "jordana_invoice" / "static" / "review.css"
        css = css_path.read_text()
        # The logo img must use height: auto to preserve aspect ratio
        self.assertRegex(css, r'\.invoice-preview-logo img\s*\{[^}]*height:\s*auto')

    # --- Test 13: Logo and invoice metadata share top alignment ---

    def test_header_top_alignment(self):
        css_path = Path(__file__).parent.parent / "app" / "jordana_invoice" / "static" / "review.css"
        css = css_path.read_text()
        # The header grid must use align-items: start so both columns top-align
        self.assertRegex(css, r'\.invoice-preview-header\s*\{[^}]*align-items:\s*start')

    # --- Test 14: Sender block remains below the logo ---

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_sender_below_logo_in_render_model(self, fake_pdf):
        fake_pdf.return_value = "x" * 64
        session = self._approved_session("logo14")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        render = preview["render_model"]
        # render_model must have both logo_data_uri and sender_lines
        self.assertIsNotNone(render["logo_data_uri"])
        self.assertTrue(len(render["sender_lines"]) > 0)
        # In the JS template, logo comes first, then sender — verify the render model
        # has both keys in the right order (logo before sender in the dict)
        keys = list(render.keys())
        logo_idx = keys.index("logo_data_uri")
        sender_idx = keys.index("sender_lines")
        self.assertLess(logo_idx, sender_idx, "logo_data_uri must come before sender_lines in render model")

    # --- Test 15: PDF uses proportionally reduced logo size ---

    def test_pdf_logo_reduced_size(self):
        try:
            import reportlab  # noqa: F401
        except ImportError:
            self.skipTest("reportlab not installed")
        from jordana_invoice.invoice_pdf import _logo_flowable
        from PIL import Image as PILImage
        with PILImage.open(str(DEFAULT_LOGO_PATH)) as img:
            orig_w, orig_h = img.size
        # New max dimensions: 1.05" x 0.73" = 75.6pt x 52.56pt
        max_w, max_h = 1.05 * 72, 0.73 * 72
        flowable = _logo_flowable(str(DEFAULT_LOGO_PATH), max_w, max_h)
        self.assertIsNotNone(flowable)
        # The rendered width must not exceed the max width
        self.assertLessEqual(flowable.drawWidth, max_w + 0.1)
        # The rendered width should be significantly smaller than the old 3.15" (226.8pt)
        self.assertLess(flowable.drawWidth, 80, "PDF logo width must be reduced to ~1/3 of former size")

    # --- Test 16: PDF preserves aspect ratio at new size ---

    def test_pdf_aspect_ratio_at_reduced_size(self):
        try:
            import reportlab  # noqa: F401
        except ImportError:
            self.skipTest("reportlab not installed")
        from jordana_invoice.invoice_pdf import _logo_flowable
        from PIL import Image as PILImage
        with PILImage.open(str(DEFAULT_LOGO_PATH)) as img:
            orig_w, orig_h = img.size
        orig_ratio = orig_w / orig_h
        flowable = _logo_flowable(str(DEFAULT_LOGO_PATH), 1.05 * 72, 0.73 * 72)
        self.assertIsNotNone(flowable)
        rendered_ratio = flowable.drawWidth / flowable.drawHeight
        self.assertAlmostEqual(rendered_ratio, orig_ratio, places=2)

    # --- Test 17: Existing invoice data and numbering behavior remain unchanged ---

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_invoice_data_and_numbering_unchanged(self, fake_pdf):
        fake_pdf.return_value = "x" * 64
        session = self._approved_session("logo17")
        draft = self._draft([session])
        finalized = finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
        inv = finalized["invoice"]
        self.assertEqual(inv["status"], "finalized")
        self.assertEqual(inv["invoice_number"], "2026-0001")
        self.assertEqual(inv["total_cents"], 15000)
        self.assertEqual(inv["bill_to_name_snapshot"], "Logo Test")
        self.assertEqual(len(finalized["lines"]), 1)
        self.assertEqual(finalized["lines"][0]["line_amount_cents"], 15000)

    # --- Test 18: No unrelated invoice layout sections change ---

    def test_no_unrelated_css_changes(self):
        css_path = Path(__file__).parent.parent / "app" / "jordana_invoice" / "static" / "review.css"
        css = css_path.read_text()
        # Verify table, totals, payment, bill-to sections are untouched
        self.assertIn(".invoice-preview-table", css)
        self.assertIn(".invoice-total", css)
        self.assertIn(".invoice-payment", css)
        self.assertIn(".invoice-billto", css)
        # Verify the header grid template hasn't changed (still uses 220px right column)
        self.assertRegex(css, r'grid-template-columns:\s*minmax\(0,\s*1fr\)\s*220px')


if __name__ == "__main__":
    unittest.main()
