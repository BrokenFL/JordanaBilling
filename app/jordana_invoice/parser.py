from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .util import parse_int, text


CLASSIFICATIONS = {
    "client_session",
    "administrative",
    "personal",
    "cancelled",
    "no_show",
    "nonbillable",
    "duplicate",
    "unresolved",
}

REVIEW_STATUSES = {
    "needs_classification",
    "needs_person_match",
    "needs_account",
    "needs_participants",
    "needs_billing_party",
    "needs_duration",
    "needs_service_mode",
    "needs_rate",
    "needs_payment_status",
    "needs_billing_treatment",
    "ready_for_approval",
    "approved",
    "excluded",
}

KNOWN_DURATION_MINUTES = {15, 20, 30, 45, 50, 60, 75, 90, 120}
STANDARD_DURATION_CHOICES = {30, 60, 90, 120}

BILLING_SESSION_TYPES = {
    "psychotherapy",
    "psychotherapy_house_call",
    "psychotherapy_weekend",
    "psychotherapy_evening",
    "custom",
}

BILLING_SESSION_TYPE_LABELS = {
    "psychotherapy": "Psychotherapy Session",
    "psychotherapy_house_call": "Psychotherapy Session / House Call",
    "psychotherapy_weekend": "Psychotherapy Session / Weekend",
    "psychotherapy_evening": "Psychotherapy Session / Evening",
    "custom": "Custom",
}

APPOINTMENT_METHODS = {"office", "phone", "facetime", "unknown"}
PERSONAL_KEYWORDS = {
    "mani",
    "pedi",
    "manicure",
    "pedicure",
    "cp reformer",
    "haircut",
    "cleaners",
    "dry cleaner",
    "dinner",
    "breakfast",
    "lunch",
    "car wash",
    "bank",
    "plant",
    "father's day",
    "fathers day",
    "gyno",
    "taxes",
    "trip",
    "trips",
}
ADMIN_PREFIXES = (
    "ask",
    "call",
    "contact",
    "follow up",
    "email",
    "invoice",
    "check",
    "cancel",
    "have i heard",
)
ADMIN_KEYWORDS = {
    "follow up",
    "email",
    "call ",
    "letter",
    "board meeting",
    "review to do",
    "open house",
    "showing",
}
CANCELLED_KEYWORDS = {"cancel", "cancelled", "canceled", "reschedule", "rescheduled"}
LATE_CANCELLATION_KEYWORDS = {"late cx", "late cancel", "late cancellation"}
NO_SHOW_KEYWORDS = {"no show", "noshow", "no-show", "did not attend"}
NONBILLABLE_KEYWORDS = {"nonbillable", "non-billable", "free consult", "courtesy"}
MULTI_PERSON_MARKERS = (" and ", " + ", " with ", " for ", "&")
STATUS_ALIASES = {
    "cancel": "cancelled",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "late cx": "late_cancellation",
    "late cancel": "late_cancellation",
    "late cancellation": "late_cancellation",
    "no show": "no_show",
    "no-show": "no_show",
    "noshow": "no_show",
}

SERVICE_MODE_ALIASES = {
    "phone": "phone",
    "call": "phone",
    "facetime": "facetime",
    "face time": "facetime",
    "ft": "facetime",
    "office": "office",
    "in person": "office",
    "in-person": "office",
    "house": "house_call",
    "home": "house_call",
    "house call": "house_call",
    "home visit": "house_call",
}

RATE_GROUP_BY_SERVICE_MODE = {
    "phone": "remote",
    "facetime": "remote",
    "office": "office",
    "house_call": "house_call",
    "unknown": "",
}

NAME_FIXES = {
    "rebecca colon": "Rebecca Colon",
    "jenny g": "Jenny G",
}


@dataclass
class ParseResult:
    classification: str
    confidence: float
    explanation: str
    fields_requiring_review: list[str] = field(default_factory=list)
    proposed_client_name: str | None = None
    proposed_start_at: str | None = None
    proposed_duration_minutes: int | None = None
    proposed_end_at: str | None = None
    time_shorthand: str | None = None
    duration_source: str | None = None
    explicit_duration_minutes: int | None = None
    calendar_duration_minutes: int | None = None
    title_time_matches_calendar: bool | None = None
    title_time_text: str | None = None
    title_time_normalized: str | None = None
    appointment_status: str = "unresolved"
    confidence_label: str = "low"
    unresolved_fields: list[str] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)
    candidate_person_names: list[str] = field(default_factory=list)
    possible_referenced_person: str | None = None
    service_mode: str = "unknown"
    rate_group: str | None = None
    time_category: str = "standard"
    is_evening: bool = False
    is_weekend: bool = False
    standardized_title_format: bool = False
    relationship_review_required: bool = False
    billing_session_type: str = "psychotherapy"
    appointment_method: str = "unknown"
    duration_choice: str = "60"
    custom_duration_minutes: int | None = None
    house_call_suggested: bool = False
    billing_type_source: str = "auto"
    location_text: str | None = None
    late_evening_warning: bool = False
    unresolved_trailing_text: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "classification": self.classification,
            "confidence": self.confidence,
            "confidence_label": self.confidence_label,
            "explanation": self.explanation,
            "fields_requiring_review": self.fields_requiring_review,
            "unresolved_fields": self.unresolved_fields,
            "review_reasons": self.review_reasons,
            "proposed_client_name": self.proposed_client_name,
            "candidate_person_names": self.candidate_person_names,
            "possible_referenced_person": self.possible_referenced_person,
            "proposed_start_at": self.proposed_start_at,
            "proposed_duration_minutes": self.proposed_duration_minutes,
            "proposed_end_at": self.proposed_end_at,
            "time_shorthand": self.time_shorthand,
            "duration_source": self.duration_source,
            "explicit_duration_minutes": self.explicit_duration_minutes,
            "calendar_duration_minutes": self.calendar_duration_minutes,
            "title_time_matches_calendar": self.title_time_matches_calendar,
            "title_time_text": self.title_time_text,
            "title_time_normalized": self.title_time_normalized,
            "appointment_status": self.appointment_status,
            "service_mode": self.service_mode,
            "rate_group": self.rate_group,
            "time_category": self.time_category,
            "is_evening": self.is_evening,
            "is_weekend": self.is_weekend,
            "standardized_title_format": self.standardized_title_format,
            "relationship_review_required": self.relationship_review_required,
            "billing_session_type": self.billing_session_type,
            "appointment_method": self.appointment_method,
            "duration_choice": self.duration_choice,
            "custom_duration_minutes": self.custom_duration_minutes,
            "house_call_suggested": self.house_call_suggested,
            "billing_type_source": self.billing_type_source,
            "location_text": self.location_text,
            "late_evening_warning": self.late_evening_warning,
            "unresolved_trailing_text": self.unresolved_trailing_text,
        }


