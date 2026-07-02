from __future__ import annotations

import os
import re
import subprocess
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .invoice_rendering import build_invoice_render_model, resolve_logo_path
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


def _present_text(value: Any) -> str:
    return str(value or "").strip()


def _billing_address_complete(party: sqlite3.Row | dict[str, Any] | None) -> bool:
    if not party:
        return False
    return all(
        _present_text(party.get(field) if isinstance(party, dict) else party[field])
        for field in ("billing_address_line_1", "billing_city", "billing_state", "billing_postal_code")
    )


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
        "payment_address_line_2", "payment_city", "payment_state", "payment_postal_code", "zelle_recipient", "logo_path",
        "logo_contains_business_details", "show_email_below_logo", "invoice_total_label", "invoice_number_format",
        "insurance_ein", "insurance_npi", "insurance_sw",
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


def _invoice_paid_cents(conn: sqlite3.Connection, invoice_id: str) -> int:
    return conn.execute(
        """
        SELECT COALESCE(SUM(pa.amount_cents), 0)
        FROM payment_allocations pa
        JOIN payments p ON p.payment_id = pa.payment_id
        JOIN invoice_line_items li ON li.invoice_line_item_id = pa.invoice_line_item_id
        WHERE li.invoice_id = ? AND pa.status = 'active' AND p.status = 'posted'
        """,
        (invoice_id,),
    ).fetchone()[0]


def calculate_invoice_account_summary(conn: sqlite3.Connection, invoice_id: str) -> dict[str, Any]:
    """Calculate the account summary values for a given invoice.

    This computes:
      - current_invoice_total_cents (Current Charges)
      - current_invoice_paid_cents (Payments Applied to Current Invoice)
      - current_invoice_balance_cents (Current Invoice Balance)
      - prior_unpaid_balance_cents (Prior Unpaid Balance)
      - total_amount_due_cents (TOTAL AMOUNT DUE)
      - prior_invoices (List of prior unpaid invoices: id, number, date, remaining_balance_cents)
    """
    invoice_row = conn.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)).fetchone()
    if not invoice_row:
        raise ValueError("Invoice was not found.")
    invoice = dict(invoice_row)
    status = invoice["status"]

    current_total = int(invoice["total_cents"] or 0)
    current_paid = _invoice_paid_cents(conn, invoice_id)
    current_balance = max(current_total - current_paid, 0)

    if status == "void":
        current_balance = 0

    # Retrieve the billing party to determine the billing responsibility
    party_row = conn.execute("SELECT * FROM billing_parties WHERE billing_party_id = ?", (invoice["bill_to_party_id"],)).fetchone()
    if not party_row:
        return {
            "version": 1,
            "current_invoice_total_cents": current_total,
            "current_invoice_paid_cents": current_paid,
            "current_invoice_balance_cents": current_balance,
            "prior_unpaid_balance_cents": 0,
            "total_amount_due_cents": current_balance,
            "prior_invoices": [],
        }
    party = dict(party_row)

    # Query potential candidate invoices for prior balance (finalized and non-void)
    if party["billing_party_type"] == "person" and party["person_id"]:
        # Person-linked: match any invoice addressed to any billing party for the same person
        candidates_rows = conn.execute(
            """
            SELECT DISTINCT i.*
            FROM invoices i
            JOIN billing_parties bp ON bp.billing_party_id = i.bill_to_party_id
            WHERE bp.person_id = ? AND bp.billing_party_type = 'person'
              AND i.status = 'finalized'
            """,
            (party["person_id"],),
        ).fetchall()
    else:
        # Organization-linked: match only invoices with the exact same billing_party_id
        candidates_rows = conn.execute(
            """
            SELECT i.*
            FROM invoices i
            WHERE i.bill_to_party_id = ?
              AND i.status = 'finalized'
            """,
            (invoice["bill_to_party_id"],),
        ).fetchall()

    prior_invoices = []
    prior_unpaid_cents = 0

    current_date = invoice["invoice_date"]
    current_finalized_at = invoice.get("finalized_at")

    for row in candidates_rows:
        cand = dict(row)
        cand_id = cand["invoice_id"]

        # 1. Skip the current invoice itself
        if cand_id == invoice_id:
            continue

        # 2. Check if the candidate is "prior" based on the cutoff rule
        cand_date = cand["invoice_date"]
        cand_finalized_at = cand.get("finalized_at")

        is_prior = False
        if cand_date < current_date:
            is_prior = True
        elif cand_date == current_date:
            # Same date cutoff ordering:
            if cand_finalized_at and not current_finalized_at:
                # Candidate is finalized, current is draft
                is_prior = True
            elif cand_finalized_at and current_finalized_at:
                # Both are finalized
                if cand_finalized_at < current_finalized_at:
                    is_prior = True
                elif cand_finalized_at == current_finalized_at:
                    # Stable tie-breaker using UUID comparison
                    is_prior = cand_id < invoice_id

        if not is_prior:
            continue

        # Calculate dynamic remaining balance for the candidate
        paid = _invoice_paid_cents(conn, cand_id)
        total = int(cand["total_cents"] or 0)
        remaining = max(total - paid, 0)

        if remaining > 0:
            prior_invoices.append({
                "invoice_id": cand_id,
                "invoice_number": cand["invoice_number"],
                "invoice_date": cand_date,
                "remaining_balance_cents": remaining,
                "_sort_key": (cand_date, cand_finalized_at or "", cand_id)
            })
            prior_unpaid_cents += remaining

    # Sort prior invoices: oldest first
    prior_invoices.sort(key=lambda x: x["_sort_key"])
    for item in prior_invoices:
        del item["_sort_key"]

    total_amount_due = current_balance + prior_unpaid_cents

    return {
        "version": 1,
        "current_invoice_total_cents": current_total,
        "current_invoice_paid_cents": current_paid,
        "current_invoice_balance_cents": current_balance,
        "prior_unpaid_balance_cents": prior_unpaid_cents,
        "total_amount_due_cents": total_amount_due,
        "prior_invoices": prior_invoices,
    }


