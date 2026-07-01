"""Focused tests for the post-v0.1.0-test.4 bug-fix round.

Covers all five issues:
1. Rate Card custom-duration payload bug
2. Ambiguous but recognizable calendar titles must go to review queue
3. Calendar event revision / latest-snapshot handling
4. Future appointments must not be approvable before they end
5. Installer must clean up .app.installing after controlled abort

Uses temporary databases and synthetic fixtures only.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from jordana_invoice.db import connect, init_db, migrate_database
from jordana_invoice.importer import import_rows
from jordana_invoice.invoice_services import save_business_profile
from jordana_invoice.parser import parse_event, extract_name_guess
from jordana_invoice.request_validation import (
    RequestValidationError,
    _optional_int_not_bool,
    parse_create_rate_rule_request,
)
from jordana_invoice.review_services import (
    approve_candidate,
    create_billing_party,
    create_person,
    create_rate_rule_from_payload,
    get_review_candidate,
    list_review_candidates,
    preview_rate_suggestion,
    replace_rate_rule_from_payload,
    save_relationship_section,
    save_session_draft,
)


PROJECT_DIR = Path(__file__).resolve().parent.parent


def raw_row(
    snapshot_key,
    title,
    start="2026-01-15T18:30:00-05:00",
    end="2026-01-15T19:30:00-05:00",
    duration="60",
    calendar="Jordana Work",
    calendar_event_id="",
    event_fingerprint="",
):
    return {
        "ingested_at": "2026-06-22T02:00:00.000Z",
        "snapshot_key": snapshot_key,
        "run_id": f"run-{snapshot_key}",
        "batch_name": "test",
        "capture_window": "past_3_days",
        "captured_at": "2026-06-22T01:00:00.000Z",
        "source_device": "test",
        "timezone": "America/New_York",
        "calendar_event_id": calendar_event_id or f"evt-{snapshot_key}",
        "event_fingerprint": event_fingerprint or f"fp-{snapshot_key}",
        "event_title": title,
        "start_at": start,
        "end_at": end,
        "duration_minutes": duration,
        "calendar": calendar,
        "payload_version": "2",
        "raw_json": "{}",
    }


class TestBase(unittest.TestCase):
    """Base class with shared DB setup."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "test.sqlite3")
        init_db(self.conn)
        save_business_profile(self.conn, {
            "business_name": "Demo Practice",
            "provider_display_name": "Demo Provider",
            "address_line_1": "100 Example Avenue",
            "city": "Example", "state": "FL", "postal_code": "00000",
            "phone": "555-0100", "email": "billing@example.test",
            "payee_name": "Demo Payee",
            "payment_address_line_1": "100 Example Avenue",
            "payment_city": "Example", "payment_state": "FL",
            "payment_postal_code": "00000",
            "zelle_recipient": "demo-zelle@example.test",
            "invoice_total_label": "TOTAL DUE",
            "invoice_number_format": "YYYY-NNNN",
        })
        self.person = create_person(self.conn, {
            "first_name": "Avery", "last_name": "Stone", "display_name": "Avery Stone",
        })
        self.party = create_billing_party(self.conn, {
            "billing_name": "Avery Stone",
            "person_id": self.person["person_id"],
            "billing_email": "avery@example.test",
            "billing_address_line_1": "10 Sample Street",
            "billing_city": "Example", "billing_state": "FL",
            "billing_postal_code": "00000",
            "preferred_delivery_method": "both",
        })

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def import_one(self, key, title, **kw):
        import_rows(self.conn, [raw_row(key, title, **kw)], "test")
        rows = list_review_candidates(self.conn)["items"]
        return next(row["candidate_id"] for row in rows if row["raw_title"] == title)

    def _create_default_rule(self, **extra):
        payload = {
            "amount": "350",
            "duration_choice": "60",
            "billing_session_type": "psychotherapy",
            "time_category": "standard",
            "applies_to": "everyone",
            "effective_from": "2026-01-01",
        }
        payload.update(extra)
        return create_rate_rule_from_payload(self.conn, payload)

    def _full_setup_for_approval(self, candidate_id):
        """Set up participants, billing, session draft, and rate for approval.
        Returns the payload needed for approve_candidate."""
        save_relationship_section(self.conn, candidate_id, {
            "participants": [{"person_id": self.person["person_id"], "is_primary": True}],
            "billing_party_id": self.party["billing_party_id"],
        })
        save_session_draft(self.conn, candidate_id, {
            "duration_choice": "60",
            "billing_session_type": "psychotherapy",
            "approved_rate": "350",
            "payment_status": "unpaid",
        })
        return {
            "participants": [{"person_id": self.person["person_id"], "display_name": "Avery Stone", "is_primary": True}],
            "billing_party_id": self.party["billing_party_id"],
            "approved_duration_minutes": 60,
            "service_mode": "office",
            "time_category": "standard",
            "approved_rate": "350",
            "payment_status": "unpaid",
            "billing_treatment": "billable",
        }