def parse_event(row: dict[str, object]) -> ParseResult:
    title = normalize_title(text(row.get("event_title") or row.get("title")))
    lower = title.lower()
    start_at = text(row.get("start_at"))
    end_at = text(row.get("end_at"))
    calendar_duration = parse_int(row.get("duration_minutes"))
    computed_duration = calendar_duration or compute_duration_minutes(start_at, end_at)
    time_info = derive_time_category(start_at)
    occurrence_status = infer_appointment_status(start_at, end_at)
    title_metadata = derive_title_metadata(title, row, start_at, computed_duration, time_info)

    if not title:
        return finalize_result(
            ParseResult(
                classification="unresolved",
                confidence=0.1,
                explanation="Missing event title.",
                fields_requiring_review=["event_title"],
                proposed_start_at=start_at or None,
                calendar_duration_minutes=computed_duration,
                appointment_status=occurrence_status,
                **time_info,
            )
        )

    if "?" in title:
        return finalize_result(
            apply_title_metadata(
                ParseResult(
                classification="unresolved",
                confidence=0.25,
                explanation="Question mark in title signals uncertainty.",
                fields_requiring_review=["event_title", "client"],
                proposed_start_at=start_at or None,
                calendar_duration_minutes=computed_duration,
                proposed_duration_minutes=computed_duration or 60,
                duration_source="calendar" if computed_duration else "default",
                appointment_status=occurrence_status,
                **time_info,
                ),
                title_metadata,
            )
        )

    parse_title, for_reference = strip_for_reference(title)
    parsed_title = parse_standard_title(parse_title) or parse_shorthand(parse_title)
    if parsed_title and (
        parsed_title[4]
        or not (
            starts_with_admin(lower)
            or contains_any(lower, ADMIN_KEYWORDS)
            or contains_any(lower, PERSONAL_KEYWORDS)
            or contains_any(lower, LATE_CANCELLATION_KEYWORDS)
            or contains_any(lower, CANCELLED_KEYWORDS)
            or contains_any(lower, NO_SHOW_KEYWORDS)
            or contains_any(lower, NONBILLABLE_KEYWORDS)
        )
    ):
        client_name, time_token, explicit_duration, service_mode, standardized, explicit_status, unknown_status = parsed_title
        proposed_duration, duration_source = choose_duration(
            explicit_duration,
            computed_duration,
        )
        proposed_end = add_minutes(start_at, proposed_duration)
        title_time_matches = compare_title_time(start_at, time_token)
        title_time = parse_title_time(time_token) if time_token else None
        people = split_candidate_people(client_name)
        fields = [
            "client_full_name",
            "client_account",
            "billing_party",
            "client_rate",
        ]
        explanation_parts = ["Recognized client session title pattern."]

        appointment_status = explicit_status or occurrence_status
        relationship_review = has_multiple_person_markers(client_name) or bool(for_reference)
        if relationship_review:
            fields.extend(["participants", "relationship_role"])
            if has_multiple_person_markers(client_name):
                explanation_parts.append(
                    "Title appears to reference multiple people or a billing relationship."
                )
            if for_reference:
                fields.append("classification")
                explanation_parts.append(
                    f"Title references \"{for_reference}\" after 'for'; relationship role unresolved."
                )

        if service_mode == "unknown":
            fields.append("service_mode")
        if unknown_status:
            fields.append("appointment_status")
            appointment_status = "unresolved"
            explanation_parts.append("Final structured title value is not a recognized appointment status.")
        if explicit_status in {"cancelled", "no_show"}:
            fields.append("billing_treatment")
            explanation_parts.append("Appointment status needs a separate billing decision.")
        if explicit_duration:
            explanation_parts.append(
                "Explicit title duration overrides calendar duration."
            )
        if explicit_duration and computed_duration and explicit_duration != computed_duration:
            fields.append("duration_discrepancy")
            explanation_parts.append(
                "Title duration differs from the Calendar event duration."
            )
        if title_time_matches is False:
            fields.append("time_discrepancy")
            explanation_parts.append(
                "Title time does not match calendar start time."
            )

        confidence = 0.86 if standardized else 0.76
        if explicit_duration:
            confidence += 0.04
        if relationship_review:
            confidence -= 0.18
        if title_time_matches is False:
            confidence -= 0.25
        if unknown_status:
            confidence -= 0.2

        location_text = text(row.get("location"))
        billing_type, billing_source, house_call_suggested = derive_billing_session_type(
            service_mode=service_mode,
            is_weekend=time_info["is_weekend"],
            is_evening=time_info["is_evening"],
            house_call_explicit=(service_mode == "house_call"),
            location_text=location_text,
        )
        appointment_method = derive_appointment_method(service_mode)
        duration_choice, custom_duration = derive_duration_choice(proposed_duration)
        late_evening = check_late_evening(start_at)

        if house_call_suggested:
            fields.append("house_call_confirmation")
            explanation_parts.append("Location suggests House Call; confirm billing type.")
        if late_evening:
            fields.append("late_evening_time")
            explanation_parts.append("Session starts after 10 PM; verify time is correct.")
        if duration_choice == "custom":
            fields.append("custom_duration")
            explanation_parts.append(f"Duration {proposed_duration} min is non-standard; confirm or adjust.")

        return finalize_result(
            ParseResult(
                classification="unresolved" if for_reference else "client_session",
                confidence=max(0.2, min(confidence, 0.94)),
                explanation=" ".join(explanation_parts),
                fields_requiring_review=sorted(set(fields)),
                proposed_client_name=client_name,
                candidate_person_names=people,
                proposed_start_at=start_at or None,
                proposed_duration_minutes=proposed_duration,
                proposed_end_at=proposed_end,
                time_shorthand=time_token,
                duration_source=duration_source,
                explicit_duration_minutes=explicit_duration,
                calendar_duration_minutes=computed_duration,
                title_time_matches_calendar=title_time_matches,
                title_time_text=time_token,
                title_time_normalized=format_title_time(title_time),
                appointment_status=appointment_status,
                service_mode=service_mode,
                rate_group=RATE_GROUP_BY_SERVICE_MODE.get(service_mode) or None,
                standardized_title_format=standardized,
                relationship_review_required=relationship_review,
                possible_referenced_person=for_reference,
                billing_session_type=billing_type,
                appointment_method=appointment_method,
                duration_choice=duration_choice,
                custom_duration_minutes=custom_duration,
                house_call_suggested=house_call_suggested,
                billing_type_source=billing_source,
                location_text=location_text or None,
                late_evening_warning=late_evening,
                **time_info,
            )
        )

    ss_title, ss_status = strip_title_appointment_status(title)
    if ss_status and ss_title:
        ss_lower = ss_title.lower()
        if not (
            starts_with_admin(ss_lower)
            or contains_any(ss_lower, ADMIN_KEYWORDS)
            or contains_any(ss_lower, PERSONAL_KEYWORDS)
            or contains_any(ss_lower, NONBILLABLE_KEYWORDS)
        ):
            ss_parse_title, ss_for_reference = strip_for_reference(ss_title)
            ss_parsed = parse_standard_title(ss_parse_title) or parse_shorthand(ss_parse_title)
            if ss_parsed:
                (
                    client_name,
                    time_token,
                    explicit_duration,
                    service_mode,
                    standardized,
                    _,
                    _,
                ) = ss_parsed
                proposed_duration, duration_source = choose_duration(explicit_duration, computed_duration)
                proposed_end = add_minutes(start_at, proposed_duration)
                title_time_matches = compare_title_time(start_at, time_token)
                title_time = parse_title_time(time_token) if time_token else None
                people = split_candidate_people(client_name)
                fields = [
                    "client_full_name",
                    "client_account",
                    "billing_party",
                    "client_rate",
                    "billing_treatment",
                ]
                explanation_parts = [
                    "Recognized client session title with appointment status suffix.",
                    "Appointment status needs a separate billing decision.",
                ]
                relationship_review = has_multiple_person_markers(client_name) or bool(ss_for_reference)
                if relationship_review:
                    fields.extend(["participants", "relationship_role"])
                    if has_multiple_person_markers(client_name):
                        explanation_parts.append(
                            "Title appears to reference multiple people or a billing relationship."
                        )
                    if ss_for_reference:
                        fields.append("classification")
                        explanation_parts.append(
                            f"Title references \"{ss_for_reference}\" after 'for'; relationship role unresolved."
                        )
                if service_mode == "unknown":
                    fields.append("service_mode")
                if explicit_duration and computed_duration and explicit_duration != computed_duration:
                    fields.append("duration_discrepancy")
                    explanation_parts.append("Title duration differs from the Calendar event duration.")
                if title_time_matches is False:
                    fields.append("time_discrepancy")
                    explanation_parts.append("Title time does not match calendar start time.")
                confidence = 0.82 if standardized else 0.72
                if explicit_duration:
                    confidence += 0.04
                if relationship_review:
                    confidence -= 0.18
                if title_time_matches is False:
                    confidence -= 0.25
                location_text = text(row.get("location"))
                billing_type, billing_source, house_call_suggested = derive_billing_session_type(
                    service_mode=service_mode,
                    is_weekend=time_info["is_weekend"],
                    is_evening=time_info["is_evening"],
                    house_call_explicit=(service_mode == "house_call"),
                    location_text=location_text,
                )
                appointment_method = derive_appointment_method(service_mode)
                duration_choice, custom_duration = derive_duration_choice(proposed_duration)
                late_evening = check_late_evening(start_at)
                if house_call_suggested:
                    fields.append("house_call_confirmation")
                    explanation_parts.append("Location suggests House Call; confirm billing type.")
                if late_evening:
                    fields.append("late_evening_time")
                    explanation_parts.append("Session starts after 10 PM; verify time is correct.")
                if duration_choice == "custom":
                    fields.append("custom_duration")
                    explanation_parts.append(f"Duration {proposed_duration} min is non-standard; confirm or adjust.")
                return finalize_result(
                    ParseResult(
                        classification="unresolved" if ss_for_reference else "client_session",
                        confidence=max(0.2, min(confidence, 0.94)),
                        explanation=" ".join(explanation_parts),
                        fields_requiring_review=sorted(set(fields)),
                        proposed_client_name=client_name,
                        candidate_person_names=people,
                        proposed_start_at=start_at or None,
                        proposed_duration_minutes=proposed_duration,
                        proposed_end_at=proposed_end,
                        time_shorthand=time_token,
                        duration_source=duration_source,
                        explicit_duration_minutes=explicit_duration,
                        calendar_duration_minutes=computed_duration,
                        title_time_matches_calendar=title_time_matches,
                        title_time_text=time_token,
                        title_time_normalized=format_title_time(title_time),
                        appointment_status=ss_status,
                        service_mode=service_mode,
                        rate_group=RATE_GROUP_BY_SERVICE_MODE.get(service_mode) or None,
                        standardized_title_format=standardized,
                        relationship_review_required=relationship_review,
                        possible_referenced_person=ss_for_reference,
                        billing_session_type=billing_type,
                        appointment_method=appointment_method,
                        duration_choice=duration_choice,
                        custom_duration_minutes=custom_duration,
                        house_call_suggested=house_call_suggested,
                        billing_type_source=billing_source,
                        location_text=location_text or None,
                        late_evening_warning=late_evening,
                        **time_info,
                    )
                )

    if starts_with_admin(lower) or contains_any(lower, ADMIN_KEYWORDS):
        return finalize_result(
            apply_title_metadata(
                ParseResult(
                classification="administrative",
                confidence=0.7 if starts_with_admin(lower) else 0.58,
                explanation="Title matches likely administrative work language.",
                fields_requiring_review=["classification"],
                proposed_start_at=start_at or None,
                proposed_duration_minutes=computed_duration or 60,
                duration_source="calendar" if computed_duration else "default",
                calendar_duration_minutes=computed_duration,
                possible_referenced_person=extract_admin_person(title),
                appointment_status=occurrence_status,
                **time_info,
                ),
                title_metadata,
            )
        )

    if contains_any(lower, PERSONAL_KEYWORDS):
        return finalize_result(
            apply_title_metadata(
                ParseResult(
                classification="personal",
                confidence=0.65,
                explanation="Title matches likely personal exclusion language.",
                fields_requiring_review=["exclusion_alias"],
                proposed_start_at=start_at or None,
                proposed_duration_minutes=computed_duration or 60,
                duration_source="calendar" if computed_duration else "default",
                calendar_duration_minutes=computed_duration,
                appointment_status=occurrence_status,
                **time_info,
                ),
                title_metadata,
            )
        )

    if contains_any(lower, LATE_CANCELLATION_KEYWORDS):
        return finalize_result(status_result("late_cancellation", start_at, computed_duration, time_info, title_metadata))

    if contains_any(lower, CANCELLED_KEYWORDS):
        return finalize_result(status_result("cancelled", start_at, computed_duration, time_info, title_metadata))

    if contains_any(lower, NO_SHOW_KEYWORDS):
        return finalize_result(status_result("no_show", start_at, computed_duration, time_info, title_metadata))

    if contains_any(lower, NONBILLABLE_KEYWORDS):
        return finalize_result(status_result("nonbillable", start_at, computed_duration, time_info, title_metadata))

    name_guess, trailing_text = extract_name_guess(title)
    if name_guess:
        explanation_parts = ["No recognized shorthand, exclusion, or status pattern."]
        explanation_parts.append(f"Participant guess: {name_guess}.")
        if trailing_text:
            explanation_parts.append(f"Unresolved trailing text: \"{trailing_text}\".")
        if for_reference:
            explanation_parts.append(f"Title references \"{for_reference}\" after 'for'.")
        return finalize_result(
            apply_title_metadata(
                ParseResult(
                classification="unresolved",
                confidence=0.3,
                explanation=" ".join(explanation_parts),
                fields_requiring_review=["classification", "client"],
                proposed_client_name=name_guess,
                candidate_person_names=[name_guess],
                proposed_start_at=start_at or None,
                proposed_duration_minutes=computed_duration or 60,
                duration_source="calendar" if computed_duration else "default",
                calendar_duration_minutes=computed_duration,
                appointment_status=occurrence_status,
                possible_referenced_person=for_reference,
                unresolved_trailing_text=trailing_text,
                **time_info,
                ),
                title_metadata,
            )
        )

    return finalize_result(
        apply_title_metadata(
            ParseResult(
            classification="unresolved",
            confidence=0.2,
            explanation="No recognized shorthand, exclusion, or status pattern.",
            fields_requiring_review=["classification", "client"],
            proposed_start_at=start_at or None,
            proposed_duration_minutes=computed_duration or 60,
            duration_source="calendar" if computed_duration else "default",
            calendar_duration_minutes=computed_duration,
            appointment_status=occurrence_status,
            possible_referenced_person=for_reference,
            **time_info,
            ),
            title_metadata,
        )
    )


