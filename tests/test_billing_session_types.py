"""Tests for billing session type derivation and duration choices."""
import tempfile
import unittest
from pathlib import Path

from jordana_invoice.db import connect, init_db
from jordana_invoice.parser import (
    BILLING_SESSION_TYPES,
    BILLING_SESSION_TYPE_LABELS,
    STANDARD_DURATION_CHOICES,
    derive_billing_session_type,
    derive_appointment_method,
    derive_duration_choice,
    check_late_evening,
    parse_event,
)


class BillingSessionTypeDerivationTests(unittest.TestCase):
    """Test the billing session type priority logic."""

    def test_standard_session_weekday_daytime(self):
        """Weekday daytime session should be standard psychotherapy."""
        billing_type, source, suggested = derive_billing_session_type(
            service_mode="phone",
            is_weekend=False,
            is_evening=False,
        )
        self.assertEqual(billing_type, "psychotherapy")
        self.assertEqual(source, "auto")
        self.assertFalse(suggested)

    def test_evening_session_weekday_after_8pm(self):
        """Weekday session at 8 PM or later should be evening."""
        billing_type, source, suggested = derive_billing_session_type(
            service_mode="office",
            is_weekend=False,
            is_evening=True,
        )
        self.assertEqual(billing_type, "psychotherapy_evening")
        self.assertEqual(source, "auto")

    def test_weekend_session_overrides_evening(self):
        """Weekend session should be weekend even if also evening."""
        billing_type, source, suggested = derive_billing_session_type(
            service_mode="facetime",
            is_weekend=True,
            is_evening=True,
        )
        self.assertEqual(billing_type, "psychotherapy_weekend")
        self.assertEqual(source, "auto")

    def test_house_call_explicit_overrides_weekend(self):
        """Explicit house call should override weekend."""
        billing_type, source, suggested = derive_billing_session_type(
            service_mode="house_call",
            is_weekend=True,
            is_evening=False,
        )
        self.assertEqual(billing_type, "psychotherapy_house_call")
        self.assertEqual(source, "auto")

    def test_house_call_from_location_suggests_confirmation(self):
        """Location-based house call should suggest confirmation."""
        billing_type, source, suggested = derive_billing_session_type(
            service_mode="office",
            is_weekend=False,
            is_evening=False,
            location_text="123 Main St, Anytown",
        )
        self.assertEqual(billing_type, "psychotherapy_house_call")
        self.assertEqual(source, "location_inferred")
        self.assertTrue(suggested)

    def test_explicit_house_call_with_location_no_suggestion(self):
        """Explicit house call with location should not suggest."""
        billing_type, source, suggested = derive_billing_session_type(
            service_mode="house_call",
            is_weekend=False,
            is_evening=False,
            location_text="123 Main St",
        )
        self.assertEqual(billing_type, "psychotherapy_house_call")
        self.assertEqual(source, "auto")
        self.assertFalse(suggested)

    def test_empty_location_does_not_trigger_house_call(self):
        """Empty or whitespace location should not trigger house call."""
        billing_type, source, suggested = derive_billing_session_type(
            service_mode="phone",
            is_weekend=False,
            is_evening=False,
            location_text="   ",
        )
        self.assertEqual(billing_type, "psychotherapy")


class AppointmentMethodTests(unittest.TestCase):
    """Test appointment method derivation."""

    def test_phone_maps_to_phone(self):
        self.assertEqual(derive_appointment_method("phone"), "phone")

    def test_facetime_maps_to_facetime(self):
        self.assertEqual(derive_appointment_method("facetime"), "facetime")

    def test_office_maps_to_office(self):
        self.assertEqual(derive_appointment_method("office"), "office")

    def test_house_call_maps_to_office(self):
        self.assertEqual(derive_appointment_method("house_call"), "office")

    def test_unknown_maps_to_unknown(self):
        self.assertEqual(derive_appointment_method("unknown"), "unknown")


class DurationChoiceTests(unittest.TestCase):
    """Test duration choice derivation."""

    def test_standard_durations(self):
        for minutes in [30, 60, 90, 120]:
            choice, custom = derive_duration_choice(minutes)
            self.assertEqual(choice, str(minutes))
            self.assertIsNone(custom)

    def test_custom_duration_45_minutes(self):
        choice, custom = derive_duration_choice(45)
        self.assertEqual(choice, "custom")
        self.assertEqual(custom, 45)

    def test_custom_duration_75_minutes(self):
        choice, custom = derive_duration_choice(75)
        self.assertEqual(choice, "custom")
        self.assertEqual(custom, 75)

    def test_none_duration_defaults_to_60(self):
        choice, custom = derive_duration_choice(None)
        self.assertEqual(choice, "60")
        self.assertIsNone(custom)


