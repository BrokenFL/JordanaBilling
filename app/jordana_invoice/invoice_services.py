from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .db import init_db
from .invoice_pdf import generate_invoice_pdf
from .service_catalog import learn_service, list_services
from .util import json_dumps, new_id, now_iso


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


def create_invoice_draft(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    init_db(conn)
    billing_party_id = str(data.get("bill_to_party_id") or "")
    party = conn.execute("SELECT * FROM billing_parties WHERE billing_party_id = ? AND active = 1", (billing_party_id,)).fetchone()
    if not party: raise ValueError("Select an active bill-to party.")
    start, end = str(data.get("billing_period_start") or ""), str(data.get("billing_period_end") or "")
    if not start or not end or start > end: raise ValueError("A valid billing period is required.")
    method = str(data.get("delivery_method") or party["preferred_delivery_method"] or "unresolved")
    if method not in DELIVERY_METHODS: raise ValueError("Invalid delivery method.")
    invoice_id, now = new_id(), now_iso()
    conn.execute(
        """INSERT INTO invoices (
          invoice_id, status, bill_to_party_id, billing_period_start, billing_period_end,
          invoice_date, delivery_method, notes, created_at, updated_at
        ) VALUES (?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?)""",
        (invoice_id, billing_party_id, start, end, data.get("invoice_date") or date.today().isoformat(), method, data.get("notes"), now, now),
    )
    _audit(conn, "invoice", invoice_id, "draft_created", {"bill_to_party_id": billing_party_id})
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
            catalog = learn_service(conn, session["service_mode"] or "Other")
            participants = _participant_names(conn, session_id)
            service_name = catalog["display_name"]
            description = _service_description(session, service_name)
            amount = session["rate_cents_snapshot"] if session["rate_cents_snapshot"] is not None else session["approved_rate_cents"]
            now = now_iso()
            conn.execute(
                """INSERT INTO invoice_line_items (
                  invoice_line_item_id, invoice_id, source_session_id, sort_order, service_date,
                  participants_snapshot, service_catalog_id, service_name_snapshot, time_category_snapshot,
                  appointment_status_snapshot, duration_minutes, description_snapshot, quantity,
                  unit_amount_cents, line_amount_cents, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)""",
                (new_id(), invoice_id, session_id, order, session["session_date"], participants,
                 catalog["service_catalog_id"], service_name, session["time_category"], session["appointment_status"],
                 session["approved_duration_minutes"] or session["duration_minutes"], description, amount, amount, now, now),
            )
            order += 1
        _recalculate(conn, invoice_id)
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
        _audit(conn, "invoice", invoice_id, "line_removed", {"invoice_line_item_id": line_id})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_invoice(conn, invoice_id)


def finalize_invoice(conn: sqlite3.Connection, invoice_id: str, *, pdf_root: str | Path | None = None) -> dict[str, Any]:
    _draft(conn, invoice_id)
    conn.execute("BEGIN IMMEDIATE")
    pdf_path: Path | None = None
    try:
        invoice = conn.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)).fetchone()
        lines = conn.execute("SELECT * FROM invoice_line_items WHERE invoice_id = ? ORDER BY sort_order", (invoice_id,)).fetchall()
        if not lines: raise ValueError("Add at least one eligible session before finalizing.")
        for line in lines:
            session = conn.execute("SELECT * FROM sessions WHERE id = ?", (line["source_session_id"],)).fetchone()
            if not session: raise ValueError("A source session is missing.")
            reasons = invoice_ineligibility_reasons(conn, session, excluding_invoice_id=invoice_id)
            if reasons: raise ValueError("A source session is no longer eligible: " + "; ".join(reasons))
            if session["session_date"] < invoice["billing_period_start"] or session["session_date"] > invoice["billing_period_end"]:
                raise ValueError("A source session is outside the current billing period.")
        profile = conn.execute("SELECT * FROM business_profile WHERE active = 1 LIMIT 1").fetchone()
        if not profile: raise ValueError("Configure an active business profile before finalizing.")
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
    conn.execute("BEGIN IMMEDIATE")
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
    if session["appointment_status"] == "no_show": return f"No Show - {service_name}"
    if session["appointment_status"] == "cancelled": return f"Cancelled Session - {service_name}"
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