def strip_for_reference(title: str) -> tuple[str, str | None]:
    """
    Strip a trailing 'for <reference>' from a calendar event title.
    Returns (stripped_title, reference_text) or (title, None) if no match.
    """
    match = re.search(r'\s+for\s+(.+)$', title, re.IGNORECASE)
    if match:
        reference = match.group(1).strip()
        stripped = normalize_title(title[:match.start()])
        if stripped and reference and re.search(r'[A-Za-z]', reference):
            return stripped, reference
    return title, None


def strip_title_appointment_status(title: str) -> tuple[str, str | None]:
    """
    Strip a trailing appointment status term from a calendar event title.
    Returns (stripped_title, appointment_status) or (title, None) if no match.
    """
    _no_show_re = re.compile(
        r"(?i)\s+(?:did\s+not\s+attend|no[\s\-]?show|noshow)\s*$"
    )
    _late_cancel_re = re.compile(
        r"(?i)\s+(?:late\s+cx|late\s+cancel(?:lation|led|ed)?)\s*$"
    )
    _cancelled_re = re.compile(
        r"(?i)\s+cancel(?:l?ed)?\s*$"
    )
    m = _late_cancel_re.search(title)
    if m:
        cleaned = title[: m.start()].strip()
        if cleaned:
            return cleaned, "late_cancellation"
    m = _no_show_re.search(title)
    if m:
        cleaned = title[: m.start()].strip()
        if cleaned:
            return cleaned, "no_show"
    m = _cancelled_re.search(title)
    if m:
        cleaned = title[: m.start()].strip()
        if cleaned:
            return cleaned, "cancelled"
    return title, None


