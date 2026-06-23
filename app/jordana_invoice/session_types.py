"""
Centralized definitions for billing session types and duration choices.

This module enforces the non-negotiable product rule:
- Exactly 5 billing session types
- Exactly 5 duration choices
- No legacy options in active workflows
"""
from __future__ import annotations

from typing import Any


ALLOWED_BILLING_SESSION_TYPES = frozenset({
    "psychotherapy",
    "psychotherapy_house_call",
    "psychotherapy_weekend",
    "psychotherapy_evening",
    "custom",
})

BILLING_SESSION_TYPE_LABELS = {
    "psychotherapy": "Psychotherapy Session",
    "psychotherapy_house_call": "Psychotherapy Session / House Call",
    "psychotherapy_weekend": "Psychotherapy Session / Weekend",
    "psychotherapy_evening": "Psychotherapy Session / Evening",
    "custom": "Custom",
}

BILLING_SESSION_TYPE_OPTIONS = [
    {"value": "psychotherapy", "label": "Psychotherapy Session"},
    {"value": "psychotherapy_house_call", "label": "Psychotherapy Session / House Call"},
    {"value": "psychotherapy_weekend", "label": "Psychotherapy Session / Weekend"},
    {"value": "psychotherapy_evening", "label": "Psychotherapy Session / Evening"},
    {"value": "custom", "label": "Custom"},
]

ALLOWED_DURATION_CHOICES = frozenset({"30", "60", "90", "120", "custom"})

STANDARD_DURATION_MINUTES = frozenset({30, 60, 90, 120})

DURATION_CHOICE_OPTIONS = [
    {"value": "30", "label": "30 minutes"},
    {"value": "60", "label": "60 minutes"},
    {"value": "90", "label": "90 minutes"},
    {"value": "120", "label": "120 minutes"},
    {"value": "custom", "label": "Custom"},
]

LEGACY_SERVICE_MODES = frozenset({
    "office",
    "phone",
    "facetime",
    "correspondence",
    "preparation",
    "mediation",
    "other",
    "unknown",
})

APPOINTMENT_METHODS = frozenset({"office", "phone", "facetime", "unknown"})


def validate_billing_session_type(value: str | None) -> str:
    """
    Validate and return a billing session type.
    
    Raises ValueError if the value is not one of the 5 allowed types.
    """
    if value is None:
        raise ValueError("Billing session type is required")
    if value not in ALLOWED_BILLING_SESSION_TYPES:
        raise ValueError(
            f"Invalid billing session type: {value}. "
            f"Allowed values: {', '.join(sorted(ALLOWED_BILLING_SESSION_TYPES))}"
        )
    return value


def validate_duration_choice(
    choice: str | None,
    custom_minutes: int | None = None,
) -> tuple[str, int | None]:
    """
    Validate duration choice and custom minutes.
    
    Returns: (validated_choice, validated_custom_minutes)
    Raises ValueError for invalid values.
    """
    if choice is None:
        raise ValueError("Duration choice is required")
    if choice not in ALLOWED_DURATION_CHOICES:
        raise ValueError(
            f"Invalid duration choice: {choice}. "
            f"Allowed values: {', '.join(sorted(ALLOWED_DURATION_CHOICES))}"
        )
    if choice == "custom":
        if custom_minutes is None:
            raise ValueError("Custom duration requires actual minutes")
        if not isinstance(custom_minutes, int) or custom_minutes <= 0:
            raise ValueError("Custom duration must be a positive integer")
        return choice, custom_minutes
    return choice, None


def map_legacy_to_billing_type(
    service_mode: str | None,
    is_weekend: bool,
    is_evening: bool,
    location_text: str | None = None,
) -> tuple[str, str, bool]:
    """
    Map legacy service_mode to billing session type using priority rules.
    
    Priority:
    1. Custom (manual only)
    2. House Call (explicit or location-based)
    3. Weekend
    4. Evening (weekday >= 8 PM)
    5. Standard Psychotherapy Session
    
    Returns: (billing_session_type, billing_type_source, house_call_suggested)
    """
    house_call_explicit = service_mode == "house_call"
    house_call_from_location = bool(location_text and location_text.strip())
    
    if house_call_explicit:
        return "psychotherapy_house_call", "auto", False
    if house_call_from_location:
        return "psychotherapy_house_call", "location_inferred", True
    if is_weekend:
        return "psychotherapy_weekend", "auto", False
    if is_evening:
        return "psychotherapy_evening", "auto", False
    return "psychotherapy", "auto", False


def map_legacy_to_appointment_method(service_mode: str | None) -> str:
    """
    Map legacy service_mode to appointment method.
    Office/Phone/FaceTime are appointment methods, not billing types.
    """
    if service_mode in {"phone", "facetime", "office"}:
        return service_mode
    if service_mode == "house_call":
        return "office"
    return "unknown"


def duration_minutes_to_choice(minutes: int | None) -> tuple[str, int | None]:
    """
    Convert duration minutes to a duration choice.
    
    Returns: (duration_choice, custom_minutes)
    """
    if minutes is None:
        return "60", None
    if minutes in STANDARD_DURATION_MINUTES:
        return str(minutes), None
    return "custom", minutes


def duration_choice_to_minutes(choice: str, custom_minutes: int | None = None) -> int:
    """
    Convert duration choice to actual minutes.
    """
    if choice == "custom":
        if custom_minutes is None:
            raise ValueError("Custom duration requires actual minutes")
        return custom_minutes
    return int(choice)


def get_billing_type_label(billing_type: str | None) -> str:
    """Get the display label for a billing session type."""
    if billing_type is None:
        return "Unknown"
    return BILLING_SESSION_TYPE_LABELS.get(billing_type, billing_type)


def is_legacy_service_mode(value: str | None) -> bool:
    """Check if a value is a legacy service mode that should not be used as a billing type."""
    return value in LEGACY_SERVICE_MODES