def _derive_payment_status(status: str, paid_cents: int, total_cents: int) -> str:
    if status == "void":
        return "void"
    balance = max(total_cents - paid_cents, 0)
    if balance == 0:
        return "paid"
    if paid_cents == 0:
        return "unpaid"
    return "partially_paid"


_VALID_SORT_FIELDS = {
    "invoice_date": "i.invoice_date",
    "invoice_number": "i.invoice_number",
    "total_cents": "i.total_cents",
    "created_at": "i.created_at",
    "bill_to_name": "bp.billing_name",
}


def _sanitize_path_part(value: Any, fallback: str = "Unknown") -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", str(value or "").strip())
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text or fallback


def _month_folder_label(invoice: sqlite3.Row | dict[str, Any]) -> str:
    billing_month = str(invoice["billing_month"] if isinstance(invoice, sqlite3.Row) else invoice.get("billing_month") or "").strip()
    if billing_month:
        try:
            parsed = date(int(billing_month[:4]), int(billing_month[5:7]), 1)
            return parsed.strftime("%B %Y")
        except (TypeError, ValueError):
            pass
    period_start = str(invoice["billing_period_start"] if isinstance(invoice, sqlite3.Row) else invoice.get("billing_period_start") or "").strip()
    try:
        parsed = date.fromisoformat(period_start[:10])
    except (TypeError, ValueError):
        return _sanitize_path_part(billing_month or period_start, "Unknown Month")
    return parsed.strftime("%B %Y")


def _existing_filing_folder_for_person(conn: sqlite3.Connection, root: Path, person_id: str) -> Path | None:
    rows = conn.execute(
        """
        SELECT pdf_path
        FROM invoices
        WHERE status IN ('finalized', 'void')
          AND filing_owner_person_id = ?
          AND pdf_path IS NOT NULL
        ORDER BY finalized_at DESC, updated_at DESC
        """,
        (person_id,),
    ).fetchall()
    for row in rows:
        try:
            resolved = Path(row["pdf_path"]).expanduser().resolve(strict=False)
            rel = resolved.relative_to(root.resolve(strict=False))
        except (OSError, ValueError):
            continue
        if len(rel.parts) >= 3 and not re.fullmatch(r"\d{4}", rel.parts[1]):
            return root / rel.parts[0]
    return None


def _plain_filing_folder_owned_by_different_person(conn: sqlite3.Connection, folder_name: str, person_id: str) -> bool:
    rows = conn.execute(
        """
        SELECT DISTINCT filing_owner_person_id, filing_owner_display_name_snapshot
        FROM invoices
        WHERE status IN ('finalized', 'void')
          AND filing_owner_person_id IS NOT NULL
          AND filing_owner_person_id != ?
          AND filing_owner_display_name_snapshot IS NOT NULL
        """,
        (person_id,),
    ).fetchall()
    return any(_sanitize_path_part(row["filing_owner_display_name_snapshot"]) == folder_name for row in rows)


def _filing_owner_folder(conn: sqlite3.Connection, root: Path, filing_owner: dict[str, Any]) -> Path:
    person_id = str(filing_owner.get("person_id") or "")
    display_name = _sanitize_path_part(filing_owner.get("display_name"), person_id or "Unknown Client")
    person_code = _sanitize_path_part(filing_owner.get("person_code"), person_id or "Unknown")
    existing = _existing_filing_folder_for_person(conn, root, person_id)
    if existing:
        return existing
    plain = root / display_name
    if _plain_filing_folder_owned_by_different_person(conn, display_name, person_id):
        return root / f"{display_name} [{person_code}]"
    if plain.exists():
        return root / f"{display_name} [{person_code}]"
    return plain


def _client_invoice_folder_for_pdf(root: Path, resolved_pdf: Path) -> Path:
    try:
        rel = resolved_pdf.relative_to(root)
    except ValueError:
        return resolved_pdf.parent
    if len(rel.parts) >= 3:
        return root / rel.parts[0]
    return resolved_pdf.parent