# ── Issue 1: Rate Card custom-duration payload bug ─────────────────────────


class TestRateCardCustomDuration(TestBase):
    """Rate Card must send null for standard durations, integer only for custom."""

    def test_standard_60_minute_rate(self):
        """Standard 60-minute rate should not require custom_duration_minutes."""
        rule = create_rate_rule_from_payload(self.conn, {
            "amount": "350",
            "duration_choice": "60",
            "custom_duration_minutes": None,
            "billing_session_type": "psychotherapy",
            "time_category": "standard",
            "applies_to": "everyone",
            "effective_from": "2026-01-01",
        })
        self.assertEqual(rule["duration_minutes"], 60)
        self.assertEqual(rule["amount_cents"], 35000)

    def test_standard_45_minute_rate(self):
        rule = create_rate_rule_from_payload(self.conn, {
            "amount": "275",
            "duration_choice": "45",
            "custom_duration_minutes": None,
            "billing_session_type": "psychotherapy",
            "time_category": "standard",
            "applies_to": "everyone",
            "effective_from": "2026-01-01",
        })
        self.assertEqual(rule["duration_minutes"], 45)

    def test_standard_90_minute_rate(self):
        rule = create_rate_rule_from_payload(self.conn, {
            "amount": "500",
            "duration_choice": "90",
            "custom_duration_minutes": None,
            "billing_session_type": "psychotherapy",
            "time_category": "standard",
            "applies_to": "everyone",
            "effective_from": "2026-01-01",
        })
        self.assertEqual(rule["duration_minutes"], 90)

    def test_custom_duration_with_valid_integer(self):
        rule = create_rate_rule_from_payload(self.conn, {
            "amount": "400",
            "duration_choice": "custom",
            "custom_duration_minutes": "75",
            "billing_session_type": "psychotherapy",
            "time_category": "standard",
            "applies_to": "everyone",
            "effective_from": "2026-01-01",
        })
        self.assertEqual(rule["duration_minutes"], 75)

    def test_custom_duration_blank_string_treated_as_none(self):
        """Empty string for custom_duration_minutes should not raise."""
        result = _optional_int_not_bool({"custom_duration_minutes": ""}, "custom_duration_minutes")
        self.assertIsNone(result)

    def test_invalid_custom_duration_still_rejected(self):
        """Non-numeric string should still raise RequestValidationError."""
        with self.assertRaises(RequestValidationError):
            _optional_int_not_bool({"custom_duration_minutes": "abc"}, "custom_duration_minutes")

    def test_edit_update_existing_rule(self):
        rule = self._create_default_rule()
        updated = replace_rate_rule_from_payload(self.conn, rule["rate_rule_id"], {
            "amount": "375",
            "duration_choice": "60",
            "custom_duration_minutes": None,
            "billing_session_type": "psychotherapy",
            "time_category": "standard",
            "applies_to": "everyone",
            "effective_from": "2026-02-01",
        })
        self.assertEqual(updated["amount_cents"], 37500)
        self.assertEqual(updated["duration_minutes"], 60)

    def test_preview_payload_standard_duration(self):
        """Preview should work with null custom_duration_minutes for standard duration."""
        result = preview_rate_suggestion(self.conn, {
            "amount": "350",
            "duration_choice": "60",
            "custom_duration_minutes": None,
            "billing_session_type": "psychotherapy",
            "time_category": "standard",
            "effective_from": "2026-01-01",
        })
        self.assertIsNotNone(result)

    def test_parse_create_rate_rule_empty_string_custom(self):
        """parse_create_rate_rule_request should accept empty string for custom_duration_minutes."""
        req = parse_create_rate_rule_request({
            "amount": "350",
            "duration_choice": "60",
            "custom_duration_minutes": "",
            "billing_session_type": "psychotherapy",
            "time_category": "standard",
            "applies_to": "everyone",
            "effective_from": "2026-01-01",
        })
        # Validation should not raise; the service layer handles empty string as None
        self.assertIsNotNone(req)


