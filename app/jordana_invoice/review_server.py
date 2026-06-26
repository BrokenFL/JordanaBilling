from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .db import DatabaseBusyError, MigrationError, connect, migrate_database
from .google_sync import (
    default_transport,
    load_sync_config_for_database,
    public_sync_status,
    sync_status_for_connection,
    sync_with_connection,
)
from .review_services import (
    add_account_member,
    approve_candidate,
    BillingPartyNotFoundError,
    BillingPartyTypeError,
    create_account,
    create_account_or_return_existing,
    deactivate_account,
    create_billing_party,
    create_person,
    create_rate_rule_from_payload,
    dashboard_status,
    end_rate_rule,
    find_duplicate_billing_relationship,
    get_account_record,
    get_organization_billing_record,
    get_person_record,
    get_review_candidate,
    list_account_records,
    list_billing_relationship_records,
    list_sessions_ledger,
    list_people_records,
    list_review_candidates,
    list_rate_rules,
    mark_candidate,
    merge_people,
    promote_candidate_to_review,
    recalc_unapproved_session_rates,
    reparse_unapproved_candidates,
    restore_candidate,
    preview_rate_suggestion,
    refresh_candidate_suggestions,
    replace_rate_rule_from_payload,
    reactivate_account,
    remove_account_member,
    save_billing_section,
    save_interpretation,
    save_person_alias,
    save_person_section,
    save_relationship_section,
    save_session_draft,
    search_accounts,
    search_billing_parties,
    search_organization_billing_parties,
    search_people,
    setup_billing_relationship,
    update_account,
    update_billing_party,
    update_billing_relationship,
    update_person,
)
from .invoice_services import (
    add_sessions_to_draft,
    create_invoice_draft,
    eligible_sessions,
    finalize_invoice,
    get_business_profile,
    get_invoice,
    list_invoice_records,
    preview_finalization,
    remove_line_from_draft,
    save_business_profile,
    stage_approved_sessions_to_monthly_drafts,
    update_invoice_draft,
    void_invoice,
)
from .csv_reports import (
    available_report_types,
    available_years,
    default_report_year,
    generate_report_csv,
    report_filename,
)
from .service_catalog import list_services, set_service_active


STATIC_DIR = Path(__file__).parent / "static"
REVIEW_SYNC_TRANSPORT = default_transport

MAX_REQUEST_BODY_BYTES = 1_048_576  # 1 MiB
WRITE_TOKEN_HEADER = "X-Jordana-Write-Token"


def review_sync_config(database_path: str):
    return load_sync_config_for_database(database_path)


def is_safe_validation_error(error: Exception) -> bool:
    if isinstance(error, (BillingPartyNotFoundError, BillingPartyTypeError, DatabaseBusyError)):
        return True
    if isinstance(error, ValueError):
        msg = str(error)
        safe_messages = {
            # Accounts / Members
            "Account not found.",
            "A billing relationship already exists for this client.",
            "This client is already included in this billing relationship.",
            "This client is not a member of this billing relationship.",
            "payer_kind must be one of: client, person, organization.",
            "At least one covered client is required for an active relationship.",
            "Covered client IDs must be non-empty strings.",
            "Duplicate covered client IDs are not allowed.",
            "payer_person_id is required for client or person payer kind.",
            "Payer person does not exist or is not active.",
            "organization_billing_party_id is required for organization payer kind.",
            "Organization billing party does not exist, is not active, or is not an organization.",
            "This billing relationship already exists.",
            "Cannot edit an inactive billing relationship. Reactivate it first.",
            # Review Candidates / Sessions
            "Review candidate not found.",
            "No session found for this candidate; only session-backed candidates can be restored.",
            "A session already exists for this candidate.",
            "Raw snapshot not found for candidate.",
            "Session not found for candidate.",
            "Select which participant should receive this future rate.",
            "session_ids must be a list.",
            "Each session_id must be a non-empty string.",
            "Explicit finalization confirmation is required.",
            # People / Aliases / Merge
            "Person not found.",
            "Display name is required.",
            "Cannot merge a person into itself.",
            "Both people must exist before merging.",
            "First and last name are required before assigning a person code.",
            # Business Profile
            "Business name is required.",
            # Invoices / Drafts / Finalization
            "Invoice was not found.",
            "Select an active bill-to party.",
            "billing_month must be in YYYY-MM format.",
            "A valid billing period is required.",
            "Invalid delivery method.",
            "Session is already included in this draft.",
            "Source session was not found.",
            "All invoice sessions must use the selected bill-to party.",
            "Session is outside the invoice billing period.",
            "Invoice line was not found.",
            "A void reason is required.",
            "Only a finalized invoice can be voided.",
            "Only a draft invoice can be changed.",
            "supplement_sequence cannot be negative.",
            # Billing parties
            "Billing name is required.",
            "Invalid preferred delivery method.",
            "Invalid billing party type.",
            "Referenced person does not exist or is not active.",
            "Bill-to client must be a confirmed active person.",
            "Billing party not found.",
            "billing_name must not be blank.",
            "Cannot reassign billing party to a different person through this operation.",
            # Reports
            "Invalid year",
            "Year out of range",
            "Year must be an integer",
        }
        if msg in safe_messages:
            return True
        # Check dynamic prefixes
        safe_prefixes = (
            "Cannot approve until required fields are complete:",
            "No active billing party found for ",
            "Session is not invoice eligible: ",
            "Invalid year:",
            "Unsupported date range:",
            "Year must be an integer, got ",
            "Year out of range: ",
            "Invalid report header for ",
        )
        if any(msg.startswith(prefix) for prefix in safe_prefixes):
            return True
    return False


