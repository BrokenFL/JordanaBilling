"""Tests for simplified payment status and safe invoice finalization."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import (
    add_sessions_to_draft,
    create_invoice_draft,
    finalize_invoice,
    get_invoice,
    invoice_ineligibility_reasons,
    preview_finalization,
    remove_line_from_draft,
    resolve_invoice_filing_owner,
    save_business_profile,
    trusted_invoice_document_action,
    update_invoice_filing_owner,
    update_invoice_draft,
    void_invoice,
)
from jordana_invoice.review_services import add_account_member, approve_candidate, create_account, create_billing_party, create_person
from jordana_invoice.util import normalize_payment_status, stable_hash


def raw_row(key, title, start):
    return {
        "ingested_at": "2026-08-20T12:00:00Z", "snapshot_key": key, "run_id": f"run-{key}",
        "batch_name": "test", "capture_window": "past_7_days", "captured_at": "2026-08-20T12:00:00Z",
        "source_device": "test", "timezone": "America/New_York", "calendar_event_id": f"event-{key}",
        "event_fingerprint": f"fp-{key}", "event_title": title, "start_at": start,
        "end_at": start[:11] + "11:00:00-04:00", "duration_minutes": "60", "calendar": "Jordana Work",
        "payload_version": "2", "raw_json": "{}",
    }


class PaymentStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "test.sqlite3")
        init_db(self.conn)
        self.person = create_person(self.conn, {"first_name": "Pat", "last_name": "Client", "display_name": "Pat Client"})
        self.party = create_billing_party(self.conn, {
            "billing_name": "Pat Client", "person_id": self.person["person_id"],
            "billing_email": "pat@example.test", "billing_address_line_1": "1 Test St",
            "billing_city": "Test", "billing_state": "FL", "billing_postal_code": "00000",
            "preferred_delivery_method": "email",
        })
        save_business_profile(self.conn, {
            "business_name": "Test Practice", "provider_display_name": "Test Provider",
            "address_line_1": "100 Test Ave", "city": "Test", "state": "FL", "postal_code": "00000",
            "phone": "555-0100", "email": "billing@test", "payee_name": "Test Payee",
            "payment_address_line_1": "100 Test Ave", "payment_city": "Test", "payment_state": "FL",
            "payment_postal_code": "00000", "zelle_recipient": "test-zelle@example.test",
        })

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _approved_session(self, key, payment_status="unpaid", amount="150.00"):
        import_rows(self.conn, [raw_row(key, f"Pat Client | 60 | Office", f"2026-05-{10 + len(key):02d}T10:00:00-04:00")], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        payload = {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Pat Client"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60, "service_mode": "office",
            "time_category": "standard", "approved_rate": amount,
            "payment_status": payment_status, "billing_treatment": "billable",
        }
        if normalize_payment_status(payment_status) == "paid_at_session":
            payload["amount_received"] = amount
            payload["payment_date"] = f"2026-05-{10 + len(key):02d}"
            payload["payment_method"] = "zelle"
        detail = approve_candidate(self.conn, candidate_id, payload)
        return self.conn.execute("SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)).fetchone()

    def test_new_session_defaults_to_unpaid(self):
        """New sessions should default to 'unpaid', not 'unresolved'."""
        import_rows(self.conn, [raw_row("default", "Pat Client | 60 | Office", "2026-05-15T10:00:00-04:00")], "test")
        session = self.conn.execute(
            "SELECT payment_status FROM sessions s JOIN calendar_event_candidates c ON s.candidate_id = c.id WHERE c.candidate_key = ?",
            (stable_hash("calendar_event_id:event-default"),),
        ).fetchone()
        self.assertEqual(session["payment_status"], "unpaid")

    def test_normalize_legacy_values(self):
        self.assertEqual(normalize_payment_status("paid"), "paid_at_session")
        self.assertEqual(normalize_payment_status("unresolved"), "unpaid")
        self.assertEqual(normalize_payment_status("partially_paid"), "unpaid")
        self.assertEqual(normalize_payment_status("waived"), "unpaid")
        self.assertEqual(normalize_payment_status("not_billable"), "unpaid")
        self.assertEqual(normalize_payment_status(""), "unpaid")
        self.assertEqual(normalize_payment_status(None), "unpaid")
        self.assertEqual(normalize_payment_status("unpaid"), "unpaid")
        self.assertEqual(normalize_payment_status("paid_at_session"), "paid_at_session")

    def test_paid_at_session_excluded_from_invoicing(self):
        """Sessions marked paid_at_session should be ineligible for invoicing."""
        session = self._approved_session("paid1", payment_status="paid_at_session")
        reasons = invoice_ineligibility_reasons(self.conn, session)
        self.assertTrue(any("paid at time of session" in r.lower() for r in reasons))

    def test_unpaid_session_remains_eligible(self):
        """Unpaid sessions should remain invoice eligible."""
        session = self._approved_session("unpaid1", payment_status="unpaid")
        reasons = invoice_ineligibility_reasons(self.conn, session)
        self.assertEqual(reasons, [])

    def test_legacy_paid_normalized_to_paid_at_session(self):
        """Legacy 'paid' value should be normalized and block invoicing."""
        session = self._approved_session("legacy", payment_status="paid")
        self.assertEqual(session["payment_status"], "paid_at_session")
        reasons = invoice_ineligibility_reasons(self.conn, session)
        self.assertTrue(any("paid at time of session" in r.lower() for r in reasons))

    def test_payment_status_not_required_for_approval(self):
        """Payment status should not block review readiness."""
        session = self._approved_session("no_payment", payment_status="unpaid")
        self.assertEqual(session["review_status"], "approved")

    def test_draft_with_paid_at_session_session_fails(self):
        """Adding a paid_at_session session to a draft should fail."""
        session = self._approved_session("draft_fail", payment_status="paid_at_session")
        with self.assertRaises(ValueError):
            create_invoice_draft(self.conn, {
                "bill_to_party_id": self.party["billing_party_id"],
                "billing_period_start": "2026-05-01", "billing_period_end": "2026-05-31",
                "invoice_date": "2026-05-31", "session_ids": [session["id"]],
            })


class SafeFinalizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "test.sqlite3")
        init_db(self.conn)
        self.person = create_person(self.conn, {"first_name": "Robin", "last_name": "Test", "display_name": "Robin Test"})
        self.party = create_billing_party(self.conn, {
            "billing_name": "Robin Test", "person_id": self.person["person_id"],
            "billing_email": "robin@example.test", "billing_address_line_1": "5 Sample St",
            "billing_city": "Sample", "billing_state": "FL", "billing_postal_code": "00000",
            "preferred_delivery_method": "email",
        })
        save_business_profile(self.conn, {
            "business_name": "Sample Practice", "provider_display_name": "Sample Provider",
            "address_line_1": "200 Sample Ave", "city": "Sample", "state": "FL", "postal_code": "00000",
            "phone": "555-0200", "email": "billing@sample", "payee_name": "Sample Payee",
            "payment_address_line_1": "200 Sample Ave", "payment_city": "Sample", "payment_state": "FL",
            "payment_postal_code": "00000", "zelle_recipient": "sample-zelle@example.test",
        })

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _approved_session(self, key, amount="150.00"):
        import_rows(self.conn, [raw_row(key, f"Robin Test | 60 | Office", f"2026-05-{10 + len(key):02d}T10:00:00-04:00")], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Robin Test"}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60, "service_mode": "office",
            "time_category": "standard", "approved_rate": amount,
            "payment_status": "unpaid", "billing_treatment": "billable",
        })
        return self.conn.execute("SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)).fetchone()

    def _draft(self, sessions):
        return create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_period_start": "2026-05-01", "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31", "session_ids": [s["id"] for s in sessions],
        })

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_preview_reads_draft_and_returns_revision(self, fake_pdf):
        """preview_finalization should return a revision without mutating the draft."""
        fake_pdf.return_value = "x" * 64
        session = self._approved_session("preview1")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        self.assertIn("preview_revision", preview)
        self.assertIsInstance(preview["preview_revision"], int)
        self.assertEqual(preview["invoice"]["status"], "draft")
        self.assertEqual(preview["invoice"]["invoice_id"], draft["invoice"]["invoice_id"])
        self.assertGreater(len(preview["lines"]), 0)
        self.assertIsNotNone(preview["business_profile"])
        self.assertIsNotNone(preview["billing_party"])
        self.assertIn("render_model", preview)

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_preview_render_model_uses_long_dates_month_period_and_pending_number(self, fake_pdf):
        fake_pdf.return_value = "x" * 64
        session = self._approved_session("render1")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        render = preview["render_model"]
        self.assertEqual(render["invoice_date_display"], "May 31, 2026")
        self.assertEqual(render["billing_period_display"], "May 2026")
        self.assertEqual(render["invoice_number_display"], "Assigned when finalized")
        self.assertEqual(render["lines"][0]["service_date_display"], "May 17, 2026")
        self.assertEqual(render["sender_lines"][0], "Sample Provider")

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_preview_render_model_uses_compact_multimonth_period(self, fake_pdf):
        fake_pdf.return_value = "x" * 64
        session = self._approved_session("render2")
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-06-30",
            "invoice_date": "2026-06-30",
            "session_ids": [session["id"]],
        })
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        self.assertEqual(preview["render_model"]["billing_period_display"], "May 2026 - June 2026")

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_preview_render_model_shows_email_delivery_under_bill_to(self, fake_pdf):
        fake_pdf.return_value = "x" * 64
        session = self._approved_session("emailbill")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        render = preview["render_model"]
        self.assertEqual(render["bill_to_lines"], ["Robin Test", "Via Email: robin@example.test"])
        self.assertIn("Or pay via Zelle: sample-zelle@example.test", render["payment_zelle_line"])
        self.assertEqual(render["payment_lines"], ["200 Sample Ave", "Sample, FL 00000"])

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_preview_render_model_shows_both_delivery_values(self, fake_pdf):
        fake_pdf.return_value = "x" * 64
        self.conn.execute(
            "UPDATE billing_parties SET preferred_delivery_method = ?, billing_address_line_2 = ? WHERE billing_party_id = ?",
            ("both", "", self.party["billing_party_id"]),
        )
        self.conn.commit()
        session = self._approved_session("bothbill")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        self.assertEqual(
            preview["render_model"]["bill_to_lines"],
            ["Robin Test", "5 Sample St", "Sample, FL 00000", "Via Email: robin@example.test"],
        )

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_preview_render_model_shows_postal_delivery_without_email(self, fake_pdf):
        fake_pdf.return_value = "x" * 64
        self.conn.execute(
            "UPDATE billing_parties SET preferred_delivery_method = ?, billing_address_line_2 = ? WHERE billing_party_id = ?",
            ("mail", "", self.party["billing_party_id"]),
        )
        self.conn.commit()
        session = self._approved_session("mailbill")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        self.assertEqual(
            preview["render_model"]["bill_to_lines"],
            ["Robin Test", "5 Sample St", "Sample, FL 00000"],
        )

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_blank_logo_path_uses_bundled_default_logo(self, fake_pdf):
        fake_pdf.return_value = "x" * 64
        self.conn.execute("UPDATE business_profile SET logo_path = NULL WHERE active = 1")
        self.conn.commit()
        session = self._approved_session("render3")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        render = preview["render_model"]
        self.assertTrue(render["logo_path"].endswith("app/jordana_invoice/static/assets/jordana-logo.png"))
        self.assertTrue(render["logo_data_uri"].startswith("data:image/png;base64,"))

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_preview_with_data_does_not_update_draft(self, fake_pdf):
        """preview_finalization payload is read-only approval-preview metadata."""
        fake_pdf.return_value = "x" * 64
        session = self._approved_session("preview2")
        draft = self._draft([session])
        before = get_invoice(self.conn, draft["invoice"]["invoice_id"])["invoice"]
        preview = preview_finalization(
            self.conn, draft["invoice"]["invoice_id"],
            data={"delivery_method": "mail"},
        )
        after = get_invoice(self.conn, draft["invoice"]["invoice_id"])["invoice"]
        self.assertEqual(preview["invoice"]["delivery_method"], before["delivery_method"])
        self.assertEqual(after["delivery_method"], before["delivery_method"])
        self.assertEqual(after["revision"], before["revision"])

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_finalize_with_matching_revision_succeeds(self, fake_pdf):
        """Finalize with correct revision should succeed."""
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("finalize1")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        final = finalize_invoice(
            self.conn, draft["invoice"]["invoice_id"],
            expected_revision=preview["preview_revision"],
            pdf_root=self.root / "Invoices",
        )
        self.assertEqual(final["invoice"]["status"], "finalized")
        self.assertEqual(final["invoice"]["invoice_number"], "2026-0001")
        self.assertEqual(final["render_model"]["invoice_number_display"], "2026-0001")

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_finalize_with_stale_revision_rejected(self, fake_pdf):
        """Finalize with stale revision should be rejected."""
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("stale1")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        # Mutate the draft after preview
        update_invoice_draft(self.conn, draft["invoice"]["invoice_id"], {"delivery_method": "mail"})
        with self.assertRaises(ValueError) as ctx:
            finalize_invoice(
                self.conn, draft["invoice"]["invoice_id"],
                expected_revision=preview["preview_revision"],
                pdf_root=self.root / "Invoices",
            )
        self.assertIn("changed since preview", str(ctx.exception))

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_finalize_without_revision_still_works(self, fake_pdf):
        """Finalize without expected_revision should still work (backward compat)."""
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("norev")
        draft = self._draft([session])
        final = finalize_invoice(
            self.conn, draft["invoice"]["invoice_id"],
            pdf_root=self.root / "Invoices",
        )
        self.assertEqual(final["invoice"]["status"], "finalized")

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_double_finalization_returns_existing_pdf_without_rewrite(self, fake_pdf):
        """Finalizing an already-finalized invoice returns the immutable artifact."""
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("double")
        draft = self._draft([session])
        first = finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
        second = finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")

        self.assertEqual(second["invoice"]["status"], "finalized")
        self.assertEqual(second["invoice"]["invoice_number"], first["invoice"]["invoice_number"])
        self.assertEqual(second["invoice"]["pdf_path"], first["invoice"]["pdf_path"])
        self.assertEqual(second["invoice"]["pdf_sha256"], first["invoice"]["pdf_sha256"])
        fake_pdf.assert_called_once()

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_finalization_snapshot_matches_preview(self, fake_pdf):
        """Finalized invoice snapshots should match what was in the preview."""
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("snapshot")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        final = finalize_invoice(
            self.conn, draft["invoice"]["invoice_id"],
            expected_revision=preview["preview_revision"],
            pdf_root=self.root / "Invoices",
        )
        # Snapshots should match preview values
        self.assertEqual(final["invoice"]["bill_to_name_snapshot"], "Robin Test")
        self.assertEqual(final["invoice"]["business_name_snapshot"], "Sample Practice")
        self.assertEqual(final["invoice"]["zelle_recipient_snapshot"], "sample-zelle@example.test")
        self.assertEqual(final["invoice"]["total_cents"], preview["invoice"]["total_cents"])
        self.assertEqual(len(final["lines"]), len(preview["lines"]))
        for f_line, p_line in zip(final["lines"], preview["lines"]):
            self.assertEqual(f_line["line_amount_cents"], p_line["line_amount_cents"])
            self.assertEqual(f_line["description_snapshot"], p_line["description_snapshot"])

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_finalized_zelle_snapshot_does_not_change_after_settings_update(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("zellesnap")
        draft = self._draft([session])
        final = finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
        save_business_profile(self.conn, {"zelle_recipient": "changed@example.test"})
        reopened = get_invoice(self.conn, final["invoice"]["invoice_id"])
        self.assertEqual(reopened["invoice"]["zelle_recipient_snapshot"], "sample-zelle@example.test")
        self.assertIn("sample-zelle@example.test", reopened["render_model"]["payment_zelle_line"])

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_finalized_delivery_snapshot_does_not_change_after_billing_party_update(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        self.conn.execute(
            "UPDATE billing_parties SET preferred_delivery_method = ? WHERE billing_party_id = ?",
            ("both", self.party["billing_party_id"]),
        )
        self.conn.commit()
        session = self._approved_session("deliverysnap")
        draft = self._draft([session])
        final = finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
        self.conn.execute(
            """UPDATE billing_parties
               SET billing_email = ?, billing_address_line_1 = ?, billing_city = ?, billing_state = ?, billing_postal_code = ?
               WHERE billing_party_id = ?""",
            ("new@example.test", "999 Changed Rd", "Elsewhere", "NY", "11111", self.party["billing_party_id"]),
        )
        self.conn.commit()
        reopened = get_invoice(self.conn, final["invoice"]["invoice_id"])
        self.assertEqual(
            reopened["render_model"]["bill_to_lines"],
            ["Robin Test", "5 Sample St", "Sample, FL 00000", "Via Email: robin@example.test"],
        )

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_legacy_finalized_invoice_without_zelle_snapshot_is_not_rewritten(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("legacyzelle")
        draft = self._draft([session])
        final = finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
        self.conn.execute(
            "UPDATE invoices SET zelle_recipient_snapshot = NULL WHERE invoice_id = ?",
            (final["invoice"]["invoice_id"],),
        )
        self.conn.commit()
        save_business_profile(self.conn, {"zelle_recipient": "rewritten@example.test"})
        reopened = get_invoice(self.conn, final["invoice"]["invoice_id"])
        self.assertEqual(reopened["render_model"]["payment_zelle_line"], "")

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_validation_failure_leaves_draft_unchanged(self, fake_pdf):
        """If finalization fails validation, invoice should remain a draft."""
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("valfail")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        # Make session ineligible after preview
        self.conn.execute("UPDATE sessions SET review_status = 'needs_review' WHERE id = ?", (session["id"],))
        self.conn.commit()
        with self.assertRaises(ValueError):
            finalize_invoice(
                self.conn, draft["invoice"]["invoice_id"],
                expected_revision=preview["preview_revision"],
                pdf_root=self.root / "Invoices",
            )
        # Invoice should still be a draft
        result = get_invoice(self.conn, draft["invoice"]["invoice_id"])
        self.assertEqual(result["invoice"]["status"], "draft")

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_failed_finalization_does_not_consume_invoice_number(self, fake_pdf):
        fake_pdf.return_value = "a" * 64
        session = self._approved_session("valfail2")
        draft = self._draft([session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        self.conn.execute("UPDATE sessions SET review_status = 'needs_review' WHERE id = ?", (session["id"],))
        self.conn.commit()
        with self.assertRaises(ValueError):
            finalize_invoice(
                self.conn, draft["invoice"]["invoice_id"],
                expected_revision=preview["preview_revision"],
                pdf_root=self.root / "Invoices",
            )
        self.conn.execute("UPDATE sessions SET review_status = 'approved' WHERE id = ?", (session["id"],))
        self.conn.commit()
        second_preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        final = finalize_invoice(
            self.conn, draft["invoice"]["invoice_id"],
            expected_revision=second_preview["preview_revision"],
            pdf_root=self.root / "Invoices",
        )
        self.assertEqual(final["invoice"]["invoice_number"], "2026-0001")

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_void_after_finalize_prevents_reedit(self, fake_pdf):
        """Voiding a finalized invoice should prevent further edits."""
        fake_pdf.return_value = "b" * 64
        session = self._approved_session("void1")
        draft = self._draft([session])
        finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
        voided = void_invoice(self.conn, draft["invoice"]["invoice_id"], "Test void")
        self.assertEqual(voided["invoice"]["status"], "void")
        with self.assertRaises(ValueError):
            update_invoice_draft(self.conn, draft["invoice"]["invoice_id"], {"notes": "edit after void"})

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_revision_increments_on_draft_update(self, fake_pdf):
        """Revision should increment when draft is updated."""
        session = self._approved_session("rev1")
        draft = self._draft([session])
        initial_rev = draft["invoice"]["revision"]
        update_invoice_draft(self.conn, draft["invoice"]["invoice_id"], {"delivery_method": "mail"})
        updated = get_invoice(self.conn, draft["invoice"]["invoice_id"])
        self.assertEqual(updated["invoice"]["revision"], initial_rev + 1)

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_revision_increments_on_add_sessions(self, fake_pdf):
        """Revision should increment when sessions are added."""
        session1 = self._approved_session("rev2a")
        draft = self._draft([session1])
        initial_rev = draft["invoice"]["revision"]
        session2 = self._approved_session("rev2b", amount="100.00")
        add_sessions_to_draft(self.conn, draft["invoice"]["invoice_id"], [session2["id"]])
        updated = get_invoice(self.conn, draft["invoice"]["invoice_id"])
        self.assertEqual(updated["invoice"]["revision"], initial_rev + 1)

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_revision_increments_on_remove_line(self, fake_pdf):
        """Revision should increment when a line is removed."""
        session1 = self._approved_session("rev3a")
        session2 = self._approved_session("rev3b", amount="100.00")
        draft = self._draft([session1, session2])
        initial_rev = draft["invoice"]["revision"]
        remove_line_from_draft(self.conn, draft["invoice"]["invoice_id"], draft["lines"][0]["invoice_line_item_id"])
        updated = get_invoice(self.conn, draft["invoice"]["invoice_id"])
        self.assertEqual(updated["invoice"]["revision"], initial_rev + 1)

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_preview_rejects_empty_draft(self, fake_pdf):
        """Preview should return readiness errors for a draft with no lines."""
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": self.party["billing_party_id"],
            "billing_period_start": "2026-05-01", "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31",
        })
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        self.assertIn("readiness", preview)
        self.assertFalse(preview["readiness"]["ready"])
        error_fields = {e["field"] for e in preview["readiness"]["errors"]}
        self.assertIn("lines", error_fields)


class FilingOwnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "test.sqlite3")
        init_db(self.conn)
        save_business_profile(self.conn, {
            "business_name": "Filing Practice", "provider_display_name": "Filing Provider",
            "address_line_1": "1 Main", "city": "Test", "state": "FL", "postal_code": "00000",
            "phone": "555-0300", "email": "billing@filing", "payee_name": "Filing Payee",
            "payment_address_line_1": "1 Main", "payment_city": "Test", "payment_state": "FL",
            "payment_postal_code": "00000", "zelle_recipient": "filing-zelle@example.test",
        })

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _person(self, first, last):
        return create_person(self.conn, {"first_name": first, "last_name": last, "display_name": f"{first} {last}"})

    def _party(self, name, person_id=None, *, organization=False):
        return create_billing_party(self.conn, {
            "billing_party_type": "organization" if organization else "person",
            "organization_name": name if organization else None,
            "billing_name": name,
            "person_id": person_id,
            "billing_email": f"{name.split()[0].lower()}@example.test",
            "billing_address_line_1": "10 Billing St",
            "billing_city": "Test",
            "billing_state": "FL",
            "billing_postal_code": "00000",
            "preferred_delivery_method": "email",
        })

    def _session(self, key, participants, bill_to_party_id):
        import_rows(self.conn, [raw_row(key, f"{participants[0]['display_name']} | 60 | Office", f"2026-05-{10 + len(key):02d}T10:00:00-04:00")], "test")
        candidate_id = self.conn.execute(
            "SELECT id FROM calendar_event_candidates WHERE candidate_key = ?",
            (stable_hash(f"calendar_event_id:event-{key}"),),
        ).fetchone()[0]
        detail = approve_candidate(self.conn, candidate_id, {
            "participants": participants,
            "billing_party_id": bill_to_party_id,
            "approved_duration_minutes": 60, "service_mode": "office",
            "time_category": "standard", "approved_rate": "150.00",
            "payment_status": "unpaid", "billing_treatment": "billable",
        })
        return self.conn.execute("SELECT * FROM sessions WHERE id = ?", (detail["session"]["id"],)).fetchone()

    def _draft(self, party_id, sessions):
        return create_invoice_draft(self.conn, {
            "bill_to_party_id": party_id,
            "billing_period_start": "2026-05-01",
            "billing_period_end": "2026-05-31",
            "invoice_date": "2026-05-31",
            "session_ids": [s["id"] for s in sessions],
        })

    def _relationship(self, name, party_id, people, default_filing_owner_person_id=None):
        account = create_account(self.conn, name)
        for index, person in enumerate(people):
            add_account_member(self.conn, account["account_id"], person["person_id"], "primary" if index == 0 else "family_member", index == 0)
        self.conn.execute(
            "UPDATE client_accounts SET default_billing_party_id = ?, default_filing_owner_person_id = ? WHERE account_id = ?",
            (party_id, default_filing_owner_person_id, account["account_id"]),
        )
        self.conn.commit()
        return account

    def test_self_paying_client_files_under_self(self):
        client = self._person("Self", "Client")
        party = self._party("Self Client", client["person_id"])
        session = self._session("selfpay", [{"person_id": client["person_id"], "display_name": "Self Client"}], party["billing_party_id"])
        draft = self._draft(party["billing_party_id"], [session])
        filing = resolve_invoice_filing_owner(self.conn, draft["invoice"]["invoice_id"])
        self.assertEqual(filing["selected"]["person_id"], client["person_id"])
        self.assertEqual(filing["source"], "bill_to_client")

    def test_client_bill_to_paying_for_another_client_files_under_bill_to(self):
        child = self._person("Child", "Client")
        parent = self._person("Parent", "Client")
        party = self._party("Parent Client", parent["person_id"])
        session = self._session("parentpay", [{"person_id": child["person_id"], "display_name": "Child Client"}], party["billing_party_id"])
        draft = self._draft(party["billing_party_id"], [session])
        filing = resolve_invoice_filing_owner(self.conn, draft["invoice"]["invoice_id"])
        self.assertEqual(filing["selected"]["person_id"], parent["person_id"])
        update_invoice_filing_owner(self.conn, draft["invoice"]["invoice_id"], parent["person_id"])

    def test_organization_one_covered_client_auto_resolves(self):
        client = self._person("Org", "Client")
        org = self._party("Helpful Org", organization=True)
        self._relationship("Helpful Org Relationship", org["billing_party_id"], [client])
        session = self._session("orgone", [{"person_id": client["person_id"], "display_name": "Org Client"}], org["billing_party_id"])
        draft = self._draft(org["billing_party_id"], [session])
        filing = resolve_invoice_filing_owner(self.conn, draft["invoice"]["invoice_id"])
        self.assertEqual(filing["selected"]["owner_kind"], "billing_party")
        self.assertEqual(filing["selected"]["owner_id"], org["billing_party_id"])
        self.assertEqual(filing["source"], "billing_organization")

    def test_organization_multiple_clients_defaults_to_org_and_rejects_outside_client(self):
        a = self._person("Alpha", "Client")
        b = self._person("Beta", "Client")
        outside = self._person("Outside", "Client")
        org = self._party("Multi Org", organization=True)
        self._relationship("Multi Org Relationship", org["billing_party_id"], [a, b])
        sessions = [
            self._session("orga", [{"person_id": a["person_id"], "display_name": "Alpha Client"}], org["billing_party_id"]),
            self._session("orgb", [{"person_id": b["person_id"], "display_name": "Beta Client"}], org["billing_party_id"]),
        ]
        draft = self._draft(org["billing_party_id"], sessions)
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        self.assertTrue(preview["readiness"]["ready"])
        self.assertEqual(preview["filing_owner"]["selected"]["owner_id"], org["billing_party_id"])
        with self.assertRaises(ValueError):
            update_invoice_filing_owner(self.conn, draft["invoice"]["invoice_id"], outside["person_id"])
        updated = update_invoice_filing_owner(self.conn, draft["invoice"]["invoice_id"], b["person_id"])
        self.assertEqual(updated["filing_owner"]["selected"]["person_id"], b["person_id"])

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_finalization_freezes_filing_owner_snapshots_and_human_folder(self, fake_pdf):
        def write_pdf(_invoice, _lines, output_path, **_kwargs):
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"pdf")
            return "c" * 64
        fake_pdf.side_effect = write_pdf
        client = self._person("Folder", "Client")
        party = self._party("Folder Client", client["person_id"])
        session = self._session("folder", [{"person_id": client["person_id"], "display_name": "Folder Client"}], party["billing_party_id"])
        draft = self._draft(party["billing_party_id"], [session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        final = finalize_invoice(self.conn, draft["invoice"]["invoice_id"], expected_revision=preview["preview_revision"], pdf_root=self.root / "Invoices")
        person_code = client["person_code"]
        self.assertEqual(final["invoice"]["filing_owner_person_id"], client["person_id"])
        self.assertEqual(final["invoice"]["filing_owner_person_code_snapshot"], person_code)
        self.assertIn("Folder Client/May 2026/Invoice_2026-0001.pdf", final["invoice"]["pdf_path"])
        self.assertNotIn(f"{person_code} - Folder Client", final["invoice"]["pdf_path"])
        self.conn.execute("UPDATE people SET display_name = ? WHERE person_id = ?", ("Changed Name", client["person_id"]))
        self.conn.commit()
        reopened = get_invoice(self.conn, final["invoice"]["invoice_id"])
        self.assertEqual(reopened["invoice"]["filing_owner_display_name_snapshot"], "Folder Client")
        self.assertEqual(reopened["invoice"]["pdf_path"], final["invoice"]["pdf_path"])

    def test_collision_does_not_overwrite_existing_pdf_or_finalize(self):
        client = self._person("Collision", "Client")
        party = self._party("Collision Client", client["person_id"])
        session = self._session("collision", [{"person_id": client["person_id"], "display_name": "Collision Client"}], party["billing_party_id"])
        draft = self._draft(party["billing_party_id"], [session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        (self.root / "Invoices" / "Collision Client").mkdir(parents=True, exist_ok=True)
        target = self.root / "Invoices" / f"Collision Client [{client['person_code']}]" / "May 2026" / "Invoice_2026-0001.pdf"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"existing")
        with self.assertRaises(ValueError):
            finalize_invoice(self.conn, draft["invoice"]["invoice_id"], expected_revision=preview["preview_revision"], pdf_root=self.root / "Invoices")
        self.assertEqual(target.read_bytes(), b"existing")
        self.assertEqual(get_invoice(self.conn, draft["invoice"]["invoice_id"])["invoice"]["status"], "draft")

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_duplicate_display_name_uses_person_code_only_for_conflict(self, fake_pdf):
        def write_pdf(_invoice, _lines, output_path, **_kwargs):
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"pdf")
            return "d" * 64
        fake_pdf.side_effect = write_pdf
        first = self._person("Lou", "Yeager")
        second = self._person("Louis", "Yeager")
        self.conn.execute("UPDATE people SET display_name = ? WHERE person_id = ?", ("Lou Yeager", second["person_id"]))
        self.conn.commit()
        first_party = self._party("Lou Yeager", first["person_id"])
        second_party = self._party("Lou Yeager 2", second["person_id"])
        first_session = self._session("louone", [{"person_id": first["person_id"], "display_name": "Lou Yeager"}], first_party["billing_party_id"])
        second_session = self._session("loutwo", [{"person_id": second["person_id"], "display_name": "Lou Yeager"}], second_party["billing_party_id"])
        first_draft = self._draft(first_party["billing_party_id"], [first_session])
        second_draft = self._draft(second_party["billing_party_id"], [second_session])

        first_final = finalize_invoice(self.conn, first_draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
        second_final = finalize_invoice(self.conn, second_draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")

        self.assertIn("Lou Yeager/May 2026/Invoice_2026-0001.pdf", first_final["invoice"]["pdf_path"])
        self.assertIn(f"Lou Yeager [{second['person_code']}]/May 2026/Invoice_2026-0002.pdf", second_final["invoice"]["pdf_path"])

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_same_person_reuses_existing_new_client_folder(self, fake_pdf):
        def write_pdf(_invoice, _lines, output_path, **_kwargs):
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"pdf")
            return "g" * 64
        fake_pdf.side_effect = write_pdf
        client = self._person("Reuse", "Client")
        party = self._party("Reuse Client", client["person_id"])
        first_session = self._session("reusea", [{"person_id": client["person_id"], "display_name": "Reuse Client"}], party["billing_party_id"])
        first_draft = self._draft(party["billing_party_id"], [first_session])
        first_final = finalize_invoice(self.conn, first_draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
        self.conn.execute("UPDATE people SET display_name = ? WHERE person_id = ?", ("Reuse Client Renamed", client["person_id"]))
        self.conn.commit()
        second_session = self._session("reuseb", [{"person_id": client["person_id"], "display_name": "Reuse Client Renamed"}], party["billing_party_id"])
        second_draft = self._draft(party["billing_party_id"], [second_session])
        second_final = finalize_invoice(self.conn, second_draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
        self.assertIn("Reuse Client/May 2026/Invoice_2026-0001.pdf", first_final["invoice"]["pdf_path"])
        self.assertIn("Reuse Client/May 2026/Invoice_2026-0002.pdf", second_final["invoice"]["pdf_path"])

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_month_folder_uses_billing_period_not_invoice_date_or_wall_clock(self, fake_pdf):
        def write_pdf(_invoice, _lines, output_path, **_kwargs):
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"pdf")
            return "e" * 64
        fake_pdf.side_effect = write_pdf
        client = self._person("Month", "Client")
        party = self._party("Month Client", client["person_id"])
        session = self._session("monthsrc", [{"person_id": client["person_id"], "display_name": "Month Client"}], party["billing_party_id"])
        self.conn.execute("UPDATE sessions SET session_date = ? WHERE id = ?", ("2026-06-15", session["id"]))
        self.conn.commit()
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": party["billing_party_id"],
            "billing_period_start": "2026-06-01",
            "billing_period_end": "2026-06-30",
            "invoice_date": "2026-07-02",
            "session_ids": [session["id"]],
        })
        final = finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
        self.assertIn("Month Client/June 2026/Invoice_2026-0001.pdf", final["invoice"]["pdf_path"])
        self.assertNotIn("July 2026", final["invoice"]["pdf_path"])

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_invoice_uses_configured_documents_client_files_root(self, fake_pdf):
        def write_pdf(_invoice, _lines, output_path, **_kwargs):
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"pdf")
            return "1" * 64

        fake_pdf.side_effect = write_pdf
        client_files = self.root / "Documents" / "Jordana Billing" / "Client Files"
        client = self._person("Docs", "Client")
        party = self._party("Docs Client", client["person_id"])
        session = self._session("docsroot", [{"person_id": client["person_id"], "display_name": "Docs Client"}], party["billing_party_id"])
        draft = self._draft(party["billing_party_id"], [session])
        with patch.dict("os.environ", {"JORDANA_INVOICES_DIR": str(client_files)}):
            final = finalize_invoice(self.conn, draft["invoice"]["invoice_id"])

        self.assertEqual(
            Path(final["invoice"]["pdf_path"]),
            client_files / "Docs Client" / "May 2026" / "Invoice_2026-0001.pdf",
        )

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_sanitized_display_name_folder_is_deterministic(self, fake_pdf):
        def write_pdf(_invoice, _lines, output_path, **_kwargs):
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"pdf")
            return "f" * 64
        fake_pdf.side_effect = write_pdf
        client = create_person(self.conn, {"first_name": "Bad", "last_name": "Name", "display_name": "Bad / Name: Jr."})
        party = self._party("Bad Name", client["person_id"])
        session = self._session("badname", [{"person_id": client["person_id"], "display_name": "Bad / Name: Jr."}], party["billing_party_id"])
        draft = self._draft(party["billing_party_id"], [session])
        final = finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
        self.assertIn("Bad Name Jr/May 2026/Invoice_2026-0001.pdf", final["invoice"]["pdf_path"])

    @patch("jordana_invoice.invoice_services.subprocess.run")
    def test_document_actions_support_new_and_legacy_paths(self, fake_open):
        client = self._person("Finder", "Client")
        new_pdf = self.root / "Invoices" / "Finder Client" / "May 2026" / "Invoice_2026-0001.pdf"
        old_pdf = self.root / "Invoices" / f"{client['person_code']} - Finder Client" / "2026" / "Invoice_2026-0000.pdf"
        new_pdf.parent.mkdir(parents=True, exist_ok=True)
        old_pdf.parent.mkdir(parents=True, exist_ok=True)
        new_pdf.write_bytes(b"pdf")
        old_pdf.write_bytes(b"pdf")
        now = "2026-06-01T00:00:00"
        party = self._party("Finder Client", client["person_id"])
        for invoice_id, pdf_path in (("new-doc-action", new_pdf), ("old-doc-action", old_pdf)):
            self.conn.execute(
                """
                INSERT INTO invoices (
                  invoice_id, invoice_number, status, bill_to_party_id, billing_period_start,
                  billing_period_end, invoice_date, total_cents, delivery_method,
                  filing_owner_person_id, filing_owner_person_code_snapshot,
                  filing_owner_display_name_snapshot, pdf_path, pdf_sha256,
                  created_at, updated_at, finalized_at
                ) VALUES (?, ?, 'finalized', ?, '2026-05-01', '2026-05-31', '2026-05-31',
                  1000, 'email', ?, ?, 'Finder Client', ?, ?, ?, ?, ?)
                """,
                (invoice_id, invoice_id, party["billing_party_id"], client["person_id"], client["person_code"], str(pdf_path), "a" * 64, now, now, now),
            )
        self.conn.commit()

        trusted_invoice_document_action(self.conn, "new-doc-action", "open_client_folder", pdf_root=self.root / "Invoices")
        self.assertEqual(fake_open.call_args.args[0], ["open", str((self.root / "Invoices" / "Finder Client").resolve(strict=False))])
        trusted_invoice_document_action(self.conn, "old-doc-action", "show_in_finder", pdf_root=self.root / "Invoices")
        self.assertEqual(fake_open.call_args.args[0], ["open", "-R", str(old_pdf.resolve(strict=False))])

    def test_document_actions_reject_paths_outside_configured_invoice_root(self):
        client = self._person("Outside", "Client")
        party = self._party("Outside Client", client["person_id"])
        bad_pdf = self.root / "Other Files" / "Outside Client" / "Invoice_2026-0001.pdf"
        bad_pdf.parent.mkdir(parents=True, exist_ok=True)
        bad_pdf.write_bytes(b"pdf")
        now = "2026-06-01T00:00:00"
        self.conn.execute(
            """
            INSERT INTO invoices (
              invoice_id, invoice_number, status, bill_to_party_id, billing_period_start,
              billing_period_end, invoice_date, total_cents, delivery_method,
              filing_owner_person_id, filing_owner_person_code_snapshot,
              filing_owner_display_name_snapshot, pdf_path, pdf_sha256,
              created_at, updated_at, finalized_at
            ) VALUES ('outside-doc-action', '2026-0001', 'finalized', ?, '2026-05-01',
              '2026-05-31', '2026-05-31', 1000, 'email', ?, ?, 'Outside Client',
              ?, ?, ?, ?, ?)
            """,
            (party["billing_party_id"], client["person_id"], client["person_code"], str(bad_pdf), "b" * 64, now, now, now),
        )
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "outside the configured invoice folder"):
            trusted_invoice_document_action(
                self.conn,
                "outside-doc-action",
                "open_pdf",
                pdf_root=self.root / "Documents" / "Jordana Billing" / "Client Files",
            )

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_storage_failure_rolls_back_and_removes_partial_file(self, fake_pdf):
        def fail_after_write(_invoice, _lines, output_path, **_kwargs):
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"partial")
            raise RuntimeError("storage failed")
        fake_pdf.side_effect = fail_after_write
        client = self._person("Partial", "Client")
        party = self._party("Partial Client", client["person_id"])
        session = self._session("partial", [{"person_id": client["person_id"], "display_name": "Partial Client"}], party["billing_party_id"])
        draft = self._draft(party["billing_party_id"], [session])
        preview = preview_finalization(self.conn, draft["invoice"]["invoice_id"])
        with self.assertRaises(RuntimeError):
            finalize_invoice(self.conn, draft["invoice"]["invoice_id"], expected_revision=preview["preview_revision"], pdf_root=self.root / "Invoices")
        invoice = get_invoice(self.conn, draft["invoice"]["invoice_id"])["invoice"]
        self.assertEqual(invoice["status"], "draft")
        self.assertIsNone(invoice["pdf_path"])
        self.assertFalse((self.root / "Invoices" / "Partial Client" / "May 2026" / "Invoice_2026-0001.pdf").exists())

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_finalized_billing_party_owner_snapshot_survives_ineligibility(self, fake_pdf):
        def write_pdf(_invoice, _lines, output_path, **_kwargs):
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"pdf")
            return "h" * 64
        fake_pdf.side_effect = write_pdf
        client = self._person("Org", "Client")
        org = self._party("Snapshot Org", organization=True)
        self._relationship("Snapshot Org Rel", org["billing_party_id"], [client])
        session = self._session("snaporg", [{"person_id": client["person_id"], "display_name": "Org Client"}], org["billing_party_id"])
        draft = self._draft(org["billing_party_id"], [session])
        final = finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
        self.assertEqual(final["invoice"]["filing_owner_kind"], "billing_party")
        self.assertEqual(final["invoice"]["filing_owner_record_id"], org["billing_party_id"])
        self.assertIn("Snapshot Org/May 2026/Invoice_2026-0001.pdf", final["invoice"]["pdf_path"])
        self.conn.execute("UPDATE billing_parties SET active = 0 WHERE billing_party_id = ?", (org["billing_party_id"],))
        self.conn.commit()
        reopened = resolve_invoice_filing_owner(self.conn, final["invoice"]["invoice_id"])
        self.assertIsNotNone(reopened["selected"])
        self.assertEqual(reopened["source"], "finalized_snapshot")
        self.assertEqual(reopened["selected"]["owner_kind"], "billing_party")
        self.assertEqual(reopened["selected"]["owner_id"], org["billing_party_id"])
        self.assertEqual(reopened["selected"]["display_name"], "Snapshot Org")

    @patch("jordana_invoice.invoice_services.generate_invoice_pdf")
    def test_month_folder_falls_back_to_billing_period_start_when_month_absent(self, fake_pdf):
        def write_pdf(_invoice, _lines, output_path, **_kwargs):
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"pdf")
            return "i" * 64
        fake_pdf.side_effect = write_pdf
        client = self._person("Fallback", "Client")
        party = self._party("Fallback Client", client["person_id"])
        session = self._session("fallback", [{"person_id": client["person_id"], "display_name": "Fallback Client"}], party["billing_party_id"])
        self.conn.execute("UPDATE sessions SET session_date = ? WHERE id = ?", ("2026-06-20", session["id"]))
        self.conn.commit()
        draft = create_invoice_draft(self.conn, {
            "bill_to_party_id": party["billing_party_id"],
            "billing_period_start": "2026-06-15",
            "billing_period_end": "2026-07-14",
            "invoice_date": "2026-07-14",
            "session_ids": [session["id"]],
        })
        inv = get_invoice(self.conn, draft["invoice"]["invoice_id"])["invoice"]
        self.assertIsNone(inv["billing_month"])
        final = finalize_invoice(self.conn, draft["invoice"]["invoice_id"], pdf_root=self.root / "Invoices")
        self.assertIn("Fallback Client/June 2026/Invoice_2026-0001.pdf", final["invoice"]["pdf_path"])
        self.assertNotIn("July 2026", final["invoice"]["pdf_path"])


if __name__ == "__main__":
    unittest.main()