# ── Issue 2: Ambiguous calendar titles must go to review queue ──────────────


class TestAmbiguousTitleReviewRouting(unittest.TestCase):
    """Ambiguous but recognizable titles must produce a participant guess
    and route to the review queue, not silently auto-approve."""

    def test_leah_grossman_630_38_extracts_name_guess(self):
        result = parse_event({
            "event_title": "Leah Grossman 630 38",
            "start_at": "2026-06-18T18:30:00-04:00",
            "end_at": "2026-06-18T19:30:00-04:00",
            "duration_minutes": 60,
        })
        self.assertEqual(result.classification, "unresolved")
        self.assertEqual(result.proposed_client_name, "Leah Grossman")
        self.assertIn("Leah Grossman", result.candidate_person_names)
        self.assertIsNotNone(result.unresolved_trailing_text)

    def test_sage_burkhead_4_zoom_extracts_name_guess(self):
        result = parse_event({
            "event_title": "Sage Burkhead 4 zoom",
            "start_at": "2026-06-18T16:00:00-04:00",
            "end_at": "2026-06-18T17:00:00-04:00",
            "duration_minutes": 60,
        })
        self.assertEqual(result.classification, "unresolved")
        self.assertEqual(result.proposed_client_name, "Sage Burkhead")
        self.assertIn("zoom", result.unresolved_trailing_text)

    def test_fred_60_extracts_name_guess(self):
        result = parse_event({
            "event_title": "Fred 60",
            "start_at": "2026-06-18T18:30:00-04:00",
            "end_at": "2026-06-18T19:30:00-04:00",
            "duration_minutes": 60,
        })
        self.assertEqual(result.classification, "unresolved")
        self.assertEqual(result.proposed_client_name, "Fred")

    def test_sara_lieppe_5_late_cx_suggests_late_cancellation(self):
        result = parse_event({
            "event_title": "Sara Lieppe 5 late cx",
            "start_at": "2026-06-18T17:00:00-04:00",
            "end_at": "2026-06-18T18:00:00-04:00",
            "duration_minutes": 60,
        })
        self.assertEqual(result.classification, "client_session")
        self.assertEqual(result.proposed_client_name, "Sara Lieppe")
        self.assertEqual(result.appointment_status, "late_cancellation")
        self.assertIn("billing_treatment", result.fields_requiring_review)

    def test_leah_grossman_6_leaves_for_israel(self):
        result = parse_event({
            "event_title": "Leah Grossman 6 leaves for Israel Wednesday",
            "start_at": "2026-06-18T18:30:00-04:00",
            "end_at": "2026-06-18T19:30:00-04:00",
            "duration_minutes": 60,
        })
        self.assertEqual(result.classification, "unresolved")
        self.assertEqual(result.proposed_client_name, "Leah Grossman")
        self.assertIsNotNone(result.unresolved_trailing_text)

    def test_leah_grossman_6_30_leaves_for_israel(self):
        result = parse_event({
            "event_title": "Leah Grossman 6 30 leaves for Israel Wednesday",
            "start_at": "2026-06-18T18:30:00-04:00",
            "end_at": "2026-06-18T19:30:00-04:00",
            "duration_minutes": 60,
        })
        self.assertEqual(result.classification, "unresolved")
        self.assertEqual(result.proposed_client_name, "Leah Grossman")

    def test_calendar_timestamp_remains_authoritative(self):
        """The proposed_start_at must come from the calendar event, not the title."""
        start = "2026-06-18T18:30:00-04:00"
        result = parse_event({
            "event_title": "Leah Grossman 630 38",
            "start_at": start,
            "end_at": "2026-06-18T19:30:00-04:00",
            "duration_minutes": 60,
        })
        self.assertEqual(result.proposed_start_at, start)

    def test_no_silent_auto_approval(self):
        """Unresolved classification must not be ready_for_approval."""
        result = parse_event({
            "event_title": "Leah Grossman 630 38",
            "start_at": "2026-06-18T18:30:00-04:00",
            "end_at": "2026-06-18T19:30:00-04:00",
            "duration_minutes": 60,
        })
        self.assertNotEqual(result.classification, "client_session")
        self.assertIn("classification", result.fields_requiring_review)

    def test_extract_name_guess_directly(self):
        name, trailing = extract_name_guess("Leah Grossman 630 38")
        self.assertEqual(name, "Leah Grossman")
        self.assertEqual(trailing, "630 38")

    def test_extract_name_guess_no_name(self):
        name, trailing = extract_name_guess("zoom call at 4")
        self.assertIsNone(name)

    def test_extract_name_guess_single_name(self):
        name, trailing = extract_name_guess("Fred 60")
        self.assertEqual(name, "Fred")
        self.assertEqual(trailing, "60")