def _eligible_filing_clients(conn: sqlite3.Connection, invoice_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT DISTINCT p.person_id, p.display_name, p.person_code
        FROM invoice_line_items li
        JOIN session_participants sp ON sp.session_id = li.source_session_id
        JOIN people p ON p.person_id = sp.person_id
        WHERE li.invoice_id = ? AND p.active = 1
        ORDER BY p.display_name, p.person_id
        """,
        (invoice_id,),
    ).fetchall()
    clients = {row["person_id"]: dict(row) for row in rows}
    payer = conn.execute(
        """
        SELECT p.person_id, p.display_name, p.person_code
        FROM invoices i
        JOIN billing_parties bp ON bp.billing_party_id = i.bill_to_party_id
        JOIN people p ON p.person_id = bp.person_id
        WHERE i.invoice_id = ? AND p.active = 1
        """,
        (invoice_id,),
    ).fetchone()
    if payer:
        clients[payer["person_id"]] = dict(payer)
    return sorted(clients.values(), key=lambda row: (row.get("display_name") or "", row.get("person_id") or ""))


def _relationship_defaults_for_invoice(conn: sqlite3.Connection, invoice: dict[str, Any]) -> list[sqlite3.Row]:
    eligible_ids = {client["person_id"] for client in _eligible_filing_clients(conn, invoice["invoice_id"])}
    if not eligible_ids:
        return []
    rows = conn.execute(
        """
        SELECT ca.*
        FROM client_accounts ca
        WHERE ca.active = 1 AND ca.default_billing_party_id = ?
        ORDER BY ca.updated_at DESC, ca.created_at DESC
        """,
        (invoice["bill_to_party_id"],),
    ).fetchall()
    matches = []
    for row in rows:
        member_ids = {
            member["person_id"]
            for member in conn.execute(
                "SELECT person_id FROM account_members WHERE account_id = ?",
                (row["account_id"],),
            ).fetchall()
        }
        if eligible_ids.issubset(member_ids):
            matches.append(row)
    return matches


def resolve_invoice_filing_owner(conn: sqlite3.Connection, invoice_id: str) -> dict[str, Any]:
    invoice_row = conn.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)).fetchone()
    if not invoice_row:
        raise ValueError("Invoice was not found.")
    invoice = dict(invoice_row)
    eligible = _eligible_filing_clients(conn, invoice_id)
    eligible_ids = {client["person_id"] for client in eligible}
    stored_id = invoice.get("filing_owner_person_id")
    party = conn.execute(
        "SELECT * FROM billing_parties WHERE billing_party_id = ?",
        (invoice["bill_to_party_id"],),
    ).fetchone()
    selected = None
    source = "unresolved"
    message = ""

    if stored_id:
        selected = next((client for client in eligible if client["person_id"] == stored_id), None)
        if selected:
            source = "invoice_selection"
        elif invoice["status"] in ("finalized", "void"):
            selected = {
                "person_id": stored_id,
                "person_code": invoice.get("filing_owner_person_code_snapshot"),
                "display_name": invoice.get("filing_owner_display_name_snapshot"),
            }
            source = "finalized_snapshot"
        else:
            message = "Selected filing client is no longer eligible for this draft."

    if not selected and party and party["person_id"] and party["person_id"] in eligible_ids:
        selected = next(client for client in eligible if client["person_id"] == party["person_id"])
        source = "bill_to_client"

    relationships = _relationship_defaults_for_invoice(conn, invoice)
    if not selected:
        default_ids = []
        for relationship in relationships:
            default_id = relationship["default_filing_owner_person_id"]
            if default_id and default_id in eligible_ids:
                default_ids.append(default_id)
        unique_defaults = sorted(set(default_ids))
        if len(unique_defaults) == 1:
            selected = next(client for client in eligible if client["person_id"] == unique_defaults[0])
            source = "relationship_default"
        elif len(eligible) == 1:
            selected = eligible[0]
            source = "single_eligible_client"

    if not selected and len(eligible) > 1:
        message = "Choose which covered client this invoice should be filed under."
    elif not selected and not eligible:
        message = "Add at least one eligible client before choosing where to file this invoice."

    return {
        "selected": selected,
        "eligible_clients": eligible,
        "source": source,
        "required": invoice["status"] == "draft",
        "message": message,
    }


def update_invoice_filing_owner(conn: sqlite3.Connection, invoice_id: str, person_id: str | None) -> dict[str, Any]:
    _draft(conn, invoice_id)
    resolution = resolve_invoice_filing_owner(conn, invoice_id)
    eligible_ids = {client["person_id"] for client in resolution["eligible_clients"]}
    chosen = str(person_id or "").strip()
    if chosen and chosen not in eligible_ids:
        raise ValueError("File invoice under must be one of the eligible covered clients.")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "UPDATE invoices SET filing_owner_person_id = ?, revision = revision + 1, updated_at = ? WHERE invoice_id = ?",
            (chosen or None, now_iso(), invoice_id),
        )
        _audit(conn, "invoice", invoice_id, "filing_owner_selected", {"filing_owner_person_id": chosen or None})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_invoice(conn, invoice_id)


def trusted_invoice_document_action(
    conn: sqlite3.Connection,
    invoice_id: str,
    action: str,
    *,
    pdf_root: str | Path | None = None,
) -> dict[str, Any]:
    if action not in {"open_pdf", "show_in_finder", "open_client_folder"}:
        raise ValueError("Unsupported invoice document action.")
    row = conn.execute(
        "SELECT status, pdf_path, filing_owner_person_id FROM invoices WHERE invoice_id = ?",
        (invoice_id,),
    ).fetchone()
    if not row:
        raise ValueError("Invoice was not found.")
    if row["status"] not in ("finalized", "void"):
        raise ValueError("Only finalized or void invoices have stored document actions.")
    pdf_text = str(row["pdf_path"] or "").strip()
    if not pdf_text:
        raise ValueError("No PDF file is stored for this invoice.")
    pdf_path = Path(pdf_text).expanduser()
    root = Path(pdf_root or os.getenv("JORDANA_INVOICES_DIR", "Invoices")).expanduser()
    try:
        resolved_pdf = pdf_path.resolve(strict=False)
        resolved_root = root.resolve(strict=False)
        resolved_pdf.relative_to(resolved_root)
    except (OSError, ValueError):
        raise ValueError("Stored invoice path is outside the configured invoice folder.")
    if not resolved_pdf.is_file():
        raise ValueError("The PDF file for this invoice is missing from the expected location.")
    args = ["open", str(resolved_pdf)]
    if action == "show_in_finder":
        args = ["open", "-R", str(resolved_pdf)]
    elif action == "open_client_folder":
        target = _client_invoice_folder_for_pdf(resolved_root, resolved_pdf)
        if not target.is_dir():
            raise ValueError("The client invoice folder is missing.")
        args = ["open", str(target)]
    subprocess.run(args, check=True)
    return {"ok": True, "action": action}


def list_invoice_records(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    search: str | None = None,
    bill_to_party_id: str | None = None,
    participant_person_id: str | None = None,
    payment_status: str | None = None,
    invoice_date_from: str | None = None,
    invoice_date_to: str | None = None,
    billing_month: str | None = None,
    service_period_from: str | None = None,
    service_period_to: str | None = None,
    sort_by: str = "invoice_date",
    sort_dir: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    init_db(conn)
    conditions: list[str] = []
    params: list[Any] = []

    if status in INVOICE_STATUSES:
        conditions.append("i.status = ?")
        params.append(status)

    search_text = (search or "").strip()
    if search_text:
        conditions.append("(i.invoice_number LIKE ? OR bp.billing_name LIKE ?)")
        params.extend([f"%{search_text}%", f"%{search_text}%"])

    if bill_to_party_id:
        conditions.append("i.bill_to_party_id = ?")
        params.append(bill_to_party_id)

    if participant_person_id:
        conditions.append(
            "i.invoice_id IN ("
            " SELECT DISTINCT li.invoice_id FROM invoice_line_items li"
            " JOIN session_participants sp ON sp.session_id = li.source_session_id"
            " WHERE sp.person_id = ?)"
        )
        params.append(participant_person_id)

    if invoice_date_from:
        conditions.append("i.invoice_date >= ?")
        params.append(invoice_date_from)

    if invoice_date_to:
        conditions.append("i.invoice_date <= ?")
        params.append(invoice_date_to)

    if billing_month:
        conditions.append("i.billing_month = ?")
        params.append(billing_month)

    if service_period_from:
        conditions.append("i.billing_period_start >= ?")
        params.append(service_period_from)

    if service_period_to:
        conditions.append("i.billing_period_end <= ?")
        params.append(service_period_to)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    sort_col = _VALID_SORT_FIELDS.get(sort_by, "i.invoice_date")
    sort_direction = "DESC" if (sort_dir or "desc").lower() == "desc" else "ASC"
    # Secondary sort for stability
    secondary = "i.invoice_number DESC" if sort_col != "i.invoice_number" else "i.created_at DESC"

    rows = conn.execute(
        f"""
        SELECT i.*, bp.billing_name AS current_bill_to_name,
               fp.display_name AS filing_owner_current_name,
               fp.person_code AS filing_owner_current_code,
               COUNT(DISTINCT li.invoice_line_item_id) AS line_count,
               GROUP_CONCAT(DISTINCT li.participants_snapshot) AS participants_display
        FROM invoices i
        JOIN billing_parties bp ON bp.billing_party_id = i.bill_to_party_id
        LEFT JOIN people fp ON fp.person_id = i.filing_owner_person_id
        LEFT JOIN invoice_line_items li ON li.invoice_id = i.invoice_id
        {where}
        GROUP BY i.invoice_id
        ORDER BY {sort_col} {sort_direction}, {secondary}, i.created_at DESC
        """,
        params,
    ).fetchall()

    # Enrich with payment info and post-filter by payment_status
    enriched: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        invoice_id = record["invoice_id"]
        paid_cents = _invoice_paid_cents(conn, invoice_id)
        total_cents = int(record.get("total_cents") or 0)
        balance_cents = max(total_cents - paid_cents, 0)
        record["paid_cents"] = paid_cents
        record["balance_cents"] = balance_cents
        record["payment_status"] = _derive_payment_status(record["status"], paid_cents, total_cents)
        record["filing_owner_display"] = (
            record.get("filing_owner_display_name_snapshot")
            or record.get("filing_owner_current_name")
            or ""
        )
        # Deduplicate participant names from the concatenated snapshot
        raw_participants = record.get("participants_display") or ""
        if raw_participants:
            seen: list[str] = []
            for name in raw_participants.split(","):
                name = name.strip()
                if name and name not in seen:
                    seen.append(name)
            record["participants_display"] = ", ".join(seen)
        else:
            record["participants_display"] = ""
        enriched.append(record)

    # Post-filter by payment_status (derived field)
    valid_payment_statuses = {"paid", "unpaid", "partially_paid", "void"}
    if payment_status and payment_status in valid_payment_statuses:
        enriched = [r for r in enriched if r["payment_status"] == payment_status]

    total = len(enriched)

    # Paginate
    if limit > 0:
        page_items = enriched[offset:offset + limit]
    else:
        page_items = enriched[offset:]

    return {
        "items": page_items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def get_invoice(conn: sqlite3.Connection, invoice_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)).fetchone()
    if not row:
        raise ValueError("Invoice was not found.")
    if row["status"] == "draft":
        synchronize_draft_delivery_method(conn, invoice_id)
        row = conn.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)).fetchone()
    lines = conn.execute("SELECT * FROM invoice_line_items WHERE invoice_id = ? ORDER BY sort_order, created_at", (invoice_id,)).fetchall()
    current_profile = conn.execute("SELECT * FROM business_profile WHERE active = 1 LIMIT 1").fetchone()
    current_party = conn.execute("SELECT * FROM billing_parties WHERE billing_party_id = ?", (row["bill_to_party_id"],)).fetchone()
    profile = dict(current_profile) if current_profile and row["status"] == "draft" else None
    party = dict(current_party) if current_party else None
    invoice = dict(row)
    line_dicts = [dict(line) for line in lines]
    paid_cents = _invoice_paid_cents(conn, invoice_id)
    total_cents = int(invoice.get("total_cents") or 0)
    invoice["paid_cents"] = paid_cents
    invoice["balance_cents"] = max(total_cents - paid_cents, 0)
    if invoice["status"] == "void":
        invoice["balance_cents"] = 0
    invoice["payment_status"] = _derive_payment_status(invoice["status"], paid_cents, total_cents)
    filing = resolve_invoice_filing_owner(conn, invoice_id)
    invoice["filing_owner_display"] = (
        invoice.get("filing_owner_display_name_snapshot")
        or (filing.get("selected") or {}).get("display_name")
        or ""
    )
    if invoice["status"] in ("finalized", "void") and invoice.get("pdf_path"):
        version = invoice.get("pdf_sha256") or invoice.get("updated_at") or invoice_id
        invoice["final_pdf_url"] = f"/api/invoices/{invoice_id}/final-pdf?v={version}"

    # 1. Parse and validate as_finalized_summary snapshot if finalized
    as_finalized_summary = None
    if invoice["status"] in ("finalized", "void"):
        snapshot_str = invoice.get("account_summary_snapshot")
        if snapshot_str:
            import json
            try:
                snapshot = json.loads(snapshot_str)
                if isinstance(snapshot, dict) and snapshot.get("version") == 1:
                    required_keys = {
                        "current_invoice_total_cents",
                        "current_invoice_paid_cents",
                        "current_invoice_balance_cents",
                        "prior_unpaid_balance_cents",
                        "total_amount_due_cents",
                        "prior_invoices",
                    }
                    if required_keys.issubset(snapshot.keys()) and isinstance(snapshot["prior_invoices"], list):
                        as_finalized_summary = snapshot
            except Exception:
                as_finalized_summary = None

    # 2. Construct dynamic current status of this selected invoice
    current_status = {
        "current_invoice_total_cents": total_cents,
        "current_invoice_paid_cents": paid_cents,
        "current_invoice_balance_cents": invoice["balance_cents"],
    }

    # 3. Compute dynamic summary for draft, or use snapshot for finalized
    if invoice["status"] == "draft":
        effective_summary = calculate_invoice_account_summary(conn, invoice_id)
    else:
        effective_summary = as_finalized_summary

    return {
        "invoice": invoice,
        "lines": line_dicts,
        "business_profile": profile,
        "billing_party": party,
        "filing_owner": filing,
        "as_finalized_summary": as_finalized_summary,
        "current_status": current_status,
        "render_model": build_invoice_render_model(
            invoice,
            line_dicts,
            business_profile=dict(current_profile) if current_profile else None,
            billing_party=party,
            account_summary=effective_summary,
        ),
    }


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
    if s.get("appointment_status") == "late_cancellation" and s.get("billing_treatment") not in {"bill_full_fee", "custom_fee", "waived"}:
        reasons.append("Late cancellation requires explicit billing treatment")
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

    requested = str(data.get("delivery_method") or "").strip()
    if requested and requested not in DELIVERY_METHODS:
        raise ValueError("Invalid delivery method.")
    if requested in ("email", "mail", "both"):
        method = requested
    else:
        method = str(party["preferred_delivery_method"] or "unresolved")
        if method not in DELIVERY_METHODS:
            method = "unresolved"
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
          time_category_snapshot, appointment_status_snapshot, billing_treatment_snapshot,
          scheduled_rate_cents_snapshot, duration_minutes, description_snapshot,
          custom_service_description_snapshot, custom_service_code_snapshot, quantity,
          unit_amount_cents, line_amount_cents, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)""",
        (new_id(), invoice_id, session_id, order, session["session_date"], participants,
         catalog["service_catalog_id"], service_name, billing_type, session["time_category"],
         session["appointment_status"], session["billing_treatment"],
         session["scheduled_rate_cents_snapshot"] if "scheduled_rate_cents_snapshot" in session.keys() else None,
         session["approved_duration_minutes"] or session["duration_minutes"],
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


def _consolidate_duplicate_payer_drafts(conn: sqlite3.Connection) -> int:
    """Merge draft invoices for duplicate person-linked billing parties.

    For each person with multiple active person-linked billing parties, find
    draft invoices for the same billing_month and move lines from redundant
    drafts to the canonical draft. Never touches finalized or void invoices.
    Returns the number of redundant drafts consolidated.
    """
    # Find persons with multiple active person-linked billing parties
    dup_persons = conn.execute(
        """
        SELECT bp.person_id, COUNT(*) AS bp_count
        FROM billing_parties bp
        WHERE bp.active = 1 AND bp.person_id IS NOT NULL AND bp.billing_party_type = 'person'
        GROUP BY bp.person_id
        HAVING COUNT(*) > 1
        """
    ).fetchall()

    consolidated = 0
    for row in dup_persons:
        person_id = row["person_id"]
        # Get all active billing parties for this person, ordered by account reference count
        parties = conn.execute(
            """
            SELECT bp.*,
              (SELECT COUNT(*) FROM client_accounts ca
               WHERE ca.default_billing_party_id = bp.billing_party_id AND ca.active = 1) AS acct_count
            FROM billing_parties bp
            WHERE bp.active = 1 AND bp.person_id = ? AND bp.billing_party_type = 'person'
            ORDER BY acct_count DESC, bp.updated_at DESC
            """,
            (person_id,),
        ).fetchall()

        canonical_id = parties[0]["billing_party_id"]
        redundant_ids = [p["billing_party_id"] for p in parties[1:]]

        for r_id in redundant_ids:
            # Find draft invoices for the redundant billing party
            r_drafts = conn.execute(
                "SELECT * FROM invoices WHERE bill_to_party_id = ? AND status = 'draft'",
                (r_id,),
            ).fetchall()
            for r_draft in r_drafts:
                bm = r_draft["billing_month"]
                if not bm:
                    continue
                # Find or create canonical draft for same month
                canonical_draft = conn.execute(
                    "SELECT * FROM invoices WHERE bill_to_party_id = ? AND billing_month = ? AND status = 'draft'",
                    (canonical_id, bm),
                ).fetchone()
                if not canonical_draft:
                    # Repoint the redundant draft to canonical
                    conn.execute(
                        "UPDATE invoices SET bill_to_party_id = ?, updated_at = ? WHERE invoice_id = ?",
                        (canonical_id, now_iso(), r_draft["invoice_id"]),
                    )
                    continue
                # Move lines from redundant draft to canonical draft
                r_lines = conn.execute(
                    "SELECT * FROM invoice_line_items WHERE invoice_id = ?",
                    (r_draft["invoice_id"],),
                ).fetchall()
                for line in r_lines:
                    already = conn.execute(
                        "SELECT 1 FROM invoice_line_items WHERE invoice_id = ? AND source_session_id = ?",
                        (canonical_draft["invoice_id"], line["source_session_id"]),
                    ).fetchone()
                    if not already:
                        order = conn.execute(
                            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM invoice_line_items WHERE invoice_id = ?",
                            (canonical_draft["invoice_id"],),
                        ).fetchone()[0]
                        conn.execute(
                            "UPDATE invoice_line_items SET invoice_id = ?, sort_order = ? WHERE invoice_line_item_id = ?",
                            (canonical_draft["invoice_id"], order, line["invoice_line_item_id"]),
                        )
                # Recalculate canonical draft totals
                _recalculate(conn, canonical_draft["invoice_id"])
                conn.execute(
                    "UPDATE invoices SET revision = revision + 1, updated_at = ? WHERE invoice_id = ?",
                    (now_iso(), canonical_draft["invoice_id"]),
                )
                # Delete the now-empty redundant draft
                conn.execute("DELETE FROM invoices WHERE invoice_id = ?", (r_draft["invoice_id"],))
                _audit(conn, "invoice", r_draft["invoice_id"], "consolidated_into_canonical_draft",
                       {"canonical_invoice_id": canonical_draft["invoice_id"], "billing_month": bm})
                consolidated += 1

    if consolidated > 0:
        conn.commit()

    return consolidated


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
        "drafts_consolidated": 0,
    }

    # --- Step 0: Consolidate drafts for duplicate person-linked billing parties ---
    result["drafts_consolidated"] = _consolidate_duplicate_payer_drafts(conn)

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
    insurance_coding_payload: dict[str, Any] | None = None,
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

    # 3. Valid positive line amounts (waived late cancellation is valid at $0.00)
    for line in lines:
        amount = line.get("line_amount_cents")
        is_waived_late_cancel = (
            line.get("appointment_status_snapshot") == "late_cancellation"
            and line.get("billing_treatment_snapshot") == "waived"
        )
        if amount is None or int(amount) < 0 or (int(amount) == 0 and not is_waived_late_cancel):
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
        if delivery == "unresolved":
            errors.append({
                "field": "delivery_method",
                "message": "Choose whether this invoice should be delivered by email, mail, or both before finalizing.",
            })
        if delivery in ("email", "both"):
            if not _present_text(party["billing_email"]):
                errors.append({
                    "field": "delivery_email",
                    "message": f"Add a billing email to the active billing setup for this payer. Email is required for {delivery} delivery.",
                })
        if delivery in ("mail", "both"):
            if not _billing_address_complete(party):
                errors.append({
                    "field": "delivery_address",
                    "message": f"Add a mailing address to the active billing setup for this payer. Street, city, state, and ZIP are required for {delivery} delivery.",
                })

    # 7. Required business / payee / payment-address details used on the invoice
    if profile:
        if not _present_text(profile["business_name"]):
            errors.append({"field": "business_name", "message": "Business name is required on the invoice."})
        if not _present_text(profile["payee_name"]):
            errors.append({"field": "payee_name", "message": "Payee name is required on the invoice."})
        if not _present_text(profile["payment_address_line_1"]):
            errors.append({"field": "payment_address", "message": "Payment address is required on the invoice."})
        if not _present_text(profile["zelle_recipient"]):
            errors.append({"field": "zelle_recipient", "message": "Invoice Settings must include a Zelle email or mobile number before finalizing."})

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
    filing = resolve_invoice_filing_owner(conn, invoice_id)
    if not filing.get("selected"):
        errors.append({
            "field": "filing_owner",
            "message": filing.get("message") or "Choose which client this invoice should be filed under.",
        })

    # 11. Preview revision is stale
    if expected_revision is not None and invoice["revision"] != expected_revision:
        errors.append({"field": "revision", "message": "Invoice has changed since preview. Please review and try again."})

    # 12. Insurance coding validation
    if insurance_coding_payload and insurance_coding_payload.get("insurance_coding_included"):
        diagnosis = str(insurance_coding_payload.get("insurance_diagnosis_code") or "").strip()
        if not diagnosis:
            errors.append({"field": "insurance_diagnosis_code", "message": "Diagnosis Code is required when insurance coding is enabled."})
        if profile:
            if not _present_text(profile["insurance_ein"]):
                errors.append({"field": "insurance_ein", "message": "EIN is required in Invoice Settings when insurance coding is enabled."})
            if not _present_text(profile["insurance_npi"]):
                errors.append({"field": "insurance_npi", "message": "NPI is required in Invoice Settings when insurance coding is enabled."})
            if not _present_text(profile["insurance_sw"]):
                errors.append({"field": "insurance_sw", "message": "SW is required in Invoice Settings when insurance coding is enabled."})

    return {
        "ready": not errors,
        "errors": errors,
        "preview_revision": invoice["revision"],
    }


def synchronize_draft_delivery_method(conn: sqlite3.Connection, invoice_id: str, *, commit: bool = True) -> bool:
    """Resolve a stale unresolved/blank delivery_method on a draft invoice from the active billing setup.

    Only operates on draft invoices. Only fills in blank or 'unresolved' values —
    never overwrites a deliberate email/mail/both choice. Only uses the active billing party.
    Increments revision and writes audit exactly once when a real change occurs.
    Idempotent: repeated calls with no change produce no writes.

    When *commit* is False the caller manages the transaction (e.g. finalize_invoice).
    Returns True if a change was made.
    """
    invoice = conn.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)).fetchone()
    if not invoice or invoice["status"] != "draft":
        return False
    current = str(invoice["delivery_method"] or "unresolved").strip()
    if current in ("email", "mail", "both"):
        return False
    party = conn.execute(
        "SELECT * FROM billing_parties WHERE billing_party_id = ? AND active = 1",
        (invoice["bill_to_party_id"],),
    ).fetchone()
    if not party:
        return False
    resolved = str(party["preferred_delivery_method"] or "unresolved").strip()
    if resolved not in ("email", "mail", "both"):
        return False
    conn.execute(
        "UPDATE invoices SET delivery_method = ?, revision = revision + 1, updated_at = ? WHERE invoice_id = ?",
        (resolved, now_iso(), invoice_id),
    )
    _audit(conn, "invoice", invoice_id, "delivery_method_synced", {
        "from": current,
        "to": resolved,
        "source": "active_billing_setup",
    })
    if commit:
        conn.commit()
    return True


