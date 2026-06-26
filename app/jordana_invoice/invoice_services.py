from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .invoice_pdf import generate_invoice_pdf
from .service_catalog import learn_service, list_services
from .session_types import get_user_facing_session_label
from .util import json_dumps, new_id, normalize_payment_status, now_iso
from .db import DatabaseBusyError


def init_db(_conn: sqlite3.Connection) -> None:
    """No-op; schema migrations run explicitly at startup via migrate_database()."""
    pass


DELIVERY_METHODS = {"email", "mail", "both", "unresolved"}
INVOICE_STATUSES = {"draft", "finalized", "void"}


def get_business_profile(conn: sqlite3.Connection) -> dict[str, Any] | None:
    init_db(conn)
    row = conn.execute("SELECT * FROM business_profile WHERE active = 1 LIMIT 1").fetchone()
    return dict(row) if row else None


def save_business_profile(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    init_db(conn)
    now = now_iso()
    existing = conn.execute("SELECT * FROM business_profile WHERE active = 1 LIMIT 1").fetchone()
    profile_id = existing["business_profile_id"] if existing else new_id()
    fields = (
        "business_name", "provider_display_name", "credentials_display", "address_line_1", "address_line_2",
        "city", "state", "postal_code", "phone", "email", "payee_name", "payment_address_line_1",
        "payment_address_line_2", "payment_city", "payment_state", "payment_postal_code", "logo_path",
        "logo_contains_business_details", "show_email_below_logo", "invoice_total_label", "invoice_number_format",
    )
    values = {field: data.get(field, existing[field] if existing else None) for field in fields}
    if not str(values["business_name"] or "").strip():
        raise ValueError("Business name is required.")
    values["invoice_total_label"] = values["invoice_total_label"] or "TOTAL DUE"
    values["invoice_number_format"] = values["invoice_number_format"] or "YYYY-NNNN"
    values["logo_contains_business_details"] = 1 if values["logo_contains_business_details"] else 0
    values["show_email_below_logo"] = 1 if values["show_email_below_logo"] else 0
    if existing:
        assignments = ", ".join(f"{field} = ?" for field in fields)
        conn.execute(f"UPDATE business_profile SET {assignments}, updated_at = ? WHERE business_profile_id = ?", (*[values[f] for f in fields], now, profile_id))
        action = "updated"
    else:
        conn.execute(
            f"INSERT INTO business_profile (business_profile_id, {', '.join(fields)}, active, created_at, updated_at) VALUES (?, {', '.join('?' for _ in fields)}, 1, ?, ?)",
            (profile_id, *[values[f] for f in fields], now, now),
        )
        action = "created"
    _audit(conn, "business_profile", profile_id, action, {"changed_fields": sorted(data)})
    conn.commit()
    return get_business_profile(conn) or {}


def list_invoice_records(conn: sqlite3.Connection, status: str | None = None) -> list[dict[str, Any]]:
    init_db(conn)
    params: list[Any] = []
    where = ""
    if status in INVOICE_STATUSES:
        where = "WHERE i.status = ?"
        params.append(status)
    rows = conn.execute(
        f"""
        SELECT i.*, bp.billing_name AS current_bill_to_name, COUNT(li.invoice_line_item_id) AS line_count
        FROM invoices i
        JOIN billing_parties bp ON bp.billing_party_id = i.bill_to_party_id
        LEFT JOIN invoice_line_items li ON li.invoice_id = i.invoice_id
        {where}
        GROUP BY i.invoice_id
        ORDER BY i.created_at DESC
        """, params
    ).fetchall()
    return [dict(row) for row in rows]


def get_invoice(conn: sqlite3.Connection, invoice_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)).fetchone()
    if not row:
        raise ValueError("Invoice was not found.")
    lines = conn.execute("SELECT * FROM invoice_line_items WHERE invoice_id = ? ORDER BY sort_order, created_at", (invoice_id,)).fetchall()
    profile = None
    party = None
    if row["status"] == "draft":
        current_profile = conn.execute("SELECT * FROM business_profile WHERE active = 1 LIMIT 1").fetchone()
        current_party = conn.execute("SELECT * FROM billing_parties WHERE billing_party_id = ?", (row["bill_to_party_id"],)).fetchone()
        profile = dict(current_profile) if current_profile else None
        party = dict(current_party) if current_party else None
    return {"invoice": dict(row), "lines": [dict(line) for line in lines], "business_profile": profile, "billing_party": party}


def eligible_sessions(
    conn: sqlite3.Connection,
    billing_party_id: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
) -> list[dict[str, Any]]:
    init_db(conn)
    filters = ["1=1"]
    params: list[Any] = []
    if billing_party_id:
        filters.append("s.billing_party_id = ?")
        params.append(billing_party_id)
    if period_start:
        filters.append("s.session_date >= ?")
        params.append(period_start)
    if period_end:
        filters.append("s.session_date <= ?")
        params.append(period_end)
    rows = conn.execute(
        f"""
        SELECT s.*, bp.billing_name,
          GROUP_CONCAT(COALESCE(p.display_name, sp.participant_name), ' & ') AS participants
        FROM sessions s
        LEFT JOIN billing_parties bp ON bp.billing_party_id = s.billing_party_id
        LEFT JOIN session_participants sp ON sp.session_id = s.id
        LEFT JOIN people p ON p.person_id = sp.person_id
        WHERE {' AND '.join(filters)}
        GROUP BY s.id ORDER BY s.session_date, s.start_at
        """, params
    ).fetchall()
    result = []
    for row in rows:
        reasons = invoice_ineligibility_reasons(conn, row)
        item = dict(row)
        item["eligible"] = not reasons
        item["ineligibility_reasons"] = reasons
        result.append(item)
    return result


