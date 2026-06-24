from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date

from .session_types import rate_rule_appointment_status_for_session
from .util import new_id, now_iso, parse_int, text


WEEKEND_EVENING_POLICY = "weekend_evening_policy"
DEFAULT_WEEKEND_EVENING_POLICY = "manual_review"

EQUIVALENT_APPOINTMENT_METHODS = {"office", "phone", "facetime"}
EQUIVALENT_RATE_GROUPS = {"remote", "office"}


def normalize_rate_inputs(
    service_mode: str | None,
    rate_group: str | None,
) -> tuple[str | None, str | None, bool]:
    is_equivalent = service_mode in EQUIVALENT_APPOINTMENT_METHODS
    return service_mode, rate_group, is_equivalent


def normalize_custom_service_description(value: str | None) -> str:
    return re.sub(r"\s+", " ", text(value).strip()).lower()


def normalize_custom_service_code(value: str | None) -> str:
    return re.sub(r"\s+", "", text(value).strip()).upper()


@dataclass
class RateSuggestion:
    suggested_rate_cents: int | None
    rate_rule_id: str | None
    rate_source: str
    rate_needs_review: bool
    explanation: str


def billing_session_type_candidates(billing_session_type: str | None) -> list[str | None]:
    return [billing_session_type]


def seed_rate_rule(
    conn: sqlite3.Connection,
    amount_cents: int,
    effective_from: str,
    duration_minutes: int | None = None,
    billing_session_type: str | None = None,
    appointment_status: str = "scheduled",
    custom_service_description: str | None = None,
    custom_service_code: str | None = None,
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
          billing_session_type, appointment_status, custom_service_description, custom_service_code,
          service_mode, rate_group, time_category, amount_cents,
          effective_from, priority, active, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            rule_id,
            client_account_id,
            person_id,
            duration_minutes,
            billing_session_type,
            appointment_status,
            text(custom_service_description).strip() or None,
            text(custom_service_code).strip() or None,
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
    billing_session_type: str | None = None,
    appointment_status: str | None = None,
    custom_service_description: str | None = None,
    custom_service_code: str | None = None,
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

    normalized_appointment_status = rate_rule_appointment_status_for_session(appointment_status)
    participant_ids = sorted(set(pid for pid in (participant_person_ids or []) if pid))
    for billing_session_type_candidate in billing_session_type_candidates(billing_session_type):
        if len(participant_ids) > 1:
            joint = find_matching_participant_rule(
                conn,
                participant_ids,
                session_date,
                duration_minutes,
                billing_session_type_candidate,
                normalized_appointment_status,
                custom_service_description,
                custom_service_code,
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
            ("billing_relationship", "client_account_id = ?", account_id),
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
                billing_session_type_candidate,
                normalized_appointment_status,
                custom_service_description,
                custom_service_code,
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
    billing_session_type: str | None,
    appointment_status: str,
    custom_service_description: str | None,
    custom_service_code: str | None,
    service_mode: str | None,
    rate_group: str | None,
    time_category: str,
) -> sqlite3.Row | None:
    placeholders = ",".join("?" for _ in participant_ids)
    rows = conn.execute(
        f"""
        SELECT rr.*
        FROM rate_rules rr
        JOIN rate_rule_participants rrp ON rrp.rate_rule_id = rr.rate_rule_id
        WHERE rr.active = 1
          AND rr.effective_from <= ?
          AND (rr.effective_through IS NULL OR rr.effective_through = '' OR rr.effective_through >= ?)
          AND (rr.duration_minutes IS NULL OR rr.duration_minutes = ?)
          AND (rr.billing_session_type IS NULL OR rr.billing_session_type = ?)
          AND rr.appointment_status = ?
          AND rr.time_category = ?
          AND rrp.person_id IN ({placeholders})
        GROUP BY rr.rate_rule_id
        HAVING COUNT(DISTINCT rrp.person_id) = ?
           AND (
             SELECT COUNT(*) FROM rate_rule_participants exact
             WHERE exact.rate_rule_id = rr.rate_rule_id
           ) = ?
        ORDER BY
          CASE WHEN rr.billing_session_type IS NOT NULL THEN 1 ELSE 0 END DESC,
          CASE WHEN rr.duration_minutes IS NOT NULL THEN 1 ELSE 0 END DESC,
          CASE WHEN rr.service_mode IS NOT NULL THEN 1 ELSE 0 END DESC,
          CASE WHEN rr.rate_group IS NOT NULL THEN 1 ELSE 0 END DESC,
          CASE WHEN rr.time_category = ? THEN 1 ELSE 0 END DESC,
          rr.priority ASC,
          rr.effective_from DESC
        """,
        (
            session_date,
            session_date,
            duration_minutes,
            billing_session_type,
            appointment_status,
            time_category,
            *participant_ids,
            len(participant_ids),
            len(participant_ids),
            time_category,
        ),
    ).fetchall()
    return first_matching_candidate_rule(
        rows,
        billing_session_type=billing_session_type,
        custom_service_description=custom_service_description,
        custom_service_code=custom_service_code,
        service_mode=service_mode,
        rate_group=rate_group,
    )


def find_matching_rule(
    conn: sqlite3.Connection,
    scope_condition: str,
    scope_value: str | None,
    session_date: str,
    duration_minutes: int | None,
    billing_session_type: str | None,
    appointment_status: str,
    custom_service_description: str | None,
    custom_service_code: str | None,
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
            billing_session_type,
            appointment_status,
            time_category,
        ]
    )
    rows = conn.execute(
        f"""
        SELECT *
        FROM rate_rules
        WHERE active = 1
          AND {scope_condition}
          AND rate_rule_id NOT IN (SELECT rate_rule_id FROM rate_rule_participants)
          AND effective_from <= ?
          AND (effective_through IS NULL OR effective_through = '' OR effective_through >= ?)
          AND (duration_minutes IS NULL OR duration_minutes = ?)
          AND (billing_session_type IS NULL OR billing_session_type = ?)
          AND appointment_status = ?
          AND time_category = ?
        ORDER BY
          CASE WHEN person_id IS NOT NULL THEN 1 ELSE 0 END DESC,
          CASE WHEN client_account_id IS NOT NULL THEN 1 ELSE 0 END DESC,
          CASE WHEN billing_session_type IS NOT NULL THEN 1 ELSE 0 END DESC,
          CASE WHEN duration_minutes IS NOT NULL THEN 1 ELSE 0 END DESC,
          CASE WHEN service_mode IS NOT NULL THEN 1 ELSE 0 END DESC,
          CASE WHEN rate_group IS NOT NULL THEN 1 ELSE 0 END DESC,
          CASE WHEN time_category = ? THEN 1 ELSE 0 END DESC,
          priority ASC,
          effective_from DESC
        """,
        (*params, time_category),
    ).fetchall()
    return first_matching_candidate_rule(
        rows,
        billing_session_type=billing_session_type,
        custom_service_description=custom_service_description,
        custom_service_code=custom_service_code,
        service_mode=service_mode,
        rate_group=rate_group,
    )