def status_result(
    classification: str,
    start_at: str,
    computed_duration: int | None,
    time_info: dict[str, object],
    title_metadata: dict[str, object] | None = None,
) -> ParseResult:
    explanations = {
        "cancelled": "Title contains cancellation or reschedule language.",
        "late_cancellation": "Title contains late-cancellation language.",
        "no_show": "Title contains no-show language.",
        "nonbillable": "Title contains non-billable language.",
    }
    fields = {
        "cancelled": ["billing_disposition"],
        "late_cancellation": ["client_full_name", "client_account", "billing_party", "client_rate", "billing_treatment"],
        "no_show": ["billing_disposition"],
        "nonbillable": ["nonbillable_reason"],
    }
    return apply_title_metadata(ParseResult(
        classification=classification,
        confidence=0.74,
        explanation=explanations[classification],
        fields_requiring_review=fields[classification],
        proposed_start_at=start_at or None,
        calendar_duration_minutes=computed_duration,
        proposed_duration_minutes=computed_duration or 60,
        duration_source="calendar" if computed_duration else "default",
        appointment_status=(
            "late_cancellation"
            if classification == "late_cancellation"
            else "cancelled"
            if classification == "cancelled"
            else "no_show"
            if classification == "no_show"
            else "unresolved"
        ),
        **time_info,
    ), title_metadata)


