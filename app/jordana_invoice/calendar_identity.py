from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


def calendar_text(value: Any) -> str:
    """Return a display-insensitive representation of calendar text.

    Calendar titles and calendar names are human-entered data.  Collapsing
    whitespace and case prevents harmless serialization differences from
    creating a second billing candidate.  The original text always remains in
    the raw snapshot and is never changed here.
    """

    return " ".join(str(value or "").split()).casefold()


def row_value(row: Mapping[str, Any] | Any, field: str) -> Any:
    try:
        return row[field]
    except (KeyError, IndexError, TypeError):
        return ""


def utc_datetime(value: Any) -> datetime | None:
    """Parse an offset-aware calendar timestamp as a UTC datetime.

    Naive or malformed values deliberately return None rather than being
    guessed into a timezone.  That keeps ambiguous evidence reviewable.
    """

    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def canonical_datetime(value: Any) -> str:
    """Return a UTC timestamp for known instants, otherwise normalized text."""

    parsed = utc_datetime(value)
    if parsed is None:
        return calendar_text(value)
    return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_structural_parts(
    row: Mapping[str, Any] | Any,
    *,
    include_duration: bool = True,
) -> tuple[str, ...]:
    """Build a timezone-stable structural calendar identity.

    The tuple is intentionally only for matching; it does not replace the raw
    event title, timestamps, calendar, or source identifiers retained in
    SQLite and Google Sheets.
    """

    parts = (
        calendar_text(row_value(row, "event_title") or row_value(row, "raw_calendar_title") or row_value(row, "title")),
        canonical_datetime(row_value(row, "start_at")),
        canonical_datetime(row_value(row, "end_at")),
    )
    if include_duration:
        parts += (
            str(
                row_value(row, "duration_minutes")
                or row_value(row, "calendar_duration_minutes")
                or row_value(row, "proposed_duration_minutes")
                or row_value(row, "approved_duration_minutes")
                or ""
            ).strip(),
        )
    return parts + (calendar_text(row_value(row, "calendar_name") or row_value(row, "calendar")),)


def canonical_event_slot_parts(row: Mapping[str, Any] | Any) -> tuple[str, str, str]:
    """Return the non-duration portion of a calendar event identity.

    This is intentionally narrower than ``canonical_structural_parts``. It
    is used only to spot conflicting legacy observations that claim to be the
    same title, instant, and calendar but disagree about end time or duration.
    Such a conflict is never automatically collapsed into a billable session.
    """

    return (
        calendar_text(row_value(row, "event_title") or row_value(row, "raw_calendar_title") or row_value(row, "title")),
        canonical_datetime(row_value(row, "start_at")),
        calendar_text(row_value(row, "calendar_name") or row_value(row, "calendar")),
    )


def has_complete_event_slot_identity(row: Mapping[str, Any] | Any) -> bool:
    return all(canonical_event_slot_parts(row))


def has_complete_structural_identity(row: Mapping[str, Any] | Any) -> bool:
    title, start_at, end_at, *remaining = canonical_structural_parts(row)
    calendar_name = remaining[-1] if remaining else ""
    return bool(title and start_at and end_at and calendar_name)