def preview_finalization(conn: sqlite3.Connection, invoice_id: str, *, data: dict[str, Any] | None = None) -> dict[str, Any]:
    _draft(conn, invoice_id)
    result = get_invoice(conn, invoice_id)
    invoice = result["invoice"]
    lines = result["lines"]
    readiness = validate_invoice_readiness(
        conn, invoice_id,
        insurance_coding_payload=data,
    )
    profile = conn.execute("SELECT * FROM business_profile WHERE active = 1 LIMIT 1").fetchone()
    party = conn.execute("SELECT * FROM billing_parties WHERE billing_party_id = ?", (invoice["bill_to_party_id"],)).fetchone()
    insurance_payload = None
    if data and data.get("insurance_coding_included"):
        insurance_payload = {
            "insurance_coding_included": True,
            "insurance_diagnosis_code": data.get("insurance_diagnosis_code") or "",
        }
    return {
        "invoice": dict(invoice),
        "lines": [dict(line) for line in lines],
        "business_profile": dict(profile) if profile else None,
        "billing_party": dict(party) if party else None,
        "filing_owner": resolve_invoice_filing_owner(conn, invoice_id),
        "render_model": build_invoice_render_model(
            dict(invoice),
            [dict(line) for line in lines],
            business_profile=dict(profile) if profile else None,
            billing_party=dict(party) if party else None,
            insurance_coding_payload=insurance_payload,
        ),
        "preview_revision": invoice["revision"],
        "readiness": readiness,
    }