def derive_title_metadata(
    title: str,
    row: dict[str, object],
    start_at: str,
    computed_duration: int | None,
    time_info: dict[str, object],
) -> dict[str, object]:
    parse_title, _ = strip_for_reference(title)
    status_title, _ = strip_title_appointment_status(parse_title)
    parsed = (
        parse_standard_title(status_title)
        or parse_shorthand(status_title)
        or parse_standard_title(parse_title)
        or parse_shorthand(parse_title)
    )
    explicit_duration = None
    service_mode = "unknown"
    if parsed:
        explicit_duration = parsed[2]
        service_mode = parsed[3]
    else:
        cleaned_title, service_mode = strip_service_mode(parse_title)
        explicit_duration = extract_title_duration(cleaned_title)

    proposed_duration, duration_source = choose_duration(explicit_duration, computed_duration)
    location_text = text(row.get("location"))
    billing_type, billing_source, house_call_suggested = derive_billing_session_type(
        service_mode=service_mode,
        is_weekend=bool(time_info["is_weekend"]),
        is_evening=bool(time_info["is_evening"]),
        house_call_explicit=(service_mode == "house_call"),
        location_text=location_text,
    )
    duration_choice, custom_duration = derive_duration_choice(proposed_duration)
    return {
        "proposed_duration_minutes": proposed_duration,
        "proposed_end_at": add_minutes(start_at, proposed_duration),
        "duration_source": duration_source,
        "explicit_duration_minutes": explicit_duration,
        "calendar_duration_minutes": computed_duration,
        "service_mode": service_mode,
        "rate_group": RATE_GROUP_BY_SERVICE_MODE.get(service_mode) or None,
        "billing_session_type": billing_type,
        "appointment_method": derive_appointment_method(service_mode),
        "duration_choice": duration_choice,
        "custom_duration_minutes": custom_duration,
        "house_call_suggested": house_call_suggested,
        "billing_type_source": billing_source,
        "location_text": location_text or None,
        "late_evening_warning": check_late_evening(start_at),
    }