class LateEveningWarningTests(unittest.TestCase):
    """Test late evening (after 10 PM) warning."""

    def test_9pm_no_warning(self):
        self.assertFalse(check_late_evening("2026-06-18T21:00:00-04:00"))

    def test_10pm_warning(self):
        self.assertTrue(check_late_evening("2026-06-18T22:00:00-04:00"))

    def test_11pm_warning(self):
        self.assertTrue(check_late_evening("2026-06-18T23:30:00-04:00"))


class ParserBillingSessionTypeIntegrationTests(unittest.TestCase):
    """Test that parse_event correctly derives billing session types."""

    def test_weekday_daytime_session(self):
        result = parse_event({
            "event_title": "Bonnie Smith | 60 | Phone",
            "start_at": "2026-06-18T14:00:00-04:00",
            "end_at": "2026-06-18T15:00:00-04:00",
        })
        self.assertEqual(result.billing_session_type, "psychotherapy")
        self.assertEqual(result.appointment_method, "phone")
        self.assertEqual(result.duration_choice, "60")

    def test_weekday_evening_session(self):
        result = parse_event({
            "event_title": "Fred Jones | 60 | Office",
            "start_at": "2026-06-18T20:00:00-04:00",
            "end_at": "2026-06-18T21:00:00-04:00",
        })
        self.assertEqual(result.billing_session_type, "psychotherapy_evening")
        self.assertEqual(result.is_evening, True)

    def test_weekend_session(self):
        result = parse_event({
            "event_title": "Sarah Lee | 90 | FaceTime",
            "start_at": "2026-06-20T10:00:00-04:00",  # Saturday
            "end_at": "2026-06-20T11:30:00-04:00",
        })
        self.assertEqual(result.billing_session_type, "psychotherapy_weekend")
        self.assertEqual(result.is_weekend, True)

    def test_house_call_explicit(self):
        result = parse_event({
            "event_title": "Mike Brown | 60 | House Call",
            "start_at": "2026-06-18T14:00:00-04:00",
            "end_at": "2026-06-18T15:00:00-04:00",
        })
        self.assertEqual(result.billing_session_type, "psychotherapy_house_call")
        self.assertEqual(result.service_mode, "house_call")

    def test_house_call_from_location(self):
        result = parse_event({
            "event_title": "Jane Doe | 60 | Office",
            "start_at": "2026-06-18T14:00:00-04:00",
            "end_at": "2026-06-18T15:00:00-04:00",
            "location": "456 Oak Avenue",
        })
        self.assertEqual(result.billing_session_type, "psychotherapy_house_call")
        self.assertTrue(result.house_call_suggested)
        self.assertEqual(result.billing_type_source, "location_inferred")

    def test_custom_duration_45_minutes(self):
        result = parse_event({
            "event_title": "Alex Kim | 45 | Phone",
            "start_at": "2026-06-18T14:00:00-04:00",
            "end_at": "2026-06-18T14:45:00-04:00",
        })
        self.assertEqual(result.duration_choice, "custom")
        self.assertEqual(result.custom_duration_minutes, 45)
        self.assertIn("custom_duration", result.fields_requiring_review)

    def test_late_evening_warning(self):
        result = parse_event({
            "event_title": "Night Owl | 60 | Phone",
            "start_at": "2026-06-18T22:30:00-04:00",
            "end_at": "2026-06-18T23:30:00-04:00",
        })
        self.assertTrue(result.late_evening_warning)
        self.assertIn("late_evening_time", result.fields_requiring_review)


class BillingSessionTypeConstantsTests(unittest.TestCase):
    """Test that constants are properly defined."""

    def test_all_types_have_labels(self):
        for billing_type in BILLING_SESSION_TYPES:
            self.assertIn(billing_type, BILLING_SESSION_TYPE_LABELS)

    def test_standard_duration_choices(self):
        self.assertEqual(STANDARD_DURATION_CHOICES, {30, 60, 90, 120})


if __name__ == "__main__":
    unittest.main()
