from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from .util import new_id, now_iso, parse_int, text


WEEKEND_EVENING_POLICY = "weekend_evening_policy"
DEFAULT_WEEKEND_EVENING_POLICY = "manual_review"


@dataclass
class RateSuggestion:
    suggested_rate_cents: int | None
    rate_rule_id: str | None
    rate_source: str
    rate_needs_review: bool
    explanation: str


def seed_rate_rule(
    conn: sqlite3.Connection,
    amount_cents: int,
    effective_from: str,
    duration_minutes: int | None = None,
    service_mode: str | None = None,
    rate_group: str | None = None,
    time_category: str = "standard",
    client_account_id: str | None = None,
    person_id: str | None = None,
    participant_person_ids: list[str] | None = None,
    priority: int = 100,
) -> str:
    now = now_iso()
    rule_id = new_id()
    conn.execute(
        """
        INSERT INTO rate_rules (
          rate_rule_id, client_account_id, person_id, duration_minutes,
          service_mode, rate_group, time_category, amount_cents,
          effective_from, priority, active, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            rule_id,
            client_account_id,
            person_id,
            duration_minutes,
            service_mode,
            rate_group,
            time_category,
            amount_cents,
            effective_from,
            priority,
            now,
            now,
        ),
    )
    for participant_id in sorted(set(participant_person_ids or [])):
        conn.execute(
            """
            INSERT OR IGNORE INTO rate_rule_participants (
              rate_rule_participant_id, rate_rule_id, person_id, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (new_id(), rule_id, participant_id, now),
        )
    return rule_id


def set_rate_policy(conn: sqlite3.Connection, policy_name: str, policy_value: str) -> None:
    now = now_iso()
    conn.execute(
        """
        INSERT INTO rate_policy (policy_name, policy_value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(policy_name) DO UPDATE SET
          policy_value = excluded.policy_value,
          updated_at = excluded.updated_at
        """,
        (policy_name, policy_value, now),
    )


def get_rate_policy(conn: sqlite3.Connection, policy_name: str) -> str:
    row = conn.execute(
        "SELECT policy_value FROM rate_policy WHERE policy_name = ?",
        (policy_name,),
    ).fetchone()
    if row:
        return text(row["policy_value"])
    return DEFAULT_WEEKEND_EVENING_POLICY if policy_name == WEEKEND_EVENING_POLICY else ""


def suggest_rate(
    conn: sqlite3.Connection,
    *,
    session_date: str,
    duration_minutes: int | None,
    service_mode: str | None,
    rate_group: str | None,
    time_category: str,
    account_id: str | None = None,
    person_id: str | None = None,
    participant_person_ids: list[str] | None = None,
) -> RateSuggestion:
    if time_category == "weekend_evening":
        policy = get_rate_policy(conn, WEEKEND_EVENING_POLICY)
        if policy == "manual_review":
            return RateSuggestion(
                None,
                None,
                "manual_review",
                True,
                "Weekend-evening rate policy is manual review.",
            )

    participant_ids = sorted(set(pid for pid in (participant_person_ids or []) if pid))
    if len(participant_ids) > 1:
        joint = find_matching_participant_rule(
            conn,
            participant_ids,
            session_date,
            duration_minutes,
            service_mode,
            rate_group,
            time_category,
        )
        if joint:
            return RateSuggestion(
                suggested_rate_cents=int(joint["amount_cents"]),
                rate_rule_id=joint["rate_rule_id"],
                rate_source="participant_combination_exception",
                rate_needs_review=False,
                explanation="Matched joint participant rate exception.",
            )

    scopes = [
        ("person_exception", "person_id = ?", person_id),
        ("account", "client_account_id = ?", account_id),
        ("default", "person_id IS NULL AND client_account_id IS NULL", None),
    ]
    for source, condition, value in scopes:
        if value is None and "?" in condition:
            continue
        row = find_matching_rule(
            conn,
            condition,
            value,
            session_date,
            duration_minutes,
            service_mode,
            rate_group,
            time_category,
        )
        if row:
            return RateSuggestion(
                suggested_rate_cents=int(row["amount_cents"]),
                rate_rule_id=row["rate_rule_id"],
                rate_source=source,
                rate_needs_review=False,
                explanation=rate_explanation(source),
            )

    return RateSuggestion(
        None,
        None,
        "none",
        True,
        "No matching effective-dated rate rule.",
    )


def find_matching_participant_rule(
    conn: sqlite3.Connection,
    participant_ids: list[str],
    session_date: str,
    duration_minutes: int | None,
    service_mode: str | None,
    rate_group: str | None,
    time_category: str,
) -> sqlite3.Row | None:
    placeholders = ",".join("?" for _ in participant_ids)
    return conn.execute(
        f"""
        SELECT rr.*
        FROM rate_rules rr
        JOIN rate_rule_participants rrp ON rrp.rate_rule_id = rr.rate_rule_id
        WHERE rr.active = 1
          AND rr.effective_from <= ?
          AND (rr.effective_through IS NULL OR rr.effective_through = '' OR rr.effective_through >= ?)
          AND (rr.duration_minutes IS NULL OR rr.duration_minutes = ?)
          AND (rr.service_mode IS NULL OR rr.service_mode = ?)
          AND (rr.rate_group IS NULL OR rr.rate_group = ?)
          AND (rr.time_category = 'standard' OR rr.time_category = ?)
          AND rrp.person_id IN ({placeholders})
        GROUP BY rr.rate_rule_id
        HAVING COUNT(DISTINCT rrp.person_id) = ?
           AND (
             SELECT COUNT(*) FROM rate_rule_participants exact
             WHERE exact.rate_rule_id = rr.rate_rule_id
           ) = ?
        ORDER BY
          CASE WHEN rr.duration_minutes IS NOT NULL THEN 1 ELSE 0 END DESC,
          CASE WHEN rr.service_mode IS NOT NULL THEN 1 ELSE 0 END DESC,
          CASE WHEN rr.rate_group IS NOT NULL THEN 1 ELSE 0 END DESC,
          CASE WHEN rr.time_category = ? THEN 1 ELSE 0 END DESC,
          rr.priority ASC,
          rr.effective_from DESC
        LIMIT 1
        """,
        (
            session_date,
            session_date,
            duration_minutes,
            service_mode,
            rate_group,
            time_category,
            *participant_ids,
            len(participant_ids),
            len(participant_ids),
            time_category,
        ),
    ).fetchone()


def find_matching_rule(
    conn: sqlite3.Connection,
    scope_condition: str,
    scope_value: str | None,
    session_date: str,
    duration_minutes: int | None,
    service_mode: str | None,
    rate_group: str | None,
    time_category: str,
) -> sqlite3.Row | None:
    params: list[object] = []
    if scope_value is not None:
        params.append(scope_value)
    params.extend(
        [
            session_date,
            session_date,
            duration_minutes,
            service_mode,
            rate_group,
            time_category,
        ]
    )
    return conn.execute(
        f"""
        SELECT *
        FROM rate_rules
        WHERE active = 1
          AND {scope_condition}
          AND effective_from <= ?
          AND (effective_through IS NULL OR effective_through = '' OR effective_through >= ?)
          AND (duration_minutes IS NULL OR duration_minutes = ?)
          AND (service_mode IS NULL OR service_mode = ?)
          AND (rate_group IS NULL OR rate_group = ?)
          AND (time_category = 'standard' OR time_category = ?)
        ORDER BY
          CASE WHEN person_id IS NOT NULL THEN 1 ELSE 0 END DESC,
          CASE WHEN client_account_id IS NOT NULL THEN 1 ELSE 0 END DESC,
          CASE WHEN duration_minutes IS NOT NULL THEN 1 ELSE 0 END DESC,
          CASE WHEN service_mode IS NOT NULL THEN 1 ELSE 0 END DESC,
          CASE WHEN rate_group IS NOT NULL THEN 1 ELSE 0 END DESC,
          CASE WHEN time_category = ? THEN 1 ELSE 0 END DESC,
          priority ASC,
          effective_from DESC
        LIMIT 1
        """,
        (*params, time_category),
    ).fetchone()


def rate_explanation(source: str) -> str:
    return {
        "default": "Default matching rate.",
        "person_exception": "Person-specific rate exception.",
        "account": "Account-specific rate rule.",
    }.get(source, "Matched rate rule.")


def dollars_to_cents(value: str) -> int:
    cleaned = text(value).replace("$", "").replace(",", "")
    cents = round(float(cleaned) * 100)
    return int(cents)


def cents_to_dollars(value: int | None) -> str:
    if value is None:
        return ""
    return f"{value / 100:.2f}"
