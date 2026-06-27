from __future__ import annotations

import base64
import mimetypes
from datetime import date
from pathlib import Path
from typing import Any


STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_LOGO_PATH = STATIC_DIR / "assets" / "jordana-logo.png"


def resolve_logo_path(raw_path: str | None) -> str | None:
    configured = str(raw_path or "").strip()
    if configured:
        return configured
    if DEFAULT_LOGO_PATH.is_file():
        return str(DEFAULT_LOGO_PATH)
    return None


def logo_data_uri(raw_path: str | None) -> str | None:
    resolved = resolve_logo_path(raw_path)
    if not resolved:
        return None
    path = Path(resolved).expanduser()
    if not path.is_file():
        return None
    mime_type, _ = mimetypes.guess_type(path.name)
    if not mime_type:
        mime_type = "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def format_long_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = date.fromisoformat(text[:10])
    except (TypeError, ValueError):
        return text
    return parsed.strftime("%B %d, %Y")


def format_month_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) == 7 and text[4] == "-":
        try:
            year = int(text[:4])
            month = int(text[5:7])
            parsed = date(year, month, 1)
        except (TypeError, ValueError):
            return text
        return parsed.strftime("%B %Y")
    try:
        parsed = date.fromisoformat(text[:10])
    except (TypeError, ValueError):
        return text
    return parsed.strftime("%B %Y")


def format_billing_period(
    billing_month: Any,
    billing_period_start: Any,
    billing_period_end: Any,
) -> str:
    month_label = format_month_label(billing_month)
    if month_label:
        return month_label

    start_text = str(billing_period_start or "").strip()
    end_text = str(billing_period_end or "").strip()
    if not start_text or not end_text:
        return ""
    try:
        start = date.fromisoformat(start_text[:10])
        end = date.fromisoformat(end_text[:10])
    except (TypeError, ValueError):
        return " - ".join(part for part in (start_text, end_text) if part)

    start_label = start.strftime("%B %Y")
    end_label = end.strftime("%B %Y")
    if start_label == end_label:
        return start_label
    return f"{start_label} - {end_label}"


def compact_address_lines(
    line1: Any,
    line2: Any,
    city: Any,
    state: Any,
    postal_code: Any,
) -> list[str]:
    lines: list[str] = []
    if str(line1 or "").strip():
        lines.append(str(line1).strip())
    if str(line2 or "").strip():
        lines.append(str(line2).strip())
    locality_parts = [str(value).strip() for value in (city, state) if str(value or "").strip()]
    locality = ", ".join(locality_parts)
    postal = str(postal_code or "").strip()
    if postal:
        locality = f"{locality} {postal}".strip() if locality else postal
    if locality:
        lines.append(locality)
    return lines


def split_snapshot_lines(value: Any) -> list[str]:
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]


def display_invoice_number(invoice_number: Any, status: Any) -> str:
    if str(invoice_number or "").strip():
        return str(invoice_number).strip()
    if str(status or "") == "draft":
        return "Assigned when finalized"
    return ""


def build_invoice_render_model(
    invoice: dict[str, Any],
    lines: list[dict[str, Any]],
    *,
    business_profile: dict[str, Any] | None = None,
    billing_party: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = business_profile or {}
    party = billing_party or {}

    logo_path = resolve_logo_path(invoice.get("logo_reference_snapshot") or profile.get("logo_path"))
    logo_uri = logo_data_uri(invoice.get("logo_reference_snapshot") or profile.get("logo_path"))
    business_address_lines = split_snapshot_lines(invoice.get("business_address_snapshot")) or compact_address_lines(
        profile.get("address_line_1"),
        profile.get("address_line_2"),
        profile.get("city"),
        profile.get("state"),
        profile.get("postal_code"),
    )
    bill_to_address_lines = split_snapshot_lines(invoice.get("bill_to_address_snapshot")) or compact_address_lines(
        party.get("billing_address_line_1"),
        party.get("billing_address_line_2"),
        party.get("billing_city"),
        party.get("billing_state"),
        party.get("billing_postal_code"),
    )

    sender_lines = [
        value for value in [
            " ".join(part for part in [
                str(invoice.get("provider_name_snapshot") or profile.get("provider_display_name") or "").strip(),
                str(invoice.get("credentials_snapshot") or profile.get("credentials_display") or "").strip(),
            ] if part).strip(),
            *business_address_lines,
            str(invoice.get("business_phone_snapshot") or profile.get("phone") or "").strip(),
        ]
        if value
    ]

    bill_to_lines = [
        value for value in [
            str(invoice.get("bill_to_name_snapshot") or party.get("billing_name") or "").strip(),
            *bill_to_address_lines,
        ]
        if value
    ]

    payee_name = str(invoice.get("payee_name_snapshot") or profile.get("payee_name") or "").strip()
    payment_lines = split_snapshot_lines(invoice.get("payment_address_snapshot"))
    if not payment_lines:
        payment_lines = compact_address_lines(
            profile.get("payment_address_line_1"),
            profile.get("payment_address_line_2"),
            profile.get("payment_city"),
            profile.get("payment_state"),
            profile.get("payment_postal_code"),
        )
    if payment_lines and payee_name and payment_lines[0] == payee_name:
        payment_lines = payment_lines[1:]

    rendered_lines = []
    for line in lines:
        rendered_lines.append({
            "service_date_display": format_long_date(line.get("service_date")),
            "participants_display": line.get("participants_snapshot") or "",
            "description_display": line.get("description_snapshot") or line.get("service_name_snapshot") or "",
            "duration_display": (
                f"{int(line['duration_minutes'])} min"
                if line.get("duration_minutes") is not None
                else "-"
            ),
            "amount_display": money(line.get("line_amount_cents")),
        })

    return {
        "logo_path": logo_path,
        "logo_data_uri": logo_uri,
        "sender_lines": sender_lines,
        "bill_to_lines": bill_to_lines,
        "invoice_number_display": display_invoice_number(invoice.get("invoice_number"), invoice.get("status")),
        "invoice_date_display": format_long_date(invoice.get("invoice_date")),
        "billing_period_display": format_billing_period(
            invoice.get("billing_month"),
            invoice.get("billing_period_start"),
            invoice.get("billing_period_end"),
        ),
        "lines": rendered_lines,
        "payment_title": "Please make all checks payable to:",
        "payment_name": payee_name,
        "payment_lines": payment_lines,
        "notes": str(invoice.get("notes") or "").strip(),
        "total_label": str(invoice.get("total_label_snapshot") or profile.get("invoice_total_label") or "TOTAL DUE"),
        "total_display": money(invoice.get("total_cents")),
    }


def money(cents: Any) -> str:
    return f"${int(cents or 0) / 100:,.2f}"