def apply_title_metadata(result: ParseResult, metadata: dict[str, object] | None) -> ParseResult:
    if not metadata:
        return result
    result.proposed_duration_minutes = int(metadata["proposed_duration_minutes"] or 60)
    result.proposed_end_at = metadata.get("proposed_end_at") or result.proposed_end_at
    result.duration_source = str(metadata.get("duration_source") or result.duration_source)
    result.explicit_duration_minutes = metadata.get("explicit_duration_minutes") or result.explicit_duration_minutes
    result.calendar_duration_minutes = metadata.get("calendar_duration_minutes") or result.calendar_duration_minutes
    result.service_mode = str(metadata.get("service_mode") or result.service_mode)
    result.rate_group = metadata.get("rate_group") or result.rate_group
    result.billing_session_type = str(metadata.get("billing_session_type") or result.billing_session_type)
    result.appointment_method = str(metadata.get("appointment_method") or result.appointment_method)
    result.duration_choice = str(metadata.get("duration_choice") or result.duration_choice)
    result.custom_duration_minutes = metadata.get("custom_duration_minutes") or result.custom_duration_minutes
    result.house_call_suggested = bool(metadata.get("house_call_suggested"))
    result.billing_type_source = str(metadata.get("billing_type_source") or result.billing_type_source)
    result.location_text = metadata.get("location_text") or result.location_text
    result.late_evening_warning = bool(metadata.get("late_evening_warning"))

    if result.house_call_suggested:
        result.fields_requiring_review.append("house_call_confirmation")
    if result.late_evening_warning:
        result.fields_requiring_review.append("late_evening_time")
    if result.duration_choice == "custom":
        result.fields_requiring_review.append("custom_duration")
    return result


def parse_standard_title(title: str) -> tuple[str, str | None, int | None, str, bool, str | None, str | None] | None:
    if "|" not in title:
        return None
    parts = [part.strip() for part in title.split("|")]
    if len(parts) not in {3, 4, 5}:
        return None
    explicit_status = None
    unknown_status = None
    if len(parts) == 3:
        name_part, duration_part, service_part = parts
        time_part = None
    elif len(parts) == 4:
        if parse_title_time(parts[1]) is not None:
            name_part, time_part, duration_part, service_part = parts
        else:
            explicit_status = normalize_status(parts[-1])
            unknown_status = None if explicit_status else parts[-1]
            name_part, duration_part, service_part = parts[:3]
            time_part = None
    elif len(parts) == 5:
        explicit_status = normalize_status(parts[-1])
        unknown_status = None if explicit_status else parts[-1]
        name_part, time_part, duration_part, service_part = parts[:4]

    name = canonicalize_name(name_part)
    explicit_duration = parse_int(duration_part)
    if explicit_duration not in KNOWN_DURATION_MINUTES:
        return None
    service_mode = normalize_service_mode(service_part)
    if not name or (time_part and parse_title_time(time_part) is None):
        return None
    return name, time_part, explicit_duration, service_mode, True, explicit_status, unknown_status


def parse_shorthand(title: str) -> tuple[str, str, int | None, str, bool, str | None, str | None] | None:
    cleaned_title, service_mode = strip_service_mode(title)
    tokens = cleaned_title.strip().split()
    if (
        len(tokens) >= 3
        and tokens[-1].lower() in {"min", "mins", "minute", "minutes"}
        and is_duration_token(tokens[-2])
    ):
        tokens = tokens[:-1]
    if len(tokens) < 2:
        return None

    last = tokens[-1]
    penultimate = tokens[-2] if len(tokens) >= 3 else ""
    antepenultimate = tokens[-3] if len(tokens) >= 4 else ""
    explicit_duration = None
    time_token = None
    name_tokens: list[str]

    if is_duration_token(last) and is_time_token(penultimate):
        explicit_duration = int(last)
        time_token = penultimate
        name_tokens = tokens[:-2]
    elif is_duration_token(last) and penultimate.lower() in {"am", "pm"} and is_time_token(antepenultimate):
        explicit_duration = None
        time_token = f"{antepenultimate} {penultimate.upper()}"
        name_tokens = tokens[:-2]
    elif penultimate.lower() in {"am", "pm"} and is_time_token(last):
        time_token = f"{last} {penultimate.upper()}"
        name_tokens = tokens[:-2]
    elif last.lower() in {"am", "pm"} and is_time_token(penultimate):
        time_token = f"{penultimate} {last.upper()}"
        name_tokens = tokens[:-2]
    elif is_time_token(last):
        time_token = last
        name_tokens = tokens[:-1]
    else:
        return None

    if not name_tokens:
        return None

    raw_name = " ".join(name_tokens).strip(" -:")
    if not re.search(r"[A-Za-z]", raw_name):
        return None

    client_name = canonicalize_name(raw_name)
    return client_name, time_token, explicit_duration, service_mode, False, None, None


def strip_service_mode(title: str) -> tuple[str, str]:
    for alias in sorted(SERVICE_MODE_ALIASES, key=len, reverse=True):
        pattern = re.compile(rf"(\b|\|){re.escape(alias)}\b", re.IGNORECASE)
        if pattern.search(title):
            stripped = pattern.sub(" ", title)
            return normalize_title(stripped), SERVICE_MODE_ALIASES[alias]
    return title, "unknown"