def finalize_invoice(conn: sqlite3.Connection, invoice_id: str, *, expected_revision: int | None = None, pdf_root: str | Path | None = None, insurance_coding_included: bool = False, insurance_diagnosis_code: str = "") -> dict[str, Any]:
    existing = conn.execute("SELECT status, pdf_path FROM invoices WHERE invoice_id = ?", (invoice_id,)).fetchone()
    if not existing:
        raise ValueError("Invoice was not found.")
    if existing["status"] == "finalized":
        if not existing["pdf_path"]:
            raise ValueError("No PDF file is stored for this invoice.")
        return get_invoice(conn, invoice_id)
    if existing["status"] != "draft":
        raise ValueError("Only a draft invoice can be finalized.")
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
    pdf_existed_before = False
    try:
        synchronize_draft_delivery_method(conn, invoice_id, commit=False)
        insurance_payload = {
            "insurance_coding_included": insurance_coding_included,
            "insurance_diagnosis_code": insurance_diagnosis_code,
        }
        readiness = validate_invoice_readiness(conn, invoice_id, expected_revision=expected_revision, insurance_coding_payload=insurance_payload)
        if not readiness["ready"]:
            raise ValueError("; ".join(e["message"] for e in readiness["errors"]))
        invoice = conn.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)).fetchone()
        lines = conn.execute("SELECT * FROM invoice_line_items WHERE invoice_id = ? ORDER BY sort_order", (invoice_id,)).fetchall()
        profile = conn.execute("SELECT * FROM business_profile WHERE active = 1 LIMIT 1").fetchone()
        party = conn.execute("SELECT * FROM billing_parties WHERE billing_party_id = ?", (invoice["bill_to_party_id"],)).fetchone()
        filing = resolve_invoice_filing_owner(conn, invoice_id)
        filing_owner = filing.get("selected")
        if not filing_owner:
            raise ValueError(filing.get("message") or "Choose which client this invoice should be filed under.")
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
            "zelle_recipient_snapshot": _present_text(profile["zelle_recipient"]),
            "filing_owner_person_id": filing_owner["person_id"],
            "filing_owner_person_code_snapshot": filing_owner.get("person_code"),
            "filing_owner_display_name_snapshot": filing_owner.get("display_name"),
            "logo_reference_snapshot": resolve_logo_path(profile["logo_path"]),
            "logo_contains_business_details_snapshot": profile["logo_contains_business_details"],
            "show_email_below_logo_snapshot": profile["show_email_below_logo"],
            "total_label_snapshot": profile["invoice_total_label"],
            "number_format_snapshot": profile["invoice_number_format"],
            "insurance_coding_included": 1 if insurance_coding_included else 0,
            "insurance_diagnosis_code_snapshot": insurance_diagnosis_code.strip() if insurance_coding_included else None,
            "insurance_ein_snapshot": _present_text(profile["insurance_ein"]) if insurance_coding_included else None,
            "insurance_npi_snapshot": _present_text(profile["insurance_npi"]) if insurance_coding_included else None,
            "insurance_sw_snapshot": _present_text(profile["insurance_sw"]) if insurance_coding_included else None,
            "status": "finalized", "finalized_at": now, "updated_at": now,
        }
        conn.execute(f"UPDATE invoices SET {', '.join(f'{k} = ?' for k in snapshots)} WHERE invoice_id = ?", (*snapshots.values(), invoice_id))
        _recalculate(conn, invoice_id)

        # Compute the frozen account summary and store it
        account_summary = calculate_invoice_account_summary(conn, invoice_id)
        import json
        summary_json = json.dumps(account_summary)
        conn.execute("UPDATE invoices SET account_summary_snapshot = ? WHERE invoice_id = ?", (summary_json, invoice_id))

        frozen = get_invoice(conn, invoice_id)
        root = Path(pdf_root or os.getenv("JORDANA_INVOICES_DIR", "Invoices"))
        client_folder = _filing_owner_folder(conn, root, filing_owner)
        month_folder = _sanitize_path_part(_month_folder_label(invoice), "Unknown Month")
        pdf_path = client_folder / month_folder / f"Invoice_{number}.pdf"
        pdf_existed_before = pdf_path.exists()
        if pdf_existed_before:
            raise ValueError("A finalized invoice PDF already exists at the target invoice location.")
        checksum = generate_invoice_pdf(frozen["invoice"], frozen["lines"], pdf_path, render_model=frozen["render_model"])
        conn.execute("UPDATE invoices SET pdf_path = ?, pdf_sha256 = ?, updated_at = ? WHERE invoice_id = ?", (str(pdf_path), checksum, now_iso(), invoice_id))
        _audit(conn, "invoice", invoice_id, "finalized", {"invoice_number": number, "pdf_sha256": checksum})
        conn.commit()
    except Exception:
        conn.rollback()
        if pdf_path and not pdf_existed_before and pdf_path.exists():
            pdf_path.unlink()
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
    billing_treatment = session["billing_treatment"] if "billing_treatment" in session.keys() else None

    if appointment_status == "late_cancellation" and billing_treatment == "waived":
        return "Late Cancellation - Fee Waived"

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