def first_matching_candidate_rule(
    rows: list[sqlite3.Row],
    *,
    billing_session_type: str | None,
    custom_service_description: str | None,
    custom_service_code: str | None,
    service_mode: str | None,
    rate_group: str | None,
) -> sqlite3.Row | None:
    for row in rows:
        if not rule_matches_custom_fields(
            row,
            billing_session_type=billing_session_type,
            custom_service_description=custom_service_description,
            custom_service_code=custom_service_code,
        ):
            continue
        if not rule_matches_method_equivalence(
            row,
            service_mode=service_mode,
            rate_group=rate_group,
        ):
            continue
        return row
    return None


def rule_matches_custom_fields(
    row: sqlite3.Row,
    *,
    billing_session_type: str | None,
    custom_service_description: str | None,
    custom_service_code: str | None,
) -> bool:
    if text(row["billing_session_type"]) != "custom" and billing_session_type != "custom":
        return True
    if billing_session_type != "custom":
        return False
    session_code = normalize_custom_service_code(custom_service_code)
    session_desc = normalize_custom_service_description(custom_service_description)
    rule_code = normalize_custom_service_code(row["custom_service_code"])
    rule_desc = normalize_custom_service_description(row["custom_service_description"])
    if session_code:
        return bool(rule_code and rule_code == session_code)
    return bool(session_desc and rule_desc and rule_desc == session_desc)


def rule_matches_method_equivalence(
    row: sqlite3.Row,
    *,
    service_mode: str | None,
    rate_group: str | None,
) -> bool:
    row_service_mode = text(row["service_mode"]) or None
    row_rate_group = text(row["rate_group"]) or None
    if row_service_mode is None and row_rate_group is None:
        return True
    normalized_service_mode, normalized_rate_group, is_equivalent_method = normalize_rate_inputs(service_mode, rate_group)
    if is_equivalent_method:
        service_ok = row_service_mode is None or row_service_mode in EQUIVALENT_APPOINTMENT_METHODS
        group_ok = row_rate_group is None or row_rate_group in EQUIVALENT_RATE_GROUPS
        return service_ok and group_ok
    service_ok = row_service_mode is None or row_service_mode == normalized_service_mode
    group_ok = row_rate_group is None or row_rate_group == normalized_rate_group
    return service_ok and group_ok


def rate_explanation(source: str) -> str:
    return {
        "default": "Default matching rate.",
        "person_exception": "Person-specific rate exception.",
        "billing_relationship": "Billing-relationship rate rule.",
    }.get(source, "Matched rate rule.")


def dollars_to_cents(value: str) -> int:
    cleaned = text(value).replace("$", "").replace(",", "")
    cents = round(float(cleaned) * 100)
    return int(cents)


def cents_to_dollars(value: int | None) -> str:
    if value is None:
        return ""
    return f"{value / 100:.2f}"
