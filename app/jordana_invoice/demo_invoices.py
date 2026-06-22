from __future__ import annotations

import sqlite3
from pathlib import Path

from .importer import import_rows
from .invoice_services import create_invoice_draft, finalize_invoice, save_business_profile, void_invoice
from .review_services import approve_candidate, create_billing_party, create_person
from .util import stable_hash


def seed_demo_invoice_data(conn: sqlite3.Connection, pdf_root: str | Path) -> dict[str, int]:
    save_business_profile(conn, {
        "business_name": "Demo Practice", "provider_display_name": "Demo Provider",
        "address_line_1": "100 Example Avenue", "city": "Example", "state": "FL", "postal_code": "00000",
        "phone": "555-0100", "email": "billing@example.test", "payee_name": "Demo Payee",
        "payment_address_line_1": "100 Example Avenue", "payment_city": "Example", "payment_state": "FL",
        "payment_postal_code": "00000", "invoice_total_label": "TOTAL DUE", "invoice_number_format": "YYYY-NNNN",
        "logo_path": "", "logo_contains_business_details": False, "show_email_below_logo": True,
    })
    avery = create_person(conn, {"first_name": "Avery", "last_name": "Stone", "display_name": "Avery Stone"})
    taylor = create_person(conn, {"first_name": "Taylor", "last_name": "Reed", "display_name": "Taylor Reed"})
    casey = create_person(conn, {"first_name": "Casey", "last_name": "North", "display_name": "Casey North"})
    avery_party = create_billing_party(conn, {
        "billing_name": "Avery Stone", "person_id": avery["person_id"], "billing_email": "avery@example.test",
        "billing_address_line_1": "10 Sample Street", "billing_city": "Example", "billing_state": "FL",
        "billing_postal_code": "00000", "preferred_delivery_method": "email",
    })
    parent_party = create_billing_party(conn, {
        "billing_name": "Jordan North", "billing_email": "parent@example.test", "billing_address_line_1": "20 Fiction Road",
        "billing_city": "Example", "billing_state": "FL", "billing_postal_code": "00000", "preferred_delivery_method": "mail",
    })

    sessions = []
    specs = [
        ("completed", "Avery Stone | 60 | Office", [avery], avery_party, "office", "standard", 15000, "billable"),
        ("joint", "Avery Stone & Taylor Reed | 60 | FaceTime", [avery, taylor], avery_party, "facetime", "evening", 22000, "billable"),
        ("minor", "Casey North | 30 | Phone", [casey], parent_party, "phone", "standard", 9000, "billable"),
        ("correspondence", "Avery Stone | 60 | Correspondence", [avery], avery_party, "correspondence", "weekend", 12500, "billable"),
        ("preparation", "Avery Stone | 60 | Preparation", [avery], avery_party, "preparation", "standard", 17500, "billable"),
        ("mediation", "Avery Stone | 60 | Mediation", [avery], avery_party, "mediation", "weekend_evening", 45000, "billable"),
        ("cancelled-billable", "Avery Stone | 60 | Office | Cancelled", [avery], avery_party, "office", "standard", 15000, "billable"),
        ("no-show-billable", "Avery Stone | 60 | Phone | No Show", [avery], avery_party, "phone", "standard", 15000, "billable"),
        ("custom", "Avery Stone | 60 | Case Conference", [avery], avery_party, "Case Conference", "standard", 11000, "billable"),
    ]
    for index, spec in enumerate(specs, start=1):
        sessions.append(_approved_demo_session(conn, index, *spec))
    cancelled_excluded = _approved_demo_session(conn, 30, "cancelled-free", "Avery Stone | 60 | Office | Cancelled", [avery], avery_party, "office", "standard", 0, "not_billable")
    no_show_unresolved = _approved_demo_session(conn, 31, "no-show-unresolved", "Avery Stone | 60 | Phone | No Show", [avery], avery_party, "phone", "standard", 15000, "billable")
    conn.execute("UPDATE sessions SET billing_treatment='unresolved', review_status='needs_billing_treatment' WHERE id = ?", (no_show_unresolved,))
    conn.commit()

    first = create_invoice_draft(conn, _draft_payload(avery_party["billing_party_id"], [sessions[0]]))
    finalized = finalize_invoice(conn, first["invoice"]["invoice_id"], pdf_root=pdf_root)
    second = create_invoice_draft(conn, _draft_payload(avery_party["billing_party_id"], sessions[1:2] + sessions[3:6]))
    parent = create_invoice_draft(conn, _draft_payload(parent_party["billing_party_id"], [sessions[2]]))

    long_sessions = []
    for index in range(40, 64):
        long_sessions.append(_approved_demo_session(conn, index, f"multi-{index}", "Avery Stone | 60 | Office", [avery], avery_party, "office", "standard", 15000, "billable"))
    multi = create_invoice_draft(conn, _draft_payload(avery_party["billing_party_id"], long_sessions))
    finalize_invoice(conn, multi["invoice"]["invoice_id"], pdf_root=pdf_root)

    voided = void_invoice(conn, finalized["invoice"]["invoice_id"], "Sanitized void-and-reissue demonstration")
    reissue = create_invoice_draft(conn, _draft_payload(avery_party["billing_party_id"], [sessions[0]]))
    finalize_invoice(conn, reissue["invoice"]["invoice_id"], pdf_root=pdf_root)
    return {"drafts": 2, "finalized": 2, "void": 1, "excluded_sessions": 2}


def _approved_demo_session(conn, index, key, title, people, party, service, time_category, amount_cents, treatment):
    day = 1 + ((index - 1) % 28)
    start = f"2026-05-{day:02d}T10:00:00-04:00"
    row = {
        "ingested_at": "2026-06-01T12:00:00Z", "snapshot_key": f"invoice-demo-{key}", "run_id": "invoice-demo-run",
        "batch_name": "invoice-demo", "capture_window": "past_7_days", "captured_at": "2026-06-01T11:00:00Z",
        "source_device": "demo", "timezone": "America/New_York", "calendar_event_id": f"invoice-event-{key}",
        "event_fingerprint": f"invoice-fp-{key}", "event_title": title, "start_at": start,
        "end_at": f"2026-05-{day:02d}T11:00:00-04:00", "duration_minutes": "60", "calendar": "Jordana Work",
        "payload_version": "2", "raw_json": "{}",
    }
    import_rows(conn, [row], "SANITIZED_INVOICE_DEMO")
    candidate = conn.execute("SELECT id FROM calendar_event_candidates WHERE candidate_key = ? ORDER BY updated_at DESC LIMIT 1", (stable_hash(f"calendar_event_id:invoice-event-{key}"),)).fetchone()
    result = approve_candidate(conn, candidate["id"], {
        "participants": [{"person_id": person["person_id"], "display_name": person["display_name"]} for person in people],
        "billing_party_id": party["billing_party_id"], "approved_duration_minutes": 60, "service_mode": service,
        "time_category": time_category, "approved_rate": f"{amount_cents / 100:.2f}", "payment_status": "unpaid",
        "billing_treatment": treatment, "rate_scope": "session_only",
    })
    return result["session"]["id"]


def _draft_payload(party_id, session_ids):
    return {"bill_to_party_id": party_id, "billing_period_start": "2026-05-01", "billing_period_end": "2026-05-31", "invoice_date": "2026-06-01", "session_ids": session_ids}