def make_handler(database_path: str, write_token: str | None = None):
    launch_write_token = write_token or secrets.token_urlsafe(32)

    class ReviewHandler(BaseHTTPRequestHandler):
        write_token = launch_write_token
        write_token_header = WRITE_TOKEN_HEADER

        def log_message(self, format: str, *args: object) -> None:
            return

        def parse_request(self) -> bool:
            if not super().parse_request():
                return False
            if not self.validate_host_and_origin():
                return False
            return True
        def validate_host_and_origin(self) -> bool:
            host_header = self.headers.get("Host")
            if host_header is None:
                self.send_json({"ok": False, "error": "Host header is required."}, status=400)
                return False

            if not self.is_valid_local_host(host_header):
                self.send_json({"ok": False, "error": "Invalid Host header."}, status=400)
                return False

            if self.command in {"POST", "PUT", "PATCH", "DELETE"}:
                origin_header = self.headers.get("Origin")
                if origin_header is not None:
                    if not origin_header.startswith("http://"):
                        self.send_json({"ok": False, "error": "Invalid Origin header."}, status=403)
                        return False
                    rest = origin_header[len("http://"):]
                    if not self.is_valid_local_host(rest):
                        self.send_json({"ok": False, "error": "Invalid Origin header."}, status=403)
                        return False
            return True

        @staticmethod
        def is_valid_local_host(host_header: str) -> bool:
            if not host_header:
                return False

            if any(c.isspace() for c in host_header):
                return False

            if "@" in host_header:
                return False

            if "/" in host_header or "\\" in host_header or "?" in host_header or "#" in host_header:
                return False

            if host_header.startswith("["):
                idx = host_header.find("]")
                if idx == -1:
                    return False
                host_part = host_header[:idx + 1]
                port_part = host_header[idx + 1:]
                if port_part:
                    if not port_part.startswith(":"):
                        return False
                    port_val = port_part[1:]
                    if not port_val or not port_val.isdigit():
                        return False
                    try:
                        p = int(port_val)
                        if not (1 <= p <= 65535):
                            return False
                    except ValueError:
                        return False
            else:
                if ":" in host_header:
                    parts = host_header.split(":")
                    if len(parts) != 2:
                        return False
                    host_part = parts[0]
                    port_val = parts[1]
                    if not port_val or not port_val.isdigit():
                        return False
                    try:
                        p = int(port_val)
                        if not (1 <= p <= 65535):
                            return False
                    except ValueError:
                        return False
                else:
                    host_part = host_header

            allowed_hosts = {"localhost", "127.0.0.1", "[::1]"}
            return host_part.lower() in allowed_hosts

        def send_error_response(self, error: Exception, default_status: int = 500) -> None:
            if isinstance(error, BillingPartyNotFoundError):
                status = 404
                msg = str(error)
            elif isinstance(error, BillingPartyTypeError):
                status = 400
                msg = str(error)
            elif isinstance(error, DatabaseBusyError):
                status = 503
                msg = "Database is busy, please try again."
            elif is_safe_validation_error(error):
                status = 400
                msg = str(error)
                if msg == "Account not found.":
                    status = 404
            else:
                status = default_status
                msg = "An unexpected error occurred."
            self.send_json({"ok": False, "error": msg}, status=status)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path in {"/", "/review", "/invoices", "/reports"} or parsed.path.startswith("/invoices/"):
                    self.send_static("review.html")
                    return
                if parsed.path in {"/clients", "/people"} or parsed.path.startswith("/clients/") or parsed.path.startswith("/people/"):
                    self.send_static("review.html")
                    return
                if parsed.path.startswith("/static/"):
                    self.send_static(parsed.path.removeprefix("/static/"))
                    return
                if parsed.path == "/api/health":
                    self.send_json({"ok": True, "status": "healthy"})
                    return
                if parsed.path == "/api/status":
                    self.send_json(dashboard_status(self.conn()))
                    return
                if parsed.path == "/api/review/candidates":
                    query = parse_qs(parsed.query)
                    self.send_json(
                        list_review_candidates(
                            self.conn(),
                            query=first(query, "q"),
                            review_status=first(query, "review_status"),
                            service_mode=first(query, "service_mode"),
                            billing_session_type=first(query, "billing_session_type"),
                            time_category=first(query, "time_category"),
                            payment_status=first(query, "payment_status"),
                            calendar_filter=first(query, "calendar_filter"),
                            limit=int(first(query, "limit") or 25),
                            offset=int(first(query, "offset") or 0),
                        )
                    )
                    return
                if parsed.path.startswith("/api/review/candidates/"):
                    candidate_id = parsed.path.rsplit("/", 1)[-1]
                    self.send_json(get_review_candidate(self.conn(), candidate_id))
                    return
                if parsed.path == "/api/people":
                    query = parse_qs(parsed.query)
                    if first(query, "full") == "1":
                        self.send_json(list_people_records(self.conn(), first(query, "q")))
                    else:
                        self.send_json(search_people(self.conn(), first(query, "q")))
                    return
                if parsed.path.startswith("/api/people/"):
                    person_id = parsed.path.rsplit("/", 1)[-1]
                    self.send_json(get_person_record(self.conn(), person_id))
                    return
                if parsed.path == "/api/accounts":
                    query = parse_qs(parsed.query)
                    if first(query, "full") == "1":
                        self.send_json(list_account_records(self.conn(), first(query, "q")))
                    else:
                        self.send_json(search_accounts(self.conn(), first(query, "q")))
                    return
                if parsed.path.startswith("/api/accounts/"):
                    account_id = parsed.path.rsplit("/", 1)[-1]
                    self.send_json(get_account_record(self.conn(), account_id))
                    return
                if parsed.path == "/api/billing-relationships":
                    self.send_json(list_billing_relationship_records(self.conn()))
                    return
                if parsed.path == "/api/billing-relationships/find-duplicate":
                    qs = parse_qs(parsed.query)
                    payer_kind = first(qs, "payer_kind") or ""
                    payer_person_id = first(qs, "payer_person_id") or None
                    org_bp_id = first(qs, "organization_billing_party_id") or None
                    covered_str = first(qs, "covered_client_ids") or ""
                    covered_ids = [c.strip() for c in covered_str.split(",") if c.strip()] if covered_str else []
                    dup = find_duplicate_billing_relationship(self.conn(), payer_kind, payer_person_id, org_bp_id, covered_ids)
                    self.send_json(dup or {})
                    return
                if parsed.path == "/api/billing-parties":
                    self.send_json(search_billing_parties(self.conn(), first(parse_qs(parsed.query), "q")))
                    return
                if parsed.path == "/api/organization-billing-parties":
                    self.send_json(search_organization_billing_parties(self.conn(), first(parse_qs(parsed.query), "q")))
                    return
                if parsed.path.startswith("/api/billing-parties/"):
                    billing_party_id = parsed.path.rsplit("/", 1)[-1]
                    self.send_json(get_organization_billing_record(self.conn(), billing_party_id))
                    return
                if parsed.path == "/api/rate-rules":
                    self.send_json(list_rate_rules(self.conn()))
                    return
                if parsed.path == "/api/business-profile":
                    self.send_json(get_business_profile(self.conn()))
                    return
                if parsed.path == "/api/sessions":
                    query = parse_qs(parsed.query)
                    self.send_json(
                        list_sessions_ledger(
                            self.conn(),
                            date_range=first(query, "date_range") or "rolling_30",
                            review_status=first(query, "review_status"),
                            payment_status=first(query, "payment_status"),
                            limit=int(first(query, "limit") or 30),
                            offset=int(first(query, "offset") or 0),
                        )
                    )
                    return
                if parsed.path == "/api/sync/status":
                    self.send_json(public_sync_status(sync_status_for_connection(self.conn())))
                    return
                if parsed.path == "/api/service-catalog":
                    self.send_json(list_services(self.conn(), first(parse_qs(parsed.query), "include_inactive") == "1"))
                    return
                if parsed.path == "/api/invoices/eligible-sessions":
                    query = parse_qs(parsed.query)
                    self.send_json(eligible_sessions(self.conn(), first(query, "bill_to_party_id"), first(query, "period_start"), first(query, "period_end")))
                    return
                if parsed.path == "/api/invoices":
                    self.send_json(list_invoice_records(self.conn(), first(parse_qs(parsed.query), "status")))
                    return
                if parsed.path.startswith("/api/invoices/"):
                    self.send_json(get_invoice(self.conn(), parsed.path.strip("/").split("/")[2]))
                    return
                if parsed.path == "/api/reports":
                    conn = self.conn()
                    self.send_json({
                        "reports": available_report_types(),
                        "years": available_years(conn),
                        "default_year": default_report_year(conn),
                    })
                    return
                if parsed.path == "/api/reports/download":
                    query = parse_qs(parsed.query)
                    report_type = first(query, "type")
                    year_str = first(query, "year")
                    try:
                        year = int(year_str)
                    except (ValueError, TypeError):
                        raise ValueError(f"Invalid year: {year_str!r}")
                    csv_text = generate_report_csv(self.conn(), report_type, year)
                    filename = report_filename(report_type, year)
                    self.send_csv(csv_text, filename)
                    return
                self.send_error(404)
            except Exception as error:
                self.send_error_response(error, default_status=500)

        def do_POST(self) -> None:
            parsed, data = self.read_mutation_json_request()
            if parsed is None:
                return
            try:
                if parsed.path == "/api/people":
                    self.send_json(create_person(self.conn(), data))
                    return
                if parsed.path.startswith("/api/people/") and parsed.path.endswith("/aliases"):
                    person_id = parsed.path.strip("/").split("/")[2]
                    self.send_json(
                        save_person_alias(
                            self.conn(),
                            person_id,
                            raw_alias=data.get("raw_alias", ""),
                            approved_by_user=bool(data.get("approved_by_user", True)),
                            alias_id=data.get("alias_id"),
                        )
                    )
                    return
                if parsed.path.startswith("/api/people/") and parsed.path.endswith("/merge"):
                    survivor_id = parsed.path.strip("/").split("/")[2]
                    self.send_json(
                        merge_people(
                            self.conn(),
                            survivor_id,
                            data["duplicate_person_id"],
                            data.get("reason", ""),
                        )
                    )
                    return
                if parsed.path.startswith("/api/people/"):
                    person_id = parsed.path.rsplit("/", 1)[-1]
                    self.send_json(update_person(self.conn(), person_id, data))
                    return
                if parsed.path == "/api/accounts":
                    self.send_json(create_account(self.conn(), data["account_name"], data.get("account_type", "individual")))
                    return
                if parsed.path == "/api/accounts/from-client":
                    result = create_account_or_return_existing(
                        self.conn(),
                        data["person_id"],
                        data["account_name"],
                        data.get("account_type", "individual"),
                    )
                    if result["existing"]:
                        self.send_json({"ok": False, "existing": True, "error": "A billing relationship already exists for this client.", "account_id": result["account"]["account_id"], "account_name": result["account"]["account_name"]}, status=409)
                    else:
                        self.send_json({"ok": True, "existing": False, "account_id": result["account"]["account_id"], "account_name": result["account"]["account_name"]})
                    return
                if parsed.path.startswith("/api/accounts/"):
                    parts = parsed.path.strip("/").split("/")
                    account_id = parts[2]
                    action = parts[3] if len(parts) > 3 else ""
                    if action == "deactivate":
                        self.send_json(deactivate_account(self.conn(), account_id))
                        return
                    if action == "reactivate":
                        self.send_json(reactivate_account(self.conn(), account_id))
                        return
                    if action == "update-billing-relationship":
                        self.send_json(update_billing_relationship(self.conn(), account_id, data))
                        return
                    if action == "remove-member":
                        remove_account_member(self.conn(), account_id, data["person_id"])
                        self.send_json({"ok": True})
                        return
                    self.send_json(update_account(self.conn(), account_id, data))
                    return
                if parsed.path == "/api/billing-parties":
                    self.send_json(create_billing_party(self.conn(), data))
                    return
                if parsed.path.startswith("/api/billing-parties/"):
                    billing_party_id = parsed.path.rsplit("/", 1)[-1]
                    self.send_json(update_billing_party(self.conn(), billing_party_id, data))
                    return
                if parsed.path == "/api/rate-rules":
                    self.send_json(create_rate_rule_from_payload(self.conn(), data))
                    return
                if parsed.path == "/api/rate-rules/preview":
                    self.send_json(preview_rate_suggestion(self.conn(), data))
                    return
                if parsed.path.startswith("/api/rate-rules/"):
                    parts = parsed.path.strip("/").split("/")
                    rule_id = parts[2]
                    action = parts[3] if len(parts) > 3 else ""
                    if action == "replace":
                        self.send_json(replace_rate_rule_from_payload(self.conn(), rule_id, data))
                        return
                    if action == "end":
                        self.send_json(end_rate_rule(self.conn(), rule_id, data.get("effective_through") or ""))
                        return
                if parsed.path == "/api/business-profile":
                    self.send_json(save_business_profile(self.conn(), data))
                    return
                if parsed.path == "/api/sync/run":
                    result = sync_with_connection(
                        self.conn(),
                        review_sync_config(database_path),
                        transport=REVIEW_SYNC_TRANSPORT,
                    )
                    self.send_json(
                        {
                            "rows_fetched": result.rows_fetched,
                            "rows_imported": result.rows_imported,
                            "status": public_sync_status(sync_status_for_connection(self.conn())),
                        }
                    )
                    return
                if parsed.path.startswith("/api/service-catalog/"):
                    parts = parsed.path.strip("/").split("/")
                    self.send_json(set_service_active(self.conn(), parts[2], parts[3] != "deactivate"))
                    return
                if parsed.path == "/api/invoices":
                    self.send_json(create_invoice_draft(self.conn(), data))
                    return
                if parsed.path == "/api/invoices/stage":
                    session_ids = data.get("session_ids")
                    if session_ids is not None:
                        if not isinstance(session_ids, list):
                            raise ValueError("session_ids must be a list.")
                        for sid in session_ids:
                            if not isinstance(sid, str) or not sid.strip():
                                raise ValueError("Each session_id must be a non-empty string.")
                    self.send_json(
                        stage_approved_sessions_to_monthly_drafts(self.conn(), session_ids=session_ids)
                    )
                    return
                if parsed.path.startswith("/api/invoices/"):
                    parts = parsed.path.strip("/").split("/")
                    invoice_id = parts[2]
                    action = parts[3] if len(parts) > 3 else "update"
                    if action == "add-sessions":
                        self.send_json(add_sessions_to_draft(self.conn(), invoice_id, data.get("session_ids") or []))
                        return
                    if action == "remove-line":
                        self.send_json(remove_line_from_draft(self.conn(), invoice_id, data["invoice_line_item_id"]))
                        return
                    if action == "preview-finalize":
                        self.send_json(preview_finalization(self.conn(), invoice_id, data=data))
                        return
                    if action == "finalize":
                        if not data.get("confirmed"):
                            raise ValueError("Explicit finalization confirmation is required.")
                        self.send_json(finalize_invoice(self.conn(), invoice_id, expected_revision=data.get("expected_revision")))
                        return
                    if action == "void":
                        self.send_json(void_invoice(self.conn(), invoice_id, data.get("reason") or ""))
                        return
                    self.send_json(update_invoice_draft(self.conn(), invoice_id, data))
                    return
                if parsed.path == "/api/account-members":
                    self.send_json(
                        {
                            "account_member_id": add_account_member(
                                self.conn(),
                                data["account_id"],
                                data["person_id"],
                                data.get("relationship_role", "primary"),
                                bool(data.get("is_primary")),
                            )
                        }
                    )
                    return
                if parsed.path.startswith("/api/review/candidates/"):
                    parts = parsed.path.strip("/").split("/")
                    candidate_id = parts[3]
                    action = parts[4] if len(parts) > 4 else "save"
                    if action == "save":
                        self.send_json(save_interpretation(self.conn(), candidate_id, data))
                        return
                    if action == "save-person":
                        self.send_json(save_person_section(self.conn(), candidate_id, data))
                        return
                    if action == "save-relationship":
                        self.send_json(save_relationship_section(self.conn(), candidate_id, data))
                        return
                    if action == "save-billing":
                        self.send_json(save_billing_section(self.conn(), candidate_id, data))
                        return
                    if action == "save-session":
                        self.send_json(save_session_draft(self.conn(), candidate_id, data))
                        return
                    if action == "refresh":
                        refresh_candidate_suggestions(self.conn(), candidate_id)
                        self.send_json(get_review_candidate(self.conn(), candidate_id))
                        return
                    if action == "approve":
                        result = approve_candidate(self.conn(), candidate_id, data)
                        approved_session_id = result.get("session", {}).get("id")
                        if approved_session_id:
                            try:
                                staging = stage_approved_sessions_to_monthly_drafts(
                                    self.conn(), session_ids=[approved_session_id],
                                )
                                result["invoice_staging"] = {
                                    "status": "success" if not staging.get("errors") else "warning",
                                    "summary": staging,
                                }
                            except DatabaseBusyError:
                                result["invoice_staging"] = {"status": "unavailable", "summary": None}
                            except Exception:
                                result["invoice_staging"] = {"status": "error", "summary": None}
                        self.send_json(result)
                        return
                    if action == "mark":
                        self.send_json(
                            mark_candidate(
                                self.conn(),
                                candidate_id,
                                classification=data.get("classification", "personal"),
                                reason=data.get("reason", ""),
                            )
                        )
                        return
                    if action == "restore":
                        self.send_json(
                            restore_candidate(
                                self.conn(),
                                candidate_id,
                                reason=data.get("reason", ""),
                            )
                        )
                        return
                    if action == "send-to-review":
                        self.send_json(
                            promote_candidate_to_review(
                                self.conn(),
                                candidate_id,
                                reason=data.get("reason", ""),
                            )
                        )
                        return
                if parsed.path == "/api/review/recalc-rates":
                    count = recalc_unapproved_session_rates(self.conn())
                    self.send_json({"ok": True, "sessions_updated": count})
                    return
                if parsed.path == "/api/review/reparse-candidates":
                    result = reparse_unapproved_candidates(self.conn())
                    self.send_json({"ok": True, **result})
                    return
                if parsed.path == "/api/billing-relationships/setup":
                    self.send_json(setup_billing_relationship(self.conn(), data))
                    return
                self.send_error(404)
            except Exception as error:
                self.send_error_response(error, default_status=400)

        def do_PUT(self) -> None:
            self.handle_unsupported_mutation_method()

        def do_PATCH(self) -> None:
            self.handle_unsupported_mutation_method()

        def do_DELETE(self) -> None:
            self.handle_unsupported_mutation_method()

        def handle_unsupported_mutation_method(self) -> None:
            parsed, _data = self.read_mutation_json_request()
            if parsed is None:
                return
            self.send_error(404)

        def conn(self):
            if not hasattr(self, "_database_connection"):
                self._database_connection = connect(database_path)
            return self._database_connection

        def finish(self) -> None:
            try:
                super().finish()
            finally:
                connection = getattr(self, "_database_connection", None)
                if connection is not None:
                    connection.close()

        def read_json(self, declared_length: int) -> dict:
            raw = self.rfile.read(declared_length)
            if len(raw) < declared_length:
                raise ValueError("Request body shorter than declared Content-Length.")
            return json.loads(raw.decode("utf-8"))

        def read_mutation_json_request(self) -> tuple[object | None, dict]:
            parsed = urlparse(self.path)
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                self.send_json({"ok": False, "error": "Content-Length header is required."}, status=411)
                return None, {}
            try:
                length = int(raw_length)
            except (ValueError, TypeError):
                self.send_json({"ok": False, "error": "Invalid Content-Length header."}, status=400)
                return None, {}
            if length < 0:
                self.send_json({"ok": False, "error": "Invalid Content-Length header."}, status=400)
                return None, {}
            if length > MAX_REQUEST_BODY_BYTES:
                self.send_json({"ok": False, "error": "Request body too large."}, status=413)
                return None, {}
            if length > 0 and not self.has_json_content_type():
                self.send_json(
                    {"ok": False, "error": "Content-Type must be application/json."},
                    status=415,
                )
                return None, {}
            if not self.has_valid_write_token():
                self.send_json({"ok": False, "error": "Forbidden."}, status=403)
                return None, {}
            try:
                return parsed, self.read_json(length)
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                self.send_json({"ok": False, "error": "Malformed JSON in request body."}, status=400)
                return None, {}

        def has_json_content_type(self) -> bool:
            content_type = self.headers.get("Content-Type", "")
            if not content_type:
                return False
            media_type = content_type.split(";", 1)[0].strip().lower()
            return media_type == "application/json"

        def has_valid_write_token(self) -> bool:
            supplied_token = self.headers.get(WRITE_TOKEN_HEADER)
            if supplied_token is None:
                return False
            return hmac.compare_digest(supplied_token, launch_write_token)

        def send_json(self, payload: object, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_static(self, name: str) -> None:
            path = (STATIC_DIR / name).resolve()
            if STATIC_DIR.resolve() not in path.parents and path != STATIC_DIR.resolve():
                self.send_error(403)
                return
            if not path.exists() or path.is_dir():
                self.send_error(404)
                return
            if path.name == "review.html":
                body = self.render_review_html(path).encode("utf-8")
            else:
                body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            if path.name == "review.html":
                self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def render_review_html(self, path: Path) -> str:
            html = path.read_text(encoding="utf-8")
            bootstrap = json.dumps({"writeToken": launch_write_token}, ensure_ascii=False)
            bootstrap = bootstrap.replace("</", "<\\/")
            bootstrap_script = f"<script>window.__JORDANA_BOOTSTRAP__={bootstrap};</script>"
            marker = '<script src="/static/review.js"></script>'
            if marker in html:
                return html.replace(marker, f"{bootstrap_script}\n    {marker}", 1)
            return f"{html}\n{bootstrap_script}\n"

        def send_csv(self, csv_text: str, filename: str) -> None:
            body = csv_text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ReviewHandler


def first(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key) or [""]
    return values[0]


def serve(database_path: str, host: str = "127.0.0.1", port: int = 8765) -> None:
    try:
        result = migrate_database(database_path)
        if result["migrated"]:
            print(f"Database migrated: {database_path}")
            if result["backup_path"]:
                print(f"Backup created: {result['backup_path']}")
    except MigrationError as error:
        print(f"Database migration failed: {error}")
        if error.backup_path:
            print(f"Backup preserved at: {error.backup_path}")
        raise SystemExit(1)
    server = ThreadingHTTPServer((host, port), make_handler(database_path))
    print(f"Review UI running at http://{host}:{port}/review")
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/jordana_invoice.sqlite3")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    serve(args.db, args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