def invoice_ineligibility_reasons(conn: sqlite3.Connection, session: sqlite3.Row | dict[str, Any], excluding_invoice_id: str | None = None) -> list[str]:
    s = dict(session)
    reasons = []
    if s.get("review_status") != "approved": reasons.append("Session is not approved")
    count = conn.execute("SELECT COUNT(*) FROM session_participants WHERE session_id = ?", (s["id"],)).fetchone()[0]
    if not count: reasons.append("Participants are not confirmed")
    if not s.get("billing_party_id"): reasons.append("Bill-to party is not confirmed")
    if s.get("approved_rate_cents") is None and s.get("rate_cents_snapshot") is None: reasons.append("Approved charged amount is missing")
    amount = s.get("rate_cents_snapshot") if s.get("rate_cents_snapshot") is not None else s.get("approved_rate_cents")
    if amount is not None and int(amount) < 0: reasons.append("Approved amount cannot be negative")
    if s.get("appointment_status") == "scheduled": reasons.append("Future scheduled session is not invoice eligible")
    if s.get("billable_status") in {"excluded", "nonbillable"}: reasons.append("Session is excluded or nonbillable")
    if normalize_payment_status(s.get("payment_status")) == "paid_at_session": reasons.append("Session was paid at time of session")
    if s.get("appointment_status") in {"cancelled", "no_show"} and s.get("billing_treatment") != "billable":
        reasons.append("Cancelled or no-show session requires explicit billable treatment")
    params: list[Any] = [s["id"]]
    invoice_filter = ""
    if excluding_invoice_id:
        invoice_filter = "AND i.invoice_id != ?"
        params.append(excluding_invoice_id)
    attached = conn.execute(
        f"""SELECT i.status FROM invoice_line_items li JOIN invoices i ON i.invoice_id = li.invoice_id
        WHERE li.source_session_id = ? AND i.status IN ('draft','finalized') {invoice_filter} LIMIT 1""", params
    ).fetchone()
    if attached: reasons.append("Session is already attached to an active invoice")
    return reasons


