from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

from .util import new_id, now_iso, text


DEFAULT_PREFERRED_WORK_CALENDAR_ENV = "JORDANA_PREFERRED_WORK_CALENDAR"

CALENDAR_DISPOSITIONS = {
    "preferred_work",
    "review_normally",
    "usually_personal_admin",
    "hidden",
}


@dataclass(frozen=True)
class CalendarDisposition:
    calendar_name: str
    disposition: str
    is_preferred_work: bool = False
    hidden_from_review: bool = False
    source: str = "default"


def preferred_work_calendar() -> str:
    return text(os.environ.get(DEFAULT_PREFERRED_WORK_CALENDAR_ENV))


def classify_calendar(
    conn: sqlite3.Connection | None,
    calendar_name: str,
    *,
    preferred_calendar: str | None = None,
) -> CalendarDisposition:
    name = text(calendar_name)
    preferred = text(preferred_calendar) or preferred_work_calendar()
    if conn is not None and name:
        row = conn.execute(
            """
            SELECT disposition, hidden_from_review, source
            FROM calendar_preferences
            WHERE lower(calendar_name) = lower(?)
              AND active = 1
            LIMIT 1
            """,
            (name,),
        ).fetchone()
        if row:
            disposition = normalize_disposition(row["disposition"])
            return CalendarDisposition(
                calendar_name=name,
                disposition=disposition,
                is_preferred_work=disposition == "preferred_work" or calendars_match(name, preferred),
                hidden_from_review=bool(row["hidden_from_review"]) or disposition == "hidden",
                source=row["source"] or "manual",
            )
    if name and preferred and calendars_match(name, preferred):
        return CalendarDisposition(
            calendar_name=name,
            disposition="preferred_work",
            is_preferred_work=True,
            source="env",
        )
    return CalendarDisposition(
        calendar_name=name,
        disposition="review_normally",
        source="default",
    )


def upsert_calendar_preference(
    conn: sqlite3.Connection,
    calendar_name: str,
    disposition: str,
    *,
    source: str = "manual",
) -> dict[str, object]:
    name = text(calendar_name)
    if not name:
        raise ValueError("Calendar name is required.")
    disposition = normalize_disposition(disposition)
    hidden = 1 if disposition == "hidden" else 0
    now = now_iso()
    existing = conn.execute(
        "SELECT calendar_preference_id FROM calendar_preferences WHERE lower(calendar_name) = lower(?)",
        (name,),
    ).fetchone()
    if existing:
        preference_id = existing["calendar_preference_id"]
        conn.execute(
            """
            UPDATE calendar_preferences
            SET calendar_name = ?, disposition = ?, hidden_from_review = ?,
                source = ?, active = 1, updated_at = ?
            WHERE calendar_preference_id = ?
            """,
            (name, disposition, hidden, source, now, preference_id),
        )
    else:
        preference_id = new_id()
        conn.execute(
            """
            INSERT INTO calendar_preferences (
              calendar_preference_id, calendar_name, disposition,
              hidden_from_review, source, active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (preference_id, name, disposition, hidden, source, now, now),
        )
    return {
        "calendar_preference_id": preference_id,
        "calendar_name": name,
        "disposition": disposition,
        "hidden_from_review": bool(hidden),
        "source": source,
    }


def normalize_disposition(disposition: str) -> str:
    value = text(disposition).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "preferred": "preferred_work",
        "work": "preferred_work",
        "normal": "review_normally",
        "review": "review_normally",
        "personal": "usually_personal_admin",
        "personal_admin": "usually_personal_admin",
        "usually_personal": "usually_personal_admin",
    }
    value = aliases.get(value, value)
    if value not in CALENDAR_DISPOSITIONS:
        return "review_normally"
    return value


def calendars_match(left: str, right: str) -> bool:
    return text(left).casefold() == text(right).casefold()