def extract_title_duration(title: str) -> int | None:
    for token in re.split(r"[\s|/\\,;:-]+", title):
        cleaned = token.strip("()[]{}")
        if is_duration_token(cleaned):
            return int(cleaned)
    return None


def normalize_service_mode(value: str) -> str:
    normalized = normalize_title(value).lower()
    return SERVICE_MODE_ALIASES.get(normalized, "unknown")


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip()


def canonicalize_name(name: str) -> str:
    lowered = normalize_title(name).lower().strip()
    if lowered in NAME_FIXES:
        return NAME_FIXES[lowered]
    chunks = re.split(r"(\s+|\+|&|\band\b|\bwith\b|\bfor\b)", lowered)
    fixed: list[str] = []
    for chunk in chunks:
        if chunk in {" + ", "+", "&"} or chunk.strip() in {"and", "with", "for"}:
            fixed.append(chunk.strip() if chunk.strip() != "+" else "+")
        elif chunk.isspace() or not chunk:
            fixed.append(chunk)
        else:
            fixed.append(" ".join(part.capitalize() for part in chunk.split()))
    return normalize_title(" ".join(part for part in fixed if part != ""))


def split_candidate_people(name: str) -> list[str]:
    raw_parts = re.split(r"\s+(?:and|with|for)\s+|\s*\+\s*|\s*&\s*", name, flags=re.I)
    return [canonicalize_name(part) for part in raw_parts if text(part)]


def has_multiple_person_markers(name: str) -> bool:
    lower = f" {name.lower()} "
    return any(marker in lower for marker in MULTI_PERSON_MARKERS)


def is_duration_token(token: str) -> bool:
    if not token.isdigit():
        return False
    return int(token) in KNOWN_DURATION_MINUTES


def is_time_token(token: str) -> bool:
    return parse_title_time(token) is not None


def choose_duration(
    explicit_duration: int | None,
    calendar_duration: int | None,
) -> tuple[int, str]:
    if explicit_duration:
        return explicit_duration, "title"
    if calendar_duration:
        return calendar_duration, "calendar"
    return 60, "default"


def compute_duration_minutes(start_at: str, end_at: str) -> int | None:
    start = parse_datetime(start_at)
    end = parse_datetime(end_at)
    if not start or not end:
        return None
    minutes = round((end - start).total_seconds() / 60)
    return minutes if minutes > 0 else None


def add_minutes(start_at: str, minutes: int | None) -> str | None:
    start = parse_datetime(start_at)
    if not start or not minutes:
        return None
    return start_plus(start, minutes)


def start_plus(start: datetime, minutes: int) -> str:
    return (start + timedelta(minutes=minutes)).isoformat()


def compare_title_time(start_at: str, token: str | None) -> bool | None:
    start = parse_datetime(start_at)
    if not start or not token:
        return None
    parsed = parse_title_time(token)
    if not parsed:
        return None
    return (start.hour, start.minute) == parsed


def shorthand_hour_minute(token: str) -> tuple[int, int, str | None]:
    parsed = parse_title_time(token)
    if not parsed:
        raise ValueError(f"Invalid title time token: {token}")
    clean = token.upper().strip().replace(".", "")
    meridiem = clean[-2:] if clean.endswith(("AM", "PM")) else None
    return parsed[0], parsed[1], meridiem


def parse_title_time(token: str | None) -> tuple[int, int] | None:
    clean = text(token).lower().replace(".", "").strip()
    if not clean:
        return None
    suffix = None
    if clean.endswith("am"):
        suffix = "am"
        clean = clean[:-2].strip()
    elif clean.endswith("pm"):
        suffix = "pm"
        clean = clean[:-2].strip()
    try:
        if ":" in clean:
            hour_text, minute_text = clean.split(":", 1)
            hour = int(hour_text)
            minute = int(minute_text)
        else:
            numeric = int(clean)
            if numeric >= 100:
                hour = numeric // 100
                minute = numeric % 100
            else:
                hour = numeric
                minute = 0
    except ValueError:
        return None
    if not (0 <= minute <= 59):
        return None
    if suffix == "pm" and 1 <= hour < 12:
        hour += 12
    elif suffix == "am" and hour == 12:
        hour = 0
    elif suffix is None and 1 <= hour <= 7:
        hour += 12
    if not (0 <= hour <= 23):
        return None
    return hour, minute


def format_title_time(parsed: tuple[int, int] | None) -> str | None:
    if not parsed:
        return None
    return f"{parsed[0]:02d}:{parsed[1]:02d}"


def normalize_status(value: str) -> str | None:
    normalized = re.sub(r"\s+", " ", text(value).lower().replace("-", " ").strip())
    return STATUS_ALIASES.get(normalized)


def infer_appointment_status(start_at: str, end_at: str) -> str:
    reference = end_at or start_at
    parsed = parse_datetime(reference)
    if not parsed:
        return "unresolved"
    now = datetime.now(parsed.tzinfo)
    return "completed" if parsed <= now else "scheduled"


def derive_time_category(start_at: str) -> dict[str, object]:
    start = parse_datetime(start_at)
    if not start:
        return {"is_evening": False, "is_weekend": False, "time_category": "standard"}
    is_evening = start.hour >= 20
    is_weekend = start.weekday() in {5, 6}
    if is_weekend:
        category = "weekend"
    elif is_evening:
        category = "evening"
    else:
        category = "standard"
    return {"is_evening": is_evening, "is_weekend": is_weekend, "time_category": category}


def parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ZoneInfo("America/New_York"))
    return parsed.astimezone(ZoneInfo("America/New_York"))


def starts_with_admin(lower_title: str) -> bool:
    return any(lower_title.startswith(prefix) for prefix in ADMIN_PREFIXES)


def extract_admin_person(title: str) -> str | None:
    match = re.search(r"\b(?:ask|call|contact|email)\s+(.+?)(?:\s+for\b|$)", title, re.I)
    if not match:
        return None
    candidate = canonicalize_name(match.group(1))
    return candidate or None


def derive_billing_session_type(
    service_mode: str,
    is_weekend: bool,
    is_evening: bool,
    house_call_explicit: bool = False,
    location_text: str | None = None,
) -> tuple[str, str, bool]:
    """
    Derive billing session type using priority order:
    1. Custom (manual only, not auto-derived)
    2. House Call (explicit text OR nonblank location)
    3. Weekend (Saturday/Sunday)
    4. Evening (weekday >= 8 PM)
    5. Standard Psychotherapy Session

    Returns: (billing_session_type, billing_type_source, house_call_suggested)
    """
    house_call_from_mode = service_mode == "house_call"
    house_call_from_location = bool(location_text and location_text.strip())
    house_call_suggested = house_call_from_location and not house_call_from_mode

    if house_call_explicit or house_call_from_mode:
        return "psychotherapy_house_call", "auto", False
    if house_call_from_location:
        return "psychotherapy_house_call", "location_inferred", True
    if is_weekend:
        return "psychotherapy_weekend", "auto", False
    if is_evening:
        return "psychotherapy_evening", "auto", False
    return "psychotherapy", "auto", False


def derive_appointment_method(service_mode: str) -> str:
    """
    Map service_mode to appointment_method.
    Office/Phone/FaceTime are appointment methods, not billing types.
    """
    if service_mode in {"phone", "facetime", "office"}:
        return service_mode
    if service_mode == "house_call":
        return "office"
    return "unknown"


def derive_duration_choice(duration_minutes: int | None) -> tuple[str, int | None]:
    """
    Map duration to standard choice or custom.
    Standard: 30, 60, 90, 120
    Custom: anything else

    Returns: (duration_choice, custom_duration_minutes)
    """
    if duration_minutes is None:
        return "60", None
    if duration_minutes in STANDARD_DURATION_CHOICES:
        return str(duration_minutes), None
    return "custom", duration_minutes


def check_late_evening(start_at: str) -> bool:
    """Check if start time is after 10 PM (22:00) for review warning."""
    start = parse_datetime(start_at)
    if not start:
        return False
    return start.hour >= 22


def finalize_result(result: ParseResult) -> ParseResult:
    result.fields_requiring_review = sorted(set(result.fields_requiring_review))
    result.unresolved_fields = list(result.fields_requiring_review)
    result.review_reasons = build_review_reasons(result)
    result.confidence_label = confidence_label(result)
    return result


def build_review_reasons(result: ParseResult) -> list[str]:
    reasons = []
    if result.fields_requiring_review:
        reasons.append(result.explanation)
    if result.service_mode == "unknown" and result.classification == "client_session":
        reasons.append("Service mode is unknown.")
    if result.relationship_review_required:
        reasons.append("Participant, account, and billing-party relationship needs review.")
    return sorted(set(reason for reason in reasons if reason))


def confidence_label(result: ParseResult) -> str:
    if result.classification in {"personal", "administrative", "cancelled", "nonbillable"} and result.confidence >= 0.65:
        return "excluded"
    if result.confidence >= 0.8:
        return "high"
    if result.confidence >= 0.5:
        return "medium"
    return "low"


def contains_any(value: str, needles: set[str]) -> bool:
    return any(needle in value for needle in needles)


_NAME_GUESS_STOPWORDS = {
    "am", "pm", "min", "mins", "minutes", "hour", "hours",
    "zoom", "phone", "facetime", "office", "home", "house",
    "late", "cx", "cancel", "cancelled", "canceled",
    "no", "show", "noshow",
    "leaves", "leave", "going", "away", "vacation",
    "for", "with", "and", "the", "a", "an",
}


def extract_name_guess(title: str) -> tuple[str | None, str | None]:
    """Try to extract a leading person name from an ambiguous title.

    Returns (name, trailing_text) or (None, None) if no name can be extracted.
    The name is a sequence of 1-3 leading capitalized alphabetic tokens
    that do not match known stopwords. Trailing text is everything after
    the name, preserved as administrative review context.
    """
    tokens = title.strip().split()
    if not tokens:
        return None, None

    name_tokens: list[str] = []
    for token in tokens:
        cleaned = token.strip(".,;:!?")
        if not cleaned or not re.search(r"[A-Za-z]", cleaned):
            break
        if not cleaned[0].isupper():
            break
        lower = cleaned.lower()
        if lower in _NAME_GUESS_STOPWORDS:
            break
        name_tokens.append(cleaned)
        if len(name_tokens) >= 3:
            break

    if not name_tokens:
        return None, None

    name = canonicalize_name(" ".join(name_tokens))
    trailing = " ".join(tokens[len(name_tokens):]).strip()
    return (name if name else None), (trailing if trailing else None)