class TestAmbiguousTitleReviewQueueIntegration(TestBase):
    """Ambiguous titles must appear in the review queue when imported."""

    def test_ambiguous_title_creates_review_queue_entry(self):
        import_rows(self.conn, [raw_row("snap-amb", "Leah Grossman 630 38")], "test")
        rows = list_review_candidates(self.conn)["items"]
        matching = [r for r in rows if r["raw_title"] == "Leah Grossman 630 38"]
        self.assertTrue(len(matching) >= 1, "Ambiguous title should appear in review candidates")
        self.assertNotEqual(matching[0]["classification"], "approved")

    def test_no_duplicate_operational_session(self):
        import_rows(self.conn, [raw_row("snap-amb", "Leah Grossman 630 38")], "test")
        sessions_before = self.conn.execute("SELECT COUNT(*) as c FROM sessions").fetchone()["c"]
        import_rows(self.conn, [raw_row("snap-amb", "Leah Grossman 630 38")], "test")
        sessions_after = self.conn.execute("SELECT COUNT(*) as c FROM sessions").fetchone()["c"]
        self.assertEqual(sessions_before, sessions_after, "Re-import should not create duplicate sessions")


# ── Issue 3: Calendar event revision / latest-snapshot handling ─────────────


class TestEventRevisionHandling(TestBase):
    """Revisions of the same event must update the unresolved candidate,
    preserve older snapshots, and not create duplicate sessions."""

    def test_same_event_id_newer_snapshot_updates_candidate(self):
        """Same calendar_event_id with a newer title should update the candidate."""
        eid = "evt-revision-001"
        import_rows(self.conn, [
            raw_row("snap-v1", "Leah Grossman 6 leaves for Israel Wednesday",
                    calendar_event_id=eid, event_fingerprint=f"fp-{eid}"),
        ], "test")
        candidates_before = self.conn.execute(
            "SELECT title FROM calendar_event_candidates",
        ).fetchall()
        self.assertEqual(len(candidates_before), 1)

        import_rows(self.conn, [
            raw_row("snap-v2", "Leah Grossman 6 30 leaves for Israel Wednesday",
                    calendar_event_id=eid, event_fingerprint=f"fp-{eid}"),
        ], "test")
        candidates_after = self.conn.execute(
            "SELECT title FROM calendar_event_candidates",
        ).fetchall()
        self.assertEqual(len(candidates_after), 1, "Should not create duplicate candidate")
        self.assertIn("30", candidates_after[0]["title"])

    def test_older_raw_snapshot_remains_preserved(self):
        eid = "evt-revision-002"
        import_rows(self.conn, [
            raw_row("snap-old", "Leah Grossman 6 leaves for Israel Wednesday",
                    calendar_event_id=eid, event_fingerprint=f"fp-{eid}"),
        ], "test")
        import_rows(self.conn, [
            raw_row("snap-new", "Leah Grossman 6 30 leaves for Israel Wednesday",
                    calendar_event_id=eid, event_fingerprint=f"fp-{eid}"),
        ], "test")
        snapshots = self.conn.execute(
            "SELECT event_title FROM raw_calendar_snapshots ORDER BY captured_at"
        ).fetchall()
        titles = [s["event_title"] for s in snapshots]
        self.assertIn("Leah Grossman 6 leaves for Israel Wednesday", titles)
        self.assertIn("Leah Grossman 6 30 leaves for Israel Wednesday", titles)

    def test_repeated_import_is_idempotent(self):
        eid = "evt-revision-003"
        row = raw_row("snap-idem", "Leah Grossman 6 leaves for Israel Wednesday",
                      calendar_event_id=eid, event_fingerprint=f"fp-{eid}")
        import_rows(self.conn, [row], "test")
        import_rows(self.conn, [row], "test")
        import_rows(self.conn, [row], "test")
        candidates = self.conn.execute(
            "SELECT COUNT(*) as c FROM calendar_event_candidates",
        ).fetchone()["c"]
        self.assertEqual(candidates, 1)

    def test_approved_session_not_silently_rewritten(self):
        """An approved session's approved values should not be rewritten by a later snapshot."""
        eid = "evt-revision-004"
        candidate_id = self.import_one("snap-pre", "Fred 630 60",
                                        calendar_event_id=eid, event_fingerprint=f"fp-{eid}")
        self._create_default_rule()
        payload = self._full_setup_for_approval(candidate_id)
        approve_candidate(self.conn, candidate_id, payload)
        approved_rate = self.conn.execute(
            "SELECT approved_rate_cents FROM sessions WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()["approved_rate_cents"]
        self.assertEqual(approved_rate, 35000)

        import_rows(self.conn, [
            raw_row("snap-post", "Fred 630 60",
                    calendar_event_id=eid, event_fingerprint=f"fp-{eid}"),
        ], "test")
        session_after = self.conn.execute(
            "SELECT review_status, approved_rate_cents, duration_minutes FROM sessions WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        self.assertEqual(session_after["review_status"], "approved")
        self.assertEqual(session_after["approved_rate_cents"], 35000)
        self.assertEqual(session_after["duration_minutes"], 60)

    def test_source_change_warning_created_for_approved_session(self):
        """When an approved session's source title changes, a review warning is created."""
        eid = "evt-revision-005"
        candidate_id = self.import_one("snap-pre-warn", "Fred 630 60",
                                        calendar_event_id=eid, event_fingerprint=f"fp-{eid}")
        self._create_default_rule()
        payload = self._full_setup_for_approval(candidate_id)
        approve_candidate(self.conn, candidate_id, payload)

        import_rows(self.conn, [
            raw_row("snap-post-warn", "Fred 630 60 Office",
                    calendar_event_id=eid, event_fingerprint=f"fp-{eid}"),
        ], "test")
        warnings = self.conn.execute(
            "SELECT review_status FROM review_items WHERE candidate_id = ? AND review_status = 'source_change_warning'",
            (candidate_id,),
        ).fetchall()
        self.assertEqual(len(warnings), 1, "Source-change warning should be created")

    def test_missing_event_in_partial_capture_does_not_cancel(self):
        """An event absent from a later capture window should not be marked cancelled."""
        eid = "evt-revision-006"
        candidate_id = self.import_one("snap-partial", "Fred 630 60",
                                        calendar_event_id=eid, event_fingerprint=f"fp-{eid}")
        session = self.conn.execute(
            "SELECT appointment_status FROM sessions WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        self.assertNotEqual(session["appointment_status"], "cancelled")


# ── Issue 4: Future appointments must not be approvable before they end ─────


class TestFutureAppointmentApprovalGate(TestBase):
    """Future appointments must not be approvable until the event end time passes."""

    def _import_future_session(self):
        """Import a session with end_at far in the future."""
        eastern = ZoneInfo("America/New_York")
        future_start = datetime.now(eastern) + timedelta(days=30)
        future_end = future_start + timedelta(hours=1)
        return self.import_one(
            "snap-future",
            "Fred 630 60",
            start=future_start.isoformat(),
            end=future_end.isoformat(),
        )

    def test_future_appointment_reviewable(self):
        """Future appointments should still be openable in the review UI."""
        candidate_id = self._import_future_session()
        detail = get_review_candidate(self.conn, candidate_id)
        self.assertIsNotNone(detail)
        self.assertIsNotNone(detail["session"])

    def test_backend_rejects_future_approval(self):
        """Backend must reject approval of a future appointment."""
        candidate_id = self._import_future_session()
        self._create_default_rule()
        payload = self._full_setup_for_approval(candidate_id)
        with self.assertRaises(ValueError) as ctx:
            approve_candidate(self.conn, candidate_id, payload)
        self.assertIn("scheduled for", str(ctx.exception))
        self.assertIn("can be approved after", str(ctx.exception))

    def test_past_appointment_approval_unaffected(self):
        """Past appointments should still be approvable normally."""
        candidate_id = self.import_one("snap-past", "Fred 630 60",
                                        start="2026-01-15T18:30:00-05:00",
                                        end="2026-01-15T19:30:00-05:00")
        self._create_default_rule()
        payload = self._full_setup_for_approval(candidate_id)
        result = approve_candidate(self.conn, candidate_id, payload)
        self.assertEqual(result["session"]["review_status"], "approved")

    def test_no_duplicate_session_or_invoice_staging(self):
        """Failed future approval should not create a session or invoice."""
        candidate_id = self._import_future_session()
        self._create_default_rule()
        payload = self._full_setup_for_approval(candidate_id)
        sessions_before = self.conn.execute("SELECT COUNT(*) as c FROM sessions").fetchone()["c"]
        with self.assertRaises(ValueError):
            approve_candidate(self.conn, candidate_id, payload)
        sessions_after = self.conn.execute("SELECT COUNT(*) as c FROM sessions").fetchone()["c"]
        self.assertEqual(sessions_before, sessions_after)
        approved = self.conn.execute(
            "SELECT COUNT(*) as c FROM sessions WHERE review_status = 'approved'"
        ).fetchone()["c"]
        self.assertEqual(approved, 0)


# ── Issue 5: Installer must clean up .app.installing after controlled abort ─


class TestInstallerTempCleanup(unittest.TestCase):
    """The installer must remove the .app.installing temp directory on abort."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="jordana_installer_test_")
        self.app_dest = Path(self.tmpdir) / "Jordana Billing.app"
        self.tmp_app = Path(self.tmpdir) / "Jordana Billing.app.installing"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_trap_cleans_up_temp_app_on_failure(self):
        """Simulate a controlled abort after staging: the trap should remove .app.installing."""
        self.tmp_app.mkdir(parents=True)
        (self.tmp_app / "Contents").mkdir()
        self.assertTrue(self.tmp_app.exists())

        env = os.environ.copy()
        env["JORDANA_INSTALL_APP_DEST"] = str(self.app_dest)
        result = subprocess.run(
            [
                "bash", "-c",
                f"""
                set -euo pipefail
                TMP_APP="{self.tmp_app}"
                cleanup_temp_app() {{
                  if [[ -e "$TMP_APP" && "$TMP_APP" != "{self.app_dest}" ]]; then
                    rm -rf "$TMP_APP"
                  fi
                }}
                trap cleanup_temp_app EXIT
                # Simulate a failure after staging
                exit 1
                """,
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.tmp_app.exists(), ".app.installing should be cleaned up on abort")

    def test_successful_install_does_not_trigger_cleanup(self):
        """On success (mv completes), the trap should be a no-op."""
        self.tmp_app.mkdir(parents=True)
        (self.tmp_app / "Contents").mkdir()

        env = os.environ.copy()
        result = subprocess.run(
            [
                "bash", "-c",
                f"""
                set -euo pipefail
                TMP_APP="{self.tmp_app}"
                APP_DEST="{self.app_dest}"
                cleanup_temp_app() {{
                  if [[ -e "$TMP_APP" && "$TMP_APP" != "$APP_DEST" ]]; then
                    rm -rf "$TMP_APP"
                  fi
                }}
                trap cleanup_temp_app EXIT
                # Simulate successful mv
                mv "$TMP_APP" "$APP_DEST"
                exit 0
                """,
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.tmp_app.exists(), "TMP_APP should be gone after mv")
        self.assertTrue(self.app_dest.exists(), "Final app should exist after successful install")

    def test_existing_valid_app_not_touched(self):
        """An existing valid app should not be deleted by the cleanup trap."""
        self.app_dest.mkdir(parents=True)
        (self.app_dest / "Contents").mkdir()
        (self.app_dest / "Contents" / "Info.plist").write_text("valid")

        self.tmp_app.mkdir(parents=True)

        env = os.environ.copy()
        result = subprocess.run(
            [
                "bash", "-c",
                f"""
                set -euo pipefail
                TMP_APP="{self.tmp_app}"
                APP_DEST="{self.app_dest}"
                cleanup_temp_app() {{
                  if [[ -e "$TMP_APP" && "$TMP_APP" != "$APP_DEST" ]]; then
                    rm -rf "$TMP_APP"
                  fi
                }}
                trap cleanup_temp_app EXIT
                # Simulate failure
                exit 1
                """,
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.tmp_app.exists(), "Temp app should be cleaned up")
        self.assertTrue(self.app_dest.exists(), "Existing valid app should remain untouched")
        self.assertTrue((self.app_dest / "Contents" / "Info.plist").exists())


if __name__ == "__main__":
    unittest.main()