def _last_day_of_month(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    from datetime import timedelta
    return date(year, month + 1, 1) - timedelta(days=1)


def _derive_billing_month(start: str, end: str) -> str | None:
    """Return YYYY-MM if start..end is exactly one complete calendar month, else None."""
    try:
        d_start = date.fromisoformat(start[:10])
        d_end = date.fromisoformat(end[:10])
    except (ValueError, TypeError):
        return None
    if d_start.day != 1:
        return None
    if d_start.year != d_end.year or d_start.month != d_end.month:
        return None
    if d_end != _last_day_of_month(d_start.year, d_start.month):
        return None
    return f"{d_start.year:04d}-{d_start.month:02d}"


def create_invoice_draft(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    init_db(conn)
    billing_party_id = str(data.get("bill_to_party_id") or "")
    party = conn.execute("SELECT * FROM billing_parties WHERE billing_party_id = ? AND active = 1", (billing_party_id,)).fetchone()
    if not party: raise ValueError("Select an active bill-to party.")

    billing_month = str(data.get("billing_month") or "").strip() or None
    start = str(data.get("billing_period_start") or "")
    end = str(data.get("billing_period_end") or "")

    if billing_month:
        # Validate YYYY-MM format
        try:
            bm_year, bm_mon = billing_month.split("-")
            bm_year_i, bm_mon_i = int(bm_year), int(bm_mon)
            if bm_mon_i < 1 or bm_mon_i > 12:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("billing_month must be in YYYY-MM format.")
        derived_start = date(bm_year_i, bm_mon_i, 1).isoformat()
        derived_end = _last_day_of_month(bm_year_i, bm_mon_i).isoformat()
        if start and end:
            if start != derived_start or end != derived_end:
                raise ValueError(
                    f"billing_period_start/end ({start} to {end}) do not match "
                    f"billing_month {billing_month} ({derived_start} to {derived_end})."
                )
        start, end = derived_start, derived_end
    else:
        if not start or not end or start > end:
            raise ValueError("A valid billing period is required.")
        billing_month = _derive_billing_month(start, end)

    method = str(data.get("delivery_method") or party["preferred_delivery_method"] or "unresolved")
    if method not in DELIVERY_METHODS: raise ValueError("Invalid delivery method.")
    supplement_sequence = int(data.get("supplement_sequence") or 0)
    if supplement_sequence < 0:
        raise ValueError("supplement_sequence cannot be negative.")
    invoice_id, now = new_id(), now_iso()
    conn.execute(
        """INSERT INTO invoices (
          invoice_id, status, bill_to_party_id, billing_period_start, billing_period_end,
          billing_month, supplement_sequence,
          invoice_date, delivery_method, notes, created_at, updated_at
        ) VALUES (?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (invoice_id, billing_party_id, start, end, billing_month, supplement_sequence,
         data.get("invoice_date") or date.today().isoformat(), method, data.get("notes"), now, now),
    )
    _audit(conn, "invoice", invoice_id, "draft_created", {"bill_to_party_id": billing_party_id, "billing_month": billing_month})
    conn.commit()
    session_ids = data.get("session_ids") or []
    if session_ids:
        try:
            add_sessions_to_draft(conn, invoice_id, session_ids)
        except Exception:
            conn.execute("DELETE FROM audit_log WHERE entity_type = 'invoice' AND entity_id = ?", (invoice_id,))
            conn.execute("DELETE FROM invoices WHERE invoice_id = ?", (invoice_id,))
            conn.commit()
            raise
    return get_invoice(conn, invoice_id)


def _insert_line_item(conn: sqlite3.Connection, invoice_id: str, session: sqlite3.Row | dict[str, Any], order: int) -> None:
    """Insert a single invoice line item from a session, reusing existing snapshot logic."""
    session_id = session["id"]
    catalog = learn_service(conn, session["service_mode"] or "Other")
    participants = _participant_names(conn, session_id)
    service_name = catalog["display_name"]
    description = _service_description(session, service_name)
    amount = session["rate_cents_snapshot"] if session["rate_cents_snapshot"] is not None else session["approved_rate_cents"]
    now = now_iso()
    billing_type = session["billing_session_type"] if "billing_session_type" in session.keys() else None
    custom_desc = session["custom_service_description"] if "custom_service_description" in session.keys() else None
    custom_code = session["custom_service_code"] if "custom_service_code" in session.keys() else None
    conn.execute(
        """INSERT INTO invoice_line_items (
          invoice_line_item_id, invoice_id, source_session_id, sort_order, service_date,
          participants_snapshot, service_catalog_id, service_name_snapshot, billing_session_type_snapshot,
          time_category_snapshot, appointment_status_snapshot, duration_minutes, description_snapshot,
          custom_service_description_snapshot, custom_service_code_snapshot, quantity,
          unit_amount_cents, line_amount_cents, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)""",
        (new_id(), invoice_id, session_id, order, session["session_date"], participants,
         catalog["service_catalog_id"], service_name, billing_type, session["time_category"],
         session["appointment_status"], session["approved_duration_minutes"] or session["duration_minutes"],
         description, custom_desc, custom_code, amount, amount, now, now),
    )


def add_sessions_to_draft(conn: sqlite3.Connection, invoice_id: str, session_ids: list[str]) -> dict[str, Any]:
    invoice = _draft(conn, invoice_id)
    conn.execute("BEGIN IMMEDIATE")
    try:
        order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM invoice_line_items WHERE invoice_id = ?", (invoice_id,)).fetchone()[0]
        for session_id in session_ids:
            duplicate = conn.execute("SELECT 1 FROM invoice_line_items WHERE invoice_id = ? AND source_session_id = ?", (invoice_id, session_id)).fetchone()
            if duplicate: raise ValueError("Session is already included in this draft.")
            session = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if not session: raise ValueError("Source session was not found.")
            if session["billing_party_id"] != invoice["bill_to_party_id"]: raise ValueError("All invoice sessions must use the selected bill-to party.")
            reasons = invoice_ineligibility_reasons(conn, session, excluding_invoice_id=invoice_id)
            if reasons: raise ValueError("Session is not invoice eligible: " + "; ".join(reasons))
            if session["session_date"] < invoice["billing_period_start"] or session["session_date"] > invoice["billing_period_end"]:
                raise ValueError("Session is outside the invoice billing period.")
            _insert_line_item(conn, invoice_id, session, order)
            order += 1
        _recalculate(conn, invoice_id)
        conn.execute("UPDATE invoices SET revision = revision + 1, updated_at = ? WHERE invoice_id = ?", (now_iso(), invoice_id))
        _audit(conn, "invoice", invoice_id, "sessions_added", {"session_ids": session_ids})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_invoice(conn, invoice_id)


def update_invoice_draft(conn: sqlite3.Connection, invoice_id: str, data: dict[str, Any]) -> dict[str, Any]:
    _draft(conn, invoice_id)
    conn.execute("BEGIN IMMEDIATE")
    try:
        method = data.get("delivery_method")
        if method is not None and method not in DELIVERY_METHODS: raise ValueError("Invalid delivery method.")
        fields = {key: data[key] for key in ("invoice_date", "billing_period_start", "billing_period_end", "delivery_method", "notes", "adjustment_cents") if key in data}
        if "billing_period_start" in fields or "billing_period_end" in fields:
            row = conn.execute("SELECT billing_period_start, billing_period_end FROM invoices WHERE invoice_id = ?", (invoice_id,)).fetchone()
            new_start = fields.get("billing_period_start", row["billing_period_start"])
            new_end = fields.get("billing_period_end", row["billing_period_end"])
            fields["billing_month"] = _derive_billing_month(str(new_start), str(new_end))
        if fields:
            fields["updated_at"] = now_iso()
            conn.execute(f"UPDATE invoices SET {', '.join(f'{k} = ?' for k in fields)} WHERE invoice_id = ?", (*fields.values(), invoice_id))
        for index, item in enumerate(data.get("lines") or []):
            line_id = item.get("invoice_line_item_id")
            if not line_id: continue
            updates = {"sort_order": item.get("sort_order", index), "updated_at": now_iso()}
            for key in ("description_snapshot", "duration_minutes"):
                if key in item: updates[key] = item[key]
            conn.execute(f"UPDATE invoice_line_items SET {', '.join(f'{k} = ?' for k in updates)} WHERE invoice_line_item_id = ? AND invoice_id = ?", (*updates.values(), line_id, invoice_id))
        _recalculate(conn, invoice_id)
        conn.execute("UPDATE invoices SET revision = revision + 1, updated_at = ? WHERE invoice_id = ?", (now_iso(), invoice_id))
        _audit(conn, "invoice", invoice_id, "draft_updated", {"changed_fields": sorted(data)})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_invoice(conn, invoice_id)


def remove_line_from_draft(conn: sqlite3.Connection, invoice_id: str, line_id: str) -> dict[str, Any]:
    _draft(conn, invoice_id)
    conn.execute("BEGIN IMMEDIATE")
    try:
        cursor = conn.execute("DELETE FROM invoice_line_items WHERE invoice_id = ? AND invoice_line_item_id = ?", (invoice_id, line_id))
        if not cursor.rowcount: raise ValueError("Invoice line was not found.")
        _recalculate(conn, invoice_id)
        conn.execute("UPDATE invoices SET revision = revision + 1, updated_at = ? WHERE invoice_id = ?", (now_iso(), invoice_id))
        _audit(conn, "invoice", invoice_id, "line_removed", {"invoice_line_item_id": line_id})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_invoice(conn, invoice_id)


def _session_month(session_date: str | None) -> str | None:
    """Extract YYYY-MM from a session date string, or None if invalid."""
    if not session_date:
        return None
    try:
        d = date.fromisoformat(str(session_date)[:10])
        return f"{d.year:04d}-{d.month:02d}"
    except (ValueError, TypeError):
        return None


def _find_or_create_monthly_draft(
    conn: sqlite3.Connection,
    billing_party_id: str,
    billing_month: str,
    *,
    party_row: sqlite3.Row | None = None,
) -> tuple[sqlite3.Row, bool]:
    """Find an existing open monthly draft for (party, month) or create one.

    Returns (draft_row, created_bool).
    """
    draft = conn.execute(
        "SELECT * FROM invoices WHERE bill_to_party_id = ? AND billing_month = ? AND status = 'draft'",
        (billing_party_id, billing_month),
    ).fetchone()
    if draft:
        return draft, False

    if party_row is None:
        party_row = conn.execute(
            "SELECT * FROM billing_parties WHERE billing_party_id = ? AND active = 1",
            (billing_party_id,),
        ).fetchone()
    if not party_row:
        raise ValueError(f"No active billing party found for {billing_party_id}")

    bm_year, bm_mon = billing_month.split("-")
    bm_year_i, bm_mon_i = int(bm_year), int(bm_mon)
    start = date(bm_year_i, bm_mon_i, 1).isoformat()
    end = _last_day_of_month(bm_year_i, bm_mon_i).isoformat()

    seq_row = conn.execute(
        "SELECT COALESCE(MAX(supplement_sequence), -1) + 1 AS next_seq "
        "FROM invoices WHERE bill_to_party_id = ? AND billing_month = ?",
        (billing_party_id, billing_month),
    ).fetchone()
    supplement_sequence = seq_row["next_seq"]

    method = str(party_row["preferred_delivery_method"] or "unresolved")
    if method not in DELIVERY_METHODS:
        method = "unresolved"

    invoice_id, now = new_id(), now_iso()
    conn.execute(
        """INSERT INTO invoices (
          invoice_id, status, bill_to_party_id, billing_period_start, billing_period_end,
          billing_month, supplement_sequence,
          invoice_date, delivery_method, notes, created_at, updated_at
        ) VALUES (?, 'draft', ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
        (invoice_id, billing_party_id, start, end, billing_month, supplement_sequence,
         date.today().isoformat(), method, now, now),
    )
    _audit(conn, "invoice", invoice_id, "draft_created_staging",
           {"bill_to_party_id": billing_party_id, "billing_month": billing_month,
            "supplement_sequence": supplement_sequence})
    return conn.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)).fetchone(), True


def stage_approved_sessions_to_monthly_drafts(
    conn: sqlite3.Connection,
    session_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Reconcile eligible approved sessions into monthly draft invoices.

    Idempotent: repeated calls produce the same correct result.

    Groups sessions by billing_party_id + calendar billing month, reuses
    existing open monthly drafts or creates supplemental drafts, moves
    stale draft lines whose session party or month changed, and removes
    lines whose session is no longer eligible.

    Returns a structured summary; does not expose private names.
    """
    init_db(conn)

    result: dict[str, Any] = {
        "drafts_created": 0,
        "drafts_reused": 0,
        "sessions_staged": 0,
        "sessions_already_staged": 0,
        "sessions_moved": 0,
        "sessions_removed_ineligible": 0,
        "sessions_skipped": [],
        "errors": [],
    }

    # --- Step 1: Determine the set of (party, month) groups to process ---

    # From eligible approved sessions
    session_filter = ""
    params: list[Any] = []
    if session_ids is not None:
        if not session_ids:
            return result
        placeholders = ", ".join("?" for _ in session_ids)
        session_filter = f" AND s.id IN ({placeholders})"
        params = list(session_ids)

    all_sessions = conn.execute(
        f"""SELECT s.* FROM sessions s
        WHERE s.review_status = 'approved' AND s.billing_party_id IS NOT NULL
              AND s.session_date IS NOT NULL{session_filter}""",
        params,
    ).fetchall()

    groups: dict[tuple[str, str], None] = {}
    for s in all_sessions:
        bm = _session_month(s["session_date"])
        if not bm:
            result["sessions_skipped"].append({
                "session_id": s["id"], "reasons": ["Invalid or nonmonthly session date"],
            })
            continue
        groups[(s["billing_party_id"], bm)] = None

    # From existing monthly drafts (to check for stale lines)
    drafts = conn.execute(
        "SELECT * FROM invoices WHERE status = 'draft' AND billing_month IS NOT NULL"
    ).fetchall()
    for d in drafts:
        groups[(d["bill_to_party_id"], d["billing_month"])] = None

    # --- Step 2: Process each (party, month) group in its own transaction ---

    for (party_id, billing_month) in sorted(groups.keys()):
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            if "locked" in str(error).lower():
                raise DatabaseBusyError(
                    "Cannot stage invoices: database is locked by another operation. "
                    "Please retry in a moment."
                ) from error
            raise

        try:
            # Look for existing draft without creating one yet
            existing_draft = conn.execute(
                "SELECT * FROM invoices WHERE bill_to_party_id = ? AND billing_month = ? AND status = 'draft'",
                (party_id, billing_month),
            ).fetchone()

            # --- Stale line reconciliation (only if a draft exists) ---
            draft_id: str | None = None
            draft_created = False
            draft_changed = False

            if existing_draft:
                draft_id = existing_draft["invoice_id"]
                lines = conn.execute(
                    "SELECT * FROM invoice_line_items WHERE invoice_id = ? ORDER BY sort_order",
                    (draft_id,),
                ).fetchall()

                for line in lines:
                    session = conn.execute(
                        "SELECT * FROM sessions WHERE id = ?", (line["source_session_id"],)
                    ).fetchone()
                    if not session:
                        conn.execute(
                            "DELETE FROM invoice_line_items WHERE invoice_line_item_id = ?",
                            (line["invoice_line_item_id"],),
                        )
                        result["sessions_removed_ineligible"] += 1
                        draft_changed = True
                        continue

                    session_month = _session_month(session["session_date"])
                    session_party = session["billing_party_id"]
                    is_wrong_party = session_party != party_id
                    is_wrong_month = session_month != billing_month

                    if is_wrong_party or is_wrong_month:
                        conn.execute(
                            "DELETE FROM invoice_line_items WHERE invoice_line_item_id = ?",
                            (line["invoice_line_item_id"],),
                        )
                        draft_changed = True

                        reasons = invoice_ineligibility_reasons(conn, session)
                        if reasons:
                            result["sessions_removed_ineligible"] += 1
                            result["sessions_skipped"].append({
                                "session_id": session["id"], "reasons": reasons,
                            })
                        else:
                            target_party = session_party or party_id
                            target_month = session_month or billing_month
                            target_draft, target_created = _find_or_create_monthly_draft(
                                conn, target_party, target_month,
                            )
                            if target_created:
                                result["drafts_created"] += 1

                            already = conn.execute(
                                "SELECT 1 FROM invoice_line_items WHERE invoice_id = ? AND source_session_id = ?",
                                (target_draft["invoice_id"], session["id"]),
                            ).fetchone()
                            if not already:
                                order = conn.execute(
                                    "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM invoice_line_items WHERE invoice_id = ?",
                                    (target_draft["invoice_id"],),
                                ).fetchone()[0]
                                _insert_line_item(conn, target_draft["invoice_id"], session, order)
                                _recalculate(conn, target_draft["invoice_id"])
                                conn.execute(
                                    "UPDATE invoices SET revision = revision + 1, updated_at = ? WHERE invoice_id = ?",
                                    (now_iso(), target_draft["invoice_id"]),
                                )
                            result["sessions_moved"] += 1
                    else:
                        reasons = invoice_ineligibility_reasons(conn, session, excluding_invoice_id=draft_id)
                        if reasons:
                            conn.execute(
                                "DELETE FROM invoice_line_items WHERE invoice_line_item_id = ?",
                                (line["invoice_line_item_id"],),
                            )
                            result["sessions_removed_ineligible"] += 1
                            result["sessions_skipped"].append({
                                "session_id": session["id"], "reasons": reasons,
                            })
                            draft_changed = True

            # --- Find eligible sessions for this (party, month) ---
            add_filter = "billing_party_id = ? AND session_date IS NOT NULL"
            add_params: list[Any] = [party_id]
            if session_ids is not None:
                placeholders = ", ".join("?" for _ in session_ids)
                add_filter += f" AND id IN ({placeholders})"
                add_params.extend(session_ids)

            month_sessions = conn.execute(
                f"""SELECT * FROM sessions WHERE {add_filter}
                ORDER BY session_date, start_at""",
                add_params,
            ).fetchall()

            # Filter to this month and check eligibility
            eligible_new: list[sqlite3.Row] = []
            for session in month_sessions:
                sm = _session_month(session["session_date"])
                if sm != billing_month:
                    continue
                if draft_id:
                    already_in = conn.execute(
                        "SELECT 1 FROM invoice_line_items WHERE invoice_id = ? AND source_session_id = ?",
                        (draft_id, session["id"]),
                    ).fetchone()
                    if already_in:
                        reasons = invoice_ineligibility_reasons(conn, session, excluding_invoice_id=draft_id)
                        if not reasons:
                            result["sessions_already_staged"] += 1
                        continue
                reasons = invoice_ineligibility_reasons(conn, session)
                if reasons:
                    result["sessions_skipped"].append({
                        "session_id": session["id"], "reasons": reasons,
                    })
                    continue
                eligible_new.append(session)

            # Only create a draft if there are eligible sessions to add
            if not existing_draft and eligible_new:
                party_row = conn.execute(
                    "SELECT * FROM billing_parties WHERE billing_party_id = ? AND active = 1",
                    (party_id,),
                ).fetchone()
                draft_row, _ = _find_or_create_monthly_draft(
                    conn, party_id, billing_month, party_row=party_row,
                )
                draft_id = draft_row["invoice_id"]
                draft_created = True
                result["drafts_created"] += 1
            elif existing_draft:
                result["drafts_reused"] += 1

            # --- Add eligible sessions to the draft ---
            if draft_id and eligible_new:
                for session in eligible_new:
                    order = conn.execute(
                        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM invoice_line_items WHERE invoice_id = ?",
                        (draft_id,),
                    ).fetchone()[0]
                    _insert_line_item(conn, draft_id, session, order)
                    result["sessions_staged"] += 1
                    draft_changed = True

            if draft_id and draft_changed:
                _recalculate(conn, draft_id)
                conn.execute(
                    "UPDATE invoices SET revision = revision + 1, updated_at = ? WHERE invoice_id = ?",
                    (now_iso(), draft_id),
                )
                _audit(conn, "invoice", draft_id, "staging_reconciled",
                       {"billing_month": billing_month})

            conn.commit()
        except Exception as error:
            conn.rollback()
            result["errors"].append({
                "billing_party_id": party_id,
                "billing_month": billing_month,
                "error": str(error),
            })
            continue

    return result


def validate_invoice_readiness(
    conn: sqlite3.Connection,
    invoice_id: str,
    *,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """One authoritative readiness check for invoice finalization.

    Returns {"ready": bool, "errors": list[dict], "preview_revision": int}.
    Each error dict has "field" and "message" keys suitable for UI display.
    Does not raise on validation failures; callers decide how to handle.
    """
    _draft(conn, invoice_id)
    result = get_invoice(conn, invoice_id)
    invoice = result["invoice"]
    lines = result["lines"]
    errors: list[dict[str, str]] = []

    # 1. Bill-to party
    party = conn.execute(
        "SELECT * FROM billing_parties WHERE billing_party_id = ?", (invoice["bill_to_party_id"],)
    ).fetchone()
    if not party:
        errors.append({"field": "bill_to", "message": "Bill-to party is missing or not found."})
    elif not party["active"]:
        errors.append({"field": "bill_to", "message": "Bill-to party is no longer active."})

    # 2. At least one eligible invoice line
    if not lines:
        errors.append({"field": "lines", "message": "Add at least one eligible session before finalizing."})

    # 3. Valid positive line amounts
    for line in lines:
        amount = line.get("line_amount_cents")
        if amount is None or int(amount) <= 0:
            errors.append({
                "field": "line_amount",
                "message": f"Line for {line['service_date']} has an invalid or non-positive amount.",
            })

    # 4. Valid invoice date
    inv_date = invoice.get("invoice_date")
    if not inv_date or not str(inv_date).strip():
        errors.append({"field": "invoice_date", "message": "Invoice date is missing."})
    else:
        try:
            date.fromisoformat(str(inv_date)[:10])
        except (ValueError, TypeError):
            errors.append({"field": "invoice_date", "message": "Invoice date is not a valid date."})

    # 5. Active business profile
    profile = conn.execute("SELECT * FROM business_profile WHERE active = 1 LIMIT 1").fetchone()
    if not profile:
        errors.append({"field": "business_profile", "message": "Configure an active business profile before finalizing."})

    # 6. Required bill-to contact details for the selected delivery method
    delivery = invoice.get("delivery_method") or "unresolved"
    if party:
        if delivery in ("email", "both"):
            if not (party["billing_email"] or "").strip():
                errors.append({
                    "field": "delivery_email",
                    "message": f"Billing party email is required for {delivery} delivery.",
                })
        if delivery in ("mail", "both"):
            if not (party["billing_address_line_1"] or "").strip():
                errors.append({
                    "field": "delivery_address",
                    "message": f"Billing party mailing address is required for {delivery} delivery.",
                })

    # 7. Required business / payee / payment-address details used on the invoice
    if profile:
        if not (profile["business_name"] or "").strip():
            errors.append({"field": "business_name", "message": "Business name is required on the invoice."})
        if not (profile["payee_name"] or "").strip():
            errors.append({"field": "payee_name", "message": "Payee name is required on the invoice."})
        if not (profile["payment_address_line_1"] or "").strip():
            errors.append({"field": "payment_address", "message": "Payment address is required on the invoice."})

    # 8. Valid, unique invoice number generation
    if profile and inv_date:
        try:
            year = int(str(inv_date)[:4])
            pattern = profile["invoice_number_format"] or "YYYY-NNNN"
            if "YYYY" not in pattern or "NNNN" not in pattern:
                errors.append({"field": "invoice_number", "message": "Invoice number format is invalid."})
            else:
                seq_row = conn.execute(
                    "SELECT last_value FROM invoice_sequences WHERE sequence_year = ?", (year,)
                ).fetchone()
                next_val = (seq_row["last_value"] + 1) if seq_row else 1
                candidate_number = pattern.replace("YYYY", str(year)).replace("NNNN", f"{next_val:04d}")
                existing = conn.execute(
                    "SELECT 1 FROM invoices WHERE invoice_number = ? AND invoice_id != ?",
                    (candidate_number, invoice_id),
                ).fetchone()
                if existing:
                    errors.append({"field": "invoice_number", "message": "Generated invoice number conflicts with an existing invoice."})
        except (ValueError, TypeError):
            errors.append({"field": "invoice_number", "message": "Cannot generate a valid invoice number."})

    # 9. Any included session is no longer invoice-eligible
    for line in lines:
        session = conn.execute("SELECT * FROM sessions WHERE id = ?", (line["source_session_id"],)).fetchone()
        if not session:
            errors.append({"field": "session", "message": f"Source session for {line['service_date']} is missing."})
        else:
            reasons = invoice_ineligibility_reasons(conn, session, excluding_invoice_id=invoice_id)
            if reasons:
                errors.append({
                    "field": "session",
                    "message": f"Session on {session['session_date']} is no longer eligible: {'; '.join(reasons)}",
                })
            elif session["session_date"] < invoice["billing_period_start"] or session["session_date"] > invoice["billing_period_end"]:
                errors.append({
                    "field": "session",
                    "message": f"Session on {session['session_date']} is outside the billing period.",
                })

    # 10. Preview revision is stale
    if expected_revision is not None and invoice["revision"] != expected_revision:
        errors.append({"field": "revision", "message": "Invoice has changed since preview. Please review and try again."})

    return {
        "ready": not errors,
        "errors": errors,
        "preview_revision": invoice["revision"],
    }


def preview_finalization(conn: sqlite3.Connection, invoice_id: str, *, data: dict[str, Any] | None = None) -> dict[str, Any]:
    _draft(conn, invoice_id)
    if data:
        update_invoice_draft(conn, invoice_id, data)
    result = get_invoice(conn, invoice_id)
    invoice = result["invoice"]
    lines = result["lines"]
    readiness = validate_invoice_readiness(conn, invoice_id)
    profile = conn.execute("SELECT * FROM business_profile WHERE active = 1 LIMIT 1").fetchone()
    party = conn.execute("SELECT * FROM billing_parties WHERE billing_party_id = ?", (invoice["bill_to_party_id"],)).fetchone()
    return {
        "invoice": dict(invoice),
        "lines": [dict(line) for line in lines],
        "business_profile": dict(profile) if profile else None,
        "billing_party": dict(party) if party else None,
        "preview_revision": invoice["revision"],
        "readiness": readiness,
    }


def finalize_invoice(conn: sqlite3.Connection, invoice_id: str, *, expected_revision: int | None = None, pdf_root: str | Path | None = None) -> dict[str, Any]:
    _draft(conn, invoice_id)
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as error:
        if "locked" in str(error).lower():
            raise DatabaseBusyError(
                "Cannot finalize invoice: database is locked by another operation. "
                "Please retry in a moment."
            ) from error
        raise
    pdf_path: Path | None = None
    try:
        readiness = validate_invoice_readiness(conn, invoice_id, expected_revision=expected_revision)
        if not readiness["ready"]:
            raise ValueError("; ".join(e["message"] for e in readiness["errors"]))
        invoice = conn.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)).fetchone()
        lines = conn.execute("SELECT * FROM invoice_line_items WHERE invoice_id = ? ORDER BY sort_order", (invoice_id,)).fetchall()
        profile = conn.execute("SELECT * FROM business_profile WHERE active = 1 LIMIT 1").fetchone()
        party = conn.execute("SELECT * FROM billing_parties WHERE billing_party_id = ?", (invoice["bill_to_party_id"],)).fetchone()
        number = _next_invoice_number(conn, int(str(invoice["invoice_date"])[:4]), profile["invoice_number_format"])
        now = now_iso()
        snapshots = {
            "invoice_number": number,
            "bill_to_name_snapshot": party["billing_name"],
            "bill_to_email_snapshot": party["billing_email"],
            "bill_to_phone_snapshot": party["billing_phone"],
            "bill_to_address_snapshot": _address(party, "billing_"),
            "business_name_snapshot": profile["business_name"],
            "provider_name_snapshot": profile["provider_display_name"],
            "credentials_snapshot": profile["credentials_display"],
            "business_address_snapshot": _address(profile),
            "business_phone_snapshot": profile["phone"],
            "business_email_snapshot": profile["email"],
            "payee_name_snapshot": profile["payee_name"],
            "payment_address_snapshot": _address(profile, "payment_", include_name=profile["payee_name"]),
            "logo_reference_snapshot": profile["logo_path"],
            "logo_contains_business_details_snapshot": profile["logo_contains_business_details"],
            "show_email_below_logo_snapshot": profile["show_email_below_logo"],
            "total_label_snapshot": profile["invoice_total_label"],
            "number_format_snapshot": profile["invoice_number_format"],
            "status": "finalized", "finalized_at": now, "updated_at": now,
        }
        conn.execute(f"UPDATE invoices SET {', '.join(f'{k} = ?' for k in snapshots)} WHERE invoice_id = ?", (*snapshots.values(), invoice_id))
        _recalculate(conn, invoice_id)
        frozen = get_invoice(conn, invoice_id)
        root = Path(pdf_root or os.getenv("JORDANA_INVOICES_DIR", "Invoices"))
        pdf_path = root / str(invoice["invoice_date"])[:4] / f"Invoice_{number}.pdf"
        checksum = generate_invoice_pdf(frozen["invoice"], frozen["lines"], pdf_path)
        conn.execute("UPDATE invoices SET pdf_path = ?, pdf_sha256 = ?, updated_at = ? WHERE invoice_id = ?", (str(pdf_path), checksum, now_iso(), invoice_id))
        _audit(conn, "invoice", invoice_id, "finalized", {"invoice_number": number, "pdf_sha256": checksum})
        conn.commit()
    except Exception:
        conn.rollback()
        if pdf_path and pdf_path.exists(): pdf_path.unlink()
        raise
    return get_invoice(conn, invoice_id)


def void_invoice(conn: sqlite3.Connection, invoice_id: str, reason: str) -> dict[str, Any]:
    if not reason.strip(): raise ValueError("A void reason is required.")
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as error:
        if "locked" in str(error).lower():
            raise DatabaseBusyError(
                "Cannot void invoice: database is locked by another operation. "
                "Please retry in a moment."
            ) from error
        raise
    try:
        row = conn.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)).fetchone()
        if not row or row["status"] != "finalized": raise ValueError("Only a finalized invoice can be voided.")
        now = now_iso()
        conn.execute("UPDATE invoices SET status = 'void', void_reason = ?, voided_at = ?, updated_at = ? WHERE invoice_id = ?", (reason.strip(), now, now, invoice_id))
        _audit(conn, "invoice", invoice_id, "voided", {"reason": reason.strip(), "invoice_number": row["invoice_number"]})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_invoice(conn, invoice_id)


def _draft(conn: sqlite3.Connection, invoice_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)).fetchone()
    if not row or row["status"] != "draft": raise ValueError("Only a draft invoice can be changed.")
    return row


def _recalculate(conn: sqlite3.Connection, invoice_id: str) -> None:
    subtotal = conn.execute("SELECT COALESCE(SUM(line_amount_cents), 0) FROM invoice_line_items WHERE invoice_id = ?", (invoice_id,)).fetchone()[0]
    adjustment = conn.execute("SELECT adjustment_cents FROM invoices WHERE invoice_id = ?", (invoice_id,)).fetchone()[0]
    conn.execute("UPDATE invoices SET subtotal_cents = ?, total_cents = ?, updated_at = ? WHERE invoice_id = ?", (subtotal, subtotal + adjustment, now_iso(), invoice_id))


def _next_invoice_number(conn: sqlite3.Connection, year: int, pattern: str) -> str:
    now = now_iso()
    conn.execute("INSERT INTO invoice_sequences (sequence_year, last_value, updated_at) VALUES (?, 0, ?) ON CONFLICT(sequence_year) DO NOTHING", (year, now))
    conn.execute("UPDATE invoice_sequences SET last_value = last_value + 1, updated_at = ? WHERE sequence_year = ?", (now, year))
    value = conn.execute("SELECT last_value FROM invoice_sequences WHERE sequence_year = ?", (year,)).fetchone()[0]
    return pattern.replace("YYYY", str(year)).replace("NNNN", f"{value:04d}")


def _participant_names(conn: sqlite3.Connection, session_id: str) -> str:
    rows = conn.execute("""SELECT COALESCE(p.display_name, sp.participant_name) AS name FROM session_participants sp LEFT JOIN people p ON p.person_id = sp.person_id WHERE sp.session_id = ? ORDER BY sp.created_at""", (session_id,)).fetchall()
    return " & ".join(row["name"] for row in rows if row["name"])


def _service_description(session: sqlite3.Row, service_name: str) -> str:
    billing_type = session["billing_session_type"] if "billing_session_type" in session.keys() else None
    custom_desc = session["custom_service_description"] if "custom_service_description" in session.keys() else None
    appointment_status = session["appointment_status"] if "appointment_status" in session.keys() else None

    if billing_type == "custom" and custom_desc:
        return get_user_facing_session_label(billing_type, appointment_status, custom_desc)
    if billing_type:
        return get_user_facing_session_label(billing_type, appointment_status, custom_desc)

    category = session["time_category"]
    suffix = {"evening": "Evening", "weekend": "Weekend", "weekend_evening": "Weekend Evening"}.get(category)
    return f"{service_name} - {suffix}" if suffix else service_name


def _address(row: sqlite3.Row, prefix: str = "", include_name: str | None = None) -> str:
    values = []
    if include_name: values.append(include_name)
    line1 = row[f"{prefix}address_line_1"]
    line2 = row[f"{prefix}address_line_2"]
    city = row[f"{prefix}city"]
    state = row[f"{prefix}state"]
    postal = row[f"{prefix}postal_code"]
    if line1: values.append(line1)
    if line2: values.append(line2)
    locality = ", ".join(filter(None, [city, state]))
    if postal: locality = f"{locality} {postal}".strip()
    if locality: values.append(locality)
    return "\n".join(values)


def _audit(conn: sqlite3.Connection, entity_type: str, entity_id: str, action: str, details: dict[str, Any]) -> None:
    conn.execute("INSERT INTO audit_log (id, entity_type, entity_id, action, details, created_at) VALUES (?, ?, ?, ?, ?, ?)", (new_id(), entity_type, entity_id, action, json_dumps(details), now_iso()))


def update_invoice_line_item(
    conn: sqlite3.Connection,
    invoice_id: str,
    *,
    line_id: str,
    description: str,
    amount_cents: int,
    amount_scope: str,
    reason: str,
    expected_revision: int,
) -> dict[str, Any]:
    _draft(conn, invoice_id)
    conn.execute("BEGIN IMMEDIATE")
    try:
        invoice = conn.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)).fetchone()
        if not invoice:
            raise ValueError("Invoice was not found.")
        if invoice["status"] != "draft":
            raise ValueError("Only a draft invoice can be changed.")
        if invoice["revision"] != expected_revision:
            raise ValueError("Invoice has changed. Please reload and try again.")

        line = conn.execute("SELECT * FROM invoice_line_items WHERE invoice_line_item_id = ?", (line_id,)).fetchone()
        if not line:
            raise ValueError("Invoice line was not found.")
        if line["invoice_id"] != invoice_id:
            raise ValueError("Line item does not belong to this invoice.")

        description = (description or "").strip()
        if not description:
            raise ValueError("Description must be non-empty.")

        if not isinstance(amount_cents, int) or amount_cents < 0:
            raise ValueError("Amount must be non-negative.")

        old_description = line["description_snapshot"]
        old_amount_cents = line["line_amount_cents"]
        amount_changed = (amount_cents != old_amount_cents)

        if amount_changed:
            if not reason or not reason.strip():
                raise ValueError("A correction reason is required when the amount changes.")
            if amount_scope not in ("invoice_line_only", "invoice_line_and_session"):
                raise ValueError("Invalid amount scope.")

        session_id = line["source_session_id"]
        if amount_changed and amount_scope == "invoice_line_and_session":
            if not session_id:
                raise ValueError("Session-update scope is only available for lines linked to a session.")

        now = now_iso()
        # Update the line item
        conn.execute(
            """UPDATE invoice_line_items
               SET description_snapshot = ?, unit_amount_cents = ?, line_amount_cents = ?, updated_at = ?
               WHERE invoice_line_item_id = ? AND invoice_id = ?""",
            (description, amount_cents, amount_cents, now, line_id, invoice_id)
        )

        # Update backing session if applicable
        if amount_changed and amount_scope == "invoice_line_and_session" and session_id:
            conn.execute(
                """UPDATE sessions
                   SET approved_rate_cents = ?, rate_cents_snapshot = ?
                   WHERE id = ?""",
                (amount_cents, amount_cents, session_id)
            )

        # Recalculate totals
        _recalculate(conn, invoice_id)

        # Increment revision
        conn.execute(
            "UPDATE invoices SET revision = revision + 1, updated_at = ? WHERE invoice_id = ?",
            (now, invoice_id)
        )

        # Log correction record
        if amount_changed:
            correction_id = new_id()
            conn.execute(
                """INSERT INTO invoice_line_item_corrections (
                    correction_id, invoice_id, invoice_line_item_id, source_session_id,
                    old_description, new_description, old_amount_cents, new_amount_cents,
                    correction_scope, reason, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (correction_id, invoice_id, line_id, session_id,
                 old_description, description, old_amount_cents, amount_cents,
                 amount_scope, reason or "", now)
            )

        # Audit
        _audit(conn, "invoice_line_item", line_id, "line_item_corrected", {
            "invoice_id": invoice_id,
            "old_description": old_description,
            "new_description": description,
            "old_amount_cents": old_amount_cents,
            "new_amount_cents": amount_cents,
            "correction_scope": amount_scope,
            "reason": reason
        })

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_invoice(conn, invoice_id)

